"""SessionManager のリセット通知と lock 待ち中の client 切断検知のユニットテスト。

test_isolation.py (稼働コンテナ必須の統合テスト) と違い、main.py を直接 import して
kernel 起動を偽物に差し替える。追加依存: fastapi / pydantic (main.py の import に要る)。

実行:
    uv run --with pytest --with fastapi python -m pytest -q sandbox/tests/test_session_manager.py
"""

import asyncio
import importlib.util
import pathlib
import sys
import types

import pytest

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _load_main(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path))
    spec = importlib.util.spec_from_file_location("sandbox_main_under_test", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    class FakeProc:
        def __init__(self):
            self._rc = None
            self.stdin = types.SimpleNamespace(write=lambda b: None, flush=lambda: None)
            self.stdout = None

        def poll(self):
            return self._rc

        def kill(self):
            self._rc = -9

    async def fake_workspace(user_id):
        return tmp_path / user_id

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    monkeypatch.setattr(mod, "_kernel_nsjail_cmd", lambda ws, binds=(): ["true"])
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: FakeProc())
    return mod


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_first_get_is_not_reset(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)
    mgr = mod.SessionManager()
    _, was_reset = _run(mgr.get("u", "c"))
    assert was_reset is False


def test_reaper_ttl_expiry_is_reported_as_reset(monkeypatch, tmp_path):
    # 12h 満了 (reaper が dict から消す) の後の最初のセルで session_reset=True が返ること。
    mod = _load_main(monkeypatch, tmp_path)
    mgr = mod.SessionManager()

    async def scenario():
        sess, _ = await mgr.get("u", "c")
        sess.last_used -= mod.SESSION_TTL_S + 1
        assert await mgr.reap_idle() == 1
        _, was_reset = await mgr.get("u", "c")
        assert was_reset is True
        # 印は 1 回で消費される
        _, again = await mgr.get("u", "c")
        assert again is False

    _run(scenario())


def test_dead_kernel_reaped_is_reported_as_reset(monkeypatch, tmp_path):
    # kernel が time_limit で死んだ (poll() != None) のを reaper が拾った場合も同じ。
    mod = _load_main(monkeypatch, tmp_path)
    mgr = mod.SessionManager()

    async def scenario():
        sess, _ = await mgr.get("u", "c")
        sess.proc._rc = 0
        assert await mgr.reap_idle() == 1
        _, was_reset = await mgr.get("u", "c")
        assert was_reset is True

    _run(scenario())


def test_session_max_eviction_is_reported_as_reset(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "SESSION_MAX", 1)
    mgr = mod.SessionManager()

    async def scenario():
        await mgr.get("u", "c1")
        await mgr.get("u", "c2")  # c1 が追い出される
        _, was_reset = await mgr.get("u", "c1")
        assert was_reset is True

    _run(scenario())


def test_drop_does_not_double_report(monkeypatch, tmp_path):
    # drop() の呼び元は自分で session_reset を返すので、次の get は「初回」扱い。
    mod = _load_main(monkeypatch, tmp_path)
    mgr = mod.SessionManager()

    async def scenario():
        sess, _ = await mgr.get("u", "c")
        await mgr.drop(sess)
        _, was_reset = await mgr.get("u", "c")
        assert was_reset is False

    _run(scenario())


def test_abandon_when_client_disconnected(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)

    class Req:
        def __init__(self, gone):
            self._gone = gone

        async def is_disconnected(self):
            return self._gone

    _run(mod._abandon_if_client_gone(Req(False)))
    with pytest.raises(mod.HTTPException) as ei:
        _run(mod._abandon_if_client_gone(Req(True)))
    assert ei.value.status_code == 499
