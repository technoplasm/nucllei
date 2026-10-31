"""chat context

会話の前置き・更新の枠の凍結 (`utils/conversation_context`)。主キーは (chat_id, message_id) — message_id は
その枠を先頭に入れる user メッセージ。text は枠込みのバイト列、state はその時点で枝が知っている記憶・
レシピの版。FK は張らない (repo 慣行)。

Revision ID: 7c2e9a4b1d03
Revises: f65264696956
Create Date: 2026-10-31 18:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "7c2e9a4b1d03"
down_revision: Union[str, None] = "f65264696956"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_context",
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("chat_id", "message_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_context")
