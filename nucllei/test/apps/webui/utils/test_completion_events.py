"""completion_events(wire v2 のイベントバス)の単体テスト。

タスクスコープの CompletionEventStream について、購読 SSE の終端(close = EOF)、
途中参加の収束点(latest_snapshot)、双方向呼び出し(call / resolve_call)、
切断(unsubscribe)がバス・タスクに影響しないことを固定する。
"""

import asyncio
import json

from nucllei.utils.completion_events import (
    CompletionEventStream,
    get_stream,
    make_event_emitter,
    register_stream,
    release_stream,
    sse_event_generator,
)


def _env(data):
    """封筒(旧 socket 'chat-events' と同形)を作る。"""
    return {"chat_id": "c", "message_id": "m", "data": data}


def test_subscribe_emit_close_reaches_eof():
    """subscribe → emit → close で SSE ジェネレータが EOF する(クライアントの
    終端は done イベントではなく EOF — wire v2 規約)。"""

    async def run():
        stream = CompletionEventStream(chat_id="c", message_id="m")
        queue = stream.subscribe()
        collected = []

        async def consume():
            async for line in sse_event_generator(stream, queue):
                assert line.startswith("data: ") and line.endswith("\n\n")
                collected.append(json.loads(line[len("data: ") :]))

        consumer = asyncio.create_task(consume())
        stream.emit(_env({"type": "chat:completion", "data": {"x": 1}}))
        stream.close()
        await asyncio.wait_for(consumer, timeout=5)  # close = EOF で自然終了
        return collected

    collected = asyncio.run(run())
    assert [e["data"] for e in collected] == [
        {"type": "chat:completion", "data": {"x": 1}}
    ]


def test_first_events_precede_live_events():
    """first_events(task 封筒)は購読キューのライブイベントより先に流れる。"""

    async def run():
        stream = CompletionEventStream()
        queue = stream.subscribe()
        stream.emit(_env({"type": "live"}))
        stream.close()

        first = _env({"type": "task", "data": {"task_id": "t-1"}})
        lines = [
            line
            async for line in sse_event_generator(stream, queue, first_events=[first])
        ]
        return [json.loads(line[len("data: ") :])["data"]["type"] for line in lines]

    assert asyncio.run(run()) == ["task", "live"]


def test_late_join_converges_via_latest_snapshot():
    """途中参加(リロード再アタッチ)は latest_snapshot で収束する:
    content_blocks 入りスナップショットだけが保持され、delta では更新されない。"""
    stream = CompletionEventStream()

    snapshot = {
        "content": "abc",
        "content_blocks": [{"type": "text", "content": "abc"}],
    }
    stream.emit(_env({"type": "chat:completion", "data": snapshot}))
    stream.emit(
        _env(
            {
                "type": "chat:completion",
                "data": {"delta": {"block": "text", "content": "d"}},
            }
        )
    )

    assert stream.latest_snapshot == snapshot


def test_call_resolves_via_resolve_call():
    """call はイベントを配って resolve_call(POST reply)で解決される。"""

    async def run():
        stream = CompletionEventStream(chat_id="c", message_id="m")
        queue = stream.subscribe()

        call_task = asyncio.create_task(stream.call({"type": "confirm"}))
        envelope = await asyncio.wait_for(queue.get(), timeout=5)
        assert envelope["data"] == {"type": "confirm"}
        assert envelope["chat_id"] == "c"

        assert stream.resolve_call(envelope["call_id"], {"ok": True}) is True
        result = await asyncio.wait_for(call_task, timeout=5)
        assert result == {"ok": True}

        # 解決済み call_id への再解決・未知 id は False
        assert stream.resolve_call(envelope["call_id"], {}) is False
        assert stream.resolve_call("unknown", {}) is False

    asyncio.run(run())


def test_unsubscribe_does_not_close_stream():
    """切断(SSE ジェネレータの cancel)は購読解除のみで、バスは生きたまま
    残る購読者へ配信され続ける(タスクに影響しない — 再アタッチ前提)。"""

    async def run():
        stream = CompletionEventStream()
        q1 = stream.subscribe()
        q2 = stream.subscribe()

        async def consume():
            async for _ in sse_event_generator(stream, q1):
                pass

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)  # ジェネレータを開始させて queue.get で待たせる
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

        # バスは閉じておらず、残る購読者(q2)へは配信される
        assert not stream.closed
        assert stream.has_subscribers
        stream.emit(_env({"type": "x"}))
        envelope = await asyncio.wait_for(q2.get(), timeout=5)
        assert envelope["data"] == {"type": "x"}
        # 解除済みの q1 には届かない
        assert q1.empty()

        stream.close()

    asyncio.run(run())


def test_subscribe_after_close_gets_immediate_eof():
    """close 済みバスへの購読は即 EOF(終了済みタスクへの再アタッチの外形)。"""

    async def run():
        stream = CompletionEventStream()
        stream.close()
        queue = stream.subscribe()
        return [line async for line in sse_event_generator(stream, queue)]

    assert asyncio.run(run()) == []


def test_registry_release_closes_stream():
    """release_stream(タスク終端の done callback)は登録解除と close を行う。"""
    stream = CompletionEventStream()
    register_stream("task-1", stream)
    assert get_stream("task-1") is stream

    release_stream("task-1")
    assert get_stream("task-1") is None
    assert stream.closed
    # 二重 release は無害
    release_stream("task-1")


# --- tool 進捗の保持と、tool 用 emitter の status 遮断(#100)------------------


def test_latest_progress_kept_for_reattach():
    """実行中 tool の進捗は tool_call_id ごとに最新 1 件だけ保持する。

    再アタッチ(リロード・途中参加)で復元するための揮発バッファ。DB には書かない。
    """
    stream = CompletionEventStream(chat_id="c", message_id="m")

    stream.emit(_env({"type": "tool:progress", "data": {"tool_call_id": "a", "n": 1}}))
    stream.emit(_env({"type": "tool:progress", "data": {"tool_call_id": "a", "n": 2}}))
    stream.emit(_env({"type": "tool:progress", "data": {"tool_call_id": "b", "n": 1}}))

    assert set(stream.latest_progress) == {"a", "b"}
    # 同じ tool_call_id は上書き(履歴は溜めない)
    assert stream.latest_progress["a"]["data"]["data"]["n"] == 2


def test_finished_tool_progress_is_dropped_on_snapshot():
    """結果が出た tool の進捗は捨てる。

    完了の権威は results の到着であって progress の done 宣言ではないので、
    再アタッチしたクライアントに終わった tool の「実行中」が復元されない。
    """
    stream = CompletionEventStream(chat_id="c", message_id="m")
    stream.emit(_env({"type": "tool:progress", "data": {"tool_call_id": "a"}}))
    stream.emit(_env({"type": "tool:progress", "data": {"tool_call_id": "b"}}))

    stream.emit(
        _env(
            {
                "type": "chat:completion",
                "data": {
                    "content_blocks": [
                        {
                            "type": "tool_calls",
                            "calls": [{"id": "a"}, {"id": "b"}],
                            "results": [{"tool_call_id": "a", "content": "ok"}],
                        }
                    ]
                },
            }
        )
    )

    # a は結果が出たので破棄、b はまだ実行中なので残る
    assert set(stream.latest_progress) == {"b"}


def test_progress_is_not_persisted_in_latest_snapshot():
    """進捗はスナップショット(= 永続・再アタッチの本体)を汚さない。"""
    stream = CompletionEventStream(chat_id="c", message_id="m")
    stream.emit(
        _env({"type": "chat:completion", "data": {"content_blocks": [], "done": False}})
    )
    stream.emit(
        _env(
            {"type": "tool:progress", "data": {"tool_call_id": "a", "description": "x"}}
        )
    )
    assert stream.latest_snapshot == {"content_blocks": [], "done": False}


# --- ライブ usage(chat:usage)の保持と破棄(#102)----------------------------


def test_latest_usage_kept_for_reattach():
    """chat:usage は message_id ごとに最新 1 件だけ保持する。

    再アタッチ(リロード・途中参加)で復元するための揮発バッファ。DB には書かない。
    """
    stream = CompletionEventStream(chat_id="c", message_id="m")

    stream.emit(_env({"type": "chat:usage", "data": {"completion_tokens": 1}}))
    stream.emit(_env({"type": "chat:usage", "data": {"completion_tokens": 5}}))

    assert set(stream.latest_usage) == {"m"}
    # 同じ message は上書き(履歴は溜めない)
    assert stream.latest_usage["m"]["data"]["data"]["completion_tokens"] == 5


def test_latest_usage_dropped_on_done_completion():
    """done=True の chat:completion で該当 message のライブ usage を破棄する。

    終わった生成の「途中値」を再アタッチに復元しない(最終真値は
    chat:completion {usage} が別途届く)。
    """
    stream = CompletionEventStream(chat_id="c", message_id="m")
    stream.emit(_env({"type": "chat:usage", "data": {"completion_tokens": 5}}))

    # done でないスナップショットでは残る(ストリーミング中の構造変化)
    stream.emit(_env({"type": "chat:completion", "data": {"content_blocks": []}}))
    assert set(stream.latest_usage) == {"m"}

    stream.emit(
        _env({"type": "chat:completion", "data": {"done": True, "content_blocks": []}})
    )
    assert stream.latest_usage == {}


def test_usage_is_not_persisted_in_latest_snapshot():
    """ライブ usage はスナップショット(= 永続・再アタッチの本体)を汚さない。"""
    stream = CompletionEventStream(chat_id="c", message_id="m")
    stream.emit(
        _env({"type": "chat:completion", "data": {"content_blocks": [], "done": False}})
    )
    stream.emit(_env({"type": "chat:usage", "data": {"completion_tokens": 5}}))
    assert stream.latest_snapshot == {"content_blocks": [], "done": False}


def test_reattach_first_events_order_task_snapshot_progress_usage():
    """再アタッチの first_events は task → snapshot → 揮発イベント(progress・usage)。

    main.py の task_events_endpoint と同じ構成規律: 揮発イベント(進捗・ライブ usage)
    はブロック・メッセージへ紐づけて描かれるため、宛先を作るスナップショットより
    後に配る。
    """

    async def run():
        stream = CompletionEventStream(chat_id="c", message_id="m")
        stream.emit(_env({"type": "chat:completion", "data": {"content_blocks": []}}))
        stream.emit(_env({"type": "tool:progress", "data": {"tool_call_id": "a"}}))
        stream.emit(_env({"type": "chat:usage", "data": {"completion_tokens": 5}}))

        # main.py(task_events_endpoint)と同じ組み立て
        first_events = [_env({"type": "task", "data": {"task_id": "t-1"}})]
        if stream.latest_snapshot is not None:
            first_events.append(
                _env({"type": "chat:completion", "data": stream.latest_snapshot})
            )
        first_events.extend(stream.latest_progress.values())
        first_events.extend(stream.latest_usage.values())

        queue = stream.subscribe()
        stream.close()
        lines = [
            line
            async for line in sse_event_generator(
                stream, queue, first_events=first_events
            )
        ]
        return [json.loads(line[len("data: ") :])["data"]["type"] for line in lines]

    assert asyncio.run(run()) == [
        "task",
        "chat:completion",
        "tool:progress",
        "chat:usage",
    ]


# --- ターン寿命リソースの硬化(#110 §6)--------------------------------------


def test_event_emitter_skips_db_writes_inside_execution(monkeypatch):
    """exec runner 内(ターン寿命を超えた実行)からの DB 分岐は全面スキップ。

    親メッセージの tool result は spawn 時の running 形のみで以後不変
    (append-only)であり、ターン中の chat JSON RMW とも競合するため。
    配信(SSE)自体は素通しで、購読者が居れば見える。
    """
    from nucllei.utils import completion_events
    from nucllei.utils.executions import in_exec

    written = []
    for name in (
        "upsert_message_to_chat_by_id_and_message_id",
        "get_message_by_id_and_message_id",
    ):
        monkeypatch.setattr(
            completion_events.Chats,
            name,
            lambda *args, _name=name, **kwargs: written.append(_name),
        )

    async def run():
        stream = CompletionEventStream(chat_id="c", message_id="m")
        queue = stream.subscribe()
        emitter = make_event_emitter(stream, {"chat_id": "c", "message_id": "m"})

        token = in_exec.set(True)
        try:
            await emitter({"type": "message", "data": {"content": "追記"}})
            await emitter({"type": "replace", "data": {"content": "上書き"}})
        finally:
            in_exec.reset(token)
        assert written == []

        # 通常のターン文脈では従来どおり DB へ書く
        await emitter({"type": "replace", "data": {"content": "上書き"}})
        assert written == ["upsert_message_to_chat_by_id_and_message_id"]

        stream.close()
        return queue.qsize()

    # 3 通 + close 番兵(DB を書かなくても配信は素通し)
    assert asyncio.run(run()) == 4


def test_event_emitter_skips_db_writes_on_closed_stream(monkeypatch):
    """ターンが畳まれた後の遅延 emit で DB だけ変異させない(#110 §6)。"""
    from nucllei.utils import completion_events

    written = []
    monkeypatch.setattr(
        completion_events.Chats,
        "upsert_message_to_chat_by_id_and_message_id",
        lambda *args, **kwargs: written.append(args),
    )

    async def run():
        stream = CompletionEventStream(chat_id="c", message_id="m")
        emitter = make_event_emitter(stream, {"chat_id": "c", "message_id": "m"})
        stream.close()
        await emitter({"type": "replace", "data": {"content": "遅延"}})

    asyncio.run(run())
    assert written == []


def test_call_on_closed_stream_fails_fast():
    """closed 後の __event_call__ は future を作らず即エラー(#110 §6)。

    応答を返せる相手が居ない以上、待てば timeout まで hang するだけ。
    """

    async def run():
        stream = CompletionEventStream(chat_id="c", message_id="m")
        stream.close()
        try:
            await asyncio.wait_for(stream.call({"type": "confirm"}), timeout=5)
        except RuntimeError as e:
            return str(e)
        raise AssertionError("closed stream への call がエラーにならない")

    assert "closed" in asyncio.run(run())


def test_close_wakes_pending_calls_with_an_exception():
    """close は pending call を cancel ではなく set_exception で起こす(#110 §6)。

    cancel だと待ち手が CancelledError を受け、「停止された」意味論
    (exec runner の D2 / 親ターン stop)と混ざるため。
    """

    async def run():
        stream = CompletionEventStream(chat_id="c", message_id="m")
        stream.subscribe()
        call_task = asyncio.create_task(stream.call({"type": "confirm"}))
        await asyncio.sleep(0)  # future 登録まで進める

        stream.close()
        try:
            await asyncio.wait_for(call_task, timeout=5)
        except asyncio.CancelledError:
            raise AssertionError("pending call が cancel で起こされている")
        except RuntimeError as e:
            return str(e)
        raise AssertionError("pending call が起こされていない")

    assert "closed" in asyncio.run(run())
