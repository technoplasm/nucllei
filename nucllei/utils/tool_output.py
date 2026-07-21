"""tool 実行中の出力チャネル(sink)基盤(#89、#88 と共用)。

tool は戻り値(モデル向け作業データ)とは別に、実行中に「誰かに見せたい」出力を
push できる。誰の目に届くかでチャネルを分ける:

- `__feed_image__(file_id)` → results[i].model_images(モデル/VLM の目。画素を再注入)
- `__display__(content, kind)` → results[i].display(人間の目。collapse 外。#88 で追加予定)

いずれも「注入ヘルパ(tool-push)+ tool-call ごとの sink へ蓄積 + ブロック永続」で、
戻り値をハイジャックしない。emitter は揮発、永続はブロック(results[i])側が正
(リロード再現)。emitter は tool の実行文脈からのみ有効で、文脈外(pipe/filter 等)や
sink 未設定時は黙って no-op。

sync tool(#28 でワーカースレッド実行)からも呼ばれるため sink は lock 付き。
harvest は snapshot(lock 下 copy)で確定値だけを取り、生参照を results へ渡さない
(timeout orphan スレッドの遅延 append が deepcopy 中の list を壊すのを防ぐ)。
ContextVar は asyncio.to_thread が呼出時 copy_context するためワーカースレッドへ伝播する。
"""

import threading
from contextvars import ContextVar
from typing import Optional


class ToolOutputSink:
    """1 tool-call の実行中に貯まる出力チャネル(lock 付き)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model_images: list[str] = []

    def add_model_image(self, file_id: str) -> None:
        if not file_id or not isinstance(file_id, str):
            return
        with self._lock:
            self._model_images.append(file_id)

    def model_images(self) -> list[str]:
        with self._lock:
            return list(self._model_images)


# 現在実行中 tool-call の sink。execute_tool_calls が call ごとに set/reset する。
current_sink: ContextVar[Optional[ToolOutputSink]] = ContextVar(
    "llens_tool_output_sink", default=None
)


def feed_image(file_id: str) -> None:
    """`__feed_image__` 実体。実行中 tool-call の sink に画像 file_id を積む。

    どの tool でも(view_image に限らず)モデルの目に画素を流せる汎用経路。実体化
    (base64)は送信直前 inline に一任し、その段で has_read_access_to_file が
    呼び出しユーザーの read 権を確認する(この push 自体は認可しない)。
    tool 実行文脈外(sink 未設定)では no-op。
    """
    sink = current_sink.get()
    if sink is None:
        return
    sink.add_model_image(file_id)
