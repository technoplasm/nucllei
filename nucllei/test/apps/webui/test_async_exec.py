"""非同期 tool 実行の呼び出し面(issue #110 §7/§8)の回帰。

_memos/async-exec-design.md の K2(tool 単位の静的適格性 `backgroundable` +
呼び出し時の共有 `background` 引数)を固定する:

- spec 注入は適格 entry の **コピー**にのみ(tools_dict 側 = unknown-drop の基準は不変)。
  恒久不適格名 / 自前 `background` 宣言 / agent session(D9)・chat 文脈なしでは注入しない
- execute_tool_calls の分岐: `background` は allowed_params フィルタより **前に pop**
  (callable へ渡さない)。適格なら spawn(running 形 K3)、不適格なら無視して同期実行
- runner の終端書き込み(done / failed / timeout)と切詰め・meta harvest
- check_async(非ブロッキング poll)/ await_async(有界待ち K5 — 時間切れは running 形で
  exec を殺さない)・認可述語・冪等・replay 一度きり・render_descriptor パススルー

エンティティと状態機械(CAS / boot スイープ / liveness / cascade)は test_executions.py。
"""

import asyncio
import json

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

    def _exec_tools(self, metadata=None):
        return self.exec_tools.build_exec_tools(
            self.user, self._metadata() if metadata is None else metadata
        )

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

        # 恒久不適格名(D4)
        for name in ("load_skill", "check_async", "await_async"):
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

    def test_exec_builtins_are_mounted_with_disabled_outer_timeout(self):
        tools = self._exec_tools()
        assert set(tools) == {"check_async", "await_async"}
        # 自前の clamp 済み有界待ちが running 形を返すので外側 wait_for は無効化
        assert tools["await_async"]["timeout"] == 0
        assert "timeout" not in tools["check_async"]
        # 回収 builtin 自身は昇格対象にしない(恒久不適格 — 適格宣言も持たない)
        for name, entry in tools.items():
            assert self.exec_tools.entry_is_backgroundable(name, entry) is False

    def test_mount_predicate_ignores_purged_rows(self):
        import time

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
            # 親メッセージの tool result は running 形のみ(K3・append-only)
            payload = json.loads(results[0]["content"])
            assert payload["ok"] is True
            assert payload["status"] == "running"
            assert payload["tool_name"] == "probe"
            exec_id = payload["exec_id"]

            await asyncio.wait_for(started.wait(), timeout=5)
            # background / unknown は callable に渡らない(pop → unknown-drop)
            assert captured == ["札幌"]

            row = await asyncio.to_thread(self.Executions.get_execution_by_id, exec_id)
            assert row.status == "running"
            assert row.chat_id == self.CHAT_ID
            assert row.message_id == self.MESSAGE_ID
            assert row.tool_call_id == "functions.probe:0"
            assert row.args == {"city": "札幌"}  # 監査は unknown-drop 後の実引数

            release.set()
            await self._wait_registry_release(exec_id)
            return exec_id

        exec_id = asyncio.run(main())
        row = self.Executions.get_execution_by_id(exec_id)
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
            await self._wait_registry_release(failed["exec_id"])

            timed_out = await self._spawn(slow, entry=self._entry(slow, timeout=0.05))
            await self._wait_registry_release(timed_out["exec_id"])
            return failed["exec_id"], timed_out["exec_id"]

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

        monkeypatch.setattr(self.executions, "EXEC_RESULT_MAX_CHARS", 40)

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
            exec_id = spawned["exec_id"]
            # 走行中は sink 経由で最新 progress 1 件が読める(check_async の源)
            try:
                for _ in range(200):
                    progress = self.executions.get_latest_progress(exec_id)
                    if progress:
                        assert progress["description"] == "進行中"
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("progress was never observed")
            finally:
                observed.set()  # 失敗時も probe を解放してタスクを残さない
            await self._wait_registry_release(exec_id)
            return exec_id

        exec_id = asyncio.run(main())
        row = self.Executions.get_execution_by_id(exec_id)
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
        assert self.executions.get_latest_progress(exec_id) is None

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

    # ---- check_async(K4)----

    def test_check_async_authorization(self):
        check = self._exec_tools()["check_async"]["callable"]
        self._insert("mine")
        self._insert("foreign-user", user_id="9")
        self._insert("foreign-chat", chat_id="chat-2")

        for exec_id in ("", "no-such", "foreign-user", "foreign-chat"):
            result = json.loads(asyncio.run(check(exec_id=exec_id)))
            assert result["ok"] is False, exec_id

        assert asyncio.run(check(exec_id="mine"))["status"] == "running"

    def test_check_async_reports_status_without_result(self):
        check = self._exec_tools()["check_async"]["callable"]
        self._insert("e1")

        running = asyncio.run(check(exec_id="e1"))
        assert running["exec_id"] == "e1"
        assert running["tool_name"] == "probe"
        assert running["status"] == "running"
        assert running["elapsed_s"] >= 0
        assert "progress" not in running  # registry に sink が無ければ出ない

        assert self.Executions.finish_execution("e1", status="done", result="秘密")
        done = asyncio.run(check(exec_id="e1"))
        assert done["status"] == "done"
        assert "result" not in done  # 回収は await_async に一本化
        assert "await_async" in done["hint"]

    def test_check_async_surfaces_latest_progress(self):
        release = asyncio.Event()

        async def probe():
            from nucllei.utils import tool_output

            tool_output.progress("解析中", current=3, total=10)
            await release.wait()
            return "ok"

        check = self._exec_tools()["check_async"]["callable"]

        async def main():
            spawned = await self._spawn(probe)
            exec_id = spawned["exec_id"]
            for _ in range(200):
                out = await check(exec_id=exec_id)
                if out.get("progress"):
                    break
                await asyncio.sleep(0.01)
            release.set()
            await self._wait_registry_release(exec_id)
            return out

        out = asyncio.run(main())
        assert out["status"] == "running"
        assert out["progress"] == {"description": "解析中", "current": 3, "total": 10}

    # ---- await_async(K5)----

    def test_await_async_authorization(self):
        await_async = self._exec_tools()["await_async"]["callable"]
        self._insert("foreign-user", user_id="9")
        self._insert("foreign-chat", chat_id="chat-2")

        assert json.loads(asyncio.run(await_async(exec_id="")))["ok"] is False
        for exec_id in ("no-such", "foreign-user", "foreign-chat"):
            result = json.loads(asyncio.run(await_async(exec_id=exec_id)))
            assert result["ok"] is False, exec_id
            # 他人・他 chat の行は「存在しない」と同じ応答(存在を漏らさない)
            assert "exec_id が不正です" in result["error"]

    def test_await_async_returns_running_form_on_timeout_without_killing_exec(
        self, monkeypatch
    ):
        """K5: 有界待ちの時間切れは running 形。shield なので exec は走り続ける。"""
        release = asyncio.Event()
        finished = []

        async def probe():
            await release.wait()
            finished.append(True)
            return "終わった"

        # 待ち時間の clamp 下限(10 秒)をテスト用に外す
        monkeypatch.setattr(self.exec_tools, "AWAIT_ASYNC_MIN_TIMEOUT", 0)
        await_async = self._exec_tools()["await_async"]["callable"]

        async def main():
            spawned = await self._spawn(probe)
            exec_id = spawned["exec_id"]

            out = await await_async(exec_id=exec_id, timeout=0)
            assert out["status"] == "running"
            assert out["exec_id"] == exec_id
            assert out["elapsed_s"] >= 0
            assert "result" not in out
            # 待ちを外しただけ — exec は生きている(D6)
            assert exec_id in self.executions._exec_tasks
            assert (
                await asyncio.to_thread(self.Executions.get_execution_by_id, exec_id)
            ).status == "running"

            release.set()
            await self._wait_registry_release(exec_id)
            # 完走後の再 await は結果を返す(回収は後追いでできる)
            return await await_async(exec_id=exec_id, timeout=0)

        out = asyncio.run(main())
        assert finished == [True]
        assert out["ok"] is True
        assert out["status"] == "done"
        assert out["result"] == "終わった"

    def test_await_async_clamps_the_timeout(self, monkeypatch):
        """待ち時間は 10..300 に clamp、非 int は既定(90)へフォールバック。"""
        from nucllei import tasks as task_registry

        recorded = []

        async def recording_wait_for(awaitable, timeout):
            # 実際には待たずに時間切れを演じる(shield の外側だけを畳む)
            recorded.append(timeout)
            if isinstance(awaitable, asyncio.Future):
                awaitable.cancel()
            raise asyncio.TimeoutError

        await_async = self._exec_tools()["await_async"]["callable"]
        self._insert("e1")

        async def main():
            release = asyncio.Event()

            async def stall():
                await release.wait()

            task_id, task = task_registry.create_task(stall(), id="exec:test")
            self.executions._exec_tasks["e1"] = task_id
            try:
                monkeypatch.setattr(asyncio, "wait_for", recording_wait_for)
                for timeout in (1, 9999, "壊れた", 120):
                    out = await await_async(exec_id="e1", timeout=timeout)
                    assert out["status"] == "running"
            finally:
                monkeypatch.undo()
                release.set()
                await task

        asyncio.run(main())
        assert recorded == [10, 300, self.exec_tools.AWAIT_ASYNC_DEFAULT_TIMEOUT, 120]

    def test_await_async_returns_terminal_forms_idempotently(self):
        await_async = self._exec_tools()["await_async"]["callable"]

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

        for exec_id, status in (
            ("failed-1", "failed"),
            ("stopped-1", "stopped"),
            ("orphaned-1", "orphaned"),
        ):
            first = asyncio.run(await_async(exec_id=exec_id))
            second = asyncio.run(await_async(exec_id=exec_id))
            assert first == second  # 再 await は冪等
            assert first["ok"] is False
            assert first["status"] == status
            assert first["error"]

    def test_await_async_returns_json_results_as_objects(self):
        await_async = self._exec_tools()["await_async"]["callable"]
        self._insert("e1")
        assert self.Executions.finish_execution(
            "e1",
            status="done",
            result=json.dumps({"answer": "2"}, ensure_ascii=False),
            meta={"render": {"kind": "json"}},
            duration=1.5,
            usage={"prompt_tokens": 3},
        )

        out = asyncio.run(await_async(exec_id="e1"))
        assert out["ok"] is True
        assert out["result"] == {"answer": "2"}  # 二重エスケープを避ける
        assert out["duration"] == 1.5
        assert out["usage"] == {"prompt_tokens": 3}

    def test_await_async_replays_side_channels_once(self):
        from nucllei.utils import tool_output
        from nucllei.utils.tool_output import ToolOutputSink

        await_async = self._exec_tools()["await_async"]["callable"]
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
            sink = ToolOutputSink(tool_call_id="functions.await_async:0")
            token = tool_output.current_sink.set(sink)
            try:
                out = asyncio.run(await_async(exec_id="e1"))
            finally:
                tool_output.current_sink.reset(token)
                sink.close()
            return out, sink

        first_out, first_sink = collect()
        assert first_out["ok"] is True
        assert first_sink.context_images() == ["file-1"]
        assert first_sink.displays() == [{"kind": "markdown", "content": "表示"}]
        # 実 tool の戻り値宣言は results[].render へ持ち上がる
        assert first_sink.result_render() == {"kind": "subagent"}
        assert self.Executions.get_execution_by_id("e1").meta["replayed_at"]

        # 2 回目: VLM 再注入・表示は繰り返さない(descriptor は毎回)
        second_out, second_sink = collect()
        assert second_out["result"] == "回答"
        assert second_sink.context_images() == []
        assert second_sink.displays() == []
        assert second_sink.result_render() == {"kind": "subagent"}

    def test_await_async_orphans_a_row_whose_task_is_gone(self):
        """running なのに registry に居ない = 失われた実行(liveness ガード §4)。"""
        from nucllei.internal.db import get_db
        from nucllei.models.executions import Execution
        import time

        await_async = self._exec_tools()["await_async"]["callable"]
        self._insert("e1")
        with get_db() as db:
            db.query(Execution).filter_by(id="e1").update(
                {
                    "created_at": int(time.time())
                    - self.executions.EXEC_SPAWN_GRACE_S
                    - 5
                }
            )
            db.commit()

        out = asyncio.run(await_async(exec_id="e1"))
        assert out["ok"] is False
        assert out["status"] == "orphaned"
        assert out["error"] == self.executions.ORPHANED_ON_PROBE_ERROR
        assert self.Executions.get_execution_by_id("e1").status == "orphaned"

    def test_await_async_reports_stop_that_lands_during_the_wait(self, monkeypatch):
        """stop API 発の cancel は「親ターンの stop」と誤認せず status を返す。"""
        started = asyncio.Event()

        async def probe():
            started.set()
            await asyncio.sleep(30)

        monkeypatch.setattr(self.exec_tools, "AWAIT_ASYNC_MIN_TIMEOUT", 0)
        await_async = self._exec_tools()["await_async"]["callable"]

        async def main():
            spawned = await self._spawn(probe)
            exec_id = spawned["exec_id"]
            await asyncio.wait_for(started.wait(), timeout=5)

            waiter = asyncio.create_task(await_async(exec_id=exec_id, timeout=30))
            await asyncio.sleep(0)
            await self.executions.stop_execution(exec_id)
            return await asyncio.wait_for(waiter, timeout=10)

        out = asyncio.run(main())
        assert out["ok"] is False
        assert out["status"] == "stopped"
        assert out["error"] == self.executions.STOPPED_ERROR

    def test_await_async_result_render_reaches_the_tool_call_result(self):
        """§8: exec 行の render_descriptor が results[].render を上書きする。"""
        from nucllei.utils.middleware import execute_tool_calls

        self._insert("e1", tool_name="run_subagent")
        assert self.Executions.finish_execution(
            "e1",
            status="done",
            result=json.dumps({"answer": "2"}, ensure_ascii=False),
            meta={
                "render": {"kind": "json"},
                "render_descriptor": {"kind": "subagent"},
            },
        )

        tools = self._exec_tools()
        results = asyncio.run(
            execute_tool_calls(
                [self._call({"exec_id": "e1"}, name="await_async")],
                tools,
                metadata=self._metadata(),
            )
        )
        # 型由来の render(kind=json)ではなく実 tool の宣言が勝つ
        assert results[0]["render"] == {"kind": "subagent"}
        assert json.loads(results[0]["content"])["result"] == {"answer": "2"}
