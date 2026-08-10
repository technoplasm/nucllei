"""file soft delete purge columns

llens(#94): file 削除の soft delete / purge 化。行は消さず deleted_at/deleted_by を
立てて非表示化し、実体の破棄(purge)は purged_at/purged_by で記録する。

Revision ID: 25eaeba5ebde
Revises: c33997f844fc
Create Date: 2026-07-28 21:18:28.198992

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "25eaeba5ebde"
down_revision: Union[str, None] = "c33997f844fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("file", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
    op.add_column("file", sa.Column("deleted_by", sa.Text(), nullable=True))
    op.add_column("file", sa.Column("purged_at", sa.BigInteger(), nullable=True))
    op.add_column("file", sa.Column("purged_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("file", "purged_by")
    op.drop_column("file", "purged_at")
    op.drop_column("file", "deleted_by")
    op.drop_column("file", "deleted_at")
