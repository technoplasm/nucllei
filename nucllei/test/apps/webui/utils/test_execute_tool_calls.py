"""execute_tool_calls の引数パースと生成ファイル(data URL)抽出の回帰。

- 引数パースは厳密 JSON 一次(OpenAI 互換の正規形。Kimi 実測も厳密 JSON —
  _memos/model-frames.md)、Python リテラル風は ast.literal_eval で救済。
- tool 結果 list 内の data URL は全部 files に抜ける
  (旧実装はイテレーション中 list.remove で要素を取りこぼしていた)。
"""

import asyncio
import json

from nucllei.utils.middleware import execute_tool_calls


def _tool(callable_, name="probe", properties=None):
    return {
        name: {
            "callable": callable_,
            "spec": {"name": name, "parameters": {"properties": properties or {}}},
        }
    }


def _call(arguments, name="probe", index=0):
    return {
        "id": f"functions.{name}:{index}",
        "function": {"name": name, "arguments": arguments},
    }


def test_arguments_parse_json_first():
    """一次パースは厳密 JSON。JSON でしか解釈できない値(true)も正しく通る
    (ast.literal_eval なら true は NameError で落ちる)。"""
    captured = []

    async def probe(**kwargs):
        captured.append(kwargs)
        return "ok"

    tools = _tool(
        probe,
        properties={"city": {"type": "string"}, "flag": {"type": "boolean"}},
    )
    results = asyncio.run(
        execute_tool_calls([_call('{"city": "札幌", "flag": true}')], tools)
    )

    assert captured == [{"city": "札幌", "flag": True}]
    assert results[0]["tool_call_id"] == "functions.probe:0"
    assert results[0]["content"] == "ok"


def test_arguments_fall_back_to_python_literal():
    """厳密 JSON で壊れている Python リテラル風(単引用符)は ast.literal_eval で
    救済される(Python リテラル風を出すモデル向けのフォールバック)。"""
    captured = []

    async def probe(**kwargs):
        captured.append(kwargs)
        return "ok"

    tools = _tool(probe, properties={"city": {"type": "string"}})
    asyncio.run(execute_tool_calls([_call("{'city': '帯広'}")], tools))

    assert captured == [{"city": "帯広"}]


def test_list_result_extracts_all_data_urls_into_files():
    """tool 結果 list 内の複数 data URL が全部 files に抜ける
    (旧 list.remove バグ: イテレーション中の remove で 2 個目を取りこぼしていた)。"""
    data_a = "data:image/png;base64,AAAA"
    data_b = "data:image/jpeg;base64,BBBB"

    async def probe():
        return [data_a, "こんにちは", data_b]

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(probe)))

    assert results[0]["files"] == [data_a, data_b]
    # content からは data URL が除かれ、残りが JSON 直列化される
    assert json.loads(results[0]["content"]) == ["こんにちは"]


def test_non_str_scalar_results_are_stringified():
    """content は常に文字列の wire 契約: int/float/bool は str()、None は空文字
    ("None" という見かけの出力を作らない)。str/スカラーに render は付かない
    (フロント既定 = text)。"""

    async def returns_int():
        return 42

    async def returns_none():
        return None

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_int)))
    assert results[0]["content"] == "42"
    assert "render" not in results[0]

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_none)))
    assert results[0]["content"] == ""
    assert "render" not in results[0]


def test_dict_result_carries_json_render_kind():
    """dict/list 戻りは json.dumps + 実型由来の render {kind: json} を同梱する
    (「JSON に見えるか」の動的解釈をフロントにさせない)。str 戻りには付けない。"""

    async def returns_dict():
        return {"ok": True}

    async def returns_str():
        return '{"ok": true}'  # JSON 風の str は str のまま(render なし)

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_dict)))
    assert json.loads(results[0]["content"]) == {"ok": True}
    assert results[0]["render"] == {"kind": "json"}

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_str)))
    assert results[0]["content"] == '{"ok": true}'
    assert "render" not in results[0]


def test_list_result_without_data_urls_has_no_files_key():
    """data URL を含まない list 結果は files キーを持たない(JSON 直列化のみ)。"""

    async def probe():
        return ["a", "b"]

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(probe)))

    assert "files" not in results[0]
    assert json.loads(results[0]["content"]) == ["a", "b"]
