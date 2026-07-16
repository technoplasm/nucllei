"""Promote tool/function description to a top-level column

llens(#43): 全 workspace エンティティを {id, name, description} に統一する一環。
Tool/Function の description を meta.description(JSON)から top-level 列へ昇格。
Function は #45 で manifest が消え meta が description だけになっていたので meta 列ごと撤去。
Tool は builtin 判別のため meta 列を残す。

既存行の meta.description → description 列への backfill はしない(air-gapped・少数・
再保存/作り直しで対処。builtin tool は起動時 sync で description が再設定される)。
SQLite は ALTER 制約が強いので batch mode。

Revision ID: b2d4e8f1a9c3
Revises: a1c3f9e2d7b4
Create Date: 2026-07-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4e8f1a9c3"
down_revision: Union[str, None] = "a1c3f9e2d7b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    with op.batch_alter_table("tool", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))

    with op.batch_alter_table("function", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.drop_column("meta")


def downgrade():
    with op.batch_alter_table("function", schema=None) as batch_op:
        batch_op.add_column(sa.Column("meta", sa.JSON(), nullable=True))
        batch_op.drop_column("description")

    with op.batch_alter_table("tool", schema=None) as batch_op:
        batch_op.drop_column("description")
