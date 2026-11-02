"""音声のまま聞かせる経路(utils/audio_input・utils/audio_refs)の回帰テスト。

- 参照の形(区間 `#t=…`・時刻の読み書き・context_audio のエントリ → url)
- ffmpeg(実物 — CI は ffmpeg を入れる): 長さの測り方(webm の録音は長さを持たない)、mp3 への変換と
  変換結果の置き場
- 送信直前の実体化(inline_audio_refs): 持ち主の file は mp3 の base64、音声を聞けないモデルでは注記、
  他人の file・読めない参照は同じ注記、上限を超える区間は名前付きの注記、data の webm は mp3 に
- 添付の音声(plan_attachment_audio と process_chat_payload): 能力が無ければ何もしない、通し録音
  (meta.dictation)は入れない、上限を超える長さは入れない(`<source>` がそう言う)、入れた音声は
  その発言の末尾に入り、履歴の発言の添付も毎ターン入る
"""

import asyncio
import base64
import copy
import io
import math
import os
import struct
import subprocess
import types
import wave

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest

USER_ID = "u-audio"


def _wav(seconds: float = 2.0, rate: int = 16000) -> bytes:
    """sine の wav(mono 16bit)。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(seconds * rate))
        )
        w.writeframes(frames)
    return buf.getvalue()


def _webm_without_duration(seconds: float = 3.0) -> bytes:
    """MediaRecorder の録音と同じく Duration を持たない webm(pipe に書くと ffmpeg は長さを書けない)。"""
    return subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-f",
            "webm",
            "pipe:1",
        ],
        input=_wav(seconds),
        capture_output=True,
        check=True,
    ).stdout


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    from nucllei.utils import audio_input

    monkeypatch.setattr(audio_input, "AUDIO_CACHE_DIR", tmp_path / "audio-input")
    return tmp_path / "audio-input"


# ---- 参照の形(純関数) ----------------------------------------------------------


def test_parse_and_format_time():
    from nucllei.utils.audio_refs import format_time, parse_time

    assert parse_time("23:10") == 1390
    assert parse_time("1:02:03") == 3723
    assert parse_time("90") == 90 and parse_time(90) == 90 and parse_time("2.5") == 2.5
    assert parse_time("90:00") == 5400  # 時が無ければ分は 60 を超えてよい
    for bad in ("", "abc", "-1", "1:60", "1:60:00", "1:2:3:4", True, float("nan")):
        with pytest.raises(ValueError):
            parse_time(bad)
    assert format_time(59.9) == "0:59"
    assert format_time(1390) == "23:10"
    assert format_time(3723) == "1:02:03"


def test_range_suffix_round_trip():
    from nucllei.utils.audio_refs import split_range, with_range

    assert split_range("/a.mp3#t=10,70") == ("/a.mp3", 10.0, 70.0)
    assert split_range("/a.mp3#t=,70.5") == ("/a.mp3", None, 70.5)
    assert split_range("/a.mp3#t=5") == ("/a.mp3", 5.0, None)
    assert split_range("/a.mp3") == ("/a.mp3", None, None)
    assert split_range("/a.pdf#page=3") == ("/a.pdf#page=3", None, None)
    assert with_range("/a.mp3", 600, 1200) == "/a.mp3#t=600,1200"
    assert with_range("/a.mp3", None, 90.25) == "/a.mp3#t=,90.25"
    assert with_range("/a.mp3#t=1,2", None, None) == "/a.mp3"


def test_context_audio_url_matches_context_images_rule():
    from nucllei.utils.audio_refs import context_audio_url

    assert context_audio_url("abc") == "/api/v1/files/abc/content"
    assert context_audio_url("abc#t=,90") == "/api/v1/files/abc/content#t=,90"
    for kept in (
        "/api/v1/files/abc/content#t=1,2",
        "/workspace/rec.mp3",
        "https://h.test/a.m4a#t=5",
        "/ext/doc/a.mp3",
    ):
        assert context_audio_url(kept) == kept


def test_is_audio_model_defaults_off():
    from nucllei.utils.audio_refs import is_audio_model

    assert is_audio_model({"info": {"meta": {"capabilities": {"audio": True}}}})
    assert not is_audio_model({"info": {"meta": {"capabilities": {"vision": True}}}})
    assert not is_audio_model({"info": {"meta": {}}})
    assert not is_audio_model({})
    assert not is_audio_model(None)


def test_max_minutes_env_parsing():
    from nucllei.env import parse_audio_input_max_minutes as parse

    assert parse(None) == 120 and parse("") == 120
    assert parse("30") == 30 and parse("0.5") == 0.5
    for bad in ("0", "-5", "abc", "nan", "100000"):
        assert parse(bad) == 120


# ---- ffmpeg(実物) ----------------------------------------------------------------


def test_probe_duration_reads_headerless_webm(cache_dir):
    """MediaRecorder の webm は Duration を持たない(ffprobe の format=duration は N/A)。stream copy で
    最後まで読んで測る。音声でないものは None。"""
    from nucllei.utils import audio_input

    duration = asyncio.run(audio_input.probe_duration(_webm_without_duration(3.0)))
    assert duration is not None and abs(duration - 3.0) < 0.2
    assert abs(asyncio.run(audio_input.probe_duration(_wav(2.0))) - 2.0) < 0.05
    assert asyncio.run(audio_input.probe_duration(b"not audio at all")) is None


def test_transcode_to_mp3_and_cache(cache_dir, monkeypatch):
    """mono・16 kHz の mp3 にし、区間はその長さだけ。結果は中身 + 区間で置き場から使い回す。"""
    from nucllei.utils import audio_input

    webm = _webm_without_duration(4.0)
    mp3 = asyncio.run(audio_input.transcode_to_mp3(webm))
    assert audio_input._sniff_audio(mp3) == "audio/mpeg"
    assert abs(asyncio.run(audio_input.probe_duration(mp3)) - 4.0) < 0.2
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=sample_rate,channels",
            "-of",
            "csv=p=0",
            "pipe:0",
        ],
        input=mp3,
        capture_output=True,
        check=True,
    ).stdout.decode()
    assert probe.strip() == "16000,1"

    part = asyncio.run(audio_input.transcode_to_mp3(webm, 1.0, 2.5))
    assert abs(asyncio.run(audio_input.probe_duration(part)) - 1.5) < 0.2
    assert len(list(cache_dir.glob("*.mp3"))) == 2

    # 2 回目は置き場から(ffmpeg を呼ばない)
    async def no_ffmpeg(args):
        raise AssertionError("ffmpeg must not run on a cache hit")

    monkeypatch.setattr(audio_input, "_run_ffmpeg", no_ffmpeg)
    assert asyncio.run(audio_input.transcode_to_mp3(webm, 1.0, 2.5)) == part


def test_transcode_rejects_non_audio(cache_dir):
    from nucllei.utils import audio_input

    with pytest.raises(audio_input.AudioConvertError):
        asyncio.run(audio_input.transcode_to_mp3(b"%PDF-1.7 not audio"))


def test_cache_is_pruned_oldest_first(cache_dir, monkeypatch):
    from nucllei.utils import audio_input

    monkeypatch.setattr(audio_input, "AUDIO_CACHE_MAX_BYTES", 250)
    cache_dir.mkdir(parents=True)
    for i, name in enumerate(("old", "mid", "new")):
        path = cache_dir / f"{name}.mp3"
        path.write_bytes(b"x" * 100)
        os.utime(path, (1000 + i, 1000 + i))
    audio_input._prune_cache()
    assert sorted(p.stem for p in cache_dir.glob("*.mp3")) == ["mid", "new"]


# ---- 送信直前の実体化・添付の音声(DB) ---------------------------------------------------


class TestAudioInput(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        from nucllei.utils import audio_input

        self.monkeypatch = monkeypatch
        self.tmp = tmp_path
        monkeypatch.setattr(audio_input, "AUDIO_CACHE_DIR", tmp_path / "audio-input")
        audio_input._FILE_DURATIONS.clear()
        self.user = types.SimpleNamespace(
            id=USER_ID, name="Taro", email="t@example.invalid", role="user", info=None
        )
        self.other = types.SimpleNamespace(
            id="u-other",
            name="Hanako",
            email="h@example.invalid",
            role="user",
            info=None,
        )
        from nucllei.models.users import Users

        Users.insert_new_user(id=USER_ID, name="Taro", email="t@h.example", role="user")
        Users.insert_new_user(
            id="u-other", name="Hanako", email="h@h.example", role="user"
        )

    def _file(self, fid, data, content_type, *, name=None, meta=None, owner=USER_ID):
        from nucllei.config import UPLOAD_DIR
        from nucllei.models.files import FileForm, Files

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        name = name or f"{fid}.bin"
        path = f"{UPLOAD_DIR}/{fid}_{name}"
        with open(path, "wb") as f:
            f.write(data)
        Files.insert_new_file(
            owner,
            FileForm(
                id=fid,
                filename=name,
                path=path,
                meta={"name": name, "content_type": content_type, **(meta or {})},
                data={},
            ),
        )
        return {"type": "file", "id": fid}

    @staticmethod
    def _model(audio=True):
        caps = {"audio": True} if audio else {"vision": True}
        return {
            "id": "audio-preset",
            "name": "audio",
            "owned_by": "openai",
            "info": {
                "id": "audio-preset",
                "meta": {"capabilities": caps},
                "params": {},
            },
        }

    def _inline(self, messages, audio_capable=True, user=None):
        from nucllei.utils.audio_input import inline_audio_refs

        return asyncio.run(
            inline_audio_refs(messages, user or self.user, audio_capable=audio_capable)
        )

    @staticmethod
    def _ref_message(url):
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "聞いて"},
                    {"type": "input_audio", "input_audio": {"url": url}},
                ],
            }
        ]

    # ---- inline_audio_refs ----

    def test_owner_file_becomes_mp3_base64(self):
        self._file("f-wav", _wav(2.0), "audio/wav", name="a.wav")
        out = self._inline(self._ref_message("/api/v1/files/f-wav/content"))
        part = out[0]["content"][1]
        assert part["type"] == "input_audio"
        assert set(part["input_audio"]) == {"data", "format"}
        assert part["input_audio"]["format"] == "mp3"
        mp3 = base64.b64decode(part["input_audio"]["data"])
        from nucllei.utils import audio_input

        assert audio_input._sniff_audio(mp3) == "audio/mpeg"
        assert out[0]["content"][0] == {"type": "text", "text": "聞いて"}

    def test_range_is_cut(self):
        from nucllei.utils import audio_input

        self._file("f-wav", _wav(4.0), "audio/wav", name="a.wav")
        out = self._inline(self._ref_message("/api/v1/files/f-wav/content#t=1,2.5"))
        mp3 = base64.b64decode(out[0]["content"][1]["input_audio"]["data"])
        assert abs(asyncio.run(audio_input.probe_duration(mp3)) - 1.5) < 0.2

    def test_not_audio_model_gets_a_note(self):
        from nucllei.utils.audio_input import NOT_AUDIO_MODEL_TEXT

        self._file("f-wav", _wav(1.0), "audio/wav")
        out = self._inline(
            self._ref_message("/api/v1/files/f-wav/content"), audio_capable=False
        )
        assert out[0]["content"][1] == {"type": "text", "text": NOT_AUDIO_MODEL_TEXT}

    def test_unreadable_refs_get_the_same_note(self):
        from nucllei.utils.audio_input import UNAVAILABLE_AUDIO_TEXT

        self._file("f-wav", _wav(1.0), "audio/wav", owner="u-other")
        for url in (
            "/api/v1/files/f-wav/content",  # 他人の file
            "/api/v1/files/missing/content",  # 無い
            "relative/path.mp3",  # どの形でもない
        ):
            out = self._inline(self._ref_message(url))
            assert out[0]["content"][1] == {
                "type": "text",
                "text": UNAVAILABLE_AUDIO_TEXT,
            }, url

    def test_unconvertible_and_too_long_get_named_notes(self):
        from nucllei.utils import audio_input

        self._file("f-bad", b"\x1aE\xdf\xa3broken webm", "audio/webm", name="bad.webm")
        out = self._inline(self._ref_message("/api/v1/files/f-bad/content"))
        assert out[0]["content"][1]["type"] == "text"
        assert "bad.webm" in out[0]["content"][1]["text"]

        self._file("f-wav", _wav(3.0), "audio/wav", name="long.wav")
        self.monkeypatch.setattr(audio_input, "AUDIO_INPUT_MAX_MINUTES", 1 / 60)
        out = self._inline(self._ref_message("/api/v1/files/f-wav/content"))
        text = out[0]["content"][1]["text"]
        assert "long.wav" in text and "上限" in text

    def test_headless_data_parts(self):
        """data を持つパート: mp3 などはそのまま、webm(上流が 400)は mp3 に。能力が無ければ触らない。"""
        webm = base64.b64encode(_webm_without_duration(1.0)).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "AAAA", "format": "mp3"},
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {"data": webm, "format": "webm"},
                    },
                ],
            }
        ]
        untouched = self._inline(copy.deepcopy(messages), audio_capable=False)
        assert untouched == messages
        out = self._inline(messages)
        assert out[0]["content"][0]["input_audio"] == {"data": "AAAA", "format": "mp3"}
        assert out[0]["content"][1]["input_audio"]["format"] == "mp3"

    # ---- plan_attachment_audio / process_chat_payload ----

    def _plan(self, files, model):
        from nucllei.utils.audio_input import plan_attachment_audio

        return asyncio.run(plan_attachment_audio(files, self.user, model))

    def test_plan(self):
        from nucllei.utils import audio_input

        a = self._file("f-a", _wav(2.0), "audio/wav", name="a.wav")
        d = self._file(
            "f-d",
            _wav(2.0),
            "audio/webm",
            meta={"dictation": {"version": 1, "parts": 1}},
        )
        t = self._file("f-t", b"text", "text/plain")
        long = self._file("f-l", _wav(3.0), "audio/wav", name="long.wav")
        assert self._plan([a, d, t, long], self._model(audio=False)) == {}

        self.monkeypatch.setattr(audio_input, "AUDIO_INPUT_MAX_MINUTES", 2.5 / 60)
        plan = self._plan([a, d, t, long], self._model())
        assert set(plan) == {"f-a", "f-l"}  # 通し録音・音声でないものは入れない
        assert plan["f-a"].inline and abs(plan["f-a"].duration - 2.0) < 0.05
        assert not plan["f-l"].inline and plan["f-l"].name == "long.wav"

    def _run(self, messages, files, model):
        from starlette.requests import Request

        from nucllei.main import app
        from nucllei.utils import audio_input, middleware

        self.monkeypatch.setattr(
            app.state, "MODELS", {model["id"]: model}, raising=False
        )

        async def fake_transcode(data, start=None, end=None):
            return b"MP3:" + data[:4]

        self.monkeypatch.setattr(audio_input, "transcode_to_mp3", fake_transcode)
        request = Request(
            scope={
                "type": "http",
                "app": app,
                "method": "POST",
                "path": "/api/chat/completions",
                "headers": [],
                "query_string": b"",
            }
        )
        metadata = {"user_id": USER_ID, "files": files, "model": model}
        form_data = {
            "model": model["id"],
            "stream": True,
            "messages": copy.deepcopy(messages),
            "files": files,
            "metadata": metadata,
        }
        out, _, _ = asyncio.run(
            middleware.process_chat_payload(
                request, form_data, self.user, metadata, model, None
            )
        )
        return out["messages"]

    def test_attached_audio_goes_into_its_message_as_audio(self):
        from nucllei.utils import audio_input

        a = self._file("f-a", _wav(2.0), "audio/wav", name="a.wav")
        d = self._file(
            "f-d", _wav(2.0), "audio/webm", meta={"dictation": {"version": 1}}
        )
        long = self._file("f-l", _wav(3.0), "audio/wav", name="long.wav")
        self.monkeypatch.setattr(audio_input, "AUDIO_INPUT_MAX_MINUTES", 2.5 / 60)
        wire = [
            {"role": "user", "id": "m1", "content": "これを聞いて", "files": [a]},
            {"role": "assistant", "content": "聞きました。"},
            {"role": "user", "id": "m2", "content": "次はこれ", "files": [d, long]},
        ]
        out = self._run(wire, [a, d, long], self._model())
        expected_data = base64.b64encode(b"MP3:" + _wav(2.0)[:4]).decode()
        first = out[0]["content"]
        assert isinstance(first, list)
        assert first[-1] == {
            "type": "input_audio",
            "input_audio": {"data": expected_data, "format": "mp3"},
        }
        assert 'file_id="f-a"' in first[0]["text"]
        assert 'audio="inline" duration="0:02"' in first[0]["text"]
        assert "(この音声はこの発言にそのまま入っている)" in first[0]["text"]
        # 2 つ目の発言: 通し録音は入れない・上限を超える長さは入れず、<source> がそう言う
        second = out[2]["content"]
        assert isinstance(second, str)
        assert "input_audio" not in str(out[2])
        assert 'file_id="f-l"' in second and 'audio="too_long"' in second
        assert "上限" in second and "聞こえていない" in second
        assert 'file_id="f-d" name="f-d.bin" dictation="true"' in second

    def test_non_audio_model_is_unchanged(self):
        a = self._file("f-a", _wav(2.0), "audio/wav", name="a.wav")
        wire = [{"role": "user", "id": "m1", "content": "これを聞いて", "files": [a]}]
        out = self._run(wire, [a], self._model(audio=False))
        assert isinstance(out[0]["content"], str)
        assert "audio=" not in out[0]["content"]
        assert 'file_id="f-a"' in out[0]["content"]
