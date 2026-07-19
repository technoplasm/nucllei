"""#84: 配備先カスタマイズ(CUSTOM_DIR 規定名オーバーレイ + ENABLE_PWA ゲート)。

- /static/{規定名} は CUSTOM_DIR のファイルがあればそれを、無ければ同梱既定を返す
  (リクエスト都度 — 差し替えに再起動不要)
- /manifest.json は ENABLE_PWA=false(既定)で {} を返し、true で WEBUI_NAME 追従の
  manifest を返す
"""

from unittest.mock import patch

from test.util.abstract_integration_test import AbstractPostgresTest


class TestCustomStatic(AbstractPostgresTest):
    BASE_PATH = "/"

    def test_customizable_static_falls_back_to_bundled(self, tmp_path):
        # CUSTOM_DIR が空 → 同梱既定(STATIC_DIR 配下)が返る
        with patch("nucllei.main.CUSTOM_DIR", tmp_path):
            response = self.fast_api_client.get("/static/favicon.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert len(response.content) > 0

    def test_customizable_static_prefers_custom_file(self, tmp_path):
        custom_bytes = b"custom-favicon-bytes"
        (tmp_path / "favicon.png").write_bytes(custom_bytes)
        with patch("nucllei.main.CUSTOM_DIR", tmp_path):
            response = self.fast_api_client.get("/static/favicon.png")
        assert response.status_code == 200
        assert response.content == custom_bytes

        # 消せば次のリクエストから既定に戻る(都度読み・再起動不要)
        (tmp_path / "favicon.png").unlink()
        with patch("nucllei.main.CUSTOM_DIR", tmp_path):
            response = self.fast_api_client.get("/static/favicon.png")
        assert response.status_code == 200
        assert response.content != custom_bytes

    def test_all_designated_names_are_served(self, tmp_path):
        from nucllei.main import CUSTOMIZABLE_STATIC

        for name in CUSTOMIZABLE_STATIC:
            (tmp_path / name).write_bytes(b"x-" + name.encode())
        with patch("nucllei.main.CUSTOM_DIR", tmp_path):
            for name in CUSTOMIZABLE_STATIC:
                response = self.fast_api_client.get(f"/static/{name}")
                assert response.status_code == 200, name
                assert response.content == b"x-" + name.encode(), name

    def test_manifest_disabled_by_default(self):
        # ENABLE_PWA 既定 false → {} (有効な JSON だがインストール性なし)
        response = self.fast_api_client.get("/manifest.json")
        assert response.status_code == 200
        assert response.json() == {}

    def test_manifest_enabled_follows_webui_name(self):
        with patch("nucllei.main.ENABLE_PWA", True):
            response = self.fast_api_client.get("/manifest.json")
        assert response.status_code == 200
        data = response.json()
        app_state = self.fast_api_client.app.state
        assert data["name"] == app_state.WEBUI_NAME
        assert data["short_name"] == app_state.WEBUI_NAME
        icon_srcs = [icon["src"] for icon in data["icons"]]
        assert "/static/favicon-192.png" in icon_srcs
        assert "/static/favicon-512.png" in icon_srcs
