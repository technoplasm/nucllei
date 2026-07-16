"""normalize access_control to read-only ACL

llens(#66): 権限3層化。per-entity ACL は「read = 使える範囲」一本に縮退し、
write 側と llens 拡張 group_terms(AND 交差)を model / tool / skill の
access_control JSON から掃除する。file はチャット共有の public マーカー等
別意味論(_memos/chat-share-files.md)のため対象外。

Revision ID: e8b3d6a1c5f7
Revises: a7c9e2f4b8d1
Create Date: 2026-07-10
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8b3d6a1c5f7"
down_revision: Union[str, None] = "a7c9e2f4b8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("model", "tool", "skill")


def _normalize(access_control):
    """{"read": {"user_ids": [...], "group_ids": [...]}} のみに正規化する。

    None(全員使用可)は None のまま。read が無い(write のみ等)場合は
    {} 相当 = 「誰も使えない」ではなく、read キーを空で明示する。
    """
    if access_control is None:
        return None
    read = access_control.get("read") or {}
    return {
        "read": {
            "user_ids": read.get("user_ids") or [],
            "group_ids": read.get("group_ids") or [],
        }
    }


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())

    for table_name in TABLES:
        if table_name not in existing:
            continue
        table = sa.table(
            table_name,
            sa.column("id", sa.Text()),
            sa.column("access_control", sa.JSON()),
        )
        rows = conn.execute(sa.select(table.c.id, table.c.access_control)).fetchall()
        for row_id, raw in rows:
            if raw is None:
                continue
            value = json.loads(raw) if isinstance(raw, str) else raw
            normalized = _normalize(value)
            if normalized == value:
                continue
            conn.execute(
                table.update()
                .where(table.c.id == row_id)
                .values(access_control=normalized)
            )


def downgrade() -> None:
    # 破壊的正規化のため復元しない(write / group_terms の情報は失われる)。
    pass
