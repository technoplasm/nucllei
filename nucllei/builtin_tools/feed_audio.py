"""組み込みツール: file_id の音声、URL (url)、sandbox の絶対パス (path) を主モデル自身の耳に
同一ターンで入れる (feed_image の音声版)。File は発行しない。

**音声を聞けるモデル (`capabilities.audio`) にだけ出す** (`Tools.available`)。feed_image と違い、
音声を受けない上流に音声を送るとリクエストごと落ちる (staging: openrouter/kimi-k2.6 が 404
"No endpoints found that support input audio") ので、能力の無いモデルには tool ごと見せない。
`capabilities.audio` は既定 OFF。

仕組み (feed_image と同じ汎用 tool-push 経路): tool は検証して `__feed_audio__(参照)` を呼ぶだけ。
middleware が実行中 tool-call の sink から参照を harvest して results[i].context_audio
(list[str]、DB に永続) へ載せ、送信直前 (`utils/audio_input.inline_audio_refs`) で mp3 の base64 に
してモデルへ再投入する (履歴でも毎ターン — context_images と同じ位置)。base64 は戻り境界にも
DB スナップショットにも載らない。

引数 (feed_image と同じ意味 — **解釈は引数名だけで決める**。値の形で別の引数に読み替えない):
`url` = URL (`resolve_url`)、`path` = sandbox の fs の絶対パス (`resolve_path`)、`file_id` = 添付・
生成物 (所有者チェック。url の `/api/v1/files/{id}/content` も file として同じチェックで読む)。
解決規則・境界・サイズ上限は `utils/image_refs` の resolver が一箇所で持つ。

区間: `start` / `end` ("23:10" / "1:02:03" / 秒数)。参照に `#t=<開始秒>,<終了秒>` を付けて積む
(`utils/audio_refs.with_range`)。1 回の区間は `AUDIO_INPUT_MAX_MINUTES` (既定 120 分) まで —
長い録音は区間に分けて聞く。音声入力の通し録音 (`meta.dictation`) も聞ける (発言には自動で
入らないが、原本を聞き直す用途がある)。
"""

import asyncio
import logging
import os
from typing import Callable, Optional

from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.env import FETCH_MAX_BYTES
from nucllei.utils.audio_input import (
    audio_media_type,
    max_minutes_text,
    max_seconds,
    probe_duration,
)
from nucllei.utils.audio_refs import (
    file_audio_ref,
    format_time,
    is_audio_model,
    parse_time,
    with_range,
)
from nucllei.utils.file_limit import sandbox_file_limit_bytes
from nucllei.utils.image_refs import (
    REF_ARGS_RULE,
    LoadedRef,
    file_id_of_url,
    resolve_path,
    resolve_url,
)
from nucllei.utils.ref_errors import (
    RefErrorHints,
    explain_ref_error,
    file_id_error,
    path_error,
    url_error,
)
from nucllei.utils.tool_errors import (
    ToolCallError,
    ToolFailure,
    ToolOutcome,
    tool_in_turn,
)

log = logging.getLogger(__name__)

# resolver が理由を付けずに失敗したとき (通常起きない) の文面
_ERR_UNEXPLAINED = (
    "ERROR: {ref} を読めない (存在しない / 許可された場所の外 / 大きすぎる)。"
)


def _path_limit() -> int:
    """path= の上限: sandbox の上限と FETCH_MAX_BYTES (送信直前の読み直しの上限) の小さいほう
    (feed_image と同じ — 大きい方に合わせると、ここで通っても送信直前に読めない)。"""
    return min(sandbox_file_limit_bytes(), FETCH_MAX_BYTES)


def _cut_next(ref_hint: str) -> str:
    """大きすぎる・長すぎる音声の次の手: sandbox で区間を切り出して path= で渡す (このターンに
    run_shell があるときだけ)。"""
    if not tool_in_turn("run_shell"):
        return ""
    return (
        f"sandbox にあるなら run_shell の ffmpeg で要る区間だけを切り出し (例: ffmpeg -ss 0 -t 600"
        f" -i {ref_hint} -ac 1 -b:a 32k /workspace/part.mp3)、その path を feed_audio(path=…) に渡す。"
    )


def _too_large(ref: str, err) -> str:
    limit = f" ({err.limit / 1024 / 1024:.1f} MB)" if err.limit else ""
    nxt = _cut_next(ref if ref.startswith("/") else "取り込んだパス")
    return f"ERROR: {ref} は取得の上限{limit}を超えている。このままでは聞けない。" + (
        f"次の手: {nxt}" if nxt else ""
    )


def _hints() -> RefErrorHints:
    return RefErrorHints(too_large=_too_large, unexplained=_ERR_UNEXPLAINED)


def _read_raw(rec) -> Optional[bytes]:
    path = getattr(rec, "path", None)
    if not path:
        return None
    try:
        with open(Storage.get_file(path), "rb") as f:
            return f.read()
    except Exception as e:
        log.error(f"[feed_audio] raw read failed: {e!r}")
        return None


def _parse_bound(name: str, value) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return parse_time(value)
    except ValueError:
        raise ToolCallError(
            f"{name} {value!r} を時刻として読めない",
            f'"23:10" (分:秒) / "1:02:03" (時:分:秒) / 秒数の形で {name} を渡し直す。',
        )


class Tools:
    def available(self, request, user, model, metadata) -> bool:
        # 音声を聞けるモデルにだけ出す (module docstring)
        return is_audio_model(model)

    async def feed_audio(
        self,
        url: Optional[str] = None,
        path: Optional[str] = None,
        file_id: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        __user__: Optional[dict] = None,
        __feed_audio__: Optional[Callable[[str], None]] = None,
    ) -> str:
        """音声 (録音・音声ファイル・動画の音声) を自分の耳に入れ、実際に聞いて判断する (話の中身・話者・言い方・音)。入れた音声は以降のターンにも残るので、長い録音は start / end で要る区間に絞る。1 回で聞ける長さには上限がある。ユーザーには再生されない。
        対象は url / path / file_id のどれか 1 つ。上限以内の添付の音声 (音声入力の録音を除く) は既に聞こえている。

        :param url: スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・`/api/v1/files/{id}/content`。受け取った形のまま
        :param path: sandbox のファイルの絶対パス (`/workspace/…` `/ext/…`)
        :param file_id: 添付・生成物の file_id
        :param start: 聞き始めの位置 ("23:10" / "1:02:03" / 秒数)。省くと先頭から
        :param end: 聞き終わりの位置 (同じ形)。省くと最後まで
        """
        if not __user__ or not __user__.get("id"):
            raise ToolFailure("ユーザーを特定できない")
        if __feed_audio__ is None:
            raise ToolFailure("この呼び出しでは音声を耳に入れる経路が使えない")

        u = url.strip() if isinstance(url, str) else ""
        p = path.strip() if isinstance(path, str) else ""
        fid = file_id.strip() if isinstance(file_id, str) else ""
        given = [
            n for n, v in (("url", url), ("path", path), ("file_id", file_id)) if v
        ]
        if len(given) > 1:
            raise ToolCallError(
                f"{' / '.join(given)} は同時に指定できない",
                f"1 回の呼び出しでどれか 1 つだけを渡す。{REF_ARGS_RULE}",
            )
        if not given:
            if url is not None:
                raise url_error("")
            if path is not None:
                raise path_error("")
            raise ToolCallError(
                "対象が無い (url / path / file_id のどれも無い)",
                "添付・生成物なら file_id を、URL なら url を、sandbox のファイルなら path を指定する。",
            )

        begin = _parse_bound("start", start)
        finish = _parse_bound("end", end)
        if begin is not None and finish is not None and finish <= begin:
            raise ToolCallError(
                f"end ({format_time(finish)}) が start ({format_time(begin)}) より前か同じ",
                "end を start より後ろにして呼び直す。",
            )

        user_id = __user__["id"]
        if fid or (u and file_id_of_url(u)):
            # このサーバのファイル配信口 (url) は file として同じ所有者チェックで読む
            target = fid or file_id_of_url(u)
            loaded = await self._load_file(target, user_id, given=u or None)
            ref = file_audio_ref(target)
            label = loaded.name or target
        elif u:
            loaded, err = await resolve_url(u, user=__user__)
            if err is not None or loaded is None:
                raise explain_ref_error(u, err, arg="url", hints=_hints())
            ref = label = u
        else:
            loaded, err = await resolve_path(p, __user__, max_bytes=_path_limit())
            if err is not None or loaded is None:
                raise explain_ref_error(p, err, arg="path", hints=_hints())
            ref = label = p

        if audio_media_type(loaded) is None:
            raise ToolCallError(
                f"{label} は音声ではない (content-type={loaded.content_type or '不明'})",
                (
                    "画像・PDF なら feed_image で見る。"
                    if tool_in_turn("feed_image")
                    else "音声のファイルを渡す。"
                ),
            )
        duration = await probe_duration(loaded.data)
        if duration is None:
            raise ToolFailure(
                f"{label} は音声として読めない (壊れているか、音声の入っていない形式)"
            )

        lo = begin or 0.0
        hi = duration if finish is None else min(finish, duration)
        if lo >= duration:
            raise ToolOutcome(
                f"{label} は全 {format_time(duration)} で、start {format_time(lo)} は範囲外",
                f"0:00〜{format_time(duration)} の範囲で start を渡し直す。",
            )
        if hi - lo > max_seconds():
            example_end = format_time(lo + max_seconds())
            raise ToolCallError(
                f"{label} の {format_time(lo)}〜{format_time(hi)} は {format_time(hi - lo)} あり、"
                f"1 回で聞ける上限 {max_minutes_text()} を超える (全 {format_time(duration)})",
                f'{max_minutes_text()}以内の区間に分けて呼ぶ (例: start="{format_time(lo)}",'
                f' end="{example_end}")。',
            )

        whole = lo <= 0 and hi >= duration
        __feed_audio__(ref if whole else with_range(ref, lo or None, hi))
        span = (
            f"全体 ({format_time(duration)})"
            if whole
            else f"{format_time(lo)}〜{format_time(hi)} (全 {format_time(duration)})"
        )
        return f"- {label} の {span} を自分の耳に入れた (ユーザーには再生されない)。"

    async def _load_file(
        self, fid: str, user_id: str, *, given: Optional[str] = None
    ) -> LoadedRef:
        """file (添付・生成物) を所有者チェックの上で読む。無い・他人の file は同じ固定文
        (存在の有無を漏らさない — feed_image と同じ)。"""
        rec = await asyncio.to_thread(Files.get_file_by_id, fid)
        if not rec or getattr(rec, "user_id", None) != user_id:
            raise file_id_error(fid, given=given)
        raw = await asyncio.to_thread(_read_raw, rec)
        if raw is None:
            raise ToolFailure(f"{rec.filename or fid} の実体を読めない")
        meta = getattr(rec, "meta", None) or {}
        name = getattr(rec, "filename", None) or fid
        return LoadedRef(
            data=raw,
            content_type=meta.get("content_type") or "",
            name=os.path.basename(name),
        )
