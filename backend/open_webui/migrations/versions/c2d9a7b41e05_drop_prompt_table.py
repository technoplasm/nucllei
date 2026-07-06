"""Drop prompt table

prompts の退役(issue #35 frontend batch と同時)。skill への複写は
f8a4d0e2c1b7 で実施済みで、旧 prompts router / フロントも削除されたため
テーブルごと落とす(データ保全は考慮しない — ユーザー決定 2026-07-06)。

Revision ID: c2d9a7b41e05
Revises: f8a4d0e2c1b7
Create Date: 2026-07-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d9a7b41e05"
down_revision: Union[str, None] = "f8a4d0e2c1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.drop_table("prompt")


def downgrade():
    # 複写元の再構築はしない(f8a4d0e2c1b7 時点のスキーマだけ戻す)。
    op.create_table(
        "prompt",
        sa.Column("command", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String()),
        sa.Column("title", sa.Text()),
        sa.Column("content", sa.Text()),
        sa.Column("timestamp", sa.BigInteger()),
        sa.Column("access_control", sa.JSON(), nullable=True),
    )
