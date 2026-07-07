"""Tool 管理の skill 型一本化(issue #44)の回帰。

- title(name)/description は DB フィールドが唯一の真実源。空なら create/update を 400 で拒否。
- content 先頭の frontmatter(`\"\"\"title: ... description: ...\"\"\"`)は退役 —
  解析されず meta.manifest に載らない(表示名/説明は frontmatter で上書きされない)。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

TOOL_CONTENT = '''
class Tools:
    def hello(self) -> str:
        """say hello"""
        return "hi"
'''

# 先頭 frontmatter 付き。退役済みなので title/description/version は無視される想定。
TOOL_CONTENT_WITH_FRONTMATTER = '''"""
title: Frontmatter Title
description: from frontmatter
version: 9.9.9
requirements: this-should-not-be-pip-installed
"""


class Tools:
    def hello(self) -> str:
        """say hello"""
        return "hi"
'''


class TestToolsMetadata(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.tools import Tools

        cls.Tools = Tools

    def _create_body(self, id, name, description, content=TOOL_CONTENT):
        return {
            "id": id,
            "name": name,
            "content": content,
            "meta": {"description": description},
        }

    def test_create_requires_description(self):
        """description(真実源)が空だと create は 400。"""
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json=self._create_body("no_desc", "No Desc", ""),
            )
        assert res.status_code == 400
        assert self.Tools.get_tool_by_id("no_desc") is None

    def test_create_requires_name(self):
        """name(真実源)が空だと create は 400。"""
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json=self._create_body("no_name", "", "d"),
            )
        assert res.status_code == 400
        assert self.Tools.get_tool_by_id("no_name") is None

    def test_update_requires_description(self):
        """既存 tool を description 空で更新すると 400。"""
        with mock_webui_user(id="admin1", role="admin"):
            created = self.fast_api_client.post(
                self.create_url("/create"),
                json=self._create_body("upd_probe", "Upd", "d"),
            )
        assert created.status_code == 200

        body = self._create_body("upd_probe", "Upd", "")
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/id/upd_probe/update"), json=body
            )
        assert res.status_code == 400

    def test_frontmatter_is_not_parsed_into_manifest(self):
        """content の frontmatter は退役 — manifest に載らず、表示名/説明も上書きされない。"""
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json=self._create_body(
                    "fm_probe",
                    "Form Name",
                    "form description",
                    content=TOOL_CONTENT_WITH_FRONTMATTER,
                ),
            )
        assert res.status_code == 200

        row = self.Tools.get_tool_by_id("fm_probe")
        # llens(#45): frontmatter 退役の掃除で manifest フィールド自体が撤去された
        # (#44 では meta.manifest=={} を固定していたが、フィールドごと消えたので存在しない)。
        assert not hasattr(row.meta, "manifest")
        # 真実源はフォーム値(frontmatter の Frontmatter Title / from frontmatter ではない)
        assert row.name == "Form Name"
        assert row.meta.description == "form description"
