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


def test_relative_ext(monkeypatch, tmp_path):
    root = _mount(monkeypatch, tmp_path)
    from nucllei.env import EXT_UPSTREAMS

    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
    # backend が有っても、"/" 始まりの /ext/ は URL として渡し直させる
    assert _code("/ext/cage/static/a.png").code == "relative_ext"


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
    assert err.code == "unknown_upstream"
    assert "EXT_UPSTREAMS" in err.detail and "nothere" in err.detail


def test_unknown_upstream_public_base_without_backend(monkeypatch):
    from nucllei.env import EXT_PUBLIC_BASES

    monkeypatch.setitem(EXT_PUBLIC_BASES, "his", PUBLIC_BASE)
    err = _code(f"{PUBLIC_BASE}/a.png")
    assert err.code == "unknown_upstream"
    assert "EXT_UPSTREAMS" in err.detail and "his" in err.detail


def test_unknown_upstream_external_site(monkeypatch):
    _enable_cage_http(monkeypatch)
    err = _code("https://his.example/x.png")
    assert err.code == "unknown_upstream"
    assert "external site" in err.detail and "his.example" in err.detail
    assert "EXT_PUBLIC_BASES" in err.detail


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


def test_not_found_public_base_empty_path(monkeypatch):
    from nucllei.env import EXT_PUBLIC_BASES

    _enable_cage_http(monkeypatch)
    monkeypatch.setitem(EXT_PUBLIC_BASES, "cage", PUBLIC_BASE)
    assert _code(f"{PUBLIC_BASE}/").code == "not_found"


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
