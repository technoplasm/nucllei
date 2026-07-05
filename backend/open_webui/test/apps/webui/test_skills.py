"""Skill エンティティ(issue #35 step1 backend)の回帰テスト。

カバー範囲:
- skills CRUD(API 経由)/ command 一意性 / `/` 正規化
- 権限ゲート: workspace.skills 無しの非 admin は作成不可 /
  tool_ids 非空 skill は非 admin(workspace.skills 有りでも)作成不可(admin 専用ゲート)
- has_access による read フィルタ(group_terms 交差を含む)
- Alembic 移行の prompt→skill 複写(copy_prompts_to_skills を直接駆動)
- `/command` サーバーサイド発火(apply_skill_command): 展開 + 後続テキスト保持 / 未知 command 素通し
"""

import types

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestSkills(AbstractPostgresTest):
    BASE_PATH = "/api/v1/skills"

    def test_skills_crud(self):
        # 空
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        assert len(response.json()) == 0

        # 作成(先頭 `/` 付きで送っても正規化されて格納される)
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "command": "/mycmd",
                    "title": "My Skill",
                    "content": "本文",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["command"] == "mycmd"  # `/` strip 済み
        assert data["tool_ids"] == []
        assert data["description"] is None
        assert data["created_at"] == data["updated_at"]

        # command 一意性: 同じ command(`/` 有無に関わらず)は 400
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"command": "mycmd", "title": "dup", "content": "x"},
            )
        assert response.status_code == 400

        # get by command(`/` 無しのパスで引ける)
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/command/mycmd"))
        assert response.status_code == 200
        assert response.json()["title"] == "My Skill"

        # 更新
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/command/mycmd/update"),
                json={
                    "command": "ignored",
                    "title": "Updated",
                    "content": "新本文",
                    "description": "モデル向け説明",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated"
        assert data["content"] == "新本文"
        assert data["description"] == "モデル向け説明"

        # list
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/list"))
        assert response.status_code == 200
        assert len(response.json()) == 1

        # 削除
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.delete(
                self.create_url("/command/mycmd/delete")
            )
        assert response.status_code == 200
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert len(response.json()) == 0

    def test_non_admin_without_permission_cannot_create(self):
        # 既定の workspace.skills は False。グループ権限も無い一般ユーザーは作成不可。
        with mock_webui_user(id="9", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"command": "nope", "title": "t", "content": "c"},
            )
        assert response.status_code == 401

    def test_workspace_skills_permission_allows_template_but_not_tool_skill(self):
        from open_webui.models.groups import GroupForm, GroupUpdateForm, Groups
        from open_webui.models.users import Users

        Users.insert_new_user(
            id="7", name="member", email="m@hospital.example", role="user"
        )
        group = Groups.insert_new_group(
            "1",
            GroupForm(
                name="skill-workspace",
                description="",
                permissions={"workspace": {"skills": True}},
            ),
        )
        Groups.update_group_by_id(
            group.id,
            GroupUpdateForm(
                name=group.name, description=group.description, user_ids=["7"]
            ),
        )

        # workspace.skills 有りなので tool_ids 空の template skill は作成できる
        with mock_webui_user(id="7", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"command": "tmpl", "title": "t", "content": "c"},
            )
        assert response.status_code == 200

        # tool_ids 非空は admin 専用ゲート。workspace.skills 有りでも非 admin は不可。
        with mock_webui_user(id="7", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "command": "tooly",
                    "title": "t",
                    "content": "c",
                    "tool_ids": ["some-tool"],
                },
            )
        assert response.status_code == 401

        # admin なら tool_ids 非空でも作成できる
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "command": "tooly",
                    "title": "t",
                    "content": "c",
                    "tool_ids": ["some-tool"],
                },
            )
        assert response.status_code == 200
        assert response.json()["tool_ids"] == ["some-tool"]

    def test_read_visibility_with_group_terms(self):
        """read フィルタ: group_terms(診療科×職位の交差)で可視性が決まる。"""
        from open_webui.models.groups import GroupForm, GroupUpdateForm, Groups
        from open_webui.models.skills import SkillForm, Skills
        from open_webui.models.users import Users

        def make_group(name):
            return Groups.insert_new_group("1", GroupForm(name=name, description="")).id

        dept_naika = make_group("dept:内科")
        rank_bucho = make_group("rank:部長")

        for uid in ("naika-bucho", "naika-ippan"):
            Users.insert_new_user(
                id=uid, name=uid, email=f"{uid}@hospital.example", role="user"
            )

        def set_members(gid, uids):
            g = Groups.get_group_by_id(gid)
            Groups.update_group_by_id(
                gid,
                GroupUpdateForm(name=g.name, description=g.description, user_ids=uids),
            )

        set_members(dept_naika, ["naika-bucho", "naika-ippan"])
        set_members(rank_bucho, ["naika-bucho"])

        # 「内科 かつ 部長」のみ read 可視な skill を admin が作成
        Skills.insert_new_skill(
            "1",
            SkillForm(
                command="secret",
                title="secret",
                content="秘匿",
                access_control={"read": {"group_terms": [[dept_naika, rank_bucho]]}},
            ),
        )

        # 内科部長は見える、内科一般は見えない
        assert any(
            s.command == "secret"
            for s in Skills.get_skills_by_user_id("naika-bucho", "read")
        )
        assert not any(
            s.command == "secret"
            for s in Skills.get_skills_by_user_id("naika-ippan", "read")
        )

    def test_migration_copy_prompts_to_skills(self):
        """Alembic 移行の複写ロジックを直接駆動して prompt→skill を検証。"""
        from open_webui.internal.db import Session
        from open_webui.models.prompts import PromptForm, Prompts
        from open_webui.models.skills import Skills
        from open_webui.migrations.versions.f8a4d0e2c1b7_add_skill_table import (
            copy_prompts_to_skills,
        )

        Prompts.insert_new_prompt(
            "42",
            PromptForm(
                command="/legacy",
                title="Legacy",
                content="旧テンプレート",
                access_control=None,
            ),
        )
        seeded = Prompts.get_prompt_by_command("/legacy")
        assert seeded is not None

        copy_prompts_to_skills(Session.connection())
        Session.commit()

        skill = Skills.get_skill_by_command("legacy")  # `/` strip 済み
        assert skill is not None
        assert skill.title == "Legacy"
        assert skill.content == "旧テンプレート"
        assert skill.user_id == "42"
        assert skill.description is None
        assert skill.tool_ids == []
        assert skill.created_at == seeded.timestamp
        assert skill.updated_at == seeded.timestamp

    def test_slash_command_injection(self):
        from open_webui.models.skills import SkillForm, Skills
        from open_webui.utils.middleware import apply_skill_command

        Skills.insert_new_skill(
            "1",
            SkillForm(
                command="mycmd",
                title="t",
                content="展開: {{USER_NAME}}",
                access_control=None,  # public read
            ),
        )

        user = types.SimpleNamespace(id="1", name="Taro")

        # ヒット: /command を content で置換し後続テキストは残す
        form_data = {"messages": [{"role": "user", "content": "/mycmd 追加テキスト"}]}
        result = apply_skill_command(form_data, user)
        assert result["messages"][-1]["content"] == "展開: Taro 追加テキスト"

        # 未知 command は素通し
        form_data = {"messages": [{"role": "user", "content": "/nope そのまま"}]}
        result = apply_skill_command(form_data, user)
        assert result["messages"][-1]["content"] == "/nope そのまま"

        # 行頭以外(先頭が `/` でない)は無反応
        form_data = {"messages": [{"role": "user", "content": "前置き /mycmd"}]}
        result = apply_skill_command(form_data, user)
        assert result["messages"][-1]["content"] == "前置き /mycmd"
