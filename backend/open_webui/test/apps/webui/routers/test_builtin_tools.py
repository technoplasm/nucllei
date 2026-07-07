"""組み込みツール機構(issue #42)の回帰。

- T1: 起動時 sync で builtin 行ができ、get_tools が repo モジュールから callable を返す
- T2: content 更新・削除・id の横取り(create)は拒否。access_control のみ更新可
- T5: builtin 行にも access_control(4pcg ガード)が効く
"""

import types

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


def _fake_request():
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(TOOLS={}))
    )


class TestBuiltinTools(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.tools import Tools

        cls.Tools = Tools

    def _sync(self):
        from open_webui.builtin_tools import sync_builtin_tools

        sync_builtin_tools()

    def test_sync_creates_builtin_row_and_get_tools_loads_module(self):
        from open_webui.utils.tools import get_tools

        self._sync()

        row = self.Tools.get_tool_by_id("builtin_history")
        assert row is not None
        assert row.meta.builtin is True
        assert row.content == ""
        spec_names = {s["name"] for s in row.specs}
        assert spec_names == {"list_history", "read_history", "grep_history"}

        # get_tools は DB 行 → レジストリのモジュールへ振り向く(content は使わない)
        loaded = get_tools(
            _fake_request(),
            ["builtin_history"],
            types.SimpleNamespace(id="u1", role="user"),
            {"__user__": {"id": "u1"}},
        )
        assert set(loaded.keys()) == {"list_history", "read_history", "grep_history"}
        assert loaded["list_history"]["spec"]["name"] == "list_history"

    def test_sync_is_idempotent_and_refreshes_from_repo(self):
        self._sync()
        # 手で specs を汚しても再 sync で repo から復元される
        self.Tools.update_tool_by_id(
            "builtin_history", {"specs": [], "content": "tampered"}
        )
        self._sync()
        row = self.Tools.get_tool_by_id("builtin_history")
        assert row.content == ""
        assert len(row.specs) == 3

    def test_update_allows_only_access_control(self):
        self._sync()
        body = {
            "id": "builtin_history",
            "name": "乗っ取り",
            "description": "hacked",
            "content": "class Tools:\n    def pwn(self):\n        return 'x'",
            "meta": {"builtin": False},
            "access_control": {},
        }
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/id/builtin_history/update"), json=body
            )
        assert res.status_code == 200

        row = self.Tools.get_tool_by_id("builtin_history")
        # 定義は repo が真実源のまま
        assert row.content == ""
        assert row.name == "履歴管理"
        assert row.meta.builtin is True
        # access_control だけ反映される
        assert row.access_control == {}

    def test_delete_and_create_are_rejected(self):
        self._sync()
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.delete(
                self.create_url("/id/builtin_history/delete")
            )
        assert res.status_code == 400

        # 予約 id の create も拒否(sync 前の空 DB でも横取りできない)
        self.Tools.delete_tool_by_id("builtin_history")
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "builtin_history",
                    "name": "偽物",
                    "description": "d",
                    "content": "class Tools:\n    def fake(self):\n        return 'x'",
                },
            )
        assert res.status_code == 400

    def test_access_control_guards_builtin_row(self):
        """4pcg 同型: リクエスト由来 id は builtin 行でも read 検証される(T5)。"""
        from open_webui.utils.tools import get_tools

        self._sync()
        # 私有化(owner="system" のみ) → 一般ユーザーには載らない
        self.Tools.update_tool_by_id("builtin_history", {"access_control": {}})

        denied = get_tools(
            _fake_request(),
            ["builtin_history"],
            types.SimpleNamespace(id="u2", role="user"),
            {"__user__": {"id": "u2"}},
        )
        assert denied == {}

        # admin はバイパス(既存セマンティクス)
        granted = get_tools(
            _fake_request(),
            ["builtin_history"],
            types.SimpleNamespace(id="a1", role="admin"),
            {"__user__": {"id": "a1"}},
        )
        assert "list_history" in granted
