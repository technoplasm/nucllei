"""内蔵 faster-whisper(ローカル STT)の実機スモーク(issue #12 残)。

prod(prism-hu/llens)の STT は OWUI 内蔵 faster-whisper + ホストマウントの
CT2 モデル(large-v3-turbo・int8・ja 固定・VAD — _memos/prod-config-snapshot.md)。
このテストは同じ経路(STT_ENGINE="" → set_faster_whisper_model → transcribe)を
軽量モデルで実機駆動する。精度ではなく「local-ml extra + モデルで経路が動く」の担保。

opt-in 実行(モデル DL を伴うため通常スイートでは skip):

    uv sync --extra local-ml
    LLENS_STT_SMOKE=1 PYTHONPATH=backend uv run pytest \\
        nucllei/test/apps/webui/routers/test_audio_stt_builtin.py -v

モデルは既定 "tiny"(初回 ~75MB を WHISPER_MODEL_DIR へ自動 DL)。
prod 同等モデルで試すときは LLENS_WHISPER_MODEL にローカル CT2 ディレクトリを指定
(例: prism の ./models/whisper/faster-whisper-large-v3-turbo-ct2)。
"""

import importlib.util
import io
import math
import os
import struct
import wave

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("LLENS_STT_SMOKE") != "1",
        reason="opt-in 実機スモーク(LLENS_STT_SMOKE=1 で実行。モデル DL を伴う)",
    ),
    pytest.mark.skipif(
        importlib.util.find_spec("faster_whisper") is None,
        reason="faster-whisper 未導入(uv sync --extra local-ml)",
    ),
]


def make_wav(seconds=1.0, rate=16000, freq=440.0):
    """16kHz mono の正弦波 WAV(発話なし — 転写結果は空でよい)。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(n)
        )
        w.writeframes(frames)
    return buf.getvalue()


class TestBuiltinWhisperSmoke(AbstractPostgresTest):
    BASE_PATH = "/api/v1/audio"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        from nucllei.main import app

        # prod 構成(prod-config-snapshot.md)をモデルサイズだけ落として踏襲
        app.state.config.ENABLE_AUDIO_STT = True
        app.state.config.STT_ENGINE = ""  # 空 = 内蔵 faster-whisper
        app.state.config.WHISPER_MODEL = os.environ.get("LLENS_WHISPER_MODEL", "tiny")
        app.state.config.WHISPER_COMPUTE_TYPE = "int8"
        app.state.config.WHISPER_LANGUAGE = "ja"
        app.state.config.WHISPER_VAD_FILTER = True
        # 既ロードのモデルを破棄し、この構成での実ロードを強制する
        app.state.faster_whisper_model = None

    def test_transcription_roundtrip(self):
        wav = make_wav()
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/transcriptions"),
                files={"file": ("smoke.wav", wav, "audio/wav")},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        # 発話なし音源なので text は空(または VAD で無音扱い)。キー存在 = 経路成立
        assert "text" in body
        assert isinstance(body["text"], str)
