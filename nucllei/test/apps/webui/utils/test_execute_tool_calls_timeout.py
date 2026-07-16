"""execute_tool_calls のシステム側タイムアウト(CHAT_TOOL_EXECUTION_TIMEOUT)。

タイムアウトは例外にせず tool result としてモデルに返す(turn は止めない)。
sandbox 等の実行側が自前上限を持たなくてよい、という権威的上限の外形を固定する。
"""

import asyncio

from nucllei.utils import middleware
from nucllei.utils.middleware import execute_tool_calls


def _tool(callable_, name="probe"):
    return {
        name: {
            "callable": callable_,
            "spec": {"name": name, "parameters": {"properties": {}}},
        }
    }


def _call(name="probe"):
    return [
        {"id": f"functions.{name}:0", "function": {"name": name, "arguments": "{}"}}
    ]


def test_slow_tool_times_out_as_result(monkeypatch):
    monkeypatch.setattr(middleware, "CHAT_TOOL_EXECUTION_TIMEOUT", 1)

    async def slow_tool():
        await asyncio.sleep(5)
        return "unreachable"

    results = asyncio.run(execute_tool_calls(_call(), _tool(slow_tool)))

    assert len(results) == 1
    assert results[0]["tool_call_id"] == "functions.probe:0"
    assert "タイムアウト" in results[0]["content"]


def test_fast_tool_unaffected_by_timeout(monkeypatch):
    monkeypatch.setattr(middleware, "CHAT_TOOL_EXECUTION_TIMEOUT", 1)

    async def fast_tool():
        return "ok"

    results = asyncio.run(execute_tool_calls(_call(), _tool(fast_tool)))
    assert results[0]["content"] == "ok"


def test_timeout_disabled_with_negative_value(monkeypatch):
    monkeypatch.setattr(middleware, "CHAT_TOOL_EXECUTION_TIMEOUT", -1)

    async def slowish_tool():
        await asyncio.sleep(0.05)
        return "done"

    results = asyncio.run(execute_tool_calls(_call(), _tool(slowish_tool)))
    assert results[0]["content"] == "done"
