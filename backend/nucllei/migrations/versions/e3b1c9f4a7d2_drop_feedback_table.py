"""Drop feedback table

llens(#48): 評価(👍/👎)フィードバック機能を撤去(UI・frontend API・admin Evaluations
画面を撤去済み)。収集済みデータは価値が無いためテーブルごと落とす(データ保全は
考慮しない — ユーザー決定 2026-07-07)。downgrade は af906e964978 と同じ定義で再作成。

Revision ID: e3b1c9f4a7d2
Revises: c5e7a1b9d2f6
Create Date: 2026-07-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3b1c9f4a7d2"
down_revision: Union[str, None] = "c5e7a1b9d2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("feedback")


def downgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("version", sa.BigInteger(), default=0),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
