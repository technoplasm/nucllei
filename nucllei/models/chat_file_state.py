"""sandbox のファイルの「この会話で読んだ・書いた版」(chat_file_state)。

sandbox の `write_file` が既にあるファイルを上書きできるのは、同じ会話でそのパスを読んだか
書いた後で、その版から変わっていないときだけ (`builtin_tools/sandbox.py`)。その記録を
(会話, 利用者, パス) ごとに 1 行で持つ:

- `path` は jail の絶対パス (正規化後 — `/workspace/…` `/ext/groups/<g>/…` `/ext/public/…`)
- `mtime` / `size` は sandbox が返した版 (`/files/stat` `/files/read` `/files/write` の値)。
  `mtime` が NULL = sandbox が版を返さなかった (上書きの確認ができない)
- `via` は記録した操作 (read / write / edit / recipe / import — ログと調べ物用)

サブエージェントの子は子の chat の id で別に持つ。chat を物理削除する経路は無い (soft delete は
記録を残す)。clone・import には持ち越さない。chat_id の無い headless の呼び出しは DB を使わず、
その 1 リクエストの `__metadata__` に持つ (tool 側)。
"""

import logging
import time
from typing import Optional

from nucllei.internal.db import Base, get_db
from nucllei.env import SRC_LOG_LEVELS
from pydantic import BaseModel
from sqlalchemy import BigInteger, Column, Float, String, Text
from sqlalchemy.exc import IntegrityError

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


class ChatFileState(Base):
    __tablename__ = "chat_file_state"

    chat_id = Column(String, primary_key=True)
    user_id = Column(String, primary_key=True)
    path = Column(Text, primary_key=True)
    mtime = Column(Float, nullable=True)
    size = Column(BigInteger, nullable=True)
    via = Column(String, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class FileStamp(BaseModel):
    """記録した版。"""

    mtime: Optional[float] = None
    size: Optional[int] = None
    via: str = ""


class ChatFileStateTable:
    def get(self, chat_id: str, user_id: str, path: str) -> Optional[FileStamp]:
        with get_db() as db:
            row = db.get(ChatFileState, (chat_id, user_id, path))
            if row is None:
                return None
            return FileStamp(mtime=row.mtime, size=row.size, via=row.via)

    def put(
        self,
        chat_id: str,
        user_id: str,
        path: str,
        mtime: Optional[float],
        size: Optional[int],
        via: str,
    ) -> None:
        """(chat_id, user_id, path) の記録を置き換える (無ければ作る)。"""
        values = {
            "mtime": mtime,
            "size": size,
            "via": via,
            "updated_at": int(time.time()),
        }
        for attempt in range(2):
            try:
                with get_db() as db:
                    row = db.get(ChatFileState, (chat_id, user_id, path))
                    if row is None:
                        db.add(
                            ChatFileState(
                                chat_id=chat_id, user_id=user_id, path=path, **values
                            )
                        )
                    else:
                        for k, v in values.items():
                            setattr(row, k, v)
                    db.commit()
                    return
            except IntegrityError:
                # 同じキーを並んで作った — 2 回目は在る行を更新する
                if attempt:
                    raise


ChatFileStates = ChatFileStateTable()
