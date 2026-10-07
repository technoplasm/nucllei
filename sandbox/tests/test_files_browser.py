"""ファイルブラウザ (#150) 用の `/files/*` — 一覧・stat・削除・改名・mkdir・使用量、
`/files/read` の mtime と `/files/write` の上書き競合 (`expected_mtime`) / 新規作成 (`exclusive`)。

固定すること:

- workspace と `/ext/...` (リクエストの `mounts` の bind) の両方で同じ操作ができ、ro の bind は
  403、bind に無いものは 404
- **経路上の symlink を辿らない** — 一覧は symlink を `type: "symlink"` で出すだけ、削除はリンク
  そのものを消し (向こう側は残る)、中間の symlink は 400
- 置き場の root・`.` 始まり・workspace 直下の `imports/` `exports/` は消せない / 改名できない
- 容量切れ (ENOSPC) は 507

`test_files_list.py` と同じく main.py を直接 import する (コンテナ不要)。

実行:
    uv run task sandbox-unit
"""

import base64
import errno
import importlib.util
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location("sandbox_main_browser", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    ws_root = tmp_path / "workspaces"

    async def fake_workspace(user_id):
        ws = ws_root / user_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    ext = tmp_path / "ext"
    (ext / "groups" / "EMR").mkdir(parents=True)
    (ext / "groups" / "OTHER").mkdir()
    (ext / "public").mkdir()
    (ext / "groups" / "OTHER" / "secret.txt").write_text("other-secret")
    (ext / "public" / "readme.md").write_text("pub")
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", ext)
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)

    ws = ws_root / "u1"
    (ws / "imports").mkdir(parents=True)
    (ws / "exports").mkdir()
    (ws / "trash").mkdir()
    (ws / ".exec").mkdir()
    (ws / "data").mkdir()
    (ws / "data" / "a.csv").write_text("x,y")
    (ws / "notes.md").write_text("# hi")
    (ws / ".hidden").write_text("h")
    return mod, TestClient(mod.app), ws, ext


MEMBER_MOUNTS = [
    {"path": "/ext/public", "rw": False},
    {"path": "/ext/groups/EMR", "rw": True},
]


def _post(c, ep, **body):
    return c.post(ep, json={"user_id": "u1", **body})


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ---------------------------------------------------------------------------
# 一覧・stat・read
# ---------------------------------------------------------------------------
def test_ls_one_level_hides_dotfiles_and_dirs_first(env):
    mod, c, ws, _ = env
    r = _post(c, "/files/ls", path=".")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exists"] and body["is_dir"] and not body["truncated"]
    assert body["cache_ttl_days"] == mod.CACHE_TTL_REPORTED
    names = [(e["name"], e["type"]) for e in body["entries"]]
    assert names == [
        ("data", "dir"),
        ("exports", "dir"),
        ("imports", "dir"),
        ("trash", "dir"),
        ("notes.md", "file"),
    ]
    notes = body["entries"][-1]
    assert notes["path"] == "notes.md" and notes["size"] == 4
    assert notes["mtime"] == pytest.approx((ws / "notes.md").stat().st_mtime)
    assert notes["ctime"] == pytest.approx((ws / "notes.md").stat().st_ctime)
    sub = _post(c, "/files/ls", path="data").json()
    assert [e["path"] for e in sub["entries"]] == ["data/a.csv"]
    assert _post(c, "/files/ls", path="nope").json()["exists"] is False
    f = _post(c, "/files/ls", path="notes.md").json()
    assert f["exists"] and not f["is_dir"]


def test_ls_shows_symlink_without_following(env):
    mod, c, ws, ext = env
    (ws / "link").symlink_to(ext / "groups" / "OTHER")
    body = _post(c, "/files/ls", path=".").json()
    assert {"name": "link", "type": "symlink"}.items() <= next(
        e for e in body["entries"] if e["name"] == "link"
    ).items()
    # symlink の先へは降りない
    r = _post(c, "/files/ls", path="link").json()
    assert (r["exists"], r["is_dir"], r["entries"]) == (True, False, [])
    r = _post(c, "/files/ls", path="link/secret.txt")
    assert r.status_code == 400


def test_stat_and_read_return_mtime(env):
    mod, c, ws, _ = env
    st = _post(c, "/files/stat", path="notes.md").json()
    assert st["exists"] and st["type"] == "file" and st["size"] == 4
    assert _post(c, "/files/stat", path="nope").json() == {"exists": False}
    assert _post(c, "/files/stat", path="data").json()["type"] == "dir"
    for nofollow in (False, True):
        r = _post(c, "/files/read", path="notes.md", nofollow=nofollow)
        assert r.status_code == 200, r.text
        assert r.json()["mtime"] == st["mtime"]
    # nofollow では workspace の中の symlink も辿らない (既定は従来どおり)
    (ws / "alias.md").symlink_to(ws / "notes.md")
    assert _post(c, "/files/read", path="alias.md").status_code == 200
    assert _post(c, "/files/read", path="alias.md", nofollow=True).status_code == 400


def test_traversal_is_rejected(env):
    mod, c, ws, _ = env
    for ep in ("/files/ls", "/files/stat", "/files/delete", "/files/mkdir"):
        assert _post(c, ep, path="../u2").status_code == 400, ep
    assert _post(c, "/files/rename", path="notes.md", dest="../x").status_code == 400


# ---------------------------------------------------------------------------
# write: 上書き競合と新規作成
# ---------------------------------------------------------------------------
def test_write_with_expected_mtime(env):
    mod, c, ws, _ = env
    mtime = _post(c, "/files/read", path="notes.md", nofollow=True).json()["mtime"]
    r = _post(
        c,
        "/files/write",
        path="notes.md",
        b64=_b64("v2"),
        expected_mtime=mtime,
        nofollow=True,
    )
    assert r.status_code == 200, r.text
    new_mtime = r.json()["mtime"]
    assert (ws / "notes.md").read_text() == "v2"
    # 古い mtime のままもう一度 → 外で変更された = 409 (今の mtime を返す)
    os.utime(ws / "notes.md", ns=(0, 1_000_000_000))
    r = _post(
        c,
        "/files/write",
        path="notes.md",
        b64=_b64("v3"),
        expected_mtime=new_mtime,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == {"message": "modified", "mtime": 1.0}
    assert (ws / "notes.md").read_text() == "v2"
    # 消されていた
    (ws / "notes.md").unlink()
    r = _post(c, "/files/write", path="notes.md", b64=_b64("v4"), expected_mtime=1.0)
    assert r.status_code == 409 and r.json()["detail"]["message"] == "deleted"


def test_write_exclusive(env):
    mod, c, ws, _ = env
    r = _post(c, "/files/write", path="new/x.txt", b64=_b64("x"), exclusive=True)
    assert r.status_code == 200, r.text
    assert (ws / "new" / "x.txt").read_text() == "x"
    r = _post(c, "/files/write", path="new/x.txt", b64=_b64("y"), exclusive=True)
    assert r.status_code == 409
    assert (ws / "new" / "x.txt").read_text() == "x"


def test_write_through_symlink_is_rejected(env):
    mod, c, ws, ext = env
    (ws / "out").symlink_to(ext / "groups" / "OTHER")
    r = _post(c, "/files/write", path="out/x.txt", b64=_b64("x"), nofollow=True)
    assert r.status_code == 400
    assert not (ext / "groups" / "OTHER" / "x.txt").exists()
    (ws / "f.txt").symlink_to(ext / "groups" / "OTHER" / "secret.txt")
    r = _post(c, "/files/write", path="f.txt", b64=_b64("x"), nofollow=True)
    assert r.status_code == 400
    assert (ext / "groups" / "OTHER" / "secret.txt").read_text() == "other-secret"


def test_no_space_is_507(env, monkeypatch):
    mod, c, ws, _ = env

    def full(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pathlib.Path, "write_bytes", full)
    r = _post(c, "/files/write", path="big.bin", b64=_b64("x"))
    assert r.status_code == 507
    monkeypatch.setattr(mod, "_open_dirs", full)
    r = _post(c, "/files/write", path="big.bin", b64=_b64("x"), nofollow=True)
    assert r.status_code == 507
    r = _post(c, "/files/mkdir", path="d")
    assert r.status_code == 507


# ---------------------------------------------------------------------------
# 削除・改名・mkdir
# ---------------------------------------------------------------------------
def test_delete(env):
    mod, c, ws, ext = env
    assert _post(c, "/files/delete", path="notes.md").json() == {"ok": True}
    assert not (ws / "notes.md").exists()
    (ws / "data" / "sub").mkdir()
    (ws / "data" / "sub" / "b.txt").write_text("b")
    assert _post(c, "/files/delete", path="data").status_code == 200
    assert not (ws / "data").exists()
    assert _post(c, "/files/delete", path="data").status_code == 404
    # 中身は消せるが imports/ exports/ trash/ そのものは消せない
    (ws / "imports" / "a.pdf").write_text("p")
    assert _post(c, "/files/delete", path="imports/a.pdf").status_code == 200
    (ws / "trash" / "old").mkdir()
    assert _post(c, "/files/delete", path="trash/old").status_code == 200
    for p in ("imports", "exports", "trash", ".", "", ".exec", ".hidden"):
        assert _post(c, "/files/delete", path=p).status_code == 400, p
    assert (ws / "imports").is_dir() and (ws / ".exec").is_dir()
    assert (ws / "trash").is_dir()


def test_delete_symlink_removes_only_the_link(env):
    mod, c, ws, ext = env
    (ws / "link").symlink_to(ext / "groups" / "OTHER")
    (ws / "flink").symlink_to(ext / "groups" / "OTHER" / "secret.txt")
    assert _post(c, "/files/delete", path="link").status_code == 200
    assert _post(c, "/files/delete", path="flink").status_code == 200
    assert not (ws / "link").is_symlink() and not (ws / "flink").is_symlink()
    assert (ext / "groups" / "OTHER" / "secret.txt").read_text() == "other-secret"
    # 中間が symlink のパスは消せない
    (ws / "link").symlink_to(ext / "groups" / "OTHER")
    assert _post(c, "/files/delete", path="link/secret.txt").status_code == 400
    assert (ext / "groups" / "OTHER" / "secret.txt").exists()


def test_rename(env):
    mod, c, ws, _ = env
    r = _post(c, "/files/rename", path="notes.md", dest="data/notes2.md")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "path": "data/notes2.md"}
    assert (ws / "data" / "notes2.md").read_text() == "# hi"
    # 行き先に在る → 409、無い親 → 404、自分の中 → 400、保護 → 400
    assert (
        _post(c, "/files/rename", path="data/a.csv", dest="data/notes2.md").status_code
        == 409
    )
    assert _post(c, "/files/rename", path="data/a.csv", dest="no/x").status_code == 404
    assert _post(c, "/files/rename", path="data", dest="data/in").status_code == 400
    assert _post(c, "/files/rename", path="imports", dest="imp").status_code == 400
    assert _post(c, "/files/rename", path="trash", dest="t").status_code == 400
    assert _post(c, "/files/rename", path="data", dest="trash").status_code == 400
    # ファイルブラウザの /workspace の削除 = trash/ の直下へ移す (同名は呼び手が -2 等を付ける)
    r = _post(c, "/files/rename", path="data", dest="trash/data")
    assert r.status_code == 200, r.text
    assert (ws / "trash" / "data" / "a.csv").exists()
    r = _post(c, "/files/rename", path="trash/data", dest="data")
    assert r.status_code == 200, r.text  # trash から戻せる
    assert _post(c, "/files/rename", path="data", dest=".data").status_code == 400
    assert _post(c, "/files/rename", path="nope", dest="x").status_code == 404
    # workspace → /ext は置き場をまたぐので不可
    r = _post(
        c,
        "/files/rename",
        path="data/a.csv",
        dest="/ext/groups/EMR/a.csv",
        mounts=MEMBER_MOUNTS,
    )
    assert r.status_code == 400


def test_mkdir(env):
    mod, c, ws, _ = env
    assert _post(c, "/files/mkdir", path="a/b").json() == {"ok": True, "path": "a/b"}
    assert (ws / "a" / "b").is_dir()
    assert _post(c, "/files/mkdir", path="a/b").status_code == 409
    assert _post(c, "/files/mkdir", path=".x").status_code == 400
    assert _post(c, "/files/mkdir", path=".").status_code == 400


def test_usage(env):
    mod, c, ws, _ = env
    r = _post(c, "/files/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_bytes"] > 0 and 0 <= body["used_bytes"] <= body["total_bytes"]
    assert body["quota_gb"] == mod.QUOTA_GB
    assert body["cache_ttl_days"] == mod.CACHE_TTL_REPORTED


# ---------------------------------------------------------------------------
# /ext/... (リクエストの bind の中だけ)
# ---------------------------------------------------------------------------
def test_ext_rw_group(env):
    mod, c, ws, ext = env
    g = ext / "groups" / "EMR"

    def p(ep, **kw):
        return _post(c, ep, mounts=MEMBER_MOUNTS, **kw)

    assert p("/files/mkdir", path="/ext/groups/EMR/recipes").json() == {
        "ok": True,
        "path": "/ext/groups/EMR/recipes",
    }
    r = p("/files/write", path="/ext/groups/EMR/recipes/r.md", b64=_b64("r"))
    assert r.status_code == 200 and "mtime" in r.json()
    body = p("/files/ls", path="/ext/groups/EMR").json()
    assert [(e["path"], e["type"]) for e in body["entries"]] == [
        ("/ext/groups/EMR/recipes", "dir")
    ]
    r = p(
        "/files/rename",
        path="/ext/groups/EMR/recipes/r.md",
        dest="/ext/groups/EMR/r2.md",
    )
    assert r.json() == {"ok": True, "path": "/ext/groups/EMR/r2.md"}
    assert (g / "r2.md").read_text() == "r"
    assert p("/files/delete", path="/ext/groups/EMR/r2.md").status_code == 200
    assert not (g / "r2.md").exists()
    # bind の root そのものは消せない
    assert p("/files/delete", path="/ext/groups/EMR").status_code == 400
    # 他グループ (bind に無い) は 404、グループ間の移動も 404
    assert p("/files/ls", path="/ext/groups/OTHER").status_code == 404
    assert p("/files/delete", path="/ext/groups/OTHER/secret.txt").status_code == 404
    assert (
        p(
            "/files/rename",
            path="/ext/groups/EMR/recipes",
            dest="/ext/groups/OTHER/x",
        ).status_code
        == 404
    )
    assert (ext / "groups" / "OTHER" / "secret.txt").exists()


def test_ext_ro_public(env):
    mod, c, ws, ext = env

    def p(ep, **kw):
        return _post(c, ep, mounts=MEMBER_MOUNTS, **kw)

    body = p("/files/ls", path="/ext/public").json()
    assert [e["path"] for e in body["entries"]] == ["/ext/public/readme.md"]
    r = p("/files/read", path="/ext/public/readme.md")
    assert r.status_code == 200 and "mtime" in r.json()
    assert p("/files/stat", path="/ext/public/readme.md").json()["type"] == "file"
    assert p("/files/delete", path="/ext/public/readme.md").status_code == 403
    assert p("/files/mkdir", path="/ext/public/d").status_code == 403
    assert (
        p(
            "/files/rename", path="/ext/public/readme.md", dest="/ext/public/r.md"
        ).status_code
        == 403
    )
    assert p("/files/write", path="/ext/public/x", b64=_b64("x")).status_code == 403
    assert (ext / "public" / "readme.md").exists()
    # admin (rw で渡される) なら書ける
    admin = [{"path": "/ext/public", "rw": True}]
    r = _post(c, "/files/mkdir", path="/ext/public/d", mounts=admin)
    assert r.status_code == 200


def test_ext_symlink_to_other_group_is_not_followed(env):
    mod, c, ws, ext = env
    g = ext / "groups" / "EMR"
    (g / "leak").symlink_to("../OTHER")

    def p(ep, **kw):
        return _post(c, ep, mounts=MEMBER_MOUNTS, **kw)

    body = p("/files/ls", path="/ext/groups/EMR").json()
    assert [(e["name"], e["type"]) for e in body["entries"]] == [("leak", "symlink")]
    assert p("/files/ls", path="/ext/groups/EMR/leak/").json()["is_dir"] is False
    assert p("/files/stat", path="/ext/groups/EMR/leak/secret.txt").status_code == 400
    assert p("/files/delete", path="/ext/groups/EMR/leak/secret.txt").status_code == 400
    assert p("/files/mkdir", path="/ext/groups/EMR/leak/x").status_code == 400
    assert (
        p(
            "/files/rename",
            path="/ext/groups/EMR/leak/secret.txt",
            dest="/ext/groups/EMR/s.txt",
        ).status_code
        == 400
    )
    # リンクそのものは消せる (向こうは残る)
    assert p("/files/delete", path="/ext/groups/EMR/leak").status_code == 200
    assert (ext / "groups" / "OTHER" / "secret.txt").read_text() == "other-secret"


# ---------------------------------------------------------------------------
# サービスが作るディレクトリの所有者 (llens staging dev-8d20370c9 で発見)
# ---------------------------------------------------------------------------
# `/files/write` の親を `p.parent.mkdir(parents=True)` で作り、root 所有 755 のまま残していた
# (jail = uid 65534 がその中で rm -r / mv できない)。新しく作ったディレクトリは**作った段すべて**
# 65534 の物にし、既存のディレクトリの所有者は変えない。テストは root で走らないので
# chown の呼び出しを記録して確かめる (fchown は fd の実体パスを /proc から引く)。


@pytest.fixture
def owned(monkeypatch):
    calls = []

    def fchown(fd, uid, gid):
        calls.append((os.path.realpath(f"/proc/self/fd/{fd}"), uid, gid))

    def chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
        if dir_fd is not None:
            path = os.path.join(os.path.realpath(f"/proc/self/fd/{dir_fd}"), path)
        calls.append((os.path.realpath(path), uid, gid))

    monkeypatch.setattr(os, "fchown", fchown)
    monkeypatch.setattr(os, "chown", chown)

    def dirs_owned():
        return {p for p, uid, gid in calls if uid == gid == 65534 and os.path.isdir(p)}

    return dirs_owned


def _real(*ps):
    return {os.path.realpath(p) for p in ps}


def test_write_creates_parents_owned_by_jail_user(env, owned):
    mod, c, ws, ext = env
    # tool の write_file (workspace 相対・既定の経路)
    r = _post(c, "/files/write", path="recipes/外来再診予約/RECIPE.md", b64=_b64("x"))
    assert r.status_code == 200, r.text
    assert owned() == _real(ws / "recipes", ws / "recipes" / "外来再診予約")
    # 既存の親 (data/) の所有者は変えない
    r = _post(c, "/files/write", path="data/new/b.csv", b64=_b64("x"))
    assert r.status_code == 200, r.text
    assert ws / "data" not in {pathlib.Path(p) for p in owned()}
    assert os.path.realpath(ws / "data" / "new") in owned()
    # ファイルブラウザの保存 (fd で辿る経路)
    r = _post(c, "/files/write", path="fb/x/y.txt", b64=_b64("x"), exclusive=True)
    assert r.status_code == 200, r.text
    assert _real(ws / "fb", ws / "fb" / "x") <= owned()
    # /ext/groups/<g> の中
    r = _post(
        c,
        "/files/write",
        path="/ext/groups/EMR/a/b/c.txt",
        b64=_b64("x"),
        mounts=MEMBER_MOUNTS,
    )
    assert r.status_code == 200, r.text
    emr = ext / "groups" / "EMR"
    assert _real(emr / "a", emr / "a" / "b") <= owned()
    assert (
        os.path.realpath(emr) not in owned()
    )  # 既存のグループのディレクトリは変えない


def test_write_to_ext_public_creates_parents_owned(env, owned):
    mod, c, ws, ext = env
    r = _post(
        c,
        "/files/write",
        path="/ext/public/p/q.txt",
        b64=_b64("x"),
        mounts=[{"path": "/ext/public", "rw": True}],
    )
    assert r.status_code == 200, r.text
    assert owned() == _real(ext / "public" / "p")


def test_mkdir_owned_by_jail_user(env, owned):
    mod, c, ws, ext = env
    assert _post(c, "/files/mkdir", path="m/n/o").status_code == 200
    assert owned() == _real(ws / "m", ws / "m" / "n", ws / "m" / "n" / "o")
    r = _post(c, "/files/mkdir", path="/ext/groups/EMR/d/e", mounts=MEMBER_MOUNTS)
    assert r.status_code == 200, r.text
    emr = ext / "groups" / "EMR"
    assert _real(emr / "d", emr / "d" / "e") <= owned()
    # 初回の rw bind で作るグループのディレクトリ
    r = _post(
        c,
        "/files/mkdir",
        path="/ext/groups/NEW/x",
        mounts=[{"path": "/ext/groups/NEW", "rw": True}],
    )
    assert r.status_code == 200, r.text
    assert _real(ext / "groups" / "NEW", ext / "groups" / "NEW" / "x") <= owned()


def test_rename_does_not_create_dirs(env, owned):
    # 改名・移動は行き先の親を作らない (無ければ 404) — root 所有のディレクトリを残す余地が無い
    mod, c, ws, ext = env
    assert _post(c, "/files/rename", path="notes.md", dest="no/x.md").status_code == 404
    assert not (ws / "no").exists()
    r = _post(
        c,
        "/files/rename",
        path="/ext/groups/EMR",
        dest="/ext/groups/EMR/no/x",
        mounts=MEMBER_MOUNTS,
    )
    assert r.status_code == 400
    assert owned() == set()


def test_workspace_imports_dirs_owned(env, owned, monkeypatch, tmp_path):
    # `/exec` の imports 書き込みは `_ensure_workspace` が用意する imports/ に置く。jail で
    # 消されていても作り直し、65534 の物にする (既存はそのまま)
    mod, c, ws, ext = env
    monkeypatch.setattr(mod, "WORKSPACES", tmp_path / "workspaces")
    monkeypatch.setattr(mod, "IMAGES", tmp_path / "images")
    monkeypatch.setattr(mod.os.path, "ismount", lambda p: True)
    import shutil

    shutil.rmtree(ws / "imports")
    assert mod._ensure_workspace("u1") == ws
    assert owned() == _real(ws / "imports")
