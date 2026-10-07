"""group name_key

llens(#151): グループ名は `/ext/groups/<グループ名>/` のディレクトリ名になる(#152)
ため、正規化した名前で一意にする。`group.name_key`(NFKC → 前後の空白除去 →
casefold)を足し、既存行を埋めてから unique index を張る。

既存データの重複は検出しない(ken 判断 2026-10-07: 本番ではグループをほとんど
作っていない)。重複があれば index 作成で migration が落ちる — 片方を改名してから
やり直す。正規化はアプリ側(utils/group_names.normalize_group_name_key)と同じ式を
ここに固定で持つ(migration はアプリのコード変更に引きずられないように)。

Revision ID: a5c8e2f17b49
Revises: e7b3c90d2f14
Create Date: 2026-10-07 12:00:00

"""

import unicodedata
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "a5c8e2f17b49"
down_revision: Union[str, None] = "e7b3c90d2f14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def name_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def upgrade() -> None:
    op.add_column("group", sa.Column("name_key", sa.Text(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text('SELECT id, name FROM "group"')).fetchall()
    for row in rows:
        if row.name is None:
            continue
        connection.execute(
            sa.text('UPDATE "group" SET name_key = :key WHERE id = :id'),
            {"key": name_key(row.name), "id": row.id},
        )

    # 命名は SQLAlchemy 既定(index=True)の ix_<table>_<column> に合わせる
    op.create_index("ix_group_name_key", "group", ["name_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_group_name_key", table_name="group")
    op.drop_column("group", "name_key")
