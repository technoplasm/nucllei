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


class TestAudioInternalFunctions(AbstractPostgresTest):
    """STT/TTS 内部関数(utils/audio.py、issue #53)。

    hook/filter/tool から呼ぶ sync 関数として、config 既定 + 引数上書き・
    キャッシュキー(voice を含む)・file_id の data.content キャッシュを固定する。
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

        app.state.config.TTS_ENGINE = "openai"
        app.state.config.TTS_OPENAI_API_BASE_URL = cls.base_url
        app.state.config.TTS_OPENAI_API_KEY = "sk-dummy"
        app.state.config.TTS_MODEL = "voicevox-tts"
        app.state.config.TTS_VOICE = "metan"
        app.state.config.STT_ENGINE = "openai"
        app.state.config.STT_OPENAI_API_BASE_URL = cls.base_url
        app.state.config.STT_OPENAI_API_KEY = "sk-dummy"
        app.state.config.STT_MODEL = "whisper-1"

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def setup_method(self):
        super().setup_method()
        self.mock_server.received.clear()

    def _request(self):
        """内部関数は request.app.state.config しか触らないので app を包むだけでよい。"""
        from types import SimpleNamespace

        from open_webui.main import app

        return SimpleNamespace(app=app)

    # ---- TTS ----

    def test_synthesize_speech_uses_config_defaults(self):
        """voice/model 未指定は config(AUDIO_TTS_MODEL/VOICE)が使われる。"""
        from open_webui.utils.audio import synthesize_speech

        text = f"既定値 {uuid.uuid4()}"
        path = synthesize_speech(self._request(), text)
        assert path.is_file()
        assert path.read_bytes() == MOCK_AUDIO

        assert len(self.mock_server.received) == 1
        sent = self.mock_server.received[0]
        assert sent == {"input": text, "model": "voicevox-tts", "voice": "metan"}

    def test_synthesize_speech_voice_in_cache_key(self):
        """同一テキストでも voice 違いは別キャッシュ、同一 voice の再呼び出しはブリッジ不達。"""
        from open_webui.utils.audio import synthesize_speech

        text = f"声切替 {uuid.uuid4()}"
        path_metan = synthesize_speech(self._request(), text, voice="metan")
        path_zunda = synthesize_speech(self._request(), text, voice="zundamon")
        assert path_metan != path_zunda
        assert [r["voice"] for r in self.mock_server.received] == [
            "metan",
            "zundamon",
        ]

        # キャッシュヒット(voice 単位)
        assert synthesize_speech(self._request(), text, voice="metan") == path_metan
        assert len(self.mock_server.received) == 2

    def test_synthesize_speech_model_override(self):
        """model 引数は config 既定を上書きする。"""
        from open_webui.utils.audio import synthesize_speech

        text = f"モデル切替 {uuid.uuid4()}"
        synthesize_speech(self._request(), text, voice="en-voice", model="en-tts")
        sent = self.mock_server.received[0]
        assert sent == {"input": text, "model": "en-tts", "voice": "en-voice"}

    def test_synthesize_speech_rejects_non_openai_engine(self):
        """openai 互換以外のエンジンでは ValueError(他エンジンは HTTP 経路のみ)。"""
        import pytest

        from open_webui.main import app
        from open_webui.utils.audio import synthesize_speech

        app.state.config.TTS_ENGINE = ""
        try:
            with pytest.raises(ValueError):
                synthesize_speech(self._request(), "エンジン未設定")
        finally:
            app.state.config.TTS_ENGINE = "openai"
        assert len(self.mock_server.received) == 0

    # ---- STT ----

    def _make_wav(self, dirname):
        import os

        path = os.path.join(dirname, f"{uuid.uuid4()}.wav")
        with open(path, "wb") as f:
            f.write(make_silent_wav())
        return path

    def test_transcribe_audio_from_path(self, tmp_path):
        """path 入力は都度実行・元ファイルは変更されない(作業コピーで保護)。"""
        from open_webui.utils.audio import transcribe_audio

        wav_path = self._make_wav(str(tmp_path))
        original = open(wav_path, "rb").read()

        text = transcribe_audio(self._request(), wav_path)
        assert text == "こんにちは、これはテストです。"
        assert open(wav_path, "rb").read() == original

        # キャッシュなし = 2 回目もブリッジへ行く
        transcribe_audio(self._request(), wav_path)
        assert len(self.mock_server.received) == 2

    def test_transcribe_audio_file_id_caches_to_data_content(self, tmp_path):
        """file_id 入力は初回のみ文字起こしし、data.content に冪等キャッシュする。"""
        from open_webui.models.files import FileForm, Files
        from open_webui.utils.audio import transcribe_audio

        wav_path = self._make_wav(str(tmp_path))
        fid = str(uuid.uuid4())
        Files.insert_new_file(
            "1",
            FileForm(id=fid, filename="voice.wav", path=wav_path),
        )

        text = transcribe_audio(self._request(), file_id=fid)
        assert text == "こんにちは、これはテストです。"
        file = Files.get_file_by_id(fid)
        assert file.data["content"] == text
        assert file.data["status"] == "success"

        # 2 回目はキャッシュから(ブリッジ不達)
        assert transcribe_audio(self._request(), file_id=fid) == text
        assert len(self.mock_server.received) == 1

    def test_transcribe_audio_requires_exactly_one_input(self):
        import pytest

        from open_webui.utils.audio import transcribe_audio

        with pytest.raises(ValueError):
            transcribe_audio(self._request())
        with pytest.raises(ValueError):
            transcribe_audio(self._request(), "/tmp/a.wav", file_id="x")


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
