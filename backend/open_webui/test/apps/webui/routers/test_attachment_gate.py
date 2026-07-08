"""添付ファイルの受け入れゲート(#52)の回帰。

upload の入口(Attachment 前)で拡張子 whitelist とサイズ上限を server-side 強制する。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestAttachmentGate(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def _upload(self, filename, content, content_type="application/octet-stream"):
        with mock_webui_user(id="1", role="admin"):
            return self.fast_api_client.post(
                self.create_url("/?process=false"),
                files={"file": (filename, content, content_type)},
            )

    def test_disallowed_extension_rejected(self):
        from open_webui.main import app

        app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS = ["pdf", "docx"]
        try:
            r = self._upload("malware.exe", b"MZ...")
            assert r.status_code == 400, r.text
            assert "許可されていない" in r.text

            # whitelist にある拡張子は通る
            ok = self._upload("report.pdf", b"%PDF-1.4", "application/pdf")
            assert ok.status_code == 200, ok.text
        finally:
            app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS = []

    def test_oversize_rejected(self):
        from open_webui.main import app

        app.state.config.ATTACHMENT_MAX_SIZE = 1  # 1 MB
        try:
            r = self._upload("big.pdf", b"x" * (2 * 1024 * 1024), "application/pdf")
            assert r.status_code == 400, r.text
            assert "上限" in r.text

            # 上限内は通る
            ok = self._upload("small.pdf", b"x" * 1024, "application/pdf")
            assert ok.status_code == 200, ok.text
        finally:
            app.state.config.ATTACHMENT_MAX_SIZE = 25

    def test_default_allows_all_extensions(self):
        # 既定(ATTACHMENT_ALLOWED_EXTENSIONS 空)は全許可
        from open_webui.main import app

        assert app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS == []
        ok = self._upload("data.csv", b"a,b,c", "text/csv")
        assert ok.status_code == 200, ok.text
