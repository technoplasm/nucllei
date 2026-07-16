"""Skill: command→id 一本化 + title→name

llens(#43 Phase 2a): 全 workspace エンティティを {id, name, description} に統一。
Skill の可読キーを id 一本に(旧 command 列を撤去。id が /command 発火キー兼 PK)、
title → name にリネーム。

prod 未出荷につき既存データは巻き込んでよい(ユーザー決定 2026-07-07)。既存 skill 行の
id は uuid のままなので /command 発火はしなくなる = 実質作り直し。skillIds 参照は #46 以降
存在しない id を静かに無視するので壊れない。backfill はしない。SQLite は batch mode。

Revision ID: c5e7a1b9d2f6
Revises: b2d4e8f1a9c3
Create Date: 2026-07-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a1b9d2f6"
down_revision: Union[str, None] = "b2d4e8f1a9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    with op.batch_alter_table("skill", schema=None) as batch_op:
        batch_op.alter_column("title", new_column_name="name")
        batch_op.drop_column("command")


def downgrade():
    with op.batch_alter_table("skill", schema=None) as batch_op:
        batch_op.add_column(sa.Column("command", sa.String(), nullable=True))
        batch_op.alter_column("name", new_column_name="title")
