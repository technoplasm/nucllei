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
# imports/ exports/ trash/ の掃除 (`SANDBOX_CACHE_TTL_DAYS`、#149)
# 規則は 3 つとも同じ: 直下のエントリ単位、そのエントリの lstat の max(mtime, ctime)。
# ctime は utime で過去にできない (utime 自体が ctime を今にする) ので、期限切れは
# 「今 + N 日」の時点 (`now=`) で掃除して作る
# ---------------------------------------------------------------------------


def _file(p: pathlib.Path, age_days=0.0, data=b"x" * 100) -> pathlib.Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    t = time.time() - age_days * DAY
    os.utime(p, (t, t), follow_symlinks=False)
    return p


def _later(days):
    return time.time() + days * DAY


def _ws(tmp_path, user="alice") -> pathlib.Path:
    ws = tmp_path / "workspaces" / user
    for d in ("imports", "exports", "trash"):
        (ws / d).mkdir(parents=True)
    return ws


@pytest.mark.parametrize("where", ["tmp_path", "source_tree"])
def test_rename_updates_ctime(tmp_path, where):
    """判定の前提: rename (mv) は移した物の mtime を変えないが ctime を今にする。
    Linux の ext4 (workspace の sparse loopback と同じ fs) と tmpfs で成り立つ (2026-10-07 に
    両方で確認)。tmp_path は tmpfs のことが多いので、ディスク上 (多くは ext4) の source tree
    でも確かめる。ctime は過去にできないので「前より進んだ」で見る。"""
    import shutil
    import tempfile

    base = (
        tmp_path
        if where == "tmp_path"
        else pathlib.Path(tempfile.mkdtemp(dir=pathlib.Path(__file__).parent))
    )
    try:
        old = time.time() - 60 * DAY
        f = _file(base / "src" / "f.txt", 60)
        d = base / "src" / "d"
        d.mkdir()
        os.utime(d, (old, old))
        (base / "trash").mkdir()
        before_f, before_d = os.lstat(f).st_ctime_ns, os.lstat(d).st_ctime_ns
        time.sleep(0.05)  # ctime は coarse clock (jiffy 単位) なので刻みを跨ぐ
        os.rename(f, base / "trash" / "f.txt")
        os.rename(d, base / "trash" / "d")
        st_f = os.lstat(base / "trash" / "f.txt")
        st_d = os.lstat(base / "trash" / "d")
        assert st_f.st_ctime_ns > before_f and st_d.st_ctime_ns > before_d
        assert st_f.st_mtime < time.time() - 59 * DAY  # mtime は古いまま
        assert st_d.st_mtime < time.time() - 59 * DAY
    finally:
        if where != "tmp_path":
            shutil.rmtree(base)


def test_cache_ttl_env_default_is_30(monkeypatch, tmp_path):
    monkeypatch.delenv("SANDBOX_CACHE_TTL_DAYS", raising=False)
    mod = _load_main(monkeypatch, tmp_path)
    assert mod.CACHE_TTL_DAYS == 30


def test_sweep_removes_whole_top_level_entries(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)
    ws = _ws(tmp_path)
    _file(ws / "imports" / "a.csv", data=b"x" * 100)
    _file(ws / "exports" / "sub" / "deep" / "b.png", data=b"x" * 50)
    (ws / "exports" / "empty").mkdir()
    _file(ws / "trash" / "memo.txt", data=b"x" * 10)
    _file(ws / "trash" / "proj" / "c.txt", data=b"x" * 5)
    other = _file(ws / "memory" / "MEMORY.md", 365)

    # 期限内 (29 日後) は何も消えない
    assert mod._sweep_cache_dirs(ws, now=_later(29)) == (0, 0)
    assert mod._sweep_trash(ws, now=_later(29)) == (0, 0)
    # 31 日後: 直下のエントリが中身ごと消え、置き場の root は残る。他の場所には触らない
    assert mod._sweep_cache_dirs(ws, now=_later(31)) == (3, 150)
    assert mod._sweep_trash(ws, now=_later(31)) == (2, 15)
    for d in ("imports", "exports", "trash"):
        assert (ws / d).is_dir() and list((ws / d).iterdir()) == [], d
    assert other.exists()


def test_exports_stays_in_sweep_after_auto_attach_removed(monkeypatch, tmp_path):
    """exports/ の自動回収 (チャットへの自動添付) は廃止したが (2026-10-19)、本番の workspace に
    残っている写しを期限で消すため、exports/ は掃除の対象のまま (ken 決定)。新しい workspace には
    作らず、ファイルブラウザからは消せる (保護しない)。"""
    mod = _load_main(monkeypatch, tmp_path)
    assert "exports" in mod.CACHE_DIRS
    assert "exports" not in mod.PROTECTED_DIRS
    ws = _ws(tmp_path)
    _file(ws / "exports" / "old.png", data=b"x" * 7)
    assert mod._sweep_cache_dirs(ws, now=_later(31)) == (1, 7)
    assert list((ws / "exports").iterdir()) == []
    # exports/ が無い (新しい workspace) 場合も掃除は落ちない
    (ws / "exports").rmdir()
    assert mod._sweep_cache_dirs(ws, now=_later(31)) == (0, 0)


def test_sweep_ignores_contents_times(monkeypatch, tmp_path):
    # 中身の時刻は見ない: 新しいファイルを含むフォルダも、直下のエントリが古ければ丸ごと消え、
    # 古いファイルを含むフォルダも、直下のエントリが新しければ残る
    mod = _load_main(monkeypatch, tmp_path)
    ws = _ws(tmp_path)
    fresh = _file(ws / "exports" / "proj" / "new.txt")
    later = _later(31)
    os.utime(fresh, (later, later))  # 中のファイルだけ「31 日後に触った」
    assert mod._sweep_cache_dirs(ws, now=later) == (1, 100)
    assert not (ws / "exports" / "proj").exists()


def test_old_contents_moved_today_survive(monkeypatch, tmp_path):
    # 2 か月前のファイルを含むフォルダ (フォルダ自身の mtime も古い) や 2 か月前のファイルを
    # 今 trash に移しても消えない — mv は mtime を変えないが、直下のエントリの ctime が今になる
    mod = _load_main(monkeypatch, tmp_path)
    ws = _ws(tmp_path)
    old = time.time() - 60 * DAY
    _file(ws / "work" / "proj" / "old.csv", 60)
    os.utime(ws / "work" / "proj", (old, old))
    old_file = _file(ws / "work" / "old.bin", 60)
    os.rename(ws / "work" / "proj", ws / "trash" / "proj")
    os.rename(old_file, ws / "trash" / "old.bin")
    assert os.lstat(ws / "trash" / "proj").st_mtime < time.time() - 59 * DAY

    assert mod._sweep_trash(ws) == (0, 0)
    assert (ws / "trash" / "proj" / "old.csv").exists()
    assert (ws / "trash" / "old.bin").exists()
    # 移した日から 31 日後には消える
    assert mod._sweep_trash(ws, now=_later(31)) == (2, 200)


def test_sweep_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_CACHE_TTL_DAYS", "0")
    mod = _load_main(monkeypatch, tmp_path)
    ws = _ws(tmp_path)
    f = _file(ws / "imports" / "a.csv")
    t = _file(ws / "trash" / "a.csv")
    assert mod._sweep_cache_dirs(ws, now=_later(400)) == (0, 0)
    assert mod._sweep_trash(ws, now=_later(400)) == (0, 0)
    assert f.exists() and t.exists()


def test_sweep_does_not_follow_symlinks(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)
    ws = _ws(tmp_path)
    outside = _file(tmp_path / "outside" / "secret.txt", 90)
    (ws / "trash" / "d").mkdir()
    (ws / "trash" / "d" / "inner_link").symlink_to(outside.parent)  # 中の symlink
    (ws / "trash" / "link").symlink_to(outside.parent)  # 直下の symlink (dir を指す)
    (ws / "imports" / "flink").symlink_to(outside)  # 直下の symlink (file を指す)

    assert mod._sweep_trash(ws, now=_later(31)) == (2, 0)
    assert mod._sweep_cache_dirs(ws, now=_later(31)) == (1, 0)
    assert not (ws / "trash" / "link").is_symlink()
    assert not (ws / "imports" / "flink").is_symlink()
    assert outside.read_bytes() == b"x" * 100  # 指す先は無事

    # 置き場そのものが外への symlink に差し替えられていたら何もしない
    ws2 = tmp_path / "workspaces" / "bob"
    ws2.mkdir()
    for d in ("imports", "exports", "trash"):
        (ws2 / d).symlink_to(outside.parent)
    assert mod._sweep_cache_dirs(ws2, now=_later(400)) == (0, 0)
    assert mod._sweep_trash(ws2, now=_later(400)) == (0, 0)
    assert outside.exists()


def _reaper_setup(mod, monkeypatch, later, run=None):
    real_c, real_t = mod._sweep_cache_dirs, mod._sweep_trash
    monkeypatch.setattr(mod, "_sweep_cache_dirs", lambda ws: real_c(ws, now=later))
    monkeypatch.setattr(mod, "_sweep_trash", lambda ws: real_t(ws, now=later))
    monkeypatch.setattr(mod, "_run", run or (lambda *a, **k: None))
    monkeypatch.setattr(mod.os.path, "ismount", lambda p: True)
    monkeypatch.setattr(mod, "WS_IDLE_TTL_S", 0)
    mod._ws_registry["alice"] = time.monotonic() - 10


def test_sweep_runs_before_umount_in_idle_reaper(monkeypatch, tmp_path, capsys):
    mod = _load_main(monkeypatch, tmp_path)
    ws = _ws(tmp_path)
    imp = _file(ws / "imports" / "a.csv")
    _file(ws / "exports" / "b.png", data=b"x" * 200)
    _file(ws / "trash" / "proj" / "c.txt", data=b"x" * 10)
    calls = []

    def fake_run(cmd, **kw):
        assert not imp.exists()  # umount の時点で掃除が済んでいる
        calls.append(cmd)

    _reaper_setup(mod, monkeypatch, _later(31), fake_run)
    assert asyncio.run(mod.reap_idle_workspaces()) == 1
    assert calls == [["umount", str(ws)]]
    out = capsys.readouterr().out
    assert (
        "cache of alice: removed entries older than 30 days — "
        "imports/exports: 2, 300 B; trash: 1, 10 B" in out
    )


def test_sweep_skipped_with_live_session(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path)
    ws = _ws(tmp_path)
    imp = _file(ws / "imports" / "a.csv")
    _reaper_setup(mod, monkeypatch, _later(31), lambda *a, **k: pytest.fail("umount"))
    monkeypatch.setattr(mod, "_has_live_session", lambda u: True)
    assert asyncio.run(mod.reap_idle_workspaces()) == 0
    assert imp.exists()
