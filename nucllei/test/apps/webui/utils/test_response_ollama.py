"""ollama 応答 → OpenAI 形式の変換(utils/response.py)。

主眼は日本語のトークン効率(#99 A-2): tool call の arguments はここで作られた
文字列がそのまま assistant message に載り、以降のターンで会話履歴として
毎回モデルへ再送される。\\uXXXX 展開されたまま残ると損失が累積する。
"""

import json

from nucllei.utils.response import (
    convert_ollama_tool_call_to_openai,
    convert_response_ollama_to_openai,
)


def test_tool_call_arguments_keep_non_ascii_unescaped():
    """arguments の直列化は ensure_ascii=False。既定(True)だと日本語の引数が
    1 文字ごとに \\uXXXX(6 文字)へ展開され、履歴として再送されるたびに効く。"""

    calls = convert_ollama_tool_call_to_openai(
        [
            {
                "index": 0,
                "id": "call_1",
                "function": {
                    "name": "grep_doc",
                    "arguments": {"query": "針刺し・切創", "path": "感染対策"},
                },
            }
        ]
    )

    arguments = calls[0]["function"]["arguments"]
    assert "針刺し・切創" in arguments
    assert "\\u" not in arguments
    # 内容は往復して壊れない(モデルが読む JSON としての同一性)
    assert json.loads(arguments) == {"query": "針刺し・切創", "path": "感染対策"}


def test_tool_call_arguments_escaping_costs_tokens():
    """エスケープ有無で文字列長がどれだけ変わるかを固定(回帰の可視化)。
    日本語 1 文字は 1 → 6 文字になるため、素の json.dumps は大幅に長い。"""

    args = {"query": "針刺し・切創"}
    calls = convert_ollama_tool_call_to_openai(
        [{"function": {"name": "f", "arguments": args}}]
    )

    ours = calls[0]["function"]["arguments"]
    escaped = json.dumps(args)  # 既定 = ensure_ascii=True
    assert len(ours) < len(escaped)
    # 非 ASCII 1 文字あたり +5 文字(1 → \uXXXX の 6 文字)。「・」も非 ASCII。
    assert len(escaped) - len(ours) == 5 * len("針刺し・切創")


def test_missing_arguments_still_serializes_to_empty_object():
    """arguments 欠落時は空 JSON オブジェクト(既存の契約を変えない)。"""

    calls = convert_ollama_tool_call_to_openai([{"function": {"name": "f"}}])
    assert calls[0]["function"]["arguments"] == "{}"
    assert calls[0]["function"]["name"] == "f"
    assert calls[0]["id"].startswith("call_")


def test_full_response_conversion_carries_unescaped_arguments():
    """変換の入口(非ストリーム応答)からも同じ文字列が出ることを見る。"""

    response = convert_response_ollama_to_openai(
        {
            "model": "kimi-k2.6",
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "grep_doc", "arguments": {"q": "曝露"}}}
                ],
            },
            "done": True,
        }
    )

    tool_calls = response["choices"][0]["message"]["tool_calls"]
    assert tool_calls[0]["function"]["arguments"] == '{"q": "曝露"}'
