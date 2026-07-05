"""llens 既定値プロファイルの回帰テスト(_memos/llens-defaults.md)。

環境変数未設定・fresh DB でこの値になることを固定する。
upstream 取り込みや設定リファクタで既定値が戻るデグレを検知する。
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestLlensDefaults(AbstractPostgresTest):
    BASE_PATH = "/api"

    def test_code_execution_disabled_and_jupyter_engine(self):
        from open_webui.main import app

        # issue #11: 組み込みコード実行は既定 OFF。実行は nsjail 外部サンドボックス側
        assert app.state.config.ENABLE_CODE_EXECUTION is False
        assert app.state.config.ENABLE_CODE_INTERPRETER is False
        # 有効化する場合も Pyodide には戻さない(CLAUDE.md 絶対制約)
        assert app.state.config.CODE_EXECUTION_ENGINE == "jupyter"
        assert app.state.config.CODE_INTERPRETER_ENGINE == "jupyter"

    def test_arena_and_community_sharing_disabled(self):
        from open_webui.main import app

        # issue #10: evaluation/arena と openwebui.com 共有導線は既定 OFF
        assert app.state.config.ENABLE_EVALUATION_ARENA_MODELS is False
        assert app.state.config.ENABLE_COMMUNITY_SHARING is False
