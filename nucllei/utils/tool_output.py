"""tool 実行中の出力チャネル(sink)基盤(#89、#88 と共用)。

tool は戻り値(モデル向け作業データ)とは別に、実行中に「誰かに見せたい」出力を
push できる。誰の目に届くかでチャネルを分ける:

- `__feed_image__(file_id)` → results[i].context_images(モデル/VLM の目。画素を再注入)
- `__display__(content, kind)` → results[i].display(人間の目。collapse 外。モデル非注入。#88)

いずれも「注入ヘルパ(tool-push)+ tool-call ごとの sink へ蓄積 + ブロック永続」で、
戻り値をハイジャックしない。emitter は揮発、永続はブロック(results[i])側が正
(リロード再現)。emitter は tool の実行文脈からのみ有効で、文脈外(pipe/filter 等)や
sink 未設定時は黙って no-op。

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

import threading
from contextvars import ContextVar
from typing import Optional


class ToolOutputSink:
    """1 tool-call の実行中に貯まる出力チャネル(lock 付き)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._context_images: list[str] = []
        self._displays: list[dict] = []

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


# 現在実行中 tool-call の sink。execute_tool_calls が call ごとに set/reset する。
current_sink: ContextVar[Optional[ToolOutputSink]] = ContextVar(
    "llens_tool_output_sink", default=None
)


def feed_image(file_id: str) -> None:
    """`__feed_image__` 実体。実行中 tool-call の sink に画像参照を積む。

    どの tool でも(view_image に限らず)モデルの目に画素を流せる汎用経路。
    引数は file_id 文字列のほか、**"/ext/" 始まりの相対パス**("/ext/{name}/...";
    #97 上流プロキシの資料参照)もそのまま積める。sink は無検証の設計 —
    検証・実体化(base64)は送信直前 inline(utils/file_images.py)に一任する。
    file_id はその段で has_read_access_to_file が呼び出しユーザーの read 権を確認し、
    "/ext/" パスは EXT_UPSTREAMS(admin 管理)を真実源にサーバ側 fetch する
    (この push 自体は認可しない)。tool 実行文脈外(sink 未設定)では no-op。

    低レベルプリミティブ: content_type / サイズの検証はしない(file_id しか持たず、
    ここで Files 参照すると async tool 実行中にイベントループを塞ぐため #28 に反する)。
    **呼び出し側 tool が画像であること・サイズ上限を保証する**(view_image は
    image/* と max_image_mb を検証済み。"/ext/" パスは inline が image/* と
    EXT_INLINE_MAX_BYTES を検査して失敗を吸収する)。非画像 file_id を積むと
    モデルへ壊れた image_url が渡る — それは tool のバグ(認可は inline が別途担保する)。
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
