"""Skill エンティティ(issue #35 step1 backend)の回帰テスト。

カバー範囲:
- skills CRUD(API 経由)/ command 一意性 / `/` 正規化
- 権限ゲート: workspace.skills 無しの非 admin は作成不可 /
  tool_ids 非空 skill は非 admin(workspace.skills 有りでも)作成不可(admin 専用ゲート)
- has_access による read フィルタ(group 所属の OR 合成)
- `/command` サーバーサイド発火(apply_skill_command): user メッセージは生のまま、
  直後に合成 load_skill tool exchange(call + 展開済み content の result)を注入 /
  未知 command 素通し
- 全 user メッセージ走査(会話持続、2026-07-06 改訂)/ 不可視 skill の素通し
- load_skill 擬似 tool 合成(#35 step2a): skillIds ∩ 可視 ∩ description 有り /
  manifest 識別子は id 一本 / callable が展開済み content を返す
"""

import types

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user
from test.util.tool_errors import call_error


def _model_with_skills(*skill_ids, features=None):
    """preset モデルの最小 fixture(#42: `/command` はモデル紐づけ skill のみ発火)。"""
    meta = {"skillIds": list(skill_ids)}
    if features is not None:
        meta["features"] = features
    return {"info": {"meta": meta}}


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
                    "id": "/mycmd",
                    "name": "My Skill",
                    "content": "本文",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "mycmd"  # `/` strip 済み
        assert data["tool_ids"] == []
        assert data["description"] is None
        assert data["created_at"] == data["updated_at"]

        # command 一意性: 同じ command(`/` 有無に関わらず)は 400
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "mycmd", "name": "dup", "content": "x"},
            )
        assert response.status_code == 400

        # get by command(`/` 無しのパスで引ける)
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/id/mycmd"))
        assert response.status_code == 200
        assert response.json()["name"] == "My Skill"

        # 更新
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/id/mycmd/update"),
                json={
                    "id": "ignored",
                    "name": "Updated",
                    "content": "新本文",
                    "description": "モデル向け説明",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated"
        assert data["content"] == "新本文"
        assert data["description"] == "モデル向け説明"

        # list
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/list"))
        assert response.status_code == 200
        assert len(response.json()) == 1

        # 削除
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.delete(self.create_url("/id/mycmd/delete"))
        assert response.status_code == 200
        with mock_webui_user(id="2", role="admin"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert len(response.json()) == 0

    def test_non_admin_without_permission_cannot_create(self):
        # 既定の workspace.skills は False。グループ権限も無い一般ユーザーは作成不可。
        with mock_webui_user(id="9", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "nope", "name": "t", "content": "c"},
            )
        assert response.status_code == 401

    def test_workspace_skills_permission_allows_template_but_not_tool_skill(self):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.models.users import Users

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
                json={"id": "tmpl", "name": "t", "content": "c"},
            )
        assert response.status_code == 200

        # tool_ids 非空は admin 専用ゲート。workspace.skills 有りでも非 admin は不可。
        with mock_webui_user(id="7", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "tooly",
                    "name": "t",
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
                    "id": "tooly",
                    "name": "t",
                    "content": "c",
                    "tool_ids": ["some-tool"],
                },
            )
        assert response.status_code == 200
        assert response.json()["tool_ids"] == ["some-tool"]

    def test_read_visibility_by_group(self):
        """read フィルタ: 所属 group の OR 合成で可視性が決まる(#66)。"""
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.models.users import Users

        def make_group(name):
            return Groups.insert_new_group("1", GroupForm(name=name, description="")).id

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

        set_members(rank_bucho, ["naika-bucho"])

        # 部長 group のみ read 可視な skill を admin が作成
        Skills.insert_new_skill(
            "1",
            SkillForm(
                id="secret",
                name="secret",
                content="秘匿",
                access_control={"read": {"group_ids": [rank_bucho], "user_ids": []}},
            ),
        )

        # 部長は見える、一般は見えない
        assert any(
            s.id == "secret"
            for s in Skills.get_skills_by_user_id("naika-bucho", "read")
        )
        assert not any(
            s.id == "secret"
            for s in Skills.get_skills_by_user_id("naika-ippan", "read")
        )

    # NOTE: prompt→skill 複写(f8a4d0e2c1b7)のテストは prompts 退役
    # (prompt テーブル drop = c2d9a7b41e05)に伴い削除した。複写は履歴上の
    # 一度きりの処理で、head に prompt テーブルが存在しないため駆動できない。

    def test_slash_command_injection(self):
        import json

        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="mycmd",
                name="t",
                content="展開: {{USER_NAME}}",
                access_control=None,  # public read
            ),
        )

        user = types.SimpleNamespace(id="1", name="Taro")
        model = _model_with_skills(skill.id)

        # ヒット: user メッセージは生のまま、直後に合成 load_skill exchange を注入
        form_data = {"messages": [{"role": "user", "content": "/mycmd 追加テキスト"}]}
        result, activations = apply_skill_command(form_data, user, model)
        msgs = result["messages"]
        assert len(msgs) == 3
        assert msgs[0]["content"] == "/mycmd 追加テキスト"  # 生のまま
        assert msgs[1]["role"] == "assistant"
        call = msgs[1]["tool_calls"][0]
        assert call["function"]["name"] == "load_skill"
        assert json.loads(call["function"]["arguments"]) == {"skill_id": "mycmd"}
        assert msgs[2]["role"] == "tool"
        assert msgs[2]["tool_call_id"] == call["id"]
        # result は構造化 JSON {result, tools}(tools は後段のロードで書き足される)
        assert json.loads(msgs[2]["content"]) == {"result": "展開: Taro", "tools": []}
        # UI seed 用の発火情報
        assert activations[0]["call_id"] == call["id"]
        assert activations[0]["id"] == "mycmd"
        assert activations[0]["content"] == "展開: Taro"
        assert activations[0]["result_message"] is msgs[2]  # 書き戻し用の同一参照
        assert activations[0]["current"] is True

        # 未知 command は素通し(注入なし)
        form_data = {"messages": [{"role": "user", "content": "/nope そのまま"}]}
        result, activations = apply_skill_command(form_data, user, model)
        assert len(result["messages"]) == 1
        assert result["messages"][0]["content"] == "/nope そのまま"
        assert activations == []

        # 行頭以外(先頭が `/` でない)は無反応
        form_data = {"messages": [{"role": "user", "content": "前置き /mycmd"}]}
        result, activations = apply_skill_command(form_data, user, model)
        assert len(result["messages"]) == 1
        assert activations == []

    def test_slash_command_activation_seed_keys(self):
        """UI seed(metadata["skill_activations"])のキー整合(#43 rename 回帰)。

        process_chat_response の synthetic tool_calls ブロックは
        call_id / id / result の 3 キーを読む。#43 の command→id 改名時に
        seed 側だけ旧キー a["command"] が残り、`/command` 発火のたびに
        KeyError で払い出しが落ちていた。
        """
        import json

        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import (
            apply_skill_command,
            current_skill_activation_seeds,
        )

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(id="seedcmd", name="t", content="指示", access_control=None),
        )
        past = Skills.insert_new_skill(
            "1",
            SkillForm(id="seedpast", name="t", content="過去", access_control=None),
        )

        user = types.SimpleNamespace(id="1", name="Taro")
        # 過去ターンは別の skill(同じ skill の再発火は冪等で足されない —
        # test_slash_command_refire_is_idempotent)
        form_data = {
            "messages": [
                {"role": "user", "content": "/seedpast 過去ターン"},
                {"role": "assistant", "content": "了解"},
                {"role": "user", "content": "/seedcmd 今ターン"},
            ]
        }
        _, activations = apply_skill_command(
            form_data, user, _model_with_skills(past.id, skill.id)
        )

        seeds = current_skill_activation_seeds(activations)
        # current(最後の user メッセージ)の発火だけが seed される
        assert len(seeds) == 1
        assert set(seeds[0].keys()) == {"call_id", "id", "result"}
        assert seeds[0]["id"] == "seedcmd"
        assert json.loads(seeds[0]["result"])["result"] == "指示"

    def test_slash_command_multi_turn_scan(self):
        """全 user メッセージ走査(会話持続)— 過去ターンの `/command` も毎リクエスト展開される。"""
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command

        s_a = Skills.insert_new_skill(
            "1",
            SkillForm(id="multia", name="t", content="指示A", access_control=None),
        )
        s_b = Skills.insert_new_skill(
            "1",
            SkillForm(id="multib", name="t", content="指示B", access_control=None),
        )

        user = types.SimpleNamespace(id="1", name="Taro")
        model = _model_with_skills(s_a.id, s_b.id)

        form_data = {
            "messages": [
                {"role": "user", "content": "/multia 最初の依頼"},
                {"role": "assistant", "content": "了解"},
                {"role": "user", "content": "続きをやって"},
                {"role": "user", "content": "/multib 追加で"},
            ]
        }
        result, activations = apply_skill_command(form_data, user, model)
        msgs = result["messages"]
        # 各ヒットの直後に合成 exchange が入る(1 ターン目も毎リクエスト再注入 = 揮発しない)
        roles = [m["role"] for m in msgs]
        assert roles == [
            "user",  # /multia(生のまま)
            "assistant",  # 合成 call
            "tool",  # 合成 result
            "assistant",  # 了解
            "user",  # 続きをやって(素通し)
            "user",  # /multib(生のまま)
            "assistant",
            "tool",
        ]
        import json

        assert msgs[0]["content"] == "/multia 最初の依頼"
        assert json.loads(msgs[2]["content"])["result"] == "指示A"
        assert json.loads(msgs[7]["content"])["result"] == "指示B"
        # current は最後の user メッセージでの発火のみ(UI 表示は当該ターンだけ)
        assert [(a["id"], a["current"]) for a in activations] == [
            ("multia", False),
            ("multib", True),
        ]

    def test_slash_command_invisible_skill_passes_through(self):
        """他人の私有 skill(access_control={})は発火せず素通し。"""
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command

        skill = Skills.insert_new_skill(
            "owner",
            SkillForm(id="secretcmd", name="t", content="秘匿", access_control={}),
        )

        # モデルには紐づいている(不可視性だけで弾かれることの確認)
        user = types.SimpleNamespace(id="someone-else", name="X")
        form_data = {"messages": [{"role": "user", "content": "/secretcmd hi"}]}
        result, activations = apply_skill_command(
            form_data, user, _model_with_skills(skill.id)
        )
        assert len(result["messages"]) == 1  # 注入なし
        assert result["messages"][0]["content"] == "/secretcmd hi"
        assert activations == []

    def test_skill_feature_disabled_gates_both_entrances(self):
        """機能「スキル」OFF(meta.features.skills=False)で両入口が止まる(#42 T3)。

        `/command` は平文素通し(発火しない)、load_skill manifest は非搭載。
        キー不在は既定 ON。
        """
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import (
            apply_skill_command,
            build_load_skill_tool,
        )

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="gatedcmd",
                name="t",
                description="説明あり",
                content="指示",
                access_control=None,
            ),
        )
        user = types.SimpleNamespace(id="1", name="Taro")

        model_off = _model_with_skills(skill.id, features={"skills": False})
        form_data = {"messages": [{"role": "user", "content": "/gatedcmd hi"}]}
        result, activations = apply_skill_command(form_data, user, model_off)
        assert len(result["messages"]) == 1  # 素通し
        assert activations == []
        assert build_load_skill_tool(model_off, user) is None

        # キー不在 = 既定 ON(従来挙動)
        model_default = _model_with_skills(skill.id)
        _, activations = apply_skill_command(
            {"messages": [{"role": "user", "content": "/gatedcmd hi"}]},
            user,
            model_default,
        )
        assert [a["id"] for a in activations] == ["gatedcmd"]
        assert build_load_skill_tool(model_default, user) is not None

    def test_slash_command_requires_model_attachment(self):
        """`/command` はモデルに紐づく skill のみ発火(#42 T4 — 挙動変更)。

        可視でも skillIds に無い skill は素通し。従来の「可視ならどのモデルでも発火」
        からの変更(モデル設定を唯一の制御面に一元化)。
        """
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command

        Skills.insert_new_skill(
            "1",
            SkillForm(id="unattached", name="t", content="指示", access_control=None),
        )
        user = types.SimpleNamespace(id="1", name="Taro")

        form_data = {"messages": [{"role": "user", "content": "/unattached hi"}]}
        result, activations = apply_skill_command(
            form_data, user, _model_with_skills()  # skillIds 空
        )
        assert len(result["messages"]) == 1
        assert result["messages"][0]["content"] == "/unattached hi"
        assert activations == []

    def test_base_model_id_expands_in_skill_content(self):
        """skill 本文の {{BASE_MODEL_ID}} は発火ターンのモデルの base で展開される。

        `/command`(合成 exchange)と load_skill(擬似 tool)の両入口。
        """
        import asyncio
        import json

        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command, build_load_skill_tool

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="whoami",
                name="t",
                description="基盤モデルを答える",
                content="on {{BASE_MODEL_ID}} for {{USER_NAME}}",
                access_control=None,
            ),
        )
        user = types.SimpleNamespace(id="1", name="Taro")
        model = {
            "id": "llens",
            "name": "LLENS",
            "info": {
                "base_model_id": "qwen3.8-flash-next",
                "meta": {"skillIds": [skill.id]},
            },
        }
        expected = "on qwen3.8-flash-next for Taro"

        # `/command`
        form_data = {"messages": [{"role": "user", "content": "/whoami"}]}
        result, activations = apply_skill_command(form_data, user, model)
        assert json.loads(result["messages"][2]["content"])["result"] == expected
        assert activations[0]["content"] == expected

        # load_skill
        tool = build_load_skill_tool(model, user)
        result = json.loads(asyncio.run(tool["callable"](skill_id="whoami")))
        assert result["result"] == expected

        # モデル情報が取れない(id の無い dict)ときは Unknown に落ちる
        bare = _model_with_skills(skill.id)
        _, activations = apply_skill_command(
            {"messages": [{"role": "user", "content": "/whoami"}]}, user, bare
        )
        assert activations[0]["content"] == "on Unknown for Taro"

    def test_build_load_skill_tool(self):
        """load_skill 合成(#35 step2a): skillIds ∩ 可視 ∩ description 有りのみ manifest に載る。"""
        import asyncio

        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import build_load_skill_tool

        s1 = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="scales",
                name="評価スケール",
                description="臨床評価スケールの参照",
                content="スケール表: {{USER_NAME}} 用",
                access_control=None,
            ),
        )
        s2 = Skills.insert_new_skill(  # description 無し → モデルに露出しない
            "1",
            SkillForm(id="nodesc", name="t", content="c", access_control=None),
        )
        s3 = Skills.insert_new_skill(  # 他人の私有 → 不可視
            "owner",
            SkillForm(
                id="hiddencmd",
                name="t",
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
        # manifest 識別子は id 一本(id/name は露出させない)
        assert spec["parameters"]["properties"]["skill_id"]["enum"] == ["scales"]
        assert "- scales: 臨床評価スケールの参照" in spec["description"]

        # 発火: tool result = 構造化 JSON {result: 展開済み content, tools: []}
        import json

        result = json.loads(asyncio.run(tool["callable"](skill_id="scales")))
        assert result == {"result": "スケール表: Hanako 用", "tools": []}

        # 未知 command は call の ToolError(#145 — middleware が結果の文に変える)
        error = asyncio.run(call_error(tool["callable"](skill_id="nope")))
        assert error.kind == "call"
        assert error.text == (
            "ERROR: skill が見つからない: nope。"
            "次の手: load_skill の説明の一覧にある id をそのまま渡す。"
        )

        # render ディスクリプタ(#20): result を skill 表示で描画
        assert tool["render"] == {"result": {"kind": "skill"}}

    def test_build_load_skill_tool_none_when_empty(self):
        """対象 skill が無ければ load_skill 自体を載せない(None)。"""
        from nucllei.utils.skills import build_load_skill_tool

        user = types.SimpleNamespace(id="1", name="A")
        assert build_load_skill_tool({}, user) is None
        assert build_load_skill_tool({"info": {"meta": {}}}, user) is None
        assert (
            build_load_skill_tool({"info": {"meta": {"skillIds": ["missing"]}}}, user)
            is None
        )

    # NOTE: 発火済み skill の manifest 除外テストは撤廃(2026-07-06)。
    # `/command` 発火は合成 load_skill exchange として履歴に見えるため、
    # モデルは「ロード済み」を自然に知り、除外ルール自体が不要になった。

    def test_scan_passes_through_structured_tool_history(self):
        """フロントが復元した tool 往復(#18-2)を含む履歴でも走査・注入が壊れない。

        assistant.tool_calls / role:tool のメッセージは素通しし、合成 exchange は
        `/command` の user メッセージ直後に入る(実 tool 往復より前)。
        """
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(id="histcmd", name="t", content="指示", access_control=None),
        )
        user = types.SimpleNamespace(id="1", name="Taro")
        model = _model_with_skills(skill.id)
        form_data = {
            "messages": [
                {"role": "user", "content": "/histcmd 開始"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "functions.f:0",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "functions.f:0", "content": "r"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "続き"},
            ]
        }
        result, activations = apply_skill_command(form_data, user, model)
        roles = [m["role"] for m in result["messages"]]
        assert roles == [
            "user",  # /histcmd(生のまま)
            "assistant",  # 合成 load_skill call
            "tool",  # 合成 result
            "assistant",  # 実 tool call(素通し)
            "tool",  # 実 result(素通し)
            "assistant",  # done
            "user",  # 続き
        ]
        assert (
            result["messages"][1]["tool_calls"][0]["function"]["name"] == "load_skill"
        )
        assert result["messages"][4]["tool_call_id"] == "functions.f:0"
        assert [a["id"] for a in activations] == ["histcmd"]

    def test_activation_carries_tool_ids(self):
        """`/command` 発火の activation は skill.tool_ids を運ぶ(#35 step2b、入口 1)。"""
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="vaultcmd",
                name="金庫番",
                content="secret_password を呼べ",
                tool_ids=["secret-password"],
                access_control=None,
            ),
        )

        user = types.SimpleNamespace(id="1", name="Taro")
        form_data = {"messages": [{"role": "user", "content": "/vaultcmd 教えて"}]}
        _, activations = apply_skill_command(
            form_data, user, _model_with_skills(skill.id)
        )
        assert activations[0]["tool_ids"] == ["secret-password"]

    def test_slash_command_refire_is_idempotent(self):
        """同じ skill の `/command` が 2 回あっても本文は 1 回だけ注入する(冪等)。

        それより前の履歴にモデル自律の load_skill がある skill も足さない。
        """
        import json

        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import apply_skill_command

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(id="twice", name="t", content="指示T", access_control=None),
        )
        user = types.SimpleNamespace(id="1", name="Taro")
        model = _model_with_skills(skill.id)

        form_data = {
            "messages": [
                {"role": "user", "content": "/twice 最初"},
                {"role": "assistant", "content": "了解"},
                {"role": "user", "content": "/twice もう一度"},
            ]
        }
        result, activations = apply_skill_command(form_data, user, model)
        msgs = result["messages"]
        assert [m["role"] for m in msgs] == [
            "user",
            "assistant",  # 合成 call(1 回目だけ)
            "tool",
            "assistant",
            "user",
        ]
        bodies = [m for m in msgs if m["role"] == "tool" and "指示T" in m["content"]]
        assert len(bodies) == 1
        assert json.loads(msgs[2]["content"])["result"] == "指示T"
        assert [(a["id"], a["current"]) for a in activations] == [("twice", False)]

        # 先行する履歴にモデル自律の load_skill があれば `/command` は足さない
        form_data = {
            "messages": [
                {"role": "user", "content": "やって"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "functions.load_skill:0",
                            "type": "function",
                            "function": {
                                "name": "load_skill",
                                "arguments": json.dumps({"skill_id": "twice"}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "functions.load_skill:0",
                    "content": json.dumps({"result": "指示T", "tools": []}),
                },
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "/twice 続き"},
            ]
        }
        result, activations = apply_skill_command(form_data, user, model)
        assert len(result["messages"]) == 5
        assert activations == []

    def test_load_skill_already_loaded_returns_short_result(self):
        """読み込み済みの skill の load_skill は本文を返さず短い結果を返す。

        同じターン内の 2 回目と、messages(`/command` の合成 exchange)に既にある場合。
        """
        import asyncio
        import json

        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import (
            LOADED_RESULT,
            apply_skill_command,
            build_load_skill_tool,
        )

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="once",
                name="t",
                description="一度だけ",
                content="長い手順書本文",
                access_control=None,
            ),
        )
        user = types.SimpleNamespace(id="1", name="Taro")
        model = _model_with_skills(skill.id)
        short = LOADED_RESULT.format(skill_id="once")
        assert "次の手: " in short and "長い手順書本文" not in short

        # 同じターン内の 2 回目
        tool = build_load_skill_tool(model, user)
        first = json.loads(asyncio.run(tool["callable"](skill_id="once")))
        second = json.loads(asyncio.run(tool["callable"](skill_id="/once")))
        assert first == {"result": "長い手順書本文", "tools": []}
        assert second == {"result": short, "tools": []}

        # `/command` で注入済み(messages に合成 exchange がある)なら 1 回目から短い結果
        form_data, _ = apply_skill_command(
            {"messages": [{"role": "user", "content": "/once 開始"}]}, user, model
        )
        tool = build_load_skill_tool(model, user, messages=form_data["messages"])
        result = json.loads(asyncio.run(tool["callable"](skill_id="once")))
        assert result == {"result": short, "tools": []}

    def test_command_then_model_load_skill_injects_body_once(self):
        """`/command` で入った skill をモデルが同じターンで load_skill しても、本文は
        context に 1 回だけ(process_chat_payload が build_load_skill_tool に messages を
        渡す — 渡さないと合成 exchange が見えず、load_skill が本文をもう 1 回返す)。"""
        import asyncio
        import json

        from starlette.requests import Request

        from nucllei.main import app
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils import middleware
        from nucllei.utils.skills import LOADED_RESULT

        body = "【一度だけの手順書本文】"
        skill = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="oncecmd",
                name="t",
                description="一度だけ",
                content=body,
                access_control=None,
            ),
        )
        model = {
            "id": "skillpreset",
            "name": "skillpreset",
            "owned_by": "openai",
            "info": {"meta": {"skillIds": [skill.id]}},
        }
        user = types.SimpleNamespace(
            id="1", name="Taro", email="t@example.invalid", role="admin", info=None
        )
        saved_models = getattr(app.state, "MODELS", None)
        app.state.MODELS = {"skillpreset": model}
        try:
            request = Request(
                scope={
                    "type": "http",
                    "app": app,
                    "method": "POST",
                    "path": "/api/chat/completions",
                    "headers": [],
                    "query_string": b"",
                }
            )
            form_data = {
                "model": "skillpreset",
                "stream": True,
                "messages": [{"role": "user", "content": "/oncecmd やって"}],
            }
            metadata = {"user_id": "1", "model": model}
            form_data, metadata, _ = asyncio.run(
                middleware.process_chat_payload(
                    request, form_data, user, metadata, model, None
                )
            )
            # `/command` の合成 exchange で本文が 1 回入っている
            sent = json.dumps(form_data["messages"], ensure_ascii=False)
            assert sent.count(body) == 1

            # 同じターンでモデルが load_skill しても本文は返らない
            load_skill = metadata["tools"]["load_skill"]["callable"]
            result = json.loads(asyncio.run(load_skill(skill_id="oncecmd")))
            assert result["result"] == LOADED_RESULT.format(skill_id="oncecmd")
            assert body not in result["result"]
        finally:
            app.state.MODELS = saved_models

    def test_load_skill_calls_load_tools_hook(self):
        """load_skill 発火時、tool_ids 非空なら load_tools フックが呼ばれる(入口 2)。"""
        import asyncio

        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import build_load_skill_tool

        with_tools = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="hooked",
                name="t",
                description="tool 付き",
                content="c",
                tool_ids=["some-tool"],
                access_control=None,
            ),
        )
        without_tools = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="plain",
                name="t",
                description="tool なし",
                content="c",
                access_control=None,
            ),
        )

        import json

        user = types.SimpleNamespace(id="1", name="Taro")
        model = {"info": {"meta": {"skillIds": [with_tools.id, without_tools.id]}}}

        loaded = []

        async def fake_load_tools(skill):  # #28: フックは async 契約
            loaded.append(skill.id)
            return ["fake_tool_fn"]

        tool = build_load_skill_tool(model, user, load_tools=fake_load_tools)

        # 導入された tool 名が戻り値の tools に載る
        result = json.loads(asyncio.run(tool["callable"](skill_id="hooked")))
        assert result == {"result": "c", "tools": ["fake_tool_fn"]}
        assert loaded == ["hooked"]

        # tool_ids 空ならフックは呼ばれず tools は空
        result = json.loads(asyncio.run(tool["callable"](skill_id="plain")))
        assert result == {"result": "c", "tools": []}
        assert loaded == ["hooked"]

    def test_replay_skill_tool_ids_from_history(self):
        """モデル自律 load_skill の tool は履歴から再導出される(#101、入口 2 の持続)。"""
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import replay_skill_tool_ids

        skill = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="audio",
                name="音声",
                description="音声処理",
                content="c",
                tool_ids=["audio-tools"],
                access_control=None,
            ),
        )
        user = types.SimpleNamespace(id="1", name="Taro")
        form_data = {
            "messages": [
                {"role": "user", "content": "文字起こしして"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "functions.load_skill:0",
                            "type": "function",
                            "function": {
                                "name": "load_skill",
                                "arguments": '{"skill_id": "audio"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "functions.load_skill:0",
                    "content": "{}",
                },
                {"role": "assistant", "content": "inspect しました"},
                {"role": "user", "content": "やってくれ"},
            ]
        }

        assert replay_skill_tool_ids(form_data, user, _model_with_skills(skill.id)) == [
            "audio-tools"
        ]

        # 認可は履歴に委ねない(その場で AND 条件を再確認):
        # モデルから skill を外したら履歴があっても復活しない
        assert replay_skill_tool_ids(form_data, user, _model_with_skills("other")) == []
        # 機能「スキル」OFF でも復活しない
        assert (
            replay_skill_tool_ids(
                form_data,
                user,
                _model_with_skills(skill.id, features={"skills": False}),
            )
            == []
        )

        # read 不可視ユーザーにも復活しない(private skill + 他人)
        private = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="vault",
                name="金庫番",
                description="d",
                content="c",
                tool_ids=["secret-password"],
                access_control={},
            ),
        )
        private_history = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "functions.load_skill:0",
                            "type": "function",
                            "function": {
                                "name": "load_skill",
                                "arguments": '{"skill_id": "vault"}',
                            },
                        }
                    ],
                }
            ]
        }
        stranger = types.SimpleNamespace(id="9", name="Jiro")
        assert (
            replay_skill_tool_ids(
                private_history, stranger, _model_with_skills(private.id)
            )
            == []
        )
        # 作成者本人なら復活する
        assert replay_skill_tool_ids(
            private_history, user, _model_with_skills(private.id)
        ) == ["secret-password"]

    def test_replay_skill_tool_ids_blocks_dedup_and_garbage(self):
        """content_blocks 形も拾う / synthetic 除外 / 重複排除 / 壊れた引数は無視(#101)。"""
        from nucllei.models.skills import SkillForm, Skills
        from nucllei.utils.skills import replay_skill_tool_ids

        audio = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="audio",
                name="音声",
                description="d",
                content="c",
                tool_ids=["audio-tools"],
                access_control=None,
            ),
        )
        doc = Skills.insert_new_skill(
            "1",
            SkillForm(
                id="doc",
                name="文書",
                description="d",
                content="c",
                tool_ids=["doc-tools", "audio-tools"],
                access_control=None,
            ),
        )
        user = types.SimpleNamespace(id="1", name="Taro")
        model = _model_with_skills(audio.id, doc.id)

        call = lambda skill_id: {  # noqa: E731
            "id": f"functions.load_skill:{skill_id}",
            "type": "function",
            "function": {
                "name": "load_skill",
                "arguments": f'{{"skill_id": "{skill_id}"}}',
            },
        }
        form_data = {
            "messages": [
                # OpenAI 形 + 同一 skill の重複呼び出し
                {"role": "assistant", "content": "", "tool_calls": [call("audio")]},
                {"role": "assistant", "content": "", "tool_calls": [call("audio")]},
                # content_blocks 形(UI 履歴)から doc を拾う。synthetic は除外
                {
                    "role": "assistant",
                    "content": "",
                    "content_blocks": [
                        {"type": "tool_calls", "content": [call("doc")]},
                        {
                            "type": "tool_calls",
                            "synthetic": True,
                            "content": [call("vault")],
                        },
                    ],
                },
                # 壊れた引数・別 tool・function 欠落は無視(落ちない)
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "x",
                            "type": "function",
                            "function": {
                                "name": "load_skill",
                                "arguments": "{broken json",
                            },
                        },
                        {
                            "id": "y",
                            "type": "function",
                            "function": {"name": "other_tool", "arguments": "{}"},
                        },
                        {"id": "z", "type": "function"},
                    ],
                },
            ]
        }

        # audio-tools は 1 回だけ(重複排除)、doc-tools は content_blocks 経由
        assert replay_skill_tool_ids(form_data, user, model) == [
            "audio-tools",
            "doc-tools",
        ]
