"""`/files/list` が「無い」「ファイル」「空」を言い分けること (#145)。

旧版はどれも `entries=[]` で、tool は「空 (ファイルが無いか、ディレクトリが存在しない)」と
しか言えず、存在しないディレクトリの一覧が緑の結果になっていた (ken、staging)。
`exists` / `is_dir` は追加フィールド — 読まない旧 nucllei とも互換。

`test_auth.py` と同じく main.py を直接 import する (コンテナ不要)。

実行:
    uv run task sandbox-unit
"""

import importlib.util
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
