"""RuntimeConfig の env/DB/既定 優先順位の回帰テスト(#30 是正)。

あるべき仕様(優先順位: 明示 env > DB(UI 保存)> 既定):
- env が非空(明示設定) → env を採用し、**起動時に DB へ書き込む**(env が正・DB を同期)。
- env が空 → DB の値(UI で保存した値)を採用(= 再起動後も残る)。
- DB も無い → hardcoded 既定値。

`798a7aa4c` は起動時に DB を一切見ず常に env/既定を採用していたため、env 未設定項目の
UI 保存値が再起動で必ず既定へ戻る不具合があった(config の DB 永続経路を叩くテストが
1本も無く、すり抜けていた)。本テストがその経路を固定する。
"""

import os
from unittest import mock

from test.util.abstract_integration_test import AbstractPostgresTest


class TestRuntimeConfigPriority(AbstractPostgresTest):
    BASE_PATH = "/api"

    ENV_NAME = "LLENS_TEST_CFG"
    CONFIG_PATH = "llens_test.cfg"

    def _make(self, default):
        import open_webui.config as cfg

        # 呼び出し側の現行様式に合わせる(env があればその値、無ければ既定)。
        return cfg.RuntimeConfig(
            self.ENV_NAME, self.CONFIG_PATH, os.environ.get(self.ENV_NAME, default)
        )

    def setup_method(self):
        super().setup_method()
        import open_webui.config as cfg

        self._saved_config_data = cfg.CONFIG_DATA
        os.environ.pop(self.ENV_NAME, None)

    def teardown_method(self):
        import open_webui.config as cfg

        cfg.CONFIG_DATA = self._saved_config_data
        cfg.reset_config()
        os.environ.pop(self.ENV_NAME, None)
        super().teardown_method()

    def test_env_set_wins_and_persists_to_db(self):
        """env 非空 → 実効値は env、かつ起動時に DB へ書き込まれる。"""
        import open_webui.config as cfg

        cfg.CONFIG_DATA = {}  # DB 空
        with mock.patch.dict(os.environ, {self.ENV_NAME: "from-env"}):
            rc = self._make("default-val")

        assert rc.value == "from-env"
        # env は起動時に DB(config テーブル / CONFIG_DATA)へ反映される
        assert cfg.get_config_value(self.CONFIG_PATH) == "from-env"

    def test_env_empty_uses_db_value(self):
        """env 空 → DB の保存値(UI 設定)を採用。再起動で消えない。"""
        import open_webui.config as cfg

        cfg.CONFIG_DATA = {"llens_test": {"cfg": "from-db"}}
        rc = self._make("default-val")

        assert rc.value == "from-db"

    def test_env_empty_and_no_db_uses_default(self):
        """env 空 かつ DB 無 → 既定値。"""
        import open_webui.config as cfg

        cfg.CONFIG_DATA = {}
        rc = self._make("default-val")

        assert rc.value == "default-val"
