"""GHSA-p4fx-23fq-jfg6(tool 更新エンドポイントの権限欠落 → ソース書換 RCE)の回帰。

作成は workspace.tools 権限を要求するのに、更新は write アクセスだけで通り、
write 共有された非 admin(workspace.tools 権限なし)が tool の Python ソースを
差し替えてコード実行に至れた。content 書換には作成と同じ権限を要求する。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

TOOL_CONTENT = '''
class Tools:
    def hello(self) -> str:
        """say hello"""
        return "hi"
'''

TOOL_CONTENT_V2 = '''
class Tools:
    def hello(self) -> str:
        """say hello v2"""
        return "hi2"
'''

# owner=user "1"、write/read を非 owner 非 admin の user "2" に付与。
AC = {
    "read": {"group_ids": [], "user_ids": ["2"]},
    "write": {"group_ids": [], "user_ids": ["2"]},
}


class TestToolsUpdateAuthz(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.tools import Tools

        cls.Tools = Tools

    def _insert_tool(self):
        from open_webui.models.tools import ToolForm, ToolMeta

        form = ToolForm(
            id="authz_probe",
            name="probe",
            content=TOOL_CONTENT,
            meta=ToolMeta(description="d", manifest={}),
            access_control=AC,
        )
        self.Tools.insert_new_tool("1", form, [])

    def _update_body(self, name, content):
        return {
            "id": "authz_probe",
            "name": name,
            "content": content,
            "meta": {"description": "d", "manifest": {}},
            "access_control": AC,
        }

    def test_write_grantee_cannot_change_source(self):
        """write 共有だけの非 admin(workspace.tools なし)は content を書き換えられない。"""
        self._insert_tool()
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.post(
                self.create_url("/id/authz_probe/update"),
                json=self._update_body("probe", TOOL_CONTENT_V2),
            )
        assert r.status_code == 401

    def test_write_grantee_can_edit_metadata(self):
        """content 不変(メタデータのみ)の更新は write アクセスで許可。"""
        self._insert_tool()
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.post(
                self.create_url("/id/authz_probe/update"),
                json=self._update_body("probe RENAMED", TOOL_CONTENT),
            )
        assert r.status_code == 200
        assert r.json()["name"] == "probe RENAMED"

    def test_admin_can_change_source(self):
        """admin は content を書き換えられる。"""
        self._insert_tool()
        with mock_webui_user(id="9", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/id/authz_probe/update"),
                json=self._update_body("probe", TOOL_CONTENT_V2),
            )
        assert r.status_code == 200

    def test_valves_read_requires_access(self):
        """GHSA-7429: 管理者 valve(秘密を含みうる)の読み取りに access 制御を適用。

        private tool の /valves を非 owner 非 admin が読むと 401、owner は 200。
        """
        self._insert_tool()  # owner=user "1" だが access_control=AC(user "2" に write 付与)
        from open_webui.models.tools import ToolForm, ToolMeta

        # owner のみ(access_control={})の別 tool を用意して純粋な拒否を確認
        self.Tools.insert_new_tool(
            "1",
            ToolForm(
                id="valve_probe",
                name="vp",
                content=TOOL_CONTENT,
                meta=ToolMeta(description="d", manifest={}),
                access_control={},
            ),
            [],
        )
        with mock_webui_user(id="2", role="user"):
            denied = self.fast_api_client.get(self.create_url("/id/valve_probe/valves"))
        assert denied.status_code == 401

        with mock_webui_user(id="1", role="user"):
            allowed = self.fast_api_client.get(
                self.create_url("/id/valve_probe/valves")
            )
        assert allowed.status_code == 200

    def test_get_tools_skips_inaccessible_tool(self):
        """GHSA-4pcg: リクエスト由来の tool_id でも read 権限が無ければ get_tools が無視する。

        private(access_control={})の他人所有 tool を tool_ids に渡しても、
        ユーザーには露出しない(module ロード前にスキップされる)。specs を実関数付きに
        して、ガードが無ければ result に載る = ガードが効いているときだけ空になる状態にする。
        """
        import types
        from open_webui.models.tools import ToolForm, ToolMeta
        from open_webui.utils.tools import get_tools

        hello_spec = {
            "name": "hello",
            "description": "say hello",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
        # owner=user "1"、private(所有者のみ)。specs は実在関数 hello に対応。
        self.Tools.insert_new_tool(
            "1",
            ToolForm(
                id="secret_tool",
                name="secret",
                content=TOOL_CONTENT,
                meta=ToolMeta(description="d", manifest={}),
                access_control={},
            ),
            [hello_spec],
        )

        request = types.SimpleNamespace(
            app=types.SimpleNamespace(state=types.SimpleNamespace(TOOLS={}))
        )
        # 権限の無い user "2": スキップされ空
        denied = get_tools(
            request, ["secret_tool"], types.SimpleNamespace(id="2", role="user"), {}
        )
        assert denied == {}

        # 所有者 user "1": アクセスできるので hello が載る(granted パスの担保)
        granted = get_tools(
            request,
            ["secret_tool"],
            types.SimpleNamespace(id="1", role="user"),
            {"__user__": {"id": "1"}},
        )
        assert "hello" in granted
