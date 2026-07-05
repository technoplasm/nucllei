"""TTS(OpenAI 互換ブリッジ経由)のスモークテスト(issue #12)。

voicevox-openai-tts ブリッジ相当のモック({base}/audio/speech, /audio/voices,
/audio/models)に対して、日本語 TTS 連携(fork.md §4.1)がデグレしていないことを
固定する。
"""

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

MOCK_AUDIO = b"ID3mock-mp3-bytes"


class MockTTSHandler(BaseHTTPRequestHandler):
    """voicevox-openai-tts ブリッジ相当の最小 OpenAI 互換 TTS サーバ。"""

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
        path = self.path.rstrip("/")
        if path == "/v1/audio/voices":
            self._json(
                {
                    "voices": [
                        {"id": "metan", "name": "四国めたん"},
                        {"id": "zundamon", "name": "ずんだもん"},
                    ]
                }
            )
        elif path == "/v1/audio/models":
            self._json({"models": [{"id": "voicevox-tts", "name": "VOICEVOX"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        path = self.path.rstrip("/")
        if path == "/v1/audio/speech":
            payload = json.loads(raw or b"{}")
            self.server.received.append(payload)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(MOCK_AUDIO)))
            self.end_headers()
            self.wfile.write(MOCK_AUDIO)
        elif path == "/v1/audio/transcriptions":
            # multipart はパースせず受信サイズだけ記録する
            self.server.received.append({"transcription_bytes": len(raw)})
            self._json({"text": "こんにちは、これはテストです。"})
        else:
            self._json({"error": "not found"}, 404)


class TestOpenAICompatTTS(AbstractPostgresTest):
    BASE_PATH = "/api/v1/audio"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockTTSHandler)
        cls.mock_server.received = []
        cls.server_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.mock_server.server_address[1]}/v1"

        from open_webui.main import app

        app.state.config.TTS_ENGINE = "openai"
        app.state.config.TTS_OPENAI_API_BASE_URL = cls.base_url
        app.state.config.TTS_OPENAI_API_KEY = "sk-dummy"
        app.state.config.TTS_MODEL = "voicevox-tts"
        app.state.config.TTS_VOICE = "metan"

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def setup_method(self):
        super().setup_method()
        self.mock_server.received.clear()

    def test_speech_generation(self):
        """/audio/speech がブリッジへ中継され音声バイナリが返る。"""
        # キャッシュ(SPEECH_CACHE_DIR は body+engine+model のハッシュ)を
        # 確実に外すため毎回ユニークな入力にする
        text = f"こんにちは {uuid.uuid4()}"
        with mock_webui_user(id="1"):
            response = self.fast_api_client.post(
                self.create_url("/speech"),
                json={"input": text, "voice": "metan"},
            )
        assert response.status_code == 200, response.text
        assert response.content == MOCK_AUDIO

        # ブリッジに TTS_MODEL が付与されたリクエストが届いている
        assert len(self.mock_server.received) == 1
        sent = self.mock_server.received[0]
        assert sent["model"] == "voicevox-tts"
        assert sent["input"] == text
        assert sent["voice"] == "metan"

    def test_speech_cache_hit_skips_bridge(self):
        """同一入力の 2 回目はキャッシュから返りブリッジへ行かない。"""
        text = f"キャッシュ確認 {uuid.uuid4()}"
        for _ in range(2):
            with mock_webui_user(id="1"):
                response = self.fast_api_client.post(
                    self.create_url("/speech"),
                    json={"input": text, "voice": "metan"},
                )
            assert response.status_code == 200
            assert response.content == MOCK_AUDIO
        assert len(self.mock_server.received) == 1

    def test_voices_listed_from_custom_endpoint(self):
        """カスタムエンドポイントの /audio/voices が UI の音声一覧に反映される。"""
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/voices"))
        assert response.status_code == 200
        voices = {v["id"]: v["name"] for v in response.json()["voices"]}
        assert voices["metan"] == "四国めたん"
        assert voices["zundamon"] == "ずんだもん"

    def test_models_listed_from_custom_endpoint(self):
        """カスタムエンドポイントの /audio/models がモデル一覧に反映される。"""
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/models"))
        assert response.status_code == 200
        model_ids = [m["id"] for m in response.json()["models"]]
        assert "voicevox-tts" in model_ids


def make_silent_wav(seconds=0.2, rate=8000):
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class TestOpenAICompatSTT(AbstractPostgresTest):
    """STT の OpenAI 互換経路({base}/audio/transcriptions)のスモークテスト。

    既定のローカル faster-whisper 経路は local-ml extra が必要なため、
    ここでは互換 API 経路のみを固定する(fork.md §4.2)。
    """

    BASE_PATH = "/api/v1/audio"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockTTSHandler)
        cls.mock_server.received = []
        cls.server_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.mock_server.server_address[1]}/v1"

        from open_webui.main import app

        app.state.config.STT_ENGINE = "openai"
        app.state.config.STT_OPENAI_API_BASE_URL = cls.base_url
        app.state.config.STT_OPENAI_API_KEY = "sk-dummy"
        app.state.config.STT_MODEL = "whisper-1"

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def test_transcription(self):
        """/audio/transcriptions が互換 API へ中継されテキストが返る。"""
        wav = make_silent_wav()
        with mock_webui_user(id="1"):
            response = self.fast_api_client.post(
                self.create_url("/transcriptions"),
                files={"file": ("voice.wav", wav, "audio/wav")},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["text"] == "こんにちは、これはテストです。"
        assert data["filename"]

        # 音声ファイルが multipart で互換 API に届いている
        assert len(self.mock_server.received) == 1
        assert self.mock_server.received[0]["transcription_bytes"] > len(wav)
