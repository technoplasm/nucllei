import logging
import time
import uuid
from typing import Optional

from nucllei.internal.db import Base, get_db
from nucllei.env import SRC_LOG_LEVELS

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, String, Text, JSON
from sqlalchemy import cast, or_, func, text
from sqlalchemy.orm.attributes import flag_modified

####################
# Chat DB Schema
####################

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


class Chat(Base):
    __tablename__ = "chat"

    id = Column(String, primary_key=True)
    user_id = Column(String)
    title = Column(Text)
    chat = Column(JSON)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)

    share_id = Column(Text, unique=True, nullable=True)
    archived = Column(Boolean, default=False)
    pinned = Column(Boolean, default=False, nullable=True)

    meta = Column(JSON, server_default="{}")

    # llens(#31): soft delete。監査証憑の保全のため行は消さず、削除時刻(epoch 秒)を立てる。
    # ユーザー向けクエリは deleted_at IS NULL で除外し、admin 一覧(#19)では見える。
    deleted_at = Column(BigInteger, nullable=True)


class ChatModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    chat: dict

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch

    share_id: Optional[str] = None
    archived: bool = False
    pinned: Optional[bool] = False

    meta: dict = {}
    deleted_at: Optional[int] = None


####################
# Forms
####################


class ChatForm(BaseModel):
    chat: dict


class ChatImportForm(ChatForm):
    meta: Optional[dict] = {}
    pinned: Optional[bool] = False


class ChatTitleMessagesForm(BaseModel):
    title: str
    messages: list[dict]


class ChatTitleForm(BaseModel):
    title: str


class ChatResponse(BaseModel):
    id: str
    user_id: str
    title: str
    chat: dict
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch
    share_id: Optional[str] = None  # id of the chat to be shared
    archived: bool
    pinned: Optional[bool] = False
    meta: dict = {}
    # llens(#19): admin の直参照ビュー(/share/{id} の admin 分岐)が
    # 削除済み(soft delete)を明示表示するため露出する
    deleted_at: Optional[int] = None


class ChatTitleIdResponse(BaseModel):
    id: str
    title: str
    updated_at: int
    created_at: int


def _chat_snippet(chat_json: Optional[dict], limit: int = 120) -> str:
    """llens(#69): チャットプレビュー用に冒頭のユーザー発話を切り出す
    (タイトルだけで判別できない一覧の 2 行目)。"""
    if not chat_json:
        return ""
    for message in chat_json.get("messages") or []:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            content = " ".join(message["content"].split())
            if content:
                return content[:limit]
    return ""


class ChatTable:
    def insert_new_chat(self, user_id: str, form_data: ChatForm) -> Optional[ChatModel]:
        with get_db() as db:
            id = str(uuid.uuid4())
            chat = ChatModel(
                **{
                    "id": id,
                    "user_id": user_id,
                    "title": (
                        form_data.chat["title"]
                        if "title" in form_data.chat
                        else "New Chat"
                    ),
                    "chat": form_data.chat,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
            )

            result = Chat(**chat.model_dump())
            db.add(result)
            db.commit()
            db.refresh(result)
            return ChatModel.model_validate(result) if result else None

    def import_chat(
        self, user_id: str, form_data: ChatImportForm
    ) -> Optional[ChatModel]:
        with get_db() as db:
            id = str(uuid.uuid4())
            chat = ChatModel(
                **{
                    "id": id,
                    "user_id": user_id,
                    "title": (
                        form_data.chat["title"]
                        if "title" in form_data.chat
                        else "New Chat"
                    ),
                    "chat": form_data.chat,
                    "meta": form_data.meta,
                    "pinned": form_data.pinned,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
            )

            result = Chat(**chat.model_dump())
            db.add(result)
            db.commit()
            db.refresh(result)
            return ChatModel.model_validate(result) if result else None

    def update_chat_by_id(self, id: str, chat: dict) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat_item = db.get(Chat, id)
                chat_item.chat = chat
                chat_item.title = chat["title"] if "title" in chat else "New Chat"
                chat_item.updated_at = int(time.time())
                db.commit()
                db.refresh(chat_item)

                return ChatModel.model_validate(chat_item)
        except Exception:
            return None

    def update_chat_title_by_id(self, id: str, title: str) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        chat["title"] = title

        return self.update_chat_by_id(id, chat)

    def get_chat_title_by_id(self, id: str) -> Optional[str]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("title", "New Chat")

    def get_messages_by_chat_id(self, id: str) -> Optional[dict]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("history", {}).get("messages", {}) or {}

    def get_message_by_id_and_message_id(
        self, id: str, message_id: str
    ) -> Optional[dict]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        return chat.chat.get("history", {}).get("messages", {}).get(message_id, {})

    def upsert_message_to_chat_by_id_and_message_id(
        self, id: str, message_id: str, message: dict
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        history = chat.get("history", {})

        if message_id in history.get("messages", {}):
            history["messages"][message_id] = {
                **history["messages"][message_id],
                **message,
            }
        else:
            history["messages"][message_id] = message

        history["currentId"] = message_id

        chat["history"] = history
        return self.update_chat_by_id(id, chat)

    def record_usage_by_id_and_message_id(
        self,
        id: str,
        message_id: str,
        usage: dict,
        context_size: Optional[int] = None,
    ) -> Optional[dict]:
        """llens(#57): モデル応答の実測 usage(OpenAI 互換)を永続化する。

        tiktoken 等の推定ではなく、モデルサーバー(SGLang)が返す本物の
        usage を記録する。真のコンテキスト量 = 最新の prompt_tokens
        (system・ファイル注入・filter 加工後の、実際にモデルへ入った量)。

        - message.usage: このターンの実測。native tool ループはターン内に
          複数回モデルを呼ぶため completion_tokens は加算、prompt_tokens は
          最新値で上書き(最後の呼び出しの入力 = ターン終了時点の会話全体)。
        - meta["usage"]: チャット単位。prompt_tokens は最新、completion_tokens
          は累積(再生成・分岐込みでこの会話が生成した総量)。model の
          context_size(max_model_len)も添えて閲覧側(#19)が使用率を出せる。
        """
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        duration = usage.get("duration")
        if prompt_tokens is None and completion_tokens is None:
            return None

        def merge(target: dict) -> dict:
            merged = {**target}
            if prompt_tokens is not None:
                merged["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                merged["completion_tokens"] = (
                    merged.get("completion_tokens", 0) + completion_tokens
                )
            merged["total_tokens"] = merged.get("prompt_tokens", 0) + merged.get(
                "completion_tokens", 0
            )
            # 生成所要時間(#102): completion 同様に累積する。呼び出しごとの実測
            # (stream 消費の壁時計)なので、tool ループでも tool 実行時間は含まれない。
            # message 単位 = ターンの生成時間、meta = このチャットの総生成時間。
            if isinstance(duration, (int, float)):
                merged["duration"] = round(merged.get("duration", 0) + duration, 3)
            if context_size is not None:
                merged["context_size"] = context_size
            return merged

        try:
            with get_db() as db:
                chat_item = db.get(Chat, id)
                if chat_item is None:
                    return None

                # message 単位(存在しない message は作らない — usage だけの
                # 骸骨 message で history を汚さない)
                history = chat_item.chat.get("history", {})
                message = history.get("messages", {}).get(message_id)
                message_usage = None
                if message is not None:
                    message_usage = merge(message.get("usage") or {})
                    message["usage"] = message_usage
                    flag_modified(chat_item, "chat")

                # チャット単位(meta)
                meta = chat_item.meta or {}
                meta["usage"] = merge(meta.get("usage") or {})
                chat_item.meta = meta
                flag_modified(chat_item, "meta")

                chat_item.updated_at = int(time.time())
                db.commit()
                # マージ後の message.usage を返す(#102): 呼び手(middleware)は
                # これをそのまま emit する — FE の message.usage と DB が常に一致し、
                # tool ループでも「ターン累積」の completion / duration が画面に出る。
                return message_usage
        except Exception:
            log.exception(f"Failed to record usage for chat {id}")
            return None

    def add_message_status_to_chat_by_id_and_message_id(
        self, id: str, message_id: str, status: dict
    ) -> Optional[ChatModel]:
        chat = self.get_chat_by_id(id)
        if chat is None:
            return None

        chat = chat.chat
        history = chat.get("history", {})

        if message_id in history.get("messages", {}):
            status_history = history["messages"][message_id].get("statusHistory", [])
            status_history.append(status)
            history["messages"][message_id]["statusHistory"] = status_history

        chat["history"] = history
        return self.update_chat_by_id(id, chat)

    def insert_shared_chat_by_chat_id(self, chat_id: str) -> Optional[ChatModel]:
        with get_db() as db:
            # Get the existing chat to share
            chat = db.get(Chat, chat_id)
            # Check if the chat is already shared
            if chat.share_id:
                return self.get_chat_by_id_and_user_id(chat.share_id, "shared")
            # Create a new chat with the same data, but with a new ID
            shared_chat = ChatModel(
                **{
                    "id": str(uuid.uuid4()),
                    "user_id": f"shared-{chat_id}",
                    "title": chat.title,
                    "chat": chat.chat,
                    "created_at": chat.created_at,
                    "updated_at": int(time.time()),
                }
            )
            shared_result = Chat(**shared_chat.model_dump())
            db.add(shared_result)
            db.commit()
            db.refresh(shared_result)

            # Update the original chat with the share_id
            result = (
                db.query(Chat)
                .filter_by(id=chat_id)
                .update({"share_id": shared_chat.id})
            )
            db.commit()
            return shared_chat if (shared_result and result) else None

    def update_shared_chat_by_chat_id(self, chat_id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, chat_id)
                shared_chat = (
                    db.query(Chat).filter_by(user_id=f"shared-{chat_id}").first()
                )

                if shared_chat is None:
                    return self.insert_shared_chat_by_chat_id(chat_id)

                shared_chat.title = chat.title
                shared_chat.chat = chat.chat

                shared_chat.updated_at = int(time.time())
                db.commit()
                db.refresh(shared_chat)

                return ChatModel.model_validate(shared_chat)
        except Exception:
            return None

    def delete_shared_chat_by_chat_id(self, chat_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=f"shared-{chat_id}").delete()
                db.commit()

                return True
        except Exception:
            return False

    def update_chat_share_id_by_id(
        self, id: str, share_id: Optional[str]
    ) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.share_id = share_id
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def toggle_chat_pinned_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.pinned = not chat.pinned
                chat.updated_at = int(time.time())
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def toggle_chat_archive_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                chat.archived = not chat.archived
                chat.updated_at = int(time.time())
                db.commit()
                db.refresh(chat)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def archive_all_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(user_id=user_id).filter(
                    Chat.deleted_at == None
                ).update({"archived": True})
                db.commit()
                return True
        except Exception:
            return False

    def get_archived_chat_list_by_user_id(
        self, user_id: str, skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, archived=True)
                .filter(Chat.deleted_at == None)
                .order_by(Chat.updated_at.desc())
                # .limit(limit).offset(skip)
                .all()
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_list_by_user_id(
        self,
        user_id: str,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ChatModel]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id)
            query = query.filter(Chat.deleted_at == None)
            if not include_archived:
                query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc())

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            all_chats = query.all()
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_title_id_list_by_user_id(
        self,
        user_id: str,
        include_archived: bool = False,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[ChatTitleIdResponse]:
        with get_db() as db:
            query = db.query(Chat).filter_by(user_id=user_id)
            query = query.filter(or_(Chat.pinned == False, Chat.pinned == None))
            query = query.filter(Chat.deleted_at == None)

            if not include_archived:
                query = query.filter_by(archived=False)

            query = query.order_by(Chat.updated_at.desc()).with_entities(
                Chat.id, Chat.title, Chat.updated_at, Chat.created_at
            )

            if skip:
                query = query.offset(skip)
            if limit:
                query = query.limit(limit)

            all_chats = query.all()

            # result has to be destrctured from sqlalchemy `row` and mapped to a dict since the `ChatModel`is not the returned dataclass.
            return [
                ChatTitleIdResponse.model_validate(
                    {
                        "id": chat[0],
                        "title": chat[1],
                        "updated_at": chat[2],
                        "created_at": chat[3],
                    }
                )
                for chat in all_chats
            ]

    def get_chat_list_by_chat_ids(
        self, chat_ids: list[str], skip: int = 0, limit: int = 50
    ) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter(Chat.id.in_(chat_ids))
                .filter_by(archived=False)
                .filter(Chat.deleted_at == None)
                .order_by(Chat.updated_at.desc())
                .all()
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_by_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = db.get(Chat, id)
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def get_chat_by_share_id(self, id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                # it is possible that the shared link was deleted. hence,
                # we check if the chat is still shared by checking if a chat with the share_id exists
                chat = db.query(Chat).filter_by(share_id=id).first()

                if chat:
                    return self.get_chat_by_id(id)
                else:
                    return None
        except Exception:
            return None

    def get_chat_by_id_and_user_id(self, id: str, user_id: str) -> Optional[ChatModel]:
        try:
            with get_db() as db:
                chat = (
                    db.query(Chat)
                    .filter_by(id=id, user_id=user_id)
                    .filter(Chat.deleted_at == None)
                    .first()
                )
                return ChatModel.model_validate(chat)
        except Exception:
            return None

    def get_chats(self, skip: int = 0, limit: int = 50) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                # .limit(limit).offset(skip)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id)
                .filter(Chat.deleted_at == None)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_pinned_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, pinned=True, archived=False)
                .filter(Chat.deleted_at == None)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_archived_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, archived=True)
                .filter(Chat.deleted_at == None)
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chats_by_user_id_and_search_text(
        self,
        user_id: str,
        search_text: str,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 60,
    ) -> list[ChatModel]:
        """
        Filters chats based on a search query using Python, allowing pagination using skip and limit.
        """
        search_text = search_text.lower().strip()

        if not search_text:
            return self.get_chat_list_by_user_id(user_id, include_archived, skip, limit)

        with get_db() as db:
            query = db.query(Chat).filter(Chat.user_id == user_id)
            query = query.filter(Chat.deleted_at == None)

            if not include_archived:
                query = query.filter(Chat.archived == False)

            query = query.order_by(Chat.updated_at.desc())

            # Check if the database dialect is either 'sqlite' or 'postgresql'
            dialect_name = db.bind.dialect.name
            if dialect_name == "sqlite":
                # SQLite case: using JSON1 extension for JSON searching
                query = query.filter(
                    (
                        Chat.title.ilike(
                            f"%{search_text}%"
                        )  # Case-insensitive search in title
                        | text(
                            """
                            EXISTS (
                                SELECT 1 
                                FROM json_each(Chat.chat, '$.messages') AS message 
                                WHERE LOWER(message.value->>'content') LIKE '%' || :search_text || '%'
                            )
                            """
                        )
                    ).params(search_text=search_text)
                )

            elif dialect_name == "postgresql":
                # PostgreSQL relies on proper JSON query for search
                query = query.filter(
                    (
                        Chat.title.ilike(
                            f"%{search_text}%"
                        )  # Case-insensitive search in title
                        | text(
                            """
                            EXISTS (
                                SELECT 1
                                FROM json_array_elements(Chat.chat->'messages') AS message
                                WHERE LOWER(message->>'content') LIKE '%' || :search_text || '%'
                            )
                            """
                        )
                    ).params(search_text=search_text)
                )
            else:
                raise NotImplementedError(
                    f"Unsupported dialect: {db.bind.dialect.name}"
                )

            # Perform pagination at the SQL level
            all_chats = query.offset(skip).limit(limit).all()

            log.info(f"The number of chats: {len(all_chats)}")

            # Validate and return chats
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_chat_previews(
        self,
        user_id: Optional[str] = None,
        file_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        """llens(#69): チャットの軽量プレビュー一覧
        (id / user_id / user_name / title / snippet / updated_at)。

        ファイル管理の「関連チャット」と admin のユーザーチャット一覧が同じ形を消費する
        (見た目の共通部品 = ChatsListModal)。user_id はそのユーザーに限定(None = 全ユーザー
        — admin 用。他人のチャットは /s/{id} で開くため user_id を見て FE が振り分ける)。
        file_id は chat JSON 全文への LIKE で参照検出 — id は UUID なので本文との誤爆は
        実質なく、message.files / chat.files / 本文中の生成物 URL のどの持ち方でも拾える。
        """
        # 循環 import 回避のため遅延 import(access_control.py と同じ流儀)
        from nucllei.models.users import User

        with get_db() as db:
            query = db.query(Chat, User.name.label("user_name")).outerjoin(
                User, User.id == Chat.user_id
            )
            if user_id is not None:
                query = query.filter(Chat.user_id == user_id)
            if file_id is not None:
                query = query.filter(cast(Chat.chat, Text).like(f"%{file_id}%"))
            rows = (
                query.order_by(Chat.updated_at.desc()).offset(skip).limit(limit).all()
            )
            return [
                {
                    "id": chat.id,
                    "user_id": chat.user_id,
                    "user_name": user_name,
                    "title": chat.title,
                    "snippet": _chat_snippet(chat.chat),
                    "created_at": chat.created_at,
                    "updated_at": chat.updated_at,
                    "deleted_at": chat.deleted_at,
                }
                for chat, user_name in rows
            ]

    def get_chats_page_admin(
        self,
        user_id: Optional[str] = None,
        title: Optional[str] = None,
        model: Optional[str] = None,
        updated_from: Optional[int] = None,
        updated_to: Optional[int] = None,
        deleted: str = "include",
        shared: str = "include",
        order_by: str = "updated_at",
        direction: str = "desc",
        limit: int = 60,
        offset: int = 0,
    ) -> tuple[list[ChatModel], int]:
        """llens(#19): admin チャット閲覧ビュー用のページング一覧。

        order/limit/offset は SQL 側で効かせる(全件ロードして python で切らない)。
        user_id=None は全ユーザー。model は chat JSON トップレベルの models 配列への
        メンバーシップ(dialect 別 JSON クエリ — search_text と同じ流儀)。
        updated_from/updated_to は updated_at の範囲(epoch 秒・両端含む)。
        deleted は soft delete 済み(#31)の扱い: include(既定)/ exclude / only。
        shared は共有状態(share_id の有無 — #19)の扱い: include(既定)/ exclude / only。
        戻り値は (items, total)。total は filter 適用後・ページング前の件数。
        """
        with get_db() as db:
            query = db.query(Chat)
            if user_id is not None:
                query = query.filter(Chat.user_id == user_id)
            if deleted == "exclude":
                query = query.filter(Chat.deleted_at == None)
            elif deleted == "only":
                query = query.filter(Chat.deleted_at != None)
            if shared == "exclude":
                query = query.filter(Chat.share_id == None)
            elif shared == "only":
                query = query.filter(Chat.share_id != None)
            if title:
                query = query.filter(Chat.title.ilike(f"%{title}%"))
            if updated_from is not None:
                query = query.filter(Chat.updated_at >= updated_from)
            if updated_to is not None:
                query = query.filter(Chat.updated_at <= updated_to)
            if model:
                dialect_name = db.bind.dialect.name
                if dialect_name == "sqlite":
                    query = query.filter(
                        text(
                            """
                            EXISTS (
                                SELECT 1
                                FROM json_each(Chat.chat, '$.models') AS m
                                WHERE m.value = :model
                            )
                            """
                        ).params(model=model)
                    )
                elif dialect_name == "postgresql":
                    query = query.filter(
                        text(
                            """
                            EXISTS (
                                SELECT 1
                                FROM json_array_elements_text(Chat.chat->'models') AS m(value)
                                WHERE m.value = :model
                            )
                            """
                        ).params(model=model)
                    )
                else:
                    raise NotImplementedError(f"Unsupported dialect: {dialect_name}")

            total = query.count()

            order_column = (
                Chat.created_at if order_by == "created_at" else Chat.updated_at
            )
            query = query.order_by(
                order_column.asc() if direction == "asc" else order_column.desc()
            )
            chats = query.offset(offset).limit(limit).all()
            return [ChatModel.model_validate(chat) for chat in chats], total

    def get_chat_owners(self) -> list[dict]:
        """llens(#19): チャットを 1 件以上持つユーザーの一覧(admin のフィルタ UI 用)。"""
        # 循環 import 回避のため遅延 import(files.get_uploaders と同じ流儀)
        from nucllei.models.users import User

        with get_db() as db:
            rows = (
                db.query(
                    User.id,
                    User.name,
                    User.email,
                    func.count(Chat.id).label("chat_count"),
                )
                .join(Chat, Chat.user_id == User.id)
                .group_by(User.id, User.name, User.email)
                .order_by(func.count(Chat.id).desc())
                .all()
            )
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "email": row.email,
                    "chat_count": row.chat_count,
                }
                for row in rows
            ]

    def get_chat_models(self) -> list[dict]:
        """llens(#19): チャットで使われている model id と件数(admin のフィルタ UI 用)。

        chat JSON トップレベルの models 配列を dialect 別 JSON クエリで展開して集計。
        """
        with get_db() as db:
            dialect_name = db.bind.dialect.name
            if dialect_name == "sqlite":
                rows = db.execute(
                    text(
                        """
                        SELECT m.value AS model, COUNT(*) AS chat_count
                        FROM chat, json_each(chat.chat, '$.models') AS m
                        GROUP BY m.value
                        ORDER BY chat_count DESC, model ASC
                        """
                    )
                ).all()
            elif dialect_name == "postgresql":
                rows = db.execute(
                    text(
                        """
                        SELECT m.value AS model, COUNT(*) AS chat_count
                        FROM chat, json_array_elements_text(chat.chat->'models') AS m(value)
                        GROUP BY m.value
                        ORDER BY chat_count DESC, model ASC
                        """
                    )
                ).all()
            else:
                raise NotImplementedError(f"Unsupported dialect: {dialect_name}")
            return [{"model": row.model, "chat_count": row.chat_count} for row in rows]

    # llens(#31): 削除系はすべて soft delete(deleted_at を立てて非表示化 + 共有解除)。
    # 監査証憑の保全のため一次データの行は消さない。share コピー(user_id="shared-*" の
    # 複製行)は派生データなので従来どおり物理削除する。

    def delete_chat_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(id=id).update(
                    {"deleted_at": int(time.time()), "share_id": None}
                )
                db.commit()

                return True and self.delete_shared_chat_by_chat_id(id)
        except Exception:
            return False

    def delete_chat_by_id_and_user_id(self, id: str, user_id: str) -> bool:
        try:
            with get_db() as db:
                db.query(Chat).filter_by(id=id, user_id=user_id).update(
                    {"deleted_at": int(time.time()), "share_id": None}
                )
                db.commit()

                return True and self.delete_shared_chat_by_chat_id(id)
        except Exception:
            return False

    def delete_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                self.delete_shared_chats_by_user_id(user_id)

                db.query(Chat).filter_by(user_id=user_id).update(
                    {"deleted_at": int(time.time()), "share_id": None}
                )
                db.commit()

                return True
        except Exception:
            return False

    def delete_shared_chats_by_user_id(self, user_id: str) -> bool:
        try:
            with get_db() as db:
                chats_by_user = db.query(Chat).filter_by(user_id=user_id).all()
                shared_chat_ids = [f"shared-{chat.id}" for chat in chats_by_user]

                db.query(Chat).filter(Chat.user_id.in_(shared_chat_ids)).delete()
                db.commit()

                return True
        except Exception:
            return False


Chats = ChatTable()
