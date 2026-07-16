"""drop memory table

llens(#67): memory は #52 で素の SQL ストアに降格・#63 で UI 撤去済みだった残骸。
API(routers/memories)・モデルごと撤去し、memory テーブルを drop する。

Revision ID: c4e8a1f6d2b9
Revises: b2d7f3a9c8e1
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1f6d2b9"
down_revision: Union[str, None] = "b2d7f3a9c8e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "memory" in inspector.get_table_names():
        op.drop_table("memory")


def downgrade() -> None:
    # 復元はしない(データは失われる)。テーブル定義だけ戻す。
    op.create_table(
        "memory",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
