"""Skill エンティティ(issue #35 step1 backend)の回帰テスト。

カバー範囲:
- skills CRUD(API 経由)/ command 一意性 / `/` 正規化
- 権限ゲート: workspace.skills 無しの非 admin は作成不可 /
  tool_ids 非空 skill は非 admin(workspace.skills 有りでも)作成不可(admin 専用ゲート)
- has_access による read フィルタ(group_terms 交差を含む)
- `/command` サーバーサイド発火(apply_skill_command): 展開 + 後続テキスト保持 / 未知 command 素通し
- 全 user メッセージ走査(会話持続、2026-07-06 改訂)/ 不可視 skill の素通し
- load_skill 擬似 tool 合成(#35 step2a): skillIds ∩ 可視 ∩ description 有り /
  manifest 識別子は command 一本 / callable が展開済み content を返す
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

    # NOTE: prompt→skill 複写(f8a4d0e2c1b7)のテストは prompts 退役
    # (prompt テーブル drop = c2d9a7b41e05)に伴い削除した。複写は履歴上の
    # 一度きりの処理で、head に prompt テーブルが存在しないため駆動できない。

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

    def test_slash_command_multi_turn_scan(self):
        """全 user メッセージ走査(会話持続)— 過去ターンの `/command` も毎リクエスト展開される。"""
        from open_webui.models.skills import SkillForm, Skills
        from open_webui.utils.middleware import apply_skill_command

        Skills.insert_new_skill(
            "1",
            SkillForm(command="multia", title="t", content="指示A", access_control=None),
        )
        Skills.insert_new_skill(
            "1",
            SkillForm(command="multib", title="t", content="指示B", access_control=None),
        )

        user = types.SimpleNamespace(id="1", name="Taro")

        form_data = {
            "messages": [
                {"role": "user", "content": "/multia 最初の依頼"},
                {"role": "assistant", "content": "了解"},
                {"role": "user", "content": "続きをやって"},
                {"role": "user", "content": "/multib 追加で"},
            ]
        }
        result = apply_skill_command(form_data, user)
        # 1 ターン目の /command も展開される(揮発しない)
        assert result["messages"][0]["content"] == "指示A 最初の依頼"
        # command の無いメッセージはそのまま
        assert result["messages"][2]["content"] == "続きをやって"
        # 複数 skill は和集合(それぞれの位置で展開)
        assert result["messages"][3]["content"] == "指示B 追加で"

    def test_slash_command_invisible_skill_passes_through(self):
        """他人の私有 skill(access_control={})は発火せず素通し。"""
        from open_webui.models.skills import SkillForm, Skills
        from open_webui.utils.middleware import apply_skill_command

        Skills.insert_new_skill(
            "owner",
            SkillForm(command="secretcmd", title="t", content="秘匿", access_control={}),
        )

        user = types.SimpleNamespace(id="someone-else", name="X")
        form_data = {"messages": [{"role": "user", "content": "/secretcmd hi"}]}
        result = apply_skill_command(form_data, user)
        assert result["messages"][0]["content"] == "/secretcmd hi"

    def test_build_load_skill_tool(self):
        """load_skill 合成(#35 step2a): skillIds ∩ 可視 ∩ description 有りのみ manifest に載る。"""
        import asyncio

        from open_webui.models.skills import SkillForm, Skills
        from open_webui.utils.middleware import build_load_skill_tool

        s1 = Skills.insert_new_skill(
            "1",
            SkillForm(
                command="scales",
                title="評価スケール",
                description="臨床評価スケールの参照",
                content="スケール表: {{USER_NAME}} 用",
                access_control=None,
            ),
        )
        s2 = Skills.insert_new_skill(  # description 無し → モデルに露出しない
            "1",
            SkillForm(command="nodesc", title="t", content="c", access_control=None),
        )
        s3 = Skills.insert_new_skill(  # 他人の私有 → 不可視
            "owner",
            SkillForm(
                command="hiddencmd",
                title="t",
                description="見えない",
                content="c",
                access_control={},
            ),
        )

        user = types.SimpleNamespace(id="7", name="Hanako")
        model = {"info": {"meta": {"skillIds": [s1.id, s2.id, s3.id, "no-such-id"]}}}

        tool = build_load_skill_tool(model, user)
        assert tool is not None
        spec = tool["spec"]
        assert spec["name"] == "load_skill"
        # manifest 識別子は command 一本(id/name は露出させない)
        assert spec["parameters"]["properties"]["skill_command"]["enum"] == ["scales"]
        assert "- scales: 臨床評価スケールの参照" in spec["description"]

        # 発火: tool result = 展開済み content(activate_skill 経由)
        result = asyncio.run(tool["callable"](skill_command="scales"))
        assert result == "スケール表: Hanako 用"

        # 未知 command はモデル向けエラーメッセージ(例外にしない)
        result = asyncio.run(tool["callable"](skill_command="nope"))
        assert "not found" in result

    def test_build_load_skill_tool_none_when_empty(self):
        """対象 skill が無ければ load_skill 自体を載せない(None)。"""
        from open_webui.utils.middleware import build_load_skill_tool

        user = types.SimpleNamespace(id="1", name="A")
        assert build_load_skill_tool({}, user) is None
        assert build_load_skill_tool({"info": {"meta": {}}}, user) is None
        assert (
            build_load_skill_tool({"info": {"meta": {"skillIds": ["missing"]}}}, user)
            is None
        )
