"""tool モジュール → spec 生成の外形(#18-3)。

v0.6.5 は dunder(`__`)しか除外せず、`_` 始まりのプライベートヘルパをメソッドとして
持つ tool(sandbox の _post/_exec 等)が全部モデルに露出していた。`_` 始まりは
tool 作者のプライベートとして spec 化しない。
"""

from open_webui.utils.tools import get_functions_from_tool, get_tool_specs


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
