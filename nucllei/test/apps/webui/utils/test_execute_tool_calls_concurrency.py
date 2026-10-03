"""execute_tool_calls の並行実行(2026-10-04 ken 承認)の回帰。

1 応答に並んだ同期 tool call は並行に走る(旧: 逐次)。保つ性質:
- 所要は和でなく最大(2 本の遅い tool が重なる)
- results の順 = call の順(後ろの call が先に終わっても)
- per-call の timeout とエラー隔離(1 本の失敗・timeout が兄弟を巻き込まない)
- 親ターンの cancel(ユーザー停止)は全 call に伝播する
- `serial` エントリ(sandbox)は toolkit 単位で応答順に 1 本ずつ
- load_skill は先に済ませる(同じ応答の後ろの call が遅延ロードされた tool を引ける)
"""

import asyncio
import json
import time

from nucllei.utils.middleware import execute_tool_calls


def _entry(callable_, *, properties=None, **extra):
    return {
        "callable": callable_,
        "spec": {"name": "probe", "parameters": {"properties": properties or {}}},
        **extra,
    }


def _call(name, arguments=None, index=0):
    return {
        "id": f"functions.{name}:{index}",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


def test_sync_calls_overlap_and_keep_call_order():
    """2 本の遅い tool は重なって走り(所要 ≒ 最大)、results は call 順。"""
    finished = []

    async def slow(**_):
        await asyncio.sleep(0.4)
        finished.append("slow")
        return "slow-result"

    async def fast(**_):
        await asyncio.sleep(0.2)
        finished.append("fast")
        return "fast-result"

    tools = {"slow": _entry(slow), "fast": _entry(fast)}
    started = time.monotonic()
    results = asyncio.run(
        execute_tool_calls([_call("slow", index=0), _call("fast", index=1)], tools)
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.55, f"calls ran sequentially ({elapsed:.2f}s)"
    # 後ろの call が先に終わっても results は call 順
    assert finished == ["fast", "slow"]
    assert [r["tool_call_id"] for r in results] == [
        "functions.slow:0",
        "functions.fast:1",
    ]
    assert [r["content"] for r in results] == ["slow-result", "fast-result"]
    # duration は call ごとに測る(並行なので 2 本目が 1 本目を待った分を含まない)
    assert results[1]["duration"] < 0.35


def test_one_failure_does_not_cancel_its_siblings():
    """1 本が例外・timeout でも兄弟は最後まで走り、結果が揃う。"""
    sibling_done = []

    async def boom(**_):
        await asyncio.sleep(0.05)
        raise RuntimeError("壊れた")

    async def hangs(**_):
        await asyncio.sleep(5)
        return "never"

    async def sibling(**_):
        await asyncio.sleep(0.3)
        sibling_done.append(True)
        return "完走"

    tools = {
        "boom": _entry(boom),
        "hangs": _entry(hangs, timeout=0.1),
        "sibling": _entry(sibling),
    }
    started = time.monotonic()
    results = asyncio.run(
        execute_tool_calls(
            [
                _call("boom", index=0),
                _call("hangs", index=1),
                _call("sibling", index=2),
            ],
            tools,
        )
    )
    elapsed = time.monotonic() - started

    assert results[0]["content"] == "壊れた"
    assert "タイムアウト" in results[1]["content"]
    assert results[2]["content"] == "完走"
    assert sibling_done == [True]
    assert elapsed < 1.0


def test_stop_cancels_every_running_call():
    """親ターンの cancel(ユーザー停止)は並行中の全 call に届く。"""
    started = []
    cancelled = []

    def make(name):
        async def tool(**_):
            started.append(name)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(name)
                raise
            return name

        return tool

    tools = {"a": _entry(make("a")), "b": _entry(make("b"))}

    async def main():
        task = asyncio.create_task(
            execute_tool_calls([_call("a", index=0), _call("b", index=1)], tools)
        )
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(0.01)
        assert sorted(started) == ["a", "b"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(main()) is True
    assert sorted(cancelled) == ["a", "b"]


def test_serial_toolkit_runs_one_at_a_time_in_call_order():
    """`serial` エントリは toolkit(tool_id)単位で応答順に 1 本ずつ。他の call とは並行。"""
    active = {"n": 0, "max": 0}
    order = []

    def make(name, delay):
        async def tool(**_):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            order.append(f"start:{name}")
            await asyncio.sleep(delay)
            order.append(f"end:{name}")
            active["n"] -= 1
            return name

        return tool

    other_overlapped = []

    async def other(**_):
        await asyncio.sleep(0.05)
        # serial レーンが走っている最中に並行で走れている
        other_overlapped.append(active["n"] >= 1)
        return "other"

    tools = {
        "cell1": _entry(make("cell1", 0.2), serial=True, tool_id="builtin_sandbox"),
        "cell2": _entry(make("cell2", 0.05), serial=True, tool_id="builtin_sandbox"),
        "other": _entry(other),
    }
    results = asyncio.run(
        execute_tool_calls(
            [_call("cell1", index=0), _call("cell2", index=1), _call("other", index=2)],
            tools,
        )
    )
    assert order == ["start:cell1", "end:cell1", "start:cell2", "end:cell2"]
    assert active["max"] == 1
    assert other_overlapped == [True]
    assert [r["content"] for r in results] == ["cell1", "cell2", "other"]


def test_load_skill_runs_first_so_later_calls_see_loaded_tools():
    """load_skill が遅延ロードした tool を、同じ応答の後ろの call が引ける。"""
    tools = {}

    async def loaded_tool(**_):
        return "loaded"

    async def load_skill(**_):
        await asyncio.sleep(0.05)
        tools["loaded_tool"] = _entry(loaded_tool)
        return "skill body"

    tools["load_skill"] = _entry(load_skill)
    results = asyncio.run(
        execute_tool_calls(
            [_call("load_skill", index=0), _call("loaded_tool", index=1)], tools
        )
    )
    assert [r["content"] for r in results] == ["skill body", "loaded"]
