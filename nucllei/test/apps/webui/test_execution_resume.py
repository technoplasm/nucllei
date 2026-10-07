"""サーバ駆動配達 driver(#110 契約 T3 / M2〜M5)の回帰。

- append_delivery_turn(models 層): user+assistant 対 append・currentId 更新・
  線形 messages 同期・同一トランザクションでの delivered_at 確定・updated_at CAS
  (負けは "conflict" — 再試行可能)・chat 不在は "missing"
- notice 書式(契約 M4 — テストで固定): ヘッダ・status 別文言・~フェンス長・
  display 併記・files(file id 形 / "/" URL 形)
- 連鎖上限の遡行(user ロールのみ・assistant スキップ・人間発話で切れる・
  サイクルガード)
- 配達サイクル: 静穏ガード・CAS 追記・プロセス内クレーム・恒久断念(chat 不在 /
  agent chat)・turn 起動失敗(恒久 / transient)で notice 非重複・「履歴が
  進んだら中止」
- wire 忠実性(契約 M5): system テンプレート展開・params マージ順・stop 配列化・
  user 画像 files の image_url 展開・壊れ tool_calls の修復
  (convert_content_blocks_to_messages)
- wake / driver registry(1 chat 1 本)・wait_for_quiet_delivery・
  tasks.py の cleanup hook(wake ①④)

check_task / await_task / stop_task の delivered は test_async_exec.py、
エンティティ(delivered_at のモデル関数)は test_executions.py。
"""

import asyncio
import time
import types

from test.util.abstract_integration_test import AbstractPostgresTest


class TestExecutionResume(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import Chats
        from nucllei.models.executions import Executions
        from nucllei.models.users import Users
        from nucllei.utils import execution_resume, executions

        self.Chats = Chats
        self.Executions = Executions
        self.Users = Users
        self.resume = execution_resume
        self.executions = executions

        # プロセス内状態はテスト間で持ち越さない
        execution_resume._delivery_tasks.clear()
        execution_resume._delivery_events.clear()
        execution_resume._delivering.clear()
        execution_resume._claimed_exec_ids.clear()
        execution_resume._consumed_media.clear()
        execution_resume._semaphore = None
        execution_resume._semaphore_loop = None
        executions._exec_tasks.clear()
        executions._active_execs_by_user.clear()
        executions._exec_sinks.clear()

        # driver の内部ターン起動が使う app(モデルは静的な fake)
        self.models = {
            "kimi": {"id": "kimi", "name": "Kimi"},
        }
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(MODELS=self.models)
        )
        self._original_app = execution_resume._app
        execution_resume._app = self.app

        self.user = Users.insert_new_user(
            id="1", name="John Doe", email="john@nucllei.local", role="user"
        )

    def teardown_method(self):
        self.resume._app = self._original_app
        super().teardown_method()

    # ---- helpers ----

    def _insert_chat(self, user_id="1", *, params=None, models=None, **kwargs):
        from nucllei.models.chats import ChatForm

        chat_json = {
            "title": "parent",
            "models": models if models is not None else ["kimi"],
            "params": params or {},
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
                        "content": "了解",
                        "model": "kimi",
                    },
                },
            },
            "messages": [],
        }
        chat = self.Chats.insert_new_chat(user_id, ChatForm(chat=chat_json), **kwargs)
        assert chat is not None
        return chat

    def _insert_exec(
        self, exec_id, chat_id, *, status="done", result="ok", user_id="1", **finish
    ):
        row = self.Executions.insert_execution(
            id=exec_id,
            user_id=user_id,
            chat_id=chat_id,
            message_id="pm-a",
            tool_call_id="functions.probe:0",
            tool_name="probe",
            args={},
        )
        assert row is not None
        if status != "running":
            assert self.Executions.finish_execution(
                exec_id, status=status, result=result, **finish
            )
        return self.Executions.get_execution_by_id(exec_id)

    def _patch_turn_start(self, monkeypatch, side_effects=None):
        """subagent.start_internal_turn を記録用 fake に差し替える。

        side_effects: 呼び出しごとに消費する例外(None = 成功)の列。
        """
        from nucllei.utils import subagent

        calls = []
        effects = list(side_effects or [])

        async def fake_start(
            request, model, user, chat_id, message_id, messages, **kwargs
        ):
            calls.append(
                {
                    "request": request,
                    "model": model,
                    "user": user,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "messages": messages,
                    **kwargs,
                }
            )
            if effects:
                effect = effects.pop(0)
                if effect is not None:
                    raise effect
            return "task-1", None

        monkeypatch.setattr(subagent, "start_internal_turn", fake_start)
        return calls

    def _notice_messages(self, chat_id):
        chat = self.Chats.get_chat_by_id(chat_id)
        messages = (chat.chat.get("history") or {}).get("messages") or {}
        return [
            m for m in messages.values() if (m.get("meta") or {}).get("system_notice")
        ]

    # ---- append_delivery_turn(models 層 — 契約 M2)----

    def test_append_delivery_turn_appends_pair_and_marks_delivered_atomically(self):
        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)
        self._insert_exec("e2", chat.id, status="failed", result=None, error="x")

        status, updated = self.Chats.append_delivery_turn(
            chat.id,
            {"role": "user", "content": "notice", "meta": {"system_notice": "task"}},
            {"role": "assistant", "content": "", "model": "kimi"},
            ["e1", "e2"],
        )
        assert status == "ok"
        history = updated.chat["history"]
        assistant_id = history["currentId"]
        assistant = history["messages"][assistant_id]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == ""
        notice = history["messages"][assistant["parentId"]]
        assert notice["role"] == "user"
        assert notice["content"] == "notice"
        assert notice["meta"] == {"system_notice": "task"}
        # 旧 currentId(pm-a)に notice がぶら下がる(parent 鎖の継続)
        assert notice["parentId"] == "pm-a"
        assert assistant_id in history["messages"][notice["id"]]["childrenIds"]
        assert notice["id"] in history["messages"]["pm-a"]["childrenIds"]
        # 線形 messages も同期される
        assert [m["id"] for m in updated.chat["messages"]][-2:] == [
            notice["id"],
            assistant_id,
        ]
        # 同一トランザクションで delivered_at 確定
        assert self.Executions.get_execution_by_id("e1").delivered_at is not None
        assert self.Executions.get_execution_by_id("e2").delivered_at is not None

    def test_append_delivery_turn_cas_detects_concurrent_write(self, monkeypatch):
        """読値の updated_at と guarded UPDATE の間に他の書き手が入ったら
        "conflict"(何も書かない — 再読して呼び直せる)。"""
        from nucllei.internal.db import get_db
        from nucllei.models import chats as chats_module
        from nucllei.models.chats import Chat

        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)
        # updated_at を過去へ(同秒 CAS の盲点を避けて決定的にする)
        with get_db() as db:
            db.query(Chat).filter_by(id=chat.id).update({"updated_at": 1000})
            db.commit()

        original = chats_module.append_turn_messages

        def racing_append(chat_json, user_message, assistant_message):
            # read と guarded UPDATE の間に別の書き手が chat を保存した状況
            result = original(chat_json, user_message, assistant_message)
            self.Chats.update_chat_by_id(chat.id, dict(chat.chat))
            return result

        monkeypatch.setattr(chats_module, "append_turn_messages", racing_append)

        status, updated = self.Chats.append_delivery_turn(
            chat.id,
            {"role": "user", "content": "notice"},
            {"role": "assistant", "content": ""},
            ["e1"],
        )
        assert status == "conflict"
        assert updated is None
        # 何も書かない: delivered_at は立たず、履歴に notice も無い
        assert self.Executions.get_execution_by_id("e1").delivered_at is None
        fresh = self.Chats.get_chat_by_id(chat.id)
        assert len((fresh.chat["history"] or {}).get("messages")) == 2

    def test_append_delivery_turn_cas_survives_same_second_interleaving(
        self, monkeypatch
    ):
        """介在書き込みが読値と**同秒**でも CAS は偽成功しない(敵対検証 2)。

        updated_at は秒粒度の int なので、全書き込み点が厳密単調増加
        (max(now, 旧値+1))で前進させないと、同秒の介在コミットでスタンプが
        変わらず guarded UPDATE が通ってしまう(ABA — 介在分の上書き消失)。
        既存テストのように updated_at を過去へ巻き戻さず、同秒のまま縛る。
        """
        from nucllei.models import chats as chats_module

        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)

        original = chats_module.append_turn_messages

        def racing_append(chat_json, user_message, assistant_message):
            result = original(chat_json, user_message, assistant_message)
            # 読値と同秒(直後)の介在書き込み
            self.Chats.update_chat_by_id(chat.id, dict(chat.chat))
            return result

        monkeypatch.setattr(chats_module, "append_turn_messages", racing_append)

        status, updated = self.Chats.append_delivery_turn(
            chat.id,
            {"role": "user", "content": "notice"},
            {"role": "assistant", "content": ""},
            ["e1"],
        )
        assert status == "conflict"
        assert updated is None
        assert self.Executions.get_execution_by_id("e1").delivered_at is None

    def test_append_delivery_turn_missing_chat(self):
        status, updated = self.Chats.append_delivery_turn(
            "no-such-chat", {"role": "user", "content": "x"}, {"role": "assistant"}, []
        )
        assert status == "missing"
        assert updated is None

        chat = self._insert_chat()
        assert self.Chats.delete_chat_by_id(chat.id)
        status, _ = self.Chats.append_delivery_turn(
            chat.id, {"role": "user", "content": "x"}, {"role": "assistant"}, []
        )
        assert status == "missing"

    # ---- notice 書式(契約 M4 — テストで固定)----

    def test_notice_format_done_with_duration_and_fence(self):
        chat = self._insert_chat()
        row = self._insert_exec("e1", chat.id, result="結果本文", duration=20.05)

        text, files = self.resume.build_delivery_notice([row], {})
        assert text == (
            "[task] バックグラウンドタスクの完了通知 (システムからの自動通知。"
            "ユーザーの発言ではない)。結果は下にあり、成果物の画像・ファイルはまだ画面に"
            "表示されていない。次の手: await_task で取り直さない。成果物を見せるなら本文に"
            "リンクを書く。この通知の書式 (task_id: / status: / note: の行) は回答に写さない。\n"
            "\n"
            "task_id: e1 (probe)\n"
            "status: done(duration: 20.05s)\n"
            "result:\n"
            "~~~~\n"
            "結果本文\n"
            "~~~~"
        )
        assert files == []

    def test_notice_fence_grows_past_body_tildes(self):
        chat = self._insert_chat()
        row = self._insert_exec("e1", chat.id, result="a\n~~~~~\nb", duration=1.0)
        text, _ = self.resume.build_delivery_notice([row], {})
        # 本文中の最長 ~ 連(5)より 1 長い 6 連のフェンス
        assert "\n~~~~~~\na\n~~~~~\nb\n~~~~~~" in text
        assert "\n~~~~\n" not in text

    def test_notice_format_terminal_variants(self):
        chat = self._insert_chat()
        failed = self._insert_exec(
            "f1", chat.id, status="failed", result=None, error="boom した"
        )
        stopped = self._insert_exec("s1", chat.id, status="stopped", result=None)
        orphaned = self._insert_exec("o1", chat.id, status="orphaned", result=None)

        text, _ = self.resume.build_delivery_notice([failed, stopped, orphaned], {})
        assert "task_id: f1 (probe)\nstatus: failed\nerror: boom した" in text
        assert (
            "task_id: s1 (probe)\n"
            "status: stopped\n"
            "note: 止められた (ユーザー操作または stop_task)。"
            "副作用は完了している可能性がある。次の手: ユーザーが止めたなら呼び直さない。"
            in text
        )
        assert (
            "task_id: o1 (probe)\n"
            "status: orphaned\n"
            "note: サーバーの再起動で結果が失われた。次の手: 読むだけの"
            "処理なら同じ引数で呼び直す。書き込みや送信を伴う処理は、結果が残っていないか"
            "確かめてから呼び直すかを決める。" in text
        )

    def test_notice_has_no_heading_or_imperative(self):
        """notice は user ロールで履歴に載り、モデルが回答へ写しうる(2026-10-04 監査):
        markdown 見出しと命令形(〜せよ)を持たない。"""
        chat = self._insert_chat()
        rows = [
            self._insert_exec("e1", chat.id, result="ok", duration=1.0),
            self._insert_exec("f1", chat.id, status="failed", result=None, error="x"),
            self._insert_exec("s1", chat.id, status="stopped", result=None),
            self._insert_exec("o1", chat.id, status="orphaned", result=None),
        ]
        text, _ = self.resume.build_delivery_notice(rows, {})
        assert not any(line.lstrip().startswith("#") for line in text.split("\n"))
        assert "せよ" not in text

    def test_notice_display_and_files(self):
        chat = self._insert_chat()
        row = self._insert_exec("e1", chat.id, result="ok", duration=1.0)
        media = (
            # 載せるのはブラウザが画像として開ける物だけ(振り分けは
            # image_refs.stored_ref_kind): file id / 絶対 URL / 同一サーバの "/ext/…"
            # (cage の図はこの形)。sandbox のパス("/workspace/…")と PDF の
            # ページ参照("#page=N")、どの形でもない物は落とす
            [
                "file-123",
                "https://llens.test/ext/tool/img.png",
                "/ext/cage/static/図.png",
                "/workspace/cage/x.pdf#page=2",
                "/workspace/a.png",
                "/api/v1/files/file-123/content#page=2",
                "/ext/cage/static/doc.pdf#page=3",
                "https://llens.test/ext/cage/static/doc.pdf#page=3",
                "/etc/passwd",
                "",
                None,
            ],
            [{"content": "## グラフ", "kind": "markdown"}],
        )
        text, files = self.resume.build_delivery_notice([row], {"e1": media})
        assert "display:\n~~~~\n## グラフ\n~~~~" in text
        assert files == [
            {
                "type": "image",
                "id": "file-123",
                "url": "/api/v1/files/file-123/content",
                "name": "task-image",
                "status": "uploaded",
            },
            {"type": "image", "url": "https://llens.test/ext/tool/img.png"},
            {"type": "image", "url": "/ext/cage/static/図.png"},
        ]

    # ---- 連鎖上限の遡行(契約 M3 手順 3)----

    def _chain_chat_json(self, roles):
        """roles(古→新)の鎖から chat_json を作る。"n" = notice user、
        "u" = 人間 user、"a" = assistant。"""
        messages = {}
        parent = None
        for i, role in enumerate(roles):
            mid = f"m{i}"
            message = {"id": mid, "parentId": parent, "childrenIds": []}
            if role == "a":
                message["role"] = "assistant"
            else:
                message["role"] = "user"
                if role == "n":
                    message["meta"] = {"system_notice": "task"}
            messages[mid] = message
            if parent:
                messages[parent]["childrenIds"].append(mid)
            parent = mid
        return {"history": {"messages": messages, "currentId": parent}}

    def test_count_notice_chain(self):
        count = self.resume.count_notice_chain
        # assistant はスキップ・notice user のみ数える
        assert count(self._chain_chat_json(["u", "a", "n", "a", "n", "a"])) == 2
        # 人間の発話で連鎖が切れる
        assert count(self._chain_chat_json(["n", "a", "u", "a", "n", "a"])) == 1
        assert count(self._chain_chat_json(["u", "a"])) == 0
        assert count({}) == 0

    def test_count_notice_chain_survives_cycles(self):
        chat_json = self._chain_chat_json(["n", "a"])
        # parent リンクを循環させる(DB 破損)— 無限ループしない
        chat_json["history"]["messages"]["m0"]["parentId"] = "m1"
        assert self.resume.count_notice_chain(chat_json) == 1

    # ---- 配達サイクル(契約 M3 手順 1〜7)----

    def test_cycle_delivers_notice_and_starts_resume_turn(self, monkeypatch):
        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat(
            params={"system": "you are {{USER_NAME}}", "temperature": 0.5}
        )
        self._insert_exec("e1", chat.id, result="結果本文", duration=1.5)

        outcome = asyncio.run(self.resume._deliver_cycle(chat.id))
        assert outcome == "delivered"

        # notice + 空 assistant が履歴に載り delivered_at が立つ
        notices = self._notice_messages(chat.id)
        assert len(notices) == 1
        assert notices[0]["content"].startswith(self.resume.NOTICE_HEADER)
        assert "task_id: e1 (probe)" in notices[0]["content"]
        assert self.Executions.get_execution_by_id("e1").delivered_at is not None

        # 続きターンが正しい形で起動される
        assert len(calls) == 1
        call = calls[0]
        assert call["chat_id"] == chat.id
        assert call["model"] == self.models["kimi"]
        assert call["metadata_extra"] == {
            "task_resume": True,
            "task_resume_chain": 1,
        }
        # 空 assistant が応答先
        chat_now = self.Chats.get_chat_by_id(chat.id)
        assert call["message_id"] == chat_now.chat["history"]["currentId"]
        # wire: system テンプレート展開 + notice が user として載る
        wire = call["messages"]
        assert wire[0] == {"role": "system", "content": "you are John Doe"}
        assert wire[-1]["role"] == "user"
        assert self.resume.NOTICE_HEADER.split("\n")[0] in wire[-1]["content"]
        # params は form_extra 経由(マージ・詳細は wire 忠実性のテストで固定)
        assert call["form_extra"]["params"]["temperature"] == 0.5

        # 2 周目は未配達なし
        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "empty"

    def test_cycle_is_quiet_guarded(self, monkeypatch):
        from nucllei import tasks as tasks_module

        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)

        monkeypatch.setitem(tasks_module.chat_tasks, chat.id, ["t-running"])
        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "busy"
        # 何も書かない
        assert self._notice_messages(chat.id) == []
        assert self.Executions.get_execution_by_id("e1").delivered_at is None
        assert calls == []

    def test_cycle_suspends_at_chain_limit(self, monkeypatch):
        calls = self._patch_turn_start(monkeypatch)
        monkeypatch.setattr(self.resume, "ASYNC_EXEC_RESUME_CHAIN_LIMIT", 1)
        chat = self._insert_chat()
        # 直近が notice user(連鎖 1)の履歴にする
        status, _ = self.Chats.append_delivery_turn(
            chat.id,
            {"role": "user", "content": "n", "meta": {"system_notice": "task"}},
            {"role": "assistant", "content": ""},
            [],
        )
        assert status == "ok"
        self._insert_exec("e1", chat.id)

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "chain_limited"
        assert self.Executions.get_execution_by_id("e1").delivered_at is None
        assert calls == []

    def test_cycle_abandons_when_chat_is_gone_or_agent(self, monkeypatch):
        calls = self._patch_turn_start(monkeypatch)
        # chat 不在
        self._insert_exec("e1", "no-such-chat")
        assert asyncio.run(self.resume._deliver_cycle("no-such-chat")) == "abandoned"
        assert self.Executions.get_execution_by_id("e1").delivered_at is not None

        # agent chat(D9 — 配達先にならない)
        parent = self._insert_chat()
        agent = self._insert_chat(
            kind="agent", parent_chat_id=parent.id, parent_message_id="pm-a", depth=1
        )
        self._insert_exec("e2", agent.id)
        assert asyncio.run(self.resume._deliver_cycle(agent.id)) == "abandoned"
        assert self.Executions.get_execution_by_id("e2").delivered_at is not None
        assert calls == []

    def test_cycle_respects_in_process_claims(self):
        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)
        self.resume._claimed_exec_ids.add("e1")
        try:
            assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "empty"
        finally:
            self.resume._claimed_exec_ids.discard("e1")
        assert self.Executions.get_execution_by_id("e1").delivered_at is None

    def test_resume_turn_request_has_no_origin(self, monkeypatch):
        """続きターンの __request__ は自オリジンを持たない(Host / X-Forwarded-* 無し)。
        exec 行に旧 meta["origin"] が残っていても読まない(2026-10-03 撤去・移行なし)。"""
        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat()
        self._insert_exec(
            "e1",
            chat.id,
            meta={"origin": {"scheme": "https", "host": "10.105.2.1"}},
        )

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "delivered"
        request = calls[0]["request"]
        assert list(request.headers.items()) == []
        assert str(request.url) == "/"  # 例外にならない(host 無し → path のみ)
        assert request.app is self.app

    def test_permanent_turn_failure_keeps_single_notice(self, monkeypatch):
        """モデル id 解決不能 = 恒久断念。notice は 1 通だけ残り再 append しない。"""
        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat(models=["gone-model"])
        # 履歴の assistant の model も解決不能にする
        chat_json = dict(chat.chat)
        chat_json["history"]["messages"]["pm-a"]["model"] = "gone-model"
        self.Chats.update_chat_by_id(chat.id, chat_json)
        self._insert_exec("e1", chat.id)

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "delivered"
        assert len(self._notice_messages(chat.id)) == 1
        assert self.Executions.get_execution_by_id("e1").delivered_at is not None
        assert calls == []  # ターンは起動されない

        # 再サイクルでも notice は増えない(delivered 済み → empty)
        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "empty"
        assert len(self._notice_messages(chat.id)) == 1

    def test_transient_turn_failure_retries_turn_without_reappending(self, monkeypatch):
        calls = self._patch_turn_start(
            monkeypatch, side_effects=[RuntimeError("SGLang down"), None]
        )
        monkeypatch.setattr(self.resume, "TURN_RETRY_INITIAL_BACKOFF_S", 0.01)
        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "delivered"
        # 1 回目は失敗 → backoff 後にターンのみ再試行(notice は 1 通のまま)
        assert len(calls) == 2
        assert len(self._notice_messages(chat.id)) == 1

    def test_turn_retry_aborts_when_history_advances(self, monkeypatch):
        """再試行前に履歴が自分の空 assistant より進んでいたら中止
        (誰かのターンが notice を消化済み)。"""
        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)

        def advance_history():
            status, _ = self.Chats.append_delivery_turn(
                chat.id,
                {"role": "user", "content": "人間の発話"},
                {"role": "assistant", "content": ""},
                [],
            )
            assert status == "ok"

        from nucllei.utils import subagent

        attempts = []

        async def failing_start(*args, **kwargs):
            attempts.append(1)
            advance_history()  # 失敗と同時に誰かが履歴を進めた状況
            raise RuntimeError("SGLang down")

        monkeypatch.setattr(subagent, "start_internal_turn", failing_start)
        monkeypatch.setattr(self.resume, "TURN_RETRY_INITIAL_BACKOFF_S", 0.01)

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "delivered"
        assert len(attempts) == 1  # 再試行しない
        assert len(self._notice_messages(chat.id)) == 1

    def test_cycle_consumes_media_once(self, monkeypatch):
        """context_images / display は mark_replayed の勝者だけが notice に載せる
        (負け = await_task が注入済み → 結果のみ)。"""
        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat()
        self._insert_exec(
            "e1",
            chat.id,
            result="ok",
            meta={"context_images": ["file-1"], "display": [{"content": "d"}]},
        )
        # e2 は既に await_task 側で replay 済み
        self._insert_exec(
            "e2",
            chat.id,
            result="ok",
            meta={"context_images": ["file-2"]},
        )
        assert self.Executions.mark_replayed("e2")

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "delivered"
        notices = self._notice_messages(chat.id)
        assert len(notices) == 1
        files = notices[0].get("files") or []
        assert [f.get("id") for f in files] == ["file-1"]  # e2 の media は載らない
        assert "display:" in notices[0]["content"]
        # e1 の replayed_at も立つ(以後の await_task 再回収は結果のみ)
        assert (self.Executions.get_execution_by_id("e1").meta or {}).get(
            "replayed_at"
        ) is not None
        assert len(calls) == 1

    # ---- wire 忠実性(契約 M5)----

    def test_merged_params_and_stop_sequences(self):
        user = types.SimpleNamespace(
            name="John",
            settings=types.SimpleNamespace(
                ui={"params": {"temperature": 0.9, "top_p": 0.4, "stop": "a,b"}}
            ),
        )
        chat_json = {"params": {"temperature": 0.5, "system": "s"}}
        params = self.resume._merged_params(chat_json, user)
        # マージ順は {...settings.params, ...chat.params}
        assert params["temperature"] == 0.5
        assert params["top_p"] == 0.4
        # stop はカンマ区切り文字列 → 配列化
        assert params["stop"] == ["a", "b"]

        # chat 側の stop("\\n" のエスケープ表記)が優先され実文字へ戻る
        chat_json = {"params": {"stop": "\\n,END"}}
        assert self.resume._merged_params(chat_json, user)["stop"] == ["\n", "END"]

        # どちらにも無ければ stop キー自体を持たない
        assert "stop" not in self.resume._merged_params(
            {"params": {}}, types.SimpleNamespace(name="J", settings=None)
        )

    def test_build_wire_messages_expands_system_variables(self):
        chat_json = {
            "params": {"system": "{{USER_NAME}} / {{BASE_MODEL_ID}}"},
            "history": {"currentId": None, "messages": {}},
        }
        user = types.SimpleNamespace(name="J", settings=None)
        model = {
            "id": "llens",
            "name": "LLENS",
            "info": {"base_model_id": "qwen3.8-flash-next"},
        }
        wire = self.resume.build_wire_messages(chat_json, user, None, model)
        assert wire == [{"role": "system", "content": "J / qwen3.8-flash-next"}]
        # モデルを渡さない呼び出しでも変数は残らない
        wire = self.resume.build_wire_messages(chat_json, user, None)
        assert wire == [{"role": "system", "content": "J / Unknown"}]

    def test_build_wire_messages_expands_user_role_and_groups(self):
        from nucllei.models.groups import GroupForm, GroupUpdateForm, Groups

        group = Groups.insert_new_group("1", GroupForm(name="病理", description=""))
        Groups.update_group_by_id(
            group.id, GroupUpdateForm(name="病理", description="", user_ids=["7"])
        )
        chat_json = {
            "params": {"system": "{{USER_ROLE}} [{{USER_GROUPS}}]"},
            "history": {"currentId": None, "messages": {}},
        }
        user = types.SimpleNamespace(id="7", name="J", role="user", settings=None)
        wire = self.resume.build_wire_messages(chat_json, user, None)
        assert wire == [{"role": "system", "content": "user [病理]"}]

    def test_build_wire_messages_expands_user_image_files(self):
        chat_json = {
            "history": {
                "currentId": "a1",
                "messages": {
                    "u1": {
                        "id": "u1",
                        "parentId": None,
                        "role": "user",
                        "content": "見て",
                        "files": [
                            {"type": "image", "id": "f-1"},
                            {"type": "image", "url": "/ext/x/y.png"},
                            {"type": "file", "id": "doc-1"},
                        ],
                    },
                    "a1": {
                        "id": "a1",
                        "parentId": "u1",
                        "role": "assistant",
                        "content": "",
                    },
                },
            }
        }
        user = types.SimpleNamespace(name="J", settings=None)
        wire = self.resume.build_wire_messages(chat_json, user, "a1")
        assert wire == [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "見て"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "/api/v1/files/f-1/content"},
                    },
                    {"type": "image_url", "image_url": {"url": "/ext/x/y.png"}},
                ],
            }
            # 空 assistant(応答先)は落ちる
        ]

    def test_convert_content_blocks_repairs_broken_tool_calls(self):
        """壊れ arguments の call 除去・result 欠落 call への [interrupted] 合成
        (FE expandAssistantToolHistory と同等 — 契約 M5)。"""
        from nucllei.utils.content_blocks import (
            INTERRUPTED_TOOL_RESULT,
            convert_content_blocks_to_messages,
        )

        blocks = [
            {"type": "text", "content": "前置き"},
            {
                "type": "tool_calls",
                "content": [
                    {
                        "id": "call-ok",
                        "function": {"name": "probe", "arguments": '{"x": 1}'},
                    },
                    {
                        "id": "call-broken",
                        "function": {"name": "probe", "arguments": '{"x": '},
                    },
                    {
                        "id": "call-interrupted",
                        "function": {"name": "probe", "arguments": ""},
                    },
                ],
                "results": [{"tool_call_id": "call-ok", "content": "ok"}],
            },
        ]
        messages = convert_content_blocks_to_messages(blocks)
        assert messages[0]["role"] == "assistant"
        # 壊れ JSON の call は履歴から落ちる(実行されていない)
        assert [c["id"] for c in messages[0]["tool_calls"]] == [
            "call-ok",
            "call-interrupted",
        ]
        assert messages[1] == {
            "role": "tool",
            "tool_call_id": "call-ok",
            "content": "ok",
        }
        # result 未着の call には合成 result を対にする
        assert messages[2] == {
            "role": "tool",
            "tool_call_id": "call-interrupted",
            "content": INTERRUPTED_TOOL_RESULT,
        }

    def test_convert_content_blocks_skips_fully_broken_block(self):
        from nucllei.utils.content_blocks import convert_content_blocks_to_messages

        blocks = [
            {"type": "text", "content": "前"},
            {
                "type": "tool_calls",
                "content": [
                    {"id": "c1", "function": {"name": "probe", "arguments": "{bad"}}
                ],
                "results": [],
            },
            {"type": "text", "content": "後"},
        ]
        # 全滅ブロックはスキップされ text は前後で合流する
        assert convert_content_blocks_to_messages(blocks) == [
            {"role": "assistant", "content": "前\n後"}
        ]

    def test_convert_content_blocks_reads_persisted_calls_key(self):
        """永続化形('calls' キー — snapshot_content_blocks の出力)でも tool
        往復が wire に載り、M5 の壊れ tool_calls 修復が効く(敵対検証 4)。

        DB の message.content_blocks は snapshot 形(call 列のキーが 'calls')。
        内部形('content' キー)しか読めないと、続きターン・subagent 継続の
        wire から tool 往復が丸ごと落ちる。fixture は snapshot 関数の実出力から
        作り、永続化形とのずれを構造ごと縛る。
        """
        from nucllei.utils.content_blocks import (
            INTERRUPTED_TOOL_RESULT,
            convert_content_blocks_to_messages,
            snapshot_content_blocks,
        )

        internal = [
            {"type": "text", "content": "前置き"},
            {
                "type": "tool_calls",
                "content": [
                    {
                        "id": "call-ok",
                        "function": {"name": "probe", "arguments": '{"x": 1}'},
                    },
                    {
                        "id": "call-broken",
                        "function": {"name": "probe", "arguments": '{"x": '},
                    },
                    {
                        "id": "call-interrupted",
                        "function": {"name": "probe", "arguments": ""},
                    },
                ],
                "results": [{"tool_call_id": "call-ok", "content": "ok"}],
            },
        ]
        persisted = snapshot_content_blocks(internal)
        # 永続化形は 'calls' キー(内部形の 'content' は持たない)
        assert "calls" in persisted[1]
        assert "content" not in persisted[1]

        messages = convert_content_blocks_to_messages(persisted)
        assert messages[0]["role"] == "assistant"
        # 壊れ JSON の call 除去(修復)が永続化形でも効く
        assert [c["id"] for c in messages[0]["tool_calls"]] == [
            "call-ok",
            "call-interrupted",
        ]
        assert messages[1] == {
            "role": "tool",
            "tool_call_id": "call-ok",
            "content": "ok",
        }
        # result 未着 call への合成 result(修復)も永続化形で効く
        assert messages[2] == {
            "role": "tool",
            "tool_call_id": "call-interrupted",
            "content": INTERRUPTED_TOOL_RESULT,
        }

    # ---- cascade 停止 × wake のレース(敵対検証 1)----

    def test_cascade_stop_is_delivered_before_wake_so_driver_stays_silent(
        self, monkeypatch
    ):
        """chat 削除 cascade の停止は stopped への CAS と**同一 UPDATE** で
        delivered_at を立てる — task cancel の cleanup wake が届いた時点で
        未配達 0 になっており、配達 driver は消えゆく chat へ notice を
        書かない(順序込みで縛る: wake 時点の delivered_at を観測する)。"""
        from nucllei import tasks as tasks_module

        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat()
        self._insert_exec("e1", chat.id, status="running")

        delivered_at_wake = []

        def observer(namespace_id):
            if namespace_id == f"exec:{chat.id}":
                delivered_at_wake.append(
                    self.Executions.get_execution_by_id("e1").delivered_at
                )

        # install() と同じ hook + 観測 listener(wake と同じタイミングで走る)
        tasks_module.register_cleanup_listener(self.resume._on_task_cleanup)
        tasks_module.register_cleanup_listener(observer)
        try:

            async def main():
                async def hang():
                    await asyncio.Event().wait()

                task_id, _task = tasks_module.create_task(hang(), id=f"exec:{chat.id}")
                self.executions._exec_tasks["e1"] = task_id

                stopped = await self.executions.stop_executions_by_chat_id(chat.id)
                assert stopped == 1
                # cancel の cleanup wake で立った driver が回りきるのを待つ
                for _ in range(500):
                    if chat.id not in self.resume._delivery_tasks:
                        break
                    await asyncio.sleep(0.01)
                assert chat.id not in self.resume._delivery_tasks

            asyncio.run(main())
        finally:
            tasks_module._cleanup_listeners.remove(self.resume._on_task_cleanup)
            tasks_module._cleanup_listeners.remove(observer)
            self.executions._exec_tasks.pop("e1", None)

        # 順序: wake(cleanup hook)の時点で既に配達済み — レース窓が存在しない
        assert delivered_at_wake and delivered_at_wake[0] is not None
        row = self.Executions.get_execution_by_id("e1")
        assert row.status == "stopped"
        assert row.delivered_at is not None
        # driver は配達しない: notice もターンも生まれない
        assert self._notice_messages(chat.id) == []
        assert calls == []

    # ---- _consumed_media の掃除(敵対検証 6/7)----

    def test_consumed_media_swept_when_row_delivered_elsewhere(self, monkeypatch):
        """CAS 負けで media を控えた後、await_task / stop_task が先に配達した行
        ("empty" 終端)の控えがリークせず回収される(敵対検証 7)。"""
        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat()
        self._insert_exec(
            "e1",
            chat.id,
            result="ok",
            meta={"context_images": ["file-1"], "display": [{"content": "d"}]},
        )

        def conflicting_append(*args, **kwargs):
            # CAS 負けの間に await_task 側が配達を確定した状況
            self.Executions.mark_delivered(["e1"])
            return ("conflict", None)

        monkeypatch.setattr(self.Chats, "append_delivery_turn", conflicting_append)

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "empty"
        # media は mark_replayed で消費済みだが、控えは残留しない
        assert self.resume._consumed_media == {}
        assert self._notice_messages(chat.id) == []
        assert calls == []

    def test_consumed_media_swept_on_missing_abandonment(self, monkeypatch):
        """append 直前に chat が消えた("missing" 断念)場合も控えを回収する
        (敵対検証 6)。"""
        chat = self._insert_chat()
        self._insert_exec(
            "e1", chat.id, result="ok", meta={"context_images": ["file-1"]}
        )

        monkeypatch.setattr(
            self.Chats, "append_delivery_turn", lambda *a, **k: ("missing", None)
        )

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "abandoned"
        assert self.Executions.get_execution_by_id("e1").delivered_at is not None
        assert self.resume._consumed_media == {}

    def test_consumed_media_swept_on_step1_abandonment(self):
        """前サイクルで media を控えた後に chat が消えた(手順 1 断念)場合も
        回収する(敵対検証 6 — 手順 1 の早期 return は finally を通らない)。"""
        chat = self._insert_chat()
        self._insert_exec(
            "e1", chat.id, result="ok", meta={"context_images": ["file-1"]}
        )
        # 前サイクルの CAS 負けで media を控えた状態を再現
        self.resume._consumed_media["e1"] = (["file-1"], [])
        assert self.Chats.delete_chat_by_id(chat.id)

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "abandoned"
        assert self.Executions.get_execution_by_id("e1").delivered_at is not None
        assert self.resume._consumed_media == {}

    def test_consumed_media_survives_conflict_while_undelivered(self, monkeypatch):
        """まだ配達されていない行の控えは掃除**されない**(本来の持ち越し —
        CAS 連敗の "retry" 終端でも media を失わない)。"""
        chat = self._insert_chat()
        self._insert_exec(
            "e1", chat.id, result="ok", meta={"context_images": ["file-1"]}
        )

        monkeypatch.setattr(
            self.Chats, "append_delivery_turn", lambda *a, **k: ("conflict", None)
        )

        assert asyncio.run(self.resume._deliver_cycle(chat.id)) == "retry"
        # 未配達のままなので控えは持ち越される(次サイクルの notice 用)
        assert self.resume._consumed_media == {"e1": (["file-1"], [])}

    # ---- wake / registry / wait_for_quiet_delivery ----

    def test_wake_runs_driver_to_completion(self, monkeypatch):
        calls = self._patch_turn_start(monkeypatch)
        chat = self._insert_chat()
        self._insert_exec("e1", chat.id)

        async def main():
            self.resume.wake(chat.id)
            assert chat.id in self.resume._delivery_tasks
            # 二重 wake でも driver は 1 本
            task = self.resume._delivery_tasks[chat.id]
            self.resume.wake(chat.id)
            assert self.resume._delivery_tasks[chat.id] is task
            # 未配達が尽きたら registry から自分を掃除して終了する
            for _ in range(500):
                if chat.id not in self.resume._delivery_tasks:
                    break
                await asyncio.sleep(0.01)
            assert chat.id not in self.resume._delivery_tasks
            assert chat.id not in self.resume._delivery_events

        asyncio.run(main())
        assert self.Executions.get_execution_by_id("e1").delivered_at is not None
        assert len(calls) == 1
        assert len(self._notice_messages(chat.id)) == 1

    def test_wake_is_noop_without_app_or_multiworker(self, monkeypatch):
        async def main():
            self.resume._app = None
            self.resume.wake("c1")
            assert self.resume._delivery_tasks == {}

            self.resume._app = self.app
            monkeypatch.setattr(self.executions, "UVICORN_WORKERS", 2)
            self.resume.wake("c1")
            assert self.resume._delivery_tasks == {}

        asyncio.run(main())

    def test_wait_for_quiet_delivery(self):
        async def main():
            # 配達中でなければ即返す
            started = time.monotonic()
            await self.resume.wait_for_quiet_delivery("c1", timeout=1.0)
            assert time.monotonic() - started < 0.05

            # 配達中は timeout まで待って通す(排他ではなく窓の緩和)
            self.resume._delivering.add("c1")
            try:
                started = time.monotonic()
                await self.resume.wait_for_quiet_delivery("c1", timeout=0.15)
                assert 0.1 <= time.monotonic() - started < 1.0
            finally:
                self.resume._delivering.discard("c1")

            # 途中で配達が終われば早く抜ける
            self.resume._delivering.add("c2")

            async def release():
                await asyncio.sleep(0.05)
                self.resume._delivering.discard("c2")

            releaser = asyncio.get_running_loop().create_task(release())
            started = time.monotonic()
            await self.resume.wait_for_quiet_delivery("c2", timeout=5.0)
            assert time.monotonic() - started < 1.0
            await releaser

        asyncio.run(main())

    def test_tasks_cleanup_hook_notifies_listener(self):
        from nucllei import tasks as tasks_module

        seen = []

        def listener(namespace_id):
            seen.append(namespace_id)

        tasks_module.register_cleanup_listener(listener)
        try:

            async def main():
                async def noop():
                    return None

                _, task = tasks_module.create_task(noop(), id="exec:chat-9")
                await task
                await asyncio.sleep(0)

            asyncio.run(main())
            assert "exec:chat-9" in seen
        finally:
            tasks_module._cleanup_listeners.remove(listener)

    def test_on_task_cleanup_strips_exec_namespace(self, monkeypatch):
        woken = []
        monkeypatch.setattr(self.resume, "wake", lambda chat_id: woken.append(chat_id))
        self.resume._on_task_cleanup("exec:chat-1")  # wake ①(runner 終了)
        self.resume._on_task_cleanup("chat-2")  # wake ④(ターン終了)
        self.resume._on_task_cleanup(None)  # 名前空間なしは無視
        assert woken == ["chat-1", "chat-2"]
