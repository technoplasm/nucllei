import logging
import time

from fastapi.testclient import TestClient
from sqlalchemy import text


log = logging.getLogger(__name__)


def get_fast_api_client():
    # mock_user.py の dependency_overrides と同一の app インスタンスを共有するため、
    # トップレベル `main` ではなくパッケージ経由で import する。
    from nucllei.main import app

    with TestClient(app) as c:
        return c


class AbstractIntegrationTest:
    BASE_PATH = None

    def create_url(self, path="", query_params=None):
        if self.BASE_PATH is None:
            raise Exception("BASE_PATH is not set")
        parts = self.BASE_PATH.split("/")
        parts = [part.strip() for part in parts if part.strip() != ""]
        path_parts = path.split("/")
        path_parts = [part.strip() for part in path_parts if part.strip() != ""]
        query_parts = ""
        if query_params:
            query_parts = "&".join(
                [f"{key}={value}" for key, value in query_params.items()]
            )
            query_parts = f"?{query_parts}"
        url = "/" + "/".join(parts + path_parts)
        if not path_parts:
            # ルータ直下("" / "/")は末尾スラッシュ必須。スラッシュ無しだと
            # API ルートに一致せず SPA の catch-all(index.html)に吸われる。
            url += "/"
        return url + query_parts

    @classmethod
    def setup_class(cls):
        pass

    def setup_method(self):
        pass

    @classmethod
    def teardown_class(cls):
        pass

    def teardown_method(self):
        pass


class AbstractPostgresTest(AbstractIntegrationTest):
    """postgres を使う統合テストの基底クラス。

    postgres コンテナの起動と DATABASE_URL の設定は conftest.py が
    collection 前(nucllei の import 前)に済ませている。
    ここでは TestClient の生成とテスト間のデータ初期化のみを行う。
    """

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.fast_api_client = get_fast_api_client()

    def _check_db_connection(self):
        from nucllei.internal.db import Session

        retries = 10
        while retries > 0:
            try:
                Session.execute(text("SELECT 1"))
                Session.commit()
                break
            except Exception as e:
                Session.rollback()
                log.warning(e)
                time.sleep(3)
                retries -= 1

    def setup_method(self):
        super().setup_method()
        self._check_db_connection()

    def teardown_method(self):
        from nucllei.internal.db import Session

        # rollback everything not yet committed
        Session.commit()

        # スキーマは migration に追従して変わるため、固定リストではなく
        # 実在する public テーブルをすべて TRUNCATE する(管理系は除外)。
        skip = {"alembic_version", "migratehistory", "config"}
        tables = [
            row[0]
            for row in Session.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            if row[0] not in skip
        ]
        for table in tables:
            Session.execute(text(f'TRUNCATE TABLE "{table}" CASCADE'))
        Session.commit()
