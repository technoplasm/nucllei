"""サーバー側 tool spec の合成が、既に form_data にあった tools を捨てない(2026-10-05)。

headless の呼び出し側が渡した tools や inlet filter が足した tools は、サーバー側の
tool が 1 つでもあると丸ごと上書きされていた。
"""

import inspect

from nucllei.utils.middleware import merge_tool_specs


def _spec(name, marker=None):
    function = {"name": name, "parameters": {"type": "object", "properties": {}}}
    if marker:
        function["description"] = marker
    return {"type": "function", "function": function}


def test_existing_tools_are_kept_after_server_tools():
    server = [_spec("get_weather")]
    existing = [_spec("client_lookup"), _spec("inlet_added")]
    merged = merge_tool_specs(server, existing)
    assert [t["function"]["name"] for t in merged] == [
        "get_weather",
        "client_lookup",
        "inlet_added",
    ]


def test_server_spec_wins_on_name_collision():
    server = [_spec("get_weather", "server")]
    merged = merge_tool_specs(server, [_spec("get_weather", "client")])
    assert merged == server


def test_no_existing_tools_and_junk_are_ignored():
    server = [_spec("a")]
    assert merge_tool_specs(server, None) == server
    assert merge_tool_specs(server, "junk") == server
    assert merge_tool_specs(server, ["junk", _spec("a"), _spec("a")]) == server
    # 呼び出し側の重複も 1 つに
    assert [
        t["function"]["name"] for t in merge_tool_specs([], [_spec("b"), _spec("b")])
    ] == ["b"]


def test_process_chat_payload_merges_instead_of_overwriting():
    from nucllei.utils import middleware

    source = inspect.getsource(middleware.process_chat_payload)
    assert "merge_tool_specs(" in source
