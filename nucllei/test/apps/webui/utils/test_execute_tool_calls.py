"""execute_tool_calls の引数パースと結果整形の回帰。

- 引数パースは厳密 JSON 一次(OpenAI 互換の正規形。Kimi 実測も厳密 JSON —
  _memos/model-frames.md)、Python リテラル風は ast.literal_eval で救済。
- #89: tool 結果 list からの data URL 無差別抽出(旧 files magic)は廃止。
  VLM 注入は __feed_image__(file_id)→context_images の型付き経路のみ
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
    VLM 注入は __feed_image__(file_id)→context_images の型付き経路のみ。"""
    data_a = "data:image/png;base64,AAAA"

    async def probe():
        return [data_a, "こんにちは"]

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(probe)))

    assert "files" not in results[0]
    assert "context_images" not in results[0]
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


def test_dict_result_keeps_non_ascii_unescaped():
    """dict/list 戻りの直列化は ensure_ascii=False。既定(True)だと日本語が 1 文字ごとに
    \\uXXXX(6 文字)へ展開され、モデルに渡るトークンが倍以上に膨らむ。"""

    async def returns_japanese():
        return {"title": "針刺し・切創", "note": "曝露時の対応"}

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_japanese)))
    content = results[0]["content"]
    assert "針刺し・切創" in content
    assert "\\u" not in content
    # 内容自体は往復して壊れない
    assert json.loads(content) == {"title": "針刺し・切創", "note": "曝露時の対応"}


def test_dict_result_is_not_pretty_printed():
    """dict/list 戻りに indent は付けない(#99)。

    フロントは kind=json の content をパースして JSON.stringify(x, null, 2) で
    整形し直す(src/lib/utils/tool-render/index.ts の formatJSONString)ので、
    ここで入れた空白は表示に一切寄与せず、モデルだけがトークンを払う。
    """

    async def returns_nested():
        return {"items": [{"title": "針刺し・切創", "dept": "感染制御部"}]}

    content = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_nested)))[0][
        "content"
    ]

    assert "\n" not in content
    assert content == '{"items": [{"title": "針刺し・切創", "dept": "感染制御部"}]}'
    # 値としては当然そのまま
    assert json.loads(content) == {
        "items": [{"title": "針刺し・切創", "dept": "感染制御部"}]
    }


def test_list_result_has_no_image_side_channel_key():
    """list 結果は画像サイドチャネルキー(旧 files / 新 context_images)を持たない
    — JSON 直列化のみ。"""

    async def probe():
        return ["a", "b"]

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(probe)))

    assert "files" not in results[0]
    assert "context_images" not in results[0]
    assert json.loads(results[0]["content"]) == ["a", "b"]


def test_rendered_result_carries_its_own_render(monkeypatch):
    """#153: `Rendered(text, kind, language)` は値が表示種別を持つ戻り値。content は text だけ
    (モデルへも text だけ)、results[].render に {kind, language?}。切詰め・エラーでは落ちる
    (既存の規則がそのまま効く)。"""
    from nucllei.utils import executions
    from nucllei.utils.tool_errors import ToolOutcome
    from nucllei.utils.tool_render import Rendered

    async def returns_markdown():
        return Rendered("# 見出し\n\n本文", "markdown")

    async def returns_code():
        return Rendered("print(1)\n", "code", "py")

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_markdown)))
    assert results[0]["content"] == "# 見出し\n\n本文"
    assert results[0]["render"] == {"kind": "markdown"}

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_code)))
    assert results[0]["content"] == "print(1)\n"
    assert results[0]["render"] == {"kind": "code", "language": "python"}

    async def raises():
        raise ToolOutcome("無い")

    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(raises)))
    assert results[0]["error_kind"] == "outcome"
    assert "render" not in results[0]

    monkeypatch.setattr(executions, "TOOL_RESULT_MAX_CHARS", 5)
    results = asyncio.run(execute_tool_calls([_call("{}")], _tool(returns_markdown)))
    assert results[0]["truncated"] is True
    assert "render" not in results[0]


def test_result_render_priority_on_the_sync_path(monkeypatch):
    """#153: 結果の表示は backend が 1 つに確定する(resolve_result_render)。
    エラー・切詰め → text、Rendered > 静的な宣言 > dict(json) > text。"""
    from nucllei.utils import tool_output
    from nucllei.utils.tool_render import Rendered

    def declared(callable_, result):
        tools = _tool(callable_)
        tools["probe"]["render"] = {"result": result}
        return tools

    def run(tools):
        return asyncio.run(execute_tool_calls([_call("{}")], tools))[0]

    async def as_dict():
        return {"ok": True}

    async def as_rendered():
        return Rendered("# a", "markdown")

    async def as_str():
        return "s"

    async def flagged():
        tool_output.mark_error_kind("outcome")
        return {"hits": []}

    # 宣言あり + dict → 宣言(subagent / skill は dict の形への宣言)
    assert run(declared(as_dict, {"kind": "subagent"}))["render"] == {
        "kind": "subagent"
    }
    # 宣言あり + Rendered → Rendered
    assert run(declared(as_rendered, {"kind": "text"}))["render"] == {
        "kind": "markdown"
    }
    # 宣言あり + str → 宣言
    assert run(declared(as_str, {"kind": "code", "language": "sql"}))["render"] == {
        "kind": "code",
        "language": "sql",
    }
    # 宣言なし + dict → json、宣言なし + str → text(render なし)
    assert run(_tool(as_dict))["render"] == {"kind": "json"}
    assert "render" not in run(_tool(as_str))
    # 旗だけの結果は render を残す(宣言なし dict → json)
    out = run(_tool(flagged))
    assert out["error_kind"] == "outcome" and out["render"] == {"kind": "json"}

    async def raises():
        from nucllei.utils.tool_errors import ToolOutcome

        raise ToolOutcome("無い")

    # エラーは宣言があっても text
    out = run(declared(raises, {"kind": "subagent"}))
    assert out["error_kind"] == "outcome" and "render" not in out

    from nucllei.utils import executions

    monkeypatch.setattr(executions, "TOOL_RESULT_MAX_CHARS", 3)
    out = run(declared(as_dict, {"kind": "subagent"}))
    assert out["truncated"] is True and "render" not in out
