"""jail ごとのプロセス数上限 (pids cgroup、`SANDBOX_JAIL_PIDS_MAX`) の回帰。

2026-10-20 まで全 jail に `--rlimit_nproc 128` を掛けていた。RLIMIT_NPROC は実 uid 単位で数える
(user namespace を作らないので全 jail・ホスト全体の uid 65534 で 1 つの枠) ため、常駐 kernel が
~18 個生きると全 chat・全ユーザーの次の fork が `Resource temporarily unavailable` で落ちた
(staging)。今は jail ごとの pids cgroup で掛ける。ここで固定するのは:

1. nsjail の引数 — `--use_cgroupv2` `--cgroupv2_mount <jails>` `--cgroup_pids_max N`、共有の
   RLIMIT_NPROC は掛けない (`inf`)。stateless exec と常駐 kernel の両方
2. 起動時の cgroup の組み替え (`_setup_jail_cgroup`) — root のプロセスを service/ へ移してから
   `+pids`、jails/ にも `+pids`。自分の物でない階層 (namespace の root ではない) には触らない
3. fail-closed — 用意できなければ jail を起こさない (`/exec` `/exec_cell` は 503、`/healthz` も 503)
4. nsjail が消し損ねた `NSJAIL.<pid>` の掃除

cgroupfs そのものの挙動 (no internal processes の EBUSY・pids.max で fork が止まる) は
ここでは再現できないので、実コンテナで `test_isolation.py` が見る。ここは tmp のディレクトリを
cgroup に見立てて「何をどこに書くか」を見る。

実行:
    uv run task sandbox-unit
"""

import asyncio
import errno
import importlib.util
import os
import pathlib
import sys
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _load(monkeypatch, tmp_path, pids_max=None):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    if pids_max is None:
        monkeypatch.delenv("SANDBOX_JAIL_PIDS_MAX", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_JAIL_PIDS_MAX", str(pids_max))
    spec = importlib.util.spec_from_file_location("sandbox_main_jail_cgroup", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def _fake_root(tmp_path, controllers="cpu io memory pids", procs="1\n7\n"):
    """cgroup v2 の root に見立てたディレクトリ。"""
    root = tmp_path / "cg"
    root.mkdir()
    (root / "cgroup.controllers").write_text(controllers + "\n")
    (root / "cgroup.subtree_control").write_text("")
    (root / "cgroup.procs").write_text(procs)
    return root


# ---------------------------------------------------------------------------
# 1. nsjail の引数
# ---------------------------------------------------------------------------
def test_jail_args_use_per_jail_pids_cgroup_not_shared_rlimit(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    assert mod.JAIL_PIDS_MAX == 128  # 既定 = 旧来の共有 128 を jail ごとに
    cmd = mod._jail_base(pathlib.Path("/workspaces/u"), 60)
    assert "--use_cgroupv2" in cmd
    assert _arg(cmd, "--cgroupv2_mount") == "/sys/fs/cgroup/jails"
    assert _arg(cmd, "--cgroup_pids_max") == "128"
    # 実 uid 単位の共有の枠は掛けない (nsjail の既定 = サービスの soft 値の継承にも任せない)
    assert _arg(cmd, "--rlimit_nproc") == "inf"


def test_pids_max_is_a_knob(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path, pids_max=300)
    cmd = mod._jail_base(pathlib.Path("/workspaces/u"), 60)
    assert _arg(cmd, "--cgroup_pids_max") == "300"


def test_both_stateless_and_kernel_commands_carry_the_limit(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_jail_cgroup_error", None)
    ws = pathlib.Path("/workspaces/u")
    for cmd in (mod._nsjail_cmd(ws, 30, ["true"]), mod._kernel_nsjail_cmd(ws)):
        assert _arg(cmd, "--cgroup_pids_max") == "128"
        assert _arg(cmd, "--rlimit_nproc") == "inf"


# ---------------------------------------------------------------------------
# 3. fail-closed
# ---------------------------------------------------------------------------
def test_no_jail_without_cgroup(monkeypatch, tmp_path):
    """起動時の設定に失敗していれば、jail のコマンド自体を組まない (どの経路から呼ばれても)。"""
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_jail_cgroup_error", "RuntimeError: boom")
    ws = pathlib.Path("/workspaces/u")
    for build in (
        lambda: mod._nsjail_cmd(ws, 30, ["true"]),
        lambda: mod._kernel_nsjail_cmd(ws),
    ):
        with pytest.raises(HTTPException) as e:
            build()
        assert e.value.status_code == 503
        assert "boom" in e.value.detail


def test_exec_endpoints_are_503_and_healthz_says_why(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_jail_cgroup_error", "RuntimeError: boom")
    monkeypatch.setattr(mod, "_loop_available", lambda: True)

    async def fake_workspace(user_id):  # 実体 (loopback mount) は作らない
        ws = tmp_path / user_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    c = TestClient(mod.app)
    r = c.post("/exec", json={"user_id": "u1", "lang": "shell", "code": "true"})
    assert r.status_code == 503 and "boom" in r.json()["detail"]
    r = c.post("/exec_cell", json={"user_id": "u1", "chat_id": "c1", "code": "1"})
    assert r.status_code == 503 and "boom" in r.json()["detail"]
    assert mod._sessions._sessions == {}  # kernel は起こしていない

    r = c.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False and body["loop"] is True
    assert body["jail_cgroup"] is False and "boom" in body["jail_cgroup_error"]
    assert body["jail_pids_max"] == 128

    monkeypatch.setattr(mod, "_jail_cgroup_error", None)
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["jail_cgroup"] is True and "jail_cgroup_error" not in r.json()


def test_startup_failure_is_logged_and_kept(monkeypatch, tmp_path, capsys):
    mod = _load(monkeypatch, tmp_path)

    def boom():
        raise RuntimeError("no pids")

    monkeypatch.setattr(mod, "_setup_jail_cgroup", boom)
    mod._init_jail_cgroup()
    assert mod._jail_cgroup_error == "RuntimeError: no pids"
    out = capsys.readouterr().out
    assert "ERROR" in out and "503" in out

    monkeypatch.setattr(mod, "_setup_jail_cgroup", lambda: None)
    mod._init_jail_cgroup()
    assert mod._jail_cgroup_error is None
    assert "jail ごとに 128" in capsys.readouterr().out


def test_unstarted_service_does_not_run_jails(monkeypatch, tmp_path):
    """import しただけ (= _startup 前) の状態では使えない扱い。"""
    mod = _load(monkeypatch, tmp_path)
    assert mod._jail_cgroup_error is not None


# ---------------------------------------------------------------------------
# 2. 起動時の組み替え
# ---------------------------------------------------------------------------
def test_setup_moves_processes_to_leaf_and_enables_pids(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    root = _fake_root(tmp_path)
    moved = []
    real_write = pathlib.Path.write_text

    def spy(self, data, *a, **k):
        if self == root / "service" / "cgroup.procs":
            moved.append(data)
        return real_write(self, data, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", spy)
    mod._setup_jail_cgroup(root, own="/")
    # 1 pid ずつ書く (cgroup.procs は 1 回の write で 1 pid)
    assert moved == ["1", "7"]
    assert (root / "cgroup.subtree_control").read_text() == "+pids"
    assert (root / "jails" / "cgroup.subtree_control").read_text() == "+pids"


def test_setup_is_idempotent_after_service_restart(monkeypatch, tmp_path):
    """サービスだけ再起動した (既に service/ に居る) 場合もそのまま通る。"""
    mod = _load(monkeypatch, tmp_path)
    root = _fake_root(tmp_path, procs="")
    mod._setup_jail_cgroup(root, own="/")
    mod._setup_jail_cgroup(root, own="/service")
    assert (root / "jails" / "cgroup.subtree_control").read_text() == "+pids"


def test_setup_retries_when_a_process_slips_in(monkeypatch, tmp_path):
    """移している間に root へ入ってきたプロセス (docker exec 等) で EBUSY → 移し直してやり直す。"""
    mod = _load(monkeypatch, tmp_path)
    root = _fake_root(tmp_path)
    calls = []
    real = mod._enable_pids

    def flaky(cg):
        calls.append(cg)
        if len(calls) == 1:
            raise OSError(errno.EBUSY, "busy")
        real(cg)

    monkeypatch.setattr(mod, "_enable_pids", flaky)
    mod._setup_jail_cgroup(root, own="/")
    assert calls == [root, root, root / "jails"]


def test_setup_refuses_hierarchy_it_does_not_own(monkeypatch, tmp_path):
    """namespace の root に居ない (cgroupns=host・ホストでのテスト実行) なら何も書かない。"""
    mod = _load(monkeypatch, tmp_path)
    root = _fake_root(tmp_path)
    with pytest.raises(RuntimeError, match="cgroupns=private"):
        mod._setup_jail_cgroup(root, own="/user.slice/user-1002.slice/x.scope")
    assert not (root / "service").exists() and not (root / "jails").exists()
    assert (root / "cgroup.subtree_control").read_text() == ""


def test_setup_refuses_without_pids_controller(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    root = _fake_root(tmp_path, controllers="cpu io memory")
    with pytest.raises(RuntimeError, match="pids"):
        mod._setup_jail_cgroup(root, own="/")


@pytest.mark.parametrize("bad", [0, -1])
def test_setup_refuses_unlimited(monkeypatch, tmp_path, bad):
    """0 は nsjail では「上限なし」。上限なしの jail は作らない。"""
    mod = _load(monkeypatch, tmp_path, pids_max=bad)
    with pytest.raises(RuntimeError, match="SANDBOX_JAIL_PIDS_MAX"):
        mod._setup_jail_cgroup(_fake_root(tmp_path), own="/")


def test_startup_wires_setup(monkeypatch, tmp_path):
    """`_startup` が組み替えを呼ぶ (ここで差し替えないとホストの cgroup に触りうる)。"""
    mod = _load(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(mod, "_setup_jail_cgroup", lambda: seen.append(1))
    monkeypatch.setattr(mod, "_ensure_loop_nodes", lambda: None)
    monkeypatch.setattr(mod, "_loop_available", lambda: True)
    monkeypatch.setattr(mod, "WS_RETENTION_DAYS", 0)
    monkeypatch.setattr(mod.asyncio, "create_task", lambda coro: coro.close())
    asyncio.run(mod._startup())
    assert seen == [1] and mod._jail_cgroup_error is None


# ---------------------------------------------------------------------------
# 4. 掃除
# ---------------------------------------------------------------------------
def test_sweep_removes_only_leftovers_of_dead_jails(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    jails = tmp_path / "jails"
    jails.mkdir()
    dead = 2**22 + 12345  # pid_max を超える = 居ない pid
    (jails / f"NSJAIL.{dead}").mkdir()
    (jails / f"NSJAIL_SELF.{dead}").mkdir()
    # 動いている = nsjail が作って pid を入れる途中かもしれない
    (jails / f"NSJAIL.{os.getpid()}").mkdir()
    # zombie = 終わった jail (親の nsjail が kill されると PID 1 の下で zombie のまま残る)
    zombie = os.fork()
    if zombie == 0:
        os._exit(0)
    try:
        for _ in range(200):
            if not mod._pid_running(str(zombie)):
                break
            time.sleep(0.01)
        (jails / f"NSJAIL.{zombie}").mkdir()
        (jails / "other").mkdir()  # 自分の物ではない名前
        busy = (
            jails / f"NSJAIL.{dead + 1}"
        )  # 中身がある (実 cgroup では populated → EBUSY)
        busy.mkdir()
        (busy / "x").write_text("")
        assert mod._sweep_jail_cgroups(jails) == 3
    finally:
        os.waitpid(zombie, 0)
    assert sorted(p.name for p in jails.iterdir()) == sorted(
        [f"NSJAIL.{os.getpid()}", "other", busy.name]
    )
    assert mod._sweep_jail_cgroups(tmp_path / "missing") == 0
