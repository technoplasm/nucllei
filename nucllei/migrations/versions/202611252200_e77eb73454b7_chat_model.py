"""chat model

チャットのモデル id(chat JSON の models[0])を `chat.model` 列に持つ。admin のチャット閲覧の
モデル絞り込みとモデル別件数は、この列で引く — chat 列(会話全体の JSON)を全行読んで
パースしない。

- `chat.model`(String、NULL 可)を足す
- 既存行は chat JSON の models[0] で埋める(models が空・無い・先頭が文字列でない行は NULL —
  書き込み点の chat_model_of と同じ規則)

Revision ID: e77eb73454b7
Revises: 7c2e9a4b1d03
Create Date: 2026-11-25 22:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e77eb73454b7"
down_revision: Union[str, None] = "7c2e9a4b1d03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat", sa.Column("model", sa.String(), nullable=True))

    connection = op.get_bind()
    dialect_name = connection.dialect.name
    if dialect_name == "postgresql":
        connection.execute(sa.text("""
                UPDATE chat SET model = chat->'models'->>0
                WHERE json_typeof(chat->'models'->0) = 'string'
                """))
    elif dialect_name == "sqlite":
        connection.execute(sa.text("""
                UPDATE chat SET model = json_extract(chat, '$.models[0]')
                WHERE json_type(chat, '$.models[0]') = 'text'
                """))
    else:
        raise NotImplementedError(f"Unsupported dialect: {dialect_name}")


def downgrade() -> None:
    op.drop_column("chat", "model")
