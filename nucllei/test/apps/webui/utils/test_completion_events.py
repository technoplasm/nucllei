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
