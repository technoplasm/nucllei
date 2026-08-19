"""completion タスクのイベント配信(wire v2 — socket.io 撤去の芯)。

チャット応答生成はバックグラウンドタスク(nucllei/tasks.py)で走り続け、
クライアントはこのモジュールの購読ストリーム(SSE)で観測する:

- POST /api/chat/completions の応答 SSE = タスク開始時からの購読
- GET  /api/tasks/{task_id}/events    = 再アタッチ(スナップショット先行 + 以降ライブ)

タスクは購読者の有無と独立に完走する(切断 = 購読解除であり cancel ではない)。
リロード/途中参加は「最新スナップショットを 1 発 → 以降のライブイベント」で収束する
ため、バックログの再生は持たない。

イベント封筒は旧 socket 'chat-events' と同形:
    {"chat_id": ..., "message_id": ..., "data": {"type": ..., "data": ...}}
"""

import asyncio
import logging
from uuid import uuid4

from nucllei.models.chats import Chats
from nucllei.utils.executions import in_exec
from nucllei.utils.sse import sse_line

log = logging.getLogger(__name__)

# タスク終端の番兵(購読キューへ流して SSE ジェネレータを閉じさせる)
_CLOSE = object()


class CompletionEventStream:
    """1 つの completion タスクスコープのイベントバス。"""

    def __init__(self, chat_id=None, message_id=None):
        self.chat_id = chat_id
        self.message_id = message_id
        self._subscribers: set[asyncio.Queue] = set()
        self._closed = False
        # 再アタッチ用: 最後に emit した chat:completion スナップショット
        # (content_blocks を含むもののみ保持。デルタでは更新しない)
        self.latest_snapshot: dict | None = None
        # 再アタッチ用: 実行中 tool の最新進捗(#100)。tool_call_id → 封筒。
        # **DB には書かない** — 進捗は揮発で、タスクの生存期間だけ持つ。
        # 結果が出た tool の分はスナップショット到着時に捨てる。
        self.latest_progress: dict[str, dict] = {}
        # 再アタッチ用: ストリーミング中の最新累積 usage(#102)。message_id → 封筒
        # (最新 1 件)。**DB には書かない** — 揮発で、最終真値は chat:completion
        # {usage} が別途届く。done=True の chat:completion が流れたら該当 message の
        # 分を捨てる(終わった生成の「途中値」を再アタッチに復元しない)。
        self.latest_usage: dict = {}
        # __event_call__(tool → UI の双方向呼び出し)の応答待ち
        self._pending_calls: dict[str, asyncio.Future] = {}

    # --- 配信 -----------------------------------------------------------

    def emit(self, event: dict):
        """封筒済みイベントを全購読者へ配る(closed 後は無視)。

        ワーカースレッドから直接呼んではならない(購読者集合を触る)。sync tool の
        進捗は `loop.call_soon_threadsafe(stream.emit, ...)` でループ側へ渡る。
        """
        if self._closed:
            return
        data = event.get("data") or {}
        event_type = data.get("type")
        payload = data.get("data") or {}

        if event_type == "chat:completion":
            if "content_blocks" in payload:
                self.latest_snapshot = payload
                self._drop_finished_progress(payload)
            if payload.get("done"):
                # #102: 生成が終わった message のライブ usage は破棄する
                # (最終真値は chat:completion {usage} 側が持つ)
                self.latest_usage.pop(event.get("message_id"), None)
        elif event_type == "tool:progress":
            tool_call_id = payload.get("tool_call_id")
            if tool_call_id is not None:
                self.latest_progress[tool_call_id] = event
        elif event_type == "chat:usage":
            self.latest_usage[event.get("message_id")] = event

        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def _drop_finished_progress(self, snapshot: dict):
        """結果が出た tool の進捗を捨てる(#100)。

        完了の権威は results の到着であって progress の done 宣言ではない。
        ここで捨てておくと、再アタッチしたクライアントに終わった tool の
        「実行中」が復元されることがない。
        """
        if not self.latest_progress:
            return
        for block in snapshot.get("content_blocks") or []:
            if block.get("type") != "tool_calls":
                continue
            for result in block.get("results") or []:
                self.latest_progress.pop(result.get("tool_call_id"), None)

    def close(self):
        if self._closed:
            return
        self._closed = True
        for queue in list(self._subscribers):
            queue.put_nowait(_CLOSE)
        for future in self._pending_calls.values():
            if not future.done():
                # llens(#110 §6): pending は cancel ではなく set_exception で起こす。
                # cancel だと in-flight の待ち手(tool)が CancelledError を受け、
                # 「停止された」意味論(exec runner の D2 / 親ターン stop)と混ざる。
                # 例外なら呼び手の失敗として扱われ、failed に落ちる。
                future.set_exception(
                    RuntimeError("completion event stream closed before reply")
                )
                # 誰も await しない場合の "exception was never retrieved" 警告抑止
                future.exception()
        self._pending_calls.clear()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue()
        if self._closed:
            queue.put_nowait(_CLOSE)
        else:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        self._subscribers.discard(queue)

    # --- 双方向呼び出し(__event_call__)---------------------------------

    async def call(self, event_data: dict):
        """イベントを配ってクライアントの応答を待つ(POST reply で解決)。

        llens(#110 §6): closed 後は誰も応答を返せない(emit は no-op で、
        reply エンドポイントが叩く相手も居ない)ため、future を作らず即エラーに
        する。ターン終了後も生きる exec(非同期 tool 実行)が親ターンの
        `__event_call__` を握ったまま呼ぶと、待ちが timeout まで hang するため。
        """
        if self._closed:
            raise RuntimeError("completion event stream is closed; cannot call client")

        call_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = future
        try:
            self.emit(
                {
                    "chat_id": self.chat_id,
                    "message_id": self.message_id,
                    "call_id": call_id,
                    "data": event_data,
                }
            )
            return await future
        finally:
            self._pending_calls.pop(call_id, None)

    def resolve_call(self, call_id: str, result) -> bool:
        future = self._pending_calls.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(result)
        return True


# --- レジストリ(task_id → stream)---------------------------------------

_streams: dict[str, CompletionEventStream] = {}


def register_stream(task_id: str, stream: CompletionEventStream):
    _streams[task_id] = stream


def release_stream(task_id: str):
    stream = _streams.pop(task_id, None)
    if stream is not None:
        stream.close()


def get_stream(task_id: str) -> CompletionEventStream | None:
    return _streams.get(task_id)


def get_stream_for_message(chat_id, message_id) -> CompletionEventStream | None:
    """chat_id / message_id からライブな stream を逆引きする。

    pipe 実行(nucllei/functions.py)のように手元に task_id が無い経路向け。
    走行中タスク数の線形走査で足りる規模のため専用インデックスは持たない。
    見つからなければ None(emitter/caller 無しで走らせる)。
    """
    if not (chat_id and message_id):
        return None
    for stream in _streams.values():
        if (
            not stream.closed
            and stream.chat_id == chat_id
            and stream.message_id == message_id
        ):
            return stream
    return None


# --- emitter / caller(tool・filter へ渡す関数)---------------------------


def make_event_emitter(stream: CompletionEventStream, metadata: dict, update_db=True):
    """__event_emitter__ 実体。イベント配信 + 種別ごとの DB 副作用(旧 socket 実装踏襲)。

    llens(#110 §6): callable には mount 時に partial で焼き込まれる(utils/tools.py)
    ため、非同期 tool 実行(execution)はターン終了後もこの emitter を握ったまま
    走り続ける。DB 副作用(親 assistant メッセージの変異)は exec 文脈では全面
    スキップする — 親メッセージの tool result は spawn 時の running 形のみで以後
    不変(append-only)であり、ターン中の chat JSON RMW とも競合するため。
    """

    chat_id = metadata.get("chat_id")
    message_id = metadata.get("message_id")

    async def __event_emitter__(event_data):
        stream.emit(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "data": event_data,
            }
        )

        if not (update_db and chat_id and message_id):
            return
        if in_exec.get():
            # exec runner 内(ターン寿命を超えた実行)からの DB 分岐は無効
            log.debug(
                "skipped event_emitter DB write from async execution: %s",
                (event_data or {}).get("type"),
            )
            return
        if stream.closed:
            # ターンが畳まれた後の遅延 emit(timeout orphan スレッド等)。
            # 配信先が居ない以上、DB だけ変異させるのは append-only 側の害
            log.debug(
                "skipped event_emitter DB write on closed stream: %s",
                (event_data or {}).get("type"),
            )
            return

        event_type = (event_data or {}).get("type")
        if event_type == "message":

            def _append_content():
                message = Chats.get_message_by_id_and_message_id(chat_id, message_id)
                if message:
                    content = message.get("content", "")
                    content += event_data.get("data", {}).get("content", "")
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        chat_id, message_id, {"content": content}
                    )

            await asyncio.to_thread(_append_content)
        elif event_type == "replace":
            await asyncio.to_thread(
                Chats.upsert_message_to_chat_by_id_and_message_id,
                chat_id,
                message_id,
                {"content": event_data.get("data", {}).get("content", "")},
            )

    return __event_emitter__


def make_event_call(stream: CompletionEventStream):
    """__event_call__ 実体。SSE でイベントを配り、reply エンドポイントの応答を待つ。"""

    async def __event_caller__(event_data):
        return await stream.call(event_data)

    return __event_caller__


async def sse_event_generator(stream: CompletionEventStream, queue, first_events=()):
    """購読キューを SSE 行として流すジェネレータ。

    queue は呼び出し側が subscribe() 済みのものを渡す(タスク起動より先に
    購読を確立し、初期イベントの取りこぼしを防ぐため)。
    切断(ジェネレータ cancel)では購読解除のみ行い、タスクには触れない。
    """
    try:
        for event in first_events:
            yield sse_line(event)
        while True:
            event = await queue.get()
            if event is _CLOSE:
                break
            yield sse_line(event)
    finally:
        stream.unsubscribe(queue)
