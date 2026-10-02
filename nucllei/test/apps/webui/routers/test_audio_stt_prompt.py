"""/transcriptions の任意フィールド prompt の回帰(_memos/audio.md)。

prompt は用語リスト等を転写の文脈として渡したい呼び出し元のための API(マイクの音声入力は
送らない)。内蔵 faster-whisper では initial_prompt、OpenAI 互換の外部 STT では同名フィールドの
prompt として届くこと、省略時は従来と同じ呼び出しになることを固定する。
モデル・外部 API はモックする(実機スモークは test_audio_stt_builtin.py)。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nucllei.routers.audio import STT_PROMPT_MAX_CHARS, normalize_stt_prompt
from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

WAV = b"RIFF0000WAVEfmt "


class FakeWhisperModel:
    """faster-whisper の WhisperModel.transcribe の外形だけを持つモック。"""

    def __init__(self):
        self.calls = []

    def transcribe(self, file_path, **kwargs):
        self.calls.append(kwargs)
        info = SimpleNamespace(language="ja", language_probability=1.0)
        return iter([SimpleNamespace(text=" こんにちは")]), info


class TestTranscriptionPrompt(AbstractPostgresTest):
    BASE_PATH = "/api/v1/audio"

    def setup_method(self):
        super().setup_method()
        from nucllei.main import app

        self.app = app
        self.saved = {
            "ENABLE_AUDIO_STT": app.state.config.ENABLE_AUDIO_STT,
            "STT_ENGINE": app.state.config.STT_ENGINE,
            "STT_OPENAI_API_BASE_URL": app.state.config.STT_OPENAI_API_BASE_URL,
        }
        self.saved_model = app.state.faster_whisper_model

        app.state.config.ENABLE_AUDIO_STT = True
        app.state.config.STT_ENGINE = ""  # 空 = 内蔵 faster-whisper
        self.model = FakeWhisperModel()
        app.state.faster_whisper_model = self.model

    def teardown_method(self):
        for key, value in self.saved.items():
            setattr(self.app.state.config, key, value)
        self.app.state.faster_whisper_model = self.saved_model
        super().teardown_method()

    def post(self, data=None):
        with (
            mock_webui_user(id="1"),
            patch("nucllei.routers.audio.compress_audio", side_effect=lambda p: p),
        ):
            return self.fast_api_client.post(
                self.create_url("/transcriptions"),
                files={"file": ("recording.wav", WAV, "audio/wav")},
                data=data,
            )

    def test_prompt_reaches_initial_prompt(self):
        response = self.post({"prompt": "用語: 咳嗽、レボフロキサシン。"})
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "こんにちは"
        assert len(self.model.calls) == 1
        assert self.model.calls[0]["initial_prompt"] == "用語: 咳嗽、レボフロキサシン。"

    def test_prompt_omitted_passes_none(self):
        """省略時は initial_prompt=None(faster-whisper の既定値 = 渡さないのと同じ)。"""
        response = self.post()
        assert response.status_code == 200, response.text
        assert self.model.calls[0]["initial_prompt"] is None

    def test_blank_prompt_is_none(self):
        response = self.post({"prompt": "  \n "})
        assert response.status_code == 200, response.text
        assert self.model.calls[0]["initial_prompt"] is None

    def test_long_prompt_is_truncated_to_tail(self):
        prompt = "あ" * 500 + "末尾の文。"
        response = self.post({"prompt": prompt})
        assert response.status_code == 200, response.text
        sent = self.model.calls[0]["initial_prompt"]
        assert len(sent) == STT_PROMPT_MAX_CHARS
        assert sent == prompt[-STT_PROMPT_MAX_CHARS:]
        assert sent.endswith("末尾の文。")

    def test_openai_engine_forwards_prompt_field(self):
        """外部 OpenAI 互換 STT へは同名の prompt フィールドで転送し、無ければ載せない。"""
        self.app.state.config.STT_ENGINE = "openai"
        self.app.state.config.STT_OPENAI_API_BASE_URL = "http://stt.invalid/v1"

        upstream = MagicMock()
        upstream.json.return_value = {"text": "ok"}

        with (
            patch("nucllei.routers.audio.get_audio_format", return_value=None),
            patch("nucllei.routers.audio.requests.post", return_value=upstream) as post,
        ):
            assert self.post({"prompt": "用語リスト。"}).status_code == 200
            assert post.call_args.kwargs["data"]["prompt"] == "用語リスト。"

            assert self.post().status_code == 200
            assert "prompt" not in post.call_args.kwargs["data"]


def test_normalize_stt_prompt():
    assert normalize_stt_prompt(None) is None
    assert normalize_stt_prompt("") is None
    assert normalize_stt_prompt("   ") is None
    assert normalize_stt_prompt(" 短い文。 ") == "短い文。"
    # 丸めた先頭が空白になっても、その空白は残さない
    text = "x" * 300 + " " + "y" * (STT_PROMPT_MAX_CHARS - 1)
    assert normalize_stt_prompt(text) == "y" * (STT_PROMPT_MAX_CHARS - 1)
