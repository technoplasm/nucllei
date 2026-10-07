"""`/files/list` が「無い」「ファイル」「空」を言い分けること (#145)。

旧版はどれも `entries=[]` で、tool は「空 (ファイルが無いか、ディレクトリが存在しない)」と
しか言えず、存在しないディレクトリの一覧が緑の結果になっていた (ken、staging)。
`exists` / `is_dir` は追加フィールド — 読まない旧 nucllei とも互換。

あわせて `/files/write` `/exec` `/exec_cell` の応答 `cache_ttl_days` (#149 — imports/ exports/
の期限を nucllei に渡す) もここで見る。

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


def test_exec_and_exec_cell_report_cache_ttl(monkeypatch, tmp_path):
    """#149: exports を返す `/exec` `/exec_cell` も `cache_ttl_days` を返す (自動添付の
    tool 出力で workspace 側の写しの期限を知らせるため)。nsjail / kernel は差し替える。"""
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
    r = c.post("/exec", json={"user_id": "u1", "lang": "shell", "code": "x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cache_ttl_days"] == 12
    assert [e["name"] for e in body["exports"]] == ["r.txt"]

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
    r = c.post("/exec_cell", json={"user_id": "u1", "chat_id": "c1", "code": "1"})
    assert r.status_code == 200, r.text
    assert r.json()["cache_ttl_days"] == 12 and r.json()["stdout"] == "1\n"


# --- 1 ファイルの上限: nucllei が渡す `file_limit_bytes` と安全上限の小さいほう ------------------
# nucllei の RC ATTACHMENT_MAX_SIZE_MB がリクエストごとに来る。sandbox の env
# SANDBOX_FILE_LIMIT_BYTES (既定 1 GiB) は OOM 防止の安全上限としてだけ残る。


def _ws_file(tmp_path, rel, data):
    p = tmp_path / "u1" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_safety_limit_default_is_1gib(monkeypatch, tmp_path):
    monkeypatch.delenv("SANDBOX_FILE_LIMIT_BYTES", raising=False)
    c = _client(monkeypatch, tmp_path)
    mod = sys.modules["sandbox_main_files_test"]
    assert mod.FILE_LIMIT == 1024 * 1024 * 1024
    # /healthz と /metrics に出す (nucllei が起動時に読んで自分の上限と比べる)
    monkeypatch.setattr(mod, "_loop_available", lambda: True)
    assert c.get("/healthz").json()["file_limit_bytes"] == 1024 * 1024 * 1024
    monkeypatch.setattr(mod, "WORKSPACES", tmp_path)
    monkeypatch.setattr(mod, "IMAGES", tmp_path / ".images")
    assert f"sandbox_file_limit_bytes {1024 * 1024 * 1024}" in c.get("/metrics").text


def test_read_uses_smaller_of_requested_and_safety(monkeypatch, tmp_path, capsys):
    c = _client(monkeypatch, tmp_path)
    mod = sys.modules["sandbox_main_files_test"]
    monkeypatch.setattr(mod, "FILE_LIMIT", 8)
    _ws_file(tmp_path, "a.bin", b"x" * 6)

    def read(**extra):
        return c.post("/files/read", json={"user_id": "u1", "path": "a.bin", **extra})

    # 渡されない (古い nucllei・無制限) → 安全上限だけ
    assert read().status_code == 200
    # 渡された値が小さければそれ。文面は nucllei が読む形
    r = read(file_limit_bytes=5)
    assert r.status_code == 413 and "file too large: 6 bytes (limit 5)" in r.text
    r = read(file_limit_bytes=5, nofollow=True)
    assert r.status_code == 413 and "(limit 5)" in r.text
    assert read(file_limit_bytes=6).status_code == 200
    assert "WARNING" not in capsys.readouterr().out
    # 安全上限を超える値 → 安全上限が勝つ + warning は値が変わったときだけ
    _ws_file(tmp_path, "b.bin", b"x" * 9)
    for _ in range(3):
        r = c.post(
            "/files/read",
            json={"user_id": "u1", "path": "b.bin", "file_limit_bytes": 100},
        )
        assert r.status_code == 413 and "(limit 8)" in r.text
    assert capsys.readouterr().out.count("安全上限が勝つ") == 1
    read(file_limit_bytes=200)
    assert capsys.readouterr().out.count("安全上限が勝つ") == 1


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


def test_exports_over_limit_are_truncated_with_limit(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    mod = sys.modules["sandbox_main_files_test"]
    ws = tmp_path / "u1"
    _ws_file(tmp_path, "exports/big.bin", b"x" * 6)
    _ws_file(tmp_path, "exports/ok.txt", b"hi")
    out = {e["name"]: e for e in mod._collect_exports(ws, {}, mod._file_limit(5))}
    assert out["big.bin"]["truncated"] and out["big.bin"]["limit"] == 5
    assert "b64" not in out["big.bin"]
    assert out["ok.txt"]["b64"] == base64.b64encode(b"hi").decode()
    # 渡されない → 安全上限
    monkeypatch.setattr(mod, "FILE_LIMIT", 4)
    out = {e["name"]: e for e in mod._collect_exports(ws, {}, mod._file_limit(None))}
    assert out["big.bin"]["limit"] == 4 and "b64" in out["ok.txt"]
