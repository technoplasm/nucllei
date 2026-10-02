"""配備ごとの音声入力の調整値(env 専用)の回帰(_memos/audio.md「配備ごとの調整」)。

AUDIO_STT_SEGMENT_SECONDS / AUDIO_STT_SEGMENT_PAUSE_MS / AUDIO_STT_SILENCE_THRESHOLD_DB /
AUDIO_STT_RECORDING_BITRATE_KBPS は `/api/config` の audio.stt で録音バーへ渡り、
WHISPER_BEAM_SIZE は内蔵 faster-whisper の transcribe まで届く。範囲外・非数は既定に落ちる。
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nucllei.config import env_number
from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

WAV = b"RIFF0000WAVEfmt "
NAME = "AUDIO_STT_TEST_KNOB"


@pytest.fixture
def warnings(monkeypatch):
    """env_number の warning を拾う(loguru 経由でも標準 logging でも同じ口)。"""
    import nucllei.config as config

    messages = []
    monkeypatch.setattr(
        config, "log", SimpleNamespace(warning=lambda m: messages.append(m))
    )
    return messages


def test_env_number_default_when_unset(monkeypatch, warnings):
    monkeypatch.delenv(NAME, raising=False)
    assert env_number(NAME, 12.0, 4, 30) == 12.0
    monkeypatch.setenv(NAME, "  ")
    assert env_number(NAME, 12.0, 4, 30) == 12.0
    assert warnings == []


def test_env_number_reads_value_in_default_type(monkeypatch, warnings):
    monkeypatch.setenv(NAME, "6")
    value = env_number(NAME, 12.0, 4, 30)
    assert value == 6.0 and isinstance(value, float)

    monkeypatch.setenv(NAME, "-55.5")
    assert env_number(NAME, -45.0, -90, -10) == -55.5

    monkeypatch.setenv(NAME, "300")
    value = env_number(NAME, 400, 100, 2000)
    assert value == 300 and isinstance(value, int)

    # 端の値は範囲内
    monkeypatch.setenv(NAME, "4")
    assert env_number(NAME, 12.0, 4, 30) == 4.0
    monkeypatch.setenv(NAME, "30")
    assert env_number(NAME, 12.0, 4, 30) == 30.0
    assert warnings == []


@pytest.mark.parametrize(
    "raw,default,minimum,maximum",
    [
        ("3.9", 12.0, 4, 30),  # 範囲の下
        ("31", 12.0, 4, 30),  # 範囲の上
        ("abc", 12.0, 4, 30),  # 非数
        ("nan", 12.0, 4, 30),  # NaN は比較が常に偽
        ("inf", -45.0, -90, -10),
        ("0", 5, 1, 10),
        ("2.5", 5, 1, 10),  # int の設定に小数
        ("-5", -45.0, -90, -10),  # しきい値の上限 -10 を超える
    ],
)
def test_env_number_falls_back_with_one_warning(
    monkeypatch, warnings, raw, default, minimum, maximum
):
    monkeypatch.setenv(NAME, raw)
    assert env_number(NAME, default, minimum, maximum) == default
    assert len(warnings) == 1
    assert NAME in warnings[0]


def test_defaults_match_documented_values():
    """既定(env 未設定)は録音バーの定数・_memos/audio.md の表と同じ値。"""
    import nucllei.config as config
    from nucllei.main import app

    assert config.AUDIO_STT_SEGMENT_SECONDS == 12
    assert config.AUDIO_STT_SEGMENT_PAUSE_MS == 400
    assert config.AUDIO_STT_SILENCE_THRESHOLD_DB == -45
    assert config.AUDIO_STT_RECORDING_BITRATE_KBPS == 24
    assert config.WHISPER_BEAM_SIZE == 5
    assert app.state.WHISPER_BEAM_SIZE == 5
    assert app.state.AUDIO_STT_INPUT == {
        "segment_seconds": 12,
        "segment_pause_ms": 400,
        "silence_threshold_db": -45,
        "recording_bitrate_kbps": 24,
    }


class FakeWhisperModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, file_path, **kwargs):
        self.calls.append(kwargs)
        info = SimpleNamespace(language="ja", language_probability=1.0)
        return iter([SimpleNamespace(text=" こんにちは")]), info


class TestAudioSttEnvKnobs(AbstractPostgresTest):
    BASE_PATH = "/api/v1/audio"

    def setup_method(self):
        super().setup_method()
        from nucllei.main import app

        self.app = app
        self.saved_config = {
            "ENABLE_AUDIO_STT": app.state.config.ENABLE_AUDIO_STT,
            "STT_ENGINE": app.state.config.STT_ENGINE,
        }
        self.saved_state = {
            "faster_whisper_model": app.state.faster_whisper_model,
            "WHISPER_BEAM_SIZE": app.state.WHISPER_BEAM_SIZE,
            "AUDIO_STT_INPUT": app.state.AUDIO_STT_INPUT,
        }
        self.fast_api_client.cookies.clear()

    def teardown_method(self):
        for key, value in self.saved_config.items():
            setattr(self.app.state.config, key, value)
        for key, value in self.saved_state.items():
            setattr(self.app.state, key, value)
        self.fast_api_client.cookies.clear()
        super().teardown_method()

    def _login(self):
        from nucllei.models.auths import Auths
        from nucllei.utils.auth import create_token, get_password_hash

        user = Auths.insert_new_auth(
            email="doctor@hospital.example",
            password=get_password_hash("password"),
            name="医師",
            profile_image_url="/user.png",
            role="user",
        )
        token = create_token(data={"id": user.id}, expires_delta=timedelta(hours=1))
        self.fast_api_client.cookies.set("token", token)

    def test_config_exposes_knobs_to_signed_in_user(self):
        self._login()
        stt = self.fast_api_client.get("/api/config").json()["audio"]["stt"]
        assert stt["segment_seconds"] == 12
        assert stt["segment_pause_ms"] == 400
        assert stt["silence_threshold_db"] == -45
        assert stt["recording_bitrate_kbps"] == 24
        # 既存のキーはそのまま
        assert "enable" in stt and "engine" in stt

        self.app.state.AUDIO_STT_INPUT = {
            "segment_seconds": 6.0,
            "segment_pause_ms": 300,
            "silence_threshold_db": -55.0,
            "recording_bitrate_kbps": 32,
        }
        stt = self.fast_api_client.get("/api/config").json()["audio"]["stt"]
        assert stt["segment_seconds"] == 6.0
        assert stt["segment_pause_ms"] == 300
        assert stt["silence_threshold_db"] == -55.0
        assert stt["recording_bitrate_kbps"] == 32

    def test_config_hides_audio_from_anonymous(self):
        assert "audio" not in self.fast_api_client.get("/api/config").json()

    def _transcribe(self):
        self.app.state.config.ENABLE_AUDIO_STT = True
        self.app.state.config.STT_ENGINE = ""  # 空 = 内蔵 faster-whisper
        model = FakeWhisperModel()
        self.app.state.faster_whisper_model = model
        with (
            mock_webui_user(id="1"),
            patch("nucllei.routers.audio.compress_audio", side_effect=lambda p: p),
        ):
            response = self.fast_api_client.post(
                self.create_url("/transcriptions"),
                files={"file": ("recording.wav", WAV, "audio/wav")},
            )
        assert response.status_code == 200, response.text
        assert len(model.calls) == 1
        return model.calls[0]

    def test_beam_size_default_reaches_whisper(self):
        assert self._transcribe()["beam_size"] == 5

    def test_beam_size_setting_reaches_whisper(self):
        self.app.state.WHISPER_BEAM_SIZE = 2
        assert self._transcribe()["beam_size"] == 2
