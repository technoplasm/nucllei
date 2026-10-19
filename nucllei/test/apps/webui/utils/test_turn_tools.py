"""ERROR・次の手で別の tool を名指しするのは、そのターンでモデルに出ている tool だけ
(2026-10-19 llens 監査 (13) — 「〜が使えるなら」をやめた)の配線と、それに乗る文面の回帰。

- `tool_errors.tool_in_turn` は middleware の同期実行(execute_tool_calls)が呼び出し時点の
  tools_dict から立てる: async tool / sync tool(to_thread)とも見える、同じ応答で
  load_skill が足した tool も入る、ターンの外(未設定)は空
- 切詰めの印の次の手は tool の spec の引数(offset / query)で選ぶ
- 同期タイムアウトの次の手は background を付けられる tool にだけ background を勧める

background の runner(exec 行 — DB が要る)は test_async_exec.py、サブエージェントの子ターンは
test_subagent.py が持つ。
"""

import asyncio

from nucllei.utils.middleware import execute_tool_calls
from nucllei.utils.tool_errors import tool_in_turn, turn_tools
from nucllei.utils.tools import get_async_tool_function_and_apply_extra_params


def _entry(callable_, name, properties=None, **extra):
    return {
        "callable": callable_,
        "spec": {"name": name, "parameters": {"properties": properties or {}}},
        **extra,
    }


def _call(name, index=0, arguments="{}"):
    return {
        "id": f"functions.{name}:{index}",
        "function": {"name": name, "arguments": arguments},
    }


def test_outside_a_turn_no_tool_is_named():
    assert tool_in_turn("feed_image") is False
    with turn_tools(["feed_image"]):
        assert tool_in_turn("feed_image") is True
        with turn_tools(["run_python"]):
            assert tool_in_turn("feed_image") is False
        assert tool_in_turn("feed_image") is True
    assert tool_in_turn("feed_image") is False


def test_sync_execution_sets_the_turn_tools_for_async_and_thread_tools():
    seen = {}

    async def probe_async():
        seen["async"] = (tool_in_turn("probe_sync"), tool_in_turn("ghost"))
        return "ok"

    def probe_sync():
        # sync の tool はワーカースレッド(asyncio.to_thread — context はコピーされる)
        seen["sync"] = (tool_in_turn("probe_async"), tool_in_turn("ghost"))
        return "ok"

    tools = {
        "probe_async": _entry(probe_async, "probe_async"),
        "probe_sync": _entry(
            get_async_tool_function_and_apply_extra_params(probe_sync, {}),
            "probe_sync",
        ),
    }
    results = asyncio.run(
        execute_tool_calls([_call("probe_async", 0), _call("probe_sync", 1)], tools)
    )
    assert [r["content"] for r in results] == ["ok", "ok"]
    assert seen == {"async": (True, False), "sync": (True, False)}
    # ターンが終われば戻る
    assert tool_in_turn("probe_async") is False


def test_tools_added_by_load_skill_in_the_same_response_are_named():
    """load_skill(prelude で先に済む)が tools_dict に足した tool は、同じ応答の
    後ろの call の時点で tool_in_turn に入る。"""
    seen = {}

    async def skill_tool():
        return "ok"

    async def load_skill(name: str = ""):
        tools["skill_tool"] = _entry(skill_tool, "skill_tool")
        return "loaded"

    async def probe():
        seen["skill_tool"] = tool_in_turn("skill_tool")
        return "ok"

    tools = {
        "load_skill": _entry(load_skill, "load_skill", {"name": {"type": "string"}}),
        "probe": _entry(probe, "probe"),
    }
    asyncio.run(execute_tool_calls([_call("probe", 0), _call("load_skill", 1)], tools))
    assert seen == {"skill_tool": True}


# --- 切詰めの印(executions.truncation_marker)---------------------------------------


def _truncated(monkeypatch, tools, name):
    from nucllei.utils import executions

    monkeypatch.setattr(executions, "TOOL_RESULT_MAX_CHARS", 10)
    results = asyncio.run(execute_tool_calls([_call(name)], tools))
    assert results[0]["truncated"] is True
    return results[0]["content"][10:]


def test_truncation_marker_follows_the_tool_arguments(monkeypatch):
    async def probe(**kwargs):
        return "あ" * 50

    head = "\n(ここで切れている: 全 50 字のうち先頭 10 字。次の手: 続きが要るなら"
    cases = {
        ("offset", "query"): "offset で続きから、または query で範囲を絞って呼び直す)",
        ("offset",): "offset で続きから呼び直す)",
        ("query",): "query で範囲を絞って呼び直す)",
        (): "出力を絞って呼び直す)",
    }
    for params, tail in cases.items():
        tools = {
            "probe": _entry(probe, "probe", {p: {"type": "string"} for p in params})
        }
        assert _truncated(monkeypatch, tools, "probe") == head + tail, params


def test_truncation_marker_names_read_file_only_when_it_is_in_the_turn(monkeypatch):
    async def probe():
        return "あ" * 50

    async def read_file(path: str = ""):
        return ""

    tools = {
        "probe": _entry(probe, "probe"),
        "read_file": _entry(read_file, "read_file", {"path": {"type": "string"}}),
    }
    assert _truncated(monkeypatch, tools, "probe").endswith(
        "次の手: 続きが要るなら出力を絞って呼び直すか、出力をファイルに書いて read_file で読む)"
    )


# --- 同期タイムアウト ----------------------------------------------------------------


def test_timeout_offers_background_only_where_it_can_be_used():
    async def probe():
        await asyncio.sleep(5)

    metadata = {"user_id": "u", "chat_id": "c", "message_id": "m"}
    fact = "ERROR: ツール probe の実行が 0.05 秒の上限でタイムアウトした。"

    eligible = {"probe": _entry(probe, "probe", timeout=0.05, backgroundable=True)}
    results = asyncio.run(
        execute_tool_calls([_call("probe")], eligible, metadata=metadata)
    )
    assert results[0]["content"] == (
        fact + "次の手: 入力を小さく分けて呼び直す。"
        "分単位かかる処理なら background: true を付けて呼び直す。"
    )

    # 子ターン(agent session)では background を付けられない — 勧めない
    results = asyncio.run(
        execute_tool_calls(
            [_call("probe")], eligible, metadata={**metadata, "agent_session": True}
        )
    )
    assert results[0]["content"] == fact + "次の手: 入力を小さく分けて呼び直す。"

    # backgroundable でない tool にも勧めない
    plain = {"probe": _entry(probe, "probe", timeout=0.05)}
    results = asyncio.run(
        execute_tool_calls([_call("probe")], plain, metadata=metadata)
    )
    assert results[0]["content"] == fact + "次の手: 入力を小さく分けて呼び直す。"


def test_timeout_of_a_lone_background_call_does_not_offer_background_again():
    """単独の background: true は同期へ降格して background の上限で走る — 付け直しても
    同じなので、タイムアウトしても background は勧めない。"""

    async def probe():
        await asyncio.sleep(5)

    metadata = {"user_id": "u", "chat_id": "c", "message_id": "m"}
    eligible = {"probe": _entry(probe, "probe", timeout=0.05, backgroundable=True)}
    results = asyncio.run(
        execute_tool_calls(
            [_call("probe", arguments='{"background": true}')],
            eligible,
            metadata=metadata,
        )
    )
    assert results[0]["content"] == (
        "ERROR: ツール probe の実行が 0.05 秒の上限でタイムアウトした。"
        "次の手: 入力を小さく分けて呼び直す。"
    )
