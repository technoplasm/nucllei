"""組み込み Function 機構(#52 プランB)の回帰。

- 起動時 sync で builtin 行(attachment 種別 / filter 種別)が Function テーブルにできる
- filter ディスパッチ(utils/filter.py)が builtin filter を load_builtin_function 経由で
  ロードして実行できる(content が repo 由来=DB 空でも動く)

Function テーブルは meta 列を持たない(#45/#43)ため、builtin 判別は id 接頭辞一本。
"""

import asyncio
from types import SimpleNamespace

from test.util.abstract_integration_test import AbstractPostgresTest


def _make_request(functions=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(FUNCTIONS=dict(functions or {})))
    )


def _refs(*ids):
    return [SimpleNamespace(id=i) for i in ids]


class TestBuiltinFunctions(AbstractPostgresTest):
    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.functions import Functions

        cls.Functions = Functions

    def _sync(self):
        from open_webui.builtin_functions import sync_builtin_functions

        sync_builtin_functions()

    def test_sync_creates_builtin_rows_with_expected_types(self):
        self._sync()

        att = self.Functions.get_function_by_id("builtin_attachment")
        assert att is not None
        assert att.type == "attachment"
        assert att.content == ""

        gate = self.Functions.get_function_by_id("builtin_large_file_gate")
        assert gate is not None
        assert gate.type == "filter"

    def test_sync_is_idempotent(self):
        self._sync()
        self._sync()  # 二度目でも例外なく upsert
        assert self.Functions.get_function_by_id("builtin_attachment") is not None

    def test_filter_dispatch_loads_builtin_filter(self):
        """process_filter_functions が builtin filter を load_builtin_function 経由で
        ロードして inlet を回す(添付なし body は素通しで戻る)。"""
        from open_webui.utils.filter import process_filter_functions

        self._sync()
        request = _make_request()  # FUNCTIONS 空 → ローダ経路を通す

        body = {"messages": [{"role": "user", "content": "hi"}]}
        out, _ = asyncio.run(
            process_filter_functions(
                request=request,
                filter_functions=_refs("builtin_large_file_gate"),
                filter_type="inlet",
                form_data=body,
                extra_params={},
            )
        )

        # 添付が無いので素通し。かつ builtin モジュールがキャッシュに載る(=ロード成功)。
        assert out["messages"][-1]["content"] == "hi"
        assert "builtin_large_file_gate" in request.app.state.FUNCTIONS
        assert type(
            request.app.state.FUNCTIONS["builtin_large_file_gate"]
        ).__name__ == ("Filter")
