"""sandbox API の共有トークン認証の回帰 (#132)。

認証は **opt-in** (2026-09-05 に fail-closed から変更)。固定するのは 3 点:

1. **トークンを設定したら要認証** — ヘッダ無し / 誤トークン / 別スキーム / 未知パスは
   401。正しい Bearer だけ通る。
2. **公開パスは `/healthz` と `/metrics` だけ** (compose の healthcheck と Prometheus)。
3. **トークン未設定なら認証は掛からない** — 従来どおり素通しし、startup も落ちない
   (警告ログだけ出る)。「キーが無ければ元のまま」。

`test_session_manager.py` と同じく main.py を直接 import する (コンテナ不要)。
TestClient は context manager にしない — lifespan (= startup) を走らせないため。

実行:
    uv run task sandbox-unit
"""

import importlib.util
import pathlib
import sys

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
# opt-in (トークン未設定 = 認証なしで従来どおり)
# ---------------------------------------------------------------------------
def test_unset_token_disables_auth(monkeypatch, tmp_path):
    """未設定なら認証は掛からない — ヘッダ無しでも通る (「キーが無ければ元のまま」)。"""
    mod = _load_main(monkeypatch, tmp_path, token=None)
    assert mod.AUTH_ENABLED is False
    c = TestClient(mod.app)
    r = c.post("/files/list", json={"user_id": "u1", "path": "."})
    assert r.status_code == 200
    assert r.json() == {"entries": []}
    # 未知パスも middleware では止めない (routing に落ちて 404)
    assert c.get("/nope").status_code == 404


def test_unset_token_warns_at_startup(monkeypatch, tmp_path, capsys):
    """起動は拒否しない。ただし無認証であることが起動ログで分かる。

    `_startup()` そのものは loop ノードの mknod / reaper 起動まで走るので、
    起動ログを出す部分 (`_log_auth_state`) だけを呼ぶ。
    """
    mod = _load_main(monkeypatch, tmp_path, token=None)
    mod._log_auth_state()
    out = capsys.readouterr().out
    assert "SANDBOX_TOKEN" in out and "WARNING" in out

    mod = _load_main(monkeypatch, tmp_path)
    mod._log_auth_state()
    assert "WARNING" not in capsys.readouterr().out


def test_empty_bearer_is_not_a_match_when_auth_is_on(monkeypatch, tmp_path):
    """認証が有効なら、空トークン (`Bearer `) が正解になる経路は無い。"""
    mod = _load_main(monkeypatch, tmp_path)
    assert mod.AUTH_ENABLED is True
    assert mod._token_ok("Bearer ") is False
    assert (
        TestClient(mod.app)
        .post(
            "/files/list", json={"user_id": "u1"}, headers={"Authorization": "Bearer "}
        )
        .status_code
        == 401
    )
