from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestUtils(AbstractPostgresTest):
    BASE_PATH = "/api/v1/utils"

    def test_code_execute_blocked_when_disabled(self):
        """GHSA-482j-2pq6-q5w4: ENABLE_CODE_EXECUTION=False では /code/execute を拒否する。

        既定(False)で任意の認証ユーザーがコード実行できてしまう穴を塞ぐ。llens では
        コード実行は自前 sandbox(nsjail+IPython)をツール経路で使うため、組み込みの
        /code/execute は既定で無効。
        """
        config = self.fast_api_client.app.state.config
        original = config.ENABLE_CODE_EXECUTION
        config.ENABLE_CODE_EXECUTION = False
        try:
            with mock_webui_user():
                response = self.fast_api_client.post(
                    self.create_url("/code/execute"),
                    json={"code": "print(1)"},
                )
        finally:
            config.ENABLE_CODE_EXECUTION = original

        assert response.status_code == 403
