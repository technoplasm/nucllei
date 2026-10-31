"""chat file state

sandbox の `write_file` が既にあるファイルを上書きできるのは、同じ会話でそのパスを
読んだか書いた後だけ (`builtin_tools/sandbox.py`)。その「読んだ・書いた」の記録を
会話 (chat) ごとに持つ: 主キーは (chat_id, user_id, path)。path は jail の絶対パス (正規化後)、
mtime / size は sandbox が返した版 (mtime が NULL = sandbox が版を返さなかった)。
FK は張らない (repo 慣行)。

Revision ID: f65264696956
Revises: 31221e5f4d38
Create Date: 2026-10-31 17:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f65264696956"
down_revision: Union[str, None] = "31221e5f4d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_file_state",
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mtime", sa.Float(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("via", sa.String(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("chat_id", "user_id", "path"),
    )


def downgrade() -> None:
    op.drop_table("chat_file_state")
