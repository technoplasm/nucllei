"""tool 実行中の出力チャネル(sink)基盤(#89、#88 / #100 と共用)。

tool は戻り値(モデル向け作業データ)とは別に、実行中に「誰かに見せたい」出力を
push できる。誰の目に届くかでチャネルを分ける:

- `__feed_image__(file_id)` → results[i].context_images(モデル/VLM の目。画素を再注入)
- `__display__(content, kind)` → results[i].display(人間の目。collapse 外。モデル非注入。#88)
- `__progress__(description, ...)` → `tool:progress` イベント(人間の目。**揮発**。#100)

前 2 者は「注入ヘルパ(tool-push)+ tool-call ごとの sink へ蓄積 + ブロック永続」で、
戻り値をハイジャックしない。emitter は揮発、永続はブロック(results[i])側が正
(リロード再現)。emitter は tool の実行文脈からのみ有効で、文脈外(pipe/filter 等)や
sink 未設定時は黙って no-op。

`__progress__` だけは蓄積せず**その場で流して捨てる**(#100): 実行中の「今どうなって
いるか」は終わった瞬間に情報として死ぬため、DB に書かない。完了後に残るのは
results[i].duration(所要時間)で、進捗テキストは残さない。後から読み返してほしい
実行ログは `__display__`(永続・リロード再現)の担当。

sync tool(#28 でワーカースレッド実行)からも呼ばれるため sink は lock 付き。
harvest は snapshot(lock 下 copy)で確定値だけを取り、生参照を results へ渡さない
(timeout orphan スレッドの遅延 append が deepcopy 中の list を壊すのを防ぐ)。

伝播の前提と限界: sink は ContextVar で運ぶ。sync tool は `asyncio.to_thread` 経由で
実行され、to_thread は呼出時に copy_context するためワーカースレッドへ伝播する。
**この伝播は to_thread(および copy_context を通す経路)に限る** — tool が自前で
`threading.Thread` / 独自 executor / multiprocessing を起こし、そのワーカーから
emitter を呼ぶと ContextVar は継承されず current_sink.get() が None を返して
黙って no-op になる。emitter は必ず tool の実行文脈(to_thread までを含む)から呼ぶこと。
"""

import logging
import threading
import time
from contextvars import ContextVar
from typing import Callable, Optional

log = logging.getLogger(__name__)

# 進捗の最小送出間隔(秒)。tool がループ内で毎周呼んでも SSE を溢れさせない
# (tqdm の mininterval 相当)。抑制した最後の 1 通は close() で必ず流す。
PROGRESS_THROTTLE_S = 0.2


def _positive_number(value) -> Optional[float]:
    """進捗の分母として使える正の実数だけを通す(bool は数値として扱わない)。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return value


def _normalize_subagent(value) -> Optional[dict]:
    """進捗の構造化フィールド `subagent` を正規化する(#140 UI 追従)。

    `{"session_id": str, "task_id"?: str}` だけを通す(未知キー・非文字列は捨てる)。
    session_id が無ければ None(= 載せない)。
    """
    if not isinstance(value, dict):
        return None
    session_id = value.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    normalized = {"session_id": session_id}
    task_id = value.get("task_id")
    if isinstance(task_id, str) and task_id:
        normalized["task_id"] = task_id
    return normalized


def _clamp(value, total: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(value, total))


class ToolOutputSink:
    """1 tool-call の実行中に貯まる出力チャネル(lock 付き)。

    progress(#100)だけは貯めずに `emit_progress` へ即時に流す。`emit_progress` は
    **同期呼び出し可能**であること — sync tool はワーカースレッドから呼ぶため、
    実体は `loop.call_soon_threadsafe(stream.emit, envelope)` を包んだ closure になる
    (middleware が sink 生成時に注入する)。
    """

    def __init__(
        self,
        tool_call_id: str = "",
        emit_progress: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._context_images: list[str] = []
        self._displays: list[dict] = []
        self._result_render: Optional[dict] = None
        # 結果に付ける失敗の種別(#145 — mark_error_kind)。raise せずに返した結果を
        # 「失敗」として見せる口
        self._error_kind: Optional[str] = None
        # --- progress(#100、揮発)---
        self._tool_call_id = tool_call_id
        self._emit_progress = emit_progress
        self._closed = False
        # description 省略時に維持する直前の文言(tqdm の set_description 相当)
        self._description = ""
        # 子 agent session の所在(run_subagent — #140 UI 追従)。description と同じく
        # **粘着**: 一度載せたら以後の全 payload(終端・再アタッチ用の最新値)に載る
        self._subagent: Optional[dict] = None
        self._last_emit_at = 0.0
        self._pending: Optional[dict] = None

    def add_context_image(self, file_id: str) -> None:
        if not file_id or not isinstance(file_id, str):
            return
        with self._lock:
            self._context_images.append(file_id)

    def context_images(self) -> list[str]:
        with self._lock:
            return list(self._context_images)

    def add_display(self, content: str, kind: str = "markdown") -> None:
        # 空/非文字列の content は捨てる(見せる中身が無い push は no-op)。
        if not content or not isinstance(content, str):
            return
        # kind は将来の描画分岐用のヒント。未知/不正は markdown へ正規化する
        # (フロントは既定 markdown で描くため壊れない・前方互換)。
        if not isinstance(kind, str) or not kind:
            kind = "markdown"
        with self._lock:
            self._displays.append({"kind": kind, "content": content})

    def displays(self) -> list[dict]:
        # snapshot(lock 下 copy)。要素 dict は不変値のみで生参照は漏らさない。
        with self._lock:
            return [dict(d) for d in self._displays]

    # --- result render override(#110 §8)---------------------------------

    def set_result_render(self, render: dict) -> None:
        """この call の結果ブロックの表示ディスクリプタ(results[].render)を宣言する。

        await_task(#110)が exec 行に保存された実 tool の戻り値宣言
        (meta.render_descriptor)を持ち上げるための口。execute_tool_calls が
        成功時に harvest し、型由来の render(kind=json 等)より優先する。
        """
        if not isinstance(render, dict) or not render:
            return
        with self._lock:
            self._result_render = dict(render)

    def result_render(self) -> Optional[dict]:
        with self._lock:
            return dict(self._result_render) if self._result_render else None

    # --- 結果の失敗フラグ(#145)-------------------------------------------

    def mark_error_kind(self, kind: str) -> None:
        """返す結果に失敗の種別を付ける(raise しない — 本文はそのまま結果になる)。

        `violation` は middleware 専用なので受けない。未知の値は ValueError。
        """
        from nucllei.utils.tool_errors import TOOL_ERROR_KINDS

        if kind not in TOOL_ERROR_KINDS or kind == "violation":
            raise ValueError(f"unknown result error kind: {kind!r}")
        with self._lock:
            self._error_kind = kind

    def error_kind(self) -> Optional[str]:
        with self._lock:
            return self._error_kind

    # --- progress(#100、揮発)-------------------------------------------

    def mark_started(self) -> None:
        """tool-call の開始を 1 発通知する(フロントの経過時間タイマの起点)。

        `execute_tool_calls` は逐次実行なのに、ブロックは実行前の 1 回の emit で
        **一斉に**描かれる。これが無いと 2 本目以降のタイマが前の tool の待ち時間まで
        数えてしまう(確定時に巻き戻って見える)。

        スロットルの窓は動かさない — 直後に tool 自身が押す最初の description を
        巻き添えで落とさないため。
        """
        if self._emit_progress is None:
            return
        with self._lock:
            if self._closed:
                return
            payload = self._base_payload(done=False)
            emit = self._emit_progress
        self._safe_emit(emit, payload)

    def _base_payload(self, *, done: bool) -> dict:
        """lock 下で呼ぶ。tool_call_id / description / done と粘着フィールド。"""
        payload = {
            "tool_call_id": self._tool_call_id,
            "description": self._description,
            "done": done,
        }
        if self._subagent is not None:
            payload["subagent"] = dict(self._subagent)
        return payload

    @staticmethod
    def _safe_emit(emit, payload) -> None:
        """進捗は装飾 — 送出の失敗で tool 本体を巻き添えにしない。

        close() は finally から呼ばれるので、ここで例外を通すと元の例外を
        握り潰して tool ループごと落ちる。
        """
        try:
            emit(payload)
        except Exception:
            log.debug("progress emit failed", exc_info=True)

    def push_progress(
        self,
        description="",
        *,
        current=None,
        total=None,
        done: bool = False,
        subagent=None,
    ) -> None:
        """進捗 1 件。スロットルを掛けて `emit_progress` へ流す(蓄積しない)。

        正規化はここで済ませ、フロントに判断を残さない(型に忠実の原則):
        `total` が正の数でなければ不定扱いに落とし、`current` は [0, total] に
        クランプする。`description` が空なら直前の文言を維持する。
        """
        if self._emit_progress is None:
            return

        with self._lock:
            # close 後(= tool-call 終了後)の push は捨てる。timeout で待ちだけ外れた
            # orphan スレッド(middleware の有界リーク)が次の tool の進捗を汚さない。
            if self._closed:
                return

            if isinstance(description, str) and description:
                self._description = description

            # subagent は差分があるときだけ更新し、更新した 1 通はスロットルを
            # 素通しする(task_id の到着は FE の live 追従の起点 — 終端まで
            # 抑制されると意味が無い)。既存キーは保ったまま上書きマージ。
            subagent_changed = False
            normalized = _normalize_subagent(subagent)
            if normalized is not None:
                merged = {**(self._subagent or {}), **normalized}
                if merged != self._subagent:
                    self._subagent = merged
                    subagent_changed = True

            payload = self._base_payload(done=bool(done))
            total_n = _positive_number(total)
            current_n = None if total_n is None else _clamp(current, total_n)
            # 決定的(current/total)として成立するときだけ載せる。片方でも欠ければ
            # 不定 = キーごと出さない(フロントは total の有無だけを見ればよい)。
            if current_n is not None:
                payload["current"] = current_n
                payload["total"] = total_n

            now = time.monotonic()
            if (
                payload["done"]
                or subagent_changed
                or now - self._last_emit_at >= PROGRESS_THROTTLE_S
            ):
                self._last_emit_at = now
                self._pending = None
                emit, pending = self._emit_progress, payload
            else:
                # 抑制した最後の 1 通は close() の flush で必ず届ける
                # (leading-edge only だと末尾が落ちて古い値が残る)
                self._pending = payload
                emit, pending = None, None

        if emit is not None:
            self._safe_emit(emit, pending)

    def close(self) -> None:
        """tool-call の終了。以後の push を no-op にし、**終端を 1 通流す**。

        終端は `done=True`。フロントはこれで**経過時間の時計だけ**止める
        (スピナーと完了表示の権威は results の到着のまま)。

        これが要るのは results がラウンド単位で届くため: `execute_tool_calls` は
        全 call を回し切ってから results を返し、スナップショットの emit も 1 回
        しかない。個々の call の完了は wire に出ないので、終端が無いと**先に
        終わった tool の時計が後続を待つ間ずっと回り続け、確定時に巻き戻る**
        (A=2 秒 / B=60 秒なら A のヘッダが 62 秒まで数えてから 2 秒に落ちる)。
        開始側の巻き戻りを mark_started() で潰したのと対になる。

        抑制されていた最後の 1 通(description と数値)はここに畳んで載せる。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending, self._pending = self._pending, None
            emit = self._emit_progress
            if emit is None:
                return
            payload = self._base_payload(done=True)
            # スロットルで落ちた最後の数値も終端に持ち上げる(古い分数のまま
            # 止まって見えないように)
            if pending is not None and "current" in pending:
                payload["current"] = pending["current"]
                payload["total"] = pending["total"]

        self._safe_emit(emit, payload)


# 現在実行中 tool-call の sink。execute_tool_calls が call ごとに set/reset する。
current_sink: ContextVar[Optional[ToolOutputSink]] = ContextVar(
    "llens_tool_output_sink", default=None
)


def feed_image(file_id: str) -> None:
    """`__feed_image__` 実体。実行中 tool-call の sink に画像参照を積む。

    どの tool でも(view_image に限らず)モデルの目に画素を流せる汎用経路。
    引数は file_id 文字列のほか、**参照文字列**(スキーム付き URL
    `https://…/ext/{name}/...` / 同一サーバの "/ext/..."(sandbox のパスと同じ文字列)/
    一般の URL、または sandbox の fs パス "/workspace/..."、任意で末尾 "#page=N" — 解決規則は
    utils/image_refs.py、#97 / #139)もそのまま積める。sink は無検証の設計 —
    検証・実体化(base64、PDF はページ画像化)は送信直前 inline
    (utils/file_images.py)に一任する。file_id はその段で has_read_access_to_file が
    呼び出しユーザーの read 権を確認し、URL / fs 参照は image_refs が境界(`EXT_MOUNTS` =
    `/ext/` の配信元 / 呼び出しユーザーの workspace)を fail closed で持つ(この push 自体は
    認可しない)。tool 実行文脈外(sink 未設定)では no-op。

    低レベルプリミティブ: content_type / サイズの検証はしない(file_id しか持たず、
    ここで Files 参照すると async tool 実行中にイベントループを塞ぐため #28 に反する)。
    **呼び出し側 tool が画像であること・サイズ上限を保証する**(view_image は
    image/* と max_image_mb を検証済み。URL / fs 参照は inline が種別と
    FETCH_MAX_BYTES を検査して失敗を吸収する)。画像でも PDF でもないものを
    積んでも inline が "(image unavailable)" に置換するので壊れた image_url は渡らないが、
    モデルには何も見えない — 積む前に種別を見るのは tool の責務(認可は inline が別途担保する)。
    """
    sink = current_sink.get()
    if sink is None:
        return
    sink.add_context_image(file_id)


def display(content: str, kind: str = "markdown") -> None:
    """`__display__` 実体。実行中 tool-call の sink に人間向け表示を積む。

    `__feed_image__`(モデルの目)と対称の、**人間の目**へ向けた汎用 tool-push
    (#88)。戻り値(モデル向け作業データ)をハイジャックせず、tool が「本文とは別に
    ユーザーへ確実に見せたいもの」(音声プレイヤー・プロット画像・skill ロード通知等)
    を collapse 外へ出す。永続はブロック(results[i].display)側が正でリロード再現する。

    feed_image と違い content を直接持つので、ここで content/kind を安全に正規化できる
    (Files 参照は不要=イベントループを塞がない)。実際の描画は content を markdown として
    sanitize(フロント Markdown = DOMPurify)して行う — 本文と同じ信頼水準。

    **モデルへは再注入しない**(convert_content_blocks_to_messages が display を読まない)
    のが 2 チャネル分離の本体。tool は戻り値(content)で「表示済み。本文で復唱不要」と
    モデルへ伝える責務を持つ。tool 実行文脈外(sink 未設定)では no-op。
    """
    sink = current_sink.get()
    if sink is None:
        return
    sink.add_display(content, kind)


def progress(
    description: str = "",
    *,
    current=None,
    total=None,
    done: bool = False,
    subagent: Optional[dict] = None,
) -> None:
    """`__progress__` 実体。実行中 tool-call の進捗をユーザーへ流す(#100)。

    tool が実行中にユーザーへ伝えられる唯一の口。行き先はチャットの共用領域では
    なく、**その tool 自身のウィジェット(ToolCallBlock のヘッダ)** に閉じている
    ため、同時に走る他の tool や filter の表示を踏み潰さない。

    - `total` を渡すと決定的(current/total)、渡さなければ不定(ぐるぐる)
    - `description` を省略すると直前の文言を維持する(tqdm の set_description 相当。
      ループ内で `__progress__(current=i, total=n)` だけ呼べる)
    - **sync / async どちらの tool からも sync 関数として呼べる**(sync tool は
      `asyncio.to_thread` のワーカースレッドから呼ぶ。emit はイベントループへ
      `call_soon_threadsafe` で渡るので、どちらのスレッドからでも待ちが要らない)

    **揮発**: DB に書かない。タスク生存中はストリームが最新値を保持して再アタッチ
    (リロード・途中参加)に耐えるが、完了後は残らない — 残るのは所要時間
    (results[i].duration)。後から読み返してほしいものは `__display__` を使う。

    完了の権威は tool の戻り(results[i] の到着)であり、`done=True` の宣言では
    ない。撃たずに return しても、timeout / 例外で tool のコードを通らなくても、
    フロントは結果の到着で進捗を破棄する。

    進捗は装飾なので、ここでの失敗が tool 本体を巻き添えにすることはない
    (文脈外・sink 未設定・stream 未設定はいずれも黙って no-op)。

    `subagent={"session_id", "task_id"?}`(#140 UI 追従)は run_subagent が子
    agent session の所在を FE へ渡す構造化フィールド。**粘着**(以後の全 payload に
    載る)で、値が変わった 1 通はスロットルを素通しする。FE はこれで実行中の
    カードに子 transcript を live 追従で描く(README_API §5.5)。
    """
    sink = current_sink.get()
    if sink is None:
        return
    try:
        sink.push_progress(
            description,
            current=current,
            total=total,
            done=done,
            subagent=subagent,
        )
    except Exception:
        log.debug("progress push failed", exc_info=True)


def mark_error_kind(kind: str = "failure") -> None:
    """返す結果を「失敗」として見せる(#145、raise しない版)。

    `ToolError` を raise すると本文は ERROR 文だけになり、副産物(添付・display)も
    捨てられる。中身のある結果(実行した Python の stdout + traceback)を
    そのまま返しつつ、チャットのカードと wire の `results[i].error_kind`(background は
    exec 行の `meta.error_kind`、行は done のまま)に種別を出したいときに呼ぶ。
    本文・render・context_images・display は成功時と同じに扱われる。kind は
    `call` / `failure` / `outcome` / `fatal`(`violation` は middleware 専用)。
    tool 実行文脈外(sink 未設定)では no-op。
    """
    sink = current_sink.get()
    if sink is None:
        return
    sink.mark_error_kind(kind)


__all__ = [
    "PROGRESS_THROTTLE_S",
    "ToolOutputSink",
    "current_sink",
    "display",
    "feed_image",
    "mark_error_kind",
    "progress",
]
