"""sandbox API の共有トークン認証の回帰 (#132)。

固定するのは 3 点:

1. **既定は要認証** — ヘッダ無し / 誤トークン / 別スキームは 401。正しい Bearer だけ通る。
2. **公開パスは `/healthz` と `/metrics` だけ** (compose の healthcheck と Prometheus)。
3. **fail-closed** — `SANDBOX_TOKEN` 未設定なら startup が RuntimeError で落ちる
   (= uvicorn が終了しコンテナが crash-loop する)。未設定で素通しする経路は無い。

`test_session_manager.py` と同じく main.py を直接 import する (コンテナ不要)。
TestClient は context manager にしない — lifespan (= startup) を走らせないため。

実行:
    uv run task sandbox-unit
"""

import asyncio
import importlib.util
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

_MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"

TOKEN = "test-token-0123456789"


def _load_main(monkeypatch, tmp_path, token=TOKEN):
    """token 付き (既定) / 未設定 (token=None) で main.py を読み込む。"""
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(tmp_path))
    if token is None:
        monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SANDBOX_TOKEN", token)
    spec = importlib.util.spec_from_file_location("sandbox_main_auth_test", _MAIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    # 認証だけを見たいので workspace の実体 (loopback mount) は作らない
    async def fake_workspace(user_id):
        ws = tmp_path / user_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    monkeypatch.setattr(mod, "_workspace", fake_workspace)
    return mod


def _client(monkeypatch, tmp_path, token=TOKEN):
    return TestClient(_load_main(monkeypatch, tmp_path, token).app)


# ---------------------------------------------------------------------------
# 認証されない要求
# ---------------------------------------------------------------------------
def test_no_header_is_401(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post("/files/list", json={"user_id": "u1", "path": "."})
    assert r.status_code == 401
    assert TOKEN not in r.text  # トークン値をレスポンスに出さない


def test_wrong_token_is_401(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post(
        "/files/list",
        json={"user_id": "u1", "path": "."},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


def test_other_scheme_is_401(monkeypatch, tmp_path):
    """Basic 等の別スキームで token 相当を送っても通さない。"""
    c = _client(monkeypatch, tmp_path)
    r = c.post(
        "/files/list",
        json={"user_id": "u1", "path": "."},
        headers={"Authorization": f"Basic {TOKEN}"},
    )
    assert r.status_code == 401


def test_all_write_paths_require_token(monkeypatch, tmp_path):
    """実行・書き込み・破棄の全経路が 401 (認証は endpoint ごとの付け忘れに依らない)。"""
    c = _client(monkeypatch, tmp_path)
    calls = [
        ("post", "/exec", {"user_id": "u1", "lang": "python", "code": "print(1)"}),
        ("post", "/exec_cell", {"user_id": "u1", "chat_id": "c1", "code": "1"}),
        ("post", "/files/read", {"user_id": "u1", "path": "a.txt"}),
        ("post", "/files/write", {"user_id": "u1", "path": "a.txt", "b64": "eA=="}),
        ("delete", "/workspace/u1", None),
    ]
    for method, path, payload in calls:
        r = getattr(c, method)(path, **({"json": payload} if payload else {}))
        assert r.status_code == 401, f"{method} {path} が 401 でない: {r.status_code}"


def test_unknown_path_is_401_not_404(monkeypatch, tmp_path):
    """既定 deny は routing より前段 (middleware)。新 endpoint の付け忘れで開かない。"""
    c = _client(monkeypatch, tmp_path)
    assert c.get("/nope").status_code == 401


# ---------------------------------------------------------------------------
# 認証された要求 / 公開パス
# ---------------------------------------------------------------------------
def test_correct_token_passes(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post(
        "/files/list",
        json={"user_id": "u1", "path": "."},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json() == {"entries": []}


def test_healthz_and_metrics_are_public(monkeypatch, tmp_path):
    """compose の healthcheck / Prometheus が token 無しで叩ける (PUBLIC_PATHS)。"""
    c = _client(monkeypatch, tmp_path)
    # loop ドライバの有無で 200/503 が変わるので「401 でない」ことだけ見る
    assert c.get("/healthz").status_code in (200, 503)
    m = c.get("/metrics")
    assert m.status_code == 200
    assert "sandbox_loop_used" in m.text


# ---------------------------------------------------------------------------
# fail-closed
# ---------------------------------------------------------------------------
def test_startup_refuses_without_token(monkeypatch, tmp_path):
    mod = _load_main(monkeypatch, tmp_path, token=None)
    with pytest.raises(RuntimeError, match="SANDBOX_TOKEN"):
        asyncio.new_event_loop().run_until_complete(mod._startup())


def test_unset_token_never_lets_a_request_through(monkeypatch, tmp_path):
    """起動チェックを迂回して app を立てても、空 token での素通しは無い。"""
    c = _client(monkeypatch, tmp_path, token=None)
    assert c.post("/files/list", json={"user_id": "u1"}).status_code == 401
    assert (
        c.post(
            "/files/list", json={"user_id": "u1"}, headers={"Authorization": "Bearer "}
        ).status_code
        == 401
    )
