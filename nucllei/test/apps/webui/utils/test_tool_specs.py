"""tool モジュール → spec 生成の外形(#18-3)。

v0.6.5 は dunder(`__`)しか除外せず、`_` 始まりのプライベートヘルパをメソッドとして
持つ tool(sandbox の _post/_exec 等)が全部モデルに露出していた。`_` 始まりは
tool 作者のプライベートとして spec 化しない。
"""

from nucllei.utils.tools import get_functions_from_tool, get_tool_specs


class FakeTools:
    def run_python(self, code: str) -> str:
        """Python コードを実行する。

        :param code: 実行するコード
        """
        return code

    def list_workspace(self, path: str = ".") -> str:
        """workspace の一覧を返す。

        :param path: 対象ディレクトリ
        """
        return path

    async def _post(self, path: str, payload: dict) -> dict:
        return {}

    def _format_result(self, res: dict) -> str:
        return ""


def test_private_methods_are_not_exposed():
    names = {f.__name__ for f in get_functions_from_tool(FakeTools())}
    assert names == {"run_python", "list_workspace"}


def test_specs_only_contain_public_functions():
    specs = get_tool_specs(FakeTools())
    assert sorted(s["name"] for s in specs) == ["list_workspace", "run_python"]


# --- description の切り出し(2026-10-04) -------------------------------------------
# モデルに見える description はソースのインデントを落とす(毎ターン送る token の節約)。
# 保存時 spec(get_tool_specs = sync / create / update)とターン時 spec(get_tools)は
# 同じ helper で切り出す — v0.6.5 は保存時の description が最後の引数の説明に化けていた。


class IndentedTools:
    def search(self, query: str, limit: int = 10) -> str:
        """文書を検索する。

        - 箇条書きの 1 行目
          継続行(相対インデントは残す)

        戻り値は一覧。

        :param query: 検索語
        :param limit: 返す件数 (1〜50、既定 10)。
        :return: 一覧
        """
        return query

    def bare(self) -> str:
        return ""


def test_description_drops_source_indentation_and_keeps_layout():
    from nucllei.utils.tools import tool_description_from_docstring

    expected = (
        "文書を検索する。\n\n"
        "- 箇条書きの 1 行目\n"
        "  継続行(相対インデントは残す)\n\n"
        "戻り値は一覧。"
    )
    assert tool_description_from_docstring(IndentedTools.search.__doc__) == expected
    spec = next(s for s in get_tool_specs(IndentedTools()) if s["name"] == "search")
    assert spec["description"] == expected
    props = spec["parameters"]["properties"]
    assert props["query"]["description"] == "検索語"
    assert props["limit"]["description"] == "返す件数 (1〜50、既定 10)。"


def test_stored_description_is_not_the_last_param_description():
    """v0.6.5 の不具合の回帰: convert_function_to_pydantic_model がループ内で関数の
    description を引数の説明で上書きし、保存時 spec の description が「最後の引数の
    説明」になっていた(grep_doc の行が「返す件数 (1〜50、既定 10)。」に化けた実例)。"""
    specs = {s["name"]: s for s in get_tool_specs(IndentedTools())}
    assert specs["search"]["description"].startswith("文書を検索する。")
    assert "返す件数" not in specs["search"]["description"]
    # docstring 無しは関数名(ターン時と同じ fallback)
    assert specs["bare"]["description"] == "bare"


def test_builtin_specs_have_no_source_indentation():
    """全 builtin の保存時 spec: description の各行はソースのインデントを持たない
    (行頭 8 桁の空白が無い)。"""
    from nucllei.builtin_tools import BUILTIN_TOOLS

    for tool_id, entry in BUILTIN_TOOLS.items():
        for spec in get_tool_specs(entry["module"].Tools()):
            desc = spec["description"]
            assert desc == desc.strip(), (tool_id, spec["name"])
            assert not any(ln.startswith("        ") for ln in desc.splitlines()), (
                tool_id,
                spec["name"],
            )
