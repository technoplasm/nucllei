from test.util.abstract_integration_test import AbstractPostgresTest


class TestUtils(AbstractPostgresTest):
    BASE_PATH = "/api/v1/utils"

    def test_removed_endpoints_absent(self):
        """llens(#67): utils の撤去 endpoint がルート表に存在しないことを固定。

        - /code/execute: 組み込みコード実行(GHSA-482j の旧ゲートを撤去固定に置換。
          実行系は自前 sandbox(nsjail+IPython)のツール経路一本)
        - /gravatar: 外部ネットワーク必須(オフライン完結方針に反する)
        - /markdown, /litellm/config: orphan 残骸
        ※ 消した path は SPA フォールバックが受けるため HTTP ステータスでは検証しない。
        """
        from nucllei.main import app

        paths = app.openapi()["paths"].keys()
        assert "/api/v1/utils/code/execute" not in paths
        assert "/api/v1/utils/gravatar" not in paths
        assert "/api/v1/utils/markdown" not in paths
        assert "/api/v1/utils/litellm/config" not in paths
        # 残す側(存在確認)
        assert "/api/v1/utils/code/format" in paths
        assert "/api/v1/utils/pdf" in paths
        assert "/api/v1/utils/db/download" in paths
