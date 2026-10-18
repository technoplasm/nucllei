"""convert_content_blocks_to_messages の reasoning の扱い(2026-10-19 ken 決定)。

- keep_reasoning=True(tool ループの追撃): 各 assistant の reasoning_content 欄に
  その区間の reasoning 本文を載せる。content には混ぜない・マークアップを付けない
- keep_reasoning=False(既定 — 次ターン以降の履歴・続きターン・subagent・carried 計測):
  reasoning は一切載らない(#18-2 以来の形)
"""

from nucllei.utils.content_blocks import convert_content_blocks_to_messages


def _two_round_blocks():
    """reasoning → text → tool_calls → reasoning → tool_calls → reasoning → text。"""
    return [
        {"type": "reasoning", "content": "  札幌の天気を調べる  ", "ended_at": 1},
        {"type": "text", "content": "調べます。"},
        {
            "type": "tool_calls",
            "content": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":"札幌"}'},
                }
            ],
            "results": [{"tool_call_id": "c1", "content": "晴れ"}],
        },
        {"type": "reasoning", "content": "次は東京", "ended_at": 2},
        {"type": "reasoning", "content": "念のため", "ended_at": 3},
        {
            "type": "tool_calls",
            "content": [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":"東京"}'},
                }
            ],
            "results": [{"tool_call_id": "c2", "content": "雨"}],
        },
        {"type": "reasoning", "content": "まとめる", "ended_at": 4},
        {"type": "text", "content": "札幌は晴れ、東京は雨です。"},
    ]


def _assistants(messages):
    return [m for m in messages if m["role"] == "assistant"]


def test_keep_reasoning_attaches_each_round_to_its_assistant():
    assistants = _assistants(
        convert_content_blocks_to_messages(_two_round_blocks(), keep_reasoning=True)
    )
    assert [m.get("reasoning_content") for m in assistants] == [
        "札幌の天気を調べる",
        "次は東京\n\n念のため",
        "まとめる",
    ]
    # content には混ぜない(#18-2 の回帰 — マークアップも本文も入らない)
    assert [m["content"] for m in assistants] == [
        "調べます。",
        "",
        "札幌は晴れ、東京は雨です。",
    ]
    for m in assistants:
        assert "<details" not in m["content"]
        assert "<think>" not in m["reasoning_content"]


def test_default_drops_reasoning():
    blocks = _two_round_blocks()
    messages = convert_content_blocks_to_messages(blocks)
    assert all("reasoning_content" not in m for m in messages)
    # reasoning を載せる以外は keep_reasoning=True と同じ形
    kept = convert_content_blocks_to_messages(blocks, keep_reasoning=True)
    for m in kept:
        m.pop("reasoning_content", None)
    assert messages == kept


def test_keep_reasoning_omits_empty_field():
    blocks = [
        {"type": "reasoning", "content": "   ", "ended_at": 1},
        {
            "type": "tool_calls",
            "content": [{"id": "c1", "type": "function", "function": {"name": "p"}}],
            "results": [{"tool_call_id": "c1", "content": "ok"}],
        },
    ]
    (assistant,) = _assistants(
        convert_content_blocks_to_messages(blocks, keep_reasoning=True)
    )
    assert "reasoning_content" not in assistant


def test_keep_reasoning_trailing_reasoning_only_makes_no_assistant():
    # 本文の無い末尾 assistant は作らない(上流で prefill 扱いになりうる)
    blocks = [
        {
            "type": "tool_calls",
            "content": [{"id": "c1", "type": "function", "function": {"name": "p"}}],
            "results": [{"tool_call_id": "c1", "content": "ok"}],
        },
        {"type": "reasoning", "content": "考え中"},
    ]
    messages = convert_content_blocks_to_messages(blocks, keep_reasoning=True)
    assert messages[-1]["role"] == "tool"
