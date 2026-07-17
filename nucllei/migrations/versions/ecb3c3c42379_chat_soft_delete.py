"""chat soft delete — deleted_at 列の追加(#31)

監査証憑の保全のためチャットは hard delete しない。削除系は deleted_at(epoch 秒)を
立てるだけにし、ユーザー向けの一覧・検索・取得から除外する(admin 一覧では見える)。

Revision ID: ecb3c3c42379
Revises: c33997f844fc
Create Date: 2026-07-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ecb3c3c42379"
down_revision: Union[str, None] = "c33997f844fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat", sa.Column("deleted_at", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat", "deleted_at")
