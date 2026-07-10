"""
llens(#28): イベントループのブロッキング計測ベンチ。

外部ネットワーク不要 — localhost に「応答まで DELAY 秒かかる」HTTP サーバを立て、
対象の async 経路を await している間、10ms 刻みの ticker タスクで
イベントループの最大停止時間(max stall)を計測する。

- 同期 requests がループ上で走ると max stall ≈ DELAY(ループ全停止 = 全ユーザーの
  ストリーミングが止まる)
- async 化(aiohttp / asyncio.to_thread)済みなら max stall ≪ DELAY

対象は「async ハンドラから届く同期 HTTP」の代表例。#28 の before/after 数値は
このテストの計測値を issue に記録する。
"""

import asyncio
import inspect
import threading
import time
from types import SimpleNamespace

from aiohttp import web

DELAY = 0.5  # 遅延サーバの応答時間(秒)
MAX_ALLOWED_STALL = 0.15  # async 化済みなら ticker のギャップはこの範囲(CI 揺らぎ込み)


class _SlowServer:
    """全パスに DELAY 秒後 JSON を返すサーバ。voices/models 両形状を含む。

    ベンチ対象(同期 requests)がテスト側のイベントループを塞いでも応答できるよう、
    別スレッドの専用ループで動かす(同一ループだとデッドロックする)。
    """

    def __init__(self):
        self._loop = None
        self._runner = None
        self._started = threading.Event()
        self.base_url = None

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def slow(request):
            await asyncio.sleep(DELAY)
            return web.json_response(
                {
                    "voices": [{"id": "v1", "name": "voice1"}],
                    "models": [{"id": "m1"}],
                }
            )

        async def start():
            app = web.Application()
            app.router.add_route("*", "/{tail:.*}", slow)
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            site = web.TCPSite(self._runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.base_url = f"http://127.0.0.1:{port}"
            self._started.set()

        self._loop.run_until_complete(start())
        self._loop.run_forever()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._started.wait(timeout=10), "slow server が起動しない"
        return self.base_url

    def stop(self):
        async def cleanup():
            await self._runner.cleanup()

        asyncio.run_coroutine_threadsafe(cleanup(), self._loop).result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)


async def _measure_stall(awaitable_or_fn):
    """対象を実行しつつ ticker の最大ギャップ(=ループ停止時間)を返す。

    sync 関数(before 状態)も同じ土俵で計測できるよう、callable はそのまま
    ループ上で呼ぶ(= 意図的にブロックさせて before 数値を得る)。
    """
    gaps = []
    stop = asyncio.Event()

    async def ticker():
        prev = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - prev)
            prev = now

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)  # ticker のウォームアップ
    try:
        if inspect.isawaitable(awaitable_or_fn):
            await awaitable_or_fn
        else:
            awaitable_or_fn()  # sync: ループ上で直接実行(before の再現)
    finally:
        stop.set()
        await t
    return max(gaps) if gaps else 0.0


def _fake_request(base_url: str):
    """audio helper 群が読む config 属性だけ持つ偽 Request。"""
    config = SimpleNamespace(
        TTS_ENGINE="openai",
        TTS_OPENAI_API_BASE_URL=base_url,  # 非 api.openai.com → カスタム endpoint 経路
        TTS_API_KEY="dummy",
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _run(scenario):
    return asyncio.run(scenario())


class TestEventLoopBlocking:
    def setup_method(self):
        self.server = _SlowServer()
        self.base = self.server.start()

    def teardown_method(self):
        self.server.stop()

    def test_audio_get_voices_does_not_stall_loop(self):
        from open_webui.routers.audio import get_voices

        async def scenario():
            request = _fake_request(self.base)
            return await _measure_stall(get_voices(request, user=None))

        stall = _run(scenario)
        print(f"\n[bench] audio /voices max loop stall: {stall*1000:.0f}ms")
        assert stall < MAX_ALLOWED_STALL, (
            f"audio /voices がイベントループを {stall:.3f}s 停止させた"
            f"(同期 HTTP が async ハンドラ上で走っている)"
        )

    def test_audio_get_models_does_not_stall_loop(self):
        from open_webui.routers.audio import get_models

        async def scenario():
            request = _fake_request(self.base)
            return await _measure_stall(get_models(request, user=None))

        stall = _run(scenario)
        print(f"\n[bench] audio /models max loop stall: {stall*1000:.0f}ms")
        assert (
            stall < MAX_ALLOWED_STALL
        ), f"audio /models がイベントループを {stall:.3f}s 停止させた"

    def test_post_webhook_does_not_stall_loop(self):
        from open_webui.utils import webhook as webhook_mod

        async def scenario():
            fn = webhook_mod.post_webhook
            if inspect.iscoroutinefunction(fn):
                target = fn("llens", f"{self.base}/hook", "msg", {"action": "test"})
            else:
                # before(sync 版): ループ上で直接呼んで停止を再現
                def target():
                    fn("llens", f"{self.base}/hook", "msg", {"action": "test"})

            return await _measure_stall(target)

        stall = _run(scenario)
        print(f"\n[bench] post_webhook max loop stall: {stall*1000:.0f}ms")
        assert stall < MAX_ALLOWED_STALL, (
            f"post_webhook がイベントループを {stall:.3f}s 停止させた"
            f"(チャット経路 background_tasks_handler から同期で呼ばれる)"
        )

    def test_sync_filter_handler_does_not_stall_loop(self):
        """CORE: 遅い sync Filter(de-id 等)が全ユーザーのストリーミングを
        止めないこと(sync handler は to_thread でワーカースレッド実行)。"""
        from open_webui.utils.filter import process_filter_functions

        def slow_inlet(body):
            time.sleep(DELAY)  # 遅い同期 Filter を模擬(重い正規表現・外部呼び出し等)
            return body

        filter_module = SimpleNamespace(inlet=slow_inlet)
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(FUNCTIONS={"bench": filter_module})
            )
        )
        filter_functions = [SimpleNamespace(id="bench")]

        async def scenario():
            return await _measure_stall(
                process_filter_functions(
                    request, filter_functions, "inlet", {"messages": []}, {}
                )
            )

        stall = _run(scenario)
        print(f"\n[bench] sync filter inlet max loop stall: {stall*1000:.0f}ms")
        assert stall < MAX_ALLOWED_STALL, (
            f"sync Filter handler がイベントループを {stall:.3f}s 停止させた"
            f"(CORE: Filter パイプラインはチャット経路)"
        )
