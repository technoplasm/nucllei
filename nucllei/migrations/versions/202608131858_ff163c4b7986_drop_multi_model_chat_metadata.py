"""drop multi model chat metadata

llens(#108): マルチモデル送信(side-by-side 応答)を仕様ごと撤去したのに伴い、
既存 chat の JSON から lane メタデータを剥がして単一モデル形へ正規化する:

- chat["models"] を先頭 1 要素へ切り詰め
- history.messages[*] / messages[*] から assistant の modelIdx と
  user の models を除去(複数 lane の応答自体は通常の sibling として残る —
  枝は有効な履歴なので消さない)

負の遺産を残さない方針(2026-08-13 ken)。対象行だけ書き換える。

Revision ID: ff163c4b7986
Revises: 25eaeba5ebde
Create Date: 2026-08-13 18:58:00

"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "ff163c4b7986"
down_revision: Union[str, None] = "25eaeba5ebde"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _strip_message(message: dict) -> bool:
    """1 メッセージから lane メタデータを除去。変更したら True。"""
    changed = False
    if "modelIdx" in message:
        message.pop("modelIdx", None)
        changed = True
    if "models" in message:
        message.pop("models", None)
        changed = True
    return changed


def _normalize_chat(chat: dict) -> bool:
    """chat JSON を単一モデル形へ。変更したら True。"""
    changed = False

    models = chat.get("models")
    if isinstance(models, list) and len(models) > 1:
        chat["models"] = models[:1]
        changed = True

    history_messages = (chat.get("history") or {}).get("messages")
    if isinstance(history_messages, dict):
        for message in history_messages.values():
            if isinstance(message, dict) and _strip_message(message):
                changed = True

    if isinstance(chat.get("messages"), list):
        for message in chat["messages"]:
            if isinstance(message, dict) and _strip_message(message):
                changed = True

    return changed


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, chat FROM chat")).fetchall()
    for row in rows:
        raw = row.chat
        if raw is None:
            continue
        chat = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(chat, dict):
            continue
        if _normalize_chat(chat):
            connection.execute(
                sa.text("UPDATE chat SET chat = :chat WHERE id = :id"),
                {"chat": json.dumps(chat, ensure_ascii=False), "id": row.id},
            )


def downgrade() -> None:
    # 破壊的正規化のため戻せない(lane メタデータは復元不能)。no-op。
    pass
