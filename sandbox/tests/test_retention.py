"""workspace の保持期限 (`SANDBOX_WORKSPACE_RETENTION_DAYS`) の回帰 (ken 決定 2026-10-03)。

`test_ro_mounts.py` と同じく main.py を直接 import する (コンテナ不要)。mount は
`_ensure_workspace` を偽物に差し替え、`.img` と `.last_used` は tmp_path に実ファイルで置く。

実行:
    uv run task sandbox-unit
"""

import asyncio
import importlib.util
import os
import pathlib
import sys
import time

import pytest

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"
DAY = 86400


def _load_main(monkeypatch, tmp_path, retention_days="0"):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.setenv("SANDBOX_WORKSPACE_RETENTION_DAYS", retention_days)
    spec = importlib.util.spec_from_file_location("sandbox_main_retention_test", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.IMAGES.mkdir(parents=True)
    return mod


def _img(mod, user, age_days=0.0) -> pathlib.Path:
    p = mod.IMAGES / f"{user}.img"
    p.write_bytes(b"x" * 4096)
    t = time.time() - age_days * DAY
    os.utime(p, (t, t))
    return p


def _marker(mod, user, age_days=0.0) -> pathlib.Path:
    p = mod._last_used_marker(user)
    p.touch()
    t = time.time() - age_days * DAY
    os.utime(p, (t, t))
    return p


def test_env_default_is_never(monkeypatch, tmp_path):
    # 既定 0 = 消さない (本番は 40TB NAS。消える事故より容量を取る)
    monkeypatch.delenv("SANDBOX_WORKSPACE_RETENTION_DAYS", raising=False)
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "sandbox_main_retention_default", _MAIN
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    assert mod.WS_RETENTION_DAYS == 0


def test_marker_touched_on_use(monkeypatch, tmp_path):
    # `_workspace` を通るたびに `.images/<user>.last_used` の mtime が今になる
    # (marker は image の外にあるので未 mount でも読める)
    mod = _load_main(monkeypatch, tmp_path, "30")
    ws = tmp_path / "workspaces" / "alice"
    monkeypatch.setattr(mod, "_ensure_workspace", lambda user_id: ws)
    marker = mod._last_used_marker("alice")
    assert marker.parent == mod.IMAGES
    assert not marker.exists()

    before = time.time() - 1
    assert asyncio.run(mod._workspace("alice")) == ws
    assert marker.exists()
    assert marker.stat().st_mtime >= before
    assert "alice" in mod._ws_registry

    # 2 回目は mtime が進む
    old = time.time() - 10 * DAY
    os.utime(marker, (old, old))
    asyncio.run(mod._workspace("alice"))
    assert marker.stat().st_mtime >= before


def test_retention_zero_deletes_nothing(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path, "0")
    img = _img(mod, "alice", age_days=400)
    _marker(mod, "alice", age_days=400)
    assert asyncio.run(mod.reap_stale_workspaces()) == 0
    assert img.exists()


def test_old_marker_is_deleted(monkeypatch, tmp_path, capsys):
    mod = _load_main(monkeypatch, tmp_path, "180")
    old_img = _img(
        mod, "alice", age_days=1
    )  # image の mtime は新しくても marker が基準
    old_marker = _marker(mod, "alice", age_days=181)
    (mod.WORKSPACES / "alice").mkdir(parents=True)  # 空の mountpoint
    fresh_img = _img(mod, "bob", age_days=300)
    _marker(mod, "bob", age_days=179)
    # suffix 違い (退避物) には触らない
    trash = mod.IMAGES / "carol.img.trash-1"
    trash.write_bytes(b"x")
    t = time.time() - 400 * DAY
    os.utime(trash, (t, t))

    assert asyncio.run(mod.reap_stale_workspaces()) == 1
    assert not old_img.exists()
    assert not old_marker.exists()
    assert not (mod.WORKSPACES / "alice").exists()
    assert fresh_img.exists()
    assert trash.exists()
    out = capsys.readouterr().out
    assert "workspace of alice removed after 180 days inactive" in out
    assert "bob" not in out


def test_missing_marker_falls_back_to_image_mtime(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path, "30")
    stale = _img(mod, "alice", age_days=31)
    recent = _img(mod, "bob", age_days=29)
    assert asyncio.run(mod.reap_stale_workspaces()) == 1
    assert not stale.exists()
    assert recent.exists()


def test_mounted_or_locked_is_skipped(monkeypatch, tmp_path, capsys):
    mod = _load_main(monkeypatch, tmp_path, "30")
    for u in ("mounted", "mlocked", "ulocked", "session"):
        _img(mod, u, age_days=100)
        _marker(mod, u, age_days=100)

    # mount 中 (ismount が True)
    monkeypatch.setattr(
        mod.os.path, "ismount", lambda p: pathlib.Path(p).name == "mounted"
    )
    # 生存 session
    monkeypatch.setattr(mod, "_has_live_session", lambda u: u == "session")

    async def run():
        # mount / remount 中 (= _mount_lock 保持) と stateless 実行中 (= _user_lock 保持)
        async with mod._mount_lock("mlocked"), mod._user_lock("ulocked"):
            return await mod.reap_stale_workspaces()

    assert asyncio.run(run()) == 0
    for u in ("mounted", "mlocked", "ulocked", "session"):
        assert (mod.IMAGES / f"{u}.img").exists(), u
        assert mod._last_used_marker(u).exists(), u
    out = capsys.readouterr().out
    assert "workspace of mlocked is busy" in out
    assert "workspace of ulocked is busy" in out

    # lock が空けば次回で消える
    monkeypatch.setattr(mod.os.path, "ismount", lambda p: False)
    monkeypatch.setattr(mod, "_has_live_session", lambda u: False)
    assert asyncio.run(mod.reap_stale_workspaces()) == 4


def test_invalid_names_and_missing_dir(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path, "1")
    bad = (
        mod.IMAGES / "a b.img"
    )  # _USER_ID_RE に合わない名前 = workspace image ではない
    bad.write_bytes(b"x")
    t = time.time() - 10 * DAY
    os.utime(bad, (t, t))
    assert asyncio.run(mod.reap_stale_workspaces()) == 0
    assert bad.exists()

    bad.unlink()
    mod.IMAGES.rmdir()
    assert asyncio.run(mod.reap_stale_workspaces()) == 0


@pytest.mark.parametrize(
    "n,s", [(0, "0 B"), (4096, "4.0 KiB"), (3 * 1024**3, "3.0 GiB")]
)
def test_fmt_size(monkeypatch, tmp_path, n, s):
    mod = _load_main(monkeypatch, tmp_path)
    assert mod._fmt_size(n) == s


# ---------------------------------------------------------------------------
# imports/ exports/ のキャッシュ掃除 (`SANDBOX_CACHE_TTL_DAYS`、#149)
# ---------------------------------------------------------------------------


def _file(p: pathlib.Path, age_days=0.0, data=b"x" * 100) -> pathlib.Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    t = time.time() - age_days * DAY
    os.utime(p, (t, t), follow_symlinks=False)
    return p


def _cache_ws(tmp_path) -> dict:
    """期限切れ / 期限内 / symlink / memory/ 配下を混ぜた workspace を作る。"""
    ws = tmp_path / "workspaces" / "alice"
    outside = _file(tmp_path / "outside" / "secret.txt", age_days=90)
    f = {
        "ws": ws,
        "outside": outside,
        "old_import": _file(ws / "imports" / "old.csv", 31),
        "new_import": _file(ws / "imports" / "new.csv", 29),
        "old_nested": _file(ws / "exports" / "sub" / "deep" / "old.png", 40),
        "old_mixed": _file(ws / "exports" / "mixed" / "old.txt", 40),
        "new_mixed": _file(ws / "exports" / "mixed" / "new.txt", 1),
        "old_memory": _file(ws / "memory" / "MEMORY.md", 365),
        "old_other": _file(ws / "work" / "old.py", 365),
    }
    (ws / "exports" / "empty").mkdir()  # 元から空のサブディレクトリは残す
    # symlink: 外のファイル / 外のディレクトリを指すもの (どちらも辿らず、消さない)
    link_file = ws / "imports" / "link.txt"
    link_file.symlink_to(outside)
    link_dir = ws / "exports" / "linkdir"
    link_dir.symlink_to(outside.parent)
    t = time.time() - 90 * DAY
    os.utime(link_file, (t, t), follow_symlinks=False)
    os.utime(link_dir, (t, t), follow_symlinks=False)
    f["link_file"] = link_file
    f["link_dir"] = link_dir
    return f


def test_cache_ttl_env_default_is_30(monkeypatch, tmp_path):
    monkeypatch.delenv("SANDBOX_CACHE_TTL_DAYS", raising=False)
    mod = _load_main(monkeypatch, tmp_path)
    assert mod.CACHE_TTL_DAYS == 30


def test_cache_sweep_removes_only_expired_files(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)
    f = _cache_ws(tmp_path)
    ws = f["ws"]

    n, b = mod._sweep_cache_dirs(ws)
    assert (n, b) == (3, 300)
    for k in ("old_import", "old_nested", "old_mixed"):
        assert not f[k].exists(), k
    for k in ("new_import", "new_mixed", "old_memory", "old_other", "outside"):
        assert f[k].exists(), k
    # symlink は残り、指す先 (workspace の外) も無事
    assert f["link_file"].is_symlink() and f["link_dir"].is_symlink()
    assert (f["outside"].parent / "secret.txt").exists()
    # 空になったサブディレクトリは消え、imports/ exports/ 自体と元から空のものは残る
    assert not (ws / "exports" / "sub").exists()
    assert (ws / "exports" / "mixed").is_dir()
    assert (ws / "exports" / "empty").is_dir()
    assert (ws / "imports").is_dir() and (ws / "exports").is_dir()

    # 2 回目は何も消えない
    assert mod._sweep_cache_dirs(ws) == (0, 0)


def test_cache_sweep_disabled_and_symlinked_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_CACHE_TTL_DAYS", "0")
    mod = _load_main(monkeypatch, tmp_path)
    f = _cache_ws(tmp_path)
    assert mod._sweep_cache_dirs(f["ws"]) == (0, 0)
    assert f["old_import"].exists()

    # imports/ 自体を外への symlink に差し替えられていても辿らない
    monkeypatch.setattr(mod, "CACHE_TTL_DAYS", 30)
    ws = tmp_path / "workspaces" / "bob"
    ws.mkdir(parents=True)
    victim = _file(tmp_path / "victim" / "old.bin", 90)
    (ws / "imports").symlink_to(victim.parent)
    assert mod._sweep_cache_dirs(ws) == (0, 0)
    assert victim.exists()


def test_cache_sweep_runs_before_umount_in_idle_reaper(monkeypatch, tmp_path, capsys):
    mod = _load_main(monkeypatch, tmp_path)
    f = _cache_ws(tmp_path)
    calls = []

    def fake_run(cmd, **kw):
        # umount の時点で掃除が済んでいる
        assert not f["old_import"].exists()
        calls.append(cmd)

    monkeypatch.setattr(mod, "_run", fake_run)
    monkeypatch.setattr(mod.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(mod, "WS_IDLE_TTL_S", 0)
    mod._ws_registry["alice"] = time.monotonic() - 10

    assert asyncio.run(mod.reap_idle_workspaces()) == 1
    assert calls == [["umount", str(f["ws"])]]
    assert f["new_import"].exists() and f["old_memory"].exists()
    out = capsys.readouterr().out
    assert "cache of alice: removed 3 file(s) older than 30 days, 300 B" in out


def test_cache_sweep_skipped_with_live_session(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)
    f = _cache_ws(tmp_path)
    monkeypatch.setattr(mod, "_run", lambda *a, **k: pytest.fail("umount"))
    monkeypatch.setattr(mod.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(mod, "_has_live_session", lambda u: True)
    monkeypatch.setattr(mod, "WS_IDLE_TTL_S", 0)
    mod._ws_registry["alice"] = time.monotonic() - 10
    assert asyncio.run(mod.reap_idle_workspaces()) == 0
    assert f["old_import"].exists()
