"""組み込み Function 機構(#52 プランB)の回帰。

fork 本体は Attachment 種別の「仕組み」を持ち、初期/参照実装として既定 Attachment を1本だけ
builtin 同梱する(実ポリシーは案件層 prism の Function)。ここでは:
- 起動時 sync で builtin Attachment 行(type attachment)が Function テーブルにできる
- load_builtin_function が repo モジュールから Attachment インスタンスを返す

Function テーブルは meta 列を持たない(#45/#43)ため、builtin 判別は id 接頭辞一本。
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestBuiltinFunctions(AbstractPostgresTest):
    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.functions import Functions

        cls.Functions = Functions

    def _sync(self):
        from open_webui.builtin_functions import sync_builtin_functions

        sync_builtin_functions()

    def test_sync_creates_builtin_attachment_row(self):
        self._sync()

        att = self.Functions.get_function_by_id("builtin_attachment")
        assert att is not None
        assert att.type == "attachment"
        assert att.content == ""

    def test_sync_is_idempotent(self):
        self._sync()
        self._sync()  # 二度目でも例外なく upsert
        assert self.Functions.get_function_by_id("builtin_attachment") is not None

    def test_load_builtin_function_returns_instance(self):
        from open_webui.builtin_functions import (
            is_builtin_function_id,
            load_builtin_function,
        )

        assert is_builtin_function_id("builtin_attachment")
        inst, typ, _ = load_builtin_function("builtin_attachment")
        assert typ == "attachment"
        assert type(inst).__name__ == "Attachment"
        assert hasattr(inst, "process")
