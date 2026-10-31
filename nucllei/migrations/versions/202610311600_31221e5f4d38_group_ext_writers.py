"""group ext writers

グループの置き場 (`/ext/groups/<グループ名>/`) に書けるのは `group.data.ext.writers` に
いるメンバーだけ (`utils/ext_acl`)。既存のグループを、書ける人が今と変わらない
`{"writers": [...]}` に揃える:

- `ext` が無い・dict でない、または `ext.write` が無い・null・`"members"` のグループ
  (メンバー全員が書ける) は `writers = user_ids`
- それ以外 (`ext.write` が `"listed"` や知らない値 — 指定されたメンバーだけ書ける) は
  `writers` のうち今もメンバーの人
- `ext` は `{"writers": [...]}` だけにする (`write` を落とす)。`data` の他のキーはそのまま。
  `data` が dict でなければ `{"ext": …}` で置き換える

判定の式はアプリ側 (`utils/ext_acl`) と独立にここへ固定で持つ (migration はアプリのコード
変更に引きずられないように)。

Revision ID: 31221e5f4d38
Revises: b6e1d9a3c7f2
Create Date: 2026-10-31 16:00:00

"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "31221e5f4d38"
down_revision: Union[str, None] = "b6e1d9a3c7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


def _unique_strings(values, allowed=None) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, list) else []:
        if isinstance(v, str) and v and v not in out:
            if allowed is None or v in allowed:
                out.append(v)
    return out


def migrate_data(data, user_ids) -> dict:
    """1 グループの `data` を新形にした物を返す (引数は変えない)。テストから直接呼ぶ。"""
    data = dict(data) if isinstance(data, dict) else {}
    members = _unique_strings(user_ids)
    ext = data.get("ext")
    ext = ext if isinstance(ext, dict) else {}
    mode = ext.get("write")
    if mode is None or mode == "members":
        writers = members
    else:
        writers = _unique_strings(ext.get("writers"), set(members))
    data["ext"] = {"writers": writers}
    return data


def migrate_down_data(data) -> dict:
    """downgrade の 1 グループ分 (引数は変えない)。テストから直接呼ぶ。"""
    data = dict(data) if isinstance(data, dict) else {}
    ext = data.get("ext")
    writers = ext.get("writers") if isinstance(ext, dict) else None
    data["ext"] = {"write": "listed", "writers": _unique_strings(writers)}
    return data


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text('SELECT id, data, user_ids FROM "group"')
    ).fetchall()
    for row in rows:
        data = migrate_data(_load(row.data), _load(row.user_ids))
        connection.execute(
            sa.text('UPDATE "group" SET data = :data WHERE id = :id'),
            {"data": json.dumps(data, ensure_ascii=False), "id": row.id},
        )


def downgrade() -> None:
    """ひとつ前の形 (`ext.write` で書き込みの形を選び、`ext` が無ければメンバー全員が書ける) で、
    書ける人が変わらないようにする: 全グループを `{"write": "listed", "writers": [...]}` にする
    (`writers` のメンバーだけ書ける。`ext` か `writers` が無ければ `[]`)。"""
    connection = op.get_bind()
    rows = connection.execute(sa.text('SELECT id, data FROM "group"')).fetchall()
    for row in rows:
        data = migrate_down_data(_load(row.data))
        connection.execute(
            sa.text('UPDATE "group" SET data = :data WHERE id = :id'),
            {"data": json.dumps(data, ensure_ascii=False), "id": row.id},
        )
