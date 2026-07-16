"""OpenAI 互換接続の最小構成スモークテスト(issue #6)と
チャットリクエスト経由の Filter 結合テスト(issue #3)。

- 「OpenAI 互換 base_url を設定すれば動く」状態(fork.md §3.4)を、
  ローカルのモック OpenAI サーバで固定する。
- inlet フィルタの加工がモデルに届き、outlet フィルタが応答に適用される
  一連の流れ(inlet → LLM → outlet)を実 HTTP 経由で固定する。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class MockOpenAIHandler(BaseHTTPRequestHandler):
    """最小の OpenAI 互換サーバ(/v1/models, /v1/chat/completions)。"""

    def log_message(self, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "mock-model",
                            "object": "model",
                            "created": 0,
                            "owned_by": "mock",
                        }
                    ],
                }
            )
        else:
            self._json({"error": "not found"}, 404)

    def _sse(self, payloads):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for item in payloads:
            chunk = item if isinstance(item, str) else json.dumps(item)
            self.wfile.write(f"data: {chunk}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.rstrip("/") == "/v1/chat/completions":
            self.server.received.append(payload)
            if payload.get("stream"):
                self._sse(
                    [
                        {
                            "id": "chatcmpl-mock",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": payload.get("model"),
                            "choices": [{"index": 0, "delta": {"content": text}}],
                        }
                        for text in ["chunk-1", "chunk-2"]
                    ]
                )
                return
            last_user = ""
            for message in reversed(payload.get("messages", [])):
                if message.get("role") == "user":
                    last_user = message.get("content", "")
                    break
            self._json(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": f"echo:{last_user}",
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )
        else:
            self._json({"error": "not found"}, 404)


E2E_FILTER_CONTENT = """
class Filter:
    def inlet(self, body: dict) -> dict:
        for message in body.get("messages", []):
            if message.get("role") == "user":
                message["content"] = message["content"] + " [inlet]"
        return body

    def outlet(self, body: dict) -> dict:
        body.setdefault("outlet_trace", []).append("e2e-filter")
        return body
"""


STREAM_FILTER_CONTENT = """
class Filter:
    def stream(self, event):
        # セッションメタデータ無しの SSE パススルー経路では、event は
        # 生の SSE チャンク(bytes / str)として渡ってくる
        # (middleware.py stream_wrapper。dict になるのはセッションあり経路のみ)
        if isinstance(event, (bytes, bytearray)):
            return event.replace(b"chunk", b"CHUNK")
        if isinstance(event, str):
            return event.replace("chunk", "CHUNK")
        return event
"""


class TestOpenAICompatChat(AbstractPostgresTest):
    BASE_PATH = "/api"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
        cls.mock_server.received = []
        cls.server_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.mock_server.server_address[1]}/v1"

        # 最小構成: OpenAI 互換 base_url の設定のみで接続する(fork.md §3.4)
        from nucllei.main import app

        app.state.config.ENABLE_OPENAI_API = True
        app.state.config.ENABLE_OLLAMA_API = False
        app.state.config.OPENAI_API_BASE_URLS = [cls.base_url]
        app.state.config.OPENAI_API_KEYS = ["sk-dummy"]
        app.state.config.OPENAI_API_CONFIGS = {}

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def setup_method(self):
        super().setup_method()
        from nucllei.main import app

        # モデル一覧・関数モジュールのキャッシュをテスト間で分離する
        app.state.MODELS = {}
        app.state.OPENAI_MODELS = {}
        app.state.FUNCTIONS = {}
        self.mock_server.received.clear()

    def _register_e2e_filter(self):
        from nucllei.models.functions import FunctionForm, Functions

        # llens(#46): is_active / is_global 撤去。適用はモデルの filterIds 割り当てで行う。
        Functions.insert_new_function(
            user_id="1",
            type="filter",
            form_data=FunctionForm(
                id="e2e_filter",
                name="e2e_filter",
                description="e2e filter",
                content=E2E_FILTER_CONTENT,
            ),
        )

    def test_chat_completion_with_base_url_only(self):
        """base_url 設定だけで OpenAI 互換モデルに接続してチャットできる。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "mock-model",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                },
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "echo:hi"

        # リクエストが実際にモックサーバへ届いている
        assert len(self.mock_server.received) == 1
        sent = self.mock_server.received[0]
        assert sent["model"] == "mock-model"
        assert sent["messages"][-1] == {"role": "user", "content": "hi"}

    def test_base_models_endpoint_lists_raw_models(self):
        """/api/models/base(admin ゲート)は接続由来の生モデルを返す。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.get("/api/models/base")
        assert response.status_code == 200
        model_ids = [m["id"] for m in response.json()["data"]]
        assert "mock-model" in model_ids

    def test_models_endpoint_hides_raw_models_from_admin(self):
        """GHSA-9vvh UX: workspace 行の無い生モデルは admin にもピッカーで見せない。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.get("/api/models")
        assert response.status_code == 200
        model_ids = [m["id"] for m in response.json()["data"]]
        assert "mock-model" not in model_ids

    def test_models_endpoint_hides_raw_models_from_user(self):
        """生モデルは role=user のピッカーからも除外される。"""
        with mock_webui_user(id="2", role="user"):
            response = self.fast_api_client.get("/api/models")
        assert response.status_code == 200
        model_ids = [m["id"] for m in response.json()["data"]]
        assert "mock-model" not in model_ids

    def _create_preset(
        self, model_id="my-preset", access_control=None, filter_ids=None
    ):
        meta = {
            "profile_image_url": "/static/favicon.png",
            "description": "preset with system prompt",
            "capabilities": None,
            "model_config": {},
        }
        # llens(#46): filter は preset のモデル別 filterIds 割り当てのみで適用される。
        if filter_ids is not None:
            meta["filterIds"] = filter_ids
        payload = {
            "id": model_id,
            "base_model_id": "mock-model",
            "name": "My Preset",
            "meta": meta,
            "params": {},
            "access_control": access_control,
        }
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post("/api/v1/models/create", json=payload)
        assert response.status_code == 200, response.text

    def test_models_endpoint_returns_preset_but_not_base(self):
        """preset(Models 行あり)は返るが、そのベースの生モデルは隠れたまま。"""
        self._create_preset()

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.get("/api/models")
        assert response.status_code == 200
        model_ids = [m["id"] for m in response.json()["data"]]
        assert "my-preset" in model_ids
        assert "mock-model" not in model_ids

    def test_public_preset_visible_to_user(self):
        """access_control=None(公開)の preset は role=user のピッカーに出る。"""
        self._create_preset(model_id="public-preset", access_control=None)

        with mock_webui_user(id="2", role="user"):
            response = self.fast_api_client.get("/api/models")
        assert response.status_code == 200
        model_ids = [m["id"] for m in response.json()["data"]]
        assert "public-preset" in model_ids
        assert "mock-model" not in model_ids

    def test_direct_base_model_id_row_kept(self):
        """id が接続由来と同じ Models 行(admin がベースを直接編集)は表示に残る。"""
        payload = {
            "id": "mock-model",
            "base_model_id": None,
            "name": "Edited Base",
            "meta": {
                "profile_image_url": "/static/favicon.png",
                "description": None,
                "capabilities": None,
                "model_config": {},
            },
            "params": {},
            "access_control": None,
        }
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post("/api/v1/models/create", json=payload)
        assert response.status_code == 200, response.text

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.get("/api/models")
        assert response.status_code == 200
        model_ids = [m["id"] for m in response.json()["data"]]
        assert "mock-model" in model_ids

    def test_inlet_filter_applies_before_llm(self):
        """inlet フィルタの加工がモデルへのリクエストに反映される(preset の filterIds 経由)。"""
        self._register_e2e_filter()
        self._create_preset(model_id="filtered-preset", filter_ids=["e2e_filter"])

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "filtered-preset",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                },
            )
        assert response.status_code == 200, response.text

        # inlet で加工された内容がモック LLM に届き、その echo が応答になる
        sent = self.mock_server.received[0]
        assert sent["messages"][-1]["content"] == "hi [inlet]"
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "echo:hi [inlet]"

    def test_streaming_chat_completion_passthrough(self):
        """stream=True の SSE がそのままクライアントへ返る(最小構成)。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "mock-model",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            )
        assert response.status_code == 200, response.text
        assert "text/event-stream" in response.headers["content-type"]
        assert "chunk-1" in response.text
        assert "chunk-2" in response.text
        assert "[DONE]" in response.text

    def test_stream_filter_applies_on_sse(self):
        """global stream フィルタが SSE チャンクに適用される(issue #3)。"""
        from nucllei.models.functions import FunctionForm, Functions

        # llens(#46): is_active / is_global 撤去。preset の filterIds 割り当てで適用する。
        Functions.insert_new_function(
            user_id="1",
            type="filter",
            form_data=FunctionForm(
                id="stream_filter",
                name="stream_filter",
                description="stream filter",
                content=STREAM_FILTER_CONTENT,
            ),
        )
        self._create_preset(model_id="stream-preset", filter_ids=["stream_filter"])

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "stream-preset",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            )
        assert response.status_code == 200, response.text
        # stream フィルタによる加工が SSE 出力へ反映されている
        assert "CHUNK-1" in response.text
        assert "CHUNK-2" in response.text
        assert "chunk-1" not in response.text

    def test_outlet_filter_applies_on_completed(self):
        """/api/chat/completed で outlet フィルタが応答に適用される(preset の filterIds 経由)。"""
        self._register_e2e_filter()
        self._create_preset(model_id="filtered-preset", filter_ids=["e2e_filter"])

        # モデル一覧をキャッシュさせる(chat/completed は app.state.MODELS を参照)
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.get("/api/models")
        assert response.status_code == 200

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completed",
                json={
                    "model": "filtered-preset",
                    "chat_id": "chat-1",
                    "id": "message-1",
                    "session_id": "session-1",
                    "messages": [
                        {"role": "user", "content": "hi [inlet]"},
                        {"role": "assistant", "content": "echo:hi [inlet]"},
                    ],
                },
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outlet_trace"] == ["e2e-filter"]
