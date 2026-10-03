import copy
import logging
import time
import uuid
from typing import Optional

from nucllei.internal.db import Base, get_db
from nucllei.env import SRC_LOG_LEVELS

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Integer, String, Text, JSON
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

    # llens(#107): agent session(サブエージェント実行チャット)の種別と親リンク。
    # 一覧クエリが全てカラム述語で引けるよう meta(JSON)ではなくカラムに持つ
    # (ChatImportForm が meta を丸ごと受けるため、meta 置きは kind 詐称を許す)。
    # ユーザー向け一覧は kind="user" で agent を除外し、admin/監査系は除外しない。
    kind = Column(String, nullable=False, server_default="user")  # "user" | "agent"
    parent_chat_id = Column(String, nullable=True, index=True)
    parent_message_id = Column(String, nullable=True)
    depth = Column(Integer, nullable=False, server_default="0")  # user chat = 0


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

    kind: str = "user"
    parent_chat_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    depth: int = 0


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
    # llens(#107 P3): FE の /c/ 観測モードが agent session 判定と
    # 親 chat へのリンク表示に使う
    kind: str = "user"
    parent_chat_id: Optional[str] = None


class ChatTitleIdResponse(BaseModel):
    id: str
    title: str
    updated_at: int
    created_at: int


def append_turn_messages(
    chat_json: dict, user_message: dict, assistant_message: dict
) -> tuple[str, str]:
    """chat JSON の history に user + assistant 対を積む(ターン追記の共通組み立て)。

    FE の submitPrompt / sendPrompt(send.ts)が書く形と同じ構造
    (id / parentId / childrenIds / role / content / timestamp)。id・親子リンク・
    currentId・線形リスト(messages — _chat_snippet 等の読み手向け)はここが補完し、
    呼び出し側は role / content / files / meta / model 等の中身だけを渡す。
    書き手は 2 者: subagent の子ターン追記(utils/subagent.py)と、サーバ駆動配達の
    notice 追記(append_delivery_turn — #110 契約 T3)。models 層に置くのは後者が
    単一トランザクション内で同じ組み立てを使うため。
    戻り値は (user_message_id, assistant_message_id)。
    """
    # 循環 import 回避のため遅延 import(get_chat_previews の User と同じ流儀)
    from nucllei.utils.misc import get_message_list

    history = chat_json.setdefault("history", {"messages": {}, "currentId": None})
    messages_map = history.setdefault("messages", {})
    parent_id = history.get("currentId")

    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    now = int(time.time())

    messages_map[user_message_id] = {
        **user_message,
        "id": user_message_id,
        "parentId": parent_id,
        "childrenIds": [assistant_message_id],
        "timestamp": user_message.get("timestamp", now),
    }
    if parent_id and parent_id in messages_map:
        messages_map[parent_id].setdefault("childrenIds", []).append(user_message_id)

    messages_map[assistant_message_id] = {
        **assistant_message,
        "id": assistant_message_id,
        "parentId": user_message_id,
        "childrenIds": [],
        "timestamp": assistant_message.get("timestamp", now),
    }
    history["currentId"] = assistant_message_id

    chat_json["messages"] = get_message_list(messages_map, assistant_message_id) or []

    return user_message_id, assistant_message_id


def _monotonic_updated_at(previous: Optional[int]) -> int:
    """chat 行の updated_at を厳密単調増加させる(#110 敵対検証 2)。

    updated_at は append_delivery_turn / update_chat_by_id_guarded の CAS が
    バージョンスタンプとして使う。秒粒度の int(time.time()) をそのまま書くと、
    同秒内の介在書き込みで値が変わらず CAS が偽成功する(ABA)。chat 行へ
    updated_at を書く**全書き込み点**は max(now, 旧値 + 1) で必ず前進させること。
    """
    now = int(time.time())
    return max(now, (previous or 0) + 1)


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
    def insert_new_chat(
        self,
        user_id: str,
        form_data: ChatForm,
        kind: str = "user",
        parent_chat_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        depth: int = 0,
    ) -> Optional[ChatModel]:
        # llens(#107): kind 以下は agent session(サブエージェント)の spawn 経路のみが
        # 指定する。HTTP 経由の作成は既定値のまま = 常に kind="user"。
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
                    "kind": kind,
                    "parent_chat_id": parent_chat_id,
                    "parent_message_id": parent_message_id,
                    "depth": depth,
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
                # 同秒 ABA 防止(#110 敵対検証 2)— CAS のスタンプは必ず前進させる
                chat_item.updated_at = _monotonic_updated_at(chat_item.updated_at)
                db.commit()
                db.refresh(chat_item)

                return ChatModel.model_validate(chat_item)
        except Exception:
            return None

    def update_chat_by_id_guarded(
        self, id: str, chat: dict, expected_updated_at: int
    ) -> tuple[str, Optional["ChatModel"]]:
        """FE 保存経路の CAS 版 update(#110 敵対検証 3 — append_delivery_turn と同型)。

        呼び出し側が読んだ updated_at を期待値に guarded UPDATE する。配達 driver の
        append_delivery_turn と同じバージョンスタンプを取り合うので、保存の
        read-modify-write の間に driver append(notice 追記)が挟まっても
        無言で上書き(notice 消失)しない。無ガード版 update_chat_by_id は
        単一書き手の内部経路(タイトル・usage・agent session 等)専用に残る。
        戻り値は (status, chat):
        - ("ok", 更新後 ChatModel) — 成功
        - ("conflict", None) — CAS 負け。呼び出し側は再読して検証し直す
        - ("missing", None) — chat 不在
        """
        try:
            with get_db() as db:
                row = db.get(Chat, id)
                if row is None:
                    return ("missing", None)
                rowcount = (
                    db.query(Chat)
                    .filter_by(id=id, updated_at=expected_updated_at)
                    .update(
                        {
                            "chat": chat,
                            "title": chat["title"] if "title" in chat else "New Chat",
                            "updated_at": _monotonic_updated_at(expected_updated_at),
                        },
                        synchronize_session=False,
                    )
                )
                if rowcount == 0:
                    db.rollback()
                    return ("conflict", None)
                db.commit()
                db.refresh(row)
                return ("ok", ChatModel.model_validate(row))
        except Exception:
            log.exception(f"Failed guarded update of chat {id}")
            return ("conflict", None)

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

    def append_delivery_turn(
        self,
        chat_id: str,
        notice_message: dict,
        assistant_message: dict,
        exec_ids: list[str],
    ) -> tuple[str, Optional[ChatModel]]:
        """サーバ駆動配達の CAS 追記(#110 契約 M2)。

        単一 DB トランザクションで:
        1. chat 行を読む(updated_at を CAS の期待値として控える)
        2. history / messages に notice(user)+ 空 assistant 対を append
           (append_turn_messages — currentId 更新・線形 messages 同期込み)
        3. `UPDATE chat SET chat=?, updated_at=? WHERE id=? AND updated_at=読値`
           の guarded UPDATE(FE 保存・別ターンの書き込みとの同着は CAS で検出)
        4. 同一トランザクションで execution 行の delivered_at を確定

        notice 追記と delivered 確定が原子化されるので、クラッシュ窓での
        「notice は載ったが未配達のまま → 二重 append」が構造的に消える。
        戻り値は (status, chat):
        - ("ok", 更新後 ChatModel) — 成功
        - ("conflict", None) — CAS 負け。呼び出し側は再読して呼び直す(再試行可能)
        - ("missing", None) — chat 不在 / 削除済み。恒久断念へ
        """
        # 循環 import 回避(models 同士でも慣行どおり遅延 import)
        from nucllei.models.executions import Execution

        try:
            with get_db() as db:
                row = db.get(Chat, chat_id)
                if row is None or row.deleted_at is not None:
                    return ("missing", None)
                expected_updated_at = row.updated_at

                # deepcopy: JSON カラムの ORM インスタンスを in-place 変異させない
                # (CAS 負けで rollback するとき部分変更を持ち越さないため)
                chat_json = copy.deepcopy(row.chat)
                append_turn_messages(chat_json, notice_message, assistant_message)

                now = int(time.time())
                # CAS スタンプは必ず前進させる(同秒 ABA 防止 — 敵対検証 2)
                new_updated_at = _monotonic_updated_at(expected_updated_at)
                rowcount = (
                    db.query(Chat)
                    .filter_by(id=chat_id, updated_at=expected_updated_at)
                    .update(
                        {"chat": chat_json, "updated_at": new_updated_at},
                        synchronize_session=False,
                    )
                )
                if rowcount == 0:
                    db.rollback()
                    return ("conflict", None)

                if exec_ids:
                    db.query(Execution).filter(
                        Execution.id.in_(exec_ids),
                        Execution.delivered_at == None,  # noqa: E712
                    ).update(
                        {"delivered_at": now},
                        synchronize_session=False,
                    )
                db.commit()
                db.refresh(row)
                return ("ok", ChatModel.model_validate(row))
        except Exception:
            log.exception(f"Failed to append delivery turn to chat {chat_id}")
            return ("conflict", None)

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
          carried_tokens(次ターンへ持ち越す量 = 最後の呼び出しの prompt +
          再送される出力。middleware が算出)も最新値で上書き。
        - meta["usage"]: チャット単位。prompt_tokens は最新、completion_tokens
          は累積(再生成・分岐込みでこの会話が生成した総量)。model の
          context_size(max_model_len)も添えて閲覧側(#19)が使用率を出せる。
        """
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        duration = usage.get("duration")
        # 次ターンへ持ち越す量(middleware.carried_tokens_from_usage が算出)。
        # prompt 同様「最後の呼び出し」の値なので最新で上書き — tool ループの以前の
        # 呼び出しの出力は最新 prompt に含まれており、累積すると二重計上になる。
        carried_tokens = usage.get("carried_tokens")
        if prompt_tokens is None and completion_tokens is None:
            return None

        def merge(target: dict) -> dict:
            merged = {**target}
            if prompt_tokens is not None:
                merged["prompt_tokens"] = prompt_tokens
            if carried_tokens is not None:
                merged["carried_tokens"] = carried_tokens
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

                # 同秒 ABA 防止(敵対検証 2)— chat 行の全書き込み点で前進させる
                chat_item.updated_at = _monotonic_updated_at(chat_item.updated_at)
                db.commit()
                # マージ後の message.usage を返す(#102): 呼び手(middleware)は
                # これをそのまま emit する — FE の message.usage と DB が常に一致し、
                # tool ループでも「ターン累積」の completion / duration が画面に出る。
                return message_usage
        except Exception:
            log.exception(f"Failed to record usage for chat {id}")
            return None

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

                # 共有コピー行は CAS 対象ではないが、書き込み点の規律は揃える
                shared_chat.updated_at = _monotonic_updated_at(shared_chat.updated_at)
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
                # 同秒 ABA 防止(敵対検証 2)
                chat.updated_at = _monotonic_updated_at(chat.updated_at)
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
                # 同秒 ABA 防止(敵対検証 2)
                chat.updated_at = _monotonic_updated_at(chat.updated_at)
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
                ).filter(Chat.kind == "user").update({"archived": True})
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
                .filter(Chat.kind == "user")
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
            query = query.filter(Chat.kind == "user")
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
            query = query.filter(Chat.kind == "user")

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

    def get_chat_kind_and_depth_by_id(self, id: str) -> Optional[tuple]:
        """llens(#107): id → (kind, depth) の軽量 lookup(chat JSON を運ばない)。

        mount 経路(build_run_subagent_tool)の depth 判定用 — get_chat_by_id は
        chat JSON 全体(MB 級になりうる)を運ぶため使わない。存在・deleted の
        権威判定は callable 側の実行時再チェック(get_chat_by_id)が持つ。
        """
        try:
            with get_db() as db:
                row = db.query(Chat.kind, Chat.depth).filter_by(id=id).first()
                return (row[0], row[1]) if row else None
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

    def get_chat_ids_by_user_id(self, user_id: str) -> list[str]:
        """get_chats_by_user_id の id 専用版(全チャット削除 cascade 用 — B-5)。

        重い chat JSON を運ばない。フィルタは get_chats_by_user_id と同一に保つこと。
        """
        with get_db() as db:
            return [
                row[0]
                for row in db.query(Chat.id)
                .filter_by(user_id=user_id)
                .filter(Chat.deleted_at == None)  # noqa: E712
                .filter(Chat.kind == "user")
                .all()
            ]

    def get_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id)
                .filter(Chat.deleted_at == None)
                .filter(Chat.kind == "user")
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_pinned_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, pinned=True, archived=False)
                .filter(Chat.deleted_at == None)
                .filter(Chat.kind == "user")
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_archived_chats_by_user_id(self, user_id: str) -> list[ChatModel]:
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(user_id=user_id, archived=True)
                .filter(Chat.deleted_at == None)
                .filter(Chat.kind == "user")
                .order_by(Chat.updated_at.desc())
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_child_chats_by_parent_id(
        self, parent_chat_id: str, user_id: str
    ) -> list[ChatModel]:
        """llens(#107): 親チャット直下の agent session 一覧(サイドバー tree / 親画面用)。"""
        with get_db() as db:
            all_chats = (
                db.query(Chat)
                .filter_by(parent_chat_id=parent_chat_id, user_id=user_id)
                .filter(Chat.deleted_at == None)
                .order_by(Chat.updated_at.desc())
                .all()
            )
            return [ChatModel.model_validate(chat) for chat in all_chats]

    def get_descendant_chat_ids(self, chat_id: str) -> list[str]:
        """llens(#107): 全子孫チャットの id を BFS で収集(deleted 済みは含まない)。

        親削除の cascade soft delete と走行中タスクの stop 連鎖が使う。
        depth は SUBAGENT_MAX_DEPTH で抑えられているが、上限値に依存せず層ごとに辿る。
        """
        with get_db() as db:
            descendant_ids: list[str] = []
            # visited は防御: 親子リンクが循環するデータ(現行 API では作れないが
            # DB 破損時)で無限ループ = イベントループ停止にしない
            visited = {chat_id}
            frontier = [chat_id]
            while frontier:
                rows = (
                    db.query(Chat.id)
                    .filter(Chat.parent_chat_id.in_(frontier))
                    .filter(Chat.deleted_at == None)
                    .all()
                )
                frontier = [row[0] for row in rows if row[0] not in visited]
                visited.update(frontier)
                descendant_ids.extend(frontier)
            return descendant_ids

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
            query = query.filter(Chat.kind == "user")

            if not include_archived:
                query = query.filter(Chat.archived == False)

            query = query.order_by(Chat.updated_at.desc())

            # Check if the database dialect is either 'sqlite' or 'postgresql'
            dialect_name = db.bind.dialect.name
            if dialect_name == "sqlite":
                # SQLite case: using JSON1 extension for JSON searching
                query = query.filter(
                    (Chat.title.ilike(f"%{search_text}%") | text("""
                            EXISTS (
                                SELECT 1 
                                FROM json_each(Chat.chat, '$.messages') AS message 
                                WHERE LOWER(message.value->>'content') LIKE '%' || :search_text || '%'
                            )
                            """)).params(  # Case-insensitive search in title
                        search_text=search_text
                    )
                )

            elif dialect_name == "postgresql":
                # PostgreSQL relies on proper JSON query for search
                query = query.filter(
                    (Chat.title.ilike(f"%{search_text}%") | text("""
                            EXISTS (
                                SELECT 1
                                FROM json_array_elements(Chat.chat->'messages') AS message
                                WHERE LOWER(message->>'content') LIKE '%' || :search_text || '%'
                            )
                            """)).params(  # Case-insensitive search in title
                        search_text=search_text
                    )
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
        kind: Optional[str] = None,
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
        kind は chat 種別(#107 agent session)の絞り込み: None(既定・全部)/ "user" / "agent"。
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
            if kind is not None:
                query = query.filter(Chat.kind == kind)
            if title:
                query = query.filter(Chat.title.ilike(f"%{title}%"))
            if updated_from is not None:
                query = query.filter(Chat.updated_at >= updated_from)
            if updated_to is not None:
                query = query.filter(Chat.updated_at <= updated_to)
            if model:
                dialect_name = db.bind.dialect.name
                if dialect_name == "sqlite":
                    query = query.filter(text("""
                            EXISTS (
                                SELECT 1
                                FROM json_each(Chat.chat, '$.models') AS m
                                WHERE m.value = :model
                            )
                            """).params(model=model))
                elif dialect_name == "postgresql":
                    query = query.filter(text("""
                            EXISTS (
                                SELECT 1
                                FROM json_array_elements_text(Chat.chat->'models') AS m(value)
                                WHERE m.value = :model
                            )
                            """).params(model=model))
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
                rows = db.execute(text("""
                        SELECT m.value AS model, COUNT(*) AS chat_count
                        FROM chat, json_each(chat.chat, '$.models') AS m
                        GROUP BY m.value
                        ORDER BY chat_count DESC, model ASC
                        """)).all()
            elif dialect_name == "postgresql":
                rows = db.execute(text("""
                        SELECT m.value AS model, COUNT(*) AS chat_count
                        FROM chat, json_array_elements_text(chat.chat->'models') AS m(value)
                        GROUP BY m.value
                        ORDER BY chat_count DESC, model ASC
                        """)).all()
            else:
                raise NotImplementedError(f"Unsupported dialect: {dialect_name}")
            return [{"model": row.model, "chat_count": row.chat_count} for row in rows]

    # llens(#31): 削除系はすべて soft delete(deleted_at を立てて非表示化 + 共有解除)。
    # 監査証憑の保全のため一次データの行は消さない。share コピー(user_id="shared-*" の
    # 複製行)は派生データなので従来どおり物理削除する。

    def delete_chat_by_id(self, id: str) -> bool:
        try:
            # llens(#107): agent session(子孫チャット)は親と一体 — 親削除で
            # cascade soft delete する(孤児の agent session を残さない)。
            target_ids = [id, *self.get_descendant_chat_ids(id)]
            with get_db() as db:
                db.query(Chat).filter(Chat.id.in_(target_ids)).update(
                    {"deleted_at": int(time.time()), "share_id": None},
                    synchronize_session=False,
                )
                db.commit()

            result = True
            for target_id in target_ids:
                result = self.delete_shared_chat_by_chat_id(target_id) and result
            return result
        except Exception:
            return False

    def delete_chat_by_id_and_user_id(self, id: str, user_id: str) -> bool:
        try:
            # llens(#107): cascade は delete_chat_by_id と同じ。子孫は spawn 時に
            # 同一 user_id で作られるため user_id 述語ごと一括で落とせる。
            target_ids = [id, *self.get_descendant_chat_ids(id)]
            with get_db() as db:
                db.query(Chat).filter(
                    Chat.id.in_(target_ids), Chat.user_id == user_id
                ).update(
                    {"deleted_at": int(time.time()), "share_id": None},
                    synchronize_session=False,
                )
                db.commit()

            result = True
            for target_id in target_ids:
                result = self.delete_shared_chat_by_chat_id(target_id) and result
            return result
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
