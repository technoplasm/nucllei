"""skill on_load

skill を読み込んだときに書いた順に処理する手順 (llens prism-hu/llens#22「hooks 的な」、
2026-10-29)。`on_load` = `[{"tool": <名前>, "args": {…}}]`。`load_skill` の要素は別の skill を
一緒に読み込み、それ以外の tool はサーバが chat につき 1 回だけ実行する。発火の規則は
_memos/skill-design.md「on_load」。

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
    op.add_column("skill", sa.Column("on_load", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("skill", "on_load")
