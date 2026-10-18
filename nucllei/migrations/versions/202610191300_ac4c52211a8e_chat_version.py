"""chat version

chat 行の CAS(append_delivery_turn / update_chat_by_id_guarded — #110)のバージョンを
updated_at から切り離す。旧実装は updated_at を CAS のスタンプと兼用し、同秒 ABA を
防ぐため全書き込み点で max(now, 旧値+1) に進めていた。1 秒に 2 回以上書かれる chat
(tool ループのスナップショット保存・usage 記録が重なる長いターン)では updated_at が
実時刻より先へ進み続けて戻らず、updated_at 降順の一覧で新しいチャットより上に
居座った(staging で実時刻より約 2 時間先、2026-10-19)。

- `chat.version`(BigInteger NOT NULL、既定 0)を足す。CAS はこれを比較・+1 する
- 実時刻より未来の updated_at は、migration 時点の時刻に丸める(ずれた既存行の掃除 —
  放っておくと実時刻が追いつくまで一覧の上に居座り続ける)

Revision ID: ac4c52211a8e
Revises: a5c8e2f17b49
Create Date: 2026-10-19 13:00:00

"""

import time
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "ac4c52211a8e"
down_revision: Union[str, None] = "a5c8e2f17b49"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat",
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
    )
    now = int(time.time())
    op.get_bind().execute(
        sa.text("UPDATE chat SET updated_at = :now WHERE updated_at > :now"),
        {"now": now},
    )


def downgrade() -> None:
    op.drop_column("chat", "version")
