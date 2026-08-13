"""chat agent session columns

llens(#107): サブエージェント = agent session(一級チャット)。chat に種別と
親リンクのカラムを追加する。一覧クエリが全てカラム述語で引けるよう
meta(JSON)ではなくカラムに持つ(ChatImportForm 経由の kind 詐称防止も兼ねる)。

- kind: "user" | "agent"(既定 "user"。ユーザー向け一覧は agent を除外)
- parent_chat_id / parent_message_id: spawn 元の親チャット・メッセージ
- depth: user chat = 0、子 = 親 + 1(SUBAGENT_MAX_DEPTH の判定用)

Revision ID: b4d19c7a52e3
Revises: ff163c4b7986
Create Date: 2026-08-13 20:15:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "b4d19c7a52e3"
down_revision: Union[str, None] = "ff163c4b7986"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default で既存行も既定値("user" / 0)に埋まる
    op.add_column(
        "chat",
        sa.Column("kind", sa.String(), nullable=False, server_default="user"),
    )
    op.add_column("chat", sa.Column("parent_chat_id", sa.String(), nullable=True))
    op.add_column("chat", sa.Column("parent_message_id", sa.String(), nullable=True))
    op.add_column(
        "chat",
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
    )
    # 命名は SQLAlchemy 既定(index=True)の ix_<table>_<column> に合わせる
    op.create_index("ix_chat_parent_chat_id", "chat", ["parent_chat_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_parent_chat_id", table_name="chat")
    op.drop_column("chat", "depth")
    op.drop_column("chat", "parent_message_id")
    op.drop_column("chat", "parent_chat_id")
    op.drop_column("chat", "kind")
