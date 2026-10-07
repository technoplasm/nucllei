"""`resolve_ref` の失敗理由(`RefError.code`)の番兵。

`load_ref` が None しか返さないと、呼び手(feed 側の tool / 送信直前 inline)は
「上流不達・不在・redirect・サイズ超過」を束ねた 1 つの失敗しか出せない — 実走で
32.6 MB の PDF が FETCH_MAX_BYTES(25 MB)に当たり、モデルが「リンク切れ」と
誤報した。ここでは code ごとに 1 本ずつ、経路(file / http / workspace)が違っても
同じ code に落ちることを固定する。成功経路と "(image unavailable)" への合流は
test_file_images_ext.py が持つ(そちらは無変更で通ること = `load_ref` の互換)。

DB は要らないので plain な pytest 関数(AbstractPostgresTest を継承しない)。
"""

import asyncio
import base64
import os

import httpx
import pytest

from test.util.ext_mounts import mount_tree

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
SELF = "https://llens.test"
# 一般の URL のホスト(DNS は `_self` の差し替えで 172.16.5.5 に引く。実ネットワークに出ない)
GENERAL = "http://intra.test"


def _resolve(ref, user_id="alice", **kw):
    from nucllei.utils.image_refs import resolve_ref

    return asyncio.run(resolve_ref(ref, user_id, **kw))


def _code(ref, user_id="alice", **kw):
    loaded, error = _resolve(ref, user_id, **kw)
    assert loaded is None
    assert error is not None
    return error


def _install_transport(monkeypatch, handler):
    from nucllei.utils import ext_upstreams

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    monkeypatch.setattr(ext_upstreams, "_client", client)


# sandbox 側のパス(= URL と同じ文字列)。nucllei 側の実体は `EXT_MOUNTS` の静的 mount
# (テストは `_mount` が tmp の木の各ディレクトリを mount にする)。`/mnt` は 2026-10-03 に捨てた
MOUNTED = "/ext/cage/static/a.png"


def _mount(monkeypatch, tmp_path):
    """tmp の木 `root/<name>/…` を `EXT_MOUNTS` の静的 mount に(= `/ext/` の配信元 =
    sandbox の `/ext` と同じ組)。"""
    root = tmp_path / "data" / "ext"
    (root / "cage" / "static").mkdir(parents=True)
    (root / "cage" / "static" / "a.png").write_bytes(PNG_BYTES)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    (root / "cage" / "static" / "escape.png").symlink_to(outside)
    mount_tree(monkeypatch, root)
    return root


def _install_sandbox(monkeypatch, files: dict, url="http://sandbox.test", fail=None):
    from nucllei.builtin_tools import sandbox

    async def fake_post(path, payload, timeout_s):
        if fail is not None:
            raise fail
        data = files.get(payload["path"])
        if data is None:
            raise RuntimeError("sandbox HTTP 404: not a file: " + payload["path"])
        return {
            "name": os.path.basename(payload["path"]),
            "size": len(data),
            "b64": base64.b64encode(data).decode(),
        }

    monkeypatch.setattr(sandbox, "SANDBOX_URL", url)
    monkeypatch.setattr(sandbox, "sandbox_post", fake_post)


# ---- 契約の形 ----


def test_ref_error_shape():
    from nucllei.utils.image_refs import RefError

    err = RefError("too_large", "x", size=3, limit=2, size_exact=True)
    assert (err.code, err.detail, err.size, err.limit, err.status, err.size_exact) == (
        "too_large",
        "x",
        3,
        2,
        None,
        True,
    )
    # too_large 以外は size_exact を持たない(既定 False)
    assert RefError("not_found", "x").size_exact is False
    # reason は unknown_upstream の内訳(他の code では None)
    assert RefError("not_found", "x").reason is None
    with pytest.raises(Exception):  # frozen
        err.code = "other"


def test_success_returns_loaded_and_none(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    loaded, error = _resolve(MOUNTED)
    assert error is None
    assert loaded is not None and loaded.data == PNG_BYTES
    assert loaded.content_type == "image/png"


def test_load_ref_is_thin_wrapper(monkeypatch, tmp_path):
    from nucllei.utils.image_refs import load_ref

    _mount(monkeypatch, tmp_path)
    assert asyncio.run(load_ref(MOUNTED, "alice")).data == PNG_BYTES
    assert asyncio.run(load_ref("/ext/cage/static/nope.png", "alice")) is None
    assert asyncio.run(load_ref("x.png", "alice")) is None


# ---- 形の拒否 ----


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "x.png",
        "ftp://h/x.png",
        "file:///etc/x.png",
        "//host/x.png",
        "/etc/hostname",
    ],
)
def test_unsupported_form(monkeypatch, tmp_path, ref):
    _mount(monkeypatch, tmp_path)
    assert _code(ref).code == "unsupported_form"


def test_unsupported_form_non_string():
    assert _code(123).code == "unsupported_form"  # type: ignore[arg-type]


# ---- 同一サーバの相対 URL "/ext/{name}/…"(2026-10-03 — #139 の `relative_ext` を覆した)----


def test_relative_ext_resolves_like_absolute_file_backend(monkeypatch, tmp_path):
    """相対 `/ext/{rest}` は「今のサーバの URL」= 絶対 `…/ext/{rest}` と同じ `EXT_MOUNTS` の静的 mount で
    解決する。`relative_ext` という失敗 code はもう無い。"""
    _mount(monkeypatch, tmp_path)
    for ref in ("/ext/cage/static/a.png", "/ext/cage/static/a.png?v=1"):
        loaded, error = _resolve(ref)
        assert error is None, ref
        assert loaded.data == PNG_BYTES and loaded.name == "a.png"
    absolute, _ = _resolve(f"{SELF}/ext/cage/static/a.png")
    assert absolute.data == PNG_BYTES


def test_relative_ext_stays_closed(monkeypatch, tmp_path):
    """fail closed は絶対 URL のときと同じ: 静的 mount に無い物 / 静的 mount の外へ出る symlink / 不在 /
    traversal。`/ext/…` の失敗は not_found だけ(`unknown_name` / `not_ext_form` は
    2026-10-03 に name の概念ごと無くした)。"""
    _mount(monkeypatch, tmp_path)
    assert _code("/ext/nothere/a.png").code == "not_found"
    assert _code("/ext/cage/static/escape.png").code == "not_found"
    assert _code("/ext/cage/static/nope.png").code == "not_found"
    assert _code("/ext/cage/../outside.png").code == "traversal"
    assert _code("/ext/cage/static/%2e%2e/%2e%2e/outside.png").code == "traversal"
    # 旧 not_ext_form の形も、ただの 静的 mount のパス(無い・ディレクトリ)= not_found
    for malformed in ("/ext/", "/ext//a.png", "/ext/CAGE/static/a.png"):
        err = _code(malformed)
        assert (err.code, err.reason) == ("not_found", None), malformed
    # ディレクトリは not_found(一覧を出さない)
    for root_url in (
        "/ext/cage",
        "/ext/cage/",
        f"{SELF}/ext/cage/",
        "/ext/cage/static/",
        "/ext/nothere",
    ):
        err = _code(root_url)
        assert (err.code, err.reason) == ("not_found", None), root_url
    # "/ext" 始まりでも "/ext/" でなければ URL ではない。protocol-relative も受けない
    assert _code("/extra/cage/a.png").code == "unsupported_form"
    assert _code("//evil.example/ext/cage/static/a.png").code == "unsupported_form"


def test_no_relative_ext_code_left():
    import inspect

    from nucllei.utils import image_refs

    assert "relative_ext" not in (image_refs.RefError.__doc__ or "")
    assert '"relative_ext"' not in inspect.getsource(image_refs)


# ---- 引数名ごとの入口: url → resolve_url / path → resolve_path(値の形で読み替えない)----


def _url_code(url, **kw):
    from nucllei.utils.image_refs import resolve_url

    loaded, error = asyncio.run(resolve_url(url, **kw))
    assert loaded is None and error is not None
    return error


def _path_code(path, **kw):
    from nucllei.utils.image_refs import resolve_path

    loaded, error = asyncio.run(resolve_path(path, "alice", **kw))
    assert loaded is None and error is not None
    return error


def test_resolve_url_does_not_read_sandbox_paths(monkeypatch, tmp_path):
    """`url` に sandbox のパスが来ても fs としては読まない(実在していても)。"""
    root = _mount(monkeypatch, tmp_path)
    calls = []
    _install_sandbox(monkeypatch, {"x.png": PNG_BYTES})
    from nucllei.builtin_tools import sandbox

    real = sandbox.sandbox_post

    async def spy(*a, **k):
        calls.append(a)
        return await real(*a, **k)

    monkeypatch.setattr(sandbox, "sandbox_post", spy)
    for value in (
        f"{root}/cage/static/a.png",  # nucllei 側の実体のパス = URL でも sandbox のパスでもない
        "/workspace/x.png",
        "/api/v1/files/abc/content",
        "/etc/hostname",
        "x.png",
        "",
        "ftp://h/x.png",
    ):
        assert _url_code(value).code == "unsupported_form", value
    assert _url_code(123).code == "unsupported_form"  # type: ignore[arg-type]
    assert calls == []


def test_resolve_path_does_not_fetch_urls(monkeypatch, tmp_path):
    """`path` に URL(絶対)が来ても URL としては解決しない。相対 `/ext/…` は sandbox の
    RO マウントと同じ文字列なので **パスとして** 読める(ネットワークには出ない)。"""
    from nucllei.utils.image_refs import resolve_path

    _mount(monkeypatch, tmp_path)
    _self(monkeypatch)
    seen = []
    _install_transport(monkeypatch, lambda r: seen.append(r) or httpx.Response(200))
    for value in (
        f"{SELF}/ext/cage/static/a.png",
        f"{GENERAL}/a.png",
        "/etc/hostname",
        "/mnt/cage/static/a.png",  # 2026-10-03 までの置き場。もう特別扱いしない
        "x.png",
    ):
        assert _path_code(value).code == "unsupported_form", value
    loaded, err = asyncio.run(resolve_path(MOUNTED, "alice"))
    assert err is None and loaded.data == PNG_BYTES
    assert seen == []


def test_resolve_url_and_path_succeed_on_their_own_forms(monkeypatch, tmp_path):
    from nucllei.utils.image_refs import resolve_path, resolve_url

    _mount(monkeypatch, tmp_path)
    _install_sandbox(monkeypatch, {"x.png": PNG_BYTES})
    for url in ("/ext/cage/static/a.png", f"{SELF}/ext/cage/static/a.png"):
        loaded, error = asyncio.run(resolve_url(url))
        assert error is None and loaded.data == PNG_BYTES, url
    for path in (MOUNTED, "/workspace/x.png"):
        loaded, error = asyncio.run(resolve_path(path, "alice"))
        assert error is None and loaded.data == PNG_BYTES, path
    # 上限は入口によらず同じ引数
    err = _url_code("/ext/cage/static/a.png", max_bytes=3)
    assert (err.code, err.limit) == ("too_large", 3)
    err = _path_code("/workspace/x.png", max_bytes=3)
    assert (err.code, err.limit) == ("too_large", 3)


def test_file_id_of_url():
    """このサーバのファイル配信口の URL → id(URL のルーティング。読み出しは呼び手)。
    絶対形は `/ext/` と同じく host を問わない。"""
    from nucllei.utils.image_refs import file_id_of_url

    assert file_id_of_url("/api/v1/files/abc/content") == "abc"
    assert file_id_of_url("/api/v1/files/abc/content?x=1") == "abc"
    assert file_id_of_url("https://any.host/api/v1/files/abc/content") == "abc"
    # fragment は付いていても同じ file(意味を持つのは末尾の `#page=N` だけで、それは
    # 呼び手が split_page で読む。他の fragment は無視 — どの tool でも同じ規則)
    for with_fragment in (
        "/api/v1/files/abc/content#page=2",
        "/api/v1/files/abc/content#section",
        "/api/v1/files/abc/content?x=1#page=2",
        "https://any.host/api/v1/files/abc/content#a#page=2",
    ):
        assert file_id_of_url(with_fragment) == "abc", with_fragment
    for other in (
        "abc",
        "/api/v1/files/abc",
        "/api/v1/files/abc/content/extra",
        "/api/v1/files/abc#/content",
        "/ext/cage/api/v1/files/abc/content",
        "/workspace/api/v1/files/abc/content",
        None,
    ):
        assert file_id_of_url(other) is None, other


@pytest.mark.parametrize(
    "ref",
    [
        f"{SELF}/ext/cage/../secret.png",
        f"{SELF}/ext/cage/a\r\nHost: evil/y.png",
        f"{SELF}/ext/cage/a\x00.png",
        f"{SELF}/ext/cage/%2e%2e/x.png",
        "/workspace/../etc/passwd",
        "/ext/cage/static/a\x00.png",
    ],
)
def test_traversal(monkeypatch, tmp_path, ref):
    _mount(monkeypatch, tmp_path)
    _install_sandbox(monkeypatch, {})
    seen = []
    _install_transport(monkeypatch, lambda r: seen.append(r) or httpx.Response(200))
    assert _code(ref).code == "traversal"
    assert seen == []


# ---- unknown_upstream(一般の URL の自分自身だけ)----


def test_unknown_upstream_server_self(monkeypatch):
    """`/ext/` 形でない絶対 URL は一般の URL として取りに行く(行き先で絞らない)。唯一
    断るのはこのサーバ自身 — reason `server_self`。"""
    _self(monkeypatch)
    err = _code("http://127.0.0.1:3000/x.png")
    assert err.code == "unknown_upstream" and err.reason == "server_self"
    assert "this server itself" in err.detail and "127.0.0.1" in err.detail


# ---- not_found ----


def test_not_found_mount_missing_dir_and_escape(monkeypatch, tmp_path):
    """sandbox のパス `/ext/…` として読む側(`resolve_path`)の not_found。"""
    _mount(monkeypatch, tmp_path)
    for ref in (
        "/ext/cage/static/nope.png",
        "/ext/cage/static",
        "/ext/cage/static/escape.png",  # symlink が 静的 mount 外へ
    ):
        assert _path_code(ref).code == "not_found", ref


def test_not_found_ext(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    assert _code(f"{SELF}/ext/cage/static/nope.png").code == "not_found"
    assert _code(f"{SELF}/ext/cage/static/escape.png").code == "not_found"
    assert _code(f"{SELF}/ext/nothere/a.png").code == "not_found"


def test_not_found_http_404(monkeypatch):
    _self(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(404, content=b"nope"))
    err = _code(f"{GENERAL}/figs/a.png")
    assert err.code == "not_found"
    assert err.status == 404


def test_not_found_workspace_404(monkeypatch):
    _install_sandbox(monkeypatch, {})
    assert _code("/workspace/out/nope.png").code == "not_found"


# name の直下(rel が空)の URL の全表記
_ROOT_URLS = ("/ext/cage/", "/ext/cage", f"{SELF}/ext/cage/", f"{SELF}/ext/cage")


def test_directories_stay_closed(monkeypatch, tmp_path):
    """ディレクトリ(`/ext/` 自体を含む)— 一覧を出さず not_found(相対でも絶対でも。ゲートも 404)。"""
    from nucllei.utils.image_refs import mounted_path_for_url, route_of_url

    _mount(monkeypatch, tmp_path)
    for url in _ROOT_URLS + (
        "/ext/",
        f"{SELF}/ext/",
        "/ext/cage/static/",
        "/ext/cage/?page=top",
    ):
        err = _code(url)
        assert (err.code, err.reason) == ("not_found", None), url
        assert asyncio.run(mounted_path_for_url(url)) is None, url
    # 形としては正しい
    for url, rel in (("/ext/", ""), ("/ext/cage", "cage"), ("/ext/cage/", "cage/")):
        route, err = route_of_url(url)
        assert err is None and route.rel == rel, url


# ---- too_large(3 経路とも size / limit を持つ)----


def test_too_large_file(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", len(PNG_BYTES) - 1)
    err = _code(MOUNTED)
    assert err.code == "too_large"
    assert err.size == len(PNG_BYTES)
    assert err.limit == len(PNG_BYTES) - 1
    assert err.size_exact is True  # stat の値


def test_too_large_http_stream_aborted(monkeypatch):
    # 全量バッファせず打ち切る。size は Content-Length(あれば)、limit は上限
    _self(monkeypatch)
    pulled = {"chunks": 0}

    async def body():
        for _ in range(10):
            pulled["chunks"] += 1
            yield b"x" * 8

    _install_transport(
        monkeypatch,
        lambda r: httpx.Response(
            200,
            content=body(),
            headers={"content-type": "application/pdf", "content-length": "80"},
        ),
    )
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 20)
    err = _code(f"{GENERAL}/big.pdf")
    assert err.code == "too_large"
    assert err.size == 80
    assert err.limit == 20
    assert err.size_exact is True  # Content-Length の値
    assert pulled["chunks"] < 10


def test_too_large_http_stream_without_content_length(monkeypatch):
    # Content-Length が無ければ打ち切り時点の受信量(実サイズの下限)
    _self(monkeypatch)

    async def body():
        for _ in range(10):
            yield b"x" * 8

    _install_transport(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=body(), headers={"content-type": "image/png"}
        ),
    )
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 20)
    err = _code(f"{GENERAL}/big.png")
    assert err.code == "too_large"
    assert err.limit == 20
    assert err.size is not None and 20 < err.size <= 80
    assert err.size_exact is False  # 下限(打ち切り時点の受信量)


def test_too_large_workspace(monkeypatch):
    _install_sandbox(monkeypatch, {"out/plot.png": PNG_BYTES})
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", len(PNG_BYTES) - 1)
    err = _code("/workspace/out/plot.png")
    assert err.code == "too_large"
    assert err.size == len(PNG_BYTES)
    assert err.limit == len(PNG_BYTES) - 1
    assert err.size_exact is True  # 全量読めている


def test_too_large_workspace_sandbox_413(monkeypatch):
    # sandbox 側の read 上限(413)も too_large に寄せ、body からサイズを拾う
    _install_sandbox(
        monkeypatch,
        {},
        fail=RuntimeError("sandbox HTTP 413: file too large: 999 bytes (limit 500)"),
    )
    err = _code("/workspace/out/huge.bin")
    assert err.code == "too_large"
    assert (err.size, err.limit) == (999, 500)
    assert err.size_exact is True  # sandbox は stat で見る


# ---- upstream_status(3xx / 5xx)----


def test_upstream_status_redirect_without_location(monkeypatch):
    # Location の無い 3xx は辿れない redirect = upstream_status(reason 無し)
    _self(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(302))
    err = _code(f"{GENERAL}/figs/a.png")
    assert (err.code, err.status, err.reason) == ("upstream_status", 302, None)


def test_upstream_status_5xx(monkeypatch):
    _self(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(503, content=b"down"))
    err = _code(f"{GENERAL}/figs/a.png")
    assert err.code == "upstream_status"
    assert err.status == 503


# ---- upstream_unreachable ----


def test_upstream_unreachable_connect_error(monkeypatch):
    _self(monkeypatch)

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(monkeypatch, handler)
    err = _code(f"{GENERAL}/figs/a.png")
    assert err.code == "upstream_unreachable"
    assert "ConnectError" in err.detail


def test_upstream_unreachable_timeout(monkeypatch):
    _self(monkeypatch)

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _install_transport(monkeypatch, handler)
    assert _code(f"{GENERAL}/figs/a.png").code == "upstream_unreachable"


# ---- workspace_unavailable ----


def test_workspace_unavailable_without_sandbox_url(monkeypatch):
    _install_sandbox(monkeypatch, {"a.png": PNG_BYTES}, url="")
    err = _code("/workspace/a.png")
    assert err.code == "workspace_unavailable"
    assert "SANDBOX_URL" in err.detail


def test_workspace_unavailable_sandbox_api_failure(monkeypatch):
    # 不達(aiohttp 例外)と 401 / 5xx は不在ではない
    _install_sandbox(monkeypatch, {}, fail=OSError("connection refused"))
    assert _code("/workspace/a.png").code == "workspace_unavailable"
    _install_sandbox(
        monkeypatch, {}, fail=RuntimeError("sandbox HTTP 401: 認証に失敗しました")
    )
    err = _code("/workspace/a.png")
    assert err.code == "workspace_unavailable"
    assert err.status == 401
    _install_sandbox(monkeypatch, {}, fail=RuntimeError("sandbox HTTP 500: boom"))
    assert _code("/workspace/a.png").code == "workspace_unavailable"


# ---- max_bytes(呼び手ごとの上限。None なら env の FETCH_MAX_BYTES)----


def test_max_bytes_overrides_env_cap_on_file(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    ref = MOUNTED
    # env の上限では落ちるが、呼び手が大きめを指定すれば読める
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", len(PNG_BYTES) - 1)
    assert _code(ref).code == "too_large"
    loaded, error = _resolve(ref, max_bytes=len(PNG_BYTES))
    assert error is None and loaded.data == PNG_BYTES
    # 逆に env より小さく指定すれば落ち、limit には指定値が入る
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 10**9)
    err = _code(ref, max_bytes=len(PNG_BYTES) - 1)
    assert err.code == "too_large"
    assert (err.size, err.limit) == (len(PNG_BYTES), len(PNG_BYTES) - 1)


def test_max_bytes_applies_to_http_stream(monkeypatch):
    _self(monkeypatch)
    _install_transport(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=b"x" * 64, headers={"content-type": "image/png"}
        ),
    )
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 10**9)
    err = _code(f"{GENERAL}/big.png", max_bytes=16)
    assert err.code == "too_large"
    assert err.limit == 16
    loaded, error = _resolve(f"{GENERAL}/big.png", max_bytes=64)
    assert error is None and len(loaded.data) == 64


def test_max_bytes_applies_to_workspace(monkeypatch):
    _install_sandbox(monkeypatch, {"out/plot.png": PNG_BYTES})
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 10**9)
    err = _code("/workspace/out/plot.png", max_bytes=4)
    assert err.code == "too_large"
    assert (err.size, err.limit) == (len(PNG_BYTES), 4)
    loaded, error = _resolve("/workspace/out/plot.png", max_bytes=len(PNG_BYTES))
    assert error is None and loaded.data == PNG_BYTES


def test_load_ref_keeps_env_cap(monkeypatch, tmp_path):
    from nucllei.utils.image_refs import load_ref

    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", len(PNG_BYTES) - 1)
    assert asyncio.run(load_ref(MOUNTED, "alice")) is None


# ---- mounted_path_for_url(URL → sandbox のパス。同じ文字列 `/ext/{rest}`)----


def test_mounted_path_for_url(monkeypatch, tmp_path):
    """`/ext/{rest}` で 静的 mount の `{rest}` が実在する通常ファイルのときだけ sandbox のパス
    (= 相対 URL と同じ文字列 `/ext/{rest}`)を返す。規則は resolve_url と同じ `_route_url`
    (/ext/ 形(絶対・相対)— host は問わない)— 理由は返さない(None)。"""
    from nucllei.utils.image_refs import mounted_path_for_url

    root = _mount(monkeypatch, tmp_path)
    _self(monkeypatch)

    def probe(url):
        return asyncio.run(mounted_path_for_url(url))

    expected = MOUNTED
    assert probe(f"{SELF}/ext/cage/static/a.png?v=1") == expected
    assert probe("http://any.host:8080/ext/cage/static/a.png#page=2") == expected
    # 同一サーバの相対 URL も同じ
    assert probe("/ext/cage/static/a.png") == expected
    assert probe("/ext/cage/static/a.png?v=1#page=2") == expected
    assert probe("/ext/cage/static/nope.png") is None
    assert probe("/ext/cage/static/escape.png") is None
    assert probe("/ext/cage/static/%2e%2e/x") is None
    assert probe("/ext/nothere/a.png") is None
    # 不在 / 静的 mount の外へ出る symlink / traversal / 一般の URL / URL でない
    assert probe(f"{SELF}/ext/cage/static/nope.png") is None
    assert probe(f"{SELF}/ext/cage/static/escape.png") is None
    assert probe(f"{SELF}/ext/cage/static/%2e%2e/x") is None
    assert probe(f"{SELF}/ext/nothere/a.png") is None
    assert probe(f"{GENERAL}/a.png") is None
    assert probe("https://example.com/a.png") is None
    assert (
        probe(f"{root}/cage/static/a.png") is None
    )  # nucllei 側の実体のパスは URL でない

    # 静的 mount 自体が無ければ None(= 404)

    mount_tree(monkeypatch, tmp_path / "elsewhere")
    assert probe(f"{SELF}/ext/cage/static/a.png") is None


# ---- url_for_mounted_path(sandbox のパス → 配信 URL。mounted_path_for_url の逆)----


def _named_mount(monkeypatch, tmp_path):
    """日本語・空白・URL の予約文字を含む名前を持つ mount。"""
    root = _mount(monkeypatch, tmp_path)
    d = root / "cage" / "static" / "図 表"
    d.mkdir()
    (d / "手順 1 (改).png").write_bytes(PNG_BYTES)
    (d / "a#b?c%20d.png").write_bytes(PNG_BYTES)
    (root / "cage" / "doc").mkdir()
    (root / "cage" / "doc" / "本文.md").write_text("# x")
    (root / "shared").mkdir()
    (root / "shared" / "s.png").write_bytes(PNG_BYTES)
    (root / "cage" / "static" / "inner.png").symlink_to(
        root / "cage" / "static" / "a.png"
    )
    mount_tree(monkeypatch, root)  # 足した shared も mount にする
    return root


def test_url_for_mounted_path(monkeypatch, tmp_path):
    """sandbox のパス `/ext/{rest}` の実体 静的 mount の `{rest}` が実ファイルのときだけ URL を返す
    (ASCII 名なら同じ文字列、それ以外は percent-encode)。値の形ではなく解決結果 —
    不在・ディレクトリ・静的 mount の外へ出る物は None。"""
    from nucllei.utils.image_refs import url_for_mounted_path

    root = _named_mount(monkeypatch, tmp_path)

    def probe(path):
        return asyncio.run(url_for_mounted_path(path))

    assert probe("/ext/cage/static/a.png") == "/ext/cage/static/a.png"
    # 静的 mount に見えている物は全部(cage / static に限らない — /ext ゲートが配信している範囲と同じ)
    assert probe("/ext/cage/doc/本文.md") == ("/ext/cage/doc/%E6%9C%AC%E6%96%87.md")
    assert probe("/ext/shared/s.png") == "/ext/shared/s.png"
    # 静的 mount 内を指す symlink は実体の URL
    assert probe("/ext/cage/static/inner.png") == "/ext/cage/static/a.png"

    # 不在 / ディレクトリ / 静的 mount の外へ出る symlink / `..` / nucllei 側の実体のパス /
    # 旧 `/mnt` / workspace / 文字列でない
    assert probe("/ext/cage/static/nope.png") is None
    assert probe("/ext/cage/static") is None
    assert probe("/ext/cage/static/escape.png") is None
    assert probe("/ext/cage/static/../static/a.png") is None
    assert probe(f"{root}/cage/static/a.png") is None
    assert probe(str(tmp_path / "outside.png")) is None
    assert probe("/mnt/cage/static/a.png") is None
    assert probe("/workspace/a.png") is None
    assert probe(None) is None


def test_url_for_mounted_path_needs_mount_root(monkeypatch, tmp_path):
    """静的 mount が別の場所なら、同じ `/ext/…` でも nucllei 側に無い = None。"""
    from nucllei.utils.image_refs import url_for_mounted_path

    _named_mount(monkeypatch, tmp_path)
    mount_tree(monkeypatch, tmp_path / "elsewhere")
    assert asyncio.run(url_for_mounted_path("/ext/cage/static/a.png")) is None


def test_bridge_never_touches_host_mnt_or_ext(monkeypatch, tmp_path):
    """橋渡し・resolver・`/ext` ゲートが見るのは `EXT_MOUNTS` に書いた物だけ(host 直起動で host の
    `/mnt` や `/ext` を晒さない — ken 指摘 2026-10-03)。表に無い name は、nucllei の fs に同じ
    パスが在っても無い物(2026-10-05: 入口を 1 つの対応表に)。"""
    from nucllei.utils.image_refs import mounted_path_for_url, url_for_mounted_path

    _mount(monkeypatch, tmp_path)
    assert asyncio.run(url_for_mounted_path(MOUNTED)) == MOUNTED
    assert asyncio.run(mounted_path_for_url(MOUNTED)) == MOUNTED
    assert asyncio.run(url_for_mounted_path("/mnt/cage/static/a.png")) is None
    assert asyncio.run(url_for_mounted_path("/ext")) is None
    # 表を空にすると、たとえ nucllei の fs に /ext/… /mnt/… が在っても見ない
    mount_tree(monkeypatch, tmp_path / "empty")
    assert asyncio.run(url_for_mounted_path(MOUNTED)) is None
    assert asyncio.run(mounted_path_for_url(MOUNTED)) is None
    assert _path_code(MOUNTED).code == "not_found"
    assert _url_code(MOUNTED).code == "not_found"


def test_static_mount_root_may_be_a_symlink_but_contents_stay_inside(
    monkeypatch, tmp_path
):
    """静的 mount の値は絶対パス。それ自体が symlink でもよい(resolve した先が root)。mount の
    中身の symlink は実体に畳む(橋渡しの返す名前は実体の名前)が、root の外へは出られない。
    2026-10-05 までの「`EXT_DIR`(撤去)の木に置いたディレクトリ symlink は mount」は、表に書く形に
    置き換わった(`cage=<llens>/cage/data` 等)。"""
    from nucllei.utils.image_refs import (
        mounted_path_for_url,
        resolve_path,
        resolve_url,
        url_for_mounted_path,
    )

    content = tmp_path / "llens" / "static"
    content.mkdir(parents=True)
    (content / "a.png").write_bytes(PNG_BYTES)
    (content / "alias.png").symlink_to(content / "a.png")
    (content / "escape.png").symlink_to(tmp_path / "outside.png")
    (tmp_path / "outside.png").write_bytes(PNG_BYTES)
    (content / "sub").symlink_to(tmp_path)  # 中身のディレクトリ symlink も外へは出ない
    link = tmp_path / "link-to-static"
    link.symlink_to(content)
    monkeypatch.setattr("nucllei.env.EXT_MOUNTS", {"static": str(link)})

    url = "/ext/static/a.png"
    assert asyncio.run(url_for_mounted_path(url)) == url
    assert asyncio.run(mounted_path_for_url(url)) == url
    assert asyncio.run(url_for_mounted_path("/ext/static/alias.png")) == url
    assert asyncio.run(mounted_path_for_url("/ext/static/alias.png")) == url
    for reader in (lambda: resolve_url(url), lambda: resolve_path(url, "alice")):
        loaded, err = asyncio.run(reader())
        assert err is None and loaded.data == PNG_BYTES
    for bad in ("/ext/static/escape.png", "/ext/static/sub/outside.png"):
        assert asyncio.run(url_for_mounted_path(bad)) is None, bad
        assert _url_code(bad).code == "not_found", bad
        assert _path_code(bad).code == "not_found", bad


def test_mounted_path_and_url_round_trip(monkeypatch, tmp_path):
    """path → URL → path、URL → path → URL が一致する(日本語・空白・予約文字)。
    返す URL は resolve_url と /ext ゲート(Starlette が decode した path)の両方で同じ
    ファイルに当たる。"""
    from urllib.parse import unquote

    from nucllei.utils.ext_upstreams import ext_mount
    from nucllei.utils.image_refs import (
        mounted_path_for_url,
        resolve_url,
        static_file,
        url_for_mounted_path,
    )

    root = _named_mount(monkeypatch, tmp_path)

    for rel in (
        "static/a.png",
        "static/図 表/手順 1 (改).png",
        "static/図 表/a#b?c%20d.png",
        "doc/本文.md",
    ):
        path = f"/ext/cage/{rel}"
        url = asyncio.run(url_for_mounted_path(path))
        assert url is not None, rel
        # markdown のリンク先に置ける形(空白・括弧・# ? % が生で残らない)
        tail = url[len("/ext/cage/") :]
        assert not set(tail) & set(" ()#?"), url
        assert unquote(tail) == rel
        assert asyncio.run(mounted_path_for_url(url)) == path, rel
        assert asyncio.run(url_for_mounted_path(path)) == url
        loaded, err = asyncio.run(resolve_url(url))
        assert err is None and loaded.name == os.path.basename(path), rel
        # /ext ゲート: Starlette は decode 済みの path を渡す(ゲートも static_file で引く)
        served = static_file(ext_mount("cage"), unquote(tail))
        assert served is not None and served[0] == (root / "cage" / rel).resolve(), rel

    # URL 側から: encode 済みの URL はそのまま戻る。生の日本語・絶対 URL・query 付きは
    # 同じファイルの正規形(相対・encode 済み・query 無し)に揃う
    canonical = "/ext/cage/static/%E5%9B%B3%20%E8%A1%A8/%E6%89%8B%E9%A0%86%201%20%28%E6%94%B9%29.png"
    for url in (
        canonical,
        "/ext/cage/static/図%20表/手順%201%20(改).png",
        f"{SELF}/ext/cage/static/%E5%9B%B3%20%E8%A1%A8/%E6%89%8B%E9%A0%86%201%20(%E6%94%B9).png?v=1",
    ):
        path = asyncio.run(mounted_path_for_url(url))
        assert path == "/ext/cage/static/図 表/手順 1 (改).png", url
        assert asyncio.run(url_for_mounted_path(path)) == canonical, url


# ---- 静的 mount に無い物は配信されていない(配備が置かない = 渡さない)----


def test_unmounted_paths_are_simply_not_served(monkeypatch, tmp_path):
    """nucllei 側の `EXT_MOUNTS` の静的 mount に無い物に URL は無い(設定で絞る仕組みは無い — 2026-10-03 までの
    EXT_FILE_SCOPES / `mounted_path_withheld` は撤去)。sandbox のパスとしての読みも同じ
    置き場なので「無い」。"""
    from nucllei.utils.image_refs import (
        mounted_path_for_url,
        resolve_path,
        url_for_mounted_path,
    )

    root = _mount(monkeypatch, tmp_path)
    missing = "/ext/cage/doc/本文.md"
    doc_url = "/ext/cage/doc/%E6%9C%AC%E6%96%87.md"
    assert asyncio.run(url_for_mounted_path(missing)) is None
    assert asyncio.run(mounted_path_for_url(doc_url)) is None
    assert _url_code(doc_url).code == "not_found"
    assert _path_code(missing).code == "not_found"
    # 置けば両方で見える
    (root / "cage" / "doc").mkdir()
    (root / "cage" / "doc" / "本文.md").write_text("# x")
    assert asyncio.run(url_for_mounted_path(missing)) == doc_url
    assert asyncio.run(mounted_path_for_url(doc_url)) == missing
    loaded, err = asyncio.run(resolve_path(missing, "alice"))
    assert err is None and loaded.data == b"# x"


# ---- 引数が受ける形の固定文(llens の tool が import する公開定数)----


def test_arg_form_constants_are_the_agreed_sentences():
    """文面は llens の tool と合意した固定文そのもの(変えるときは両側を揃える)。"""
    from nucllei.utils import image_refs

    assert image_refs.URL_ARG_FORM == (
        "url が受けるのは、スキーム付きの絶対 URL (`http(s)://…`) と、同一サーバの "
        "`/ext/…`・`/api/v1/files/{id}/content` だけ。"
    )
    assert image_refs.PATH_ARG_FORM == (
        "path が受けるのは、sandbox の絶対パス (`/workspace/…` `/ext/…`) だけ。"
    )
    assert image_refs.WORKSPACE_PATH_ARG_FORM == (
        "path が受けるのは、sandbox の絶対パス (`/workspace/…`) だけ。"
    )
    assert image_refs.FILE_ID_ARG_FORM == "file_id は添付・生成物の id。"
    assert image_refs.REF_ARGS_RULE == (
        "URL は url、sandbox のパスは path、添付・生成物は file_id に、受け取った形のまま渡す "
        "(値を書き換えて別の引数に入れ直さない)。"
    )


# ---- 保存済み参照の振り分け(1 箇所の規則)----


def test_stored_ref_kind():
    from nucllei.utils.image_refs import stored_ref_kind

    cases = {
        "file-123": "file_id",
        "0b54ed0c-6f0b-4b5c-9d0e-3f6f3f0a1c11": "file_id",
        "https://llens.test/ext/cage/a.png": "url",
        "HTTP://his-info.test/docs/a.pdf#page=2": "url",
        "/ext/cage/static/a.png": "url",
        "/ext/cage/static/a.pdf#page=3": "url",
        "/workspace/a.png": "path",
        "/mnt/cage/x.pdf#page=2": None,  # 2026-10-03 までの置き場。もう特別扱いしない
        "/api/v1/files/abc/content": "file_url",
        "/api/v1/files/abc/content#page=2": "file_url",
        "https://any.host/api/v1/files/abc/content": "file_url",
        "/etc/passwd": None,
        "//evil.example/a.png": None,
        "/extra/a.png": None,
        "/api/v1/files/abc": None,
        "": None,
        None: None,
    }
    for ref, kind in cases.items():
        assert stored_ref_kind(ref) == kind, ref


def test_history_wire_uses_the_same_dispatch():
    """履歴の組み立て(content_blocks)は裸の file id だけ配信 URL に組み、他は参照文字列の
    まま image_url に載せる — 振り分けは stored_ref_kind。"""
    from nucllei.utils.content_blocks import convert_content_blocks_to_messages

    entries = [
        "file-123",
        "/ext/cage/static/a.png",
        "https://llens.test/ext/cage/a.png",
        "/workspace/out/x.pdf#page=2",
        "/api/v1/files/abc/content#page=2",
    ]
    block = {
        "type": "tool_calls",
        "content": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "t", "arguments": "{}"},
            }
        ],
        "results": [{"tool_call_id": "c1", "content": "ok", "context_images": entries}],
    }
    messages = convert_content_blocks_to_messages([block])
    urls = [
        part["image_url"]["url"]
        for part in messages[-1]["content"]
        if part["type"] == "image_url"
    ]
    assert urls == ["/api/v1/files/file-123/content", *entries[1:]]


# ---- route_of_url(URL がどこに属するか。読まない)----


def test_route_of_url(monkeypatch, tmp_path):
    from nucllei.utils.image_refs import UrlRoute, route_of_url

    def boom(request):
        raise AssertionError("route_of_url must not touch the network")

    _install_transport(monkeypatch, boom)
    _mount(monkeypatch, tmp_path)

    # 相対 `/ext/{rest}`
    route, err = route_of_url("/ext/cage/static/%E5%9B%B3/a.png?rev=2")
    assert err is None
    assert route == UrlRoute(
        rel="cage/static/%E5%9B%B3/a.png", query="?rev=2", origin=None, kind="ext"
    )
    assert route.relative is True and route.general is False
    assert route.page_url == "/ext/cage/static/%E5%9B%B3/a.png?rev=2"

    # 絶対 `/ext/{rest}`: host は問わない(origin は与えられたまま)。実在も見ない
    route, err = route_of_url("https://elsewhere.example:8443/ext/his/a/b.aspx#top")
    assert err is None
    assert (route.rel, route.query, route.kind) == ("his/a/b.aspx", "", "ext")
    assert route.origin == "https://elsewhere.example:8443"
    assert route.relative is False
    assert route.page_url == "https://elsewhere.example:8443/ext/his/a/b.aspx"

    # `/ext/` 自体・ディレクトリも形としては正しい(読めば not_found)
    for url, rel, origin in (
        ("/ext/", "", None),
        ("/ext/his", "his", None),
        ("/ext/his/", "his/", None),
        ("https://elsewhere.example/ext/", "", "https://elsewhere.example"),
    ):
        route, err = route_of_url(url)
        assert err is None, url
        assert route == UrlRoute(rel=rel, query="", origin=origin), url
    route, err = route_of_url("/ext/his/?a=1")
    assert err is None and (route.rel, route.query) == ("his/", "?a=1")
    # 旧フィールドは無い
    for gone in ("name", "public_base", "ext_form", "mount"):
        assert not hasattr(route, gone), gone


def test_route_of_url_file_kind(monkeypatch):
    """このサーバのファイル配信口は `kind="file"`・`rel` = id(route_of_url だけ。resolve_url は
    権限検査が要るので unsupported_form のまま — 呼び手が file_id_of_url で読む)。"""
    from nucllei.utils.image_refs import UrlRoute, resolve_url, route_of_url

    for url, origin in (
        ("/api/v1/files/abc/content", None),
        ("/api/v1/files/abc/content?x=1#page=2", None),
        ("https://any.host/api/v1/files/abc/content", "https://any.host"),
    ):
        route, err = route_of_url(url)
        assert err is None, url
        assert route == UrlRoute(rel="abc", query="", origin=origin, kind="file"), url
        assert route.page_url is None and not route.general
    # resolve_url は読まない: 相対形は unsupported_form(絶対形はただの一般の URL として
    # 扱われる — 呼び手が file_id_of_url で先に拾うのが規則)
    _, resolved_err = asyncio.run(resolve_url("/api/v1/files/abc/content"))
    assert resolved_err.code == "unsupported_form"


def test_route_of_url_fails_like_resolve_url(monkeypatch, tmp_path):
    """ルーティングできない URL は、resolve_url が同じ入力に返すのと同じ code / reason。"""
    from nucllei.utils.image_refs import resolve_url, route_of_url

    _mount(monkeypatch, tmp_path)
    cases = {
        # `/ext/` 形でない絶対 URL は一般の URL としてルーティングできる(下の
        # test_route_of_url_general_shape)— ここには出ない。未知の `/ext/…` も形としては
        # 正しい(読めば not_found)— ここには出ない
        "/ext/cage/../x": ("traversal", None),
        "/workspace/a.png": ("unsupported_form", None),
        "/extra/a.png": ("unsupported_form", None),
        "/ext": ("unsupported_form", None),
        "abc": ("unsupported_form", None),
        "ftp://h/a": ("unsupported_form", None),
        "": ("unsupported_form", None),
        None: ("unsupported_form", None),
    }
    for url, expected in cases.items():
        route, err = route_of_url(url)
        assert route is None and (err.code, err.reason) == expected, url
        _, resolved_err = asyncio.run(resolve_url(url))
        assert (resolved_err.code, resolved_err.reason) == expected, url


def test_route_of_url_ignores_existence(monkeypatch, tmp_path):
    """実在は見ない(「どこに属するか」だけ。読めるかは resolve_url の責務)。"""
    from nucllei.utils.image_refs import route_of_url

    _mount(monkeypatch, tmp_path)
    route, err = route_of_url("/ext/nothere/doc/nope.md")
    assert err is None and route.rel == "nothere/doc/nope.md"
    assert _url_code("/ext/nothere/doc/nope.md").code == "not_found"


# ---- 一般の URL — `/ext/` 形でない絶対 URL はどこでも取りに行く(自分自身を除く)----
#
# ken 決定(2026-10-03)「url= はスキーム付きの http(s) URL ならどこでも」。行き先で絞らない —
# 「閉域だから届かない」は prompt の文で、tool のゲートではない。同日に一度入れた院内の範囲
# (EXT_INTRANET_CIDRS)は同日に撤回。唯一断るのは **このサーバ自身(と同居するサービス)** で、
# その集合は実行時に求める(`ext_upstreams.discover_self_addresses`)— テストはそれを差し替える。
# 接続先は DNS で解決したアドレスに固定、redirect は 3 回まで(各ホップで同じ判定)。
# DNS は `ext_upstreams.lookup_host` を差し替え、HTTP は MockTransport(実ネットワークに出ない)。

# このテストが「自分自身」とみなすアドレス(実行時発見の代わり): コンテナ自身のアドレスと
# docker bridge のゲートウェイ(本番は 10.106.x — 固定 CIDR では守れない)
SELF_ADDRESSES = ("10.106.0.5", "10.106.0.1", "fd00:5e1f::5")
DNS = {
    "intra.test": ["172.16.5.5"],
    "intra2.test": ["172.16.7.7"],
    "dual.test": ["fd00::1", "172.16.9.9"],
    "lan.test": ["10.0.0.5"],
    "internet.test": ["93.184.216.34"],
    "rebind.test": ["172.16.5.5", "127.0.0.1"],  # 外と loopback を同時に返す
    "gateway.test": ["10.106.0.1"],  # docker bridge のホスト側(実行時発見)
    "host.docker.internal": ["10.106.0.1"],
}


def _self(monkeypatch, addresses=SELF_ADDRESSES, dns=DNS):
    """自分自身の発見と DNS を差し替える。"""
    import ipaddress

    from nucllei.utils import ext_upstreams

    found = frozenset(ipaddress.ip_address(a) for a in addresses)
    monkeypatch.setattr(ext_upstreams, "discover_self_addresses", lambda: found)
    ext_upstreams.reset_self_addresses()

    async def fake_lookup(host):
        try:
            return [host] if ipaddress.ip_address(host) else []
        except ValueError:
            return list(dns.get(host, []))

    monkeypatch.setattr(ext_upstreams, "lookup_host", fake_lookup)


class _Recorder:
    """MockTransport の記録: 出た要求(URL・Host ヘッダ・SNI)。"""

    def __init__(self, routes):
        self.requests: list[httpx.Request] = []
        self.routes = routes  # 接続先 URL(固定済み IP の形)→ Response か callable

    def __call__(self, request):
        self.requests.append(request)
        key = str(request.url)
        hit = self.routes.get(key)
        if hit is None:
            return httpx.Response(599, content=f"unexpected {key}".encode())
        return hit(request) if callable(hit) else hit

    @property
    def urls(self):
        return [str(r.url) for r in self.requests]

    @property
    def hosts(self):
        return [r.headers.get("host") for r in self.requests]


def _url(url, **kw):
    from nucllei.utils.image_refs import resolve_url

    return asyncio.run(resolve_url(url, **kw))


def test_ext_self_addresses_env_adds_host_addresses(monkeypatch, caplog):
    """`SELF_ADDRESSES`(配備が足す host の外向き IP)は固定集合・発見した集合に**追加**で
    効く(コンテナからは host の物理 / VPN インタフェースを列挙できない)。IP 以外・CIDR は捨てて
    WARNING、既定は空。起動ログには「設定で追加」として載る。"""
    import ipaddress
    import logging

    from nucllei import env
    from nucllei.utils import ext_upstreams, image_refs

    assert env.SELF_ADDRESSES == frozenset()
    with caplog.at_level(logging.WARNING, logger=env.log.name):
        parsed = env.parse_self_addresses(
            " 10.105.2.1, 172.16.41.21 ,, 10.0.0.0/8, host, fe80::1 "
        )
    assert parsed == frozenset(
        ipaddress.ip_address(a) for a in ("10.105.2.1", "172.16.41.21", "fe80::1")
    )
    assert caplog.text.count("SELF_ADDRESSES: ignoring invalid entry") == 2

    _self(monkeypatch)
    assert not ext_upstreams.is_self_address("10.105.2.1")
    monkeypatch.setattr(
        env,
        "SELF_ADDRESSES",
        frozenset({ipaddress.ip_address("10.105.2.1")}),
    )
    monkeypatch.setattr(image_refs, "SELF_ADDRESSES", env.SELF_ADDRESSES)
    assert ext_upstreams.is_self_address("10.105.2.1")
    assert not ext_upstreams.is_self_address("10.105.2.2")
    with caplog.at_level(logging.INFO, logger=image_refs.log.name):
        image_refs.log_mounts()
    assert "設定で追加 (SELF_ADDRESSES): 10.105.2.1" in caplog.text
    assert "EXT_SELF_ADDRESSES" not in caplog.text


def test_env_has_no_intranet_cidrs():
    """EXT_INTRANET_CIDRS は撤去済み(同日に入れて同日に撤回)— env にもパーサにも残さない。"""
    from nucllei import env
    from nucllei.utils import ext_upstreams

    assert not hasattr(env, "EXT_INTRANET_CIDRS")
    assert not hasattr(env, "parse_ext_intranet_cidrs")
    assert not hasattr(ext_upstreams, "INTRANET_DENIED_NETWORKS")
    assert not hasattr(ext_upstreams, "EXT_INTRANET_CIDRS")


def test_self_address_set_is_fixed_plus_discovered(monkeypatch):
    """`is_self_address` = 正当な行き先になり得ない固定の集合(loopback・unspecified・
    リンクローカル・multicast / 予約)+ 実行時に求めた自分のアドレス。private 帯(10/8・
    172.16/12・192.168/16)も docker 既定帯 172.17/16 も **固定では見ない**(本物の院内)。"""
    from nucllei.utils.ext_upstreams import is_self_address

    _self(monkeypatch)
    for bad in (
        "127.0.0.1",
        "127.255.255.255",
        "169.254.169.254",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fe80::1%eth0",
        "ff02::1",
        "::ffff:127.0.0.1",  # IPv4-mapped は v4 として判定
        # 実行時に求めた自分のアドレス(コンテナ・bridge のゲートウェイ)
        "10.106.0.5",
        "10.106.0.1",
        "::ffff:10.106.0.1",
        "fd00:5e1f::5",
    ):
        assert is_self_address(bad), bad
    for ok in (
        "172.16.5.5",
        "172.17.0.1",  # docker 既定帯も固定では拒否しない(自分の bridge なら発見で入る)
        "10.105.2.2",
        "10.106.0.6",  # 自分の隣(同じセグメントの別ホスト)は院内
        "192.168.0.1",
        "93.184.216.34",
        "fd00::1",
        "::ffff:172.16.5.5",
    ):
        assert not is_self_address(ok), ok
    assert not is_self_address("intra.test")  # ホスト名は先に引く
    assert not is_self_address("")


def test_self_addresses_are_cached_with_a_ttl(monkeypatch):
    import ipaddress

    from nucllei.utils import ext_upstreams

    calls = []

    def discover():
        calls.append(1)
        return frozenset({ipaddress.ip_address("10.106.0.5")})

    monkeypatch.setattr(ext_upstreams, "discover_self_addresses", discover)
    ext_upstreams.reset_self_addresses()
    assert ext_upstreams.is_self_address("10.106.0.5")
    assert ext_upstreams.is_self_address("10.106.0.5")
    assert len(calls) == 1
    monkeypatch.setattr(ext_upstreams, "SELF_ADDRESSES_TTL", 0.0)
    assert ext_upstreams.is_self_address("10.106.0.5")
    assert len(calls) == 2
    ext_upstreams.reset_self_addresses()


def test_discover_self_addresses_reads_proc_and_hostname(monkeypatch, tmp_path):
    """発見は /proc(fib_trie の LOCAL 葉・if_inet6・route / ipv6_route の既定ゲートウェイ)+
    gethostname の解決 + docker の特別名。無い物は無視して例外にしない。"""
    import ipaddress
    import socket

    from nucllei.utils import ext_upstreams

    fib = tmp_path / "fib_trie"
    fib.write_text(
        "Main:\n"
        "  +-- 0.0.0.0/0 3 0 5\n"
        "     |-- 0.0.0.0\n"
        "        /0 universe UNICAST\n"
        "     +-- 10.106.0.0/24 2 0 2\n"
        "        |-- 10.106.0.0\n"
        "           /32 link BROADCAST\n"
        "           /24 link UNICAST\n"
        "        |-- 10.106.0.5\n"
        "           /32 host LOCAL\n"
        "        |-- 10.106.0.255\n"
        "           /32 link BROADCAST\n"
        "     |-- 127.0.0.1\n"
        "        /32 host LOCAL\n"
        "Local:\n"
        "     |-- 10.106.0.5\n"
        "        /32 host LOCAL\n"
    )
    inet6 = tmp_path / "if_inet6"
    inet6.write_text(
        "fd00000000000000000000000000abcd 02 40 00 80     eth0\n"
        "00000000000000000000000000000001 01 80 10 80       lo\n"
        "garbage line\n"
    )
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t01006A0A\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t00006A0A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    )
    route6 = tmp_path / "ipv6_route"
    route6.write_text(
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
        "fe800000000000000000000000000001 00000400 00000001 00000000 00450003     eth0\n"
        "fd000000000000000000000000000000 40 00000000000000000000000000000000 00 "
        "00000000000000000000000000000000 00000100 00000001 00000000 00450001     eth0\n"
    )
    files = {
        "/proc/net/fib_trie": fib,
        "/proc/net/if_inet6": inet6,
        "/proc/net/route": route,
        "/proc/net/ipv6_route": route6,
    }
    real_open = open

    def fake_open(path, *a, **kw):
        if path in files:
            return real_open(files[path], *a, **kw)
        if str(path).startswith("/proc/net/"):
            raise OSError("no such proc file")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(socket, "gethostname", lambda: "me.test")

    def fake_getaddrinfo(name, *a, **kw):
        table = {
            "me.test": ["10.106.0.5", "fd00::1"],
            "host.docker.internal": ["10.106.0.1"],
        }
        if name not in table:
            raise socket.gaierror("nope")
        return [(None, None, None, None, (addr, 0)) for addr in table[name]]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    found = ext_upstreams.discover_self_addresses()
    assert found == frozenset(
        ipaddress.ip_address(a)
        for a in (
            "10.106.0.5",  # fib_trie LOCAL + hostname
            "127.0.0.1",  # fib_trie LOCAL
            "fd00::1",  # hostname
            "::1",  # if_inet6
            "10.106.0.1",  # 既定ゲートウェイ(01006A0A little-endian)+ host.docker.internal
            "fe80::1",  # IPv6 既定ゲートウェイ
            "fd00::abcd",  # if_inet6
        )
    )

    # /proc が無い(Linux 以外)でも hostname の分だけで動く
    def no_proc(path, *a, **kw):
        if str(path).startswith("/proc/net/"):
            raise OSError("not linux")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", no_proc)
    found = ext_upstreams.discover_self_addresses()
    assert found == frozenset(
        ipaddress.ip_address(a) for a in ("10.106.0.5", "fd00::1", "10.106.0.1")
    )


def test_general_url_is_fetched_without_any_env(monkeypatch):
    """/ext の対応表(EXT_MOUNTS)とは無関係に、スキーム付きの URL はそのまま取れる。
    接続先は DNS の結果に固定し、Host ヘッダは元のホスト名(+port)。cookie / 認証は無し。
    content-type は上流ヘッダ、name は basename。インターネットのホストも同じ。"""
    _self(monkeypatch)
    rec = _Recorder(
        {
            "http://172.16.5.5:8080/docs/%E5%9B%B3/a.png?rev=2": httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            ),
            "https://93.184.216.34/": httpx.Response(
                200, content=b"<html>hi</html>", headers={"content-type": "text/html"}
            ),
            "http://10.0.0.5/x": httpx.Response(200, content=b"lan"),
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://intra.test:8080/docs/%E5%9B%B3/a.png?rev=2#frag")
    assert err is None and loaded.data == PNG_BYTES
    assert loaded.content_type == "image/png" and loaded.name == "a.png"
    loaded, err = _url("https://internet.test/")
    assert err is None and loaded.content_type == "text/html"
    assert rec.requests[-1].extensions["sni_hostname"] == "internet.test"
    loaded, err = _url("http://lan.test/x")
    assert err is None and loaded.data == b"lan"
    assert rec.urls == [
        "http://172.16.5.5:8080/docs/%E5%9B%B3/a.png?rev=2",
        "https://93.184.216.34/",
        "http://10.0.0.5/x",
    ]
    assert rec.hosts == ["intra.test:8080", "internet.test", "lan.test"]
    assert "cookie" not in rec.requests[0].headers
    assert "authorization" not in rec.requests[0].headers
    assert rec.requests[0].extensions.get("sni_hostname") is None  # http には不要


def test_general_ip_literals_and_dual_stack(monkeypatch):
    """IP リテラルも同じ。v4 / v6 の両方を返す名前は最初(OS の優先順)を採る。IPv6 リテラルは
    [] 付きで接続し、ホストにも [] を付ける。root(path 無し)は name が空。"""
    _self(monkeypatch)
    rec = _Recorder(
        {
            "http://172.16.1.1/": httpx.Response(200, content=b"root"),
            "http://[fd00::1]/p": httpx.Response(200, content=b"dual"),
            "http://[fd00:1::5]:81/v6": httpx.Response(200, content=b"six"),
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://172.16.1.1")
    assert err is None and loaded.data == b"root" and loaded.name == ""
    loaded, err = _url("http://dual.test/p")
    assert err is None and loaded.data == b"dual"
    loaded, err = _url("http://[fd00:1::5]:81/v6")
    assert err is None and loaded.data == b"six"
    assert rec.hosts == ["172.16.1.1", "dual.test", "[fd00:1::5]:81"]


def test_server_self_is_the_only_denied_destination(monkeypatch):
    """loopback・自分のアドレス・既定ゲートウェイ・host.docker.internal・メタデータ・外と
    loopback を同時に返す名前は取りに行かない — reason `server_self`。繋ぎもしない。"""
    _self(monkeypatch)

    def boom(request):
        raise AssertionError(f"must not connect: {request.url}")

    _install_transport(monkeypatch, boom)
    for u in (
        "http://127.0.0.1:3000/health",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://0.0.0.0:3000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.106.0.5:13303/",  # 自分のアドレス
        "http://10.106.0.1:13303/",  # 既定ゲートウェイ(docker bridge のホスト側)
        "http://gateway.test:13303/",
        "http://host.docker.internal:13303/",
        "http://[fd00:5e1f::5]/x",
        "http://rebind.test/x",
    ):
        loaded, err = _url(u)
        assert loaded is None, u
        assert (err.code, err.reason) == ("unknown_upstream", "server_self"), u
        assert "this server itself" in err.detail, u


def test_general_unresolvable_host_is_unreachable(monkeypatch):
    _self(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(200))
    loaded, err = _url("http://nosuch.test/a")
    assert loaded is None
    # 閉域の本番には DNS が無い — tool が「IP で指定」と案内できるよう reason を付ける
    assert (err.code, err.reason) == ("upstream_unreachable", "unresolvable")


def test_ext_form_wins_over_the_general_rule(monkeypatch, tmp_path):
    """`/ext/{rest}` 形は host を問わず nucllei の `EXT_MOUNTS` の静的 mount をローカル読み(そのホストには
    繋がない)。同じホストでもその外は一般の URL(直接、redirect は辿る)。"""
    _mount(monkeypatch, tmp_path)
    _self(monkeypatch, dns={**DNS, "his-info.test": ["172.16.3.3"]})
    rec = _Recorder(
        {
            "http://172.16.3.3/other/a.pdf": httpx.Response(
                302, headers={"location": "/other/b.pdf"}
            ),
            "http://172.16.3.3/other/b.pdf": httpx.Response(200, content=b"b"),
            "http://172.16.3.3/ext/cage/static/a.png": httpx.Response(
                200, content=b"nope"
            ),
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://his-info.test/ext/cage/static/a.png")
    assert err is None and loaded.data == PNG_BYTES
    loaded, err = _url("http://his-info.test/other/a.pdf")
    assert err is None and loaded.data == b"b"
    loaded, err = _url("http://his-info.test/ext/nosuch/a.png")
    assert loaded is None and (err.code, err.reason) == ("not_found", None)
    assert rec.urls == [
        "http://172.16.3.3/other/a.pdf",
        "http://172.16.3.3/other/b.pdf",
    ]


def test_general_redirects_are_followed(monkeypatch):
    """3 回までの redirect を辿る(絶対・相対の Location、別のホストへも — インターネットへも)。
    各ホップで DNS 固定。最終応答の content-type / name を採る。"""
    _self(monkeypatch)
    rec = _Recorder(
        {
            "http://172.16.5.5/1": httpx.Response(301, headers={"location": "/2?x=1"}),
            "http://172.16.5.5/2?x=1": httpx.Response(
                302, headers={"location": "http://intra2.test/3"}
            ),
            "http://172.16.7.7/3": httpx.Response(
                307, headers={"location": "https://internet.test/final.png"}
            ),
            "https://93.184.216.34/final.png": httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            ),
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://intra.test/1")
    assert err is None and loaded.data == PNG_BYTES and loaded.name == "final.png"
    assert rec.urls == [
        "http://172.16.5.5/1",
        "http://172.16.5.5/2?x=1",
        "http://172.16.7.7/3",
        "https://93.184.216.34/final.png",
    ]
    assert rec.hosts == ["intra.test", "intra.test", "intra2.test", "internet.test"]


def test_general_fourth_redirect_fails(monkeypatch):
    _self(monkeypatch)
    rec = _Recorder(
        {
            f"http://172.16.5.5/{i}": httpx.Response(
                302, headers={"location": f"/{i + 1}"}
            )
            for i in range(1, 6)
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://intra.test/1")
    assert loaded is None
    assert (err.code, err.status, err.reason) == (
        "upstream_status",
        302,
        "too_many_redirects",
    )
    assert len(rec.urls) == 4  # 元 + 3 回。4 回目の転送先には行かない


def test_general_redirect_to_the_server_itself_stops(monkeypatch):
    """自分自身への転送(loopback・自分のアドレス・ゲートウェイ・host.docker.internal)は
    `upstream_status`(3xx の status、reason `redirect_self`)で止め、転送先には繋がない。"""
    _self(monkeypatch)
    for location in (
        "http://127.0.0.1:3000/health",
        "http://10.106.0.5:13303/",
        "http://10.106.0.1:13303/",
        "http://host.docker.internal:13303/",
        "http://169.254.169.254/latest/meta-data/",
        "http://rebind.test/x",
    ):
        rec = _Recorder(
            {
                "http://172.16.5.5/go": httpx.Response(
                    303, headers={"location": location}
                )
            }
        )
        _install_transport(monkeypatch, rec)
        loaded, err = _url("http://intra.test/go")
        assert loaded is None, location
        assert (err.code, err.status, err.reason) == (
            "upstream_status",
            303,
            "redirect_self",
        ), location
        assert rec.urls == ["http://172.16.5.5/go"], location


def test_general_redirect_to_an_unresolvable_form_is_a_plain_redirect(monkeypatch):
    """転送先が URL として組めない(http(s) でない・traversal)なら reason 無しの
    `upstream_status`(辿らなかった redirect)。繋がない。"""
    _self(monkeypatch)
    for location in (
        "ftp://intra.test/x",
        "http://intra2.test/x\\y",  # バックスラッシュ(生の Location を traversal 判定)
        "/ext/../x",
    ):
        rec = _Recorder(
            {
                "http://172.16.5.5/go": httpx.Response(
                    303, headers={"location": location}
                )
            }
        )
        _install_transport(monkeypatch, rec)
        loaded, err = _url("http://intra.test/go")
        assert loaded is None, location
        assert (err.code, err.status, err.reason) == (
            "upstream_status",
            303,
            None,
        ), location
        assert rec.urls == ["http://172.16.5.5/go"], location


def test_general_redirect_into_an_ext_url_reads_the_mount(monkeypatch, tmp_path):
    """転送先が `/ext/{rest}` 形に当たれば、nucllei の `EXT_MOUNTS` の静的 mount をローカル読み(そのホストには
    繋がない)。"""
    _mount(monkeypatch, tmp_path)
    _self(monkeypatch)
    rec = _Recorder(
        {
            "http://172.16.5.5/go": httpx.Response(
                302, headers={"location": "http://his-info.test/ext/cage/static/a.png"}
            ),
            "http://172.16.5.5/go2": httpx.Response(
                302, headers={"location": "/ext/cage/static/nope.png"}
            ),
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://intra.test/go")
    assert err is None and loaded.data == PNG_BYTES and loaded.name == "a.png"
    loaded, err = _url("http://intra.test/go2")
    assert loaded is None and err.code == "not_found"
    assert rec.urls == ["http://172.16.5.5/go", "http://172.16.5.5/go2"]


def test_general_shares_the_http_limits_and_codes(monkeypatch):
    """上限(max_bytes / env)・404 → not_found・5xx → upstream_status・接続失敗 →
    upstream_unreachable は http backend と同じ。"""
    _self(monkeypatch)
    big = b"x" * 100

    def conn_error(request):
        raise httpx.ConnectError("refused", request=request)

    rec = _Recorder(
        {
            "http://172.16.5.5/big": httpx.Response(
                200, content=big, headers={"content-length": "100"}
            ),
            "http://172.16.5.5/404": httpx.Response(404),
            "http://172.16.5.5/503": httpx.Response(503),
            "http://172.16.5.5/down": conn_error,
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://intra.test/big", max_bytes=10)
    assert (err.code, err.size, err.limit, err.size_exact) == (
        "too_large",
        100,
        10,
        True,
    )
    monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 20)
    _, err = _url("http://intra.test/big")
    assert (err.code, err.limit) == ("too_large", 20)
    _, err = _url("http://intra.test/404")
    assert (err.code, err.status) == ("not_found", 404)
    _, err = _url("http://intra.test/503")
    assert (err.code, err.status, err.reason) == ("upstream_status", 503, None)
    _, err = _url("http://intra.test/down")
    assert (err.code, err.reason) == ("upstream_unreachable", None)


def test_route_of_url_general_shape(monkeypatch, tmp_path):
    """一般の URL は `kind="general"`・`name=None`。DNS も自分自身の判定も行わない(純粋)—
    自分自身のホストでも候補として返り、実際の判定は resolve_url。"""
    from nucllei.utils import ext_upstreams
    from nucllei.utils.image_refs import (
        UrlRoute,
        mounted_path_for_url,
        route_of_url,
    )

    async def no_dns(host):
        raise AssertionError("route_of_url must not resolve names")

    _self(monkeypatch)
    monkeypatch.setattr(ext_upstreams, "lookup_host", no_dns)
    monkeypatch.setattr(
        ext_upstreams,
        "discover_self_addresses",
        lambda: (_ for _ in ()).throw(AssertionError("must not discover")),
    )
    ext_upstreams.reset_self_addresses()
    _install_transport(monkeypatch, lambda r: httpx.Response(599))

    route, err = route_of_url("http://intra.test:8080/docs/%E5%9B%B3/a.png?rev=2#top")
    assert err is None
    assert route == UrlRoute(
        rel="docs/%E5%9B%B3/a.png",
        query="?rev=2",
        origin="http://intra.test:8080",
        kind="general",
    )
    assert route.general and not route.relative
    assert route.page_url == "http://intra.test:8080/docs/%E5%9B%B3/a.png?rev=2"
    for u, page in (
        ("https://example.com", "https://example.com/"),
        ("http://127.0.0.1:3000/x", "http://127.0.0.1:3000/x"),
    ):
        route, err = route_of_url(u)
        assert err is None and (route.kind, route.page_url) == ("general", page), u
    # `/ext/{rest}` の route は kind "ext"(page_url はそのページ自身)
    _mount(monkeypatch, tmp_path)
    route, err = route_of_url("/ext/cage/a.png")
    assert err is None and (route.kind, route.rel, route.general) == (
        "ext",
        "cage/a.png",
        False,
    )
    assert route.page_url == "/ext/cage/a.png"
    # 一般の URL は sandbox に同じ実体を持たない
    assert asyncio.run(mounted_path_for_url("http://intra.test/a.png")) is None
    # ホストが無い・port が不正 → unsupported_form
    for bad in ("http:///x", "http://intra.test:99999/x"):
        route, err = route_of_url(bad)
        assert route is None and err.code == "unsupported_form", bad


def test_log_mounts_lists_the_discovered_self_addresses(monkeypatch, caplog):
    import logging

    from nucllei.utils import image_refs

    _self(monkeypatch)
    with caplog.at_level(logging.INFO, logger=image_refs.log.name):
        image_refs.log_mounts()
    text = caplog.text
    assert (
        "どこでも取りに行く" in text and "10.106.0.5" in text and "10.106.0.1" in text
    )
    assert "EXT_INTRANET_CIDRS" not in text


def test_general_ip_literals_without_dns(monkeypatch):
    """閉域の本番(MiNet)には DNS が無く、院内の URL は IP リテラル(`http://10.x.x.x/…`)が
    普通 — これが主経路。名前解決を一切呼ばずに接続・redirect が通ること。"""
    from nucllei.utils import ext_upstreams

    _self(monkeypatch)

    async def no_dns(host):
        raise AssertionError(f"must not resolve names: {host}")

    monkeypatch.setattr(ext_upstreams, "lookup_host", no_dns)
    rec = _Recorder(
        {
            "http://10.1.2.3/his-info/top.aspx": httpx.Response(
                200, content=b"<html>top</html>", headers={"content-type": "text/html"}
            ),
            "http://10.1.2.3:8080/": httpx.Response(200, content=b"root"),
            "https://10.105.2.2:4000/v1/models": httpx.Response(200, content=b"{}"),
            "http://10.1.2.3/old.aspx": httpx.Response(
                301, headers={"location": "http://10.1.2.4/new.aspx"}
            ),
            "http://10.1.2.4/new.aspx": httpx.Response(200, content=b"new"),
            "http://10.1.2.3/leave": httpx.Response(
                302, headers={"location": "http://192.168.1.1/x"}
            ),
            "http://192.168.1.1/x": httpx.Response(200, content=b"other lan"),
            "http://10.1.2.3/loop": httpx.Response(
                302, headers={"location": "http://127.0.0.1:3000/health"}
            ),
            "http://[fd00:10::7]/v6": httpx.Response(200, content=b"v6"),
        }
    )
    _install_transport(monkeypatch, rec)

    loaded, err = _url("http://10.1.2.3/his-info/top.aspx")
    assert (
        err is None and loaded.content_type == "text/html" and loaded.name == "top.aspx"
    )
    loaded, err = _url("http://10.1.2.3:8080")
    assert err is None and loaded.data == b"root"
    loaded, err = _url("https://10.105.2.2:4000/v1/models")
    assert err is None and loaded.data == b"{}"
    assert rec.requests[-1].extensions["sni_hostname"] == "10.105.2.2"
    loaded, err = _url("http://10.1.2.3/old.aspx")
    assert err is None and loaded.data == b"new" and loaded.name == "new.aspx"
    loaded, err = _url("http://[fd00:10::7]/v6")
    assert err is None and loaded.data == b"v6"
    # 別のセグメントへの転送も辿る(行き先で絞らない)
    loaded, err = _url("http://10.1.2.3/leave")
    assert err is None and loaded.data == b"other lan"
    # 自分自身への転送だけ止める
    loaded, err = _url("http://10.1.2.3/loop")
    assert loaded is None and (err.code, err.status, err.reason) == (
        "upstream_status",
        302,
        "redirect_self",
    )
    # 他のセグメント・staging の 172.16 は普通に繋ぐ。loopback だけ繋がない
    for u in ("http://192.168.1.1/x", "http://172.16.5.5/x"):
        _url(u)
    loaded, err = _url("http://127.0.0.1/x")
    assert (err.code, err.reason) == ("unknown_upstream", "server_self")
    assert rec.hosts == [
        "10.1.2.3",
        "10.1.2.3:8080",
        "10.105.2.2:4000",
        "10.1.2.3",
        "10.1.2.4",
        "[fd00:10::7]",
        "10.1.2.3",
        "192.168.1.1",
        "10.1.2.3",
        "192.168.1.1",
        "172.16.5.5",
    ]


def test_lookup_host_passes_literals_and_uses_getaddrinfo_for_names():
    """IP リテラルは引かずにそのまま(括弧は urlsplit が外している)。名前は getaddrinfo
    (= /etc/hosts も効く: localhost は DNS 無しで引ける)。引けない名前は空。"""
    from nucllei.utils.ext_upstreams import lookup_host

    assert asyncio.run(lookup_host("10.1.2.3")) == ["10.1.2.3"]
    assert asyncio.run(lookup_host("::1")) == ["::1"]
    assert asyncio.run(lookup_host("::ffff:10.1.2.3")) == ["::ffff:10.1.2.3"]
    assert "127.0.0.1" in asyncio.run(lookup_host("localhost"))
    assert asyncio.run(lookup_host("nosuch.invalid")) == []


def test_pin_address_uses_the_real_discovery_for_localhost():
    """差し替え無しでも loopback は固定の集合で断る(発見の失敗に依らない)。"""
    from nucllei.utils import ext_upstreams

    ext_upstreams.reset_self_addresses()
    assert asyncio.run(ext_upstreams.pin_address("127.0.0.1")) == (None, "server_self")
    assert asyncio.run(ext_upstreams.pin_address("localhost")) == (None, "server_self")
    assert asyncio.run(ext_upstreams.pin_address("93.184.216.34")) == (
        "93.184.216.34",
        "",
    )
    assert asyncio.run(ext_upstreams.pin_address("nosuch.invalid")) == (
        None,
        "unresolvable",
    )


# ---- proxy の name(EXT_MOUNTS の値が http(s) — 2026-10-05)----
#
# `/ext/…` を内部で解決する消費者も /ext ゲートと同じ表を引く: proxy の name は上流へ HTTP GET
# (サイズ上限・404 / 2xx 以外 / 不達の code は一般の URL と同じ)。上流は fake(MockTransport)。

PROXY_BASE = "http://cage.test:13303/root"


def _proxy(monkeypatch, tmp_path, handler):
    """`cage` = proxy、`shared` = 静的。上流へ出た要求を記録して返す。"""
    root = tmp_path / "ext"
    (root / "shared").mkdir(parents=True)
    (root / "shared" / "s.png").write_bytes(PNG_BYTES)
    mount_tree(monkeypatch, root, cage=PROXY_BASE)
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    _install_transport(monkeypatch, record)
    return seen


def _png_upstream(request):
    if request.url.path == "/root/static/a.png":
        return httpx.Response(
            200, content=PNG_BYTES, headers={"content-type": "image/png"}
        )
    return httpx.Response(404)


def test_proxy_name_is_fetched_from_the_upstream(monkeypatch, tmp_path):
    """`feed_image(url="/ext/cage/static/…png")` の解決 = proxy の先から取る。相対・絶対
    (host 不問)・sandbox のパスとも同じ表。cookie / 認証ヘッダは送らない。"""
    from nucllei.utils.image_refs import resolve_path, resolve_url

    seen = _proxy(monkeypatch, tmp_path, _png_upstream)
    for reader in (
        lambda: resolve_url("/ext/cage/static/a.png"),
        lambda: resolve_url(f"{SELF}/ext/cage/static/a.png"),
        lambda: resolve_path("/ext/cage/static/a.png", "alice"),
        lambda: _resolve_async("/ext/cage/static/a.png"),
    ):
        loaded, err = asyncio.run(reader())
        assert err is None and loaded.data == PNG_BYTES
        assert loaded.content_type == "image/png" and loaded.name == "a.png"
    assert {str(r.url) for r in seen} == {f"{PROXY_BASE}/static/a.png"}
    assert all(
        "cookie" not in r.headers and "authorization" not in r.headers for r in seen
    )
    # 日本語の住所は percent-encoded で、query は保つ(URL のとき)
    asyncio.run(resolve_url("/ext/cage/doc/%E5%9B%B3%20x.png?v=1"))
    assert seen[-1].url.raw_path.decode() == "/root/doc/%E5%9B%B3%20x.png?v=1"
    # 静的の name は今までどおりファイル
    loaded, err = asyncio.run(resolve_url("/ext/shared/s.png"))
    assert err is None and loaded.data == PNG_BYTES


def _resolve_async(ref):
    from nucllei.utils.image_refs import resolve_ref

    return resolve_ref(ref, "alice")


def test_proxy_name_failures_use_the_same_codes(monkeypatch, tmp_path):
    def handler(request):
        path = request.url.path
        if path == "/root/down.png":
            raise httpx.ConnectError("refused", request=request)
        if path == "/root/slow.png":
            raise httpx.ReadTimeout("slow", request=request)
        if path == "/root/err.png":
            return httpx.Response(500)
        if path == "/root/big.png":
            return httpx.Response(200, content=b"x" * 100)
        return httpx.Response(404)

    _proxy(monkeypatch, tmp_path, handler)
    assert _url_code("/ext/cage/missing.png").code == "not_found"
    assert _url_code("/ext/cage/down.png").code == "upstream_unreachable"
    assert _url_code("/ext/cage/slow.png").code == "upstream_unreachable"
    err = _url_code("/ext/cage/err.png")
    assert (err.code, err.status) == ("upstream_status", 500)
    err = _url_code("/ext/cage/big.png", max_bytes=10)
    assert (err.code, err.limit) == ("too_large", 10)
    assert _url_code("/ext/cage/%2e%2e/x.png").code == "traversal"
    # 表に無い name は not_found(上流にも fs にも行かない)
    assert _url_code("/ext/nothere/a.png").code == "not_found"
    assert _path_code("/ext/nothere/a.png").code == "not_found"


def test_proxy_name_redirects_inside_the_base_only(monkeypatch, tmp_path):
    """上流の redirect は base 配下なら `/ext/<name>/…` として辿る(ディレクトリの末尾 `/` の
    301 を想定)。base の外は upstream_status(辿らない)。"""

    def handler(request):
        path = request.url.path
        if path == "/root/static":
            return httpx.Response(301, headers={"location": "/root/static/"})
        if path == "/root/static/":
            return httpx.Response(302, headers={"location": "a.png?v=2"})
        if path == "/root/static/a.png":
            return httpx.Response(200, content=PNG_BYTES)
        if path == "/root/out":
            return httpx.Response(302, headers={"location": "http://other.test/x"})
        if path == "/root/sideways":
            return httpx.Response(302, headers={"location": "/rootx/a.png"})
        if path == "/root/loop":
            return httpx.Response(302, headers={"location": "/root/loop"})
        return httpx.Response(404)

    seen = _proxy(monkeypatch, tmp_path, handler)
    loaded, err = _url_resolve("/ext/cage/static")
    assert err is None and loaded.data == PNG_BYTES
    assert str(seen[-1].url) == f"{PROXY_BASE}/static/a.png?v=2"
    for url in ("/ext/cage/out", "/ext/cage/sideways"):
        err = _url_code(url)
        assert (err.code, err.status) == ("upstream_status", 302), url
    err = _url_code("/ext/cage/loop")
    assert (err.code, err.reason) == ("upstream_status", "too_many_redirects")


def _url_resolve(url):
    from nucllei.utils.image_refs import resolve_url

    return asyncio.run(resolve_url(url))


def test_proxy_name_bridges(monkeypatch, tmp_path):
    """橋渡し: URL → sandbox のパスは proxy では返さない(上流が返す物が jail の同じパスに
    在るとは限らない — 呼び手は上流から取り込む)。sandbox のパス → URL は上流が 2xx を返す
    ときだけ(HEAD で確かめる。405 なら GET)。"""
    from nucllei.utils.image_refs import mounted_path_for_url, url_for_mounted_path

    def handler(request):
        if request.url.path == "/root/nohead/a.png" and request.method == "HEAD":
            return httpx.Response(405)
        if request.url.path in ("/root/static/a.png", "/root/nohead/a.png"):
            return httpx.Response(200, content=PNG_BYTES)
        if request.url.path == "/root/static/dir":
            return httpx.Response(301, headers={"location": "/root/static/dir/"})
        return httpx.Response(404)

    seen = _proxy(monkeypatch, tmp_path, handler)
    assert asyncio.run(mounted_path_for_url("/ext/cage/static/a.png")) is None
    assert seen == []
    assert asyncio.run(url_for_mounted_path("/ext/cage/static/a.png")) == (
        "/ext/cage/static/a.png"
    )
    assert seen[-1].method == "HEAD"
    assert asyncio.run(url_for_mounted_path("/ext/cage/nohead/a.png")) == (
        "/ext/cage/nohead/a.png"
    )
    assert [r.method for r in seen[-2:]] == ["HEAD", "GET"]
    assert asyncio.run(url_for_mounted_path("/ext/cage/図.png")) is None
    assert seen[-1].url.raw_path.decode() == "/root/%E5%9B%B3.png"
    assert asyncio.run(url_for_mounted_path("/ext/cage/static/dir")) is None
    assert asyncio.run(url_for_mounted_path("/ext/cage")) is None
    # 静的の name は従来どおり
    assert asyncio.run(mounted_path_for_url("/ext/shared/s.png")) == "/ext/shared/s.png"
    assert asyncio.run(url_for_mounted_path("/ext/shared/s.png")) == "/ext/shared/s.png"


def test_proxy_bridge_follows_redirects_inside_the_base(monkeypatch, tmp_path):
    """sandbox のパス → URL は、上流が base の中へ redirect するなら辿り、**飛び先の** URL を
    返す(cage は `…/_index.md` を 302 で `…/` へ飛ばす — llens 2026-10-06。辿らないと
    `fetch_from_workspace` が取り込みに落ち、HTML が添付になる)。base の外への redirect は
    今のまま None、ループは `MAX_REDIRECTS` で止まる。"""
    from nucllei.utils.ext_upstreams import MAX_REDIRECTS
    from nucllei.utils.image_refs import url_for_mounted_path

    def handler(request):
        path = request.url.path
        if path == "/root/doc/X/_index.md":
            return httpx.Response(302, headers={"location": "/root/doc/X/"})
        if path == "/root/doc/X/":
            return httpx.Response(200, content=b"<html></html>")
        if path == "/root/doc/図/CQ1":
            return httpx.Response(
                302, headers={"location": "/root/doc/%E5%9B%B3/CQ1.md"}
            )
        if path == "/root/doc/図/CQ1.md":
            return httpx.Response(200, content=b"<html></html>")
        if path == "/root/out":
            return httpx.Response(302, headers={"location": "http://other.test/x"})
        if path == "/root/sideways":
            return httpx.Response(302, headers={"location": "/rootx/a"})
        if path == "/root/loop":
            return httpx.Response(302, headers={"location": "/root/loop"})
        return httpx.Response(404)

    seen = _proxy(monkeypatch, tmp_path, handler)
    assert asyncio.run(url_for_mounted_path("/ext/cage/doc/X/_index.md")) == (
        "/ext/cage/doc/X/"
    )
    assert [r.url.path for r in seen] == ["/root/doc/X/_index.md", "/root/doc/X/"]
    assert asyncio.run(url_for_mounted_path("/ext/cage/doc/図/CQ1")) == (
        "/ext/cage/doc/%E5%9B%B3/CQ1.md"
    )
    for path in ("/ext/cage/out", "/ext/cage/sideways"):
        assert asyncio.run(url_for_mounted_path(path)) is None, path
    seen.clear()
    assert asyncio.run(url_for_mounted_path("/ext/cage/loop")) is None
    assert len(seen) == MAX_REDIRECTS + 1


def test_log_mounts_lists_the_table(monkeypatch, tmp_path, caplog):
    import logging

    from nucllei.utils import image_refs

    (tmp_path / "s").mkdir()
    monkeypatch.setattr(
        "nucllei.env.EXT_MOUNTS",
        {"cage": PROXY_BASE, "s": str(tmp_path / "s"), "gone": str(tmp_path / "x")},
    )
    monkeypatch.setattr(image_refs, "self_addresses", lambda: frozenset())
    with caplog.at_level(logging.INFO, logger=image_refs.log.name):
        image_refs.log_mounts()
    text = caplog.text
    assert f"/ext/cage/ → proxy {PROXY_BASE}/" in text
    assert f"/ext/s/ → static {tmp_path / 's'}/" in text
    assert "/ext/gone/" in text and "ディレクトリが無い" in text


# --- sink(send_to_workspace(url=) が一時ファイルに落とす)--------------------------------
def test_sink_receives_the_body_and_only_the_head_is_kept(monkeypatch, tmp_path):
    import io

    from nucllei.utils.image_refs import resolve_url

    # 静的 mount(ファイルのコピー)
    _mount(monkeypatch, tmp_path)
    sink = io.BytesIO()
    loaded, err = asyncio.run(resolve_url(MOUNTED, user="alice", sink=sink))
    assert err is None and loaded.spooled and loaded.size == len(PNG_BYTES)
    assert sink.getvalue() == PNG_BYTES and loaded.content_type == "image/png"
    # 一般の URL(stream)。上限は sink でも同じく効く
    body = PNG_BYTES + b"x" * 200_000
    _install_transport(monkeypatch, lambda r: httpx.Response(200, content=body))
    _self(monkeypatch)
    sink = io.BytesIO()
    loaded, err = asyncio.run(resolve_url(f"{GENERAL}/b.png", sink=sink))
    assert err is None and loaded.spooled
    assert sink.getvalue() == body and loaded.size == len(body)
    assert len(loaded.data) == 64 and loaded.content_type == "image/png"
    loaded, err = asyncio.run(
        resolve_url(f"{GENERAL}/b.png", max_bytes=1000, sink=io.BytesIO())
    )
    assert loaded is None and err.code == "too_large"
