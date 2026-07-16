"""add accessed_at to file table

llens(#69): ファイル管理ページの「最近使った順」ソート用に accessed_at を追加する。
content 取得・再添付フック(process)でタッチされ、一覧・メタ取得では更新しない。
既存行は updated_at(無ければ created_at)で backfill する。

Revision ID: d9f1c3b7a2e8
Revises: e8b3d6a1c5f7
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9f1c3b7a2e8"
down_revision: Union[str, None] = "e8b3d6a1c5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns("file")}

    if "accessed_at" not in columns:
        op.add_column("file", sa.Column("accessed_at", sa.BigInteger(), nullable=True))

    file_table = sa.table(
        "file",
        sa.column("accessed_at", sa.BigInteger()),
        sa.column("updated_at", sa.BigInteger()),
        sa.column("created_at", sa.BigInteger()),
    )
    conn.execute(
        file_table.update()
        .where(file_table.c.accessed_at.is_(None))
        .values(
            accessed_at=sa.func.coalesce(
                file_table.c.updated_at, file_table.c.created_at
            )
        )
    )


def downgrade() -> None:
    op.drop_column("file", "accessed_at")
