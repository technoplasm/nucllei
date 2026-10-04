"""messages 加工ヘルパの非破壊性(2026-10-04)。

routers/openai.py は form_data の浅いコピーに system prompt を掛け、middleware の
tool ループ追撃は form_data["messages"] の dict を共有したまま新しい list を組む。
ヘルパが共有 dict を in-place で書き換えると、追撃のたびに preset system が積み重なる
(test_native_tool_calling.py の tool ループ回帰テストが端到端で固定)。ここでは
各ヘルパ単体で「入力の list も dict も変えない」ことを固定する。
"""

import copy

from nucllei.routers.openai import openai_o1_o3_handler
from nucllei.utils.misc import (
    add_or_update_system_message,
    add_or_update_user_message,
    append_or_update_assistant_message,
    prepend_to_first_user_message_content,
)


def _conversation():
    return [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": [{"type": "text", "text": "Q"}]},
        {"role": "assistant", "content": "A"},
        {"role": "user", "content": "Q2"},
    ]


def test_add_or_update_system_message_prepends_without_mutating():
    messages = _conversation()
    before = copy.deepcopy(messages)

    result = add_or_update_system_message("PRESET", messages)

    assert result[0] == {"role": "system", "content": "PRESET\nSYS"}
    assert result[1:] == before[1:]
    assert messages == before
    assert result is not messages


def test_add_or_update_system_message_inserts_without_mutating():
    messages = [{"role": "user", "content": "Q"}]
    before = copy.deepcopy(messages)

    result = add_or_update_system_message("PRESET", messages)

    assert result == [{"role": "system", "content": "PRESET"}, *before]
    assert messages == before


def test_repeated_application_on_shared_dicts_does_not_accumulate():
    """tool ループの再現: 毎回「元の list を写した新しい list」に掛けても 1 枚のまま。"""
    original = [{"role": "user", "content": "Q"}]
    first = add_or_update_system_message("PRESET", original)
    for _ in range(3):
        again = add_or_update_system_message("PRESET", [*original])
        assert again[0]["content"] == "PRESET"
    assert first[0]["content"] == "PRESET"
    assert original == [{"role": "user", "content": "Q"}]


def test_user_and_assistant_helpers_do_not_mutate():
    messages = _conversation()
    before = copy.deepcopy(messages)

    assert add_or_update_user_message("X", messages)[-1]["content"] == "Q2\nX"
    assert append_or_update_assistant_message("X", messages)[-1] == {
        "role": "assistant",
        "content": "X",
    }
    assert append_or_update_assistant_message("X", messages[:3])[-1]["content"] == (
        "A\nX"
    )
    assert add_or_update_user_message("X", messages[:3])[-1] == {
        "role": "user",
        "content": "X",
    }
    assert messages == before


def test_prepend_to_first_user_message_content_does_not_mutate():
    messages = _conversation()
    before = copy.deepcopy(messages)

    result = prepend_to_first_user_message_content("CTX", messages)

    assert result[1]["content"] == [{"type": "text", "text": "CTX\nQ"}]
    assert result[3] == before[3]  # 2 つ目以降の user は触らない
    assert messages == before

    plain = [{"role": "user", "content": "Q"}]
    assert prepend_to_first_user_message_content("CTX", plain)[0]["content"] == (
        "CTX\nQ"
    )
    assert plain == [{"role": "user", "content": "Q"}]


def test_o1_o3_handler_swaps_system_role_without_mutating_shared_dict():
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}]
    payload = {"model": "o3-mini", "messages": messages, "max_tokens": 10}

    result = openai_o1_o3_handler(payload)

    assert result["messages"][0] == {"role": "developer", "content": "S"}
    assert result["max_completion_tokens"] == 10
    assert messages[0] == {"role": "system", "content": "S"}
