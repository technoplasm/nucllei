"""sandbox API の共有トークン認証の回帰 (#132)。

認証は **opt-in** (2026-09-05 に fail-closed から変更)。固定するのは 3 点:

1. **トークンを設定したら要認証** — ヘッダ無し / 誤トークン / 別スキーム / 未知パスは
   401。正しい Bearer だけ通る。
2. **公開パスは `/healthz` と `/metrics` だけ** (compose の healthcheck と Prometheus)。
3. **トークン未設定なら認証は掛からない** — 従来どおり素通しし、startup も落ちない
   (警告ログだけ出る)。「キーが無ければ元のまま」。
4. **免除は完全一致だけ** — `/healthz/` `//healthz` `/HEALTHZ` `/healthz/../…` 等の
   似た形は 401。免除判定を prefix 一致や正規化に変えたら落ちる。
5. **非 ASCII のトークン**は HTTP ヘッダの latin-1 解釈のため一致せず全要求が 401 に
   なるので、起動ログで理由を名指しする (起動は止めない)。

`test_session_manager.py` と同じく main.py を直接 import する (コンテナ不要)。
TestClient は context manager にしない — lifespan (= startup) を走らせないため。

実行:
    uv run task sandbox-unit
"""

import asyncio
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


# ---------------------------------------------------------------------------
# 免除パスの形状 — 「完全一致だけが免除」を固定する
# ---------------------------------------------------------------------------
# 現実装の免除判定は `scope["path"] not in PUBLIC_PATHS` = 完全一致なので、以下は
# すべて閉じている。ここを `startswith` や自前の正規化に変えた瞬間に**静かに開く**
# ので、開かないことをテストで宣言しておく (レビュー指摘 #132)。
#
# **ASGI を直接叩く** (TestClient を使わない): httpx はクライアント側で URL を正規化して
# しまい、`//healthz` を protocol-relative URL (host=healthz, path=/) と見なし、
# `/healthz/../files/list` を `/files/list` に解決するため、これらの形が middleware に
# 届かない。uvicorn はリクエストラインを percent-decode するだけで dot-segment を畳まない
# ので、実配備で middleware が見るのは下の生パスそのもの。
PUBLIC_PATH_LOOKALIKES = [
    "/healthz/",  # 末尾スラッシュ
    "//healthz",  # 先頭二重スラッシュ
    "/HEALTHZ",  # 大文字 (パスは case-sensitive)
    "/healthz/../files/list",  # 相対参照で免除パスを頭に付ける (uvicorn は畳まない)
    "/healthz%2f../files/list",  # 同上をパーセントエンコードのまま渡した形
    "/metrics/",  # 末尾スラッシュ (/metrics 側)
]


def _asgi_status(
    app, path: str, headers: list[tuple[bytes, bytes]] | None = None
) -> int:
    """`scope["path"]` を生のまま指定して ASGI アプリを 1 回叩き、ステータスを返す。"""
    got = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            got["status"] = message["status"]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 51000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    return got["status"]


def test_public_path_lookalikes_are_401(monkeypatch, tmp_path):
    """免除は `/healthz` `/metrics` への**完全一致だけ**。似た形のパスは通さない。

    末尾スラッシュ・二重スラッシュ・大文字・`..` の混入 (パーセントエンコード由来も
    含む) のいずれでも認証を迂回できないこと (= 免除判定を prefix 一致や自前の正規化に
    変えたらこのテストが落ちる)。
    """
    mod = _load_main(monkeypatch, tmp_path)
    for path in PUBLIC_PATH_LOOKALIKES:
        status = _asgi_status(mod.app, path)
        assert status == 401, f"{path} が 401 でない: {status}"


def test_public_path_lookalikes_pass_with_token(monkeypatch, tmp_path):
    """上記が 401 なのは「認証で止めている」から (パス自体が壊れているからではない)。

    正しい Bearer を付ければ middleware は通り、routing の結果 (404 / 405 / 307 等)
    が返る。
    """
    mod = _load_main(monkeypatch, tmp_path)
    auth = [(b"authorization", f"Bearer {TOKEN}".encode())]
    for path in PUBLIC_PATH_LOOKALIKES:
        status = _asgi_status(mod.app, path, auth)
        assert status != 401, f"{path} が token 付きで 401"


def test_exact_public_paths_are_exempt(monkeypatch, tmp_path):
    """同じ経路で「完全一致は素通し」も見る (上の 401 が全部落としているだけではない)。"""
    mod = _load_main(monkeypatch, tmp_path)
    assert _asgi_status(mod.app, "/healthz") in (200, 503)
    assert _asgi_status(mod.app, "/metrics") == 200


def test_duplicate_authorization_header_uses_first(monkeypatch, tmp_path):
    """`Authorization` が重複したら**先頭勝ち** (Starlette の Headers.get の挙動)。

    誤 → 正の順で 2 本送っても、後から正しい値を足して通す経路にはならない。
    正 → 誤の順は通る (先頭が正しいため)。どちらも「先頭だけを見る」の帰結。
    """
    c = _client(monkeypatch, tmp_path)
    body = {"user_id": "u1", "path": "."}
    wrong_first = c.post(
        "/files/list",
        json=body,
        headers=[
            ("Authorization", "Bearer wrong"),
            ("Authorization", f"Bearer {TOKEN}"),
        ],
    )
    assert wrong_first.status_code == 401
    right_first = c.post(
        "/files/list",
        json=body,
        headers=[
            ("Authorization", f"Bearer {TOKEN}"),
            ("Authorization", "Bearer wrong"),
        ],
    )
    assert right_first.status_code == 200


# ---------------------------------------------------------------------------
# 非 ASCII トークン (ヘッダは latin-1 で解釈されるため一致しない)
# ---------------------------------------------------------------------------
def test_non_ascii_token_warns_at_startup(monkeypatch, tmp_path, capsys):
    """非 ASCII のトークンは「設定したのに全要求 401」になるので起動ログで名指しする。

    起動は止めない (opt-in を壊さない)。認証自体は有効のまま = 安全側に倒れる。
    ASCII のトークンではこの warning を出さない。
    """
    mod = _load_main(monkeypatch, tmp_path, token="tokén-非ascii")
    assert mod.AUTH_ENABLED is True
    assert mod.TOKEN_IS_ASCII is False
    mod._log_auth_state()
    out = capsys.readouterr().out
    assert "WARNING" in out and "ASCII" in out

    mod = _load_main(monkeypatch, tmp_path)  # ASCII の既定トークン
    assert mod.TOKEN_IS_ASCII is True
    mod._log_auth_state()
    assert "WARNING" not in capsys.readouterr().out


def test_unknown_path_with_token_is_404(monkeypatch, tmp_path):
    """認証を通ると routing に落ちて 404。

    配備側の監査 (llens の `scripts/preflight/audit.sh`) が **positive control** に使う:
    トークン無しで 401 だけでは「全部落ちている」と区別できないため、同じ存在しない
    パスにトークンを付けて 404 が返ることで「トークンが受理された」ことまで示す。
    副作用のあるパス (`/exec` 等) を叩かずに両方向を取れる形なので、**契約として維持する**
    (`sandbox/README.md` セキュリティモデル節に明記)。
    """
    c = _client(monkeypatch, tmp_path)
    assert c.get("/nope").status_code == 401
    assert (
        c.get("/nope", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404
    )
