"""チャットフォルダ機能の撤去 — folder テーブルと chat.folder_id を drop

フォルダは upstream 残骸で feature-triage 未掲載・UI に作成導線も無い
半死に機能だったため、機能ごと撤去(FE/BE/API 同時 — 2026-07-19 決定)。
フォルダ所属チャットは folder_id が消えるだけで本体は残る(未分類に戻る)。

Revision ID: f3d7a91c5b24
Revises: a4f21c7e9d03
Create Date: 2026-07-19

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f3d7a91c5b24"
down_revision: Union[str, None] = "a4f21c7e9d03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("folder")
    # sqlite の drop column は batch mode(テーブル再作成)で行う
    with op.batch_alter_table("chat") as batch_op:
        batch_op.drop_column("folder_id")


def downgrade() -> None:
    # 機能ごと撤去 — 戻さない
    pass
