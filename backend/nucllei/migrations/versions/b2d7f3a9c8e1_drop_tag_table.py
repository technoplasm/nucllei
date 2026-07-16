"""drop tag table

llens(#67): chat tag は機能ごと撤去(UI・API・自動生成・検索の tag: 構文)。
tag テーブルを drop する。chat.meta 内の "tags" キーは残置(無害な遺物。
読む者はもういない)。

Revision ID: b2d7f3a9c8e1
Revises: e3b1c9f4a7d2
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d7f3a9c8e1"
down_revision: Union[str, None] = "e3b1c9f4a7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "tag" in inspector.get_table_names():
        op.drop_table("tag")


def downgrade() -> None:
    # 復元はしない(データは失われる)。テーブル定義だけ戻す。
    op.create_table(
        "tag",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", "user_id", name="pk_id_user_id"),
    )
