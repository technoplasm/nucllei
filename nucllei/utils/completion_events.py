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
        # __event_call__(tool → UI の双方向呼び出し)の応答待ち
        self._pending_calls: dict[str, asyncio.Future] = {}

    # --- 配信 -----------------------------------------------------------

    def emit(self, event: dict):
        """封筒済みイベントを全購読者へ配る(closed 後は無視)。"""
        if self._closed:
            return
        data = event.get("data") or {}
        if data.get("type") == "chat:completion" and "content_blocks" in (
            data.get("data") or {}
        ):
            self.latest_snapshot = data["data"]
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def close(self):
        if self._closed:
            return
        self._closed = True
        for queue in list(self._subscribers):
            queue.put_nowait(_CLOSE)
        for future in self._pending_calls.values():
            if not future.done():
                future.cancel()
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
        """イベントを配ってクライアントの応答を待つ(POST reply で解決)。"""
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
    """__event_emitter__ 実体。イベント配信 + 種別ごとの DB 副作用(旧 socket 実装踏襲)。"""

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

        event_type = (event_data or {}).get("type")
        if event_type == "status":
            await asyncio.to_thread(
                Chats.add_message_status_to_chat_by_id_and_message_id,
                chat_id,
                message_id,
                event_data.get("data", {}),
            )
        elif event_type == "message":

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
