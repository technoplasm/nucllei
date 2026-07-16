"""テストセッション全体の DB セットアップ。

nucllei パッケージは import した時点の DATABASE_URL で DB エンジンと
migration が確定する(config.py / internal/db.py の import 副作用)。
pytest は collection 時に全テストモジュールを import するため、
nucllei をモジュールレベルで import するテスト(storage/test_provider.py 等)より
前に DATABASE_URL を postgres へ向けておく必要がある。

conftest.py はテストモジュールの import より先に読み込まれるので、
ここで(モジュールレベルで)postgres コンテナの起動と env 設定を行う。
コンテナはセッション全体で 1 つを共有し、プロセス終了時に atexit で削除する。
"""

import atexit
import logging
import os
import time

import docker
from pytest_docker.plugin import get_docker_ip
from sqlalchemy import create_engine

log = logging.getLogger(__name__)

DOCKER_CONTAINER_NAME = "postgres-test-container-will-get-deleted"
POSTGRES_PORT = 8081
POSTGRES_ENV = {
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "example",
    "POSTGRES_DB": "nucllei",
}


def _database_url() -> str:
    host = get_docker_ip()
    user = POSTGRES_ENV["POSTGRES_USER"]
    pw = POSTGRES_ENV["POSTGRES_PASSWORD"]
    db = POSTGRES_ENV["POSTGRES_DB"]
    return f"postgresql://{user}:{pw}@{host}:{POSTGRES_PORT}/{db}"


def _remove_container():
    try:
        client = docker.from_env()
        client.containers.get(DOCKER_CONTAINER_NAME).remove(force=True)
    except Exception:
        pass


def _ensure_postgres() -> str:
    client = docker.from_env()
    try:
        container = client.containers.get(DOCKER_CONTAINER_NAME)
        if container.status != "running":
            container.remove(force=True)
            raise docker.errors.NotFound("container not running")
    except docker.errors.NotFound:
        client.containers.run(
            "postgres:16.2",
            detach=True,
            environment=POSTGRES_ENV,
            name=DOCKER_CONTAINER_NAME,
            ports={5432: ("0.0.0.0", POSTGRES_PORT)},
            command="postgres -c log_statement=all",
        )
        atexit.register(_remove_container)

    database_url = _database_url()
    engine = create_engine(database_url, pool_pre_ping=True)
    last_error = None
    for _ in range(30):
        try:
            with engine.connect():
                engine.dispose()
                return database_url
        except Exception as e:
            last_error = e
            time.sleep(1)
    engine.dispose()
    raise RuntimeError(f"postgres コンテナに接続できません: {last_error}")


os.environ["DATABASE_URL"] = _ensure_postgres()
