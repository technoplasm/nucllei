"""STT の幻聴(無音・雑音から出る定型句や繰り返し)を転写結果から落とす後処理。

Whisper は無音・物音の区間にも文を出す。典型は動画の締めの定型句(「ご視聴ありがとうございました」
「チャンネル登録…」「Thank you for watching」)と、同じ語・句の繰り返しループ。
ここではエンジンを問わず、転写されたテキストに対して次の 2 つを行う:

- **定型句の除去**: 定型句だけでできている区間を、無音由来と言える位置にあるときだけ落とす。
  発話の途中で本当にそう言った場合(文の一部、話の途中の区間)は残す。
  - 内蔵 faster-whisper(区間の信号がある): 区間が定型句だけで、かつ
    (転写全体が定型句だけ / 音声の終わりを越えている / 末尾の区間で、前に無音がある
    か無音らしさが高い)のときに落とす。
  - 外部エンジン(テキストだけ): 転写全体が定型句だけのときに限り空にする。
- **繰り返しの畳み込み**: 同じ短い単位(1〜`repeat_max_unit_chars` 文字)が
  `repeat_min_count` 回以上続いたら 1 回にする。数字だけの単位(「1000000」の「0」)は畳まない。

内蔵 faster-whisper の区間には、さらに無音らしさ(no_speech_prob と avg_logprob)と
区間自身の圧縮率(区間の中を畳んだ後)による落としをかける(`filter_segments`)。
設定値は config.py の WHISPER_* / AUDIO_STT_* が持ち、`SttFilterOptions` にまとめて渡す。
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass, field
from typing import Iterable, Optional

# 既定の定型句(ja / en)。照合は正規化(NFKC・小文字化・空白と句読点の除去)したうえで、
# 区間(または転写全体)がこれらの 1 つ以上の連なり「だけ」でできているかで見る
DEFAULT_HALLUCINATION_PHRASES: tuple[str, ...] = (
    # ja
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "ご視聴いただきありがとうございました",
    "ご視聴いただきありがとうございます",
    "最後までご視聴いただきありがとうございました",
    "最後までご視聴ありがとうございました",
    "最後までご覧いただきありがとうございました",
    "ご清聴ありがとうございました",
    "チャンネル登録お願いします",
    "チャンネル登録をお願いします",
    "チャンネル登録よろしくお願いします",
    "チャンネル登録をよろしくお願いします",
    "チャンネル登録お願いいたします",
    "チャンネル登録をお願いいたします",
    "チャンネル登録と高評価をお願いします",
    "チャンネル登録と高評価よろしくお願いします",
    "高評価とチャンネル登録をお願いします",
    "高評価とチャンネル登録よろしくお願いします",
    "次回もお楽しみに",
    "字幕視聴ありがとうございました",
    # en
    "Thank you for watching",
    "Thanks for watching",
    "Thank you so much for watching",
    "Thank you very much for watching",
    "Thank you for watching and see you next time",
    "Please subscribe",
    "Please subscribe to my channel",
    "Please like and subscribe",
    "Don't forget to like and subscribe",
    "Subtitles by the Amara.org community",
)

# 区間の終わりが音声の長さをこれだけ越えたら「音声の外まで喋っている」とみなす(秒)。
# 発話の区間は VAD の余白込みでも音声の中に収まる。無音・物音から出た定型句は、Whisper が
# 30 秒窓の終わりの時刻を付けるので音声の長さを大きく越える
PAST_END_MARGIN_SECONDS = 1.0

# 照合の前に落とす文字(空白・句読点・記号)
_STRIP_CHARS = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SttFilterOptions:
    """後処理の設定。既定値は config.py の既定と同じ。"""

    enabled: bool = True
    # 区間の無音判定: no_speech_prob > これ かつ avg_logprob < log_prob_threshold で落とす
    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    # 区間自身のテキストの圧縮率(gzip)がこれを越えたら繰り返しとして落とす
    compression_ratio_threshold: float = 2.4
    # 末尾の定型句の前にこれ以上の無音があれば「無音の後ろ」とみなす(秒)
    silence_seconds: float = 2.0
    phrases: tuple[str, ...] = field(default=DEFAULT_HALLUCINATION_PHRASES)
    # 繰り返しの畳み込み: 1〜repeat_max_unit_chars 文字の単位が repeat_min_count 回以上続いたら 1 回に。
    # repeat_min_count = 0 で畳まない
    repeat_min_count: int = 5
    repeat_max_unit_chars: int = 50


def parse_phrases(raw: Optional[str]) -> tuple[str, ...]:
    """`|` か改行で区切った定型句の一覧。None(未設定)なら既定の一覧、空文字なら空。"""
    if raw is None:
        return DEFAULT_HALLUCINATION_PHRASES
    return tuple(p.strip() for p in re.split(r"[|\n]", raw) if p.strip())


def _normalize(text: str) -> str:
    return _STRIP_CHARS.sub("", unicodedata.normalize("NFKC", text).casefold())


def _phrase_pattern(phrases: Iterable[str]) -> Optional[re.Pattern]:
    normalized = sorted({_normalize(p) for p in phrases} - {""}, key=len, reverse=True)
    if not normalized:
        return None
    return re.compile("(?:" + "|".join(map(re.escape, normalized)) + ")+")


_PATTERN_CACHE: dict[tuple[str, ...], Optional[re.Pattern]] = {}


def is_hallucination_phrase(text: str, phrases: Iterable[str]) -> bool:
    """text が定型句(1 つ以上の連なり)だけでできているか。空のテキストは False。"""
    key = tuple(phrases)
    if key not in _PATTERN_CACHE:
        _PATTERN_CACHE[key] = _phrase_pattern(key)
    pattern = _PATTERN_CACHE[key]
    normalized = _normalize(text)
    return bool(pattern and normalized and pattern.fullmatch(normalized))


def compression_ratio(text: str) -> float:
    """Whisper と同じ gzip 圧縮率(繰り返しが多いほど大きい)。"""
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(zlib.compress(data))


def collapse_repetitions(text: str, min_count: int, max_unit_chars: int) -> str:
    """同じ単位が min_count 回以上続く箇所を 1 回にする。数字だけの単位は畳まない。"""
    if min_count < 2 or max_unit_chars < 1 or not text:
        return text
    pattern = re.compile(
        r"(.{1,%d}?)\1{%d,}" % (max_unit_chars, min_count - 1), re.DOTALL
    )

    def replace(match: re.Match) -> str:
        unit = match.group(1)
        if unit.isdigit() or not unit.strip():
            return match.group(0)
        return unit

    return pattern.sub(replace, text)


def filter_text(text: str, options: SttFilterOptions) -> str:
    """テキストだけが返るエンジン用: 全体が定型句だけなら空、繰り返しは畳む。"""
    if not options.enabled or not text:
        return text
    if is_hallucination_phrase(text, options.phrases):
        return ""
    return collapse_repetitions(
        text, options.repeat_min_count, options.repeat_max_unit_chars
    ).strip()


def _num(segment, name: str, default: float) -> float:
    value = getattr(segment, name, None)
    return default if value is None else value


def filter_segments(
    segments: Iterable, duration: Optional[float], options: SttFilterOptions
) -> str:
    """内蔵 faster-whisper の区間列から転写テキストを組み立てる(幻聴の区間を落とす)。

    segments は faster-whisper の Segment(text / start / end / no_speech_prob / avg_logprob を
    読む。無い属性は落とす判定に使わない)。duration は入力音声の長さ(秒、不明なら None)。
    """
    segments = [s for s in segments if (getattr(s, "text", "") or "").strip()]
    if not options.enabled:
        return "".join(s.text for s in segments).strip()

    kept = []
    for segment in segments:
        no_speech = _num(segment, "no_speech_prob", 0.0)
        logprob = _num(segment, "avg_logprob", 0.0)
        if (
            no_speech > options.no_speech_threshold
            and logprob < options.log_prob_threshold
        ):
            continue
        # 区間の中のループは先に畳み、畳んでもなお繰り返しが多い区間は落とす
        text = collapse_repetitions(
            segment.text, options.repeat_min_count, options.repeat_max_unit_chars
        )
        if compression_ratio(text.strip()) > options.compression_ratio_threshold:
            continue
        kept.append((segment, text))

    phrase_only = [is_hallucination_phrase(text, options.phrases) for _, text in kept]
    if all(phrase_only):
        # 転写全体が定型句だけ
        return ""

    result = []
    prev_end = 0.0
    for i, (segment, text) in enumerate(kept):
        start = _num(segment, "start", 0.0)
        end = _num(segment, "end", start)
        if phrase_only[i]:
            past_end = duration is not None and end > duration + PAST_END_MARGIN_SECONDS
            silent_tail = i == len(kept) - 1 and (
                start - prev_end >= options.silence_seconds
                or _num(segment, "no_speech_prob", 0.0) > options.no_speech_threshold
            )
            if past_end or silent_tail:
                continue
        result.append(text)
        prev_end = end

    return collapse_repetitions(
        "".join(result), options.repeat_min_count, options.repeat_max_unit_chars
    ).strip()
