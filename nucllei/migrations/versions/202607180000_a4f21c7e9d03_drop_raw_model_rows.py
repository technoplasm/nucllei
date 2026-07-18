"""生モデル直接編集の産物(base_model_id が NULL の Models 行)を一掃(#82)

Models 行は base_model_id 必須(= preset のみ)になった。旧経路(upstream の
Settings > Models)で生モデル id に作られた base 無し行は、/api/models のフィルタを
通過して生モデルを普通のモデルとして露出させ続けるゴミデータなので削除する。
preset(base_model_id あり)には触れない。

Revision ID: a4f21c7e9d03
Revises: ecb3c3c42379
Create Date: 2026-07-18

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a4f21c7e9d03"
down_revision: Union[str, None] = "ecb3c3c42379"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM model WHERE base_model_id IS NULL")


def downgrade() -> None:
    # データ削除の migration — 復元不能(旧経路自体が廃止済みなので戻す意味も無い)
    pass
