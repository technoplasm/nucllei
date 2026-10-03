"""`resolve_ref` の失敗理由(`RefError.code`)の番兵。

`load_ref` が None しか返さないと、呼び手(feed 側の tool / 送信直前 inline)は
「上流不達・不在・redirect・サイズ超過」を束ねた 1 つの失敗しか出せない — 実走で
32.6 MB の PDF が EXT_INLINE_MAX_BYTES(25 MB)に当たり、モデルが「リンク切れ」と
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

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
UPSTREAM = "http://upstream.test"
SELF = "https://llens.test"
PUBLIC_BASE = "http://his-info.test/docs"


def _resolve(ref, user_id="alice", **kw):
    from nucllei.utils.image_refs import resolve_ref

    return asyncio.run(resolve_ref(ref, user_id, **kw))


def _code(ref, user_id="alice", **kw):
    loaded, error = _resolve(ref, user_id, **kw)
    assert loaded is None
    assert error is not None
    return error


def _enable_cage_http(monkeypatch):
    from nucllei.env import EXT_UPSTREAMS

    monkeypatch.setitem(EXT_UPSTREAMS, "cage", UPSTREAM)


def _install_transport(monkeypatch, handler):
    from nucllei.utils import ext_upstreams

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    monkeypatch.setattr(ext_upstreams, "_client", client)


def _mount(monkeypatch, tmp_path):
    from nucllei.utils import image_refs

    root = tmp_path / "mnt"
    (root / "cage" / "static").mkdir(parents=True)
    (root / "cage" / "static" / "a.png").write_bytes(PNG_BYTES)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    (root / "cage" / "static" / "escape.png").symlink_to(outside)
    monkeypatch.setattr(image_refs, "RO_MOUNT_ROOT", root)
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
    root = _mount(monkeypatch, tmp_path)
    loaded, error = _resolve(f"{root}/cage/static/a.png")
    assert error is None
    assert loaded is not None and loaded.data == PNG_BYTES
    assert loaded.content_type == "image/png"


def test_load_ref_is_thin_wrapper(monkeypatch, tmp_path):
    from nucllei.utils.image_refs import load_ref

    root = _mount(monkeypatch, tmp_path)
    assert asyncio.run(load_ref(f"{root}/cage/static/a.png", "alice")).data == PNG_BYTES
    assert asyncio.run(load_ref(f"{root}/cage/static/nope.png", "alice")) is None
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
    """相対 `/ext/{name}/{rest}` は「今のサーバの URL」= 絶対 `…/ext/{name}/{rest}` と
    同じ backend で解決する。`relative_ext` という失敗 code はもう無い。"""
    root = _mount(monkeypatch, tmp_path)
    from nucllei.env import EXT_UPSTREAMS

    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    for ref in ("/ext/cage/static/a.png", "/ext/cage/static/a.png?v=1"):
        loaded, error = _resolve(ref)
        assert error is None, ref
        assert loaded.data == PNG_BYTES and loaded.name == "a.png"
    absolute, _ = _resolve(f"{SELF}/ext/cage/static/a.png")
    assert absolute.data == PNG_BYTES


def test_relative_ext_http_backend(monkeypatch):
    _enable_cage_http(monkeypatch)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200, content=PNG_BYTES, headers={"content-type": "image/png"}
        )

    _install_transport(monkeypatch, handler)
    loaded, error = _resolve("/ext/cage/figs/%E5%9B%B3.png?rev=2")
    assert error is None and loaded.data == PNG_BYTES
    # encode されたまま、EXT_UPSTREAMS の base へ
    assert seen == [f"{UPSTREAM}/figs/%E5%9B%B3.png?rev=2"]


def test_relative_ext_stays_closed(monkeypatch, tmp_path):
    """fail closed は絶対 URL のときと同じ: 未知の name / root の外へ出る symlink / 不在 /
    traversal / `/ext/{name}/{rest}` の形でないもの。"""
    root = _mount(monkeypatch, tmp_path)
    from nucllei.env import EXT_UPSTREAMS

    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    assert _code("/ext/nothere/a.png").code == "unknown_upstream"
    assert _code("/ext/cage/static/escape.png").code == "not_found"
    assert _code("/ext/cage/static/nope.png").code == "not_found"
    assert _code("/ext/cage/../outside.png").code == "traversal"
    assert _code("/ext/cage/static/%2e%2e/%2e%2e/outside.png").code == "traversal"
    # not_ext_form は name が取れない形だけ
    for malformed in ("/ext/", "/ext//a.png", "/ext/CAGE/static/a.png"):
        err = _code(malformed)
        assert err.code == "unknown_upstream", malformed
        assert err.reason == "not_ext_form", malformed
        assert "/ext/{name}/{path}" in err.detail, malformed
    # name の直下は正しい形。file backend の root はディレクトリ = not_found(一覧を出さない)
    for root_url in (
        "/ext/cage",
        "/ext/cage/",
        f"{SELF}/ext/cage/",
        "/ext/cage/static/",
    ):
        err = _code(root_url)
        assert (err.code, err.reason) == ("not_found", None), root_url
    # 未知の name の直下は unknown_name(形は正しい)
    assert _code("/ext/nothere").reason == "unknown_name"
    assert _code("/ext/nothere/").reason == "unknown_name"
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
        f"{root}/cage/static/a.png",
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
    """`path` に URL(絶対・相対 `/ext/…`)が来ても URL としては解決しない。"""
    root = _mount(monkeypatch, tmp_path)
    from nucllei.env import EXT_UPSTREAMS

    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    monkeypatch.setitem(EXT_UPSTREAMS, "web", UPSTREAM)
    seen = []
    _install_transport(monkeypatch, lambda r: seen.append(r) or httpx.Response(200))
    for value in (
        "/ext/cage/static/a.png",
        f"{SELF}/ext/cage/static/a.png",
        f"{SELF}/ext/web/a.png",
        "/etc/hostname",
        "x.png",
    ):
        assert _path_code(value).code == "unsupported_form", value
    assert seen == []


def test_resolve_url_and_path_succeed_on_their_own_forms(monkeypatch, tmp_path):
    from nucllei.env import EXT_UPSTREAMS
    from nucllei.utils.image_refs import resolve_path, resolve_url

    root = _mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    _install_sandbox(monkeypatch, {"x.png": PNG_BYTES})
    for url in ("/ext/cage/static/a.png", f"{SELF}/ext/cage/static/a.png"):
        loaded, error = asyncio.run(resolve_url(url))
        assert error is None and loaded.data == PNG_BYTES, url
    for path in (f"{root}/cage/static/a.png", "/workspace/x.png"):
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
        "/mnt/cage/static/a\x00.png",
    ],
)
def test_traversal(monkeypatch, tmp_path, ref):
    _mount(monkeypatch, tmp_path)
    _enable_cage_http(monkeypatch)
    _install_sandbox(monkeypatch, {})
    seen = []
    _install_transport(monkeypatch, lambda r: seen.append(r) or httpx.Response(200))
    assert _code(ref).code == "traversal"
    assert seen == []


# ---- unknown_upstream(2 種を detail で区別)----


def test_unknown_upstream_ext_name_not_configured(monkeypatch):
    # /ext/{name}/ 形だが name が EXT_UPSTREAMS に無い(機能無効も同じ)
    err = _code(f"{SELF}/ext/nothere/figs/a.png")
    assert err.code == "unknown_upstream" and err.reason == "unknown_name"
    assert "EXT_UPSTREAMS" in err.detail and "nothere" in err.detail


def test_unknown_upstream_public_base_without_backend(monkeypatch):
    from nucllei.env import EXT_PUBLIC_BASES

    monkeypatch.setitem(EXT_PUBLIC_BASES, "his", PUBLIC_BASE)
    err = _code(f"{PUBLIC_BASE}/a.png")
    assert err.code == "unknown_upstream" and err.reason == "unknown_name"
    assert "EXT_UPSTREAMS" in err.detail and "his" in err.detail


def test_unknown_upstream_server_self(monkeypatch):
    """登録に当たらない絶対 URL は一般の URL として取りに行く(行き先で絞らない)。唯一
    断るのはこのサーバ自身 — reason `server_self`。"""
    _enable_cage_http(monkeypatch)
    _self(monkeypatch)
    err = _code("http://127.0.0.1:3000/x.png")
    assert err.code == "unknown_upstream" and err.reason == "server_self"
    assert "this server itself" in err.detail and "127.0.0.1" in err.detail


# ---- not_found ----


def test_not_found_mount_missing_dir_and_escape(monkeypatch, tmp_path):
    root = _mount(monkeypatch, tmp_path)
    for ref in (
        f"{root}/cage/static/nope.png",
        f"{root}/cage/static",
        f"{root}/cage/static/escape.png",  # symlink が root 外へ
    ):
        assert _code(ref).code == "not_found", ref


def test_not_found_ext_file_backend(monkeypatch, tmp_path):
    from nucllei.env import EXT_UPSTREAMS

    root = _mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    assert _code(f"{SELF}/ext/cage/static/nope.png").code == "not_found"
    assert _code(f"{SELF}/ext/cage/static/escape.png").code == "not_found"


def test_not_found_http_404(monkeypatch):
    _enable_cage_http(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(404, content=b"nope"))
    err = _code(f"{SELF}/ext/cage/figs/a.png")
    assert err.code == "not_found"
    assert err.status == 404


def test_not_found_workspace_404(monkeypatch):
    _install_sandbox(monkeypatch, {})
    assert _code("/workspace/out/nope.png").code == "not_found"


# name の直下(rel が空)の URL の全表記。http backend ではどれも上流の `<base>/` を GET する
_ROOT_URLS = (
    f"{PUBLIC_BASE}/",
    PUBLIC_BASE,
    "/ext/cage/",
    "/ext/cage",
    f"{SELF}/ext/cage/",
    f"{SELF}/ext/cage",
)


def _public_base_http(monkeypatch, handler):
    from nucllei.env import EXT_PUBLIC_BASES

    _enable_cage_http(monkeypatch)
    monkeypatch.setitem(EXT_PUBLIC_BASES, "cage", PUBLIC_BASE)
    seen = []

    def record(request):
        seen.append(str(request.url))
        return handler(request)

    _install_transport(monkeypatch, record)
    return seen


def test_root_of_http_backend_asks_upstream(monkeypatch):
    """base の直下(rel が空)も上流に取りに行く(問い合わせる前に not_found にしない —
    実走で原本のトップページ `<base>/`(上流は 302)が「リンク切れ」と誤案内された)。"""
    seen = _public_base_http(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=b"<html>top</html>", headers={"content-type": "text/html"}
        ),
    )
    for url in _ROOT_URLS:
        loaded, error = _resolve(url)
        assert error is None, url
        assert loaded.data == b"<html>top</html>", url
        assert loaded.content_type == "text/html" and loaded.name == "", url
    assert seen == [f"{UPSTREAM}/"] * len(_ROOT_URLS)
    # query は付いたまま渡る
    seen.clear()
    assert _resolve(f"{PUBLIC_BASE}/?page=top")[1] is None
    assert _resolve(f"{PUBLIC_BASE}?page=top")[1] is None
    assert _resolve("/ext/cage/?page=top")[1] is None
    assert seen == [f"{UPSTREAM}/?page=top"] * 3


def test_root_of_http_backend_redirect_is_upstream_status(monkeypatch):
    seen = _public_base_http(
        monkeypatch,
        lambda r: httpx.Response(302, headers={"location": "/docs/top.aspx?x=1"}),
    )
    for url in _ROOT_URLS:
        err = _code(url)
        assert (err.code, err.status) == ("upstream_status", 302), url
    assert seen == [f"{UPSTREAM}/"] * len(_ROOT_URLS)


def test_root_of_http_backend_404_is_not_found(monkeypatch):
    seen = _public_base_http(monkeypatch, lambda r: httpx.Response(404))
    err = _code(f"{PUBLIC_BASE}/")
    assert (err.code, err.status) == ("not_found", 404)
    assert seen == [f"{UPSTREAM}/"]


def test_root_of_file_backend_stays_closed(monkeypatch, tmp_path):
    """file backend の直下はディレクトリ — 一覧を出さず not_found(原本 URL でも /ext 形でも)。"""
    from nucllei.env import EXT_PUBLIC_BASES, EXT_UPSTREAMS
    from nucllei.utils.image_refs import mounted_path_for_url

    root = _mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    monkeypatch.setitem(EXT_PUBLIC_BASES, "cage", PUBLIC_BASE)
    for url in _ROOT_URLS:
        err = _code(url)
        assert (err.code, err.reason) == ("not_found", None), url
        assert asyncio.run(mounted_path_for_url(url)) is None, url


# ---- too_large(3 経路とも size / limit を持つ)----


def test_too_large_file(monkeypatch, tmp_path):
    root = _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", len(PNG_BYTES) - 1
    )
    err = _code(f"{root}/cage/static/a.png")
    assert err.code == "too_large"
    assert err.size == len(PNG_BYTES)
    assert err.limit == len(PNG_BYTES) - 1
    assert err.size_exact is True  # stat の値


def test_too_large_http_stream_aborted(monkeypatch):
    # 全量バッファせず打ち切る。size は Content-Length(あれば)、limit は上限
    _enable_cage_http(monkeypatch)
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
    monkeypatch.setattr("nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", 20)
    err = _code(f"{SELF}/ext/cage/big.pdf")
    assert err.code == "too_large"
    assert err.size == 80
    assert err.limit == 20
    assert err.size_exact is True  # Content-Length の値
    assert pulled["chunks"] < 10


def test_too_large_http_stream_without_content_length(monkeypatch):
    # Content-Length が無ければ打ち切り時点の受信量(実サイズの下限)
    _enable_cage_http(monkeypatch)

    async def body():
        for _ in range(10):
            yield b"x" * 8

    _install_transport(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=body(), headers={"content-type": "image/png"}
        ),
    )
    monkeypatch.setattr("nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", 20)
    err = _code(f"{SELF}/ext/cage/big.png")
    assert err.code == "too_large"
    assert err.limit == 20
    assert err.size is not None and 20 < err.size <= 80
    assert err.size_exact is False  # 下限(打ち切り時点の受信量)


def test_too_large_workspace(monkeypatch):
    _install_sandbox(monkeypatch, {"out/plot.png": PNG_BYTES})
    monkeypatch.setattr(
        "nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", len(PNG_BYTES) - 1
    )
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


def test_upstream_status_redirect(monkeypatch):
    _enable_cage_http(monkeypatch)
    _install_transport(
        monkeypatch,
        lambda r: httpx.Response(302, headers={"location": f"{UPSTREAM}/moved.png"}),
    )
    err = _code(f"{SELF}/ext/cage/figs/a.png")
    assert err.code == "upstream_status"
    assert err.status == 302


def test_upstream_status_5xx(monkeypatch):
    _enable_cage_http(monkeypatch)
    _install_transport(monkeypatch, lambda r: httpx.Response(503, content=b"down"))
    err = _code(f"{SELF}/ext/cage/figs/a.png")
    assert err.code == "upstream_status"
    assert err.status == 503


# ---- upstream_unreachable ----


def test_upstream_unreachable_connect_error(monkeypatch):
    _enable_cage_http(monkeypatch)

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(monkeypatch, handler)
    err = _code(f"{SELF}/ext/cage/figs/a.png")
    assert err.code == "upstream_unreachable"
    assert "ConnectError" in err.detail


def test_upstream_unreachable_timeout(monkeypatch):
    _enable_cage_http(monkeypatch)

    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _install_transport(monkeypatch, handler)
    assert _code(f"{SELF}/ext/cage/figs/a.png").code == "upstream_unreachable"


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


# ---- max_bytes(呼び手ごとの上限。None なら env の EXT_INLINE_MAX_BYTES)----


def test_max_bytes_overrides_env_cap_on_file(monkeypatch, tmp_path):
    root = _mount(monkeypatch, tmp_path)
    ref = f"{root}/cage/static/a.png"
    # env の上限では落ちるが、呼び手が大きめを指定すれば読める
    monkeypatch.setattr(
        "nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", len(PNG_BYTES) - 1
    )
    assert _code(ref).code == "too_large"
    loaded, error = _resolve(ref, max_bytes=len(PNG_BYTES))
    assert error is None and loaded.data == PNG_BYTES
    # 逆に env より小さく指定すれば落ち、limit には指定値が入る
    monkeypatch.setattr("nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", 10**9)
    err = _code(ref, max_bytes=len(PNG_BYTES) - 1)
    assert err.code == "too_large"
    assert (err.size, err.limit) == (len(PNG_BYTES), len(PNG_BYTES) - 1)


def test_max_bytes_applies_to_http_stream(monkeypatch):
    _enable_cage_http(monkeypatch)
    _install_transport(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=b"x" * 64, headers={"content-type": "image/png"}
        ),
    )
    monkeypatch.setattr("nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", 10**9)
    err = _code(f"{SELF}/ext/cage/big.png", max_bytes=16)
    assert err.code == "too_large"
    assert err.limit == 16
    loaded, error = _resolve(f"{SELF}/ext/cage/big.png", max_bytes=64)
    assert error is None and len(loaded.data) == 64


def test_max_bytes_applies_to_workspace(monkeypatch):
    _install_sandbox(monkeypatch, {"out/plot.png": PNG_BYTES})
    monkeypatch.setattr("nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", 10**9)
    err = _code("/workspace/out/plot.png", max_bytes=4)
    assert err.code == "too_large"
    assert (err.size, err.limit) == (len(PNG_BYTES), 4)
    loaded, error = _resolve("/workspace/out/plot.png", max_bytes=len(PNG_BYTES))
    assert error is None and loaded.data == PNG_BYTES


def test_load_ref_keeps_env_cap(monkeypatch, tmp_path):
    from nucllei.utils.image_refs import load_ref

    root = _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", len(PNG_BYTES) - 1
    )
    assert asyncio.run(load_ref(f"{root}/cage/static/a.png", "alice")) is None


# ---- mounted_path_for_url(URL → sandbox と共有の /mnt パス)----


def test_mounted_path_for_url(monkeypatch, tmp_path):
    """file backend の実体が RO_MOUNT_ROOT 配下のときだけパスを返す。規則は resolve_url と
    同じ `_route_url`(/ext/ 形(絶対・相対)・EXT_PUBLIC_BASES)— 理由は返さない(None)。"""
    from nucllei.env import EXT_PUBLIC_BASES, EXT_UPSTREAMS
    from nucllei.utils.image_refs import mounted_path_for_url

    root = _mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    monkeypatch.setitem(EXT_UPSTREAMS, "web", UPSTREAM)
    monkeypatch.setitem(EXT_PUBLIC_BASES, "cage", PUBLIC_BASE)

    def probe(url):
        return asyncio.run(mounted_path_for_url(url))

    expected = f"{root}/cage/static/a.png"
    assert probe(f"{SELF}/ext/cage/static/a.png?v=1") == expected
    assert probe(f"{PUBLIC_BASE}/static/a.png#page=2") == expected
    # 同一サーバの相対 URL も同じ
    assert probe("/ext/cage/static/a.png") == expected
    assert probe("/ext/cage/static/a.png?v=1#page=2") == expected
    assert probe("/ext/cage/static/nope.png") is None
    assert probe("/ext/cage/static/escape.png") is None
    assert probe("/ext/cage/static/%2e%2e/x") is None
    assert probe("/ext/web/a.png") is None
    assert probe("/ext/nothere/a.png") is None
    # 不在 / root の外へ出る symlink / traversal / http backend / 許可先の外 / URL でない
    assert probe(f"{SELF}/ext/cage/static/nope.png") is None
    assert probe(f"{SELF}/ext/cage/static/escape.png") is None
    assert probe(f"{SELF}/ext/cage/static/%2e%2e/x") is None
    assert probe(f"{SELF}/ext/web/a.png") is None
    assert probe("https://example.com/a.png") is None
    assert probe(expected) is None

    # backend root が RO_MOUNT_ROOT の外なら None(sandbox から見える保証が無い)
    from nucllei.utils import image_refs

    monkeypatch.setattr(image_refs, "RO_MOUNT_ROOT", tmp_path / "elsewhere")
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
    return root


def test_url_for_mounted_path(monkeypatch, tmp_path):
    """file backend の root 配下(かつ RO_MOUNT_ROOT 配下)の実ファイルだけ URL を返す。
    値の形ではなく解決結果 — 配信されていない置き場・不在・root の外へ出る物は None。"""
    from nucllei.env import EXT_UPSTREAMS
    from nucllei.utils.image_refs import url_for_mounted_path

    root = _named_mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    monkeypatch.setitem(EXT_UPSTREAMS, "web", UPSTREAM)

    def probe(path):
        return asyncio.run(url_for_mounted_path(path))

    assert probe(f"{root}/cage/static/a.png") == "/ext/cage/static/a.png"
    # root 配下なら static に限らない(/ext ゲートが配信している範囲と同じ)
    assert probe(f"{root}/cage/doc/本文.md") == ("/ext/cage/doc/%E6%9C%AC%E6%96%87.md")
    # root 内を指す symlink は実体の URL
    assert probe(f"{root}/cage/static/inner.png") == "/ext/cage/static/a.png"

    # 配信されていない置き場 / 不在 / ディレクトリ / root(と mount)の外へ出る symlink /
    # `..` / mount の外 / workspace / URL / 文字列でない
    assert probe(f"{root}/shared/s.png") is None
    assert probe(f"{root}/cage/static/nope.png") is None
    assert probe(f"{root}/cage/static") is None
    assert probe(f"{root}/cage/static/escape.png") is None
    assert probe(f"{root}/cage/static/../static/a.png") is None
    assert probe(str(tmp_path / "outside.png")) is None
    assert probe("/workspace/a.png") is None
    assert probe("/ext/cage/static/a.png") is None
    assert probe(None) is None

    # http backend しか無ければ、どのパスも URL にならない
    monkeypatch.delitem(EXT_UPSTREAMS, "cage")
    assert probe(f"{root}/cage/static/a.png") is None


def test_url_for_mounted_path_needs_mount_root(monkeypatch, tmp_path):
    # backend root が RO_MOUNT_ROOT の外 = sandbox のパスではない → None
    from nucllei.env import EXT_UPSTREAMS
    from nucllei.utils import image_refs
    from nucllei.utils.image_refs import url_for_mounted_path

    root = _named_mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    monkeypatch.setattr(image_refs, "RO_MOUNT_ROOT", tmp_path / "elsewhere")
    assert asyncio.run(url_for_mounted_path(f"{root}/cage/static/a.png")) is None


def test_url_for_mounted_path_prefers_deepest_root(monkeypatch, tmp_path):
    from nucllei.env import EXT_UPSTREAMS
    from nucllei.utils.image_refs import mounted_path_for_url, url_for_mounted_path

    root = _named_mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    monkeypatch.setitem(EXT_UPSTREAMS, "figs", f"file://{root}/cage/static")
    path = f"{root}/cage/static/a.png"
    url = asyncio.run(url_for_mounted_path(path))
    assert url == "/ext/figs/a.png"
    assert asyncio.run(mounted_path_for_url(url)) == path


def test_mounted_path_and_url_round_trip(monkeypatch, tmp_path):
    """path → URL → path、URL → path → URL が一致する(日本語・空白・予約文字)。
    返す URL は resolve_url と /ext ゲート(Starlette が decode した path)の両方で同じ
    ファイルに当たる。"""
    from urllib.parse import unquote

    from nucllei.env import EXT_UPSTREAMS
    from nucllei.routers.ext import _serve_local
    from nucllei.utils.ext_upstreams import ext_backend
    from nucllei.utils.image_refs import (
        mounted_path_for_url,
        resolve_url,
        url_for_mounted_path,
    )

    root = _named_mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")

    for rel in (
        "static/a.png",
        "static/図 表/手順 1 (改).png",
        "static/図 表/a#b?c%20d.png",
        "doc/本文.md",
    ):
        path = f"{root}/cage/{rel}"
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
        # /ext ゲート: Starlette は decode 済みの path を渡す
        response = _serve_local("cage", ext_backend("cage")[1], unquote(tail))
        assert str(response.path) == path, rel

    # URL 側から: encode 済みの URL はそのまま戻る。生の日本語・絶対 URL・query 付きは
    # 同じファイルの正規形(相対・encode 済み・query 無し)に揃う
    canonical = "/ext/cage/static/%E5%9B%B3%20%E8%A1%A8/%E6%89%8B%E9%A0%86%201%20%28%E6%94%B9%29.png"
    for url in (
        canonical,
        "/ext/cage/static/図%20表/手順%201%20(改).png",
        f"{SELF}/ext/cage/static/%E5%9B%B3%20%E8%A1%A8/%E6%89%8B%E9%A0%86%201%20(%E6%94%B9).png?v=1",
    ):
        path = asyncio.run(mounted_path_for_url(url))
        assert path == f"{root}/cage/static/図 表/手順 1 (改).png", url
        assert asyncio.run(url_for_mounted_path(path)) == canonical, url


# ---- EXT_FILE_SCOPES(file backend の配信範囲。/ext ゲートと同じ ext_file_served)----


def test_parse_ext_file_scopes():
    from nucllei.env import parse_ext_file_scopes

    assert parse_ext_file_scopes("") == {}
    assert parse_ext_file_scopes("cage=static") == {"cage": ("static",)}
    assert parse_ext_file_scopes(" cage=/static/ , cage=figs/a ,his=docs") == {
        "cage": ("static", "figs/a"),
        "his": ("docs",),
    }
    # 値が不正でも name の絞り込みは有効のまま(root 全体に戻さない)
    assert parse_ext_file_scopes("cage=..,cage=a/../b,cage=,cage=a\\b") == {"cage": ()}
    assert parse_ext_file_scopes("cage=static,cage=../x") == {"cage": ("static",)}
    # name の形が不正 / "=" 無しは捨てる
    assert parse_ext_file_scopes("Cage=static,static") == {}


def test_file_scope_applies_to_every_resolution(monkeypatch, tmp_path):
    """範囲外(root の中だが EXT_FILE_SCOPES の外)は、resolve_url でも読めず、URL ⇔ パスの
    橋渡しも値を返さない。未設定なら従来どおり root 全体。"""
    from nucllei.env import EXT_FILE_SCOPES, EXT_UPSTREAMS
    from nucllei.utils.image_refs import (
        mounted_path_for_url,
        resolve_path,
        resolve_url,
        url_for_mounted_path,
    )

    root = _named_mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    doc_url, doc_path = (
        "/ext/cage/doc/%E6%9C%AC%E6%96%87.md",
        f"{root}/cage/doc/本文.md",
    )
    fig_url, fig_path = "/ext/cage/static/a.png", f"{root}/cage/static/a.png"

    def state():
        return (
            asyncio.run(resolve_url(doc_url))[0] is not None,
            asyncio.run(mounted_path_for_url(doc_url)),
            asyncio.run(url_for_mounted_path(doc_path)),
        )

    assert state() == (True, doc_path, doc_url)

    monkeypatch.setitem(EXT_FILE_SCOPES, "cage", ("static",))
    assert state() == (False, None, None)
    assert _url_code(doc_url).code == "not_found"
    # 範囲内は変わらない
    assert asyncio.run(resolve_url(fig_url))[1] is None
    assert asyncio.run(mounted_path_for_url(fig_url)) == fig_path
    assert asyncio.run(url_for_mounted_path(fig_path)) == fig_url
    # sandbox のパスとしての読みは /ext の配信範囲と無関係(RO_MOUNT_ROOT の中なら読める)
    loaded, err = asyncio.run(resolve_path(doc_path, "alice"))
    assert err is None and loaded.data == b"# x"

    # http backend の name に書いても何も起きない
    monkeypatch.setitem(EXT_UPSTREAMS, "web", UPSTREAM)
    monkeypatch.setitem(EXT_FILE_SCOPES, "web", ("static",))
    assert asyncio.run(url_for_mounted_path(fig_path)) == fig_url


# ---- 引数が受ける形の固定文(llens の tool が import する公開定数)----


def test_arg_form_constants_are_the_agreed_sentences():
    """文面は llens の tool と合意した固定文そのもの(変えるときは両側を揃える)。"""
    from nucllei.utils import image_refs

    assert image_refs.URL_ARG_FORM == (
        "url が受けるのは、スキーム付きの絶対 URL (`http(s)://…`) と、同一サーバの "
        "`/ext/…`・`/api/v1/files/{id}/content` だけ。"
    )
    assert image_refs.PATH_ARG_FORM == (
        "path が受けるのは、sandbox の絶対パス (`/workspace/…` `/mnt/…`) だけ。"
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
        "/mnt/cage/x.pdf#page=2": "path",
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
        "/mnt/cage/x.pdf#page=2",
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


def test_route_of_url(monkeypatch):
    from nucllei.env import EXT_PUBLIC_BASES, EXT_UPSTREAMS
    from nucllei.utils.image_refs import UrlRoute, route_of_url

    def boom(request):
        raise AssertionError("route_of_url must not touch the network")

    _install_transport(monkeypatch, boom)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", UPSTREAM)
    monkeypatch.setitem(EXT_UPSTREAMS, "his", "http://172.17.0.1:13303/his-info")
    monkeypatch.setitem(EXT_PUBLIC_BASES, "his", PUBLIC_BASE)

    # 相対 `/ext/{name}/…`: 原本ルートの無い name
    route, err = route_of_url("/ext/cage/static/%E5%9B%B3/a.png?rev=2")
    assert err is None
    assert route == UrlRoute(
        name="cage",
        rel="static/%E5%9B%B3/a.png",
        query="?rev=2",
        public_base=None,
        origin=None,
        ext_form=True,
    )
    assert route.relative is True

    # 絶対 `/ext/{name}/…`: host は問わない(origin は与えられたまま)。原本ルートがあれば返す
    route, err = route_of_url("https://elsewhere.example:8443/ext/his/a/b.aspx#top")
    assert err is None
    assert (route.name, route.rel, route.query) == ("his", "a/b.aspx", "")
    assert route.public_base == PUBLIC_BASE
    assert route.origin == "https://elsewhere.example:8443"
    assert (route.relative, route.ext_form) == (False, True)

    # 原本 URL(EXT_PUBLIC_BASES 配下)
    route, err = route_of_url(f"{PUBLIC_BASE}/a/b.pdf?rev=2#page=3")
    assert err is None
    assert (route.name, route.rel, route.query) == ("his", "a/b.pdf", "?rev=2")
    assert (route.public_base, route.origin) == (PUBLIC_BASE, "http://his-info.test")
    assert (route.relative, route.ext_form) == (False, False)

    # name の直下: rel は ""(末尾 `/` の有無は区別しない)。失敗にしない
    for url, ext_form, origin in (
        ("/ext/his", True, None),
        ("/ext/his/", True, None),
        ("https://elsewhere.example/ext/his", True, "https://elsewhere.example"),
        (PUBLIC_BASE, False, "http://his-info.test"),
        (f"{PUBLIC_BASE}/", False, "http://his-info.test"),
        (f"{PUBLIC_BASE}/#top", False, "http://his-info.test"),
    ):
        route, err = route_of_url(url)
        assert err is None, url
        assert route == UrlRoute(
            name="his",
            rel="",
            query="",
            public_base=PUBLIC_BASE,
            origin=origin,
            ext_form=ext_form,
        ), url
    route, err = route_of_url(f"{PUBLIC_BASE}?a=1")
    assert err is None and (route.rel, route.query) == ("", "?a=1")
    route, err = route_of_url("/ext/his/?a=1")
    assert err is None and (route.rel, route.query) == ("", "?a=1")


def test_route_of_url_fails_like_resolve_url(monkeypatch):
    """ルーティングできない URL は、resolve_url が同じ入力に返すのと同じ code / reason。"""
    from nucllei.env import EXT_PUBLIC_BASES, EXT_UPSTREAMS
    from nucllei.utils.image_refs import resolve_url, route_of_url

    monkeypatch.setitem(EXT_UPSTREAMS, "cage", UPSTREAM)
    monkeypatch.setitem(EXT_PUBLIC_BASES, "his", PUBLIC_BASE)  # backend 無し
    cases = {
        "/ext/nosuch/a.png": ("unknown_upstream", "unknown_name"),
        "https://llens.test/ext/nosuch/a.png": ("unknown_upstream", "unknown_name"),
        f"{PUBLIC_BASE}/a.png": ("unknown_upstream", "unknown_name"),
        "/ext/nosuch": ("unknown_upstream", "unknown_name"),
        PUBLIC_BASE: ("unknown_upstream", "unknown_name"),
        f"{PUBLIC_BASE}/": ("unknown_upstream", "unknown_name"),
        "/ext/": ("unknown_upstream", "not_ext_form"),
        "/ext//a.png": ("unknown_upstream", "not_ext_form"),
        "/ext/CAGE/a.png": ("unknown_upstream", "not_ext_form"),
        # 登録に当たらない絶対 URL は一般の URL としてルーティングできる(下の
        # test_route_of_url_general_shape)— ここには出ない
        "/ext/cage/../x": ("traversal", None),
        "/workspace/a.png": ("unsupported_form", None),
        "/api/v1/files/abc/content": ("unsupported_form", None),
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


def test_route_of_url_ignores_serving_scope(monkeypatch, tmp_path):
    """配信範囲(EXT_FILE_SCOPES)・実在は見ない(「どこに属するか」だけ。読めるかは
    resolve_url の責務)。"""
    from nucllei.env import EXT_FILE_SCOPES, EXT_UPSTREAMS
    from nucllei.utils.image_refs import route_of_url

    root = _mount(monkeypatch, tmp_path)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    monkeypatch.setitem(EXT_FILE_SCOPES, "cage", ("static",))
    route, err = route_of_url("/ext/cage/doc/nope.md")
    assert err is None and (route.name, route.rel) == ("cage", "doc/nope.md")
    assert _url_code("/ext/cage/doc/nope.md").code == "not_found"


# ---- 配信範囲の外は利用者に渡さない(mounted_path_withheld)----


def test_mounted_path_withheld(monkeypatch, tmp_path):
    from nucllei.env import EXT_FILE_SCOPES, EXT_UPSTREAMS
    from nucllei.utils.image_refs import mounted_path_withheld

    root = _mount(monkeypatch, tmp_path)
    (root / "cage" / "doc" / "inner").mkdir(parents=True)
    (root / "cage" / "doc" / "x.md").write_text("# x")
    (root / "cage" / "doc" / "inner" / "y.md").write_text("# y")
    (root / "shared").mkdir()
    (root / "shared" / "memo.txt").write_text("m")
    (root / "shared" / "link.md").symlink_to(root / "cage" / "doc" / "x.md")

    def withheld(path):
        return asyncio.run(mounted_path_withheld(path))

    doc, fig = f"{root}/cage/doc/x.md", f"{root}/cage/static/a.png"
    # backend が無ければ、配信の設定と無関係
    assert not withheld(doc)
    # scope 未設定 = root 全体が配信範囲
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    assert not withheld(doc) and not withheld(fig)
    # scope あり: root 配下で範囲の外だけが True(実在は見ない。symlink は実体で判定)
    monkeypatch.setitem(EXT_FILE_SCOPES, "cage", ("static",))
    assert withheld(doc) and withheld(f"{root}/cage/doc/nope.md")
    assert withheld(f"{root}/shared/link.md")
    assert not withheld(fig)
    assert not withheld(f"{root}/shared/memo.txt")
    # sandbox のパスでないもの・traversal は False(読みの失敗に任せる)
    assert not withheld("/ext/cage/doc/x.md")
    assert not withheld(f"{root}/cage/doc/../doc/x.md")
    assert not withheld(None)
    # root が入れ子: 外側の scope からは外れるが、内側の backend が配信している物は渡せる
    monkeypatch.setitem(EXT_UPSTREAMS, "inner", f"file://{root}/cage/doc/inner")
    assert not withheld(f"{root}/cage/doc/inner/y.md")
    assert withheld(doc)
    # http backend は関係しない
    monkeypatch.setitem(EXT_UPSTREAMS, "web", UPSTREAM)
    assert withheld(doc)


# ---- 一般の URL — 登録に当たらない絶対 URL はどこでも取りに行く(自分自身を除く)----
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
    """`EXT_SELF_ADDRESSES`(配備が足す host の外向き IP)は固定集合・発見した集合に**追加**で
    効く(コンテナからは host の物理 / VPN インタフェースを列挙できない)。IP 以外・CIDR は捨てて
    WARNING、既定は空。起動ログには「設定で追加」として載る。"""
    import ipaddress
    import logging

    from nucllei import env
    from nucllei.utils import ext_upstreams, image_refs

    assert env.EXT_SELF_ADDRESSES == frozenset()
    with caplog.at_level(logging.WARNING, logger=env.log.name):
        parsed = env.parse_ext_self_addresses(
            " 10.105.2.1, 172.16.41.21 ,, 10.0.0.0/8, host, fe80::1 "
        )
    assert parsed == frozenset(
        ipaddress.ip_address(a) for a in ("10.105.2.1", "172.16.41.21", "fe80::1")
    )
    assert caplog.text.count("EXT_SELF_ADDRESSES: ignoring invalid entry") == 2

    _self(monkeypatch)
    assert not ext_upstreams.is_self_address("10.105.2.1")
    monkeypatch.setattr(
        env,
        "EXT_SELF_ADDRESSES",
        frozenset({ipaddress.ip_address("10.105.2.1")}),
    )
    monkeypatch.setattr(image_refs, "EXT_SELF_ADDRESSES", env.EXT_SELF_ADDRESSES)
    assert ext_upstreams.is_self_address("10.105.2.1")
    assert not ext_upstreams.is_self_address("10.105.2.2")
    with caplog.at_level(logging.INFO, logger=image_refs.log.name):
        image_refs.log_mounts()
    assert "設定で追加 (EXT_SELF_ADDRESSES): 10.105.2.1" in caplog.text


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
    """登録(EXT_UPSTREAMS / EXT_PUBLIC_BASES)が空でも、スキーム付きの URL はそのまま取れる。
    接続先は DNS の結果に固定し、Host ヘッダは元のホスト名(+port)。cookie / 認証は無し。
    content-type は上流ヘッダ、name は basename。インターネットのホストも同じ。"""
    from nucllei.env import EXT_PUBLIC_BASES, EXT_UPSTREAMS

    _self(monkeypatch)
    monkeypatch.setattr("nucllei.utils.ext_upstreams.EXT_UPSTREAMS", {})
    monkeypatch.setattr("nucllei.utils.ext_upstreams.EXT_PUBLIC_BASES", {})
    assert EXT_UPSTREAMS is not None and EXT_PUBLIC_BASES is not None
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


def test_registered_namespaces_win_over_the_general_rule(monkeypatch):
    """同じホストでも EXT_PUBLIC_BASES の `<base>/` 配下は登録した配信元の規則(backend 経由、
    redirect は辿らない)。その外は一般の URL(直接、redirect は辿る)。`/ext/{name}/…` 形は
    host を問わず backend。"""
    from nucllei.env import EXT_PUBLIC_BASES, EXT_UPSTREAMS

    _self(monkeypatch, dns={**DNS, "his-info.test": ["172.16.3.3"]})
    monkeypatch.setitem(EXT_UPSTREAMS, "his", "http://backend.test/his")
    monkeypatch.setitem(EXT_PUBLIC_BASES, "his", "http://his-info.test/docs")
    rec = _Recorder(
        {
            "http://backend.test/his/a.pdf": httpx.Response(
                302, headers={"location": "http://his-info.test/docs/b.pdf"}
            ),
            "http://172.16.3.3/other/a.pdf": httpx.Response(
                302, headers={"location": "/other/b.pdf"}
            ),
            "http://172.16.3.3/other/b.pdf": httpx.Response(200, content=b"b"),
            "http://172.16.3.3/ext/his/a.pdf": httpx.Response(200, content=b"nope"),
            "http://backend.test/his/x/a.pdf": httpx.Response(200, content=b"ext"),
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://his-info.test/docs/a.pdf")
    assert loaded is None and (err.code, err.status, err.reason) == (
        "upstream_status",
        302,
        None,
    )
    loaded, err = _url("http://his-info.test/other/a.pdf")
    assert err is None and loaded.data == b"b"
    loaded, err = _url("http://his-info.test/ext/his/x/a.pdf")
    assert err is None and loaded.data == b"ext"
    assert rec.urls == [
        "http://backend.test/his/a.pdf",
        "http://172.16.3.3/other/a.pdf",
        "http://172.16.3.3/other/b.pdf",
        "http://backend.test/his/x/a.pdf",
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
    """転送先が URL として組めない(http(s) でない・traversal・未知の /ext/{name}/)なら
    reason 無しの `upstream_status`(辿らなかった redirect)。繋がない。"""
    _self(monkeypatch)
    for location in (
        "ftp://intra.test/x",
        "http://intra2.test/x\\y",  # バックスラッシュ(生の Location を traversal 判定)
        "/ext/nosuch/x",
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


def test_general_redirect_into_a_registered_upstream_uses_its_backend(monkeypatch):
    """転送先が登録した配信元に当たれば、その backend で 1 回だけ取る(配信元の規則 —
    そこから先の redirect は辿らない)。"""
    from nucllei.env import EXT_PUBLIC_BASES, EXT_UPSTREAMS

    _self(monkeypatch)
    monkeypatch.setitem(EXT_UPSTREAMS, "his", "http://backend.test/his")
    monkeypatch.setitem(EXT_PUBLIC_BASES, "his", "http://his-info.test/docs")
    rec = _Recorder(
        {
            "http://172.16.5.5/go": httpx.Response(
                302, headers={"location": "http://his-info.test/docs/a.pdf"}
            ),
            "http://backend.test/his/a.pdf": httpx.Response(
                200, content=b"via backend"
            ),
            "http://172.16.5.5/go2": httpx.Response(
                302, headers={"location": "http://his-info.test/docs/r.pdf"}
            ),
            "http://backend.test/his/r.pdf": httpx.Response(
                302, headers={"location": "http://his-info.test/docs/a.pdf"}
            ),
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("http://intra.test/go")
    assert err is None and loaded.data == b"via backend"
    loaded, err = _url("http://intra.test/go2")
    assert loaded is None and (err.code, err.status, err.reason) == (
        "upstream_status",
        302,
        None,
    )
    assert rec.urls == [
        "http://172.16.5.5/go",
        "http://backend.test/his/a.pdf",
        "http://172.16.5.5/go2",
        "http://backend.test/his/r.pdf",
    ]


def test_registered_http_backend_still_does_not_follow_redirects(monkeypatch):
    """登録した配信元の挙動は変えない: backend の 3xx は `upstream_status`(reason 無し)で、
    Location には行かない。"""
    _self(monkeypatch)
    _enable_cage_http(monkeypatch)
    rec = _Recorder(
        {
            f"{UPSTREAM}/a.png": httpx.Response(
                302, headers={"location": "http://intra.test/elsewhere.png"}
            )
        }
    )
    _install_transport(monkeypatch, rec)
    loaded, err = _url("/ext/cage/a.png")
    assert loaded is None
    assert (err.code, err.status, err.reason) == ("upstream_status", 302, None)
    assert rec.urls == [f"{UPSTREAM}/a.png"]


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
    monkeypatch.setattr("nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", 20)
    _, err = _url("http://intra.test/big")
    assert (err.code, err.limit) == ("too_large", 20)
    _, err = _url("http://intra.test/404")
    assert (err.code, err.status) == ("not_found", 404)
    _, err = _url("http://intra.test/503")
    assert (err.code, err.status, err.reason) == ("upstream_status", 503, None)
    _, err = _url("http://intra.test/down")
    assert (err.code, err.reason) == ("upstream_unreachable", None)


def test_route_of_url_general_shape(monkeypatch):
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
        name=None,
        rel="docs/%E5%9B%B3/a.png",
        query="?rev=2",
        public_base=None,
        origin="http://intra.test:8080",
        ext_form=False,
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
    # 登録した配信元の route は従来の形のまま(kind は既定の "upstream"、page_url は None)
    _enable_cage_http(monkeypatch)
    route, err = route_of_url("/ext/cage/a.png")
    assert err is None and (route.kind, route.name, route.general) == (
        "upstream",
        "cage",
        False,
    )
    assert route.page_url is None
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
