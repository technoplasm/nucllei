"""execution table

llens(#110): 非同期 tool 実行の一級エンティティ。親メッセージの tool result は
spawn 時の running 形のみで以後不変(append-only)— 完了の真実はこの exec 行が持つ。
status は running|done|failed|stopped|orphaned(遷移は guarded UPDATE で一方向)。

index は (chat_id) と (chat_id, status) — チップの主クエリ
(chat 内の running 絞り込み)と一覧・cascade の引き当て用。FK は張らない(repo 慣行)。

Revision ID: 7e2c41a80d95
Revises: b4d19c7a52e3
Create Date: 2026-08-14 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "7e2c41a80d95"
down_revision: Union[str, None] = "b4d19c7a52e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("tool_call_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.Column("purged_at", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execution_chat_id", "execution", ["chat_id"])
    op.create_index("ix_execution_chat_id_status", "execution", ["chat_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_execution_chat_id_status", table_name="execution")
    op.drop_index("ix_execution_chat_id", table_name="execution")
    op.drop_table("execution")
