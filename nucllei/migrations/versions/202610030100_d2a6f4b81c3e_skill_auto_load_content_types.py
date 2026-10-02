"""skill auto_load_content_types

skill の自動ロード条件(第 3 の入口)。content_type の前方一致リスト(例 ["audio/"])を
持ち、条件に合う添付が会話にあれば `/command` と同じ経路で発火する
(_memos/skill-design.md「入口 3」)。

Revision ID: d2a6f4b81c3e
Revises: c9d4e1f6a2b7
Create Date: 2026-10-03 01:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "d2a6f4b81c3e"
down_revision: Union[str, None] = "c9d4e1f6a2b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skill", sa.Column("auto_load_content_types", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("skill", "auto_load_content_types")
