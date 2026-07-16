"""drop knowledge table

llens(#41): KB 機能は #52 で撤去済み。models/knowledge.py は #40 の
has_read_access_to_collection(KB 委譲)だけが温存理由だったが、file の read が
共有 public マーカー(#41)へ置き換わり KB 経路が消えたため、モデルごと撤去し
knowledge テーブルを drop する。

Revision ID: a7c9e2f4b8d1
Revises: d5f2c7b8e4a3
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e2f4b8d1"
down_revision: Union[str, None] = "d5f2c7b8e4a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "knowledge" in inspector.get_table_names():
        op.drop_table("knowledge")


def downgrade() -> None:
    # 復元はしない(データは失われる)。テーブル定義だけ戻す。
    op.create_table(
        "knowledge",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("access_control", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
