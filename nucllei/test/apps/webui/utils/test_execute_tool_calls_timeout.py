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


####################################
# per-tool timeout(#107: tool エントリの timeout キーが全体上限より優先)
####################################


def test_per_tool_timeout_shorter_than_global_wins(monkeypatch):
    monkeypatch.setattr(middleware, "CHAT_TOOL_EXECUTION_TIMEOUT", 100)

    async def slow_tool():
        await asyncio.sleep(5)
        return "unreachable"

    tools = _tool(slow_tool)
    tools["probe"]["timeout"] = 0.2

    results = asyncio.run(execute_tool_calls(_call(), tools))
    assert "タイムアウト" in results[0]["content"]
    # 文言も per-tool 値(全体上限の 100 ではない)
    assert "0.2 秒" in results[0]["content"]


def test_per_tool_timeout_longer_than_global_wins(monkeypatch):
    # run_subagent のケース: 子ターン全体を包むため全体上限より長い timeout を持つ
    monkeypatch.setattr(middleware, "CHAT_TOOL_EXECUTION_TIMEOUT", 0.05)

    async def slowish_tool():
        await asyncio.sleep(0.2)
        return "done"

    tools = _tool(slowish_tool)
    tools["probe"]["timeout"] = 10

    results = asyncio.run(execute_tool_calls(_call(), tools))
    assert results[0]["content"] == "done"


def test_per_tool_timeout_negative_disables(monkeypatch):
    monkeypatch.setattr(middleware, "CHAT_TOOL_EXECUTION_TIMEOUT", 0.05)

    async def slowish_tool():
        await asyncio.sleep(0.2)
        return "done"

    tools = _tool(slowish_tool)
    tools["probe"]["timeout"] = -1

    results = asyncio.run(execute_tool_calls(_call(), tools))
    assert results[0]["content"] == "done"


def test_tool_without_timeout_key_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(middleware, "CHAT_TOOL_EXECUTION_TIMEOUT", 0.2)

    async def slow_tool():
        await asyncio.sleep(5)
        return "unreachable"

    results = asyncio.run(execute_tool_calls(_call(), _tool(slow_tool)))
    assert "タイムアウト" in results[0]["content"]
    assert "0.2 秒" in results[0]["content"]
