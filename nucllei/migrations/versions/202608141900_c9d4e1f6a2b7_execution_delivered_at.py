"""execution delivered_at

llens(#110): 配達済みカラム。バックグラウンドタスクの結果配達はサーバ駆動
(execution_resume — タスク終了時に BE が親 chat へ通知メッセージを書き、続きターンを
起動する)で、delivered_at は「結果がモデルの文脈に載った」事実を持つ:
notice が履歴に載った / await_task・stop_task が terminal 形を返した /
配達先が消えた(chat 削除 = 恒久断念)。一度立てたら戻さない(FE の履歴推測による
handled 判定を廃止する土台 — 契約 T3)。

Revision ID: c9d4e1f6a2b7
Revises: 7e2c41a80d95
Create Date: 2026-08-14 19:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import nucllei.internal.db

# revision identifiers, used by Alembic.
revision: str = "c9d4e1f6a2b7"
down_revision: Union[str, None] = "7e2c41a80d95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "execution", sa.Column("delivered_at", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("execution", "delivered_at")
