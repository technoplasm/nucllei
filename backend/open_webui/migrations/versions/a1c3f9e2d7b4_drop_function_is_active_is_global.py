"""Drop function.is_active / is_global

llens(#46): function(filter/action/pipe)の適用制御を per-model 割り当て
(model.meta.filterIds / actionIds)一本化。is_global(全モデル強制 + 解除禁止)と
is_active(master switch)を撤去したのでカラムごと落とす(データ保全は考慮しない
— ユーザー決定 2026-07-07)。SQLite は ALTER DROP COLUMN 非対応なので batch mode。

Revision ID: a1c3f9e2d7b4
Revises: c2d9a7b41e05
Create Date: 2026-07-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3f9e2d7b4"
down_revision: Union[str, None] = "c2d9a7b41e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    with op.batch_alter_table("function", schema=None) as batch_op:
        batch_op.drop_column("is_active")
        batch_op.drop_column("is_global")


def downgrade():
    with op.batch_alter_table("function", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_global", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
