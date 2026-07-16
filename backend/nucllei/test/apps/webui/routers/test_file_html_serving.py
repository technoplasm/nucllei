"""HTML ファイル配信の XSS 隔離(issue #36、GHSA-8gh5-qqh8-hq3x / CVE-2025-46571)。

アップロードされた HTML は `/api/v1/files/{id}/content/html` で iframe に読み込まれる。
フロントの iframe sandbox に加え、配信レスポンス自体に CSP sandbox を付けて opaque origin で
隔離し、親オリジンの localStorage トークン/Cookie への到達(Stored XSS→乗っ取り)を封じる。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestHtmlFileServingCSP(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def test_content_html_is_sandboxed(self):
        evil = (
            b"<html><body><script>"
            b"window.parent.postMessage(localStorage.token,'*')"
            b"</script>hi</body></html>"
        )
        with mock_webui_user(id="1", role="admin"):
            up = self.fast_api_client.post(
                self.create_url("", query_params={"process": "false"}),
                files={"file": ("evil.html", evil, "text/html")},
            )
        assert up.status_code == 200, up.text
        file_id = up.json()["id"]

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url(f"/{file_id}/content/html"))

        assert r.status_code == 200
        # CSP sandbox が opaque origin へ隔離する(allow-same-origin は付けない)。
        csp = r.headers.get("content-security-policy", "")
        assert "sandbox" in csp
        assert "allow-same-origin" not in csp
        # MIME スニッフィングによる text/html 昇格も防ぐ。
        assert r.headers.get("x-content-type-options") == "nosniff"
