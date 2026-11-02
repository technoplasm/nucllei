"""音声のまま聞かせる — 音声を聞けるモデル(`capabilities.audio`)への input_audio。

画像を見せる経路(`utils/file_images.py`)と同じ形: 発言・tool の結果には **参照** だけを置き、
モデルへ出る直前(process_chat_payload の末尾・tool ループの追撃・carried の計測)にここが
mp3 の base64 にする。base64 は DB にも戻り境界にも載らない。

参照の形(nucllei の中だけの形 — 上流には出ない):

    {"type": "input_audio", "input_audio": {"url": "<参照>"}}

`<参照>` は保存済みの参照文字列(`utils/image_refs.stored_ref_kind` の表 — file の配信 URL
`/api/v1/files/{id}/content`・裸の file id・URL・sandbox のパス)に、任意で区間
`#t=<開始秒>,<終了秒>`(W3C Media Fragments の形。どちらかは省ける)を付けたもの。
上流へ出る形は `{"type": "input_audio", "input_audio": {"data": "<base64>", "format": "mp3"}}`
(staging で mimo-v2.6-pro / gemini が受けた形。webm は 400、`audio_url` は黙って無視される)。

入口は 2 つ:

- **添付の音声**(`plan_attachment_audio`): `audio/*` の添付を、それを付けた発言に入れる
  (置き場所は `utils/conversation_context` — `<source>` と同じ発言)。音声入力の通し録音
  (`meta.dictation`)も同じ規則で入れる(発言の文はその文字起こしで、音声も聞ける — 分かれた録音は
  ファイルごと)。長さが `AUDIO_INPUT_MAX_MINUTES` を
  超えるものは入れず、`<source>` がそう告げる(聞くなら feed_audio で区間を指定する)。
  添付は履歴に残るので、以後のターンも毎回入る(画像の添付と同じ)。
- **feed_audio**(`builtin_tools/feed_audio.py`): tool が参照を sink に積み、middleware が
  results[i].context_audio に載せる。履歴では tool メッセージ群の直後の user メッセージに
  入る(context_images と同じ位置 — `utils/content_blocks` / FE `chat-messages.ts`)。

長さは送信時に測る(ffmpeg で中身を読む)。webm(ブラウザの録音)は長さを持たないので、
ffprobe ではなく ffmpeg の stream copy(デコードしない)で最後まで読む — 60 分で約 0.4 秒。
結果は中身のハッシュでプロセス内に覚える。mp3 への変換(60 分で約 5 秒)は中身のハッシュ +
区間で `CACHE_DIR/audio/input/` に置き、次のターンからは読むだけにする。

モデルが音声を聞けない(`capabilities.audio` が無い)ターンに参照が来たら(音声を聞けるモデル
から切り替えたチャットの履歴)、注記の text に置き換える — 音声を受けない上流はリクエストごと
落とす(staging: openrouter/kimi-k2.6 が 404 "No endpoints found that support input audio")。
読めない・変換できない参照も同じく注記にする(リクエストを恒久的に落とさない — 画像の
"(image unavailable)" と同じ規律。読めない理由は言い分けない)。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import mimetypes
import os
import shutil
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from nucllei.config import CACHE_DIR
from nucllei.env import AUDIO_INPUT_MAX_MINUTES, SRC_LOG_LEVELS
from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.access_control import has_read_access_to_file
from nucllei.utils.audio_refs import (
    audio_ref_part as audio_ref_part,  # 呼び手(middleware)が引く
    file_audio_ref as file_audio_ref,
    format_time,
    seconds_text,
    is_audio_model,
    split_range,
)
from nucllei.utils.image_refs import (
    LoadedRef,
    base_media_type,
    file_id_of_url,
    resolve_ref,
    stored_ref_kind,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["AUDIO"])

# 上流が data のまま受ける形式(staging で確かめた 4 つ)。これ以外の data は mp3 にする
ACCEPTED_FORMATS = frozenset({"mp3", "wav", "flac", "m4a"})
# 送る形: mono・16 kHz・32 kbps の mp3(60 分で約 14 MB、MiMo で 60 分 ≈ 2.3 万 token)
ENCODE_ARGS = (
    "-ac",
    "1",
    "-ar",
    "16000",
    "-c:a",
    "libmp3lame",
    "-b:a",
    "32k",
)
_ENCODE_TAG = "mono16k32k-v1"

AUDIO_CACHE_DIR = CACHE_DIR / "audio" / "input"
# 変換済み mp3 の置き場の上限。超えたら古い(最後に使った時刻の)順に消す
AUDIO_CACHE_MAX_BYTES = 1024 * 1024 * 1024
# ffmpeg 1 回の上限(秒)。120 分の変換で約 10 秒
FFMPEG_TIMEOUT_S = 300

# 音声の代わりに置く注記(画像の UNAVAILABLE_IMAGE_TEXT と同じ言い方。理由は言い分けない)
UNAVAILABLE_AUDIO_TEXT = "(ここにあった音声は読み込めなかった — 中身は聞こえていない)"
NOT_AUDIO_MODEL_TEXT = "(ここにあった音声は、このモデルが音声を聞けないので渡していない — 中身は聞こえていない)"


def max_seconds() -> float:
    """1 本(feed_audio は 1 回の区間)の長さの上限(秒)。"""
    return AUDIO_INPUT_MAX_MINUTES * 60


def max_minutes_text() -> str:
    return f"{AUDIO_INPUT_MAX_MINUTES:g} 分"


# ---------------------------------------------------------------------------
# 種別
# ---------------------------------------------------------------------------

_AUDIO_MAGIC = (
    (b"ID3", "audio/mpeg"),
    (b"fLaC", "audio/flac"),
    (b"OggS", "audio/ogg"),
    (b"\x1aE\xdf\xa3", "audio/webm"),  # EBML(webm / matroska)
)


def _sniff_audio(data: bytes) -> str | None:
    for magic, media_type in _AUDIO_MAGIC:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    if len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mpeg"  # mp3 のフレーム同期(ID3 無し)
    return None


def audio_media_type(loaded: LoadedRef) -> str | None:
    """音声(または音声を含みうる動画)の型。magic 優先、次に content-type、次に拡張子。
    どれでもなければ None。中に音声の流れがあるかは ffmpeg が読むまで分からない。"""
    for media_type in (
        _sniff_audio(loaded.data),
        loaded.content_type,
        mimetypes.guess_type(loaded.name or "")[0],
    ):
        base = base_media_type(media_type)
        if base.startswith(("audio/", "video/")):
            return base
    return None


# ---------------------------------------------------------------------------
# ffmpeg(長さ・mp3 への変換)
# ---------------------------------------------------------------------------


class AudioConvertError(Exception):
    """ffmpeg が読めなかった・変換できなかった。"""


async def _run_ffmpeg(args: list[str]) -> tuple[int, bytes, bytes]:
    if shutil.which("ffmpeg") is None:
        raise AudioConvertError("ffmpeg is not installed")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise AudioConvertError(f"ffmpeg timed out ({FFMPEG_TIMEOUT_S}s)")
    return proc.returncode, out, err


def _write_temp(data: bytes, directory: str) -> str:
    path = os.path.join(directory, "in")
    with open(path, "wb") as f:
        f.write(data)
    return path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# 長さ(秒)の覚え(中身のハッシュ → 秒)。中身が同じなら長さも同じ
_DURATIONS: OrderedDict[str, float] = OrderedDict()
_DURATIONS_MAX = 512


async def probe_duration(data: bytes) -> float | None:
    """音声の長さ(秒)。音声の流れが無い・読めなければ None。

    ffprobe の format=duration は webm(MediaRecorder の録音)で N/A になるので、ffmpeg の
    stream copy で最後のパケットまで読んだ時刻を使う(デコードしない — 60 分で約 0.4 秒)。"""
    digest = await asyncio.to_thread(_sha256, data)
    if digest in _DURATIONS:
        _DURATIONS.move_to_end(digest)
        return _DURATIONS[digest]
    with tempfile.TemporaryDirectory(prefix="nucllei-audio-") as tmp:
        src = await asyncio.to_thread(_write_temp, data, tmp)
        try:
            code, out, err = await _run_ffmpeg(
                [
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    src,
                    "-map",
                    "0:a:0",
                    "-c",
                    "copy",
                    "-f",
                    "null",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    "-",
                ]
            )
        except AudioConvertError as e:
            log.warning(f"audio_input: duration probe failed: {e}")
            return None
    if code != 0:
        log.warning(
            f"audio_input: duration probe failed (ffmpeg exit {code}): "
            f"{err.decode('utf-8', 'replace')[-300:]!r}"
        )
        return None
    duration = None
    for line in out.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition("=")
        if key == "out_time_us" and value.strip().isdigit():
            duration = int(value.strip()) / 1_000_000
    if not duration:
        return None
    _DURATIONS[digest] = duration
    if len(_DURATIONS) > _DURATIONS_MAX:
        _DURATIONS.popitem(last=False)
    return duration


def _cache_path(digest: str, start: float | None, end: float | None) -> Path:
    span = f"{seconds_text(start) if start is not None else ''}-" + (
        seconds_text(end) if end is not None else ""
    )
    key = hashlib.sha256(f"{digest}|{span}|{_ENCODE_TAG}".encode()).hexdigest()
    return AUDIO_CACHE_DIR / f"{key}.mp3"


def _read_cached(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        os.utime(path)  # 最後に使った時刻(古い順に消すため)
    except OSError:
        pass
    return data or None


def _store_cached(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        _prune_cache()
    except OSError:
        log.exception("audio_input: failed to write the converted audio cache")


def _prune_cache() -> None:
    entries = []
    total = 0
    for entry in AUDIO_CACHE_DIR.glob("*.mp3"):
        try:
            stat = entry.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, entry))
        total += stat.st_size
    if total <= AUDIO_CACHE_MAX_BYTES:
        return
    for _mtime, size, entry in sorted(entries):
        try:
            entry.unlink()
        except OSError:
            continue
        total -= size
        if total <= AUDIO_CACHE_MAX_BYTES * 0.8:
            break


async def transcode_to_mp3(
    data: bytes, start: float | None = None, end: float | None = None
) -> bytes:
    """音声(の区間)を mono・16 kHz・32 kbps の mp3 にする(`ENCODE_ARGS`)。結果は中身の
    ハッシュ + 区間で `AUDIO_CACHE_DIR` に置いて使い回す。読めない・音声の流れが無いと
    AudioConvertError。"""
    digest = await asyncio.to_thread(_sha256, data)
    cached_path = _cache_path(digest, start, end)
    cached = await asyncio.to_thread(_read_cached, cached_path)
    if cached is not None:
        return cached
    with tempfile.TemporaryDirectory(prefix="nucllei-audio-") as tmp:
        src = await asyncio.to_thread(_write_temp, data, tmp)
        out = os.path.join(tmp, "out.mp3")
        args = ["-nostdin", "-v", "error", "-y"]
        if start:
            args += ["-ss", seconds_text(start)]
        args += ["-i", src]
        if end is not None:
            args += ["-t", seconds_text(max(0.0, end - (start or 0.0)))]
        args += ["-vn", "-map", "0:a:0", *ENCODE_ARGS, "-f", "mp3", out]
        code, _out, err = await _run_ffmpeg(args)
        if code != 0:
            raise AudioConvertError(
                f"ffmpeg exit {code}: {err.decode('utf-8', 'replace')[-300:]!r}"
            )
        try:
            mp3 = await asyncio.to_thread(Path(out).read_bytes)
        except OSError as e:
            raise AudioConvertError(f"no output: {e!r}")
    if not mp3:
        raise AudioConvertError("empty output")
    await asyncio.to_thread(_store_cached, cached_path, mp3)
    return mp3


# ---------------------------------------------------------------------------
# 添付の音声
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachedAudio:
    """添付の音声 1 本の扱い(`<source>` の `audio` 属性の値)。

    - `inline` — 発言に音声のまま入れる(長さが測れなかったものも入れる — 送信直前に読めなければ
      注記になる)
    - `too_long` — 上限(`AUDIO_INPUT_MAX_MINUTES`)を超えるので入れない
    - `unsupported` — モデルが音声を聞けない(`capabilities.audio` が無い)ので入れない

    `duration` は秒(測れなければ None。`unsupported` は測らない)。`dictation` は音声入力の
    通し録音(`meta.dictation` — 発言の文はその文字起こし)か。"""

    file_id: str
    name: str
    duration: float | None
    state: str
    dictation: bool = False

    @property
    def inline(self) -> bool:
        return self.state == "inline"


def _read_file(file_id: str, user) -> tuple[LoadedRef | None, dict]:
    """file の実体と meta(blocking — thread で呼ぶ)。読めない・権限が無ければ (None, {})。"""
    if not has_read_access_to_file(file_id, user):
        log.warning(
            "audio_input: skipping unreadable or missing file reference "
            f"(user_id={getattr(user, 'id', None)}, file_id={file_id})"
        )
        return None, {}
    file = Files.get_file_by_id(file_id)
    if not file or not file.path:
        log.warning(f"audio_input: file has no stored content (file_id={file_id})")
        return None, {}
    meta = file.meta or {}
    try:
        with open(Storage.get_file(file.path), "rb") as f:
            raw = f.read()
    except Exception:
        log.exception(f"audio_input: storage read failed (file_id={file_id})")
        return None, meta
    content_type = meta.get("content_type") or (
        mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    )
    return (
        LoadedRef(data=raw, content_type=content_type, name=file.filename or ""),
        meta,
    )


# file id → 長さ(秒)。file の中身は id ごとに変わらない
_FILE_DURATIONS: OrderedDict[str, float] = OrderedDict()


def _file_entry_id(entry) -> str | None:
    if not isinstance(entry, dict):
        return None
    if entry.get("type") not in (None, "file"):
        return None
    file_id = entry.get("id") or (entry.get("file") or {}).get("id")
    return file_id if isinstance(file_id, str) and file_id else None


async def plan_attachment_audio(files, user, model) -> dict[str, AttachedAudio]:
    """添付(`metadata["files"]` — 読取権限で絞った後)の音声の扱い(file id → AttachedAudio)。

    対象は Files.meta の content_type が `audio/*` のもの。モデルが音声を聞けなければ全部
    `unsupported`(中身は読まない — `<source>` が音声で、渡していないと言うため)。ただし音声入力の
    通し録音(`meta.dictation`)は聞けないモデルでは対象にしない(発言の文がその文字起こしなので、
    「渡していない」とは言わない)。聞けるモデルでは通し録音も含めて長さを測り、上限
    (`AUDIO_INPUT_MAX_MINUTES`)以下は `inline`、超えるものは `too_long`。"""
    audio_capable = is_audio_model(model)
    plans: dict[str, AttachedAudio] = {}
    for entry in files or []:
        file_id = _file_entry_id(entry)
        if not file_id or file_id in plans:
            continue
        record = await asyncio.to_thread(Files.get_file_by_id, file_id)
        if record is None:
            continue
        meta = record.meta or {}
        if not base_media_type(meta.get("content_type")).startswith("audio/"):
            continue
        dictation = bool(meta.get("dictation"))
        if dictation and not audio_capable:
            continue
        name = record.filename or meta.get("name") or file_id
        if not audio_capable:
            plans[file_id] = AttachedAudio(file_id, name, None, "unsupported")
            continue
        duration = _FILE_DURATIONS.get(file_id)
        if duration is None:
            loaded, _meta = await asyncio.to_thread(_read_file, file_id, user)
            if loaded is None:
                continue
            duration = await probe_duration(loaded.data)
            if duration is not None:
                _FILE_DURATIONS[file_id] = duration
                if len(_FILE_DURATIONS) > _DURATIONS_MAX:
                    _FILE_DURATIONS.popitem(last=False)
        plans[file_id] = AttachedAudio(
            file_id=file_id,
            name=name,
            duration=duration,
            state=(
                "inline"
                if duration is None or duration <= max_seconds()
                else "too_long"
            ),
            dictation=dictation,
        )
    return plans


# ---------------------------------------------------------------------------
# 送信直前の実体化
# ---------------------------------------------------------------------------


class _Note(str):
    """input_audio パートの代わりに置く text(音声として送れなかった注記)。"""


def _too_long_note(name: str, start: float, end: float) -> _Note:
    span = "" if start <= 0 else f"の {format_time(start)}〜{format_time(end)} "
    return _Note(
        f"({name} {span}は {format_time(end - start)} あり、一度に聞ける上限 "
        f"{max_minutes_text()} を超えるので渡していない — 中身は聞こえていない)"
    )


async def _encode(loaded: LoadedRef, start, end, name: str, label: str):
    """読めた音声(の区間)を input_audio の中身 {"data", "format"} にする。区間が上限を
    超える・範囲外・変換できなければ注記。"""
    duration = await probe_duration(loaded.data)
    if duration is None:
        log.warning(f"audio_input: not readable as audio ({label})")
        return _Note(f"({name}: 音声として読めなかった — 中身は聞こえていない)")
    begin = start or 0.0
    finish = duration if end is None else min(end, duration)
    if begin >= duration or finish <= begin:
        log.warning(
            f"audio_input: range outside the audio ({label}, duration={duration})"
        )
        return _Note(
            f"({name} は全 {format_time(duration)} で、指定の区間は範囲外 — 中身は聞こえていない)"
        )
    if finish - begin > max_seconds():
        return _too_long_note(name, begin, finish)
    try:
        mp3 = await transcode_to_mp3(
            loaded.data, start or None, end if end is not None else None
        )
    except AudioConvertError as e:
        log.warning(f"audio_input: conversion failed ({label}): {e}")
        return _Note(f"({name}: 音声に変換できなかった — 中身は聞こえていない)")
    encoded = await asyncio.to_thread(base64.b64encode, mp3)
    return {"data": encoded.decode("ascii"), "format": "mp3"}


async def inline_audio_refs(
    messages: list, user, *, audio_capable: bool, cache: dict | None = None
) -> list:
    """messages(OpenAI 形式)内の input_audio の参照(`{"url": …}`)を mp3 の base64 にする。

    messages を破壊的に書き換えて同じ list を返す。モデルが音声を聞けない
    (`audio_capable=False`)ときは参照を注記 `NOT_AUDIO_MODEL_TEXT` に置き換える。読めない
    参照(権限が無い・無い・解決できない)は `UNAVAILABLE_AUDIO_TEXT`、読めたが変換できない・
    上限を超える区間は名前付きの注記にする。

    `data` を持つパート(headless API の呼び手が自分で入れたもの)は、音声を聞けるモデルで
    形式が `ACCEPTED_FORMATS` の外のときだけ mp3 にする(それ以外は触らない)。

    cache: ターン内(1 user)で結果を使い回す dict(tool ループの追撃は毎周 messages を
    作り直す)。使い回すのは中身が変わらない参照(file・http(s) の URL)だけ — sandbox の
    パスと `/ext/…` は同じターンでも上書きされうるので毎回読む(mp3 は中身のハッシュで
    `AUDIO_CACHE_DIR` から引くので、変換し直しはしない)。
    """
    turn_cache: dict = cache if cache is not None else {}

    async def from_ref(ref: str):
        base, start, end = split_range(ref)
        kind = stored_ref_kind(base)
        reusable = kind in ("file_url", "file_id") or base.lower().startswith(
            ("http://", "https://")
        )
        if reusable and ref in turn_cache:
            return turn_cache[ref]
        file_id = file_id_of_url(base) or (base if kind == "file_id" else None)
        if file_id:
            loaded, _meta = await asyncio.to_thread(_read_file, file_id, user)
            name = loaded.name if loaded is not None and loaded.name else file_id
            label = f"file_id={file_id}"
        else:
            loaded, error = await resolve_ref(base, user)
            if error is not None:
                log.warning(
                    f"audio_input: reference unavailable (ref={base[:120]!r}, "
                    f"code={error.code}, detail={error.detail})"
                )
            name = base
            label = f"ref={base[:120]}"
        if loaded is None:
            result = None
        elif audio_media_type(loaded) is None:
            log.warning(
                f"audio_input: reference is not audio ({label}, "
                f"content_type={loaded.content_type!r})"
            )
            result = _Note(f"({name}: 音声ではない — 中身は聞こえていない)")
        else:
            result = await _encode(loaded, start, end, name, label)
        if reusable:
            turn_cache[ref] = result
        return result

    async def from_data(payload: dict):
        data = payload.get("data")
        key = "data:" + hashlib.sha256(data.encode()).hexdigest()
        if key in turn_cache:
            return turn_cache[key]
        try:
            raw = base64.b64decode(data, validate=False)
        except (ValueError, binascii.Error):
            raw = b""
        loaded = LoadedRef(data=raw, content_type="", name="")
        result = (
            await _encode(loaded, None, None, "添付の音声", "data")
            if raw
            else _Note(UNAVAILABLE_AUDIO_TEXT)
        )
        turn_cache[key] = result
        return result

    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not (isinstance(part, dict) and part.get("type") == "input_audio"):
                continue
            payload = part.get("input_audio")
            if not isinstance(payload, dict):
                continue
            ref = payload.get("url")
            if isinstance(ref, str):
                if not audio_capable:
                    result = _Note(NOT_AUDIO_MODEL_TEXT)
                else:
                    result = await from_ref(ref)
            elif (
                audio_capable
                and isinstance(payload.get("data"), str)
                and str(payload.get("format") or "").lower() not in ACCEPTED_FORMATS
            ):
                result = await from_data(payload)
            else:
                continue
            part.clear()
            if isinstance(result, dict):
                part["type"] = "input_audio"
                part["input_audio"] = dict(result)
            else:
                part["type"] = "text"
                part["text"] = UNAVAILABLE_AUDIO_TEXT if result is None else str(result)
    return messages
