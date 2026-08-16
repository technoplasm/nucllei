"""execution(非同期 tool 実行のエンティティと実行系 — issue #110)の回帰。

_memos/async-exec-design.md の状態機械(§4)と実行系(§5)を固定する:

- D1: 終端遷移はすべて guarded UPDATE(CAS)。同着は先勝ちで、負けは no-op
- 権威の分担: orphaned = boot スイープ、照会時は liveness ガード(防御)、
  一覧 API は**検出のみで書かない**
- liveness ガードのグレース(spawn 窓)と再読(完走直後との取り違え防止)
- stop の冪等(既に終端なら no-op)と、registry に居る exec の task cancel
- chat 削除の cascade(単発 / 子孫 / 全削除)— exec task は `exec:{chat_id}`
  名前空間で detach されており、親ターンの cancel 連鎖では止まらない
- 新エンドポイント 2 本(一覧 / 個別停止)の所有者検証・chat 一致検証・応答の形
- delivered_at(サーバ駆動配達の土台 — redo 契約 M2)のモデル関数 3 本

呼び出し面(background 昇格・check_task / await_task / stop_task)は
test_async_exec.py。
"""

import asyncio
import time

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestExecutions(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import Chats
        from nucllei.models.executions import Executions
        from nucllei.utils import executions

        self.Chats = Chats
        self.Executions = Executions
        self.executions = executions
        # プロセス内 registry / 会計はテスト間で持ち越さない
        executions._exec_tasks.clear()
        executions._active_execs_by_user.clear()
        executions._exec_sinks.clear()

    # ---- helpers ----

    def _insert(self, exec_id="e1", chat_id="c1", user_id="1", tool_name="probe"):
        row = self.Executions.insert_execution(
            id=exec_id,
            user_id=user_id,
            chat_id=chat_id,
            message_id="pm-a",
            tool_call_id=f"functions.{tool_name}:0",
            tool_name=tool_name,
            args={"x": 1},
        )
        assert row is not None
        return row

    def _age(self, exec_id, seconds):
        """created_at を過去へずらす(spawn 窓グレースを抜けさせる)。"""
        from nucllei.internal.db import get_db
        from nucllei.models.executions import Execution

        with get_db() as db:
            db.query(Execution).filter_by(id=exec_id).update(
                {"created_at": int(time.time()) - seconds}
            )
            db.commit()
        return self.Executions.get_execution_by_id(exec_id)

    def _age_finished(self, exec_id, seconds):
        """finished_at を過去へずらす(scope=active の追従窓を抜けさせる)。"""
        from nucllei.internal.db import get_db
        from nucllei.models.executions import Execution

        with get_db() as db:
            db.query(Execution).filter_by(id=exec_id).update(
                {"finished_at": int(time.time()) - seconds}
            )
            db.commit()
        return self.Executions.get_execution_by_id(exec_id)

    def _insert_chat(self, user_id="1", title="chat", **kwargs):
        from nucllei.models.chats import ChatForm

        chat = self.Chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={"title": title, "history": {"currentId": None, "messages": {}}}
            ),
            **kwargs,
        )
        assert chat is not None
        return chat

    # ---- 状態機械: guarded UPDATE の CAS 勝敗 ----

    def test_finish_execution_is_a_one_shot_cas(self):
        self._insert()

        assert (
            self.Executions.finish_execution("e1", status="done", result="ok") is True
        )
        # 同着の 2 人目は負け(no-op)— 終端は一方向・一度きり
        assert (
            self.Executions.finish_execution("e1", status="stopped", error="x") is False
        )

        row = self.Executions.get_execution_by_id("e1")
        assert row.status == "done"
        assert row.result == "ok"
        assert row.error is None
        assert row.finished_at is not None

        # 終端間の遷移も、存在しない行への遷移も負け
        assert self.Executions.finish_execution("e1", status="failed") is False
        assert self.Executions.finish_execution("no-such", status="done") is False
        # running への「遷移」は終端でないので拒否(ログのみ・書かない)
        assert self.Executions.finish_execution("e1", status="running") is False

    def test_finish_execution_only_writes_given_fields(self):
        self._insert()
        assert self.Executions.finish_execution(
            "e1",
            status="failed",
            error="boom",
            duration=1.25,
            meta={"truncated": True},
            usage={"prompt_tokens": 3},
        )
        row = self.Executions.get_execution_by_id("e1")
        assert row.status == "failed"
        assert row.error == "boom"
        assert row.duration == 1.25
        assert row.meta == {"truncated": True}
        assert row.usage == {"prompt_tokens": 3}
        assert row.result is None
        assert row.args == {"x": 1}  # 監査(spawn 時の実引数)は残る

    # ---- 状態機械: boot スイープ(orphaned の権威)----

    def test_boot_sweep_orphans_running_rows_only(self):
        self._insert("e1")
        self._insert("e2")
        self._insert("e3")
        assert self.Executions.finish_execution("e3", status="done", result="ok")

        swept = self.executions.sweep_executions_on_boot()
        assert swept == 2

        for exec_id in ("e1", "e2"):
            row = self.Executions.get_execution_by_id(exec_id)
            assert row.status == "orphaned"
            assert row.error == self.executions.ORPHANED_ON_BOOT_ERROR
            assert row.finished_at is not None
        assert self.Executions.get_execution_by_id("e3").status == "done"

        # 冪等(2 回目は running が居ないので 0 件)
        assert self.executions.sweep_executions_on_boot() == 0

    def test_boot_sweep_purges_stale_results_when_retention_is_set(self, monkeypatch):
        self._insert("old")
        self._insert("fresh")
        assert self.Executions.finish_execution(
            "old", status="done", result="重い結果", meta={"render": {"kind": "json"}}
        )
        assert self.Executions.finish_execution("fresh", status="done", result="新しい")

        from nucllei.internal.db import get_db
        from nucllei.models.executions import Execution

        with get_db() as db:
            db.query(Execution).filter_by(id="old").update(
                {"finished_at": int(time.time()) - 10 * 86400}
            )
            db.commit()

        # 既定(0)では GC しない
        assert self.executions.sweep_executions_on_boot() == 0
        assert self.Executions.get_execution_by_id("old").result == "重い結果"

        monkeypatch.setattr(self.executions, "EXEC_RETENTION_DAYS", 7)
        self.executions.sweep_executions_on_boot()

        old = self.Executions.get_execution_by_id("old")
        assert old.result is None and old.args is None and old.meta is None
        assert old.purged_at is not None
        # 監査用の軽量フィールドは残す
        assert old.status == "done" and old.tool_name == "probe"
        # 期限内の行は無傷
        assert self.Executions.get_execution_by_id("fresh").result == "新しい"

    # ---- 状態機械: liveness ガード(グレースと再読)----

    def test_liveness_guard_respects_spawn_grace(self):
        row = self._insert()  # created_at = 今 → グレース内
        guarded = asyncio.run(self.executions.ensure_execution_liveness(row))
        assert guarded.status == "running"
        assert self.Executions.get_execution_by_id("e1").status == "running"

    def test_liveness_guard_orphans_stale_running_row(self):
        self._insert()
        row = self._age("e1", self.executions.EXEC_SPAWN_GRACE_S + 5)

        guarded = asyncio.run(self.executions.ensure_execution_liveness(row))
        assert guarded.status == "orphaned"
        assert guarded.error == self.executions.ORPHANED_ON_PROBE_ERROR
        assert self.Executions.get_execution_by_id("e1").status == "orphaned"

    def test_liveness_guard_skips_rows_present_in_registry(self):
        self._insert()
        row = self._age("e1", self.executions.EXEC_SPAWN_GRACE_S + 5)
        # spawn センチネル(task_id 未確定)でも「生きている」扱い
        self.executions._exec_tasks["e1"] = None

        guarded = asyncio.run(self.executions.ensure_execution_liveness(row))
        assert guarded.status == "running"
        assert self.Executions.get_execution_by_id("e1").status == "running"

    def test_liveness_guard_rereads_before_cas(self):
        """完走直後(DB は書き済み・registry は pop 済み)との取り違え防止。

        手元のモデルが running でも、行を再読して終端していれば CAS しない。
        """
        self._insert()
        stale = self._age("e1", self.executions.EXEC_SPAWN_GRACE_S + 5)
        assert self.Executions.finish_execution("e1", status="done", result="ok")
        assert stale.status == "running"  # 手元のモデルは古い

        guarded = asyncio.run(self.executions.ensure_execution_liveness(stale))
        assert guarded.status == "done"
        assert guarded.result == "ok"
        assert self.Executions.get_execution_by_id("e1").error is None

    def test_liveness_guard_is_a_noop_for_terminal_rows(self):
        self._insert()
        assert self.Executions.finish_execution("e1", status="stopped", error="x")
        row = self.Executions.get_execution_by_id("e1")
        guarded = asyncio.run(self.executions.ensure_execution_liveness(row))
        assert guarded.status == "stopped"

    # ---- stop(冪等・CAS 先行 → cancel)----

    def test_stop_execution_is_idempotent(self):
        self._insert()

        row = asyncio.run(self.executions.stop_execution("e1"))
        assert row.status == "stopped"
        assert row.error == self.executions.STOPPED_ERROR
        finished_at = row.finished_at

        again = asyncio.run(self.executions.stop_execution("e1"))
        assert again.status == "stopped"
        assert again.finished_at == finished_at  # CAS 負け = 書き換えない

    def test_stop_execution_does_not_touch_terminal_rows(self):
        self._insert()
        assert self.Executions.finish_execution("e1", status="done", result="ok")

        row = asyncio.run(self.executions.stop_execution("e1"))
        assert row.status == "done"
        assert row.result == "ok"
        assert row.error is None

    def test_stop_execution_cancels_the_running_task(self):
        """CAS に勝ったら task を cancel。runner は CancelledError で何も書かない
        (D2)ので、行は stop 経路が書いた stopped のまま。"""
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(30)
            return "遅すぎる"

        async def main():
            spawned = await self.executions.spawn_execution(
                user_id="1",
                chat_id="c1",
                message_id="pm-a",
                tool_call_id="functions.slow:0",
                tool_name="slow",
                entry={"callable": slow},
                params={},
            )
            assert spawned["status"] == "running"
            exec_id = spawned["task_id"]  # 返りのキーはモデル向け用語(契約 T1)
            await asyncio.wait_for(started.wait(), timeout=5)

            row = await self.executions.stop_execution(exec_id)
            assert row.status == "stopped"
            # registry / per-user 会計が解放される(done callback)
            for _ in range(20):
                if exec_id not in self.executions._exec_tasks:
                    break
                await asyncio.sleep(0.01)
            assert exec_id not in self.executions._exec_tasks
            assert self.executions._active_execs_by_user == {}
            return exec_id

        exec_id = asyncio.run(main())
        row = self.Executions.get_execution_by_id(exec_id)
        assert row.status == "stopped"
        assert row.result is None

    def test_stop_during_spawn_window_cancels_once_the_task_id_is_known(
        self, monkeypatch
    ):
        """INSERT 中(センチネル期間)の stop は予約になり、task 確定時に cancel。"""
        import threading

        entered = threading.Event()
        release = threading.Event()

        async def never_should_finish():
            await asyncio.sleep(30)

        original_insert = self.Executions.insert_execution

        def gated_insert(**kwargs):
            row = original_insert(**kwargs)
            entered.set()
            release.wait(timeout=30)  # to_thread 実行なのでループは止まらない
            return row

        monkeypatch.setattr(self.Executions, "insert_execution", gated_insert)

        async def main():
            try:
                spawn = asyncio.create_task(
                    self.executions.spawn_execution(
                        user_id="1",
                        chat_id="c1",
                        message_id="pm-a",
                        tool_call_id="functions.slow:0",
                        tool_name="slow",
                        entry={"callable": never_should_finish},
                        params={},
                    )
                )
                assert await asyncio.to_thread(entered.wait, 10)
                exec_id = next(iter(self.executions._exec_tasks))
                assert self.executions._exec_tasks[exec_id] is None  # センチネル

                stopped = await self.executions.stop_execution(exec_id)
                assert stopped.status == "stopped"
                assert (
                    self.executions._exec_tasks[exec_id]
                    == self.executions._STOP_REQUESTED
                )

                release.set()
                spawned = await asyncio.wait_for(spawn, timeout=10)
                assert spawned["task_id"] == exec_id
                for _ in range(100):
                    if exec_id not in self.executions._exec_tasks:
                        break
                    await asyncio.sleep(0.01)
                assert exec_id not in self.executions._exec_tasks
                return exec_id
            finally:
                release.set()

        exec_id = asyncio.run(main())
        assert self.Executions.get_execution_by_id(exec_id).status == "stopped"

    # ---- cascade(chat 削除)----

    def test_stop_executions_by_chat_id_targets_running_rows_of_that_chat(self):
        self._insert("a1", chat_id="chat-a")
        self._insert("a2", chat_id="chat-a")
        self._insert("a3", chat_id="chat-a")
        assert self.Executions.finish_execution("a3", status="done", result="ok")
        self._insert("b1", chat_id="chat-b")

        stopped = asyncio.run(self.executions.stop_executions_by_chat_id("chat-a"))
        assert stopped == 2
        assert self.Executions.get_execution_by_id("a1").status == "stopped"
        assert self.Executions.get_execution_by_id("a2").status == "stopped"
        assert self.Executions.get_execution_by_id("a3").status == "done"
        # 他 chat は無傷
        assert self.Executions.get_execution_by_id("b1").status == "running"
        # cascade の停止は CAS と同時に配達済み(敵対検証 1 — 配達先消滅の恒久断念)。
        # CAS 負け(自然完走済み)の a3 は保険の一括 mark(routers 側)が拾う
        assert self.Executions.get_execution_by_id("a1").delivered_at is not None
        assert self.Executions.get_execution_by_id("a2").delivered_at is not None
        assert self.Executions.get_execution_by_id("a3").delivered_at is None
        assert self.Executions.get_execution_by_id("b1").delivered_at is None

    def test_individual_stop_keeps_the_row_undelivered(self):
        """チップの個別 stop(stop エンドポイント / stop_task の基盤)は配達対象の
        まま — delivered_at を立てない(敵対検証 1 の対称: cascade だけが立てる)。"""
        self._insert()
        row = asyncio.run(self.executions.stop_execution("e1"))
        assert row.status == "stopped"
        assert row.delivered_at is None

    def test_delete_chat_cascades_stop_to_chat_tree_executions(self):
        parent = self._insert_chat(title="parent")
        child = self._insert_chat(
            title="agent",
            kind="agent",
            parent_chat_id=parent.id,
            parent_message_id="pm-a",
            depth=1,
        )
        other = self._insert_chat(title="other")
        self._insert("p1", chat_id=parent.id)
        self._insert("c1", chat_id=child.id)
        self._insert("o1", chat_id=other.id)

        with mock_webui_user(id="1"):
            response = self.fast_api_client.delete(self.create_url(f"/{parent.id}"))
        assert response.status_code == 200, response.text

        assert self.Executions.get_execution_by_id("p1").status == "stopped"
        # 子孫 agent session の分も止める(cancel 連鎖では届かない — D6)
        assert self.Executions.get_execution_by_id("c1").status == "stopped"
        assert self.Executions.get_execution_by_id("o1").status == "running"
        # 配達先消滅 = 恒久断念(契約 M2)— 未配達行に delivered_at を立てる
        assert self.Executions.get_execution_by_id("p1").delivered_at is not None
        assert self.Executions.get_execution_by_id("c1").delivered_at is not None
        assert self.Executions.get_execution_by_id("o1").delivered_at is None

    def test_delete_all_chats_cascades_stop_to_executions(self):
        first = self._insert_chat(title="first")
        second = self._insert_chat(title="second")
        foreign = self._insert_chat(user_id="9", title="foreign")
        self._insert("x1", chat_id=first.id)
        self._insert("x2", chat_id=second.id)
        self._insert("y1", chat_id=foreign.id, user_id="9")

        with mock_webui_user(id="1"):
            response = self.fast_api_client.delete(self.create_url())
        assert response.status_code == 200, response.text

        assert self.Executions.get_execution_by_id("x1").status == "stopped"
        assert self.Executions.get_execution_by_id("x2").status == "stopped"
        assert self.Executions.get_execution_by_id("y1").status == "running"
        # 配達先消滅 = 恒久断念(契約 M2)
        assert self.Executions.get_execution_by_id("x1").delivered_at is not None
        assert self.Executions.get_execution_by_id("x2").delivered_at is not None
        assert self.Executions.get_execution_by_id("y1").delivered_at is None

    # ---- delivered_at(サーバ駆動配達の土台 — redo 契約 M2)----

    def test_mark_delivered_is_one_shot(self):
        self._insert("e1")
        self._insert("e2")
        self._insert("e3")
        assert self.Executions.finish_execution("e1", status="done", result="ok")
        assert self.Executions.finish_execution("e2", status="failed", error="x")

        assert self.Executions.mark_delivered([]) == 0
        # 存在しない id は数えない・他はまとめて立つ
        assert self.Executions.mark_delivered(["e1", "e2", "no-such"]) == 2
        first = self.Executions.get_execution_by_id("e1").delivered_at
        assert first is not None
        assert self.Executions.get_execution_by_id("e2").delivered_at is not None
        assert self.Executions.get_execution_by_id("e3").delivered_at is None

        # 一度立てたら戻さない・上書きしない(WHERE delivered_at IS NULL の guard)
        assert self.Executions.mark_delivered(["e1", "e2"]) == 0
        assert self.Executions.get_execution_by_id("e1").delivered_at == first

    def test_get_undelivered_terminal_by_chat_id(self):
        self._insert("r1", chat_id="chat-a")  # running は配達対象でない
        self._insert("u1", chat_id="chat-a")
        self._insert("u2", chat_id="chat-a")
        self._insert("d1", chat_id="chat-a")
        self._insert("b1", chat_id="chat-b")
        assert self.Executions.finish_execution("u1", status="done", result="ok")
        assert self.Executions.finish_execution("u2", status="stopped", error="x")
        assert self.Executions.finish_execution("d1", status="done", result="ok")
        assert self.Executions.finish_execution("b1", status="failed", error="x")
        assert self.Executions.mark_delivered(["d1"]) == 1

        rows = self.Executions.get_undelivered_terminal_by_chat_id("chat-a")
        # created_at 同秒でも id で tie-break した全順序(古い順)
        assert [row.id for row in rows] == ["u1", "u2"]
        # 他 chat は混ざらない
        assert [
            row.id
            for row in self.Executions.get_undelivered_terminal_by_chat_id("chat-b")
        ] == ["b1"]

    def test_list_chat_ids_with_undelivered(self):
        assert self.Executions.list_chat_ids_with_undelivered() == []
        self._insert("u1", chat_id="chat-a")
        self._insert("u2", chat_id="chat-a")
        self._insert("b1", chat_id="chat-b")
        self._insert("r1", chat_id="chat-c")  # running のみの chat は対象外
        assert self.Executions.finish_execution("u1", status="done", result="ok")
        assert self.Executions.finish_execution("u2", status="done", result="ok")
        assert self.Executions.finish_execution("b1", status="orphaned", error="x")

        assert sorted(self.Executions.list_chat_ids_with_undelivered()) == [
            "chat-a",
            "chat-b",
        ]

        # 全部配達済みになった chat は消える(boot スキャンの終了条件)
        assert self.Executions.mark_delivered(["u1", "u2"]) == 2
        assert self.Executions.list_chat_ids_with_undelivered() == ["chat-b"]

    def test_user_scoped_reads_for_delete_all_cascade(self):
        """B-5: 全チャット削除 cascade の一括読み口(user_id スコープ・N+1 回避)。"""
        self._insert("r1", chat_id="c1", user_id="1")
        self._insert("r2", chat_id="c2", user_id="1")
        self._insert("f1", chat_id="c9", user_id="9")  # 他ユーザーは混ざらない
        self._insert("u1", chat_id="c1", user_id="1")
        self._insert("d1", chat_id="c2", user_id="1")
        assert self.Executions.finish_execution("u1", status="done", result="ok")
        assert self.Executions.finish_execution("d1", status="done", result="ok")
        assert self.Executions.mark_delivered(["d1"]) == 1

        assert [
            row.id for row in self.Executions.get_running_executions_by_user_id("1")
        ] == ["r1", "r2"]
        assert [
            row.id for row in self.Executions.get_undelivered_terminal_by_user_id("1")
        ] == ["u1"]

    def test_delete_all_chats_delivers_undelivered_terminal_rows(self):
        """B-5: cascade は running の stop に加え、自然完走済みの未配達 terminal も
        恒久断念として delivered 確定する(契約 M2 — stop CAS 負け相当の行)。"""
        chat = self._insert_chat(title="mine")
        self._insert("t1", chat_id=chat.id)
        assert self.Executions.finish_execution("t1", status="done", result="ok")

        with mock_webui_user(id="1"):
            response = self.fast_api_client.delete(self.create_url())
        assert response.status_code == 200, response.text
        assert self.Executions.get_execution_by_id("t1").delivered_at is not None

    # ---- エンドポイント(一覧 / 個別停止)----

    def test_execution_list_shape_and_ownership(self):
        chat = self._insert_chat(title="chat")
        self._insert("e1", chat_id=chat.id, tool_name="run_subagent")
        self._insert("e2", chat_id=chat.id)
        self._insert("e3", chat_id=chat.id)
        assert self.Executions.finish_execution(
            "e2", status="done", result="秘密の結果", duration=2.5
        )
        assert self.Executions.finish_execution("e3", status="done", result="ok")
        assert self.Executions.mark_delivered(["e3"]) == 1

        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url(f"/{chat.id}/executions")
            )
        assert response.status_code == 200, response.text
        body = response.json()
        # 応答は {executions, resume_suspended} のオブジェクト形(契約 M2)
        assert set(body.keys()) == {"executions", "resume_suspended"}
        assert body["resume_suspended"] is False
        items = body["executions"]
        assert [item["id"] for item in items] == ["e1", "e2", "e3"]
        assert set(items[0].keys()) == {
            "id",
            "tool_name",
            "status",
            "created_at",
            "finished_at",
            "duration",
            "error",
            "delivered",
        }
        assert items[0]["tool_name"] == "run_subagent"
        assert items[0]["status"] == "running"
        assert items[1]["status"] == "done"
        assert items[1]["duration"] == 2.5
        # delivered = 「結果がモデルの文脈に載った」事実(FE 未回収バッジの根拠)
        assert items[0]["delivered"] is False
        assert items[1]["delivered"] is False
        assert items[2]["delivered"] is True
        # result 本文は載せない(回収は await_task の仕事)
        assert "result" not in items[1]

        # 所有者以外は 401(行の存在も漏らさない)
        with mock_webui_user(id="9"):
            response = self.fast_api_client.get(
                self.create_url(f"/{chat.id}/executions")
            )
        assert response.status_code == 401, response.text

    def test_execution_list_scope_active_bounds_the_response(self):
        """B-6: scope=active は running ∨ 未配達 ∨ 直近終端のみ(poll tick 用の
        有界応答)。terminal ∧ 配達済み ∧ 古い行は不変なので落とす(FE はマージで
        保持)。未知の scope は 422 — 黙って全量にしない。"""
        chat = self._insert_chat(title="chat")
        self._insert("run1", chat_id=chat.id)
        self._insert("und1", chat_id=chat.id)
        self._insert("rec1", chat_id=chat.id)
        self._insert("old1", chat_id=chat.id)
        assert self.Executions.finish_execution("und1", status="done", result="ok")
        assert self.Executions.finish_execution("rec1", status="done", result="ok")
        assert self.Executions.finish_execution("old1", status="done", result="ok")
        assert self.Executions.mark_delivered(["rec1", "old1"]) == 2
        self._age_finished("old1", 3600)  # 追従窓(10 分)の外へ

        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url(f"/{chat.id}/executions?scope=active")
            )
        assert response.status_code == 200, response.text
        body = response.json()
        # created_at 同秒 → id tie-break の昇順(old1 だけ落ちる)
        assert [item["id"] for item in body["executions"]] == ["rec1", "run1", "und1"]

        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url(f"/{chat.id}/executions?scope=bogus")
            )
        assert response.status_code == 422, response.text

    def test_execution_list_detects_orphans_without_writing(self):
        """一覧は liveness を**検出のみ**(orphaned の権威は boot スイープ)。"""
        chat = self._insert_chat(title="chat")
        self._insert("fresh", chat_id=chat.id)
        self._insert("stale", chat_id=chat.id)
        self._age("stale", self.executions.EXEC_SPAWN_GRACE_S + 5)

        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url(f"/{chat.id}/executions")
            )
        assert response.status_code == 200, response.text
        by_id = {item["id"]: item for item in response.json()["executions"]}
        assert by_id["fresh"]["status"] == "running"  # spawn 窓グレース
        assert by_id["stale"]["status"] == "orphaned"

        # DB は書き換えない
        assert self.Executions.get_execution_by_id("stale").status == "running"

    def test_execution_stop_endpoint_ownership_and_chat_binding(self):
        chat = self._insert_chat(title="chat")
        other = self._insert_chat(title="other")
        self._insert("e1", chat_id=chat.id)
        self._insert("o1", chat_id=other.id)

        # 他人の chat → 401
        with mock_webui_user(id="9"):
            response = self.fast_api_client.post(
                self.create_url(f"/{chat.id}/executions/e1/stop")
            )
        assert response.status_code == 401, response.text
        assert self.Executions.get_execution_by_id("e1").status == "running"

        with mock_webui_user(id="1"):
            # 別 chat の exec / 存在しない exec → 404
            response = self.fast_api_client.post(
                self.create_url(f"/{chat.id}/executions/o1/stop")
            )
            assert response.status_code == 404, response.text
            response = self.fast_api_client.post(
                self.create_url(f"/{chat.id}/executions/no-such/stop")
            )
            assert response.status_code == 404, response.text

            response = self.fast_api_client.post(
                self.create_url(f"/{chat.id}/executions/e1/stop")
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["id"] == "e1"
            assert body["status"] == "stopped"
            assert body["error"] == self.executions.STOPPED_ERROR

            # 冪等(2 回目も同じ形)
            response = self.fast_api_client.post(
                self.create_url(f"/{chat.id}/executions/e1/stop")
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "stopped"

        assert self.Executions.get_execution_by_id("o1").status == "running"
