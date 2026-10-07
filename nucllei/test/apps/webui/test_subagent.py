"""run_subagent(agent session 実行系 — issue #107 / #140)の回帰テスト。

- 入口は builtin `builtin_subagent`(#140): get_tools 経由で載る。静的 spec(sync 時)/
  tool 行の read ACL / `available` フック(allowlist ∩ アクセス可・depth 上限・
  chat 文脈)/ `spec_overrides` フック(preset_id の enum と preset 一覧)
- 実行: 子 chat の spawn(kind="agent"・親リンク・決定的タイトル)→ 子ターン完走 →
  最終 assistant テキストだけが親 tool result に返る(transcript は返らない)
- session_id 継続 / 実行時再認可・depth 再チェック / 同時数上限 /
  stop 連鎖(親タスク cancel → 子タスク cancel)
- 親ターン統合: 実イベントバス + SSE EOF 完走待ちで、親の継続リクエストの
  tool result に answer が載る(test_native_tool_calling.py のハーネス同型)

子ターンのモデル呼び出し(subagent.generate_chat_completion)は実キャプチャ
fixture の SSE リプレイで代替し、payload 段(process_chat_payload)は素通しに
差し替える。応答組み立て・永続化(process_chat_response)は実物を使う —
子 chat の履歴・usage が DB(postgres)に実際に書かれることまで固定する。
"""

import asyncio
import json
import types

from starlette.responses import StreamingResponse

from test.util import model_frames as mf
from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.tool_errors import call_error


def _raised(coro):
    """run_subagent の入力・認可・上限エラーは ToolError の raise(#145)。それを返す。"""
    return asyncio.run(call_error(coro))


def _sse_response(name: str) -> StreamingResponse:
    """実キャプチャ SSE を行単位で流す(test_native_tool_calling.py と同型)。"""
    raw = mf.load_raw(name).decode("utf-8")

    async def gen():
        for line in raw.split("\n"):
            yield line + "\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _read_sse_events(response):
    """POST 応答 SSE を読み切り、封筒の data を集める(EOF = タスク完走待ち)。"""
    events = []
    async for line in response.body_iterator:
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        for part in line.split("\n"):
            if part.startswith("data: "):
                events.append(json.loads(part[len("data: ") :])["data"])
    return events


class TestRunSubagent(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.builtin_tools import sync_builtin_tools
        from nucllei.models.chats import ChatForm, Chats
        from nucllei.models.models import ModelForm, Models
        from nucllei.models.users import Users
        from nucllei.utils import subagent

        self.Chats = Chats
        self.Models = Models
        # builtin 行(#140 — run_subagent は Tools 行の builtin)と、callable が
        # __user__ から引き直す実ユーザー行
        sync_builtin_tools()
        Users.insert_new_user(
            id="1", name="John Doe", email="john.doe@nucllei.local", role="user"
        )
        # 失敗テストのカウンタ・実行中集合の残留が後続へ漏れないように毎回リセット
        subagent._active_turns = 0
        subagent._running_agent_sessions.clear()

        Models.insert_new_model(
            ModelForm(
                id="researcher",
                name="Researcher",
                meta={"description": "調査担当"},
                params={},
                access_control=None,
            ),
            "1",
        )
        self.parent = Chats.insert_new_chat(
            "1",
            ChatForm(
                chat={
                    "title": "parent",
                    "models": ["kimi"],
                    "history": {
                        "currentId": "pm-a",
                        "messages": {
                            "pm-u": {
                                "id": "pm-u",
                                "parentId": None,
                                "childrenIds": ["pm-a"],
                                "role": "user",
                                "content": "調査依頼",
                            },
                            "pm-a": {
                                "id": "pm-a",
                                "parentId": "pm-u",
                                "childrenIds": [],
                                "role": "assistant",
                                "content": "",
                            },
                        },
                    },
                }
            ),
        )

    def _user(self, id="1", role="user"):
        return types.SimpleNamespace(
            id=id, email="john.doe@nucllei.local", name="John Doe", role=role
        )

    def _request(self):
        # 実 Request の外形(app.state / request.state)だけを持つ薄い fake
        return types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(
                    WEBUI_NAME="llens",
                    config=types.SimpleNamespace(WEBUI_URL="http://localhost"),
                    MODELS={"researcher": {"id": "researcher", "name": "Researcher"}},
                    TOOLS={},
                )
            ),
            state=types.SimpleNamespace(),
        )

    def _parent_model(self, preset_ids=("researcher",)):
        meta = {
            "subagentPresetIds": list(preset_ids),
            "toolIds": ["builtin_subagent"],
        }
        return {"id": "kimi", "info": {"meta": meta}}

    def _metadata(self):
        return {"chat_id": self.parent.id, "message_id": "pm-a"}

    def _build_tools(self, request=None, model=None, metadata=None, user=None):
        """チャット経路と同じ get_tools(builtin 行 → モジュール)で載せる(#140)。

        model は親ターンの preset — チャット経路と同じく metadata["model"] に載せる
        (フックと callable はここから allowlist を読む)。
        """
        from nucllei.utils.tools import get_tools

        request = request if request is not None else self._request()
        user = user if user is not None else self._user()
        metadata = metadata if metadata is not None else self._metadata()
        metadata["model"] = model if model is not None else self._parent_model()
        return get_tools(
            request,
            ["builtin_subagent"],
            user,
            {
                "__user__": {"id": user.id, "role": user.role},
                "__metadata__": metadata,
                "__request__": request,
                # タスクモデル(フックは見ない — metadata["model"] が優先)
                "__model__": {"id": "task-model", "info": {"meta": {}}},
            },
        )

    def _build_tool(self, request=None, model=None, metadata=None, user=None):
        tools = self._build_tools(request, model, metadata, user)
        return tools.get("run_subagent")

    def _patch_child_pipeline(self, monkeypatch, child_responses=("text_basic",)):
        """子ターン 3 段のうちモデル呼び出しを fixture リプレイ、payload 段を素通しに。

        process_chat_response(組み立て・永続化・タスク)は実物のまま。
        戻り値は子ターンへ渡った form_data の列。
        """
        from nucllei.utils import middleware, subagent

        child_calls = []
        followups = list(child_responses)

        async def fake_child_completion(request, form_data, user):
            child_calls.append(form_data)
            return _sse_response(followups.pop(0) if followups else "text_basic")

        async def fake_payload(
            request, form_data, user, metadata, model, events_stream
        ):
            return form_data, metadata, []

        monkeypatch.setattr(subagent, "generate_chat_completion", fake_child_completion)
        monkeypatch.setattr(middleware, "process_chat_payload", fake_payload)
        monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda model: [])
        return child_calls

    def _patch_gated_child_pipeline(self, monkeypatch):
        """子ターンを gate 解放まで走行中に保つ(background / await 系 — #109)。

        返す asyncio.Event の set() まで子タスクは完走しない(先頭 delta の
        content "2" だけ流して堰き止める)。
        """
        from nucllei.utils import middleware, subagent

        gate = asyncio.Event()

        async def gated_child_completion(request, form_data, user):
            async def gen():
                yield (
                    'data: {"choices":[{"index":0,"delta":'
                    '{"role":"assistant","content":"2"},"finish_reason":null}]}\n'
                )
                await gate.wait()
                yield "data: [DONE]\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        async def fake_payload(
            request, form_data, user, metadata, model, events_stream
        ):
            return form_data, metadata, []

        monkeypatch.setattr(
            subagent, "generate_chat_completion", gated_child_completion
        )
        monkeypatch.setattr(middleware, "process_chat_payload", fake_payload)
        monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda model: [])
        return gate

    async def _wait_turn_slot_release(self):
        """子 task の done callback(call_soon 経由)が会計を解放するまで譲る。"""
        from nucllei.utils import subagent

        for _ in range(10):
            if subagent._active_turns == 0:
                return
            await asyncio.sleep(0)

    # ---- mount 条件 ----

    def test_builtin_registered_with_generic_static_spec(self):
        """#140: builtin 行の静的 spec(sync 時)は enum 無しの汎用形で、フック
        (available / spec_overrides)は関数として spec 化されない。"""
        from nucllei.builtin_tools import BUILTIN_TOOLS
        from nucllei.models.tools import Tools

        assert "builtin_subagent" in BUILTIN_TOOLS
        row = Tools.get_tool_by_id("builtin_subagent")
        assert row is not None and row.meta.builtin is True
        assert [s["name"] for s in row.specs] == ["run_subagent"]
        spec = row.specs[0]
        props = spec["parameters"]["properties"]
        assert set(props) == {"preset_id", "instruction", "session_id"}
        assert "enum" not in props["preset_id"]
        assert spec["parameters"]["required"] == ["instruction"]

    def test_mounts_with_accessible_preset(self):
        from nucllei.env import SUBAGENT_TURN_TIMEOUT_S

        tool = self._build_tool()
        assert tool is not None
        assert tool["tool_id"] == "builtin_subagent"
        spec = tool["spec"]
        assert spec["name"] == "run_subagent"
        # spec_overrides: preset_id の enum と description 末尾の preset 一覧
        assert spec["parameters"]["properties"]["preset_id"]["enum"] == ["researcher"]
        assert spec["parameters"]["properties"]["preset_id"]["type"] == "string"
        assert spec["parameters"]["required"] == ["instruction"]
        assert "利用可能な preset:\n- researcher: Researcher — 調査担当" in (
            spec["description"]
        )
        # クラス docstring(運用規律)が toolkit 注記として 1 回前置される
        assert spec["description"].startswith("【Subagent ツール群 共通】")
        assert "同時 4 本まで" in spec["description"]
        # per-tool timeout(クラス属性 timeouts)と表示ディスクリプタ(戻り型の Annotated)
        assert tool["timeout"] == SUBAGENT_TURN_TIMEOUT_S
        assert tool["render"] == {"result": {"kind": "subagent"}}

    def test_spec_overrides_do_not_leak_into_stored_spec(self):
        """ターン時のパッチ(user × preset 依存)は DB の静的 spec を汚さない。"""
        from nucllei.models.tools import Tools

        assert self._build_tool() is not None
        spec = Tools.get_tool_by_id("builtin_subagent").specs[0]
        assert "enum" not in spec["parameters"]["properties"]["preset_id"]

    def test_not_mounted_when_allowlist_empty(self):
        assert self._build_tool(model=self._parent_model(preset_ids=())) is None
        # 旧 meta.features.subagent は撤去(#140)— もう読まない
        model = self._parent_model()
        model["info"]["meta"]["features"] = {"subagent": False}
        assert self._build_tool(model=model) is not None

    def test_tool_row_acl_gates_per_user(self):
        """#140 の芯: tool 行の read ACL でユーザー単位に止められる。"""
        from nucllei.models.tools import Tools

        # 私有化(所有者 system のみ)→ 一般ユーザーには載らない
        Tools.update_tool_by_id("builtin_subagent", {"access_control": {}})
        assert self._build_tool() is None
        # read を付与したユーザーには載る
        Tools.update_tool_by_id(
            "builtin_subagent",
            {"access_control": {"read": {"group_ids": [], "user_ids": ["1"]}}},
        )
        assert self._build_tool() is not None
        assert self._build_tool(user=self._user(id="2")) is None
        # admin はバイパス(既存セマンティクス)
        assert self._build_tool(user=self._user(id="9", role="admin")) is not None

    def test_not_mounted_without_chat_context(self):
        # 素の completions(chat_id / message_id 無し)では子を紐づけられない
        assert self._build_tool(metadata={}) is None
        assert self._build_tool(metadata={"chat_id": self.parent.id}) is None
        assert self._build_tool(metadata={"message_id": "pm-a"}) is None
        # 実在しない親 chat でも載せない
        assert (
            self._build_tool(metadata={"chat_id": "no-such", "message_id": "x"}) is None
        )

    def test_not_mounted_when_depth_limit_reached(self):
        from nucllei.env import SUBAGENT_MAX_DEPTH
        from nucllei.models.chats import ChatForm

        deep = self.Chats.insert_new_chat(
            "1",
            ChatForm(
                chat={"title": "deep", "history": {"currentId": None, "messages": {}}}
            ),
            kind="agent",
            parent_chat_id=self.parent.id,
            parent_message_id="pm-a",
            depth=SUBAGENT_MAX_DEPTH,
        )
        assert (
            self._build_tool(metadata={"chat_id": deep.id, "message_id": "x"}) is None
        )

    def test_enum_excludes_inaccessible_but_includes_inactive_presets(self):
        from nucllei.models.models import ModelForm

        # 他人の私有 preset(access_control={})は enum に出ない。
        # is_active=False は「ユーザーのモデル一覧に出ない」だけで、subagent の
        # spawnable としては選択できる(2026-08-14 仕様変更)— enum に載る。
        self.Models.insert_new_model(
            ModelForm(
                id="private", name="Private", meta={}, params={}, access_control={}
            ),
            "other-user",
        )
        self.Models.insert_new_model(
            ModelForm(
                id="inactive",
                name="Inactive",
                meta={},
                params={},
                access_control=None,
                is_active=False,
            ),
            "1",
        )
        tool = self._build_tool(
            model=self._parent_model(
                ("researcher", "private", "inactive", "no-such-preset")
            )
        )
        # description の一覧も enum と同じ集合
        assert "- private:" not in tool["spec"]["description"]
        assert "- inactive: Inactive" in tool["spec"]["description"]
        assert tool["spec"]["parameters"]["properties"]["preset_id"]["enum"] == [
            "researcher",
            "inactive",
        ]
        # アクセス可 preset がゼロなら tool 自体を載せない
        assert self._build_tool(model=self._parent_model(("private",))) is None

    # ---- 実行 ----

    def test_spawn_runs_child_turn_and_returns_answer(self, monkeypatch):
        child_calls = self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        result = asyncio.run(
            tool["callable"](preset_id="researcher", instruction="1+1 は?")
        )

        assert result["ok"] is True
        child_id = result["session_id"]
        # answer は子の最終 assistant テキストのみ(text_basic の content = "2")
        assert result["answer"] == "2"
        assert "truncated" not in result
        # usage は子 message の実測(text_basic.sse の usage フレーム)の要点
        assert result["usage"]["prompt_tokens"] == 22
        assert result["usage"]["completion_tokens"] == 66
        assert result["usage"]["duration"] >= 0

        child = self.Chats.get_chat_by_id(child_id)
        assert child.kind == "agent"
        assert child.parent_chat_id == self.parent.id
        assert child.parent_message_id == "pm-a"
        assert child.depth == 1
        # 決定的タイトル(生成タスクは走らせない)
        assert child.title == "Researcher — 1+1 は?"

        # 子 history: user(instruction)+ assistant(完走した応答が永続化済み)
        messages = child.chat["history"]["messages"]
        by_role = {m["role"]: m for m in messages.values()}
        assert set(by_role.keys()) == {"user", "assistant"}
        assert by_role["user"]["content"] == "1+1 は?"
        assert by_role["assistant"]["content"] == "2"
        assert by_role["assistant"]["model"] == "researcher"

        # 子ターンへの wire: instruction のみ・モデルは preset・agent_session マーカ
        assert len(child_calls) == 1
        assert child_calls[0]["model"] == "researcher"
        assert child_calls[0]["messages"] == [{"role": "user", "content": "1+1 は?"}]
        assert child_calls[0]["metadata"]["agent_session"] is True
        assert child_calls[0]["metadata"]["chat_id"] == child_id

        # 子はユーザー向け一覧(L4)に出ない(admin/監査系の get_chats には出る)
        listed = {c.id for c in self.Chats.get_chats_by_user_id("1")}
        assert self.parent.id in listed
        assert child_id not in listed
        assert child_id in {c.id for c in self.Chats.get_chats()}

    def test_child_turn_expands_parent_user_role_and_groups(self, monkeypatch):
        """子ターンの preset system prompt の {{USER_*}} は親ターンのユーザーの値。

        子の generate_chat_completion に渡る user で、openai ルーターと同じ
        apply_model_system_prompt_to_body を掛けて確かめる(payload 段は素通し)。
        """
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups
        from nucllei.models.models import ModelForm
        from nucllei.utils import subagent
        from nucllei.utils.payload import apply_model_system_prompt_to_body

        for name, user_ids in (("b-team", ["1"]), ("Alpha", ["1"]), ("other", ["9"])):
            group = Groups.insert_new_group("1", GroupForm(name=name, description=""))
            Groups.update_group_by_id(
                group.id,
                GroupUpdateForm(name=name, description="", user_ids=user_ids),
            )
        self.Models.update_model_by_id(
            "researcher",
            ModelForm(
                id="researcher",
                name="Researcher",
                meta={"description": "調査担当"},
                params={"system": "{{USER_NAME}}/{{USER_ROLE}}/[{{USER_GROUPS}}]"},
                access_control=None,
            ),
        )
        self._patch_child_pipeline(monkeypatch)
        systems = []

        async def child_completion(request, form_data, user):
            model_info = self.Models.get_model_by_id(form_data["model"])
            payload = apply_model_system_prompt_to_body(
                model_info.params.model_dump(),
                {"messages": list(form_data["messages"])},
                form_data.get("metadata"),
                user,
                model_info,
            )
            systems.append(payload["messages"][0]["content"])
            return _sse_response("text_basic")

        monkeypatch.setattr(subagent, "generate_chat_completion", child_completion)
        tool = self._build_tool()
        result = asyncio.run(tool["callable"](preset_id="researcher", instruction="q"))

        assert result["ok"] is True
        assert systems == ["John Doe/user/[Alpha, b-team]"]

    def test_child_turn_request_inherits_forwarded_headers(self, monkeypatch):
        """子ターンの __request__ は親の scope(headers / scheme / server …)を引き継ぐ —
        tool が X-Forwarded-Proto / X-Forwarded-Host / Host から自オリジンを組める。"""
        from starlette.requests import Request

        from nucllei.utils import middleware

        self._patch_child_pipeline(monkeypatch)
        seen = []

        async def capturing_payload(
            request, form_data, user, metadata, model, events_stream
        ):
            seen.append(request)
            return form_data, metadata, []

        monkeypatch.setattr(middleware, "process_chat_payload", capturing_payload)

        fake = self._request()
        parent = Request(
            scope={
                "type": "http",
                "app": fake.app,
                "method": "POST",
                "path": "/api/chat/completions",
                "raw_path": b"/api/chat/completions",
                "query_string": b"",
                "root_path": "",
                "scheme": "http",
                "http_version": "1.1",
                "server": ("127.0.0.1", 13301),
                "client": ("10.105.2.9", 51234),
                "headers": [
                    (b"host", b"10.105.2.1"),
                    (b"x-forwarded-proto", b"https"),
                    (b"cookie", b"token=abc"),
                ],
            }
        )
        tool = self._build_tool(request=parent)
        result = asyncio.run(
            tool["callable"](preset_id="researcher", instruction="1+1 は?")
        )
        assert result["ok"] is True

        assert len(seen) == 1
        child = seen[0]
        assert child is not parent  # 親 Request は使い回さない(state を汚さない)
        assert child.app is parent.app
        assert child.headers["x-forwarded-proto"] == "https"
        assert child.headers["host"] == "10.105.2.1"
        assert child.headers["cookie"] == "token=abc"
        assert child.scope["server"] == ("127.0.0.1", 13301)
        assert child.scope["client"] == ("10.105.2.9", 51234)
        # path は引き継がない("/" — 特定ルートの要求ではない)が url は組める
        assert str(child.url) == "http://10.105.2.1/"
        # 親の scope と headers を共有しない(子で足しても親に漏れない)
        child.scope["headers"].append((b"x-extra", b"1"))
        assert (b"x-extra", b"1") not in parent.scope["headers"]

    def test_child_turn_request_from_thin_parent_is_well_formed(self, monkeypatch):
        # 親が scope を持たない薄い fake でも request.url が例外にならない
        from nucllei.utils import middleware

        self._patch_child_pipeline(monkeypatch)
        seen = []

        async def capturing_payload(
            request, form_data, user, metadata, model, events_stream
        ):
            seen.append(request)
            return form_data, metadata, []

        monkeypatch.setattr(middleware, "process_chat_payload", capturing_payload)
        tool = self._build_tool()
        asyncio.run(tool["callable"](preset_id="researcher", instruction="x"))
        assert str(seen[0].url) == "/"
        assert list(seen[0].headers.items()) == []

    def test_spawn_inactive_preset_synthesizes_child_model(self, monkeypatch):
        """is_active=False の preset は MODELS に載らないが spawn できる(2026-08-14 仕様変更)。

        子モデルは Model 行 + base モデルから、get_all_models のアクティブ preset
        合成と同じ形で _resolve_child_model がその場合成する。base 自体が MODELS に
        無ければ従来どおり「preset が利用できません」。
        """
        from nucllei.models.models import ModelForm

        child_calls = self._patch_child_pipeline(monkeypatch)
        self.Models.insert_new_model(
            ModelForm(
                id="hidden-worker",
                base_model_id="kimi-base",
                name="Hidden Worker",
                meta={"description": "裏方"},
                params={},
                access_control=None,
                is_active=False,
            ),
            "1",
        )
        request = self._request()
        request.app.state.MODELS = {
            "kimi-base": {
                "id": "kimi-base",
                "name": "kimi-base",
                "owned_by": "openai",
                "openai": {"max_model_len": 262144},
            }
        }
        tool = self._build_tool(
            request=request, model=self._parent_model(("hidden-worker",))
        )
        assert tool["spec"]["parameters"]["properties"]["preset_id"]["enum"] == [
            "hidden-worker"
        ]

        result = asyncio.run(
            tool["callable"](preset_id="hidden-worker", instruction="裏で調査")
        )
        assert result["ok"] is True
        assert result["answer"] == "2"

        # 子ターンへは合成 model が渡る(アクティブ preset 合成と同じ形)
        assert len(child_calls) == 1
        assert child_calls[0]["model"] == "hidden-worker"
        synthesized = child_calls[0]["metadata"]["model"]
        assert synthesized["preset"] is True
        assert synthesized["owned_by"] == "openai"
        assert synthesized["info"]["is_active"] is False
        assert synthesized["info"]["base_model_id"] == "kimi-base"

        child = self.Chats.get_chat_by_id(result["session_id"])
        assert child.kind == "agent"
        assert child.chat["history"]["messages"] != {}

        # base が MODELS に無ければエラー — allowlist・権限の不一致とは別の文言で、
        # 土台のモデルが使えないことを言う(staging で原因を取り違えた件)
        request.app.state.MODELS = {}
        tool = self._build_tool(
            request=request, model=self._parent_model(("hidden-worker",))
        )
        failed = _raised(tool["callable"](preset_id="hidden-worker", instruction="x"))
        assert failed.kind == "failure"
        assert failed.text == (
            "ERROR: preset hidden-worker の土台のモデル kimi-base が今は使えない"
            " (接続先のモデル一覧に無い)。次の手: 自分で処理する。"
        )

    def test_session_id_continuation_appends_to_same_child(self, monkeypatch):
        child_calls = self._patch_child_pipeline(
            monkeypatch, ("text_basic", "text_basic")
        )
        tool = self._build_tool()

        async def run_two():
            first = await tool["callable"](
                preset_id="researcher", instruction="最初の指示"
            )
            second = await tool["callable"](
                instruction="続けて", session_id=first["session_id"]
            )
            return first, second

        first, second = asyncio.run(run_two())
        assert first["ok"] is True
        assert second["ok"] is True
        assert second["session_id"] == first["session_id"]

        # 同一子 chat に 2 ターン分(user + assistant × 2)が積まれる
        child = self.Chats.get_chat_by_id(first["session_id"])
        assert len(child.chat["history"]["messages"]) == 4

        # 2 回目の wire は前ターン込みで再構成される(reasoning は運ばない)
        assert [m["role"] for m in child_calls[1]["messages"]] == [
            "user",
            "assistant",
            "user",
        ]
        assert child_calls[1]["messages"][1]["content"] == "2"
        assert child_calls[1]["messages"][-1]["content"] == "続けて"

    def test_session_id_validation_rejects_foreign_sessions(self, monkeypatch):
        self._patch_child_pipeline(monkeypatch)
        from nucllei.models.chats import ChatForm
        from nucllei.utils import subagent

        tool = self._build_tool()

        # 別親から spawn された子 / 他人の子 — parent_chat_id・所有者の一致を検証
        other_parent_child = self.Chats.insert_new_chat(
            "1",
            ChatForm(chat={"title": "x"}),
            kind="agent",
            parent_chat_id="another-parent",
            parent_message_id="m",
            depth=1,
        )
        foreign_child = self.Chats.insert_new_chat(
            "9",
            ChatForm(chat={"title": "x"}),
            kind="agent",
            parent_chat_id=self.parent.id,
            parent_message_id="m",
            depth=1,
        )

        for session_id in (
            "no-such-id",  # 存在しない
            self.parent.id,  # kind=user(agent session ではない)
            other_parent_child.id,
            foreign_child.id,
        ):
            result = _raised(tool["callable"](instruction="x", session_id=session_id))
            assert result.kind == "call", session_id
            assert subagent._active_turns == 0  # エラー経路でもカウンタ解放

        # instruction 空 / 新規で preset_id 無しも call の ToolError(#145)
        assert _raised(tool["callable"](instruction="  ")).kind == "call"
        result = _raised(tool["callable"](instruction="x"))
        assert "preset_id" in result.text

    def test_continuation_rejected_after_cascade_delete(self, monkeypatch):
        # cascade 削除済みの子への追いメッセージは拒否(deleted_at IS NULL 検証)
        child_calls = self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        first = asyncio.run(
            tool["callable"](preset_id="researcher", instruction="最初")
        )
        assert first["ok"] is True
        assert self.Chats.delete_chat_by_id(first["session_id"])

        result = _raised(
            tool["callable"](instruction="続き", session_id=first["session_id"])
        )
        assert result.kind == "call"
        assert len(child_calls) == 1  # 2 回目の子ターンは走っていない

    def test_runtime_reauthorization_denies_revoked_preset(self, monkeypatch):
        self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        # allowlist 外の preset 名指し → エラー文字列(例外・打ち切りにしない)
        result = _raised(tool["callable"](preset_id="not-allowed", instruction="x"))
        assert result.kind == "call"

        # mount 後に preset が消えても、enum と同一述語の実行時再評価で拒否
        assert self.Models.delete_model_by_id("researcher")
        result = _raised(tool["callable"](preset_id="researcher", instruction="x"))
        assert result.kind == "call"
        # 子 chat は作られていない
        assert self.Chats.get_child_chats_by_parent_id(self.parent.id, "1") == []

    def test_runtime_depth_recheck(self, monkeypatch):
        from nucllei.env import SUBAGENT_MAX_DEPTH
        from nucllei.internal.db import get_db
        from nucllei.models.chats import Chat

        self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        # mount 後に親 depth が上限へ達した(ありえないが再チェックの固定)
        with get_db() as db:
            db.query(Chat).filter_by(id=self.parent.id).update(
                {"depth": SUBAGENT_MAX_DEPTH}
            )
            db.commit()

        result = _raised(tool["callable"](preset_id="researcher", instruction="x"))
        assert result.kind == "failure"
        assert "自分で処理する" in result.text

    def test_concurrency_limit_returns_error(self, monkeypatch):
        from nucllei.utils import subagent

        self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        monkeypatch.setattr(
            subagent, "_active_turns", subagent.SUBAGENT_MAX_CONCURRENCY
        )
        result = _raised(tool["callable"](preset_id="researcher", instruction="x"))
        assert result.kind == "failure"
        assert "同時" in result.text
        # 超過エラーはカウンタを消費しない(decrement で負にならない)
        assert subagent._active_turns == subagent.SUBAGENT_MAX_CONCURRENCY

    def test_long_answer_is_truncated(self, monkeypatch):
        from nucllei.utils import subagent

        self._patch_child_pipeline(monkeypatch)
        # 8000 字の切り詰め境界だけを見る(fixture の answer は "2" なので上限を下げる)
        monkeypatch.setattr(subagent, "ANSWER_MAX_CHARS", 0)
        tool = self._build_tool()

        result = asyncio.run(
            tool["callable"](preset_id="researcher", instruction="長文になる依頼")
        )
        assert result["ok"] is True
        assert result["answer"] == ""
        assert result["truncated"] is True
        # 黙って切らない: 事実と次の手を添える
        assert result["note"] == (
            "回答は先頭 0 字で切った。次の手: 続きが要るなら同じ session_id で頼む。"
        )

    # ---- stop 連鎖 ----

    def test_parent_cancel_stops_child_task(self, monkeypatch):
        """stop 連鎖(#107 決定事項 10): 親タスクの cancel が子タスクへ伝播する。"""
        from nucllei import tasks as task_registry
        from nucllei.utils import middleware, subagent

        async def stalled_child_completion(request, form_data, user):
            async def gen():
                yield (
                    'data: {"choices":[{"index":0,"delta":'
                    '{"role":"assistant","content":"少"},"finish_reason":null}]}\n'
                )
                await asyncio.sleep(60)

            return StreamingResponse(gen(), media_type="text/event-stream")

        async def fake_payload(
            request, form_data, user, metadata, model, events_stream
        ):
            return form_data, metadata, []

        monkeypatch.setattr(
            subagent, "generate_chat_completion", stalled_child_completion
        )
        monkeypatch.setattr(middleware, "process_chat_payload", fake_payload)
        monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda model: [])

        tool = self._build_tool()

        async def main():
            outer = asyncio.create_task(
                tool["callable"](preset_id="researcher", instruction="遅い処理")
            )

            # 子 chat の spawn とタスク登録を待つ
            child_id, child_task = None, None
            for _ in range(200):
                children = await asyncio.to_thread(
                    self.Chats.get_child_chats_by_parent_id, self.parent.id, "1"
                )
                if children:
                    child_id = children[0].id
                    task_ids = task_registry.list_task_ids_by_chat_id(child_id)
                    if task_ids:
                        child_task = task_registry.get_task(task_ids[-1])
                        break
                await asyncio.sleep(0.05)
            assert child_task is not None, "child task did not start"

            outer.cancel()
            try:
                await outer
                raise RuntimeError("cancel が伝播していない")
            except asyncio.CancelledError:
                pass

            # stop_task が子タスクの終了(cancel 処理の完走)まで面倒を見る
            assert child_task.done()
            assert task_registry.list_task_ids_by_chat_id(child_id) == []
            assert subagent._active_turns == 0

        asyncio.run(main())

    def test_child_task_external_stop_keeps_parent_alive(self, monkeypatch):
        """子 task 側発の cancel(所有者の直接 stop 等)を親 cancel と誤認しない。

        子 task が最初の一歩を踏む前に cancel を配達し(post_response_handler の
        大 try に入る前 — 中で受けた cancel は内部処理されて正常終了になる)、子 task を
        cancelled で終わらせる。callable は CancelledError を re-raise せず(親ターンを
        interrupted にせず)、stopped エラー + persisted 済み部分回答を tool result で返す。

        (旧版は子タスク先頭の get_message_by_id_and_message_id を堰き止めて cancel を
        配達していた。その読み出しは「続きを生成」の seed 用で、撤去とともに無くなった
        — refs #142。)
        """
        from nucllei.utils import middleware

        self._patch_child_pipeline(monkeypatch)

        orig_create_task = middleware.create_task
        stopped = {}

        def create_task_then_stop(coroutine, id=None):
            task_id, task = orig_create_task(coroutine, id=id)
            if not stopped:
                # 所有者による子 task の直接 stop(tasks.stop_task の cancel 配達)が
                # 子 task の最初の一歩より前に届く
                stopped["chat_id"] = id
                task.cancel()
            return task_id, task

        monkeypatch.setattr(middleware, "create_task", create_task_then_stop)

        tool = self._build_tool()

        async def main():
            result = await tool["callable"](
                preset_id="researcher", instruction="調査して"
            )
            return stopped["chat_id"], result

        child_id, result = asyncio.run(main())
        assert result["ok"] is False
        assert "止められた" in result["error"]
        assert result["session_id"] == child_id
        from nucllei.utils import subagent as _subagent

        assert _subagent._active_turns == 0
        assert _subagent._running_agent_sessions == set()

    def test_concurrent_continuation_to_running_session_is_rejected(self, monkeypatch):
        """並行継続の排他(#107 レビュー): 実行中 session への継続は待たずに即エラー。

        同一 session_id への並行 read-modify-write は後勝ち書き戻しが先行ターンの
        user/assistant 対を消して木を壊す(agent session は HTTP read-only なので
        修復不能)ため、入口で拒否されることを固定する。
        """
        from nucllei.utils import middleware, subagent

        calls = []
        gate_holder = {}

        async def gated_child_completion(request, form_data, user):
            calls.append(form_data)

            async def gen():
                yield (
                    'data: {"choices":[{"index":0,"delta":'
                    '{"role":"assistant","content":"2"},"finish_reason":null}]}\n'
                )
                if len(calls) >= 2:
                    # 2 本目(継続)だけ gate 解放まで実行中に保つ
                    await gate_holder["gate"].wait()
                yield "data: [DONE]\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        async def fake_payload(
            request, form_data, user, metadata, model, events_stream
        ):
            return form_data, metadata, []

        monkeypatch.setattr(
            subagent, "generate_chat_completion", gated_child_completion
        )
        monkeypatch.setattr(middleware, "process_chat_payload", fake_payload)
        monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda model: [])

        tool = self._build_tool()

        async def main():
            gate_holder["gate"] = asyncio.Event()
            first = await tool["callable"](preset_id="researcher", instruction="最初")
            assert first["ok"] is True
            session = first["session_id"]

            second = asyncio.create_task(
                tool["callable"](instruction="続き", session_id=session)
            )
            # 2 本目がターン実行中(モデル呼び出し到達)になるまで待つ
            for _ in range(200):
                if len(calls) >= 2:
                    break
                await asyncio.sleep(0.01)
            assert len(calls) == 2
            assert session in subagent._running_agent_sessions

            # 実行中 session への並行継続 → 即エラー(子ターンは走らない)
            racing = await call_error(
                tool["callable"](instruction="横入り", session_id=session)
            )
            # 呼び方の問題(待ってから同じ session_id で続ける)— llens 判定 2026-10-04
            assert racing.kind == "call"
            assert racing.text == (
                f"ERROR: このサブエージェントのセッションは実行中: {session}。"
                "次の手: 実行中の分の結果を待ってから (background で起動したなら"
                " await_task で受け取ってから) 同じ session_id で続ける。"
            )
            assert len(calls) == 2  # 3 本目の子ターンは走っていない

            gate_holder["gate"].set()
            result = await second
            assert result["ok"] is True
            # 完了で実行中登録が解除され、後続の継続は通る
            assert session not in subagent._running_agent_sessions
            assert subagent._active_turns == 0

        asyncio.run(main())

    # ---- 非同期形(#110): background 昇格は execution 基盤へ一本化 ----

    def test_run_subagent_is_backgroundable_without_own_async_surface(self):
        """非同期形は #110 の共有 background 引数(middleware が適格 tool の spec
        コピーへ注入)に一本化 — builtin はクラス属性 backgroundable で宣言し(#140)、
        #109 の専用 background 引数と await_subagent は存在しない。"""
        from nucllei.utils.exec_tools import entry_is_backgroundable

        tools = self._build_tools()
        assert set(tools.keys()) == {"run_subagent"}

        entry = tools["run_subagent"]
        assert entry["backgroundable"] is True
        assert entry_is_backgroundable("run_subagent", entry) is True
        # 自前の background 宣言は恒久不適格(D4 衝突ガード)に掛かるため無いこと
        assert "background" not in entry["spec"]["parameters"]["properties"]
        assert entry["spec"]["parameters"]["required"] == ["instruction"]

    def test_exec_tools_has_no_run_subagent_special_case(self):
        """#140: 適格性は宣言経路だけ — exec_tools に tool 名の特例が無い。"""
        import inspect

        from nucllei.utils import exec_tools

        source = inspect.getsource(exec_tools)
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert '"run_subagent"' not in code and "'run_subagent'" not in code

    def test_middleware_no_longer_injects_pseudo_tool(self):
        import inspect

        from nucllei.utils import middleware as mw

        source = inspect.getsource(mw.process_chat_payload)
        assert "build_subagent_tools" not in source

    def test_callable_without_user_row_or_chat_context_errors(self, monkeypatch):
        """callable は __user__ から実ユーザー行を引き直す / chat 文脈が無ければ
        起動しない(どちらも例外にせず tool result のエラー)。"""
        self._patch_child_pipeline(monkeypatch)
        # ユーザー行の無い __user__(tool 行は public read なので mount はされる)
        tool = self._build_tool(user=self._user(id="ghost"))
        metadata = self._metadata()
        tool_no_ctx = self._build_tool(metadata=metadata)
        metadata.pop("message_id")  # mount 後に文脈が欠けた(防御の固定)
        result = _raised(
            tool_no_ctx["callable"](preset_id="researcher", instruction="x")
        )
        assert result.kind == "failure"

        result = _raised(tool["callable"](preset_id="researcher", instruction="x"))
        assert result.kind == "failure"
        assert "ユーザー" in result.text
        assert self.Chats.get_child_chats_by_parent_id(self.parent.id, "1") == []
        assert self.Chats.get_child_chats_by_parent_id(self.parent.id, "ghost") == []

    def test_child_turn_suppresses_webhook(self, monkeypatch):
        """子ターンは購読ゼロで走るのが正常形 — webhook(「誰も見ていなかった」
        通知)を agent_session マーカで抑止する(#107 レビュー: 購読で防がない)。"""
        from nucllei.utils import middleware

        self._patch_child_pipeline(monkeypatch)
        monkeypatch.setattr(
            middleware.Users,
            "get_user_webhook_url_by_id",
            lambda user_id: "http://hook.local",
        )
        posted = []

        async def fake_post_webhook(*args, **kwargs):
            posted.append(args)

        monkeypatch.setattr(middleware, "post_webhook", fake_post_webhook)

        tool = self._build_tool()
        result = asyncio.run(
            tool["callable"](preset_id="researcher", instruction="調査して")
        )
        assert result["ok"] is True
        assert posted == []

    # ---- 実行中カードの live 追従(#140 UI 追従)----

    def test_sync_progress_carries_child_session_and_task_ids(self, monkeypatch):
        """dispatch 時点で `subagent.session_id` が、子ターン起動直後に
        `task_id` が progress に載る(FE は session_id で子 transcript を即マウントし、
        task_id で live 追従を始める)。どちらも完走より前に届く。"""
        from nucllei import tasks as task_registry
        from nucllei.utils import tool_output

        gate = self._patch_gated_child_pipeline(monkeypatch)
        tool = self._build_tool()
        emitted = []

        async def main():
            sink = tool_output.ToolOutputSink(
                tool_call_id="functions.run_subagent:0", emit_progress=emitted.append
            )
            token = tool_output.current_sink.set(sink)
            try:
                outer = asyncio.create_task(
                    tool["callable"](preset_id="researcher", instruction="1+1 は?")
                )
            finally:
                tool_output.current_sink.reset(token)

            for _ in range(200):
                if any((p.get("subagent") or {}).get("task_id") for p in emitted):
                    break
                await asyncio.sleep(0.02)
            # 子はまだ走行中(gate 未解放)— 所在は完走前に届いている
            assert not outer.done()
            with_session = [p for p in emitted if p.get("subagent")]
            first = with_session[0]["subagent"]
            assert set(first) == {"session_id"}
            task_id = with_session[-1]["subagent"]["task_id"]
            session_id = first["session_id"]
            assert task_id in task_registry.list_task_ids_by_chat_id(session_id)

            gate.set()
            result = await outer
            await self._wait_turn_slot_release()
            return session_id, result

        session_id, result = asyncio.run(main())
        assert result["ok"] is True
        assert result["session_id"] == session_id

    def test_background_progress_reaches_parent_stream_and_done_is_pushed(
        self, monkeypatch
    ):
        """background(#110 の exec)でも子の所在が spawn したターンの stream へ
        `tool:progress` として転送され、終端は `execution:done` で即時に届く
        (FE の 5 秒ポーリングを待たない)。"""
        from nucllei.utils import completion_events, executions

        executions._exec_tasks.clear()
        executions._active_execs_by_user.clear()
        executions._exec_sinks.clear()
        gate = self._patch_gated_child_pipeline(monkeypatch)
        tool = self._build_tool()

        async def main():
            stream = completion_events.CompletionEventStream(
                chat_id=self.parent.id, message_id="pm-a"
            )
            completion_events.register_stream("parent-turn", stream)
            queue = stream.subscribe()
            try:
                spawned = await executions.spawn_execution(
                    user_id="1",
                    chat_id=self.parent.id,
                    message_id="pm-a",
                    tool_call_id="functions.run_subagent:0",
                    tool_name="run_subagent",
                    entry=tool,
                    params={"preset_id": "researcher", "instruction": "1+1 は?"},
                )
                assert spawned["status"] == "running"
                exec_id = spawned["task_id"]

                events = []

                async def collect_until(predicate):
                    while True:
                        event = await asyncio.wait_for(queue.get(), timeout=10)
                        events.append(event)
                        if predicate(event):
                            return event

                progress_event = await collect_until(
                    lambda e: e["data"]["type"] == "tool:progress"
                    and (e["data"]["data"].get("subagent") or {}).get("task_id")
                )
                assert progress_event["message_id"] == "pm-a"
                payload = progress_event["data"]["data"]
                assert payload["tool_call_id"] == "functions.run_subagent:0"
                session_id = payload["subagent"]["session_id"]
                assert (
                    self.Chats.get_chat_by_id(session_id).parent_chat_id
                    == self.parent.id
                )

                gate.set()
                done_event = await collect_until(
                    lambda e: e["data"]["type"] == "execution:done"
                )
                assert done_event["message_id"] == "pm-a"
                done = done_event["data"]["data"]
                assert done["task_id"] == exec_id
                assert done["status"] == "done"
                assert done["tool_call_id"] == "functions.run_subagent:0"
                assert done["finished_at"] is not None
                await self._wait_turn_slot_release()
                return exec_id
            finally:
                stream.unsubscribe(queue)
                completion_events.release_stream("parent-turn")

        exec_id = asyncio.run(main())
        from nucllei.models.executions import Executions

        assert Executions.get_execution_by_id(exec_id).status == "done"

    def test_background_progress_is_dropped_without_open_parent_stream(
        self, monkeypatch
    ):
        """spawn したターンの stream が無ければ転送は黙って捨てる(check_task 用の
        最新値は従来どおり保持)。"""
        from nucllei.utils import executions

        executions._exec_tasks.clear()
        executions._active_execs_by_user.clear()
        executions._exec_sinks.clear()
        gate = self._patch_gated_child_pipeline(monkeypatch)
        tool = self._build_tool()

        async def main():
            spawned = await executions.spawn_execution(
                user_id="1",
                chat_id=self.parent.id,
                message_id="pm-a",
                tool_call_id="functions.run_subagent:0",
                tool_name="run_subagent",
                entry=tool,
                params={"preset_id": "researcher", "instruction": "1+1 は?"},
            )
            exec_id = spawned["task_id"]
            for _ in range(200):
                latest = executions.get_latest_progress(exec_id) or {}
                if (latest.get("subagent") or {}).get("task_id"):
                    break
                await asyncio.sleep(0.02)
            assert latest["subagent"]["session_id"]
            gate.set()
            task = executions.get_exec_task(exec_id)
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
            await self._wait_turn_slot_release()

        asyncio.run(main())

    # ---- 親ターン統合(実イベントバス + SSE EOF 完走待ち)----

    def test_parent_turn_carries_child_answer_into_tool_result(self, monkeypatch):
        from nucllei.utils import middleware
        from nucllei.utils.completion_events import CompletionEventStream

        child_calls = self._patch_child_pipeline(monkeypatch)

        parent_calls = []

        async def fake_parent_completion(request, form_data, user):
            parent_calls.append(form_data)
            return _sse_response("text_basic")

        monkeypatch.setattr(
            middleware, "generate_chat_completion", fake_parent_completion
        )

        # ターン末の carried_tokens 計測リクエストはこのテストの対象外
        # (test_native_tool_calling の measure 系が持つ)— 呼び出し数を数えるので外す
        async def no_measure(*args, **kwargs):
            return None

        monkeypatch.setattr(middleware, "measure_carried_tokens", no_measure)

        request = self._request()
        user = self._user()
        metadata = self._metadata()
        tool = self._build_tool(request=request, metadata=metadata)
        metadata["tools"] = {"run_subagent": tool}

        # run_subagent を呼ぶ tool_calls SSE(実キャプチャと同形のインライン組み立て)
        arguments = json.dumps(
            {"preset_id": "researcher", "instruction": "調査して"}, ensure_ascii=False
        )
        frames = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_content": None,
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "functions.run_subagent:0",
                                    "index": 0,
                                    "type": "function",
                                    "function": {
                                        "name": "run_subagent",
                                        "arguments": arguments,
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": None},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]

        async def gen():
            for frame in frames:
                yield "data: " + json.dumps(frame, ensure_ascii=False) + "\n"
            yield "data: [DONE]\n"

        initial = StreamingResponse(gen(), media_type="text/event-stream")

        form_data = {
            "model": "kimi",
            "stream": True,
            "messages": [{"role": "user", "content": "調査依頼"}],
            "tools": [{"type": "function", "function": tool["spec"]}],
        }
        model = {"id": "kimi", "name": "kimi"}
        events_stream = CompletionEventStream(chat_id=self.parent.id, message_id="pm-a")
        queue = events_stream.subscribe()

        async def drive():
            response = await middleware.process_chat_response(
                request,
                initial,
                form_data,
                user,
                metadata,
                model,
                [],
                {},
                events_stream=events_stream,
                queue=queue,
            )
            assert isinstance(response, StreamingResponse)
            # SSE 読み切り = 親タスク完走待ち(子ターンは tool 実行中に完走する)
            return await asyncio.wait_for(_read_sse_events(response), timeout=30)

        events = asyncio.run(drive())

        # 子 chat が spawn され、親の継続リクエストの tool result に answer が入る
        children = self.Chats.get_child_chats_by_parent_id(self.parent.id, "1")
        assert len(children) == 1
        assert len(child_calls) == 1
        assert len(parent_calls) == 1
        tool_messages = [
            m for m in parent_calls[0]["messages"] if m.get("role") == "tool"
        ]
        assert len(tool_messages) == 1
        assert tool_messages[0]["tool_call_id"] == "functions.run_subagent:0"
        payload = json.loads(tool_messages[0]["content"])
        assert payload["ok"] is True
        assert payload["answer"] == "2"
        assert payload["session_id"] == children[0].id

        # 親ターンは正常完走(done イベント)し、子 chat は kind=agent で残る
        assert any(
            e.get("type") == "chat:completion" and e.get("data", {}).get("done")
            for e in events
        )
        assert children[0].kind == "agent"
