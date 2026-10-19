"""リクエストごとの bind (`/ext/groups/<g>` `/ext/public`。#152) の回帰。

sandbox は ACL を解釈しない — 呼び出し元 (nucllei) が `mounts` で渡したものだけを bind し、
パスとして安全か (形・各セグメント・経路上の symlink) だけを見る。ここで固定するのは:

- `mounts` の検証 (受ける形は `/ext/groups/<g>` と `/ext/public` だけ。`/ext/groups` 丸ごと・
  深い位置・traversal・`:` は 400)
- 直下の自動 read-only bind から `groups` `public` を外す (丸ごと見えない)
- nsjail の引数 (rw は `--bindmount`、ro は `--bindmount_ro`)、無い置き場は飛ばす、
  rw のグループのディレクトリは作る、symlink に差し替えられたグループのディレクトリは bind しない
- `/files/*` の `/ext/...`: 渡された bind の中だけ。ro への書き込みは 403、bind に無いものは 404。
  **経路上の symlink を辿らない** — jail 内で作った「他グループへの symlink」をサービス側で
  辿って抜ける穴の回帰 (読み・書き・一覧・中間ディレクトリ・workspace 側からの symlink)
- 常駐 kernel は bind の指紋が変わったら作り直し、reset を返す

`test_files_list.py` と同じく main.py を直接 import する (コンテナ不要)。

実行:
    uv run task sandbox-unit
"""

import asyncio
import base64
import importlib.util
import os
import pathlib
import sys
import types

import pytest
from fastapi.testclient import TestClient

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location("sandbox_main_ext_mounts", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    ext = tmp_path / "ext"
    (ext / "groups" / "EMR").mkdir(parents=True)
    (ext / "groups" / "OTHER").mkdir()
    (ext / "public").mkdir()
    (ext / "doc").mkdir()
    (ext / "groups" / "OTHER" / "secret.txt").write_text("other-secret")
    (ext / "public" / "readme.md").write_text("pub")
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", ext)
    # テストは root ではないので、jail のユーザーへの chown は素通しにする
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)
    return mod, ext


def _M(path, rw=False):
    return {"path": path, "rw": rw}


def _mounts(mod, *items):
    return [mod.MountIn(**m) for m in items]


# ---------------------------------------------------------------------------
# mounts の検証
# ---------------------------------------------------------------------------
def test_validate_accepts_only_group_dirs_and_public(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    got = mod._validate_mounts(
        _mounts(
            mod,
            _M("/ext/public"),
            _M("/ext/groups/EMR", True),
            _M("/ext/groups/内科 外来", True),
            _M("/ext/groups/EMR", True),  # 重複は 1 つに
        )
    )
    assert got == [
        ("/ext/groups/EMR", True),
        ("/ext/groups/内科 外来", True),
        ("/ext/public", False),
    ]
    assert mod._validate_mounts([]) == []


@pytest.mark.parametrize(
    "path",
    [
        "/ext/groups",  # 丸ごとは bind しない
        "/ext/groups/",
        "/ext/groups/EMR/sub",  # 深い位置
        "/ext/public/x",
        "/ext/doc",  # 自動 bind の名前は受けない
        "/ext/groups/..",
        "/ext/groups/.",
        "/ext/groups/.hidden",
        "/ext/groups/a:b",  # nsjail の src:dst 区切り
        "/ext/groups/a\\b",
        "/ext/groups/a\nb",
        "/ext/groups/a​b",  # 書式文字
        "/ext/groups/ EMR",
        "/ext/groups/" + "あ" * 86,  # 255 バイト超
        "/ext//groups/EMR",
        "/workspace",
        "ext/public",
        "/ext/../ext/public",
    ],
)
def test_validate_rejects_unsafe_paths(monkeypatch, tmp_path, path):
    mod, _ = _load(monkeypatch, tmp_path)
    with pytest.raises(mod.HTTPException) as ei:
        mod._validate_mounts(_mounts(mod, _M(path, True)))
    assert ei.value.status_code == 400


def test_validate_rejects_rw_ro_conflict(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    with pytest.raises(mod.HTTPException):
        mod._validate_mounts(_mounts(mod, _M("/ext/public"), _M("/ext/public", True)))


# ---------------------------------------------------------------------------
# nsjail の引数
# ---------------------------------------------------------------------------
def _binds_of(cmd):
    return [
        (a, cmd[i + 1])
        for i, a in enumerate(cmd[:-1])
        if a in ("--bindmount", "--bindmount_ro")
    ]


def test_groups_and_public_are_never_bound_wholesale(monkeypatch, tmp_path):
    mod, ext = _load(monkeypatch, tmp_path)
    assert mod._scan_ro_mount_root() == ([str(ext / "doc")], [])
    binds = _binds_of(mod._jail_base(pathlib.Path("/workspaces/u"), 60))
    assert ("--bindmount_ro", str(ext / "doc")) in binds
    assert not any(
        b.startswith((str(ext / "groups"), str(ext / "public"))) for _, b in binds
    )


def test_request_mounts_become_rw_and_ro_binds(monkeypatch, tmp_path):
    mod, ext = _load(monkeypatch, tmp_path)
    validated = mod._validate_mounts(
        _mounts(mod, _M("/ext/public"), _M("/ext/groups/EMR", True))
    )
    binds = mod._prepare_mounts(validated)
    assert binds == [
        (str(ext / "groups" / "EMR"), "/ext/groups/EMR", True),
        (str(ext / "public"), "/ext/public", False),
    ]
    got = _binds_of(mod._jail_base(pathlib.Path("/workspaces/u"), 60, binds))
    assert ("--bindmount", f"{ext}/groups/EMR:/ext/groups/EMR") in got
    assert ("--bindmount_ro", f"{ext}/public:/ext/public") in got
    # 渡していない OTHER は jail に無い
    assert not any("OTHER" in b for _, b in got)


def test_src_equal_to_dst_is_passed_alone(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)
    cmd = mod._jail_base(
        pathlib.Path("/w"), 60, [("/ext/public", "/ext/public", False)]
    )
    assert ("--bindmount_ro", "/ext/public") in _binds_of(cmd)


def test_missing_places_are_skipped_and_rw_group_dir_is_created(monkeypatch, tmp_path):
    mod, ext = _load(monkeypatch, tmp_path)
    # ro で無いもの = 飛ばす (nsjail は存在しない src で jail ごと落ちる)
    assert mod._prepare_mounts([("/ext/groups/NEW", False)]) == []
    assert not (ext / "groups" / "NEW").exists()
    # rw = 所属グループの置き場。末端だけ作る
    assert mod._prepare_mounts([("/ext/groups/NEW", True)]) == [
        (str(ext / "groups" / "NEW"), "/ext/groups/NEW", True)
    ]
    assert (ext / "groups" / "NEW").is_dir()
    # 置き場の根 (`/ext/groups` `/ext/public`) は作らない = 配備側が置く
    import shutil

    shutil.rmtree(ext / "public")
    assert mod._prepare_mounts([("/ext/public", True)]) == []
    assert not (ext / "public").exists()


def test_group_dir_replaced_by_symlink_is_not_bound(monkeypatch, tmp_path):
    mod, ext = _load(monkeypatch, tmp_path)
    (ext / "groups" / "EVIL").symlink_to(ext / "groups" / "OTHER")
    assert mod._prepare_mounts([("/ext/groups/EVIL", True)]) == []
    assert mod._prepare_mounts([("/ext/groups/EVIL", False)]) == []


# ---------------------------------------------------------------------------
# /files/* の /ext/...
# ---------------------------------------------------------------------------
EMR_RW = [_M("/ext/groups/EMR", True), _M("/ext/public")]


def _client(mod, tmp_path):
    async def fake_workspace(user_id):
        ws = tmp_path / "workspaces" / user_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    mod._workspace = fake_workspace
    return TestClient(mod.app)


def _b64(s: bytes) -> str:
    return base64.b64encode(s).decode()


def test_ext_list_says_truncated(monkeypatch, tmp_path):
    """`/ext/...` の一覧も件数の上限 (`LIST_LIMIT`) で打ち切ったら `truncated: true`。"""
    mod, ext = _load(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "LIST_LIMIT", 2)
    c = _client(mod, tmp_path)
    d = ext / "groups" / "EMR" / "many"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (d / f"{i}.md").write_text("x")

    def listed():
        r = c.post(
            "/files/list",
            json={"user_id": "u1", "path": "/ext/groups/EMR/many", "mounts": EMR_RW},
        )
        assert r.status_code == 200, r.text
        return r.json()

    assert len(listed()["entries"]) == 2 and listed()["truncated"] is False
    (d / "2.md").write_text("x")
    res = listed()
    assert len(res["entries"]) == 2 and res["truncated"] is True


def test_files_roundtrip_inside_rw_group(monkeypatch, tmp_path):
    mod, ext = _load(monkeypatch, tmp_path)
    c = _client(mod, tmp_path)
    r = c.post(
        "/files/write",
        json={
            "user_id": "u1",
            "path": "/ext/groups/EMR/recipes/a.md",
            "b64": _b64(b"hello"),
            "mounts": EMR_RW,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["size"] == 5
    assert (ext / "groups" / "EMR" / "recipes" / "a.md").read_bytes() == b"hello"

    r = c.post(
        "/files/read",
        json={
            "user_id": "u1",
            "path": "/ext/groups/EMR/recipes/a.md",
            "mounts": EMR_RW,
        },
    )
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["b64"]) == b"hello" and r.json()["name"] == "a.md"

    r = c.post(
        "/files/list",
        json={"user_id": "u1", "path": "/ext/groups/EMR", "mounts": EMR_RW},
    )
    assert r.json() == {
        "entries": [{"path": "/ext/groups/EMR/recipes/a.md", "size": 5}],
        "exists": True,
        "is_dir": True,
        "truncated": False,
    }
    r = c.post(
        "/files/list", json={"user_id": "u1", "path": "/ext/public", "mounts": EMR_RW}
    )
    assert r.json()["entries"] == [{"path": "/ext/public/readme.md", "size": 3}]
    r = c.post(
        "/files/list",
        json={"user_id": "u1", "path": "/ext/groups/EMR/nope", "mounts": EMR_RW},
    )
    assert r.json() == {"entries": [], "exists": False, "is_dir": False}


def test_files_ro_is_403_and_unbound_is_404(monkeypatch, tmp_path):
    mod, ext = _load(monkeypatch, tmp_path)
    c = _client(mod, tmp_path)
    r = c.post(
        "/files/write",
        json={
            "user_id": "u1",
            "path": "/ext/public/x.md",
            "b64": _b64(b"x"),
            "mounts": EMR_RW,
        },
    )
    assert r.status_code == 403
    assert not (ext / "public" / "x.md").exists()
    # 渡されていないグループ = jail に無い = 不在と同じ
    for ep, extra in (("/files/read", {}), ("/files/write", {"b64": _b64(b"x")})):
        r = c.post(
            ep,
            json={
                "user_id": "u1",
                "path": "/ext/groups/OTHER/secret.txt",
                "mounts": EMR_RW,
                **extra,
            },
        )
        assert r.status_code == 404, (ep, r.text)
    assert (ext / "groups" / "OTHER" / "secret.txt").read_text() == "other-secret"
    # mounts が無い (旧 nucllei) = どれも見えない
    r = c.post("/files/read", json={"user_id": "u1", "path": "/ext/public/readme.md"})
    assert r.status_code == 404
    # `/ext` の外の絶対パス・traversal は 400
    for p in ("/etc/passwd", "/ext/groups/EMR/../OTHER/secret.txt"):
        r = c.post("/files/read", json={"user_id": "u1", "path": p, "mounts": EMR_RW})
        assert r.status_code == 400, p


def test_symlink_to_other_group_is_not_followed(monkeypatch, tmp_path):
    """jail 内で `/ext/groups/EMR/` に他グループを指す symlink を作られても、サービス側
    (コンテナの名前空間には全グループが在る) はそれを辿らない。"""
    mod, ext = _load(monkeypatch, tmp_path)
    c = _client(mod, tmp_path)
    emr = ext / "groups" / "EMR"
    other = ext / "groups" / "OTHER"
    (emr / "leak.txt").symlink_to(other / "secret.txt")  # 絶対
    (emr / "rel.txt").symlink_to("../OTHER/secret.txt")  # 相対
    (emr / "dir").symlink_to(other)  # ディレクトリ
    (emr / "real").mkdir()
    (emr / "real" / "x.txt").write_text("ok")

    for p in (
        "/ext/groups/EMR/leak.txt",
        "/ext/groups/EMR/rel.txt",
        "/ext/groups/EMR/dir/secret.txt",
    ):
        r = c.post("/files/read", json={"user_id": "u1", "path": p, "mounts": EMR_RW})
        assert r.status_code == 400, (p, r.text)
        assert "other-secret" not in r.text

    # 書き込みも辿らない (leaf の symlink / 中間ディレクトリの symlink)
    for p in ("/ext/groups/EMR/leak.txt", "/ext/groups/EMR/dir/secret.txt"):
        r = c.post(
            "/files/write",
            json={"user_id": "u1", "path": p, "b64": _b64(b"pwn"), "mounts": EMR_RW},
        )
        assert r.status_code == 400, (p, r.text)
    assert (other / "secret.txt").read_text() == "other-secret"
    assert not (other / "new.txt").exists()

    # 一覧は symlink を出さず、ディレクトリ symlink にも降りない
    r = c.post(
        "/files/list",
        json={"user_id": "u1", "path": "/ext/groups/EMR", "mounts": EMR_RW},
    )
    assert r.json()["entries"] == [{"path": "/ext/groups/EMR/real/x.txt", "size": 2}]
    r = c.post(
        "/files/list",
        json={"user_id": "u1", "path": "/ext/groups/EMR/dir", "mounts": EMR_RW},
    )
    assert r.status_code == 400


def test_workspace_symlink_to_group_dir_is_refused(monkeypatch, tmp_path):
    """workspace (ユーザー単位) に置いた「グループの置き場を指す symlink」も辿らない
    (workspace 相対の `path` は `_safe_path` が workspace の外への解決を弾く)。"""
    mod, ext = _load(monkeypatch, tmp_path)
    c = _client(mod, tmp_path)
    ws = tmp_path / "workspaces" / "u1"
    ws.mkdir(parents=True)
    (ws / "x").symlink_to(ext / "groups" / "OTHER" / "secret.txt")
    r = c.post("/files/read", json={"user_id": "u1", "path": "x"})
    assert r.status_code == 400 and "other-secret" not in r.text


# ---------------------------------------------------------------------------
# /exec /exec_cell
# ---------------------------------------------------------------------------
def test_exec_passes_binds_and_rejects_bad_mounts(monkeypatch, tmp_path):
    mod, ext = _load(monkeypatch, tmp_path)
    c = _client(mod, tmp_path)
    (tmp_path / "workspaces" / "u1" / ".exec").mkdir(parents=True)
    (tmp_path / "workspaces" / "u1" / "exports").mkdir()
    seen = {}

    def fake_cmd(ws, t, argv, binds=()):
        seen["binds"] = binds
        return ["true"]

    monkeypatch.setattr(mod, "_nsjail_cmd", fake_cmd)
    r = c.post(
        "/exec",
        json={"user_id": "u1", "lang": "shell", "code": "x", "mounts": EMR_RW},
    )
    assert r.status_code == 200, r.text
    assert [(dst, rw) for _s, dst, rw in seen["binds"]] == [
        ("/ext/groups/EMR", True),
        ("/ext/public", False),
    ]
    r = c.post(
        "/exec",
        json={
            "user_id": "u1",
            "lang": "shell",
            "code": "x",
            "mounts": [_M("/ext/groups", True)],
        },
    )
    assert r.status_code == 400


def test_kernel_is_recreated_when_mounts_change(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path)

    class FakeProc:
        def __init__(self):
            self._rc = None
            self.stdin = types.SimpleNamespace(write=lambda b: None, flush=lambda: None)
            self.stdout = None

        def poll(self):
            return self._rc

        def kill(self):
            self._rc = -9

    seen = []

    async def fake_workspace(user_id):
        return tmp_path / user_id

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    monkeypatch.setattr(
        mod, "_kernel_nsjail_cmd", lambda ws, binds=(): seen.append(binds) or ["true"]
    )
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **k: FakeProc())
    mgr = mod.SessionManager()
    a = [("/x/EMR", "/ext/groups/EMR", True)]
    b = a + [("/x/NEW", "/ext/groups/NEW", True)]

    async def scenario():
        s1, r1 = await mgr.get("u", "c", a, mod._mounts_fingerprint(a))
        s2, r2 = await mgr.get("u", "c", a, mod._mounts_fingerprint(a))
        assert (r1, r2) == (False, False) and s1 is s2
        # グループに追加された = 見え方が変わった → 作り直して reset を返す
        s3, r3 = await mgr.get("u", "c", b, mod._mounts_fingerprint(b))
        assert r3 is True and s3 is not s1 and s1.proc.poll() is not None
        assert seen[-1] == b
        # rw → ro の変化も別の指紋
        ro = [(a[0][0], a[0][1], False)]
        assert mod._mounts_fingerprint(ro) != mod._mounts_fingerprint(a)

    asyncio.new_event_loop().run_until_complete(scenario())
