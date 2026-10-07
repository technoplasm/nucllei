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
