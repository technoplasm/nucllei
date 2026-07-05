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
