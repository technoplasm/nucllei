"""skill の before / after(prism-hu/llens#22)の回帰テスト。

before / after = skill が読み込まれたときにサーバが実行する tool の並び(admin が書く
`[{"tool": …, "args": {…}}]`)。結果は before → 本文 → after の順に 1 つの load_skill の結果へ
入る。`/command`・自動ロードの発火はその load_skill 往復ごと応答に残るので、2 ターン目以降も
同じ並びのまま履歴として届き、tool は再実行しない。設計は _memos/skill-design.md「before / after」。

カバー範囲:
- API: 保存・正規化・検証(400 — 短い記法・load_skill / load_recipe・知らないキー)・
  before / after を 1 つでも持つものは admin のみ(editor は空のまま保存できる)・
  update の省略は保持・空配列で消える
- 3 入口(`/command`・モデルの load_skill・添付の自動ロード)と子ターン: 書いた順に 1 回だけ
  実行し、before → 本文 → after の 1 つの結果になる / 2 ターン目は発火し直さず同じ並び
- このターンに無い tool は実行せず ERROR 行 / 例外も ERROR 行で、残りも本文も入る
- llens の emr.md の形(run_shell の before)がそのまま通る
- 永続化の形: 発火のブロックは in_payload の印で同じターンの追撃では二重にせず、保存では
  落ちて次のターンからは通常の tool 往復として読まれる
"""

import asyncio
import json
import types

from starlette.requests import Request

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

# llens の emr.md の形
EMR_BEFORE = [
    {
        "tool": "run_shell",
        "args": {
            "command": "ls /ext/groups/EMR/notes/ 2>/dev/null; "
            "cat /ext/groups/EMR/notes/全体.md 2>/dev/null; true"
        },
    }
]


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


def _skill_result(messages, call_id):
    """messages の中の load_skill の結果(call id で引く)の本文。"""
    content = next(
        m["content"]
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id
    )
    return json.loads(content)["result"]


def _in_order(text, *parts):
    positions = [text.index(part) for part in parts]
    assert positions == sorted(positions), (parts, text)


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

    def _tool_entry(self, name="probe", fail=False):
        calls = self.probe_calls

        async def probe(x: int = 0, command: str = ""):
            calls.append(command or x)
            if fail:
                raise RuntimeError("probe exploded")
            return f"{name} result {command or x}"

        return {
            "tool_id": "probe-tools",
            "spec": {
                "name": name,
                "description": name,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "command": {"type": "string"},
                    },
                },
            },
            "callable": probe,
        }

    def _patch_get_tools(self, monkeypatch, fail=False, names=("probe",)):
        from nucllei.utils import middleware

        entries = {name: self._tool_entry(name, fail=fail) for name in names}

        def fake_get_tools(request, tool_ids, user, extra_params, **kwargs):
            return dict(entries) if "probe-tools" in (tool_ids or []) else {}

        monkeypatch.setattr(middleware, "get_tools", fake_get_tools)

    def _payload(self, messages, model, metadata=None, files=None):
        from nucllei.main import app
        from nucllei.utils import middleware

        saved = getattr(app.state, "MODELS", None)
        app.state.MODELS = {model["id"]: model}
        try:
            form_data = {"model": model["id"], "stream": True, "messages": messages}
            if files:
                form_data["files"] = files
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

        応答の先頭は発火の tool_calls ブロック(process_chat_response の seed)。保存されるのは
        snapshot_content_blocks の形(in_payload の印は落ちる)。FE は tool_calls ブロックを
        tool 往復に展開する(chat-messages.ts expandAssistantToolHistory と同じ形 — ここでは
        backend の convert_content_blocks_to_messages で代用する)。
        """
        from nucllei.utils.content_blocks import (
            convert_content_blocks_to_messages,
            snapshot_content_blocks,
        )
        from nucllei.utils.skills import skill_activation_seed_block

        blocks = []
        if metadata.get("skill_activations"):
            blocks.append(skill_activation_seed_block(metadata["skill_activations"]))
        blocks.append({"type": "text", "content": "わかりました"})
        saved = snapshot_content_blocks(blocks)
        assert all("in_payload" not in block for block in saved)
        user_messages = [m for m in first_messages if m.get("role") == "user"]
        return [
            *user_messages,
            *convert_content_blocks_to_messages(saved),
            {"role": "user", "content": "つづけて"},
        ]

    def _editor(self, user_id="7"):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.models.users import Users

        Users.insert_new_user(
            id=user_id, name="editor", email=f"e{user_id}@hospital.example", role="user"
        )
        group = Groups.insert_new_group(
            "1",
            GroupForm(
                name=f"skill-editors-{user_id}",
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

    # ── API ─────────────────────────────────────────────────────

    def test_create_stores_and_normalizes(self):
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "emr",
                    "name": "EMR",
                    "content": "c",
                    "before": [
                        {"tool": " read_file ", "args": {"path": "/ext/a.md"}},
                        {"tool": "probe"},
                    ],
                    "after": [{"tool": "probe", "args": None}],
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "on_load" not in body
        assert body["before"] == [
            {"tool": "read_file", "args": {"path": "/ext/a.md"}},
            {"tool": "probe", "args": {}},
        ]
        assert body["after"] == [{"tool": "probe", "args": {}}]
        # 省略した skill は空
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "plain", "name": "p", "content": "c"},
            )
        assert response.json()["before"] == [] and response.json()["after"] == []

    def test_emr_shape_is_accepted(self):
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "emr", "name": "EMR", "content": "c", "before": EMR_BEFORE},
            )
        assert response.status_code == 200, response.text
        assert response.json()["before"] == EMR_BEFORE

    def test_validation_errors_are_400(self):
        bad = [
            {"before": {"tool": "probe"}},
            {"after": ["probe"]},
            {"before": [{"tool": ""}]},
            {"before": [{"tool": 1}]},
            {"before": [{"tool": "probe", "args": [1]}]},
            {"after": [{"tool": "probe", "when": "x"}]},
            # 短い記法・読み込みの要素は無い(skill から別の skill やレシピは読み込まない)
            {"before": [{"skill": "base"}]},
            {"before": [{"recipe": "/workspace/recipes/a"}]},
            {"after": [{"read": "/ext/public/a.md"}]},
            {"before": [{"tool": "load_skill", "args": {"skill_id": "base"}}]},
            {
                "after": [
                    {"tool": "load_recipe", "args": {"path": "/workspace/recipes/a"}}
                ]
            },
        ]
        for extra in bad:
            with mock_webui_user(id="1", role="admin"):
                response = self.fast_api_client.post(
                    self.create_url("/create"),
                    json={"id": "bad", "name": "b", "content": "c", **extra},
                )
            assert response.status_code == 400, (extra, response.text)
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={
                    "id": "bad",
                    "name": "b",
                    "content": "c",
                    "before": [{"tool": "load_skill", "args": {"skill_id": "x"}}],
                },
            )
        assert "load_skill は書けない" in response.json()["detail"]

    def test_hooks_are_admin_only(self):
        self._editor()
        hooks = [{"tool": "probe", "args": {}}]
        with mock_webui_user(id="7", role="user"):
            # 空のままなら作れる・更新できる
            response = self.fast_api_client.post(
                self.create_url("/create"),
                json={"id": "mine", "name": "m", "content": "c", "before": []},
            )
            assert response.status_code == 200, response.text
            response = self.fast_api_client.post(
                self.create_url("/id/mine/update"),
                json={"id": "mine", "name": "m", "content": "c2"},
            )
            assert response.status_code == 200, response.text
            for field in ("before", "after"):
                response = self.fast_api_client.post(
                    self.create_url("/create"),
                    json={"id": f"h{field}", "name": "h", "content": "c", field: hooks},
                )
                assert response.status_code == 401, field
                response = self.fast_api_client.post(
                    self.create_url("/id/mine/update"),
                    json={"id": "mine", "name": "m", "content": "c", field: hooks},
                )
                assert response.status_code == 401, field
        # admin が before / after を付けた skill は、editor が自分の物でも編集・削除できない
        # (省略した更新・空にする更新も不可)
        self._skill("adminhook", owner="7", after=hooks)
        with mock_webui_user(id="7", role="user"):
            for extra in ({}, {"after": []}):
                response = self.fast_api_client.post(
                    self.create_url("/id/adminhook/update"),
                    json={"id": "adminhook", "name": "h", "content": "x", **extra},
                )
                assert response.status_code == 401, extra
            response = self.fast_api_client.delete(
                self.create_url("/id/adminhook/delete")
            )
            assert response.status_code == 401

    def test_update_omitted_keeps_explicit_empty_clears(self):
        self._skill(
            "keep",
            before=[{"tool": "probe", "args": {"x": 1}}],
            after=[{"tool": "probe", "args": {"x": 2}}],
        )
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/id/keep/update"),
                json={"id": "keep", "name": "k", "content": "new"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["before"] == [{"tool": "probe", "args": {"x": 1}}]
        assert response.json()["after"] == [{"tool": "probe", "args": {"x": 2}}]
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/id/keep/update"),
                json={"id": "keep", "name": "k", "content": "new", "before": []},
            )
        assert response.json()["before"] == []
        assert response.json()["after"] == [{"tool": "probe", "args": {"x": 2}}]

    # ── /command ───────────────────────────────────────────────

    def test_command_runs_once_and_keeps_order_on_next_turn(self, monkeypatch):
        self._patch_get_tools(monkeypatch)
        # probe は skill の tool(model の toolIds には無い)
        self._skill(
            "emr",
            tool_ids=["probe-tools"],
            before=[
                {"tool": "probe", "args": {"x": 1}},
                {"tool": "probe", "args": {"x": 2}},
            ],
            after=[{"tool": "probe", "args": {"x": 3}}],
        )
        model = _model("emr")

        first = [{"role": "user", "content": "/emr 開始"}]
        form_data, metadata = self._payload(list(first), model)
        assert self.probe_calls == [1, 2, 3]
        # 1 つの load_skill の結果: before(書いた順)→ 本文 → after。他の tool 往復は無い
        assert [
            m.get("tool_call_id") for m in form_data["messages"] if m["role"] == "tool"
        ] == ["skill:emr:0"]
        result = _skill_result(form_data["messages"], "skill:emr:0")
        _in_order(
            result,
            '<skill-hook skill="emr" phase="before" n="1" tool="probe">',
            "### 読み込み時に実行した probe の結果 (before 1)",
            "probe result 1",
            "probe result 2",
            "【emr の手順書】",
            "### 手順書の後に実行した probe の結果 (after 1)",
            "probe result 3",
        )
        # 応答に残すのも同じ結果(モデルに渡したものと同じ文字列)
        (seed,) = metadata["skill_activations"]
        assert seed["call_id"] == "skill:emr:0"
        assert seed["result"] == form_data["messages"][2]["content"]
        assert json.loads(seed["result"])["tools"] == ["probe"]
        assert [(h["phase"], h["n"], h["status"]) for h in seed["hooks"]] == [
            ("before", 0, "ok"),
            ("before", 1, "ok"),
            ("after", 0, "ok"),
        ]

        # 次のリクエスト: 発火の往復が履歴にあるので発火し直さず、実行もしない。並びもそのまま
        history = self._next_turn_history(first, metadata)
        form_data, metadata = self._payload(history, model)
        assert self.probe_calls == [1, 2, 3]
        assert "skill_activations" not in metadata
        sent = form_data["messages"]
        assert _called_skill_ids(sent) == ["emr"]
        assert _skill_result(sent, "skill:emr:0") == result
        # 履歴の発火から skill の tool も載り続ける(replay_skill_tool_ids)
        assert "probe" in metadata["tools"]

    def test_command_without_hooks_is_body_only(self, monkeypatch):
        self._patch_get_tools(monkeypatch)
        self._skill("plain")
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/plain"}], _model("plain")
        )
        assert (
            _skill_result(form_data["messages"], "skill:plain:0")
            == "【plain の手順書】"
        )
        assert "hooks" not in metadata["skill_activations"][0]

    def test_unavailable_tool_is_error_line(self, monkeypatch):
        self._patch_get_tools(monkeypatch)
        self._skill(
            "emr",
            before=[{"tool": "probe"}],
            after=[{"tool": "read_file", "args": {"path": "/ext/a.md"}}],
        )
        # probe-tools がモデルに無い = このターンに無い(read ACL で落ちた tool も同じ)
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/emr"}], _model("emr")
        )
        assert self.probe_calls == []
        result = _skill_result(form_data["messages"], "skill:emr:0")
        _in_order(
            result,
            "ERROR: skill emr の読み込み時に実行する probe は使えない (このターンの tool の一覧に無い)。"
            "次の手: その結果無しで手順書に従う。",
            "【emr の手順書】",
            "ERROR: skill emr の読み込み時に実行する read_file は使えない",
        )
        hooks = metadata["skill_activations"][0]["hooks"]
        assert [h["status"] for h in hooks] == ["unavailable", "unavailable"]

    def test_exception_is_error_and_load_continues(self, monkeypatch):
        self._patch_get_tools(monkeypatch, fail=True)
        self._skill(
            "emr",
            before=[{"tool": "probe", "args": {"x": 3}}],
            after=[{"tool": "probe", "args": {"x": 4}}],
        )
        form_data, metadata = self._payload(
            [{"role": "user", "content": "/emr"}],
            _model("emr", tool_ids=["probe-tools"]),
        )
        # 1 つ目が失敗しても残りも実行し、本文も入る
        assert self.probe_calls == [3, 4]
        before, after = _skill_result(form_data["messages"], "skill:emr:0").split(
            "【emr の手順書】"
        )
        assert "probe exploded" in before and "probe exploded" in after
        hooks = metadata["skill_activations"][0]["hooks"]
        assert [h["status"] for h in hooks] == ["error", "error"]

    def test_emr_shape_runs_shell_before_body(self, monkeypatch):
        self._patch_get_tools(monkeypatch, names=("run_shell",))
        self._skill("emr", before=EMR_BEFORE)
        form_data, _ = self._payload(
            [{"role": "user", "content": "/emr"}],
            _model("emr", tool_ids=["probe-tools"]),
        )
        assert self.probe_calls == [EMR_BEFORE[0]["args"]["command"]]
        _in_order(
            _skill_result(form_data["messages"], "skill:emr:0"),
            "### 読み込み時に実行した run_shell の結果 (before 1)",
            "run_shell result ls /ext/groups/EMR/notes/",
            "【emr の手順書】",
        )

    def test_hook_may_use_the_skills_own_tool(self, monkeypatch):
        """skill の tool_ids で入る tool も before / after から使える(tool を載せた後に実行)。"""
        self._patch_get_tools(monkeypatch)
        self._skill(
            "emr",
            tool_ids=["probe-tools"],
            before=[{"tool": "probe", "args": {"x": 8}}],
        )
        form_data, _ = self._payload(
            [{"role": "user", "content": "/emr"}], _model("emr")
        )
        assert self.probe_calls == [8]
        content = json.loads(form_data["messages"][2]["content"])
        assert content["tools"] == ["probe"]
        assert "probe result 8" in content["result"]

    # ── 自動ロード ─────────────────────────────────────────────

    def test_auto_load_runs_once_and_keeps_order(self, monkeypatch):
        from nucllei.models.files import FileForm, Files

        self._patch_get_tools(monkeypatch)
        self._skill(
            "audio",
            auto_load_content_types=["audio/"],
            before=[{"tool": "probe", "args": {"x": 5}}],
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
        files = [{"type": "file", "id": "rec"}]
        first = [{"role": "user", "content": "文字起こしして"}]
        form_data, metadata = self._payload(list(first), model, files=files)
        assert self.probe_calls == [5]
        result = _skill_result(form_data["messages"], "skill:audio:auto")
        _in_order(result, "probe result 5", "【audio の手順書】")

        history = self._next_turn_history(first, metadata)
        form_data, metadata = self._payload(history, model, files=files)
        assert self.probe_calls == [5]
        assert "skill_activations" not in metadata
        assert _called_skill_ids(form_data["messages"]) == ["audio"]
        assert _skill_result(form_data["messages"], "skill:audio:auto") == result

    # ── モデルの load_skill ────────────────────────────────────

    def test_model_load_skill_composes_one_result_once(self):
        from nucllei.utils.skills import (
            LOADED_RESULT,
            build_load_skill_tool,
            run_skill_hooks,
        )

        self._skill(
            "emr",
            tool_ids=["emr-tools"],
            before=[{"tool": "probe", "args": {"x": 1}}],
            after=[{"tool": "missing"}, {"tool": "probe", "args": {"x": 2}}],
        )
        order = []
        tools = {"probe": self._tool_entry()}

        async def load_tools(skill):
            order.append("tools")
            return ["emr_fn"]

        async def execute(calls):
            order.extend(json.loads(c["function"]["arguments"])["x"] for c in calls)
            return [
                {"tool_call_id": c["id"], "content": f"ok {c['id']}", "duration": 0.0}
                for c in calls
            ]

        async def run_hooks(skill):
            return await run_skill_hooks(skill, tools, execute)

        entry = build_load_skill_tool(
            _model("emr"), self.user, load_tools=load_tools, run_hooks=run_hooks
        )
        out = json.loads(asyncio.run(entry["callable"](skill_id="emr")))
        # tool を載せてから before / after(書いた順、1 つずつ)
        assert order == ["tools", 1, 2]
        assert out["tools"] == ["emr_fn"]
        _in_order(
            out["result"],
            "ok skill:emr:before:0",
            "【emr の手順書】",
            "ERROR: skill emr の読み込み時に実行する missing は使えない",
            "ok skill:emr:after:1",
        )
        # 同じターンの 2 回目は短い結果で、実行もしない
        again = json.loads(asyncio.run(entry["callable"](skill_id="emr")))
        assert again["result"] == LOADED_RESULT.format(skill_id="emr")
        assert order == ["tools", 1, 2, "tools"]

        # 次のリクエスト: 履歴に load_skill の結果がある → 短い結果で、実行もしない
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
            {
                "role": "tool",
                "tool_call_id": "functions.load_skill:0",
                "content": json.dumps(out),
            },
        ]
        entry = build_load_skill_tool(
            _model("emr"), self.user, messages=history, run_hooks=run_hooks
        )
        again = json.loads(asyncio.run(entry["callable"](skill_id="emr")))
        assert again["result"] == LOADED_RESULT.format(skill_id="emr")
        assert order == ["tools", 1, 2, "tools"]

    def test_model_load_skill_through_payload_uses_turn_tools(self, monkeypatch):
        """process_chat_payload が組む load_skill は、そのターンの tool で before を実行する。"""
        self._patch_get_tools(monkeypatch)
        self._skill("emr", before=[{"tool": "probe", "args": {"x": 6}}])
        _, metadata = self._payload(
            [{"role": "user", "content": "診療録"}],
            _model("emr", tool_ids=["probe-tools"]),
        )
        entry = metadata["tools"]["load_skill"]
        out = json.loads(asyncio.run(entry["callable"](skill_id="emr")))
        assert self.probe_calls == [6]
        _in_order(out["result"], "probe result 6", "【emr の手順書】")

    # ── 子ターン ───────────────────────────────────────────────

    def test_hooks_in_subagent_turn(self, monkeypatch):
        """子ターン(run_subagent — start_internal_turn が agent_session の印を付けて
        process_chat_payload を通す)でも同じ経路で 1 回だけ実行される。"""
        self._patch_get_tools(monkeypatch)
        self._skill("emr", before=[{"tool": "probe", "args": {"x": 4}}])
        model = _model("emr", tool_ids=["probe-tools"])
        child = {"chat_id": "child-chat", "message_id": "m1", "agent_session": True}
        first = [{"role": "user", "content": "/emr 調べて"}]
        form_data, metadata = self._payload(list(first), model, metadata=child)
        assert self.probe_calls == [4]
        history = self._next_turn_history(first, metadata)
        self._payload(history, model, metadata=child)
        assert self.probe_calls == [4]

    # ── 永続化の形 ─────────────────────────────────────────────

    def test_seed_block_skipped_in_turn_kept_after_save(self):
        from nucllei.utils.content_blocks import (
            convert_content_blocks_to_messages,
            snapshot_content_blocks,
        )
        from nucllei.utils.skills import skill_activation_seed_block

        block = skill_activation_seed_block(
            [{"call_id": "skill:emr:0", "id": "emr", "result": '{"result": "x"}'}]
        )
        # 同じターン(payload に注入済み): 追撃では再送しない
        assert block["in_payload"] is True
        assert convert_content_blocks_to_messages([block]) == []
        # 保存後(次のターン): 通常の load_skill 往復
        saved = snapshot_content_blocks([block])
        assert "in_payload" not in saved[0]
        messages = convert_content_blocks_to_messages(saved)
        assert [m["role"] for m in messages] == ["assistant", "tool"]
        assert _called_skill_ids(messages) == ["emr"]

    # ── カードの木 (results[i].chain) ─────────────────────────

    def test_seed_block_carries_chain_not_sent_to_model(self, monkeypatch):
        """発火のブロックの結果に before / after の木と本文が載る(モデルには渡らない)。"""
        from nucllei.utils.content_blocks import (
            convert_content_blocks_to_messages,
            snapshot_content_blocks,
        )
        from nucllei.utils.skills import skill_activation_seed_block

        self._patch_get_tools(monkeypatch)
        self._skill(
            "emr",
            before=[{"tool": "probe", "args": {"x": 1}}],
            after=[{"tool": "missing"}],
        )
        _, metadata = self._payload(
            [{"role": "user", "content": "/emr"}],
            _model("emr", tool_ids=["probe-tools"]),
        )
        block = skill_activation_seed_block(metadata["skill_activations"])
        chain = block["results"][0]["chain"]
        assert chain["body"] == "【emr の手順書】"
        assert [
            (n["phase"], n["kind"], n["target"], n["status"]) for n in chain["entries"]
        ] == [("before", "tool", "probe", "ok"), ("after", "tool", "missing", "error")]
        assert chain["entries"][0]["output"] == "probe result 1"
        assert chain["entries"][0]["args"] == {"x": 1}
        assert chain["entries"][1]["reason"].startswith(
            "ERROR: skill emr の読み込み時に実行する missing は使えない"
        )
        saved = snapshot_content_blocks([block])
        assert saved[0]["results"][0]["chain"] == chain  # 保存される
        (tool_message,) = [
            m for m in convert_content_blocks_to_messages(saved) if m["role"] == "tool"
        ]
        assert set(tool_message) == {"role", "tool_call_id", "content"}

    def test_model_load_skill_sets_chain_on_its_sink(self):
        from nucllei.utils.skills import build_load_skill_tool, run_skill_hooks
        from nucllei.utils.tool_output import ToolOutputSink, current_sink

        self._skill("emr", before=[{"tool": "probe", "args": {"x": 2}}])
        tools = {"probe": self._tool_entry()}

        async def execute(calls):
            return [
                {"tool_call_id": c["id"], "content": "ok", "duration": 0.0}
                for c in calls
            ]

        async def run_hooks(skill):
            return await run_skill_hooks(skill, tools, execute)

        entry = build_load_skill_tool(_model("emr"), self.user, run_hooks=run_hooks)
        sink = ToolOutputSink("functions.load_skill:0")

        async def go():
            token = current_sink.set(sink)
            try:
                return await entry["callable"](skill_id="emr")
            finally:
                current_sink.reset(token)

        asyncio.run(go())
        chain = sink.chain()
        assert chain["body"] == "【emr の手順書】"
        assert [(n["phase"], n["status"]) for n in chain["entries"]] == [
            ("before", "ok")
        ]
