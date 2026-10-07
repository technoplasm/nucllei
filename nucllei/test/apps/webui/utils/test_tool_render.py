"""tool 引数/結果の型駆動レンダリング(issue #20)の単体テスト。

仕様の一次情報: _memos/tool-arg-render.md。
「型 → 正規化ディスクリプタ {kind, language}」の抽出をテストで固定する。
名前・長さヒューリスティックが無いこと(宣言なし = ディスクリプタなし)も含む。
"""

from typing import Optional

import pytest

from nucllei.utils.tool_render import (
    Code,
    Markdown,
    Text,
    apply_render_to_spec,
    extract_render_from_function,
    extract_render_from_spec,
    hljs_to_media_type,
    media_type_to_hljs,
    normalize_hljs_language,
)

PythonCode = Code("python")


####################################
# 言語 id / media type の正規化
####################################


def test_normalize_hljs_language_passthrough_and_alias():
    assert normalize_hljs_language("python") == "python"
    assert normalize_hljs_language("SQL") == "sql"
    assert normalize_hljs_language("shell") == "bash"  # alias
    assert normalize_hljs_language("py") == "python"  # alias
    assert normalize_hljs_language("") == "plaintext"  # fallback
    assert normalize_hljs_language(None) == "plaintext"


def test_media_type_to_hljs_table_prefix_and_fallback():
    assert media_type_to_hljs("text/x-python") == "python"
    assert media_type_to_hljs("application/json") == "json"
    assert media_type_to_hljs("text/x-sql") == "sql"  # 慣用プレフィックス素通し
    assert media_type_to_hljs("text/x-sh") == "bash"  # プレフィックス + alias
    assert (
        media_type_to_hljs("text/X-Python; charset=utf-8") == "python"
    )  # パラメータ除去
    assert media_type_to_hljs("application/octet-stream") == "plaintext"  # 不明
    assert media_type_to_hljs(None) == "plaintext"


def test_hljs_to_media_type_roundtrip():
    # ネイティブ宣言 → spec 反映 → OpenAPI 抽出 で言語が保存される
    for lang in ("python", "sql", "bash", "json"):
        assert media_type_to_hljs(hljs_to_media_type(lang)) == lang
    # テーブル外も text/x-<lang> 慣用形で往復する
    assert media_type_to_hljs(hljs_to_media_type("rust")) == "rust"


####################################
# ネイティブ関数(Annotated)からの抽出
####################################


def test_extract_from_function_code_argument():
    def run_python(code: PythonCode) -> str:
        return code

    assert extract_render_from_function(run_python) == {
        "args": {"code": {"kind": "code", "language": "python"}}
    }


def test_extract_from_function_return_annotation():
    # 戻り型注釈(RenderAs)が result ディスクリプタに抽出されること。
    # ※ table 型は廃止済み(2026-07-09)なので code 戻り型で検証する。
    def run_sql(query: Code("sql")) -> Code("sql"):
        return ""

    render = extract_render_from_function(run_sql)
    assert render["args"] == {"query": {"kind": "code", "language": "sql"}}
    assert render["result"] == {"kind": "code", "language": "sql"}


def test_extract_from_function_text_result():
    def run_python(code: PythonCode) -> Text:
        return ""

    assert extract_render_from_function(run_python)["result"] == {"kind": "text"}


def test_extract_from_function_markdown_result():
    # Markdown 戻り型 = md としてレンダリングして見せる(Code("markdown") とは別契約)
    def read_doc(path: str) -> Markdown:
        return ""

    assert extract_render_from_function(read_doc)["result"] == {"kind": "markdown"}


def test_extract_from_function_no_declaration_is_empty():
    # 宣言が無い引数はディスクリプタを持たない(名前 "code" でも推測しない)
    def run_python(code: str, city: str, count: int) -> str:
        return code

    assert extract_render_from_function(run_python) == {}


def test_extract_from_function_mixed_arguments():
    # 宣言のある引数だけが載る(複数引数の共存)
    def query_db(sql: Code("sql"), limit: int, verbose: bool = False):
        return None

    assert extract_render_from_function(query_db) == {
        "args": {"sql": {"kind": "code", "language": "sql"}}
    }


def test_extract_from_function_skips_reserved_params():
    def tool(code: PythonCode, __user__: Optional[dict] = None) -> str:
        return code

    assert extract_render_from_function(tool) == {
        "args": {"code": {"kind": "code", "language": "python"}}
    }


def test_extract_from_function_alias_language_normalized():
    def run_shell(script: Code("shell")) -> str:
        return script

    assert extract_render_from_function(run_shell)["args"]["script"] == {
        "kind": "code",
        "language": "bash",
    }


####################################
# OpenAPI 由来 spec(contentMediaType)からの抽出
####################################


def _spec(properties: dict) -> dict:
    return {
        "name": "run_python",
        "description": "コードを実行する",
        "parameters": {"type": "object", "properties": properties, "required": []},
    }


def test_extract_from_spec_content_media_type():
    spec = _spec(
        {
            "code": {"type": "string", "contentMediaType": "text/x-python"},
            "timeout": {"type": "integer"},
        }
    )
    assert extract_render_from_spec(spec) == {
        "args": {"code": {"kind": "code", "language": "python"}}
    }


def test_extract_from_spec_without_declaration_is_empty():
    spec = _spec({"code": {"type": "string"}, "city": {"type": "string"}})
    assert extract_render_from_spec(spec) == {}
    assert extract_render_from_spec({"name": "t"}) == {}  # parameters 欠落にも耐える


def test_extract_from_spec_unknown_media_type_falls_back_to_plaintext():
    spec = _spec({"blob": {"type": "string", "contentMediaType": "application/vnd.x"}})
    assert extract_render_from_spec(spec)["args"]["blob"] == {
        "kind": "code",
        "language": "plaintext",
    }


####################################
# spec への反映(ネイティブ宣言 → contentMediaType)
####################################


def test_apply_render_to_spec_injects_content_media_type():
    spec = _spec({"code": {"type": "string"}, "timeout": {"type": "integer"}})
    apply_render_to_spec(
        spec, {"args": {"code": {"kind": "code", "language": "python"}}}
    )
    assert (
        spec["parameters"]["properties"]["code"]["contentMediaType"] == "text/x-python"
    )
    assert "contentMediaType" not in spec["parameters"]["properties"]["timeout"]


def test_apply_render_to_spec_preserves_existing_declaration():
    spec = _spec({"code": {"type": "string", "contentMediaType": "application/sql"}})
    apply_render_to_spec(
        spec, {"args": {"code": {"kind": "code", "language": "python"}}}
    )
    # 既存宣言を上書きしない
    assert (
        spec["parameters"]["properties"]["code"]["contentMediaType"]
        == "application/sql"
    )


def test_apply_render_to_spec_tolerates_missing_property():
    # 宣言はあるが spec に現れない引数(__ 予約引数の除去後など)は無視
    spec = _spec({})
    apply_render_to_spec(
        spec, {"args": {"code": {"kind": "code", "language": "python"}}}
    )
    assert spec["parameters"]["properties"] == {}


####################################
# spec ビルダ経由(pydantic + langchain パイプライン)
####################################


def test_get_tool_specs_injects_content_media_type():
    """Annotated(RenderAs)が include_extras 経由で spec ビルダを通り、
    保存時 spec に contentMediaType として反映される(#20 ステップ2)。"""
    from nucllei.utils.tools import get_tool_specs

    class ToolsModule:
        def run_python(self, code: PythonCode, timeout: int = 30) -> str:
            """Python コードを実行する。
            :param code: 実行するコード
            :param timeout: タイムアウト秒
            """
            return "ok"

    specs = get_tool_specs(ToolsModule())
    assert len(specs) == 1
    assert specs[0]["name"] == "run_python"

    props = specs[0]["parameters"]["properties"]
    # 実行時/スキーマ的には素の string のまま、型情報だけが載る
    assert props["code"]["type"] == "string"
    assert props["code"]["contentMediaType"] == "text/x-python"
    assert "contentMediaType" not in props["timeout"]


####################################
# 値が表示種別を持つ戻り値(Rendered、#153)
####################################


def test_rendered_descriptor():
    from nucllei.utils.tool_render import Rendered

    assert Rendered("x", "markdown").descriptor() == {"kind": "markdown"}
    assert Rendered("x", "text").descriptor() == {"kind": "text"}
    # code の language は hljs id に正規化(alias・空は plaintext)
    assert Rendered("x", "code", "sh").descriptor() == {
        "kind": "code",
        "language": "bash",
    }
    assert Rendered("x", "code").descriptor() == {
        "kind": "code",
        "language": "plaintext",
    }
    # code 以外の language は載せない
    assert Rendered("x", "markdown", "python").descriptor() == {"kind": "markdown"}


def test_rendered_table_descriptor():
    """表(2026-10-07): text は CSV、表に添えるメタは meta(dict)で渡す。
    descriptor は meta があるときだけ meta を持つ。"""
    from nucllei.utils.tool_render import Rendered

    assert Rendered("a,b\n1,2", "table").descriptor() == {"kind": "table"}
    meta = {"rows": 120, "truncated": False, "duration": "0.3s"}
    assert Rendered("a,b\n1,2", "table", meta=meta).descriptor() == {
        "kind": "table",
        "meta": {"rows": 120, "truncated": False, "duration": "0.3s"},
    }
    # 空の meta は meta 無しと同じ
    assert Rendered("a\n1", "table", meta={}).descriptor() == {"kind": "table"}
    # language は table に載せない
    assert Rendered("a\n1", "table", "csv").descriptor() == {"kind": "table"}


def test_rendered_table_content_meta_line():
    """モデルへの本文は nucllei が組み立てる: meta があれば先頭に `# k=v …` の 1 行 + CSV。"""
    from nucllei.utils.tool_render import Rendered

    csv_text = "a,b\n1,2\n"
    assert Rendered(csv_text, "table").content() == csv_text
    assert Rendered(csv_text, "table", meta={}).content() == csv_text
    assert (
        Rendered(
            csv_text,
            "table",
            meta={"rows": 120, "truncated": False, "duration": "0.3s"},
        ).content()
        == "# rows=120 truncated=false duration=0.3s\n" + csv_text
    )
    # bool は小文字、None は空、float は str()、空白 / = / " / \ / 改行 / 空文字は JSON 文字列
    line = Rendered(
        "a\n",
        "table",
        meta={
            "ok": True,
            "none": None,
            "f": 1.5,
            "sp": "a b",
            "eq": "x=y",
            "q": 'say "hi"',
            "bs": "c:\\tmp",
            "nl": "l1\nl2",
            "empty": "",
            "jp": "東京",
        },
    ).content()
    assert line == (
        '# ok=true none= f=1.5 sp="a b" eq="x=y" q="say \\"hi\\"" '
        'bs="c:\\\\tmp" nl="l1\\nl2" empty="" jp=東京\na\n'
    )
    # meta 行は必ず 1 行
    assert line.count("\n") == 2
    # meta の写しを持つ(呼び手の dict を後から書き換えても変わらない)
    meta = {"rows": 1}
    rendered = Rendered("a\n1\n", "table", meta=meta)
    meta["rows"] = 999
    assert rendered.content() == "# rows=1\na\n1\n"
    assert rendered.descriptor() == {"kind": "table", "meta": {"rows": 1}}


@pytest.mark.parametrize(
    "meta",
    [
        {"rows": [1, 2]},
        {"rows": {"a": 1}},
        {"rows": object()},
        {"nan": float("nan")},
        {"inf": float("inf")},
        {1: "x"},
        {"": "x"},
        {"a b": 1},
        {"a=b": 1},
        {'a"b': 1},
        {"a\nb": 1},
        "rows=1",
        [("rows", 1)],
    ],
)
def test_rendered_meta_rejects_non_scalar_and_bad_keys(meta):
    from nucllei.utils.tool_render import Rendered

    with pytest.raises(ValueError):
        Rendered("a\n1", "table", meta=meta)


def test_rendered_meta_only_for_table():
    from nucllei.utils.tool_render import Rendered

    with pytest.raises(ValueError):
        Rendered("x", "text", meta={"rows": 1})
    # None は既定なので他 kind でも通る
    assert Rendered("x", "markdown", meta=None).descriptor() == {"kind": "markdown"}


def test_resolve_result_render_table():
    """Rendered table は resolve を素通しし、切詰め・エラーでは text に落ちる(既存規則)。"""
    from nucllei.utils.tool_render import Rendered, resolve_result_render

    raw = Rendered("a,b\n1,2\n3,4", "table", meta={"rows": 2})
    assert resolve_result_render(raw) == {"kind": "table", "meta": {"rows": 2}}
    # 静的な宣言より強い
    assert resolve_result_render(raw, {"kind": "text"}) == {
        "kind": "table",
        "meta": {"rows": 2},
    }
    assert resolve_result_render(raw, truncated=True) is None
    assert resolve_result_render(raw, failed=True) is None


def test_normalize_tool_result_takes_rendered():
    """同期 middleware と background runner が共有する正規化。モデルへは text だけ。"""
    from nucllei.utils.executions import normalize_tool_result
    from nucllei.utils.tool_render import Rendered

    assert normalize_tool_result(Rendered("# a", "markdown")) == (
        "# a",
        {"kind": "markdown"},
    )
    assert normalize_tool_result(Rendered("a = 1", "code", "py")) == (
        "a = 1",
        {"kind": "code", "language": "python"},
    )
    # table: モデルへは meta 行 + CSV、表示は meta 付きの descriptor
    assert normalize_tool_result(
        Rendered("a,b\n1,2", "table", meta={"rows": 1, "truncated": False})
    ) == (
        "# rows=1 truncated=false\na,b\n1,2",
        {"kind": "table", "meta": {"rows": 1, "truncated": False}},
    )
    # 既存の分岐は不変
    assert normalize_tool_result({"a": 1}) == ('{"a": 1}', {"kind": "json"})
    assert normalize_tool_result("s") == ("s", None)
    assert normalize_tool_result(None) == ("", None)


def test_rendered_return_type_is_not_a_static_declaration():
    """戻り型 Rendered は静的な結果宣言を作らない(FE は calls[].render.result を値の
    render より優先するため、宣言があると呼び出しごとの種別が負ける)。"""
    from typing import Annotated

    from nucllei.utils.tool_render import RenderAs, Rendered

    def plain(path: str) -> Rendered:
        return Rendered("", "text")

    def annotated(path: str) -> Annotated[Rendered, RenderAs(kind="markdown")]:
        return Rendered("", "text")

    assert extract_render_from_function(plain) == {}
    assert extract_render_from_function(annotated) == {}


def test_hljs_language_for_extension():
    from nucllei.utils.tool_render import hljs_language_for_extension

    assert hljs_language_for_extension(".PY") == "python"
    assert hljs_language_for_extension("sh") == "bash"
    assert hljs_language_for_extension("yml") == "yaml"
    assert hljs_language_for_extension("tsx") == "typescript"
    assert hljs_language_for_extension("csv") == "csv"
    # 表に無い物・md は code にしない(推測しない)
    assert hljs_language_for_extension("md") is None
    assert hljs_language_for_extension("txt") is None
    assert hljs_language_for_extension("") is None
    assert hljs_language_for_extension(None) is None
