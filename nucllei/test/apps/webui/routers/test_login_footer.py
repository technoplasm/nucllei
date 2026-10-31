"""ログイン画面・承認待ち/無効化の画面に出す配備側の案内(#157)。

`WEBUI_FOOTER_TEXT`(平文)と `WEBUI_CONTACT_EMAIL` を `/api/config` が未認証でも返す。
空なら空文字のまま返し、FE は何も描かない。値は加工せずそのまま返す(改行も保つ)。
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestLoginFooter(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    def test_unset_is_empty(self, monkeypatch):
        import nucllei.main as main_module

        monkeypatch.setattr(main_module, "WEBUI_FOOTER_TEXT", "")
        monkeypatch.setattr(main_module, "WEBUI_CONTACT_EMAIL", "")
        response = self.fast_api_client.get("/api/config")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["footer_text"] == ""
        assert body["contact_email"] == ""

    def test_unauthenticated_config_returns_both(self, monkeypatch):
        import nucllei.main as main_module

        text = "医療情報部 内線 1234\n平日 9:00-17:00"
        monkeypatch.setattr(main_module, "WEBUI_FOOTER_TEXT", text)
        monkeypatch.setattr(main_module, "WEBUI_CONTACT_EMAIL", "helpdesk@example.org")
        self.fast_api_client.cookies.clear()
        response = self.fast_api_client.get("/api/config")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["footer_text"] == text
        assert body["contact_email"] == "helpdesk@example.org"
