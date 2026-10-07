"""`/files/raw` (生のバイト列のアップロード / ダウンロード) と `/files/read` の範囲読み。

固定すること:

- PUT はチャンクで受けて一時ファイル (`.upload-*.part`) に書き、全部受けてから rename で確定する。
  上限超過 (413)・容量切れ (507)・`exclusive` の衝突 (409) では一時ファイルを残さず、元のファイルは
  変わらない。Content-Length が上限を超えていれば本文を読まずに 413
- 上限は渡された `file_limit_bytes` と sandbox の上限 (SANDBOX_FILE_MAX_SIZE_MB) の小さいほう
- パスは `/files/*` の fd 経路と同じ: 経路の symlink は 400、末端の symlink はリンクそのものを
  置き換える (向こう側を書かない)、ro の bind は 403、bind に無いものは 404、新しい段は作る
- GET は Content-Length 付きでそのまま返す。上限超過は 413、symlink は辿らない
- `/files/read` の `offset` / `length`: 上限は読む量に掛かり、ファイル全体の大きさで断らない。
  `size` は全体のバイト数

`test_files_browser.py` と同じく main.py を直接 import する (コンテナ不要)。

実行:
    uv run task sandbox-unit
"""

import base64
import errno
import importlib.util
import json
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
    spec = importlib.util.spec_from_file_location("sandbox_main_raw", _MAIN)
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
    (ext / "public").mkdir()
    monkeypatch.setattr(mod, "RO_MOUNT_ROOT", ext)
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)
    ws = ws_root / "u1"
    (ws / "imports").mkdir(parents=True)
    return mod, TestClient(mod.app), ws, ext


MOUNTS = json.dumps(
    [{"path": "/ext/public", "rw": False}, {"path": "/ext/groups/EMR", "rw": True}]
)


def _put(c, path, data, **params):
    return c.put(
        "/files/raw",
        params={"user_id": "u1", "path": path, **params},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def _get(c, path, **params):
    return c.get("/files/raw", params={"user_id": "u1", "path": path, **params})


def _leftovers(d: pathlib.Path):
    return [p.name for p in d.rglob(".upload-*")]


def test_put_writes_in_chunks_and_commits(env):
    mod, c, ws, _ = env

    def chunks():
        for i in range(5):
            yield bytes([65 + i]) * 1000

    r = _put(c, "data/new/a.bin", chunks())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["size"] == 5000 and body["path"] == "data/new/a.bin"
    assert isinstance(body["mtime"], float) and "cache_ttl_days" in body
    assert (ws / "data" / "new" / "a.bin").read_bytes() == b"".join(chunks())
    assert _leftovers(ws) == []
    # 上書き (既定) と exclusive
    assert _put(c, "data/new/a.bin", b"v2").status_code == 200
    assert (ws / "data" / "new" / "a.bin").read_bytes() == b"v2"
    r = _put(c, "data/new/a.bin", b"v3", exclusive="true")
    assert r.status_code == 409 and r.json()["detail"]["message"] == "already exists"
    assert (ws / "data" / "new" / "a.bin").read_bytes() == b"v2"
    assert _put(c, "data/new/b.bin", b"x", exclusive="true").status_code == 200
    assert _leftovers(ws) == []


def test_put_limit_removes_the_temp_and_keeps_the_old_file(env, monkeypatch):
    mod, c, ws, _ = env
    (ws / "a.bin").write_bytes(b"old")
    monkeypatch.setattr(mod, "FILE_LIMIT", 10)

    def chunks():
        yield b"x" * 6
        yield b"x" * 6

    # Content-Length の無い (chunked) 本文 → 受けながら数えて 413
    r = _put(c, "a.bin", chunks())
    assert r.status_code == 413 and "(limit 10)" in r.text
    assert (ws / "a.bin").read_bytes() == b"old" and _leftovers(ws) == []
    # Content-Length が上限超え → 本文を読まずに 413
    r = _put(c, "a.bin", b"x" * 11)
    assert r.status_code == 413 and "file too large: 11 bytes (limit 10)" in r.text
    assert _leftovers(ws) == []
    # 渡された file_limit_bytes が小さければそれ
    r = _put(c, "a.bin", b"x" * 6, file_limit_bytes=5)
    assert r.status_code == 413 and "(limit 5)" in r.text
    assert _put(c, "a.bin", b"x" * 10).status_code == 200


def test_put_quota_full_is_507_without_leftovers(env, monkeypatch):
    mod, c, ws, _ = env

    def full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(mod, "_write_all", full)
    r = _put(c, "big.bin", b"x" * 100)
    assert r.status_code == 507
    assert not (ws / "big.bin").exists() and _leftovers(ws) == []


def test_put_paths_follow_the_fd_rules(env, tmp_path):
    mod, c, ws, ext = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "t.txt").write_text("keep")
    (ws / "link").symlink_to(outside)
    (ws / "leaf.txt").symlink_to(outside / "t.txt")
    # 経路の symlink は辿らない
    assert _put(c, "link/x.txt", b"x").status_code == 400
    assert not (outside / "x.txt").exists()
    # 末端の symlink は 400 (向こう側を書かない)
    assert _put(c, "leaf.txt", b"x").status_code == 400
    assert (outside / "t.txt").read_text() == "keep"
    # ディレクトリそのもの
    assert _put(c, "imports", b"x").status_code == 400
    assert _put(c, "../x", b"x").status_code == 400
    # /ext: rw の bind には書ける、ro は 403、bind に無いものは 404
    r = _put(c, "/ext/groups/EMR/sub/a.csv", b"a,b", mounts=MOUNTS)
    assert r.status_code == 200 and r.json()["path"] == "/ext/groups/EMR/sub/a.csv"
    assert (ext / "groups" / "EMR" / "sub" / "a.csv").read_bytes() == b"a,b"
    assert _put(c, "/ext/public/a.csv", b"x", mounts=MOUNTS).status_code == 403
    assert _put(c, "/ext/groups/OTHER/a.csv", b"x", mounts=MOUNTS).status_code == 404
    assert _put(c, "/ext/groups/EMR/a", b"x", mounts="{").status_code == 400
    assert _leftovers(ws) == [] and _leftovers(ext) == []


def test_get_streams_with_content_length(env, monkeypatch, tmp_path):
    mod, c, ws, ext = env
    data = os.urandom(3 * 1024 * 1024 + 17)
    (ws / "big.bin").write_bytes(data)
    r = _get(c, "big.bin")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-length"] == str(len(data))
    assert r.headers["content-type"] == "application/octet-stream"
    (ext / "public" / "p.txt").write_text("pub")
    assert _get(c, "/ext/public/p.txt", mounts=MOUNTS).content == b"pub"
    assert _get(c, "/ext/groups/OTHER/x", mounts=MOUNTS).status_code == 404
    assert _get(c, "nope.bin").status_code == 404
    assert _get(c, "imports").status_code == 404
    outside = tmp_path / "o.txt"
    outside.write_text("secret")
    (ws / "l.txt").symlink_to(outside)
    assert _get(c, "l.txt").status_code == 400
    monkeypatch.setattr(mod, "FILE_LIMIT", 100)
    r = _get(c, "big.bin")
    assert r.status_code == 413 and "(limit 100)" in r.text


# --- /files/read の範囲読み ------------------------------------------------------------------


@pytest.mark.parametrize("nofollow", [False, True])
def test_read_range_is_not_refused_by_the_whole_size(env, monkeypatch, nofollow):
    mod, c, ws, _ = env
    data = bytes(range(256)) * 40  # 10240 バイト
    (ws / "a.log").write_bytes(data)
    monkeypatch.setattr(mod, "FILE_LIMIT", 1000)

    def read(**extra):
        return c.post(
            "/files/read",
            json={"user_id": "u1", "path": "a.log", "nofollow": nofollow, **extra},
        )

    # 全体は上限超え
    assert read().status_code == 413
    r = read(offset=5000, length=600)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["size"] == 10240 and body["offset"] == 5000 and body["length"] == 600
    assert base64.b64decode(body["b64"]) == data[5000:5600]
    # 末尾を越える範囲は短く返る
    body = read(offset=10000, length=600).json()
    assert body["length"] == 240 and base64.b64decode(body["b64"]) == data[10000:]
    body = read(offset=20000, length=10).json()
    assert body["length"] == 0 and body["b64"] == ""
    # 上限は読む量に掛かる
    r = read(offset=0, length=1001)
    assert r.status_code == 413 and "(limit 1000)" in r.text
    assert read(offset=0, length=10, file_limit_bytes=5).status_code == 413
    assert read(offset=-1, length=10).status_code == 400


def test_read_range_on_ext(env):
    mod, c, ws, ext = env
    (ext / "groups" / "EMR" / "t.txt").write_bytes(b"0123456789")
    r = c.post(
        "/files/read",
        json={
            "user_id": "u1",
            "path": "/ext/groups/EMR/t.txt",
            "mounts": json.loads(MOUNTS),
            "offset": 3,
            "length": 4,
        },
    )
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["b64"]) == b"3456" and r.json()["size"] == 10
