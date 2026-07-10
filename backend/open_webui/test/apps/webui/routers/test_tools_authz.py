"""GHSA-p4fx-23fq-jfg6(tool 更新エンドポイントの権限欠落 → ソース書換 RCE)の回帰。

元は「write 共有だけの非 admin がソースを差し替えられる」穴だった。
#66 の3層化で tool の定義変更・valves は admin 専有になり、境界はさらに単純化
(write ACL 自体が廃止)。非 admin は content どころか更新自体ができない。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict

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

# owner=user "1"、read(使用権限)を非 owner 非 admin の user "2" に付与。
# (#66: ACL は read 一本。write 側は廃止済み)
AC = {
    "read": {"group_ids": [], "user_ids": ["2"]},
}


class TestToolsUpdateAuthz(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.tools import Tools

        cls.Tools = Tools

    def _insert_tool(self):
        from open_webui.models.tools import ToolForm

        form = ToolForm(
            id="authz_probe",
            name="probe",
            description="d",
            content=TOOL_CONTENT,
            access_control=AC,
        )
        self.Tools.insert_new_tool("1", form, [])

    def _update_body(self, name, content):
        return {
            "id": "authz_probe",
            "name": name,
            "description": "d",
            "content": content,
            "access_control": AC,
        }

    def test_non_admin_cannot_update_at_all(self):
        """#66: 非 admin は content どころか更新エンドポイント自体に到達できない。"""
        self._insert_tool()
        with mock_webui_user_strict(id="2", role="user"):
            r = self.fast_api_client.post(
                self.create_url("/id/authz_probe/update"),
                json=self._update_body("probe", TOOL_CONTENT_V2),
            )
            assert r.status_code == 401

            # メタデータのみ(content 不変)でも同様に 401
            r = self.fast_api_client.post(
                self.create_url("/id/authz_probe/update"),
                json=self._update_body("probe RENAMED", TOOL_CONTENT),
            )
            assert r.status_code == 401

    def test_admin_can_change_source(self):
        """admin は content を書き換えられる。"""
        self._insert_tool()
        with mock_webui_user(id="9", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/id/authz_probe/update"),
                json=self._update_body("probe", TOOL_CONTENT_V2),
            )
        assert r.status_code == 200

    def test_valves_read_is_admin_only(self):
        """GHSA-7429 / #66: 管理者 valve(秘密を含みうる)の読み取りは admin 専有。

        #66 で owner 概念ごと廃止 — owner(user "1")でも非 admin なら 401。
        """
        from open_webui.models.tools import ToolForm

        self.Tools.insert_new_tool(
            "1",
            ToolForm(
                id="valve_probe",
                name="vp",
                description="d",
                content=TOOL_CONTENT,
                access_control={},
            ),
            [],
        )
        for uid in ("2", "1"):  # 非 owner も owner も同じく拒否
            with mock_webui_user_strict(id=uid, role="user"):
                denied = self.fast_api_client.get(
                    self.create_url("/id/valve_probe/valves")
                )
            assert denied.status_code == 401

        with mock_webui_user(id="9", role="admin"):
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
        from open_webui.models.tools import ToolForm
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
                description="d",
                content=TOOL_CONTENT,
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
