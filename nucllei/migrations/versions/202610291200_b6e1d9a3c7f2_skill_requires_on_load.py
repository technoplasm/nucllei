"""skill requires / on_load

skill を読み込んだときに効く 2 キー (llens prism-hu/llens#22「hooks 的な」、2026-10-29)。
`requires` = 一緒に読み込む skill の id の配列、`on_load` = 読み込み時にサーバが 1 回だけ
実行する tool の配列 (`[{"tool": <名前>, "args": {…}}]`)。発火の規則は
_memos/skill-design.md「requires / on_load」。

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
    op.add_column("skill", sa.Column("requires", sa.JSON(), nullable=True))
    op.add_column("skill", sa.Column("on_load", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("skill", "on_load")
    op.drop_column("skill", "requires")
