"""Postgres の接続は既定でプールを使い回す(問い合わせのたびに接続を張り直さない)。"""

import os

import pytest
from sqlalchemy.pool import QueuePool


@pytest.mark.skipif(
    any(
        os.environ.get(name)
        for name in ("DATABASE_POOL_SIZE", "DATABASE_POOL_MAX_OVERFLOW")
    ),
    reason="プールの env が設定されている",
)
def test_postgres_engine_pools_connections_by_default():
    from nucllei.internal.db import engine

    assert engine.dialect.name == "postgresql"
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 10
    # 10 本を超える分は上限なしで張る(使い切っても待たせない)
    assert engine.pool._max_overflow == -1

    # 返した接続は閉じずにプールに残る
    with engine.connect():
        pass
    assert engine.pool.checkedin() >= 1
