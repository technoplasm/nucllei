"""/transcriptions の幻聴対策の回帰(_memos/audio.md「幻聴対策」)。

内蔵 faster-whisper には幻聴対策のパラメータが渡り、区間の後処理がかかること、
外部 OpenAI 互換 STT のテキストにも後処理がかかることを固定する。
後処理そのものの規則は test/apps/webui/utils/test_stt_filter.py。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import nucllei.routers.audio as audio_router
from nucllei.utils.stt_filter import SttFilterOptions
from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

WAV = b"RIFF0000WAVEfmt "


def seg(text, start, end):
    return SimpleNamespace(
        text=text, start=start, end=end, no_speech_prob=0.0, avg_logprob=-0.2
    )


class FakeWhisperModel:
    def __init__(self, segments, duration=12.0):
        self.calls = []
        self.segments = segments
        self.duration = duration

    def transcribe(self, file_path, **kwargs):
        self.calls.append(kwargs)
        info = SimpleNamespace(
            language="ja", language_probability=1.0, duration=self.duration
        )
        return iter(self.segments), info


class TestTranscriptionHallucination(AbstractPostgresTest):
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

    def teardown_method(self):
        for key, value in self.saved.items():
            setattr(self.app.state.config, key, value)
        self.app.state.faster_whisper_model = self.saved_model
        super().teardown_method()

    def post(self):
        with (
            mock_webui_user(id="1"),
            patch("nucllei.routers.audio.compress_audio", side_effect=lambda p: p),
        ):
            return self.fast_api_client.post(
                self.create_url("/transcriptions"),
                files={"file": ("recording.wav", WAV, "audio/wav")},
            )

    def builtin(self, segments, duration=12.0):
        model = FakeWhisperModel(segments, duration)
        self.app.state.faster_whisper_model = model
        response = self.post()
        assert response.status_code == 200, response.text
        return response.json()["text"], model.calls[0]

    def test_builtin_receives_mitigation_kwargs(self):
        _, kwargs = self.builtin([seg(" こんにちは", 0.4, 1.2)])
        assert kwargs["condition_on_previous_text"] is False
        assert kwargs["word_timestamps"] is True
        assert kwargs["hallucination_silence_threshold"] == 2.0
        assert kwargs["no_speech_threshold"] == 0.6
        assert kwargs["log_prob_threshold"] == -1.0
        assert kwargs["compression_ratio_threshold"] == 2.4
        assert kwargs["vad_parameters"] == {"threshold": 0.5, "speech_pad_ms": 400}
        # 既存のつまみはそのまま届く
        assert kwargs["beam_size"] == 5
        assert "vad_filter" in kwargs and "language" in kwargs

    def test_builtin_silence_threshold_zero_disables_word_timestamps(self, monkeypatch):
        monkeypatch.setattr(audio_router, "WHISPER_HALLUCINATION_SILENCE_THRESHOLD", 0)
        _, kwargs = self.builtin([seg(" こんにちは", 0.4, 1.2)])
        assert "word_timestamps" not in kwargs
        assert "hallucination_silence_threshold" not in kwargs

    def test_builtin_drops_phrase_from_silence(self):
        text, _ = self.builtin([seg("ご視聴ありがとうございました", 0.6, 30.6)])
        assert text == ""
        text, _ = self.builtin(
            [
                seg("血圧は120の80です。", 0.4, 3.1),
                seg("ご視聴ありがとうございました", 3.5, 30.6),
            ]
        )
        assert text == "血圧は120の80です。"

    def test_builtin_keeps_normal_speech(self):
        segments = [
            seg("68歳男性。", 0.3, 2.1),
            seg("3日前からの発熱と咳嗽を主訴に来院した。", 2.1, 5.9),
        ]
        text, _ = self.builtin(segments, duration=6.5)
        assert text == "68歳男性。3日前からの発熱と咳嗽を主訴に来院した。"

    def test_builtin_filter_disabled(self, monkeypatch):
        monkeypatch.setattr(
            audio_router, "STT_FILTER_OPTIONS", SttFilterOptions(enabled=False)
        )
        text, _ = self.builtin([seg("ご視聴ありがとうございました", 0.6, 30.6)])
        assert text == "ご視聴ありがとうございました"

    def test_openai_engine_text_is_filtered(self):
        self.app.state.config.STT_ENGINE = "openai"
        self.app.state.config.STT_OPENAI_API_BASE_URL = "http://stt.invalid/v1"

        def upstream(text):
            response = MagicMock()
            response.json.return_value = {"text": text}
            return response

        with patch("nucllei.routers.audio.get_audio_format", return_value=None):
            with patch(
                "nucllei.routers.audio.requests.post",
                return_value=upstream("Thank you for watching!"),
            ):
                assert self.post().json()["text"] == ""
            # 文の一部なら残す
            text = "ご視聴ありがとうございましたと言って終わる。"
            with patch(
                "nucllei.routers.audio.requests.post", return_value=upstream(text)
            ):
                assert self.post().json()["text"] == text
            with patch(
                "nucllei.routers.audio.requests.post",
                return_value=upstream("CRPは8.6です。" + "8.6、" * 20),
            ):
                assert self.post().json()["text"] == "CRPは8.6です。8.6、"
