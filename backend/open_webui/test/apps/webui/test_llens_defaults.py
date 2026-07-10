"""llens 既定値プロファイルの回帰テスト(_memos/llens-defaults.md)。

環境変数未設定・fresh DB でこの値になることを固定する。
upstream 取り込みや設定リファクタで既定値が戻るデグレを検知する。
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestLlensDefaults(AbstractPostgresTest):
    BASE_PATH = "/api"

    def test_code_execution_disabled_and_jupyter_engine(self):
        from open_webui.main import app

        # issue #67: 組み込みコード実行(CODE_EXECUTION_*)は config ごと撤去。
        # 実行系は nsjail 外部サンドボックスのツール経路一本
        assert not hasattr(app.state.config, "ENABLE_CODE_EXECUTION")
        assert not hasattr(app.state.config, "CODE_EXECUTION_ENGINE")
        # interpreter は既定 OFF・有効化時も Pyodide には戻さない(CLAUDE.md 絶対制約)
        assert app.state.config.ENABLE_CODE_INTERPRETER is False
        assert app.state.config.CODE_INTERPRETER_ENGINE == "jupyter"

    def test_arena_and_community_sharing_eradicated(self):
        from open_webui.main import app

        # llens(#48): evaluation/arena は撲滅(config ごと撤去)。config 属性が存在しないこと。
        assert not hasattr(app.state.config, "ENABLE_EVALUATION_ARENA_MODELS")
        assert not hasattr(app.state.config, "EVALUATION_ARENA_MODELS")
        # llens(#10): openwebui.com コミュニティ共有(ストア導線)も撲滅(config ごと撤去)。
        assert not hasattr(app.state.config, "ENABLE_COMMUNITY_SHARING")
