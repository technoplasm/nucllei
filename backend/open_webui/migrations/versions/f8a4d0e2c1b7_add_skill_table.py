"""Add skill table and migrate prompt rows

Skill は Prompt の後継(issue #35)。prompt テーブルは移行期間中も残す
(旧 prompts router / フロントが参照するため)。詳細は `_memos/skill-design.md`。

Revision ID: f8a4d0e2c1b7
Revises: 3781e22d8b01
Create Date: 2026-07-05 00:00:00.000000

"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a4d0e2c1b7"
down_revision: Union[str, None] = "3781e22d8b01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def copy_prompts_to_skills(conn):
    """既存 prompt 行を skill へ複写する(移行の中核。テストから直接呼べるよう分離)。

    - command の先頭 `/` を strip
    - timestamp → created_at / updated_at 両方
    - id は新規 uuid、description=null、tool_ids=[]
    """
    prompt_table = sa.table(
        "prompt",
        sa.column("command", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("title", sa.Text()),
        sa.column("content", sa.Text()),
        sa.column("timestamp", sa.BigInteger()),
        sa.column("access_control", sa.JSON()),
    )

    skill_table = sa.table(
        "skill",
        sa.column("id", sa.String()),
        sa.column("command", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("title", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("content", sa.Text()),
        sa.column("tool_ids", sa.JSON()),
        sa.column("access_control", sa.JSON()),
        sa.column("created_at", sa.BigInteger()),
        sa.column("updated_at", sa.BigInteger()),
    )

    rows = conn.execute(sa.select(prompt_table)).mappings().all()
    for row in rows:
        command = (row["command"] or "").lstrip("/").strip()
        timestamp = row["timestamp"]
        conn.execute(
            skill_table.insert().values(
                id=str(uuid.uuid4()),
                command=command,
                user_id=row["user_id"],
                title=row["title"],
                description=None,
                content=row["content"],
                tool_ids=[],
                access_control=row["access_control"],
                created_at=timestamp,
                updated_at=timestamp,
            )
        )


def upgrade():
    op.create_table(
        "skill",
        sa.Column("id", sa.String(), nullable=False, primary_key=True, unique=True),
        # command は先頭 `/` なしで正規化して格納。
        sa.Column("command", sa.String(), nullable=True, unique=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_ids", sa.JSON(), nullable=True),
        sa.Column("access_control", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=True),
    )

    # prompt テーブルは移行期間中も残す(旧 prompts router / フロントが参照)。
    copy_prompts_to_skills(op.get_bind())


def downgrade():
    op.drop_table("skill")
