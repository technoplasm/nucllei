"""権限3層化(issue #66、真実源 _memos/permissions.md)の回帰テスト。

3層 = admin(model/tool/function の定義変更)> skill editor(workspace.skills、
全 skill CRUD + use-ACL 編集)> user(read ACL の範囲で使うだけ)。

- admin 専有境界: model/tool の定義 CRUD・admin valves を user が叩くと 401
- skill editor: 他人の skill も編集・削除・ACL 変更できる / 非 editor は管理系 401
- tool_ids 付き skill は editor でも触れない(admin 専有 — GHSA-4pcg 境界)
- G1: features.image_generation のサーバー側 enforcement
- G2: task endpoint の model read ACL 検証(picker 外モデルの裏口を塞ぐ)
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict


def _make_editor(user_id: str):
    """workspace.skills(skill editor)権限を持つ group に user を入れる。"""
    from open_webui.models.groups import GroupForm, GroupUpdateForm, Groups
    from open_webui.models.users import Users

    Users.insert_new_user(
        id=user_id, name=user_id, email=f"{user_id}@hospital.example", role="user"
    )
    group = Groups.insert_new_group(
        "1",
        GroupForm(
            name=f"editors-{user_id}",
            description="",
            permissions={"workspace": {"skills": True}},
        ),
    )
    Groups.update_group_by_id(
        group.id,
        GroupUpdateForm(
            name=group.name, description=group.description, user_ids=[user_id]
        ),
    )


class TestModelAdminOnly(AbstractPostgresTest):
    BASE_PATH = "/api/v1/models"

    def _model_form(self, id="m1"):
        return {"id": id, "name": id, "meta": {}, "params": {}}

    def test_user_cannot_crud_models(self):
        with mock_webui_user_strict(id="9", role="user"):
            assert (
                self.fast_api_client.post(
                    self.create_url("/create"), json=self._model_form()
                ).status_code
                == 401
            )
            assert (
                self.fast_api_client.post(
                    self.create_url("/model/update?id=m1"), json=self._model_form()
                ).status_code
                == 401
            )
            assert (
                self.fast_api_client.delete(
                    self.create_url("/model/delete?id=m1")
                ).status_code
                == 401
            )
            assert (
                self.fast_api_client.post(
                    self.create_url("/model/toggle?id=m1")
                ).status_code
                == 401
            )

    def test_admin_can_crud_models(self):
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"), json=self._model_form()
            )
            assert response.status_code == 200
            assert (
                self.fast_api_client.delete(
                    self.create_url("/model/delete?id=m1")
                ).json()
                is True
            )

    def test_user_list_filtered_by_read_acl(self):
        from open_webui.models.models import ModelForm, Models
        from open_webui.models.users import Users

        Users.insert_new_user(id="u", name="u", email="u@h.example", role="user")
        # preset(base_model_id あり)のみが一覧対象
        Models.insert_new_model(
            ModelForm(
                id="pub",
                name="pub",
                base_model_id="base",
                meta={},
                params={},
                access_control=None,
            ),
            "1",
        )
        Models.insert_new_model(
            ModelForm(
                id="priv",
                name="priv",
                base_model_id="base",
                meta={},
                params={},
                access_control={},
            ),
            "1",
        )

        with mock_webui_user(id="u", role="user"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        ids = {m["id"] for m in response.json()}
        assert "pub" in ids
        assert "priv" not in ids


class TestToolAdminOnly(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def test_user_cannot_manage_tools(self):
        with mock_webui_user_strict(id="9", role="user"):
            # 管理一覧(content 込み)・CRUD・admin valves すべて 401
            assert (
                self.fast_api_client.get(self.create_url("/list")).status_code == 401
            )
            assert (
                self.fast_api_client.post(
                    self.create_url("/create"),
                    json={"id": "t1", "name": "t", "description": "d", "content": "x", "meta": {}},
                ).status_code
                == 401
            )
            assert (
                self.fast_api_client.post(
                    self.create_url("/id/t1/update"),
                    json={"id": "t1", "name": "t", "description": "d", "content": "x", "meta": {}},
                ).status_code
                == 401
            )
            assert (
                self.fast_api_client.delete(
                    self.create_url("/id/t1/delete")
                ).status_code
                == 401
            )
            assert (
                self.fast_api_client.get(self.create_url("/id/t1/valves")).status_code
                == 401
            )
            assert (
                self.fast_api_client.get(self.create_url("/id/t1")).status_code == 401
            )

    def test_picker_list_filtered_by_read_acl(self):
        """GET / は全ユーザー可だが read ACL(= 使える範囲)で絞られる。"""
        from open_webui.models.tools import ToolForm, Tools
        from open_webui.models.users import Users

        Users.insert_new_user(id="u", name="u", email="u@h.example", role="user")
        Tools.insert_new_tool(
            "1",
            ToolForm(id="pubtool", name="t", description="d", content="c", meta={}),
            [],
        )
        Tools.insert_new_tool(
            "1",
            ToolForm(
                id="privtool",
                name="t",
                description="d",
                content="c",
                meta={},
                access_control={},
            ),
            [],
        )

        with mock_webui_user(id="u", role="user"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        ids = {t["id"] for t in response.json()}
        assert "pubtool" in ids
        assert "privtool" not in ids


class TestSkillEditor(AbstractPostgresTest):
    BASE_PATH = "/api/v1/skills"

    def test_editor_edits_any_skill_including_acl(self):
        """editor は他人の skill を編集・ACL 変更・削除できる(共有レシピ帳)。"""
        from open_webui.models.skills import SkillForm, Skills

        _make_editor("ed")
        # 他人(admin)名義・非公開 ACL の skill
        Skills.insert_new_skill(
            "1",
            SkillForm(id="team", name="t", content="c", access_control={}),
        )

        with mock_webui_user_strict(id="ed", role="user"):
            # 管理一覧に全 skill が見える
            response = self.fast_api_client.get(self.create_url("/list"))
            assert response.status_code == 200
            assert {s["id"] for s in response.json()} == {"team"}

            # 中身 + use-ACL(read)を編集できる
            response = self.fast_api_client.post(
                self.create_url("/id/team/update"),
                json={
                    "id": "team",
                    "name": "renamed",
                    "content": "new",
                    "access_control": {"read": {"user_ids": ["someone"], "group_ids": []}},
                },
            )
            assert response.status_code == 200
            assert response.json()["name"] == "renamed"

            # 編集用の単体取得も可(read ACL 外でも editor なら開ける)
            assert (
                self.fast_api_client.get(self.create_url("/id/team")).status_code
                == 200
            )

            # 削除も可
            assert (
                self.fast_api_client.delete(
                    self.create_url("/id/team/delete")
                ).json()
                is True
            )

    def test_editor_cannot_touch_tool_skill(self):
        """tool_ids 付き skill は editor でも更新・削除・剥奪も不可(admin 専有)。"""
        from open_webui.models.skills import SkillForm, Skills

        _make_editor("ed2")
        Skills.insert_new_skill(
            "1",
            SkillForm(
                id="armed",
                name="t",
                content="c",
                tool_ids=["some-tool"],
                access_control=None,
            ),
        )

        with mock_webui_user_strict(id="ed2", role="user"):
            # tool_ids を外す更新(剥奪)も 401 — 既存 tool_ids でゲート
            response = self.fast_api_client.post(
                self.create_url("/id/armed/update"),
                json={"id": "armed", "name": "t", "content": "c", "tool_ids": []},
            )
            assert response.status_code == 401

            assert (
                self.fast_api_client.delete(
                    self.create_url("/id/armed/delete")
                ).status_code
                == 401
            )

    def test_non_editor_cannot_manage_skills(self):
        from open_webui.models.users import Users

        Users.insert_new_user(id="pl", name="pl", email="pl@h.example", role="user")
        with mock_webui_user_strict(id="pl", role="user"):
            assert (
                self.fast_api_client.get(self.create_url("/list")).status_code == 401
            )
            assert (
                self.fast_api_client.post(
                    self.create_url("/create"),
                    json={"id": "nope", "name": "t", "content": "c"},
                ).status_code
                == 401
            )


class TestImageGenerationGate(AbstractPostgresTest):
    """G1: features.image_generation のサーバー側 enforcement。"""

    BASE_PATH = "/api/v1/images"

    def test_user_without_permission_gets_401(self):
        from open_webui.main import app

        original = app.state.config.USER_PERMISSIONS
        app.state.config.USER_PERMISSIONS = {
            "features": {"image_generation": False}
        }
        try:
            with mock_webui_user(id="9", role="user"):
                response = self.fast_api_client.post(
                    self.create_url("/generations"),
                    json={"prompt": "a cat"},
                )
            assert response.status_code == 401
        finally:
            app.state.config.USER_PERMISSIONS = original


class TestTaskModelAccess(AbstractPostgresTest):
    """G2: task endpoint も model の read ACL を検証する。"""

    BASE_PATH = "/api/v1/tasks"

    def _setup_models(self):
        from open_webui.main import app
        from open_webui.models.models import ModelForm, Models

        Models.insert_new_model(
            ModelForm(id="hidden", name="h", meta={}, params={}, access_control={}),
            "1",
        )
        app.state.MODELS = {
            "hidden": {"id": "hidden", "owned_by": "openai"},
        }

    def test_unknown_model_404(self):
        from open_webui.main import app

        app.state.MODELS = {}
        with mock_webui_user(id="9", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/title/completions"),
                json={"model": "no-such", "messages": []},
            )
        assert response.status_code == 404

    def test_acl_hidden_model_404_for_user(self):
        self._setup_models()
        with mock_webui_user(id="9", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/title/completions"),
                json={"model": "hidden", "messages": []},
            )
        # 存在は明かさない(ACL 外 = 404)
        assert response.status_code == 404

    def test_admin_passes_acl_gate(self):
        self._setup_models()
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/title/completions"),
                json={"model": "hidden", "messages": []},
            )
        # ゲートは通過する(後段の生成は失敗してよい — 404 でないことが本質)
        assert response.status_code != 404
