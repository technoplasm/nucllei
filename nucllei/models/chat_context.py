"""会話の前置き・更新の枠の凍結 (utils/conversation_context)。

行は (chat_id, user メッセージの id) ごとに 1 つ。その発言の先頭に入れる枠込みのバイト列 (`text`) と、その時点で
その枝が知っている記憶・レシピの版 (`state`) を持つ。書くのは 1 回だけで書き換えない (同時の 2 リクエストは
先勝ち — 後の方は読み直して同じものを使う)。

- kind: `initial` (前置き) / `update` (更新の枠) / `none` (何も入れない — 枝の state を途切れさせないための行)
- state: `{"template": <前置きテンプレートの sha256 先頭 16 桁 or null>, "vars": {"{{USER_MEMORY_INDEX}}": <sha> | "unknown", …}}`

chat の JSON・メッセージには置かない (FE の保存が history を丸ごと置き換える・import で偽の前置きを持ち込める)。
chat の行は soft delete で残るので、この行も消さない。clone・import・共有には持ち越さない (次の発言で組み直す)。
FK は張らない (repo 慣行)。
"""

import logging
import time
from typing import Optional

from nucllei.internal.db import Base, get_db
from nucllei.env import SRC_LOG_LEVELS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Column, String, Text
from sqlalchemy.exc import IntegrityError

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

CONTEXT_KINDS = ("initial", "update", "none")


class ChatContext(Base):
    __tablename__ = "chat_context"

    chat_id = Column(String, primary_key=True)
    message_id = Column(String, primary_key=True)  # user メッセージの id
    kind = Column(String, nullable=False)  # initial | update | none
    text = Column(Text, nullable=False)  # 発言の先頭に入れる枠込みのバイト列
    state = Column(JSON, nullable=False)
    model_id = Column(String, nullable=True)  # 組んだときの preset
    created_at = Column(BigInteger, nullable=False)


class ChatContextModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_id: str
    message_id: str
    kind: str
    text: str
    state: dict
    model_id: Optional[str] = None
    created_at: int


class ChatContextTable:
    def get_by_chat_id(self, chat_id: str) -> dict[str, ChatContextModel]:
        """その chat の行を {message_id: 行} で。"""
        with get_db() as db:
            rows = db.query(ChatContext).filter_by(chat_id=chat_id).all()
            return {
                row.message_id: ChatContextModel.model_validate(row) for row in rows
            }

    def get(self, chat_id: str, message_id: str) -> Optional[ChatContextModel]:
        with get_db() as db:
            row = db.get(ChatContext, (chat_id, message_id))
            return ChatContextModel.model_validate(row) if row else None

    def insert_once(
        self,
        chat_id: str,
        message_id: str,
        kind: str,
        text: str,
        state: dict,
        model_id: Optional[str],
    ) -> ChatContextModel:
        """行を 1 回だけ書く。既にあれば (同時のリクエストが先に書いた) 書かずにその行を返す。"""
        if kind not in CONTEXT_KINDS:
            raise ValueError(f"unknown chat_context kind: {kind!r}")
        try:
            with get_db() as db:
                db.add(
                    ChatContext(
                        chat_id=chat_id,
                        message_id=message_id,
                        kind=kind,
                        text=text,
                        state=state,
                        model_id=model_id,
                        created_at=int(time.time()),
                    )
                )
                db.commit()
        except IntegrityError:
            log.info(
                f"chat_context already written (chat_id={chat_id}, message_id={message_id})"
            )
        row = self.get(chat_id, message_id)
        if row is None:
            raise RuntimeError(
                f"chat_context の行を書けない (chat_id={chat_id}, message_id={message_id})"
            )
        return row


ChatContexts = ChatContextTable()
