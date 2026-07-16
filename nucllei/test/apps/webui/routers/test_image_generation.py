"""画像生成(OpenAI 互換エンジン)のスモークテスト(issue #12)。

triage 判断「画像生成: 機能として維持(KEEP-MINIMAL、厳密キャッチアップ不要)」の
動作保証。prod(prism-hu/llens)は画像生成を配備していないため実機構成は無く、
test_audio_tts.py と同じ「OpenAI 互換モックサーバ」方式でエンジン経路を固定する:
POST /api/v1/images/generations → 互換 API 呼び出し(b64_json)→ files 保存 → url 返却。

llens 固有の担保点:
- features.image_generation のサーバー側 enforcement(#66 G1 — UI 隠しだけでない)
- 生成画像が files テーブル経由で保存される(#69 — base64 直入れ廃止・await 欠落の修理)
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

# 1x1 透明 PNG
MOCK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)


class MockImagesHandler(BaseHTTPRequestHandler):
    """OpenAI 互換 images API の最小モック。"""

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.rstrip("/") == "/v1/images/generations":
            self.server.received.append(payload)
            body = json.dumps(
                {
                    "data": [
                        {"b64_json": base64.b64encode(MOCK_PNG).decode()}
                        for _ in range(payload.get("n", 1))
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


class TestImageGeneration(AbstractPostgresTest):
    BASE_PATH = "/api/v1/images"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockImagesHandler)
        cls.mock_server.received = []
        cls.server_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.server_thread.start()

        from nucllei.main import app

        app.state.config.ENABLE_IMAGE_GENERATION = True
        app.state.config.IMAGE_GENERATION_ENGINE = "openai"
        app.state.config.IMAGES_OPENAI_API_BASE_URL = (
            f"http://127.0.0.1:{cls.mock_server.server_address[1]}/v1"
        )
        app.state.config.IMAGES_OPENAI_API_KEY = "sk-dummy"
        app.state.config.IMAGE_GENERATION_MODEL = "mock-image-model"
        app.state.config.IMAGE_SIZE = "512x512"

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def setup_method(self):
        super().setup_method()
        self.mock_server.received.clear()

    def test_generation_saves_file_and_returns_internal_url(self):
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/generations"),
                json={"prompt": "a cat"},
            )
        assert response.status_code == 200
        images = response.json()
        assert len(images) == 1

        # 内部 file URL(#69: 画像は files テーブル経由に統一)
        url = images[0]["url"]
        assert "/api/v1/files/" in url and url.endswith("/content")

        # files 実体が保存されている
        from nucllei.models.files import Files

        file_id = url.split("/files/")[1].split("/")[0]
        file_item = Files.get_file_by_id(file_id)
        assert file_item is not None
        assert file_item.user_id == "2"

        # 互換 API へ渡った形(b64_json・既定 size・モデル名)
        sent = self.mock_server.received[0]
        assert sent["prompt"] == "a cat"
        assert sent["model"] == "mock-image-model"
        assert sent["size"] == "512x512"
        assert sent["response_format"] == "b64_json"

    def test_permission_enforced_server_side(self):
        # llens(#66 G1): features.image_generation はサーバー側 enforcement
        from nucllei.main import app

        permissions = app.state.config.USER_PERMISSIONS
        original = json.loads(json.dumps(permissions))
        try:
            permissions.setdefault("features", {})["image_generation"] = False
            app.state.config.USER_PERMISSIONS = permissions
            with mock_webui_user(id="3", role="user"):
                response = self.fast_api_client.post(
                    self.create_url("/generations"),
                    json={"prompt": "a dog"},
                )
            assert response.status_code == 401
            assert self.mock_server.received == []
        finally:
            app.state.config.USER_PERMISSIONS = original

    def test_admin_bypasses_permission_flag(self):
        from nucllei.main import app

        permissions = app.state.config.USER_PERMISSIONS
        original = json.loads(json.dumps(permissions))
        try:
            permissions.setdefault("features", {})["image_generation"] = False
            app.state.config.USER_PERMISSIONS = permissions
            with mock_webui_user(id="2", role="admin"):
                response = self.fast_api_client.post(
                    self.create_url("/generations"),
                    json={"prompt": "a bird"},
                )
            assert response.status_code == 200
        finally:
            app.state.config.USER_PERMISSIONS = original
