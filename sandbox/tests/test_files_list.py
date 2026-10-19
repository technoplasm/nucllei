"""`/files/list` が「無い」「ファイル」「空」を言い分けること (#145)。

旧版はどれも `entries=[]` で、tool は「空 (ファイルが無いか、ディレクトリが存在しない)」と
しか言えず、存在しないディレクトリの一覧が緑の結果になっていた (ken、staging)。
`exists` / `is_dir` は追加フィールド — 読まない旧 nucllei とも互換。

あわせて `/files/write` `/exec` `/exec_cell` の応答 `cache_ttl_days` (#149 — imports/ exports/
の期限を nucllei に渡す) と、exports/ の自動回収を廃止したこと (2026-10-19) もここで見る。

`test_auth.py` と同じく main.py を直接 import する (コンテナ不要)。

実行:
    uv run task sandbox-unit
"""

import base64
import importlib.util
import os
import pathlib
import sys

from fastapi.testclient import TestClient

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path))
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    spec = importlib.util.spec_from_file_location("sandbox_main_files_test", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    # workspace の実体 (loopback mount) は作らず、tmp_path 配下のディレクトリで代える
    async def fake_workspace(user_id):
        ws = tmp_path / user_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    return TestClient(mod.app)


def test_missing_file_empty_and_listed_are_told_apart(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    ws = tmp_path / "u1"
    (ws / "empty").mkdir(parents=True)
    (ws / "hidden_only").mkdir()
    (ws / "hidden_only" / ".cache").write_text("x")
    (ws / "data").mkdir()
    (ws / "data" / "a.csv").write_text("x,y")

    def listed(path):
        r = c.post("/files/list", json={"user_id": "u1", "path": path})
        assert r.status_code == 200, r.text
        return r.json()

    assert listed("nope") == {"entries": [], "exists": False, "is_dir": False}
    assert listed("data/a.csv") == {"entries": [], "exists": True, "is_dir": False}
    assert listed("empty") == {"entries": [], "exists": True, "is_dir": True}
    # `.` 始まりしか無いディレクトリは「空」(見せるファイルが無い)
    assert listed("hidden_only") == {"entries": [], "exists": True, "is_dir": True}
    assert listed("data") == {
        "entries": [{"path": "data/a.csv", "size": 3}],
        "exists": True,
        "is_dir": True,
    }
    assert listed(".")["entries"] == [{"path": "data/a.csv", "size": 3}]


def test_files_write_reports_cache_ttl(monkeypatch, tmp_path):
    """#149: `/files/write` は imports/ exports/ の期限 `cache_ttl_days` を返す
    (nucllei が取り込みの tool 出力で「YYYY-MM-DD 以降に消える」を知らせる)。0 = 消さない。"""
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)
    for env, want in ((None, 30), ("5", 5), ("0", 0), ("-3", 0)):
        if env is None:
            monkeypatch.delenv("SANDBOX_CACHE_TTL_DAYS", raising=False)
        else:
            monkeypatch.setenv("SANDBOX_CACHE_TTL_DAYS", env)
        c = _client(monkeypatch, tmp_path)
        r = c.post(
            "/files/write",
            json={"user_id": "u1", "path": "imports/a.txt", "b64": "eA=="},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "size": 1, "cache_ttl_days": want}


def test_exec_and_exec_cell_report_cache_ttl_without_exports(monkeypatch, tmp_path):
    """#149: `/exec` `/exec_cell` は `cache_ttl_days` を返す。exports/ の実行前後差分の回収
    (チャットへの自動添付) は廃止 (2026-10-19) — exports/ に書いても応答に `exports` キーは出ない。
    古い nucllei が送る `file_limit_bytes` (自動添付の上限だった) は 422 にせず受け流す。
    nsjail / kernel は差し替える。"""
    import asyncio

    monkeypatch.setenv("SANDBOX_CACHE_TTL_DAYS", "12")
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    c = _client(monkeypatch, tmp_path)
    mod = sys.modules["sandbox_main_files_test"]
    (tmp_path / "u1" / ".exec").mkdir(parents=True)
    (tmp_path / "u1" / "exports").mkdir()
    # nsjail の代わりに exports/ へ 1 つ書くだけのコマンド
    out_file = tmp_path / "u1" / "exports" / "r.txt"
    monkeypatch.setattr(
        mod,
        "_nsjail_cmd",
        lambda ws, t, argv, binds=(): ["sh", "-c", f"echo hi > {out_file}"],
    )
    for extra in ({}, {"file_limit_bytes": 5}):
        out_file.unlink(missing_ok=True)
        r = c.post(
            "/exec", json={"user_id": "u1", "lang": "shell", "code": "x", **extra}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cache_ttl_days"] == 12
        assert "exports" not in body
        assert out_file.read_text() == "hi\n"  # 書けてはいる (回収しないだけ)

    class FakeSession:
        lock = asyncio.Lock()
        last_used = 0.0

        def _send(self, code, timeout):
            pass

        def _recv(self):
            return {"stdout": "1\n"}

    async def fake_get(user_id, chat_id, *a, **k):
        return FakeSession(), False

    monkeypatch.setattr(mod._sessions, "get", fake_get)
    for extra in ({}, {"file_limit_bytes": 5}):
        r = c.post(
            "/exec_cell",
            json={"user_id": "u1", "chat_id": "c1", "code": "1", **extra},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cache_ttl_days"] == 12 and body["stdout"] == "1\n"
        assert "exports" not in body
    # kernel が落ちた (EOF) 応答にも出ない
    monkeypatch.setattr(mod._sessions, "drop", lambda sess: asyncio.sleep(0))
    monkeypatch.setattr(FakeSession, "_recv", lambda self: None)
    r = c.post("/exec_cell", json={"user_id": "u1", "chat_id": "c1", "code": "1"})
    assert r.status_code == 200, r.text
    assert r.json()["session_reset"] and "exports" not in r.json()
    assert not hasattr(mod, "_collect_exports") and not hasattr(mod, "_snapshot_exports")


# --- 1 ファイルの上限: nucllei が渡す `file_limit_bytes` と sandbox の上限の小さいほう ------------
# nucllei の RC ATTACHMENT_MAX_SIZE_MB がリクエストごとに来る。sandbox の env
# SANDBOX_FILE_MAX_SIZE_MB (既定 1024) は b64 の経路では安全上限として効く。


def _ws_file(tmp_path, rel, data):
    p = tmp_path / "u1" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_sandbox_limit_default_is_1024_mb(monkeypatch, tmp_path):
    monkeypatch.delenv("SANDBOX_FILE_MAX_SIZE_MB", raising=False)
    c = _client(monkeypatch, tmp_path)
    mod = sys.modules["sandbox_main_files_test"]
    assert mod.FILE_MAX_SIZE_MB == 1024
    assert mod.FILE_LIMIT == 1024 * 1024 * 1024
    # /healthz と /metrics に出す (nucllei が起動時に読む)
    monkeypatch.setattr(mod, "_loop_available", lambda: True)
    assert c.get("/healthz").json()["file_max_size_mb"] == 1024
    monkeypatch.setattr(mod, "WORKSPACES", tmp_path)
    monkeypatch.setattr(mod, "IMAGES", tmp_path / ".images")
    assert f"sandbox_file_max_size_bytes {1024 * 1024 * 1024}" in c.get("/metrics").text


def test_sandbox_limit_env_is_mb_and_old_name_only_warns(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SANDBOX_FILE_MAX_SIZE_MB", "3")
    monkeypatch.setenv("SANDBOX_FILE_LIMIT_BYTES", "5")
    _client(monkeypatch, tmp_path)
    mod = sys.modules["sandbox_main_files_test"]
    # 旧名は読み替えない (値は新名のまま)
    assert mod.FILE_LIMIT == 3 * 1024 * 1024
    monkeypatch.setattr(mod, "_ensure_loop_nodes", lambda: None)
    monkeypatch.setattr(mod, "_loop_available", lambda: True)
    monkeypatch.setattr(mod, "WS_RETENTION_DAYS", 0)
    monkeypatch.setattr(mod.asyncio, "create_task", lambda coro: coro.close())
    import asyncio

    asyncio.run(mod._startup())
    out = capsys.readouterr().out
    assert "SANDBOX_FILE_LIMIT_BYTES は廃止 (読まない)" in out
    assert "SANDBOX_FILE_MAX_SIZE_MB" in out


def test_read_uses_smaller_of_requested_and_safety(monkeypatch, tmp_path, capsys):
    c = _client(monkeypatch, tmp_path)
    mod = sys.modules["sandbox_main_files_test"]
    monkeypatch.setattr(mod, "FILE_LIMIT", 8)
    _ws_file(tmp_path, "a.bin", b"x" * 6)

    def read(**extra):
        return c.post("/files/read", json={"user_id": "u1", "path": "a.bin", **extra})

    # 渡されない (無制限) → sandbox の上限だけ
    assert read().status_code == 200
    # 渡された値が小さければそれ。文面は nucllei が読む形
    r = read(file_limit_bytes=5)
    assert r.status_code == 413 and "file too large: 6 bytes (limit 5)" in r.text
    r = read(file_limit_bytes=5, nofollow=True)
    assert r.status_code == 413 and "(limit 5)" in r.text
    assert read(file_limit_bytes=6).status_code == 200
    assert "WARNING" not in capsys.readouterr().out
    # sandbox の上限を超える値 → sandbox の上限が勝つ + warning は値が変わったときだけ
    _ws_file(tmp_path, "b.bin", b"x" * 9)
    for _ in range(3):
        r = c.post(
            "/files/read",
            json={"user_id": "u1", "path": "b.bin", "file_limit_bytes": 100},
        )
        assert r.status_code == 413 and "(limit 8)" in r.text
    assert capsys.readouterr().out.count("sandbox の上限が勝つ") == 1
    read(file_limit_bytes=200)
    assert capsys.readouterr().out.count("sandbox の上限が勝つ") == 1


def test_write_and_exec_imports_follow_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(os, "fchown", lambda *a, **k: None)
    c = _client(monkeypatch, tmp_path)
    b64 = base64.b64encode(b"x" * 6).decode()
    r = c.post(
        "/files/write",
        json={"user_id": "u1", "path": "imports/a", "b64": b64, "file_limit_bytes": 5},
    )
    assert r.status_code == 413 and "(limit 5)" in r.text
    r = c.post("/files/write", json={"user_id": "u1", "path": "imports/a", "b64": b64})
    assert r.status_code == 200
    r = c.post(
        "/exec",
        json={
            "user_id": "u1",
            "lang": "shell",
            "code": "true",
            "imports": [{"name": "a", "b64": b64}],
            "file_limit_bytes": 5,
        },
    )
    assert r.status_code == 413 and "(limit 5)" in r.text
