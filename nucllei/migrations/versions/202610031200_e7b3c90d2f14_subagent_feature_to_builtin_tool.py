"""subagent feature flag to builtin tool

llens(#140): run_subagent は Tools テーブルの builtin `builtin_subagent` になった
(ACL が付く — ユーザー/グループ単位で止められる)。preset の機能フラグ
`meta.features.subagent`(既定 ON)は「`builtin_subagent` を `meta.toolIds` に
入れるか」に吸収して撤去する:

- フラグが False でなく(キー不在 = 既定 ON を含む)かつ `meta.subagentPresetIds` が
  非空の preset は、これまで run_subagent が出ていた — `meta.toolIds` に
  `builtin_subagent` を足して挙動を保つ(重複は足さない)
- 全 preset から `meta.features.subagent` を消す(空になった `features` も消す)

フラグ OFF の preset は toolIds に足さない(= 出ないまま)。subagentPresetIds は
残す(委任先 allowlist の層として現役)。

Revision ID: e7b3c90d2f14
Revises: d2a6f4b81c3e
Create Date: 2026-10-03 12:00:00

"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "e7b3c90d2f14"
down_revision: Union[str, None] = "d2a6f4b81c3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUBAGENT_TOOL_ID = "builtin_subagent"


def migrate_meta(meta: dict) -> bool:
    """1 preset の meta を新形へ(in-place)。変更したら True。テストから直接呼ぶ。"""
    changed = False
    features = meta.get("features")
    features = features if isinstance(features, dict) else None
    flag_on = features is None or features.get("subagent", True) is not False

    preset_ids = meta.get("subagentPresetIds")
    if flag_on and isinstance(preset_ids, list) and preset_ids:
        tool_ids = meta.get("toolIds")
        tool_ids = list(tool_ids) if isinstance(tool_ids, list) else []
        if SUBAGENT_TOOL_ID not in tool_ids:
            tool_ids.append(SUBAGENT_TOOL_ID)
            meta["toolIds"] = tool_ids
            changed = True

    if features is not None and "subagent" in features:
        features.pop("subagent")
        if not features:
            meta.pop("features")
        changed = True
    return changed


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, meta FROM model")).fetchall()
    for row in rows:
        raw = row.meta
        if raw is None:
            continue
        meta = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(meta, dict):
            continue
        if migrate_meta(meta):
            connection.execute(
                sa.text("UPDATE model SET meta = :meta WHERE id = :id"),
                {"meta": json.dumps(meta, ensure_ascii=False), "id": row.id},
            )


def downgrade() -> None:
    # 旧データ互換を持たない方針(CLAUDE.md)。フラグの復元は不要 — キー不在が
    # 旧コードでの既定 ON なので、toolIds に残った builtin_subagent も旧コードは
    # 未定義 id として無視する。no-op。
    pass
