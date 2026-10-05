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
            self.server.models_gets += 1
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
        cls.mock_server.models_gets = 0
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
        self.mock_server.models_gets = 0

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

    def test_consecutive_completions_do_not_refetch_models(self):
        """2026-10-04: completion の hot path は upstream の /models を取り直さない。

        初回(キャッシュ空)だけ 1 回取り、2 回目以降(tool ループの追撃・subagent の
        子ターンと同じ形)は OPENAI_MODELS から urlIdx を引く。新しいモデル id
        (キャッシュに無い)だけが取り直しを起こす。
        """
        from nucllei.main import app

        def complete(model="mock-model"):
            with mock_webui_user(id="1", role="admin"):
                return self.fast_api_client.post(
                    "/api/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                )

        assert complete().status_code == 200
        assert self.mock_server.models_gets == 1
        assert complete().status_code == 200
        assert complete().status_code == 200
        assert self.mock_server.models_gets == 1
        assert len(self.mock_server.received) == 3

        # 接続設定の更新は OPENAI_MODELS を捨てる(urlIdx の陳腐化防止)→ 次の
        # completion で 1 回だけ取り直す
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/openai/config/update",
                json={
                    "ENABLE_OPENAI_API": True,
                    "OPENAI_API_BASE_URLS": [self.base_url],
                    "OPENAI_API_KEYS": ["sk-dummy"],
                    "OPENAI_API_CONFIGS": {},
                },
            )
        assert response.status_code == 200, response.text
        assert app.state.OPENAI_MODELS == {}
        assert complete().status_code == 200
        assert self.mock_server.models_gets == 2

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
        self, model_id="my-preset", access_control=None, filter_ids=None, params=None
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
            "params": params or {},
            "access_control": access_control,
        }
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post("/api/v1/models/create", json=payload)
        assert response.status_code == 200, response.text

    def test_preset_system_prompt_expands_base_model_id(self):
        """preset のシステムプロンプトの {{BASE_MODEL_ID}} が展開されて届く。

        値は実際に推論する base の id。利用者の本文に書かれた変数は展開しない。
        """
        self._create_preset(
            model_id="named-preset",
            params={"system": "基盤は {{BASE_MODEL_ID}}"},
        )

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "named-preset",
                    "messages": [{"role": "user", "content": "{{BASE_MODEL_ID}} とは"}],
                    "stream": False,
                },
            )
        assert response.status_code == 200, response.text

        sent = self.mock_server.received[0]
        assert sent["model"] == "mock-model"
        assert sent["messages"][0] == {"role": "system", "content": "基盤は mock-model"}
        assert sent["messages"][-1] == {
            "role": "user",
            "content": "{{BASE_MODEL_ID}} とは",
        }

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

    def test_base_model_id_required(self):
        """llens(#82): Models 行は base_model_id 必須(= preset のみ)。

        生モデル(接続由来)の id に base 無し行を作る旧経路は、生モデルを普通の
        モデルとして露出させるため 400 で拒否する。update で base を null に
        剥がすのも同様に拒否。
        """
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
        assert response.status_code == 400, response.text

        # 生モデルは露出しないまま
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.get("/api/models")
        assert response.status_code == 200
        assert "mock-model" not in [m["id"] for m in response.json()["data"]]

        # 正規の preset は作れる → base を null に剥がす update は拒否
        self._create_preset(model_id="guard-preset")
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/v1/models/model/update?id=guard-preset",
                json={**payload, "id": "guard-preset", "name": "Guard"},
            )
        assert response.status_code == 400, response.text

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

    def test_stream_hook_is_not_run(self):
        """Filter の stream フックは撤去済み(2026-10-05 — llens の filter は inlet だけ)。
        stream を持つ filter を割り当てても SSE は素通し。"""
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
        assert "chunk-1" in response.text
        assert "chunk-2" in response.text
        assert "CHUNK" not in response.text

    def test_chat_completed_endpoint_is_gone(self):
        """`/api/chat/completed`(outlet を走らせる完了後 POST)は撤去済み(2026-10-05)。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completed",
                json={
                    "model": "filtered-preset",
                    "chat_id": "chat-1",
                    "id": "message-1",
                    "messages": [],
                },
            )
        assert response.status_code in (404, 405), response.text
