"""run_subagent(agent session 実行系 — issue #107)の回帰テスト。

- mount 条件: preset allowlist ∩ アクセス可(所有者 or read ACL)/ 機能フラグ /
  depth 上限 / chat 文脈(chat_id・message_id)の有無
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
        from nucllei.models.chats import ChatForm, Chats
        from nucllei.models.models import ModelForm, Models
        from nucllei.utils import subagent

        self.Chats = Chats
        self.Models = Models
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

    def _user(self):
        return types.SimpleNamespace(
            id="1", email="john.doe@nucllei.local", name="John Doe", role="user"
        )

    def _request(self):
        # 実 Request の外形(app.state / request.state)だけを持つ薄い fake
        return types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(
                    WEBUI_NAME="llens",
                    config=types.SimpleNamespace(WEBUI_URL="http://localhost"),
                    MODELS={"researcher": {"id": "researcher", "name": "Researcher"}},
                )
            ),
            state=types.SimpleNamespace(),
        )

    def _parent_model(self, preset_ids=("researcher",), features=None):
        meta = {"subagentPresetIds": list(preset_ids)}
        if features is not None:
            meta["features"] = features
        return {"id": "kimi", "info": {"meta": meta}}

    def _metadata(self):
        return {"chat_id": self.parent.id, "message_id": "pm-a"}

    def _build_tool(self, request=None, model=None, metadata=None, user=None):
        from nucllei.utils.subagent import build_run_subagent_tool

        return build_run_subagent_tool(
            request if request is not None else self._request(),
            model if model is not None else self._parent_model(),
            user if user is not None else self._user(),
            metadata if metadata is not None else self._metadata(),
        )

    def _build_tools(self, request=None, model=None, metadata=None, user=None):
        from nucllei.utils.subagent import build_subagent_tools

        return build_subagent_tools(
            request if request is not None else self._request(),
            model if model is not None else self._parent_model(),
            user if user is not None else self._user(),
            metadata if metadata is not None else self._metadata(),
        )

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

    def test_mounts_with_accessible_preset(self):
        from nucllei.env import SUBAGENT_TURN_TIMEOUT

        tool = self._build_tool()
        assert tool is not None
        spec = tool["spec"]
        assert spec["name"] == "run_subagent"
        assert spec["parameters"]["properties"]["preset_id"]["enum"] == ["researcher"]
        assert spec["parameters"]["required"] == ["instruction"]
        # listing に preset の名前と説明が載る
        assert "Researcher" in spec["description"]
        assert "調査担当" in spec["description"]
        # per-tool timeout(execute_tool_calls が全体上限より優先)と表示ディスクリプタ
        assert tool["timeout"] == SUBAGENT_TURN_TIMEOUT
        assert tool["render"] == {"result": {"kind": "subagent"}}

    def test_not_mounted_when_feature_off_or_allowlist_empty(self):
        assert (
            self._build_tool(model=self._parent_model(features={"subagent": False}))
            is None
        )
        assert self._build_tool(model=self._parent_model(preset_ids=())) is None
        # キー不在は既定 ON(skills と対称)
        assert self._build_tool(model=self._parent_model(features={})) is not None

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

    def test_enum_excludes_inaccessible_and_inactive_presets(self):
        from nucllei.models.models import ModelForm

        # 他人の私有 preset(access_control={})と非 active preset は enum に出ない
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
        assert tool["spec"]["parameters"]["properties"]["preset_id"]["enum"] == [
            "researcher"
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
            result = json.loads(
                asyncio.run(tool["callable"](instruction="x", session_id=session_id))
            )
            assert result["ok"] is False, session_id
            assert subagent._active_turns == 0  # エラー経路でもカウンタ解放

        # instruction 空 / 新規で preset_id 無しも tool result エラー(例外にしない)
        assert (
            json.loads(asyncio.run(tool["callable"](instruction="  ")))["ok"] is False
        )
        result = json.loads(asyncio.run(tool["callable"](instruction="x")))
        assert "preset_id" in result["error"]

    def test_continuation_rejected_after_cascade_delete(self, monkeypatch):
        # cascade 削除済みの子への追いメッセージは拒否(deleted_at IS NULL 検証)
        child_calls = self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        first = asyncio.run(
            tool["callable"](preset_id="researcher", instruction="最初")
        )
        assert first["ok"] is True
        assert self.Chats.delete_chat_by_id(first["session_id"])

        result = json.loads(
            asyncio.run(
                tool["callable"](instruction="続き", session_id=first["session_id"])
            )
        )
        assert result["ok"] is False
        assert len(child_calls) == 1  # 2 回目の子ターンは走っていない

    def test_runtime_reauthorization_denies_revoked_preset(self, monkeypatch):
        self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        # allowlist 外の preset 名指し → エラー文字列(例外・打ち切りにしない)
        result = json.loads(
            asyncio.run(tool["callable"](preset_id="not-allowed", instruction="x"))
        )
        assert result["ok"] is False

        # mount 後に preset が消えても、enum と同一述語の実行時再評価で拒否
        assert self.Models.delete_model_by_id("researcher")
        result = json.loads(
            asyncio.run(tool["callable"](preset_id="researcher", instruction="x"))
        )
        assert result["ok"] is False
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

        result = json.loads(
            asyncio.run(tool["callable"](preset_id="researcher", instruction="x"))
        )
        assert result["ok"] is False
        assert "上限" in result["error"]

    def test_concurrency_limit_returns_error(self, monkeypatch):
        from nucllei.utils import subagent

        self._patch_child_pipeline(monkeypatch)
        tool = self._build_tool()

        monkeypatch.setattr(subagent, "_active_turns", subagent.SUBAGENT_MAX_CONCURRENT)
        result = json.loads(
            asyncio.run(tool["callable"](preset_id="researcher", instruction="x"))
        )
        assert result["ok"] is False
        assert "上限" in result["error"]
        # 超過エラーはカウンタを消費しない(decrement で負にならない)
        assert subagent._active_turns == subagent.SUBAGENT_MAX_CONCURRENT

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

        子タスクの入口(post_response_handler の大 try 前の await)を堰き止めて
        そこへ cancel を配達し、子 task を cancelled で終わらせる。callable は
        CancelledError を re-raise せず(親ターンを interrupted にせず)、
        stopped エラー + persisted 済み部分回答を tool result で返す。
        """
        import threading

        from nucllei import tasks as task_registry

        self._patch_child_pipeline(monkeypatch)

        orig_get_message = self.Chats.get_message_by_id_and_message_id
        entered = threading.Event()
        release = threading.Event()
        state = {"first": True}

        def gated_get_message(chat_id, message_id):
            # 子タスク先頭の await を堰き止める(2 回目以降 = callable の最終読みは素通し)
            if state["first"]:
                state["first"] = False
                entered.set()
                release.wait(timeout=30)
            return orig_get_message(chat_id, message_id)

        monkeypatch.setattr(
            self.Chats, "get_message_by_id_and_message_id", gated_get_message
        )

        tool = self._build_tool()

        async def main():
            outer = asyncio.create_task(
                tool["callable"](preset_id="researcher", instruction="調査して")
            )
            try:
                assert await asyncio.to_thread(entered.wait, 30)
                children = await asyncio.to_thread(
                    self.Chats.get_child_chats_by_parent_id, self.parent.id, "1"
                )
                assert children, "child chat not spawned"
                task_ids = task_registry.list_task_ids_by_chat_id(children[0].id)
                assert task_ids, "child task not registered"
                # 所有者による子 task の直接 stop。cancel は大 try 前の await に
                # 配達され、子 task は cancelled で終了する(内部処理されない)
                await task_registry.stop_task(task_ids[-1])
                # 親(callable)は CancelledError にならず正常フローで返る
                return children[0].id, await outer
            finally:
                release.set()

        child_id, result = asyncio.run(main())
        assert result["ok"] is False
        assert "停止" in result["error"]
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
            racing = json.loads(
                await tool["callable"](instruction="横入り", session_id=session)
            )
            assert racing["ok"] is False
            assert "実行中" in racing["error"]
            assert len(calls) == 2  # 3 本目の子ターンは走っていない

            gate_holder["gate"].set()
            result = await second
            assert result["ok"] is True
            # 完了で実行中登録が解除され、後続の継続は通る
            assert session not in subagent._running_agent_sessions
            assert subagent._active_turns == 0

        asyncio.run(main())

    # ---- 非同期形(#109): background spawn / await_subagent ----

    def test_await_subagent_mounted_alongside_run_subagent(self):
        """2 つの tool は常に同時合成・同じ None 条件(#109 決定事項 2)。"""
        from nucllei.env import SUBAGENT_TURN_TIMEOUT

        tools = self._build_tools()
        assert set(tools.keys()) == {"run_subagent", "await_subagent"}

        # run_subagent には background 引数が生える(必須にはならない)
        run_spec = tools["run_subagent"]["spec"]
        assert run_spec["parameters"]["properties"]["background"]["type"] == "boolean"
        assert run_spec["parameters"]["required"] == ["instruction"]

        await_spec = tools["await_subagent"]["spec"]
        assert await_spec["name"] == "await_subagent"
        assert await_spec["parameters"]["required"] == ["session_id"]
        assert tools["await_subagent"]["render"] == {"result": {"kind": "subagent"}}
        assert tools["await_subagent"]["timeout"] == SUBAGENT_TURN_TIMEOUT

        # None 条件は run_subagent と同一(同時に消える)
        assert (
            self._build_tools(model=self._parent_model(features={"subagent": False}))
            is None
        )
        assert self._build_tools(metadata={}) is None

    def test_background_spawn_returns_immediately_and_frees_slot_on_done(
        self, monkeypatch
    ):
        """spawn 即返し(#109 決定事項 1)+ 会計解放は子 task の done callback
        (#109 決定事項 4)。走行中は同期版と同じく継続も拒否される。"""
        from nucllei import tasks as task_registry
        from nucllei.utils import subagent

        gate = self._patch_gated_child_pipeline(monkeypatch)
        tools = self._build_tools()
        run = tools["run_subagent"]["callable"]

        async def main():
            result = await run(
                preset_id="researcher", instruction="調査して", background=True
            )
            # 即返しの形: answer は無く status: running
            session = result["session_id"]
            assert result == {"ok": True, "session_id": session, "status": "running"}

            # 子は走行中のまま — 会計(カウンタ・実行中集合)は保持されている
            task_ids = task_registry.list_task_ids_by_chat_id(session)
            assert task_ids
            child_task = task_registry.get_task(task_ids[-1])
            assert child_task is not None and not child_task.done()
            assert subagent._active_turns == 1
            assert session in subagent._running_agent_sessions

            # 実行中 session への継続は同期版と同じく即エラー
            racing = json.loads(await run(instruction="横入り", session_id=session))
            assert racing["ok"] is False
            assert "実行中" in racing["error"]

            # 完走 → done callback が会計を解放する
            gate.set()
            await child_task
            await self._wait_turn_slot_release()
            assert subagent._active_turns == 0
            assert session not in subagent._running_agent_sessions
            return session

        session = asyncio.run(main())
        # 子 chat は通常どおり永続化されている(kind=agent・部分応答 "2" が保存済み)
        child = self.Chats.get_chat_by_id(session)
        assert child.kind == "agent"
        assert child.parent_chat_id == self.parent.id

    def test_background_spawn_respects_concurrency_limit(self, monkeypatch):
        from nucllei.utils import subagent

        self._patch_gated_child_pipeline(monkeypatch)
        tools = self._build_tools()

        monkeypatch.setattr(subagent, "_active_turns", subagent.SUBAGENT_MAX_CONCURRENT)
        result = json.loads(
            asyncio.run(
                tools["run_subagent"]["callable"](
                    preset_id="researcher", instruction="x", background=True
                )
            )
        )
        assert result["ok"] is False
        assert "上限" in result["error"]
        assert subagent._active_turns == subagent.SUBAGENT_MAX_CONCURRENT

    def test_await_subagent_collects_background_result(self, monkeypatch):
        """走行中 session への await は完走まで待って answer を返す。"""
        from nucllei.utils import subagent

        gate = self._patch_gated_child_pipeline(monkeypatch)
        tools = self._build_tools()

        async def main():
            spawn = await tools["run_subagent"]["callable"](
                preset_id="researcher", instruction="調査して", background=True
            )
            assert spawn["status"] == "running"
            session = spawn["session_id"]

            waiter = asyncio.create_task(
                tools["await_subagent"]["callable"](session_id=session)
            )
            await asyncio.sleep(0.05)
            assert not waiter.done()  # 走行中は返らない

            gate.set()
            result = await asyncio.wait_for(waiter, timeout=30)
            assert result["ok"] is True
            assert result["session_id"] == session
            assert result["answer"] == "2"
            await self._wait_turn_slot_release()
            assert subagent._active_turns == 0
            assert subagent._running_agent_sessions == set()

        asyncio.run(main())

    def test_await_subagent_returns_immediately_when_done(self, monkeypatch):
        """完了済み session への await は待たずに最終結果を整形して返す。"""
        self._patch_child_pipeline(monkeypatch)
        tools = self._build_tools()

        async def main():
            first = await tools["run_subagent"]["callable"](
                preset_id="researcher", instruction="1+1 は?"
            )
            assert first["ok"] is True
            result = await tools["await_subagent"]["callable"](
                session_id=first["session_id"]
            )
            return first, result

        first, result = asyncio.run(main())
        assert result["ok"] is True
        assert result["session_id"] == first["session_id"]
        assert result["answer"] == "2"
        # usage も同期版の結果整形と同じ要点が載る(共用ロジック)
        assert result["usage"]["prompt_tokens"] == 22
        assert result["usage"]["completion_tokens"] == 66

    def test_await_cancel_does_not_stop_child(self, monkeypatch):
        """await 中の親 cancel は re-raise のみで子を止めない(#109 決定事項 3)。"""
        from nucllei import tasks as task_registry
        from nucllei.utils import subagent, tool_output

        gate = self._patch_gated_child_pipeline(monkeypatch)

        # await_subagent が shield 待ちへ入る直前の progress を観測点にする
        # (sleep 待ちにしない — cancel の配達先を shield 待ちに固定する)
        progressed = asyncio.Event()
        orig_progress = tool_output.progress

        def spy_progress(text):
            progressed.set()
            orig_progress(text)

        monkeypatch.setattr(tool_output, "progress", spy_progress)

        tools = self._build_tools()

        async def main():
            spawn = await tools["run_subagent"]["callable"](
                preset_id="researcher", instruction="調査して", background=True
            )
            session = spawn["session_id"]
            child_task = task_registry.get_task(
                task_registry.list_task_ids_by_chat_id(session)[-1]
            )
            progressed.clear()  # spawn 側の progress を捨てる

            waiter = asyncio.create_task(
                tools["await_subagent"]["callable"](session_id=session)
            )
            await asyncio.wait_for(progressed.wait(), timeout=30)
            waiter.cancel()
            try:
                await waiter
                raise RuntimeError("cancel が伝播していない")
            except asyncio.CancelledError:
                pass

            # 子は走り続ける(fire の意思の尊重)— 会計も保持されたまま
            assert not child_task.done()
            assert subagent._active_turns == 1
            assert session in subagent._running_agent_sessions

            # 後始末: 完走で done callback が解放する
            gate.set()
            await child_task
            await self._wait_turn_slot_release()
            assert subagent._active_turns == 0
            assert session not in subagent._running_agent_sessions

        asyncio.run(main())

    def test_await_subagent_validates_session(self, monkeypatch):
        """検証は継続経路と同一述語(存在∧非削除∧所有者∧kind=agent∧親一致)。"""
        from nucllei.models.chats import ChatForm

        self._patch_child_pipeline(monkeypatch)
        tools = self._build_tools()
        await_call = tools["await_subagent"]["callable"]

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
            "",  # 必須
            "no-such-id",  # 存在しない
            self.parent.id,  # kind=user(agent session ではない)
            other_parent_child.id,
            foreign_child.id,
        ):
            result = json.loads(asyncio.run(await_call(session_id=session_id)))
            assert result["ok"] is False, session_id

        # cascade 削除済みの子も拒否(deleted_at IS NULL 検証)
        first = asyncio.run(
            tools["run_subagent"]["callable"](
                preset_id="researcher", instruction="最初"
            )
        )
        assert self.Chats.delete_chat_by_id(first["session_id"])
        result = json.loads(asyncio.run(await_call(session_id=first["session_id"])))
        assert result["ok"] is False

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
