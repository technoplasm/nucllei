"""skill before / after

skill を読み込んだときにサーバが実行する tool (llens prism-hu/llens#22「hooks 的な」、
2026-10-29。2026-10-30 に on_load 1 列から before / after の 2 列へ — 未出荷なのでこの
migration を書き換えた)。どちらも `[{"tool": <名前>, "args": {…}}]`。結果は before → 本文 →
after の順に 1 つの load_skill の結果へ入る。発火の規則は _memos/skill-design.md「before / after」。

Revision ID: b6e1d9a3c7f2
Revises: ac4c52211a8e
Create Date: 2026-10-29 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b6e1d9a3c7f2"
down_revision: Union[str, None] = "ac4c52211a8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("skill", sa.Column("before", sa.JSON(), nullable=True))
    op.add_column("skill", sa.Column("after", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("skill", "after")
    op.drop_column("skill", "before")
