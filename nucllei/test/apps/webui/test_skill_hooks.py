"""skill の on_load(llens prism-hu/llens#22「hooks 的な」)の回帰テスト。

on_load = skill が読み込まれたときに書いた順に処理する手順。`load_skill` の要素は別の skill を
一緒に読み込み(毎リクエスト再導出・保存しない)、それ以外の tool の要素はサーバが chat に
つき 1 回だけ実行する(保存して以後は履歴から拾う)。3 入口(`/command`・モデルの
load_skill・添付の自動ロード)で効く。設計は _memos/skill-design.md「on_load」。

カバー範囲:
- API: 保存・正規化・検証(400)・load_skill 以外の要素を含むものは admin のみ(load_skill の
  要素だけなら editor も可)・update の省略は保持・旧 `requires` は未知フィールドとして無視
- load_skill の要素: 3 入口それぞれで読み込み先が続けて入る / 読み込み先の tool も生える /
  履歴にあれば入れない / 使えない読み込み先は「使えない」行 / 循環は 1 回で止まる /
  履歴からの tool 再導出
- tool の要素: 3 入口それぞれで 1 回だけ実行 / 履歴に結果があれば再実行しない(呼び出し回数) /
  このターンに無い tool は ERROR 行 / 例外も ERROR 行で読み込みは続く / 子ターン(subagent)でも同じ
- 書いた順: load_skill の要素で入った skill の on_load はその位置で深さ優先に展開
- 永続化の形: in_payload の印は同じターンの追撃では二重にせず、保存では落ちて次のターンからは
  通常の tool 往復として読まれる
"""

import asyncio
import json
import logging
import types

from starlette.requests import Request

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


def _model(*skill_ids, tool_ids=()):
    return {
        "id": "hookpreset",
        "name": "hookpreset",
        "owned_by": "openai",
        "info": {"meta": {"skillIds": list(skill_ids), "toolIds": list(tool_ids)}},
    }


def _request():
    from nucllei.main import app

    return Request(
        scope={
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/chat/completions",
            "headers": [],
            "query_string": b"",
        }
    )


def _called_skill_ids(messages):
    return [
        json.loads(call["function"]["arguments"]).get("skill_id")
        for message in messages
        for call in message.get("tool_calls") or []
        if call["function"]["name"] == "load_skill"
    ]


class TestSkillHooks(AbstractPostgresTest):
    BASE_PATH = "/api/v1/skills"

    def setup_method(self):
        super().setup_method()
        self.user = types.SimpleNamespace(
            id="1", name="Taro", email="t@example.invalid", role="user", info=None
        )
        self.probe_calls = []

    # ── helpers ────────────────────────────────────────────────

    def _skill(self, skill_id, **kwargs):
        from nucllei.models.skills import SkillForm, Skills

        skill = Skills.insert_new_skill(
            kwargs.pop("owner", "1"),
            SkillForm(
                id=skill_id,
                name=skill_id,
                description=kwargs.pop("description", f"{skill_id} の説明"),
                content=kwargs.pop("content", f"【{skill_id} の手順書】"),
                access_control=kwargs.pop("access_control", None),
                **kwargs,
            ),
        )
        assert skill is not None
        return skill

    def _probe_entry(self, fail=False):
        calls = self.probe_calls

        async def probe(x: int = 0):
            calls.append(x)
            if fail:
                raise RuntimeError("probe exploded")
            return f"probe result {x}"

        return {
            "tool_id": "probe-tools",
            "spec": {
                "name": "probe",
                "description": "probe",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                },
            },
            "callable": probe,
        }

    def _patch_get_tools(self, monkeypatch, fail=False):
        from nucllei.utils import middleware

        entry = self._probe_entry(fail=fail)

        def fake_get_tools(request, tool_ids, user, extra_params, **kwargs):
            return {"probe": entry} if "probe-tools" in (tool_ids or []) else {}

        monkeypatch.setattr(middleware, "get_tools", fake_get_tools)

    def _payload(self, messages, model, metadata=None):
        from nucllei.main import app
        from nucllei.utils import middleware

        saved = getattr(app.state, "MODELS", None)
        app.state.MODELS = {model["id"]: model}
        try:
            form_data = {"model": model["id"], "stream": True, "messages": messages}
            form_data, metadata, _ = asyncio.run(
                middleware.process_chat_payload(
                    _request(),
                    form_data,
                    self.user,
                    {"user_id": self.user.id, "model": model, **(metadata or {})},
                    model,
                    None,
                )
            )
            return form_data, metadata
        finally:
            app.state.MODELS = saved

    def _next_turn_history(self, first_messages, metadata):
        """1 ターン目の応答を保存して FE が次のリクエストで送る形にした履歴。

        保存されるのは snapshot_content_blocks の形(in_payload の印は落ちる)。FE は
        synthetic でない tool_calls ブロックを tool 往復に展開する(chat-messages.ts
        expandAssistantToolHistory と同じ形 — ここでは backend の
        convert_content_blocks_to_messages で代用する)。
        """
        from nucllei.utils.content_blocks import (
            convert_content_blocks_to_messages,
            snapshot_content_blocks,
        )

        blocks = list(metadata.get("skill_on_load_blocks") or []) + [
            {"type": "text", "content": "わかりました"}
        ]
        saved = snapshot_content_blocks(blocks)
        assert all("in_payload" not in block for block in saved)
        user_messages = [m for m in first_messages if m.get("role") == "user"]
        return [
            *user_messages,
            *convert_content_blocks_to_messages(saved),
            {"role": "user", "content": "つづけて"},
        ]

    # ── API ─────────────────────────────────────────────────────

    def test_create_stores_and_normalizes(self):
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "emr",
                    "name": "EMR",
                    "content": "c",
                    "on_load": [
                        {"tool": " read_file ", "args": {"path": "/ext/a.md"}},
                        {"tool": "load_skill", "args": {"skill_id": "/base"}},
                        {"tool": "probe"},
                    ],
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "requires" not in body
        assert body["on_load"] == [
            {"tool": "read_file", "args": {"path": "/ext/a.md"}},
            {"tool": "load_skill", "args": {"skill_id": "base"}},
            {"tool": "probe", "args": {}},
        ]
        # 省略した skill は空
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "plain", "name": "p", "content": "c"},
            )
        assert response.json()["on_load"] == []

    def test_old_requires_field_is_ignored(self):
        """旧 `requires` は廃止(on_load の load_skill の要素に統合)。送っても未知フィールドとして
        無視される(SkillForm は pydantic 既定の extra=ignore)。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "old", "name": "o", "content": "c", "requires": ["x"]},
            )
        assert response.status_code == 200, response.text
        assert "requires" not in response.json()
        assert response.json()["on_load"] == []

    def test_validation_errors_are_400(self):
        bad = [
            {"on_load": [{"tool": "load_skill", "args": {"skill_id": "self"}}]},
            {"on_load": [{"tool": "load_skill", "args": {"skill_id": "/self"}}]},
            {"on_load": [{"tool": "load_skill", "args": {}}]},
            {"on_load": [{"tool": "load_skill", "args": {"skill_id": "/"}}]},
            {"on_load": [{"tool": "load_skill", "args": {"skill_id": 1}}]},
            {"on_load": [{"tool": "load_skill", "args": {"skill_id": "a", "x": 1}}]},
            {"on_load": {"tool": "probe"}},
            {"on_load": ["probe"]},
            {"on_load": [{"tool": ""}]},
            {"on_load": [{"tool": "probe", "args": [1]}]},
            {"on_load": [{"tool": "probe", "when": "x"}]},
        ]
        for extra in bad:
            with mock_webui_user(id="1", role="admin"):
                response = self.fast_api_client.post(
                    self.create_url("/create"),
                    json={"id": "self", "name": "s", "content": "c", **extra},
                )
            assert response.status_code == 400, (extra, response.text)

    def test_shorthand_on_load_is_normalized(self):
        """レシピと同じ短い記法 (skill / recipe / read) を受けて正規形にする (2026-10-30)。
        shell / python は付属の無い skill では 400。JSON 形の run_shell は今までどおり admin が書ける。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "short",
                    "name": "s",
                    "content": "c",
                    "on_load": [
                        {"skill": "/emr"},
                        {"recipe": "/ext/groups/EMR/recipes/抽出/RECIPE.md"},
                        {"read": "/ext/groups/EMR/notes/./全体.md"},
                        {"tool": "run_shell", "args": {"command": "ls"}},
                    ],
                },
            )
        assert response.status_code == 200, response.text
        assert response.json()["on_load"] == [
            {"tool": "load_skill", "args": {"skill_id": "emr"}},
            {"tool": "load_recipe", "args": {"path": "/ext/groups/EMR/recipes/抽出"}},
            {"tool": "read_file", "args": {"path": "/ext/groups/EMR/notes/全体.md"}},
            {"tool": "run_shell", "args": {"command": "ls"}},
        ]
        for bad in (
            [{"shell": "prep.sh"}],
            [{"python": "calc.py"}],
            [{"read": "notes.md"}],
            [{"recipe": "/workspace/notes/x"}],
            [{"recipe": "共通"}],
            [{"skill": ""}],
            [{"tool": "load_recipe", "args": {"path": "/x"}}],
            [{"tool": "load_recipe", "args": {"path": "/workspace/recipes/a", "x": 1}}],
        ):
            with mock_webui_user(id="1", role="admin"):
                response = self.fast_api_client.post(
                    self.create_url("/create"),
                    json={"id": "bad", "name": "b", "content": "c", "on_load": bad},
                )
            assert response.status_code == 400, (bad, response.text)

    def test_editor_may_set_skill_and_recipe_but_not_read(self):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.models.users import Users

        Users.insert_new_user(
            id="8", name="editor2", email="e2@hospital.example", role="user"
        )
        group = Groups.insert_new_group(
            "1",
            GroupForm(
                name="skill-editors-2",
                description="",
                permissions={"workspace": {"skills": True}},
            ),
        )
        Groups.update_group_by_id(
            group.id,
            GroupUpdateForm(
                name=group.name, description=group.description, user_ids=["8"]
            ),
        )
        with mock_webui_user(id="8", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "chain",
                    "name": "c",
                    "content": "c",
                    "on_load": [{"skill": "emr"}, {"recipe": "/workspace/recipes/a"}],
                },
            )
            assert response.status_code == 200, response.text
            # read は実際に tool を実行する要素 — admin のみ
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "reader",
                    "name": "r",
                    "content": "c",
                    "on_load": [{"read": "/ext/public/a.md"}],
                },
            )
            assert response.status_code == 401

    def test_on_load_tools_are_admin_only_load_skill_is_not(self):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.models.users import Users

        Users.insert_new_user(
            id="7", name="editor", email="e@hospital.example", role="user"
        )
        group = Groups.insert_new_group(
            "1",
            GroupForm(
                name="skill-editors",
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
        load_only = [{"tool": "load_skill", "args": {"skill_id": "x"}}]
        mixed = [*load_only, {"tool": "probe"}]
        # editor: load_skill の要素だけなら作成も更新もできる
        with mock_webui_user(id="7", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "mine", "name": "m", "content": "c", "on_load": load_only},
            )
            assert response.status_code == 200, response.text
            assert response.json()["on_load"] == load_only
            response = self.fast_api_client.post(
                self.create_url("/id/mine/update"),
                json={"id": "mine", "name": "m", "content": "c2", "on_load": load_only},
            )
            assert response.status_code == 200, response.text
        # editor: load_skill 以外の tool を 1 つでも含むと作成も更新も不可(tool_ids と同じ
        # admin ゲート)
        with mock_webui_user(id="7", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "hooky", "name": "h", "content": "c", "on_load": mixed},
            )
            assert response.status_code == 401
            response = self.fast_api_client.post(
                self.create_url("/id/mine/update"),
                json={"id": "mine", "name": "m", "content": "c", "on_load": mixed},
            )
            assert response.status_code == 401
        # admin が付けた tool の要素がある skill は、editor が自分の物でも編集・削除できない
        # (on_load を省略した更新・load_skill だけに書き換える更新も不可)
        self._skill(
            "adminhook",
            owner="7",
            on_load=[{"tool": "probe", "args": {}}],
        )
        with mock_webui_user(id="7", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/id/adminhook/update"),
                json={"id": "adminhook", "name": "h", "content": "changed"},
            )
            assert response.status_code == 401
            response = self.fast_api_client.post(
                self.create_url("/id/adminhook/update"),
                json={
                    "id": "adminhook",
                    "name": "h",
                    "content": "changed",
                    "on_load": load_only,
                },
            )
            assert response.status_code == 401
            response = self.fast_api_client.delete(
                self.create_url("/id/adminhook/delete")
            )
            assert response.status_code == 401

    def test_update_omitted_keeps_explicit_empty_clears(self):
        self._skill("keep", on_load=[{"tool": "probe", "args": {"x": 1}}])
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/id/keep/update"),
                json={"id": "keep", "name": "k", "content": "new"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["on_load"] == [{"tool": "probe", "args": {"x": 1}}]
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/id/keep/update"),
                json={
                    "id": "keep",
                    "name": "k",
                    "content": "new",
                    "on_load": [],
                },
            )
        assert response.json()["on_load"] == []

    # ── load_skill の要素 ───────────────────────────────────────

    def test_load_skill_entry_via_command(self):
        from nucllei.utils.skills import REQUIRED_SKILL_UNAVAILABLE, apply_skill_command

        self._skill("base", tool_ids=["base-tools"], description="")
        self._skill("secret", access_control={})  # 作成者以外は見えない
        self._skill(
            "emr",
            owner="2",
            on_load=[
                {"tool": "load_skill", "args": {"skill_id": "base"}},
                {"tool": "load_skill", "args": {"skill_id": "secret"}},
                {"tool": "load_skill", "args": {"skill_id": "missing"}},
            ],
        )
        # base は preset に紐づいていなくても、description が空でも load_skill の要素で入る
        model = _model("emr")
        form_data = {"messages": [{"role": "user", "content": "/emr はじめる"}]}
        user = types.SimpleNamespace(id="2", name="Hanako", role="user")
        form_data, activations = apply_skill_command(form_data, user, model)

        messages = form_data["messages"]
        assert _called_skill_ids(messages) == ["emr", "base"]
        emr_result = json.loads(messages[2]["content"])["result"]
        assert emr_result.startswith("【emr の手順書】")
        for missing in ("secret", "missing"):
            assert REQUIRED_SKILL_UNAVAILABLE.format(skill_id=missing) in emr_result
        assert messages[3]["tool_calls"][0]["id"] == "skill:emr:on_load:0"
        assert json.loads(messages[4]["content"])["result"] == "【base の手順書】"
        assert [a["id"] for a in activations] == ["emr", "base"]
        assert activations[1]["tool_ids"] == ["base-tools"]

    def test_load_skill_entry_skips_already_loaded_and_stops_cycles(self, caplog):
        from nucllei.utils.skills import apply_skill_command

        self._skill("a", on_load=[{"tool": "load_skill", "args": {"skill_id": "b"}}])
        self._skill(
            "b",
            on_load=[
                {"tool": "load_skill", "args": {"skill_id": "a"}},
                {"tool": "load_skill", "args": {"skill_id": "c"}},
            ],
        )
        self._skill("c", on_load=[{"tool": "load_skill", "args": {"skill_id": "b"}}])
        model = _model("a", "c")
        caplog.set_level(logging.WARNING)
        form_data = {
            "messages": [
                {"role": "user", "content": "/a"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "/c"},
            ]
        }
        form_data, activations = apply_skill_command(form_data, self.user, model)
        # a → b → (a は循環で止まる) → c → (b は循環で止まる)。/c は既に載っているので足さない
        assert _called_skill_ids(form_data["messages"]) == ["a", "b", "c"]
        assert [a["id"] for a in activations] == ["a", "b", "c"]
        assert any("循環" in r.message for r in caplog.records)

    def test_load_skill_entry_via_auto_load(self):
        from nucllei.models.files import FileForm, Files
        from nucllei.utils.skills import apply_skill_auto_load

        self._skill(
            "audio",
            auto_load_content_types=["audio/"],
            on_load=[{"tool": "load_skill", "args": {"skill_id": "base"}}],
        )
        self._skill("base", tool_ids=["base-tools"])
        Files.insert_new_file(
            "1",
            FileForm(
                id="rec",
                filename="rec",
                path="/nonexistent/rec",
                meta={"name": "rec", "content_type": "audio/webm"},
                data={},
            ),
        )
        form_data = {
            "messages": [{"role": "user", "content": "文字起こし"}],
            "files": [{"type": "file", "id": "rec"}],
        }
        form_data, activations = apply_skill_auto_load(
            form_data, self.user, _model("audio")
        )
        assert _called_skill_ids(form_data["messages"]) == ["audio", "base"]
        assert form_data["messages"][3]["tool_calls"][0]["id"] == (
            "skill:audio:on_load:0"
        )
        assert [a["id"] for a in activations] == ["audio", "base"]

    def test_load_skill_entry_via_model_load_skill(self):
        from nucllei.utils.skills import (
            build_load_skill_tool,
            drain_load_skill_followups,
        )

        self._skill(
            "emr",
            on_load=[
                {"tool": "load_skill", "args": {"skill_id": "base"}},
                {"tool": "load_skill", "args": {"skill_id": "missing"}},
            ],
        )
        self._skill("base", tool_ids=["base-tools"], description="")
        loaded_tools = []

        async def load_tools(skill):
            loaded_tools.append(skill.id)
            return ["base_fn"]

        entry = build_load_skill_tool(_model("emr"), self.user, load_tools=load_tools)
        result = json.loads(asyncio.run(entry["callable"](skill_id="emr")))
        assert "skill missing は使えない" in result["result"]
        # 読み込み先の tool は load_skill の中で生える(次のイテレーションから使える)
        assert loaded_tools == ["base"]

        async def never(calls):
            raise AssertionError("tool の要素は無い")

        calls, results = asyncio.run(
            drain_load_skill_followups(entry, {"load_skill": entry}, never)
        )
        assert [c["id"] for c in calls] == ["skill:emr:on_load:0"]
        assert json.loads(results[0]["content"]) == {
            "result": "【base の手順書】",
            "tools": ["base_fn"],
        }
        # 取り出したら空(同じ応答で 2 回足さない)
        assert asyncio.run(drain_load_skill_followups(entry, {}, never)) == ([], [])

    def test_replay_includes_chained_skill_tools(self):
        from nucllei.utils.skills import replay_skill_tool_ids

        self._skill(
            "emr",
            tool_ids=["emr-tools"],
            on_load=[{"tool": "load_skill", "args": {"skill_id": "base"}}],
        )
        self._skill("base", tool_ids=["base-tools"])
        form_data = {
            "messages": [
                {"role": "user", "content": "診療録"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "functions.load_skill:0",
                            "type": "function",
                            "function": {
                                "name": "load_skill",
                                "arguments": json.dumps({"skill_id": "emr"}),
                            },
                        }
                    ],
                },
            ]
        }
        assert replay_skill_tool_ids(form_data, self.user, _model("emr")) == [
            "emr-tools",
            "base-tools",
        ]

    # ── on_load ────────────────────────────────────────────────

    def test_on_load_via_command_runs_once(self, monkeypatch):
        self._patch_get_tools(monkeypatch)
        self._skill("emr", on_load=[{"tool": "probe", "args": {"x": 7}}])
        model = _model("emr", tool_ids=["probe-tools"])

        first = [{"role": "user", "content": "/emr 開始"}]
        form_data, metadata = self._payload(list(first), model)
        assert self.probe_calls == [7]
        messages = form_data["messages"]
        # load_skill の結果の直後に on_load の往復
        assert messages[3]["tool_calls"][0]["id"] == "skill:emr:on_load:0"
        assert messages[3]["tool_calls"][0]["function"] == {
            "name": "probe",
            "arguments": json.dumps({"x": 7}),
        }
        assert messages[4] == {
            "role": "tool",
            "tool_call_id": "skill:emr:on_load:0",
            "content": "probe result 7",
        }
        blocks = metadata["skill_on_load_blocks"]
        assert len(blocks) == 1 and blocks[0]["in_payload"] is True

        # 次のリクエスト: 履歴に結果があるので実行しない
        history = self._next_turn_history(first, metadata)
        form_data, metadata = self._payload(history, model)
        assert self.probe_calls == [7]
        assert "skill_on_load_blocks" not in metadata
        sent = form_data["messages"]
        assert [m.get("tool_call_id") for m in sent if m.get("role") == "tool"] == [
            "skill:emr:0",
            "skill:emr:on_load:0",
        ]

    def test_on_load_runs_in_written_order_depth_first(self, monkeypatch):
        """書いた順に処理し、load_skill の要素で入った skill の on_load はその位置で展開する
        (初回のターン)。2 ターン目は tool を再実行せず、load_skill の exchange が先・保存された
        結果が後に並ぶ(/command の割り切り — README_API §6.4)。"""
        self._patch_get_tools(monkeypatch)
        self._skill(
            "emr",
            on_load=[
                {"tool": "probe", "args": {"x": 1}},
                {"tool": "load_skill", "args": {"skill_id": "base"}},
                {"tool": "probe", "args": {"x": 3}},
            ],
        )
        self._skill("base", on_load=[{"tool": "probe", "args": {"x": 2}}])
        model = _model("emr", tool_ids=["probe-tools"])
        first = [{"role": "user", "content": "/emr"}]
        form_data, metadata = self._payload(list(first), model)
        ids = [
            m.get("tool_call_id") for m in form_data["messages"] if m["role"] == "tool"
        ]
        assert ids == [
            "skill:emr:0",
            "skill:emr:on_load:0",
            "skill:emr:on_load:1",  # base の load_skill
            "skill:base:on_load:0",
            "skill:emr:on_load:2",
        ]
        assert self.probe_calls == [1, 2, 3]
        # 続けて並ぶ tool の要素は 1 つの往復(base の後の 2 つ)
        assert [
            [c["id"] for c in b["content"]] for b in metadata["skill_on_load_blocks"]
        ] == [
            ["skill:emr:on_load:0"],
            ["skill:base:on_load:0", "skill:emr:on_load:2"],
        ]

        history = self._next_turn_history(first, metadata)
        form_data, metadata = self._payload(history, model)
        assert self.probe_calls == [1, 2, 3]
        assert "skill_on_load_blocks" not in metadata
        ids = [
            m.get("tool_call_id") for m in form_data["messages"] if m["role"] == "tool"
        ]
        assert ids == [
            "skill:emr:0",
            "skill:emr:on_load:1",
            "skill:emr:on_load:0",
            "skill:base:on_load:0",
            "skill:emr:on_load:2",
        ]

    def test_model_load_skill_followups_in_written_order(self):
        from nucllei.utils.skills import (
            build_load_skill_tool,
            drain_load_skill_followups,
        )

        self._skill(
            "emr",
            on_load=[
                {"tool": "probe", "args": {"x": 1}},
                {"tool": "load_skill", "args": {"skill_id": "base"}},
                {"tool": "probe", "args": {"x": 3}},
            ],
        )
        self._skill("base", on_load=[{"tool": "probe", "args": {"x": 2}}])
        executed = []

        async def execute(calls):
            executed.extend(json.loads(c["function"]["arguments"])["x"] for c in calls)
            return [
                {"tool_call_id": c["id"], "content": "ok", "duration": 0.0}
                for c in calls
            ]

        tools = {"probe": self._probe_entry()}
        entry = build_load_skill_tool(_model("emr"), self.user)
        tools["load_skill"] = entry
        asyncio.run(entry["callable"](skill_id="emr"))
        calls, _ = asyncio.run(drain_load_skill_followups(entry, tools, execute))
        assert [c["id"] for c in calls] == [
            "skill:emr:on_load:0",
            "skill:emr:on_load:1",
            "skill:base:on_load:0",
            "skill:emr:on_load:2",
        ]
        assert calls[1]["function"]["name"] == "load_skill"
        assert executed == [1, 2, 3]

    def test_on_load_unavailable_tool_is_error_line(self, monkeypatch):
        self._patch_get_tools(monkeypatch)
        self._skill("emr", on_load=[{"tool": "probe"}, {"tool": "read_file"}])
        # probe-tools がモデルに無い = このターンに無い(read ACL で落ちた tool も同じ)
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/emr"}], _model("emr")
        )
        assert self.probe_calls == []
        results = metadata["skill_on_load_blocks"][0]["results"]
        assert [r["error_kind"] for r in results] == ["failure", "failure"]
        assert results[0]["content"].startswith(
            "ERROR: skill emr の読み込み時に実行する probe は使えない"
        )
        assert "次の手:" in results[0]["content"]
        # skill 自体は読み込まれている
        assert _called_skill_ids(form_data["messages"]) == ["emr"]

    def test_on_load_exception_is_error_and_load_continues(self, monkeypatch):
        self._patch_get_tools(monkeypatch, fail=True)
        self._skill("emr", on_load=[{"tool": "probe", "args": {"x": 3}}])
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/emr"}],
            _model("emr", tool_ids=["probe-tools"]),
        )
        assert self.probe_calls == [3]
        result = metadata["skill_on_load_blocks"][0]["results"][0]
        assert result["content"].startswith("ERROR:")
        assert "error_kind" in result
        assert _called_skill_ids(form_data["messages"]) == ["emr"]

    def test_on_load_via_auto_load_runs_once(self, monkeypatch):
        from nucllei.models.files import FileForm, Files

        self._patch_get_tools(monkeypatch)
        self._skill(
            "audio",
            auto_load_content_types=["audio/"],
            on_load=[{"tool": "probe", "args": {"x": 5}}],
        )
        Files.insert_new_file(
            "1",
            FileForm(
                id="rec",
                filename="rec",
                path="/nonexistent/rec",
                meta={"name": "rec", "content_type": "audio/webm"},
                data={},
            ),
        )
        model = _model("audio", tool_ids=["probe-tools"])
        from nucllei.main import app
        from nucllei.utils import middleware

        def run(messages):
            saved = getattr(app.state, "MODELS", None)
            app.state.MODELS = {model["id"]: model}
            try:
                return asyncio.run(
                    middleware.process_chat_payload(
                        _request(),
                        {
                            "model": model["id"],
                            "stream": True,
                            "messages": messages,
                            "files": [{"type": "file", "id": "rec"}],
                        },
                        self.user,
                        {"user_id": "1", "model": model},
                        model,
                        None,
                    )
                )
            finally:
                app.state.MODELS = saved

        first = [{"role": "user", "content": "文字起こしして"}]
        form_data, metadata, _ = run(list(first))
        assert self.probe_calls == [5]
        history = self._next_turn_history(first, metadata)
        form_data, metadata, _ = run(history)
        assert self.probe_calls == [5]

    def test_on_load_via_model_load_skill_runs_once(self):
        from nucllei.utils.skills import (
            build_load_skill_tool,
            drain_load_skill_followups,
        )

        self._skill("emr", on_load=[{"tool": "probe", "args": {"x": 9}}])
        executed = []

        async def execute(calls):
            executed.extend(c["function"]["arguments"] for c in calls)
            return [
                {"tool_call_id": c["id"], "content": "ok", "duration": 0.0}
                for c in calls
            ]

        tools = {"probe": self._probe_entry()}
        entry = build_load_skill_tool(_model("emr"), self.user)
        tools["load_skill"] = entry
        asyncio.run(entry["callable"](skill_id="emr"))
        calls, results = asyncio.run(drain_load_skill_followups(entry, tools, execute))
        assert [c["id"] for c in calls] == ["skill:emr:on_load:0"]
        assert executed == [json.dumps({"x": 9})]

        # 次のリクエスト: 履歴に load_skill と on_load の結果がある → 再ロードは短い結果で、
        # on_load も実行しない
        history = [
            {"role": "user", "content": "診療録"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "functions.load_skill:0",
                        "type": "function",
                        "function": {
                            "name": "load_skill",
                            "arguments": json.dumps({"skill_id": "emr"}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "functions.load_skill:0", "content": "{}"},
            {"role": "assistant", "content": "", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "skill:emr:on_load:0", "content": "ok"},
        ]
        entry = build_load_skill_tool(_model("emr"), self.user, messages=history)
        tools["load_skill"] = entry
        asyncio.run(entry["callable"](skill_id="emr"))
        assert asyncio.run(drain_load_skill_followups(entry, tools, execute)) == (
            [],
            [],
        )
        assert executed == [json.dumps({"x": 9})]

    def test_on_load_in_subagent_turn(self, monkeypatch):
        """子ターン(run_subagent — start_internal_turn が agent_session の印を付けて
        process_chat_payload を通す)でも同じ経路で 1 回だけ実行される。"""
        self._patch_get_tools(monkeypatch)
        self._skill("emr", on_load=[{"tool": "probe", "args": {"x": 4}}])
        model = _model("emr", tool_ids=["probe-tools"])
        child = {"chat_id": "child-chat", "message_id": "m1", "agent_session": True}
        first = [{"role": "user", "content": "/emr 調べて"}]
        form_data, metadata = self._payload(list(first), model, metadata=child)
        assert self.probe_calls == [4]
        history = self._next_turn_history(first, metadata)
        self._payload(history, model, metadata=child)
        assert self.probe_calls == [4]

    # ── 永続化の形 ─────────────────────────────────────────────

    def test_in_payload_block_skipped_in_turn_kept_after_save(self):
        from nucllei.utils.content_blocks import (
            convert_content_blocks_to_messages,
            snapshot_content_blocks,
        )

        call = {
            "id": "skill:emr:on_load:0",
            "type": "function",
            "function": {"name": "probe", "arguments": "{}"},
        }
        block = {
            "type": "tool_calls",
            "content": [call],
            "results": [{"tool_call_id": call["id"], "content": "ok"}],
            "in_payload": True,
        }
        # 同じターン(payload に差し込み済み): 追撃では再送しない
        assert convert_content_blocks_to_messages([block]) == []
        # 保存後(次のターン): 通常の tool 往復
        saved = snapshot_content_blocks([block])
        assert "in_payload" not in saved[0] and "synthetic" not in saved[0]
        assert [m["role"] for m in convert_content_blocks_to_messages(saved)] == [
            "assistant",
            "tool",
        ]

    def test_assembler_append_tool_exchange(self):
        from nucllei.utils.response_assembler import ContentBlocksAssembler

        assembler = ContentBlocksAssembler()
        calls = [{"id": "c1", "type": "function", "function": {"name": "x"}}]
        assembler.ensure_tool_calls_block(calls)
        assembler.set_tool_results([{"tool_call_id": "c1", "content": "r"}])
        extra = [
            {"id": "skill:a:on_load:0", "type": "function", "function": {"name": "y"}}
        ]
        assembler.append_tool_exchange(
            extra, [{"tool_call_id": extra[0]["id"], "content": "r2"}]
        )
        kinds = [b["type"] for b in assembler.content_blocks]
        assert kinds[-3:] == ["tool_calls", "tool_calls", "text"]
        assert assembler.content_blocks[-2]["content"] is extra
