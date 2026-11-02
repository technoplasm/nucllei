"""音声の参照の形 — input_audio の参照パート・区間 `#t=…`・時刻の読み書き(純関数だけ)。

送信直前の実体化(ffmpeg・Storage)は `utils/audio_input.py`。ここは履歴の組み立て
(`utils/content_blocks.py`)と feed_audio が共有する、DB や設定に触れない部分。

参照パート(nucllei の中だけの形 — 上流には出ない):

    {"type": "input_audio", "input_audio": {"url": "<参照>"}}

`<参照>` は保存済みの参照文字列(`utils/image_refs.stored_ref_kind` の表)に、任意で区間
`#t=<開始秒>,<終了秒>`(W3C Media Fragments の形。どちらかは省ける)を付けたもの。
"""

import math
import re

from nucllei.utils.image_refs import stored_ref_kind

# 区間 `#t=<開始>,<終了>`(秒。どちらかは省ける)。参照の末尾だけを見る
_RANGE_SUFFIX = re.compile(r"^(.*)#t=(\d+(?:\.\d+)?)?(?:,(\d+(?:\.\d+)?))?$")
_CLOCK = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")
_SECONDS = re.compile(r"^\d+(?:\.\d+)?$")


def is_audio_model(model) -> bool:
    """モデルが音声を聞けるか(`info.meta.capabilities.audio`)。**既定 OFF** — 音声を受けない
    上流に送るとリクエストごと落ちるので、明示的に有効にしたモデルだけ。"""
    meta = ((model or {}).get("info") or {}).get("meta") or {}
    return (meta.get("capabilities") or {}).get("audio") is True


# ---------------------------------------------------------------------------
# 時刻・区間
# ---------------------------------------------------------------------------


def parse_time(value) -> float:
    """ "23:10"(分:秒)/ "1:02:03"(時:分:秒)/ 秒数("90" / 90 / 90.5)を秒にする。
    読めない・負の値は ValueError。"""
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip()
        if _SECONDS.match(text):
            seconds = float(text)
        else:
            match = _CLOCK.match(text)
            if not match:
                raise ValueError(value)
            hours, minutes, secs = match.groups()
            if float(secs) >= 60 or (hours is not None and int(minutes) >= 60):
                raise ValueError(value)
            seconds = int(hours or 0) * 3600 + int(minutes) * 60 + float(secs)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(value)
    return seconds


def format_time(seconds: float) -> str:
    """秒 → "M:SS" / "H:MM:SS"(秒未満は切り捨て)。"""
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def seconds_text(seconds: float) -> str:
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def with_range(ref: str, start: float | None, end: float | None) -> str:
    """参照に区間 `#t=<開始>,<終了>` を付ける(どちらも None なら付けない)。"""
    base, _, _ = split_range(ref)
    if start is None and end is None:
        return base
    head = seconds_text(start) if start is not None else ""
    tail = f",{seconds_text(end)}" if end is not None else ""
    return f"{base}#t={head}{tail}"


def split_range(ref: str) -> tuple[str, float | None, float | None]:
    """ "/a.mp3#t=10,70" → ("/a.mp3", 10.0, 70.0)。区間が無ければ (ref, None, None)。"""
    match = _RANGE_SUFFIX.match(ref) if isinstance(ref, str) else None
    if not match or (match.group(2) is None and match.group(3) is None):
        return ref, None, None
    start = float(match.group(2)) if match.group(2) is not None else None
    end = float(match.group(3)) if match.group(3) is not None else None
    return match.group(1), start, end


def audio_ref_part(ref: str) -> dict:
    """参照の input_audio パート(送信直前に `inline_audio_refs` が実体にする)。"""
    return {"type": "input_audio", "input_audio": {"url": ref}}


def file_audio_ref(file_id: str) -> str:
    return f"/api/v1/files/{file_id}/content"


def context_audio_url(entry: str) -> str:
    """results[].context_audio のエントリ → input_audio の url(context_images と同じ分岐:
    裸の文字列 = file id だけ配信 URL に組み、それ以外はそのまま)。"""
    base, start, end = split_range(entry)
    if stored_ref_kind(base) == "file_id":
        return with_range(file_audio_ref(base), start, end)
    return entry
