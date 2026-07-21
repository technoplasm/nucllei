"""execute_tool_calls の引数パースと結果整形の回帰。

- 引数パースは厳密 JSON 一次(OpenAI 互換の正規形。Kimi 実測も厳密 JSON —
  _memos/model-frames.md)、Python リテラル風は ast.literal_eval で救済。
- #89: tool 結果 list からの data URL 無差別抽出(旧 files magic)は廃止。
  VLM 注入は __feed_image__(file_id)→model_images の型付き経路のみ
  (test_tool_output.py)。list はそのまま JSON 直列化される。
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


def test_list_result_data_urls_are_no_longer_extracted():
    """#89: list 戻りからの data URL 無差別抽出(旧 files magic)は廃止。list は
    そのまま JSON 直列化され、data URL も content に残る(勝手に画像化しない)。
    VLM 注入は __feed_image__(file_id)→model_images の型付き経路のみ。"""
    data_a = "data:image/png;base64,AAAA"

    async def probe():
        return [data_a, "こんにちは"]

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(probe)))

    assert "files" not in results[0]
    assert "model_images" not in results[0]
    assert json.loads(results[0]["content"]) == [data_a, "こんにちは"]
    assert results[0]["render"] == {"kind": "json"}


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


def test_list_result_has_no_image_side_channel_key():
    """list 結果は画像サイドチャネルキー(旧 files / 新 model_images)を持たない
    — JSON 直列化のみ。"""

    async def probe():
        return ["a", "b"]

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(probe)))

    assert "files" not in results[0]
    assert "model_images" not in results[0]
    assert json.loads(results[0]["content"]) == ["a", "b"]
