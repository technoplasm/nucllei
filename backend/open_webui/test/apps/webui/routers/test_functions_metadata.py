"""Function(filter/action/pipe)管理の skill 型一本化(issue #45 — #44 の filter 版)の回帰。

- title(name)/description は DB フィールドが唯一の真実源。空なら create/update を 400 で拒否。
- content 先頭の frontmatter(`\"\"\"title: ... description: ...\"\"\"`)は退役 —
  解析されず、manifest フィールド自体が meta から撤去されている(表示名/説明も上書きされない)。
- type はコード中のクラス名(Filter/Action/Pipe)で確定する(フォーム側で二重定義しない)。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

FILTER_CONTENT = """
from pydantic import BaseModel
from typing import Optional


class Filter:
    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body
"""

ACTION_CONTENT = """
from pydantic import BaseModel
from typing import Optional


class Action:
    async def action(self, body: dict, __user__: Optional[dict] = None) -> Optional[dict]:
        return body
"""

PIPE_CONTENT = """
from pydantic import BaseModel


class Pipe:
    def pipe(self, body: dict) -> str:
        return "hi"
"""

# 先頭 frontmatter 付き。退役済みなので title/description/version/requirements は無視される想定。
FILTER_CONTENT_WITH_FRONTMATTER = '''"""
title: Frontmatter Title
description: from frontmatter
version: 9.9.9
requirements: this-should-not-be-pip-installed
"""

from pydantic import BaseModel
from typing import Optional


class Filter:
    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body
'''


class TestFunctionsMetadata(AbstractPostgresTest):
    BASE_PATH = "/api/v1/functions"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.functions import Functions

        cls.Functions = Functions

    def _create_body(self, id, name, description, content=FILTER_CONTENT):
        # llens(#43): description は top-level 列に昇格(旧 meta.description)。
        return {
            "id": id,
            "name": name,
            "description": description,
            "content": content,
        }

    def test_create_allows_empty_description(self):
        """description は任意 — 実行対象は per-model 割り当てで決まり、
        description は UI メタデータにすぎないため、空でも create は 200。"""
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json=self._create_body("no_desc", "No Desc", ""),
            )
        assert res.status_code == 200
        assert self.Functions.get_function_by_id("no_desc") is not None

    def test_create_requires_name(self):
        """name(真実源)が空だと create は 400。"""
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json=self._create_body("no_name", "", "d"),
            )
        assert res.status_code == 400
        assert self.Functions.get_function_by_id("no_name") is None

    def test_update_allows_empty_description(self):
        """既存 function を description 空で更新しても 200(description は任意)。"""
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
        assert res.status_code == 200
        assert self.Functions.get_function_by_id("upd_probe").description == ""

    def test_frontmatter_is_not_parsed_and_manifest_removed(self):
        """content の frontmatter は退役 — manifest フィールドは meta から撤去され、
        表示名/説明も上書きされない。"""
        with mock_webui_user(id="admin1", role="admin"):
            res = self.fast_api_client.post(
                self.create_url("/create"),
                json=self._create_body(
                    "fm_probe",
                    "Form Name",
                    "form description",
                    content=FILTER_CONTENT_WITH_FRONTMATTER,
                ),
            )
        assert res.status_code == 200

        row = self.Functions.get_function_by_id("fm_probe")
        # llens(#43): meta 列ごと撤去(#45 で manifest が消え description だけになったため)。
        assert not hasattr(row, "meta")
        # 真実源はフォーム値。description は top-level 列。
        assert row.name == "Form Name"
        assert row.description == "form description"

    def test_type_detected_from_class(self):
        """type はコード中のクラス名で確定する(filter/action/pipe)。"""
        cases = {
            "t_filter": ("filter", FILTER_CONTENT),
            "t_action": ("action", ACTION_CONTENT),
            "t_pipe": ("pipe", PIPE_CONTENT),
        }
        for fid, (expected_type, content) in cases.items():
            with mock_webui_user(id="admin1", role="admin"):
                res = self.fast_api_client.post(
                    self.create_url("/create"),
                    json=self._create_body(fid, fid, "d", content=content),
                )
            assert res.status_code == 200, res.text
            assert self.Functions.get_function_by_id(fid).type == expected_type
