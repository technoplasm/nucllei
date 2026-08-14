"""非同期 tool 実行の呼び出し面(issue #110 §7/§8 + redo 契約 M0/M1)の回帰。

_memos/async-exec-design.md の K2(tool 単位の静的適格性 `backgroundable` +
呼び出し時の共有 `background` 引数)と、redo 契約の tool 面を固定する:

- spec 注入は適格 entry の **コピー**にのみ(tools_dict 側 = unknown-drop の基準は不変)。
  恒久不適格名 / 自前 `background` 宣言 / agent session(D9)・chat 文脈なしでは注入しない
- execute_tool_calls の分岐: `background` は allowed_params フィルタより **前に pop**
  (callable へ渡さない)。適格なら spawn(running 形 K3 — 返りは task_id)、
  不適格なら無視して同期実行
- runner の終端書き込み(done / failed / timeout)と切詰め・meta harvest
- 回収 builtin 3 tool(契約 T2 — 役割 1 関数 1 つ):
  check_task(状態のみ・read-only)/ await_task(有界窓 1 回の複数待ち・部分回収・
  replay・render_descriptor・delivered_at)/ stop_task(冪等停止 + delivered_at)
- 切詰めの一般化(契約 M0): 同期経路の生産点適用と回収 builtin の適用除外
- mount 条件の分離(契約 M1)と A-3(遅延ロードでの回収 builtin 追加 mount)

エンティティと状態機械(CAS / boot スイープ / liveness / cascade / delivered_at の
モデル関数)は test_executions.py。
"""

import asyncio
import json
import time

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest


class TestAsyncExec(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    CHAT_ID = "chat-1"
    MESSAGE_ID = "pm-a"

    def setup_method(self):
        super().setup_method()
        import types

        from nucllei.models.executions import Executions
        from nucllei.utils import exec_tools, executions

        self.Executions = Executions
        self.exec_tools = exec_tools
        self.executions = executions
        executions._exec_tasks.clear()
        executions._active_execs_by_user.clear()
        executions._exec_sinks.clear()
        self.user = types.SimpleNamespace(
            id="1", email="john.doe@nucllei.local", name="John Doe", role="user"
        )

    # ---- helpers ----

    def _metadata(self, **overrides):
        return {
            "user_id": "1",
            "chat_id": self.CHAT_ID,
            "message_id": self.MESSAGE_ID,
            **overrides,
        }

    def _entry(self, callable_, *, properties=None, backgroundable=True, **extra):
        entry = {
            "callable": callable_,
            "spec": {
                "name": "probe",
                "parameters": {
                    "type": "object",
                    "properties": properties if properties is not None else {},
                },
            },
            **extra,
        }
        if backgroundable:
            entry["backgroundable"] = True
        return entry

    def _call(self, arguments, name="probe", index=0):
        return {
            "id": f"functions.{name}:{index}",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }

    def _insert(self, exec_id="e1", *, chat_id=None, user_id="1", tool_name="probe"):
        row = self.Executions.insert_execution(
            id=exec_id,
            user_id=user_id,
            chat_id=self.CHAT_ID if chat_id is None else chat_id,
            message_id=self.MESSAGE_ID,
            tool_call_id=f"functions.{tool_name}:0",
            tool_name=tool_name,
            args={},
        )
        assert row is not None
        return row

    def _age(self, exec_id, seconds):
        """created_at を過去へずらす(順序・グレースの制御)。"""
        from nucllei.internal.db import get_db
        from nucllei.models.executions import Execution

        with get_db() as db:
            db.query(Execution).filter_by(id=exec_id).update(
                {"created_at": int(time.time()) - seconds}
            )
            db.commit()
        return self.Executions.get_execution_by_id(exec_id)

    def _exec_tools(self, metadata=None):
        return self.exec_tools.build_exec_tools(
            self.user, self._metadata() if metadata is None else metadata
        )

    def _delivered_at(self, exec_id):
        return self.Executions.get_execution_by_id(exec_id).delivered_at

    async def _wait_registry_release(self, exec_id):
        for _ in range(200):
            if exec_id not in self.executions._exec_tasks:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"execution {exec_id} was never released from registry")

    # ---- 適格性と spec 注入(§7)----

    def test_background_is_injected_into_a_copy_of_eligible_specs(self):
        async def probe(city: str = ""):
            return "ok"

        entry = self._entry(probe, properties={"city": {"type": "string"}})
        original = entry["spec"]

        spec = self.exec_tools.spec_for_model("probe", entry, True)
        assert set(spec["parameters"]["properties"]) == {"city", "background"}
        assert spec["parameters"]["properties"]["background"]["type"] == "boolean"
        # tools_dict 側の spec は汚さない(unknown-drop の基準は元 spec)
        assert set(original["parameters"]["properties"]) == {"city"}
        assert entry["spec"] is original

    def test_background_is_not_injected_when_ineligible(self):
        async def probe():
            return "ok"

        # backgroundable 宣言なし
        plain = self._entry(probe, backgroundable=False)
        assert self.exec_tools.spec_for_model("probe", plain, True) is plain["spec"]

        # 適格でも allow_background=False(agent session / chat 文脈なし)
        entry = self._entry(probe)
        assert self.exec_tools.spec_for_model("probe", entry, False) is entry["spec"]

        # 恒久不適格名(D4 — 回収 builtin 3 tool + load_skill)
        for name in ("load_skill", "check_task", "await_task", "stop_task"):
            assert self.exec_tools.entry_is_backgroundable(name, entry) is False

        # 自前の background 引数を宣言する tool は衝突ガードで不適格(D4)
        collides = self._entry(probe, properties={"background": {"type": "string"}})
        assert self.exec_tools.entry_is_backgroundable("probe", collides) is False
        assert (
            self.exec_tools.spec_for_model("probe", collides, True) is collides["spec"]
        )

    def test_spawn_context_predicate(self):
        assert self.exec_tools.exec_spawn_context_ok(self._metadata()) is True
        # D9: 子ターン(agent session)には注入しない
        assert (
            self.exec_tools.exec_spawn_context_ok(self._metadata(agent_session=True))
            is False
        )
        # exec 行の束縛に必要な文脈が欠けていれば不可
        for missing in ("user_id", "chat_id", "message_id"):
            metadata = self._metadata()
            metadata.pop(missing)
            assert self.exec_tools.exec_spawn_context_ok(metadata) is False
        assert self.exec_tools.exec_spawn_context_ok(None) is False

    def test_recall_context_is_independent_of_async_exec_available(self, monkeypatch):
        """契約 M1(mount 条件の分離): 回収 builtin の mount は spawn 系
        (async_exec_available)と独立 — multi-worker でも過去の exec 行は
        DB 読みで回収できる。"""
        monkeypatch.setattr(self.executions, "UVICORN_WORKERS", 2)
        assert self.executions.async_exec_available() is False
        # spawn 面は閉じる
        assert self.exec_tools.exec_spawn_context_ok(self._metadata()) is False
        # 回収面は開いたまま
        assert self.exec_tools.exec_recall_context_ok(self._metadata()) is True
        # agent session(D9)と chat 文脈なしは回収面も閉じる
        assert (
            self.exec_tools.exec_recall_context_ok(self._metadata(agent_session=True))
            is False
        )
        metadata = self._metadata()
        metadata.pop("chat_id")
        assert self.exec_tools.exec_recall_context_ok(metadata) is False
        # message_id は回収に不要(行を作らない)
        metadata = self._metadata()
        metadata.pop("message_id")
        assert self.exec_tools.exec_recall_context_ok(metadata) is True

    def test_exec_builtins_are_mounted_with_disabled_outer_timeout(self):
        tools = self._exec_tools()
        assert set(tools) == {"check_task", "await_task", "stop_task"}
        # 自前の clamp 済み有界待ちが running 形を返すので外側 wait_for は無効化
        assert tools["await_task"]["timeout"] == 0
        assert "timeout" not in tools["check_task"]
        assert "timeout" not in tools["stop_task"]
        for name, entry in tools.items():
            # mount_recall_tools の冪等判定マーカ
            assert entry["exec_recall"] is True
            # 回収 builtin 自身は昇格対象にしない(恒久不適格 — 適格宣言も持たない)
            assert self.exec_tools.entry_is_backgroundable(name, entry) is False

    def test_mount_predicate_ignores_purged_rows(self):
        assert (
            self.Executions.has_awaitable_executions_by_chat_id(self.CHAT_ID) is False
        )
        self._insert("e1")
        assert self.Executions.has_awaitable_executions_by_chat_id(self.CHAT_ID) is True

        assert self.Executions.finish_execution("e1", status="done", result="ok")
        assert self.Executions.purge_stale_results(int(time.time()) + 1) == 1
        assert (
            self.Executions.has_awaitable_executions_by_chat_id(self.CHAT_ID) is False
        )

    def test_mount_recall_tools_is_idempotent_and_appends_specs(self):
        """A-3 の配達機序: 遅延ロード時は form_data["tools"] へ spec も append。
        二重呼び(初回合成 + A-3)は no-op で spec を重複させない。"""
        tools_dict = {}
        form_data = {"tools": []}

        mounted = self.exec_tools.mount_recall_tools(
            tools_dict, form_data, self.user, self._metadata()
        )
        assert mounted == ["check_task", "await_task", "stop_task"]
        assert set(tools_dict) == {"check_task", "await_task", "stop_task"}
        assert [t["function"]["name"] for t in form_data["tools"]] == mounted

        # 2 回目は no-op(既 mount)
        assert (
            self.exec_tools.mount_recall_tools(
                tools_dict, form_data, self.user, self._metadata()
            )
            == []
        )
        assert len(form_data["tools"]) == 3

        # form_data に "tools" が無い初回合成前は tools_dict だけに載る
        tools_dict2 = {}
        form_data2 = {}
        self.exec_tools.mount_recall_tools(
            tools_dict2, form_data2, self.user, self._metadata()
        )
        assert set(tools_dict2) == {"check_task", "await_task", "stop_task"}
        assert "tools" not in form_data2

    # ---- execute_tool_calls の分岐(§7)----

    def test_background_call_spawns_instead_of_running_inline(self):
        from nucllei.utils.middleware import execute_tool_calls

        started = asyncio.Event()
        release = asyncio.Event()
        captured = []

        async def probe(city: str = ""):
            captured.append(city)
            started.set()
            await release.wait()
            return {"answer": city}

        tools = {"probe": self._entry(probe, properties={"city": {"type": "string"}})}

        async def main():
            results = await execute_tool_calls(
                [self._call({"city": "札幌", "background": True, "unknown": 1})],
                tools,
                metadata=self._metadata(),
            )
            # 親メッセージの tool result は running 形のみ(K3・append-only)。
            # キーは task_id(契約 T1 — モデル向けの用語)
            payload = json.loads(results[0]["content"])
            assert payload["ok"] is True
            assert payload["status"] == "running"
            assert payload["tool_name"] == "probe"
            assert "exec_id" not in payload
            task_id = payload["task_id"]

            await asyncio.wait_for(started.wait(), timeout=5)
            # background / unknown は callable に渡らない(pop → unknown-drop)
            assert captured == ["札幌"]

            row = await asyncio.to_thread(self.Executions.get_execution_by_id, task_id)
            assert row.status == "running"
            assert row.chat_id == self.CHAT_ID
            assert row.message_id == self.MESSAGE_ID
            assert row.tool_call_id == "functions.probe:0"
            assert row.args == {"city": "札幌"}  # 監査は unknown-drop 後の実引数

            release.set()
            await self._wait_registry_release(task_id)
            return task_id

        task_id = asyncio.run(main())
        row = self.Executions.get_execution_by_id(task_id)
        assert row.status == "done"
        assert json.loads(row.result) == {"answer": "札幌"}
        assert row.meta["render"] == {"kind": "json"}
        assert row.duration >= 0

    def test_background_is_ignored_for_ineligible_tools_and_contexts(self):
        from nucllei.utils.middleware import execute_tool_calls

        calls = []

        async def probe(city: str = ""):
            calls.append(city)
            return "同期の結果"

        # 適格宣言なし → 同期実行(background は callable にも渡らない)
        plain = {
            "probe": self._entry(
                probe, properties={"city": {"type": "string"}}, backgroundable=False
            )
        }
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"city": "旭川", "background": True})],
                plain,
                metadata=self._metadata(),
            )
        )
        assert results[0]["content"] == "同期の結果"
        assert calls == ["旭川"]

        # 適格でも agent session(D9)では昇格しない
        eligible = {
            "probe": self._entry(probe, properties={"city": {"type": "string"}})
        }
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"city": "帯広", "background": True})],
                eligible,
                metadata=self._metadata(agent_session=True),
            )
        )
        assert results[0]["content"] == "同期の結果"
        assert calls == ["旭川", "帯広"]

        # chat 文脈が無い素の completions でも同様
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"city": "釧路", "background": True})], eligible
            )
        )
        assert results[0]["content"] == "同期の結果"
        assert calls == ["旭川", "帯広", "釧路"]

        # 行は 1 本も作られていない
        assert self.Executions.get_executions_by_chat_id(self.CHAT_ID) == []

    def test_tool_declared_background_argument_is_passed_through(self):
        """自前で background を宣言する tool(恒久不適格 D4)の実引数は奪わない。"""
        from nucllei.utils.middleware import execute_tool_calls

        captured = []

        async def probe(background: str = ""):
            captured.append(background)
            return "ok"

        tools = {
            "probe": self._entry(probe, properties={"background": {"type": "string"}})
        }
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"background": "夜景"})], tools, metadata=self._metadata()
            )
        )
        assert captured == ["夜景"]
        assert results[0]["content"] == "ok"
        assert self.Executions.get_executions_by_chat_id(self.CHAT_ID) == []

    # ---- 切詰めの一般化(契約 M0)----

    def test_sync_results_are_truncated_at_the_production_point(self, monkeypatch):
        """同期 tool の生産点(wire 正規化直後)で共通切詰め。render は text 降格。"""
        from nucllei.utils.middleware import execute_tool_calls

        monkeypatch.setattr(self.executions, "TOOL_RESULT_MAX_CHARS", 40)

        async def probe():
            return {"answer": "あ" * 100}

        tools = {"probe": self._entry(probe, backgroundable=False)}
        results = asyncio.run(
            execute_tool_calls([self._call({})], tools, metadata=self._metadata())
        )
        assert len(results[0]["content"]) == 40
        assert results[0]["truncated"] is True
        assert results[0]["original_chars"] > 40
        # 切詰めで JSON が壊れるため render(kind=json)は落ちる(text 降格)
        assert "render" not in results[0]

    def test_recall_builtin_results_are_exempt_from_truncation(self, monkeypatch):
        """回収 builtin の返りは適用除外(契約 M0 — 中身は保存時に切詰め済みで、
        envelope の json.dumps 膨張で再切詰めすると壊れ JSON になる)。"""
        from nucllei.utils.middleware import execute_tool_calls

        monkeypatch.setattr(self.executions, "TOOL_RESULT_MAX_CHARS", 40)
        self._insert("e1")
        assert self.Executions.finish_execution("e1", status="done", result="い" * 30)

        tools = self._exec_tools()
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"task_ids": ["e1"]}, name="await_task")],
                tools,
                metadata=self._metadata(),
            )
        )
        content = results[0]["content"]
        assert len(content) > 40  # envelope は上限を超えるが切らない
        payload = json.loads(content)  # 壊れ JSON になっていない
        assert payload["tasks"][0]["result"] == "い" * 30
        assert "truncated" not in results[0]

    # ---- spawn / runner(§5)----

    def _spawn(self, callable_, *, entry=None, params=None, user_id="1"):
        entry = entry if entry is not None else self._entry(callable_)
        return self.executions.spawn_execution(
            user_id=user_id,
            chat_id=self.CHAT_ID,
            message_id=self.MESSAGE_ID,
            tool_call_id="functions.probe:0",
            tool_name="probe",
            entry=entry,
            params=params or {},
        )

    def test_runner_records_failure_and_timeout(self):
        async def boom():
            raise RuntimeError("壊れた")

        async def slow():
            await asyncio.sleep(30)

        async def main():
            failed = await self._spawn(boom)
            await self._wait_registry_release(failed["task_id"])

            timed_out = await self._spawn(slow, entry=self._entry(slow, timeout=0.05))
            await self._wait_registry_release(timed_out["task_id"])
            return failed["task_id"], timed_out["task_id"]

        failed_id, timed_out_id = asyncio.run(main())

        failed = self.Executions.get_execution_by_id(failed_id)
        assert failed.status == "failed"
        assert failed.error == "壊れた"
        assert failed.result is None

        timed_out = self.Executions.get_execution_by_id(timed_out_id)
        assert timed_out.status == "failed"
        assert "タイムアウト" in timed_out.error

    def test_runner_harvests_usage_images_and_truncates(self, monkeypatch):
        from nucllei.utils import tool_output

        monkeypatch.setattr(self.executions, "TOOL_RESULT_MAX_CHARS", 40)

        # progress は走行中だけ読める揮発値(in-process sink)。probe が即 return すると
        # 観測前に完走してしまうため、観測できたことを gate で通知するまで待たせる
        observed = asyncio.Event()

        async def probe():
            tool_output.feed_image("file-1")
            tool_output.display("表示", "markdown")
            tool_output.progress("進行中", current=1, total=2)
            await asyncio.wait_for(observed.wait(), timeout=5)
            return {"answer": "あ" * 100, "usage": {"prompt_tokens": 7}}

        entry = self._entry(probe, render={"result": {"kind": "subagent"}})

        async def main():
            spawned = await self._spawn(probe, entry=entry)
            task_id = spawned["task_id"]
            # 走行中は sink 経由で最新 progress 1 件が読める(check_task の源)
            try:
                for _ in range(200):
                    progress = self.executions.get_latest_progress(task_id)
                    if progress:
                        assert progress["description"] == "進行中"
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("progress was never observed")
            finally:
                observed.set()  # 失敗時も probe を解放してタスクを残さない
            await self._wait_registry_release(task_id)
            return task_id

        task_id = asyncio.run(main())
        row = self.Executions.get_execution_by_id(task_id)
        assert row.status == "done"
        assert len(row.result) == 40
        assert row.meta["truncated"] is True
        assert row.meta["original_chars"] > 40
        # 切詰めで JSON が壊れるため render / descriptor は降格・破棄
        assert "render" not in row.meta
        assert "render_descriptor" not in row.meta
        # 副産物と usage は harvest される
        assert row.meta["context_images"] == ["file-1"]
        assert row.meta["display"] == [{"kind": "markdown", "content": "表示"}]
        assert row.usage == {"prompt_tokens": 7}
        # sink は runner 終了で回収される(progress の読み口も消える)
        assert self.executions.get_latest_progress(task_id) is None

    def test_spawn_enforces_concurrency_limits(self, monkeypatch):
        async def probe():
            return "ok"

        monkeypatch.setattr(self.executions, "ASYNC_EXEC_MAX_CONCURRENT", 1)
        self.executions._active_execs_by_user["1"] = 1
        error = json.loads(asyncio.run(self._spawn(probe)))
        assert error["ok"] is False
        assert "ユーザー上限" in error["error"]
        # 超過エラーは席を消費しない・行も作らない
        assert self.executions._active_execs_by_user == {"1": 1}
        assert self.Executions.get_executions_by_chat_id(self.CHAT_ID) == []

        monkeypatch.setattr(self.executions, "ASYNC_EXEC_MAX_TOTAL", 1)
        self.executions._active_execs_by_user.clear()
        self.executions._active_execs_by_user["other"] = 1
        error = json.loads(asyncio.run(self._spawn(probe)))
        assert error["ok"] is False
        assert "サーバー上限" in error["error"]

    def test_spawn_is_disabled_for_multi_worker_deployments(self, monkeypatch):
        """D7: registry はプロセス内 — multi-worker では liveness 判定が壊れる。"""

        async def probe():
            return "ok"

        monkeypatch.setattr(self.executions, "UVICORN_WORKERS", 2)
        assert self.executions.async_exec_available() is False
        error = json.loads(asyncio.run(self._spawn(probe)))
        assert error["ok"] is False
        assert "multi-worker" in error["error"]
        assert self.Executions.get_executions_by_chat_id(self.CHAT_ID) == []

    def test_multi_worker_hides_the_spawn_surface_but_recall_still_works(
        self, monkeypatch
    ):
        """D7 + 契約 M1: 露出・実行分岐は exec_spawn_context_ok で揃えて閉じる。
        回収(check_task / await_task / stop_task)だけは DB 読みで完結するため
        multi-worker でも生きる(mount 条件の分離)。
        """
        from nucllei.utils.middleware import execute_tool_calls

        calls = []

        async def probe(city: str = ""):
            calls.append(city)
            return "同期の結果"

        monkeypatch.setattr(self.executions, "UVICORN_WORKERS", 2)
        assert self.exec_tools.exec_spawn_context_ok(self._metadata()) is False

        entry = self._entry(probe, properties={"city": {"type": "string"}})
        # spec 注入なし(spawn 面は閉じる)
        assert (
            self.exec_tools.spec_for_model(
                "probe", entry, self.exec_tools.exec_spawn_context_ok(self._metadata())
            )
            is entry["spec"]
        )
        # 実行分岐も昇格しない(同期実行・行も作らない)
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"city": "小樽", "background": True})],
                {"probe": entry},
                metadata=self._metadata(),
            )
        )
        assert results[0]["content"] == "同期の結果"
        assert calls == ["小樽"]
        assert self.Executions.get_executions_by_chat_id(self.CHAT_ID) == []

        # 過去の terminal 行(前構成の遺産)は multi-worker でも回収できる
        self._insert("legacy")
        assert self.Executions.finish_execution("legacy", status="done", result="遺産")
        out = asyncio.run(
            self._exec_tools()["await_task"]["callable"](task_ids=["legacy"])
        )
        assert out["tasks"][0]["result"] == "遺産"

    def test_spawn_releases_the_slot_when_cancelled_mid_insert(self, monkeypatch):
        """INSERT の await でキャンセルされても席とセンチネルを残さない。

        CancelledError は BaseException 派生 — `except Exception` では捕らえられず、
        素通りすると同時数スロットと registry センチネルが恒久リークする
        (以後その exec_id は「走行中」と誤認され、ユーザーの席も 1 つ減ったまま)。
        """
        import threading

        entered = threading.Event()
        release = threading.Event()

        class BlockingExecutions:
            """INSERT を掴んだまま離さない Executions(キャンセル点の再現)。"""

            @staticmethod
            def insert_execution(**kwargs):
                entered.set()
                release.wait(5)
                return None

        monkeypatch.setattr(self.executions, "Executions", BlockingExecutions)

        async def probe():
            return "ok"

        async def main():
            spawning = asyncio.create_task(self._spawn(probe))
            await asyncio.to_thread(entered.wait, 5)
            # spawn 中(センチネル登録済み・INSERT の await 中)にキャンセル
            assert self.executions._active_execs_by_user == {"1": 1}
            assert list(self.executions._exec_tasks.values()) == [None]
            spawning.cancel()
            try:
                await spawning
            finally:
                release.set()

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(main())

        assert self.executions._exec_tasks == {}
        assert self.executions._active_execs_by_user == {}

    # ---- check_task(契約 T2 — 状態確認のみ)----

    def test_check_task_defaults_to_running_only_oldest_first(self):
        check = self._exec_tools()["check_task"]["callable"]

        # 空のとき: note で明示
        out = asyncio.run(check())
        assert out == {"tasks": [], "total": 0, "note": "実行中のタスクはありません"}

        self._insert("old-running")
        self._age("old-running", 100)
        self._insert("new-running")
        self._insert("finished")
        assert self.Executions.finish_execution("finished", status="done", result="秘")

        out = asyncio.run(check())
        assert out["total"] == 2
        assert "note" not in out
        assert [t["task_id"] for t in out["tasks"]] == ["old-running", "new-running"]
        entry = out["tasks"][0]
        assert entry["tool_name"] == "probe"
        assert entry["status"] == "running"
        assert isinstance(entry["started_at"], int)
        assert isinstance(entry["elapsed_s"], float)
        assert entry["elapsed_s"] >= 100
        # 結果本文は決して返さない
        for t in out["tasks"]:
            assert "result" not in t
        # read-only: liveness を書かない(グレースを過ぎた registry 不在の
        # running でも check_task は倒さない)
        assert self.Executions.get_execution_by_id("old-running").status == "running"

    def test_check_task_include_done_lists_newest_first_with_cap(self, monkeypatch):
        check = self._exec_tools()["check_task"]["callable"]
        for i, exec_id in enumerate(("t1", "t2", "t3")):
            self._insert(exec_id)
            self._age(exec_id, 300 - i * 100)  # t1 が最古、t3 が最新
        assert self.Executions.finish_execution(
            "t1", status="failed", error="壊れた", duration=1.5
        )
        assert self.Executions.finish_execution("t2", status="done", result="秘密")

        out = asyncio.run(check(include_done=True))
        assert out["total"] == 3
        assert [t["task_id"] for t in out["tasks"]] == ["t3", "t2", "t1"]
        by_id = {t["task_id"]: t for t in out["tasks"]}
        assert by_id["t2"]["status"] == "done"
        assert "result" not in by_id["t2"]  # 本文は返さない(回収は await_task)
        assert by_id["t1"]["status"] == "failed"
        assert by_id["t1"]["error"] == "壊れた"
        assert by_id["t1"]["duration"] == 1.5
        # terminal の elapsed_s は finished_at - created_at
        assert by_id["t1"]["elapsed_s"] >= 290

        # 上限は新しい順に切る(total は切る前の総数)
        monkeypatch.setattr(self.exec_tools, "CHECK_TASK_LIST_LIMIT", 2)
        out = asyncio.run(check(include_done=True))
        assert out["total"] == 3
        assert [t["task_id"] for t in out["tasks"]] == ["t3", "t2"]

    def test_check_task_by_ids_keeps_order_and_reports_per_entry_errors(self):
        check = self._exec_tools()["check_task"]["callable"]
        self._insert("mine")
        self._insert("foreign-user", user_id="9")
        self._insert("foreign-chat", chat_id="chat-2")

        out = asyncio.run(
            check(
                task_ids=[
                    "mine",
                    "no-such",
                    "foreign-user",
                    "foreign-chat",
                    "mine",  # 重複は dedupe
                ]
            )
        )
        assert [t["task_id"] for t in out["tasks"]] == [
            "mine",
            "no-such",
            "foreign-user",
            "foreign-chat",
        ]
        assert out["total"] == 4
        assert out["tasks"][0]["status"] == "running"
        # 不正 / 他人 / 他 chat は per-entry error(存在を漏らさない同一文言)
        for entry in out["tasks"][1:]:
            assert set(entry) == {"task_id", "error"}
            assert "task_id が不正です" in entry["error"]

        # 単一 string も受ける(coerce)
        out = asyncio.run(check(task_ids="mine"))
        assert out["tasks"][0]["task_id"] == "mine"

    def test_check_task_surfaces_latest_progress(self):
        release = asyncio.Event()

        async def probe():
            from nucllei.utils import tool_output

            tool_output.progress("解析中", current=3, total=10)
            await release.wait()
            return "ok"

        check = self._exec_tools()["check_task"]["callable"]

        async def main():
            spawned = await self._spawn(probe)
            task_id = spawned["task_id"]
            for _ in range(200):
                out = await check(task_ids=[task_id])
                if out["tasks"][0].get("progress"):
                    break
                await asyncio.sleep(0.01)
            release.set()
            await self._wait_registry_release(task_id)
            return out["tasks"][0]

        entry = asyncio.run(main())
        assert entry["status"] == "running"
        assert entry["progress"] == {"description": "解析中", "current": 3, "total": 10}

    # ---- await_task(K5 + 契約 T2 — 有界窓 1 回の複数待ち)----

    def test_await_task_rejects_empty_and_all_invalid(self):
        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("foreign-user", user_id="9")
        self._insert("foreign-chat", chat_id="chat-2")

        # 空・型不正・全不正はエラー文字列(_error 流儀)
        for task_ids in (None, [], "", 42):
            result = json.loads(asyncio.run(await_task(task_ids=task_ids)))
            assert result["ok"] is False, task_ids
        result = json.loads(
            asyncio.run(
                await_task(task_ids=["no-such", "foreign-user", "foreign-chat"])
            )
        )
        assert result["ok"] is False
        assert "有効な task_id がありません" in result["error"]

    def test_await_task_mixes_results_with_per_entry_errors(self):
        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("mine")
        assert self.Executions.finish_execution("mine", status="done", result="回答")

        out = asyncio.run(await_task(task_ids=["mine", "no-such"]))
        assert [t["task_id"] for t in out["tasks"]] == ["mine", "no-such"]
        assert out["tasks"][0]["ok"] is True
        assert out["tasks"][0]["result"] == "回答"
        assert "task_id が不正です" in out["tasks"][1]["error"]

    def test_await_task_returns_running_form_on_timeout_without_killing_exec(
        self, monkeypatch
    ):
        """K5: 有界待ちの時間切れは running 形(部分回収)。exec は走り続ける。"""
        release = asyncio.Event()
        finished = []

        async def probe():
            await release.wait()
            finished.append(True)
            return "終わった"

        # 待ち時間の clamp 下限(10 秒)をテスト用に外す
        monkeypatch.setattr(self.exec_tools, "AWAIT_TASK_MIN_TIMEOUT", 0)
        await_task = self._exec_tools()["await_task"]["callable"]

        async def main():
            spawned = await self._spawn(probe)
            task_id = spawned["task_id"]

            out = await await_task(task_ids=[task_id], timeout=0)
            entry = out["tasks"][0]
            assert entry["status"] == "running"
            assert entry["task_id"] == task_id
            assert isinstance(entry["started_at"], int)
            assert entry["elapsed_s"] >= 0
            assert "result" not in entry
            # 待ちを外しただけ — exec は生きている(D6)
            assert task_id in self.executions._exec_tasks
            assert (
                await asyncio.to_thread(self.Executions.get_execution_by_id, task_id)
            ).status == "running"
            # running 形で返した行は delivered を立てない(契約 M1)
            assert (
                await asyncio.to_thread(self.Executions.get_execution_by_id, task_id)
            ).delivered_at is None

            release.set()
            await self._wait_registry_release(task_id)
            # 完走後の再 await は結果を返す(回収は後追いでできる)
            return task_id, await await_task(task_ids=[task_id], timeout=0)

        task_id, out = asyncio.run(main())
        assert finished == [True]
        entry = out["tasks"][0]
        assert entry["ok"] is True
        assert entry["status"] == "done"
        assert entry["result"] == "終わった"
        # terminal 形で返した行は配達済み(サーバ駆動配達の重複防止)
        assert self._delivered_at(task_id) is not None

    def test_await_task_partial_recovery_across_multiple_ids(self, monkeypatch):
        """複数待ち(追補 2): 1 回の有界窓で全 id をカバーし、時間切れは
        terminal 分だけ結果・残りは running 形(部分回収)。"""
        release = asyncio.Event()

        async def fast():
            return "はやい"

        async def slow():
            await release.wait()
            return "おそい"

        monkeypatch.setattr(self.exec_tools, "AWAIT_TASK_MIN_TIMEOUT", 0)
        await_task = self._exec_tools()["await_task"]["callable"]

        async def main():
            fast_id = (await self._spawn(fast))["task_id"]
            await self._wait_registry_release(fast_id)
            slow_id = (await self._spawn(slow))["task_id"]

            out = await await_task(task_ids=[fast_id, slow_id], timeout=0)
            release.set()
            await self._wait_registry_release(slow_id)
            return fast_id, slow_id, out

        fast_id, slow_id, out = asyncio.run(main())
        assert [t["task_id"] for t in out["tasks"]] == [fast_id, slow_id]
        assert out["tasks"][0]["status"] == "done"
        assert out["tasks"][0]["result"] == "はやい"
        assert out["tasks"][1]["status"] == "running"
        # delivered は terminal 形で返した行だけ
        assert self._delivered_at(fast_id) is not None
        assert self._delivered_at(slow_id) is None

    def test_await_task_with_terminal_rows_skips_the_wait(self):
        """終了済みだけなら待ち集合が空 — wait せず即返(冪等)。"""
        await_task = self._exec_tools()["await_task"]["callable"]

        self._insert("failed-1")
        assert self.Executions.finish_execution(
            "failed-1", status="failed", error="壊れた"
        )
        self._insert("stopped-1")
        assert self.Executions.finish_execution(
            "stopped-1", status="stopped", error=self.executions.STOPPED_ERROR
        )
        self._insert("orphaned-1")
        assert self.Executions.finish_execution(
            "orphaned-1",
            status="orphaned",
            error=self.executions.ORPHANED_ON_BOOT_ERROR,
        )

        ids = ["failed-1", "stopped-1", "orphaned-1"]
        first = asyncio.run(await_task(task_ids=ids))
        second = asyncio.run(await_task(task_ids=ids))
        assert first == second  # 再 await は冪等
        for entry, status in zip(first["tasks"], ("failed", "stopped", "orphaned")):
            assert entry["ok"] is False
            assert entry["status"] == status
            assert entry["error"]
            # terminal 報告 = 配達済み
            assert self._delivered_at(entry["task_id"]) is not None

    def test_await_task_all_invalid_head_with_over_limit_stays_per_entry(self):
        """先頭 20 件が全不正でも 21 件目以降(over)がある場合は全体エラーに
        しない(敵対検証 5)— over に有効 id が残っているかもしれず、全体エラー
        だと per-entry の「次の呼び出しで指定」案内が届かない。"""
        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("valid-21")
        assert self.Executions.finish_execution("valid-21", status="done", result="ok")

        ids = [f"no-such-{i}" for i in range(20)] + ["valid-21"]
        out = asyncio.run(await_task(task_ids=ids))
        assert isinstance(out, dict)  # 全体エラー文字列(_error)ではない
        assert len(out["tasks"]) == 21
        for entry in out["tasks"][:20]:
            assert "task_id が不正です" in entry["error"]
        assert "20 件まで" in out["tasks"][20]["error"]
        # over の有効 id は今回処理されていない(次の呼び出しで回収できる)
        assert self._delivered_at("valid-21") is None

        # over が無い全不正は従来どおり全体エラー(既存挙動の保存)
        result = json.loads(asyncio.run(await_task(task_ids=["no-such-a"])))
        assert result["ok"] is False

    def test_await_task_caps_ids_at_20(self):
        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("e1")
        assert self.Executions.finish_execution("e1", status="done", result="ok")

        ids = ["e1"] + [f"x{i}" for i in range(21)]  # 22 件(dedupe 後)
        out = asyncio.run(await_task(task_ids=ids))
        assert len(out["tasks"]) == 22
        assert out["tasks"][0]["status"] == "done"
        # 21 件目以降(上限超過)は per-entry error で処理されない
        for entry in out["tasks"][20:]:
            assert "20 件まで" in entry["error"]

    def test_await_task_clamps_the_timeout(self, monkeypatch):
        """待ち時間は 10..300 に clamp、非 int は既定(90)へフォールバック。"""
        from nucllei import tasks as task_registry

        recorded = []

        async def recording_wait(aws, timeout=None):
            # 実際には待たずに時間切れを演じる
            recorded.append(timeout)
            return set(), set(aws)

        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("e1")

        async def main():
            release = asyncio.Event()

            async def stall():
                await release.wait()

            task_id, task = task_registry.create_task(stall(), id="exec:test")
            self.executions._exec_tasks["e1"] = task_id
            try:
                monkeypatch.setattr(asyncio, "wait", recording_wait)
                for timeout in (1, 9999, "壊れた", 120):
                    out = await await_task(task_ids=["e1"], timeout=timeout)
                    assert out["tasks"][0]["status"] == "running"
            finally:
                monkeypatch.undo()
                release.set()
                await task

        asyncio.run(main())
        assert recorded == [10, 300, self.exec_tools.AWAIT_TASK_DEFAULT_TIMEOUT, 120]

    def test_await_task_returns_json_results_as_objects(self):
        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("e1")
        assert self.Executions.finish_execution(
            "e1",
            status="done",
            result=json.dumps({"answer": "2"}, ensure_ascii=False),
            meta={"render": {"kind": "json"}},
            duration=1.5,
            usage={"prompt_tokens": 3},
        )

        out = asyncio.run(await_task(task_ids=["e1"]))
        entry = out["tasks"][0]
        assert entry["ok"] is True
        assert entry["result"] == {"answer": "2"}  # 二重エスケープを避ける
        assert entry["duration"] == 1.5
        assert entry["usage"] == {"prompt_tokens": 3}

    def test_await_task_replays_side_channels_once(self):
        from nucllei.utils import tool_output
        from nucllei.utils.tool_output import ToolOutputSink

        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("e1", tool_name="run_subagent")
        assert self.Executions.finish_execution(
            "e1",
            status="done",
            result="回答",
            meta={
                "render_descriptor": {"kind": "subagent"},
                "context_images": ["file-1"],
                "display": [{"kind": "markdown", "content": "表示"}],
            },
        )

        def collect():
            sink = ToolOutputSink(tool_call_id="functions.await_task:0")
            token = tool_output.current_sink.set(sink)
            try:
                out = asyncio.run(await_task(task_ids=["e1"]))
            finally:
                tool_output.current_sink.reset(token)
                sink.close()
            return out, sink

        first_out, first_sink = collect()
        assert first_out["tasks"][0]["ok"] is True
        assert first_sink.context_images() == ["file-1"]
        assert first_sink.displays() == [{"kind": "markdown", "content": "表示"}]
        # 実 tool の戻り値宣言は results[].render へ持ち上がる
        assert first_sink.result_render() == {"kind": "subagent"}
        assert self.Executions.get_execution_by_id("e1").meta["replayed_at"]

        # 2 回目: VLM 再注入・表示は繰り返さない(descriptor は毎回)
        second_out, second_sink = collect()
        assert second_out["tasks"][0]["result"] == "回答"
        assert second_sink.context_images() == []
        assert second_sink.displays() == []
        assert second_sink.result_render() == {"kind": "subagent"}

    def test_await_task_render_descriptor_only_for_a_single_requested_done(self):
        """契約 M1: render_descriptor の sink 上書きは「要求 id ちょうど 1 件 ∧
        done」のみ — 複数回収の envelope に単一 tool の descriptor を当てない。"""
        from nucllei.utils import tool_output
        from nucllei.utils.tool_output import ToolOutputSink

        await_task = self._exec_tools()["await_task"]["callable"]
        for exec_id in ("d1", "d2"):
            self._insert(exec_id, tool_name="run_subagent")
            assert self.Executions.finish_execution(
                exec_id,
                status="done",
                result="回答",
                meta={"render_descriptor": {"kind": "subagent"}},
            )

        def collect(ids):
            sink = ToolOutputSink(tool_call_id="functions.await_task:0")
            token = tool_output.current_sink.set(sink)
            try:
                asyncio.run(await_task(task_ids=ids))
            finally:
                tool_output.current_sink.reset(token)
                sink.close()
            return sink

        # 2 件要求 → descriptor は適用しない
        assert collect(["d1", "d2"]).result_render() is None
        # 1 件要求 ∧ done → 適用
        assert collect(["d1"]).result_render() == {"kind": "subagent"}

    def test_await_task_orphans_a_row_whose_task_is_gone(self):
        """running なのに registry に居ない = 失われた実行(liveness ガード §4)。"""
        await_task = self._exec_tools()["await_task"]["callable"]
        self._insert("e1")
        self._age("e1", self.executions.EXEC_SPAWN_GRACE_S + 5)

        out = asyncio.run(await_task(task_ids=["e1"]))
        entry = out["tasks"][0]
        assert entry["ok"] is False
        assert entry["status"] == "orphaned"
        assert entry["error"] == self.executions.ORPHANED_ON_PROBE_ERROR
        assert self.Executions.get_execution_by_id("e1").status == "orphaned"
        assert self._delivered_at("e1") is not None

    def test_await_task_reports_stop_that_lands_during_the_wait(self, monkeypatch):
        """stop 発の cancel は asyncio.wait の done に畳まれ、全行再読で
        stopped を確定する(親ターンの stop と誤認しない)。"""
        started = asyncio.Event()

        async def probe():
            started.set()
            await asyncio.sleep(30)

        monkeypatch.setattr(self.exec_tools, "AWAIT_TASK_MIN_TIMEOUT", 0)
        await_task = self._exec_tools()["await_task"]["callable"]

        async def main():
            spawned = await self._spawn(probe)
            task_id = spawned["task_id"]
            await asyncio.wait_for(started.wait(), timeout=5)

            waiter = asyncio.create_task(await_task(task_ids=[task_id], timeout=30))
            await asyncio.sleep(0)
            await self.executions.stop_execution(task_id)
            return await asyncio.wait_for(waiter, timeout=10)

        out = asyncio.run(main())
        entry = out["tasks"][0]
        assert entry["ok"] is False
        assert entry["status"] == "stopped"
        assert entry["error"] == self.executions.STOPPED_ERROR

    def test_await_task_result_render_reaches_the_tool_call_result(self):
        """§8: exec 行の render_descriptor が results[].render を上書きする。

        併せて FE との契約を固定する: 回収形は封筒 {"tasks":[...]} で、per-entry は
        {ok, task_id, tool_name, status, result, ...}。**descriptor が指すのは
        封筒ではなく result の中身**。FE(tool-render の封筒剥がし)は tasks 配列 ∧
        task_id で封筒と判定して剥がし、中身へ descriptor を適用する —
        この形を変えるなら FE も直すこと。
        """
        from nucllei.utils.middleware import execute_tool_calls

        self._insert("e1", tool_name="run_subagent")
        assert self.Executions.finish_execution(
            "e1",
            status="done",
            result=json.dumps(
                {"ok": True, "session_id": "c1", "answer": "2"}, ensure_ascii=False
            ),
            meta={
                "render": {"kind": "json"},
                "render_descriptor": {"kind": "subagent"},
            },
        )

        tools = self._exec_tools()
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"task_ids": ["e1"]}, name="await_task")],
                tools,
                metadata=self._metadata(),
            )
        )
        # 型由来の render(kind=json)ではなく実 tool の宣言が勝つ
        assert results[0]["render"] == {"kind": "subagent"}
        payload = json.loads(results[0]["content"])
        # 封筒の外形(FE の剥がし条件)
        entry = payload["tasks"][0]
        assert entry["task_id"] == "e1"
        assert entry["status"] == "done"
        # 中身は実 tool の生 object のまま(二重エスケープしない)= descriptor の対象
        assert entry["result"] == {"ok": True, "session_id": "c1", "answer": "2"}

    # ---- stop_task(契約 T2 — 強制終了・冪等)----

    def test_stop_task_stops_running_and_reports_terminal_state(self):
        started = asyncio.Event()

        async def probe():
            started.set()
            await asyncio.sleep(30)

        stop_task = self._exec_tools()["stop_task"]["callable"]

        async def main():
            spawned = await self._spawn(probe)
            task_id = spawned["task_id"]
            await asyncio.wait_for(started.wait(), timeout=5)

            out = await stop_task(task_ids=[task_id])
            assert out["tasks"] == [
                {"task_id": task_id, "tool_name": "probe", "status": "stopped"}
            ]
            await self._wait_registry_release(task_id)

            # 冪等(2 回目も現状を返すだけ)
            again = await stop_task(task_ids=[task_id])
            assert again["tasks"][0]["status"] == "stopped"
            return task_id

        task_id = asyncio.run(main())
        row = self.Executions.get_execution_by_id(task_id)
        assert row.status == "stopped"
        # 報告した terminal 行は配達済み(幽霊ターン防止 — 契約 M1)
        assert row.delivered_at is not None

    def test_stop_task_on_terminal_rows_and_invalid_ids(self):
        stop_task = self._exec_tools()["stop_task"]["callable"]
        self._insert("done-1")
        assert self.Executions.finish_execution("done-1", status="done", result="ok")
        self._insert("foreign", user_id="9")

        # 空はエラー文字列
        assert json.loads(asyncio.run(stop_task(task_ids=[])))["ok"] is False

        out = asyncio.run(stop_task(task_ids=["done-1", "no-such", "foreign"]))
        assert out["tasks"][0] == {
            "task_id": "done-1",
            "tool_name": "probe",
            "status": "done",  # 終了済みに撃っても現状を返すだけ(冪等)
        }
        for entry in out["tasks"][1:]:
            assert "task_id が不正です" in entry["error"]
        # done のまま(stop で上書きしない)+ 報告済みなので delivered
        row = self.Executions.get_execution_by_id("done-1")
        assert row.status == "done"
        assert row.result == "ok"
        assert row.delivered_at is not None

    # ---- A-3(遅延ロードでの回収 builtin 追加 mount)----

    def test_inject_skill_tools_mounts_recall_builtins_for_eligible_tools(self):
        """A-3: 初回合成時に適格 tool が無くても、load_skill の遅延ロードで適格
        tool が載ったら回収 builtin を追加 mount し、spec も form_data["tools"] へ
        append する(次イテレーションからモデルに見える)。"""

        async def probe(city: str = ""):
            return "ok"

        eligible_entry = self._entry(probe, properties={"city": {"type": "string"}})

        # inject_skill_tools と同じ機序を、実装が使う部品(get_tools 相当の結果 +
        # mount_recall_tools)で再現する — closure 本体は process_chat_payload に
        # 閉じているため、A-3 の判定・配達を担う共有ヘルパを直接検証する。
        metadata = self._metadata()
        tools_dict = {}
        form_data = {"tools": []}

        # 適格 tool が載っていなければ mount しない(初回合成の判定と同じ)
        loaded = []
        if any(
            self.exec_tools.entry_is_backgroundable(name, tools_dict[name])
            for name in loaded
        ):
            self.exec_tools.mount_recall_tools(tools_dict, form_data, self.user, metadata)
        assert "check_task" not in tools_dict

        # 遅延ロードで適格 tool が載った → 回収 builtin を追加 mount
        tools_dict["probe"] = eligible_entry
        form_data["tools"].append(
            {
                "type": "function",
                "function": self.exec_tools.spec_for_model(
                    "probe", eligible_entry, self.exec_tools.exec_spawn_context_ok(metadata)
                ),
            }
        )
        loaded = ["probe"]
        if self.exec_tools.exec_recall_context_ok(metadata) and any(
            self.exec_tools.entry_is_backgroundable(name, tools_dict[name])
            for name in loaded
        ):
            self.exec_tools.mount_recall_tools(tools_dict, form_data, self.user, metadata)

        assert {"check_task", "await_task", "stop_task"} <= set(tools_dict)
        spec_names = [t["function"]["name"] for t in form_data["tools"]]
        assert spec_names == ["probe", "check_task", "await_task", "stop_task"]
        # probe の spec には background が注入されている(第 2 経路)
        assert "background" in form_data["tools"][0]["function"]["parameters"]["properties"]

    def test_inject_skill_tools_source_wires_a3(self):
        """A-3 の配線が inject_skill_tools 実体に存在することを固定する
        (closure のため直接呼べない — ソース契約として検証)。"""
        import inspect

        from nucllei.utils import middleware as mw

        source = inspect.getsource(mw.process_chat_payload)
        # 遅延ロード経路に回収 builtin の追加 mount がある
        assert "mount_recall_tools(tools_dict, form_data, user, metadata)" in source
        assert "exec_recall_context_ok(metadata)" in source
