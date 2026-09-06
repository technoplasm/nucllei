"""認証付き汎用上流プロキシ /ext/{name}/** の回帰テスト(#97)。

cage 等の HTTP ツールゲートウェイが配信する文書内の図・PDF を、同一オリジン
相対 URL("/ext/{name}/...")としてブラウザが session cookie 付きで解決できる
ようにする中継。上流 allowlist は env EXT_UPSTREAMS 一本(SSRF の真実源):

- map 空(機能無効)・未知 name は 404
- セッション認証必須(get_verified_user)。未認証は素通りさせない
- percent デコード後に ".." セグメント / バックスラッシュを含むパスは 400
- upstream 2xx はストリーミング透過(content-type 維持)、404 は 404、
  redirect を含むその他は 502(Location で内部 URL を漏らさない)
- 制御文字パスは fail-closed(400 か、ASGI 層が先に弾く 404)
- アクティブ型(HTML/SVG/JS)は nosniff + 添付で実行を止める
- GET(HEAD は Starlette 既定で同経路)。POST 等は 405

#139: 値が `file:///abs/path` の name はローカル root からの静的配信(FileResponse)。
ゲートは同じで、root の外(traversal / symlink 越え)・ディレクトリ・不在は 404、
content-type は拡張子から、アクティブ型は同じく添付 + nosniff。
"""

import httpx

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict

UPSTREAM = "http://upstream.test"


class TestExtProxy(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def _enable_cage(self, monkeypatch):
        from nucllei.env import EXT_UPSTREAMS

        monkeypatch.setitem(EXT_UPSTREAMS, "cage", UPSTREAM)

    def _install_transport(self, monkeypatch, handler):
        """共有 AsyncClient を MockTransport 付きに差し替え、上流への実 HTTP を封じる。"""
        from nucllei.utils import ext_upstreams

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )
        monkeypatch.setattr(ext_upstreams, "_client", client)

    # ---- allowlist(EXT_UPSTREAMS が真実源) ----

    def test_disabled_when_no_upstreams_configured(self):
        # 既定(EXT_UPSTREAMS 空)は機能無効 = 404
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/fig.png"))
        assert response.status_code == 404

    def test_unknown_name_404(self, monkeypatch):
        self._enable_cage(monkeypatch)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/other/fig.png"))
        assert response.status_code == 404

    # ---- 認証 ----

    def test_unauthenticated_request_rejected(self, monkeypatch):
        self._enable_cage(monkeypatch)
        # 資格情報なし(cookie も Authorization も無い)は素通りさせない
        response = self.fast_api_client.get(self.create_url("/cage/fig.png"))
        assert response.status_code == 403

    def test_pending_user_401(self, monkeypatch):
        self._enable_cage(monkeypatch)
        # get_verified_user 実物を通す strict mock: pending role は 401
        with mock_webui_user_strict(id="1", role="pending"):
            response = self.fast_api_client.get(self.create_url("/cage/fig.png"))
        assert response.status_code == 401

    # ---- path traversal ----

    def test_traversal_encoded_dotdot_400(self, monkeypatch):
        self._enable_cage(monkeypatch)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url("/cage/..%2Fsecret.txt")
            )
        assert response.status_code == 400

    def test_traversal_double_encoded_400(self, monkeypatch):
        self._enable_cage(monkeypatch)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url("/cage/..%252Fsecret.txt")
            )
        assert response.status_code == 400

    def test_backslash_400(self, monkeypatch):
        self._enable_cage(monkeypatch)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/a%5Cb.png"))
        assert response.status_code == 400

    def test_control_chars_rejected(self, monkeypatch):
        # CRLF / NUL / TAB は fail-closed であること(500 やパススルーにしない)。
        # ASGI 層が先に弾けば 404、届けば共有ゲートで 400 — どちらも安全側(#97 review)
        self._enable_cage(monkeypatch)
        for encoded in ("a%0d%0ab.png", "a%00b.png", "a%09b.png"):
            with mock_webui_user(id="1"):
                response = self.fast_api_client.get(self.create_url(f"/cage/{encoded}"))
            assert response.status_code in (400, 404), (encoded, response.status_code)

    # ---- host confinement(baseURL 外へ出さない) ----

    def _streaming_handler(self, seen=None, content_type="image/png"):
        async def body():
            # bytes 直渡しは aiter_raw が StreamConsumed になる(既存テストと同様)
            yield b"payload"

        def handler(request):
            if seen is not None:
                seen.append(request)
            return httpx.Response(
                200, content=body(), headers={"content-type": content_type}
            )

        return handler

    def test_host_confinement_absolute_and_authority_paths(self, monkeypatch):
        # 絶対 URL・protocol-relative・"@" authority を path に混ぜても、
        # 転送先ホストは常に設定上流(upstream.test)のまま(#97 review)
        self._enable_cage(monkeypatch)
        for path in ("http://evil.com/x", "//evil.com/x", "a@evil.com/x"):
            seen = []
            self._install_transport(monkeypatch, self._streaming_handler(seen))
            with mock_webui_user(id="1"):
                self.fast_api_client.get(self.create_url(f"/cage/{path}"))
            assert seen, path
            assert httpx.URL(str(seen[0].url)).host == "upstream.test", path

    # ---- アクティブ型の実行防止(nosniff + 添付) ----

    def _passthrough_with_content_type(self, monkeypatch, content_type):
        self._install_transport(
            monkeypatch, self._streaming_handler(content_type=content_type)
        )

    def test_active_type_forced_to_attachment(self, monkeypatch):
        self._enable_cage(monkeypatch)
        for ct in ("text/html", "image/svg+xml", "application/javascript"):
            self._passthrough_with_content_type(monkeypatch, ct)
            with mock_webui_user(id="1"):
                response = self.fast_api_client.get(self.create_url("/cage/x"))
            assert response.headers["x-content-type-options"] == "nosniff", ct
            assert response.headers.get("content-disposition") == "attachment", ct

    def test_image_type_stays_inline(self, monkeypatch):
        self._enable_cage(monkeypatch)
        self._passthrough_with_content_type(monkeypatch, "image/png")
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/x.png"))
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "content-disposition" not in response.headers

    # ---- 透過 ----

    def test_success_passthrough(self, monkeypatch):
        self._enable_cage(monkeypatch)
        seen = []

        async def body():
            # ストリーミング応答を模擬(bytes 直渡しだと aiter_raw が
            # StreamConsumed になり、ストリーミング転送の回帰にならない)
            yield b"png-"
            yield b"bytes"

        def handler(request):
            seen.append(request)
            return httpx.Response(
                200,
                content=body(),
                headers={
                    "content-type": "image/png",
                    # 認証系・hop-by-hop は落とすことを固定
                    "set-cookie": "internal=1",
                },
            )

        self._install_transport(monkeypatch, handler)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url("/cage/figs/a.png", query_params={"v": "1"})
            )
        assert response.status_code == 200
        assert response.content == b"png-bytes"
        assert response.headers["content-type"] == "image/png"
        assert "set-cookie" not in response.headers

        # 上流 URL は baseURL + パス + クエリ。クライアントの資格情報は転送しない
        assert str(seen[0].url) == f"{UPSTREAM}/figs/a.png?v=1"
        assert "cookie" not in seen[0].headers
        assert "authorization" not in seen[0].headers

    def test_upstream_404_maps_to_404(self, monkeypatch):
        self._enable_cage(monkeypatch)
        self._install_transport(monkeypatch, lambda request: httpx.Response(404))
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/missing.png"))
        assert response.status_code == 404

    def test_upstream_redirect_maps_to_502_without_location(self, monkeypatch):
        self._enable_cage(monkeypatch)
        self._install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                302, headers={"location": "http://172.17.0.1:13303/internal"}
            ),
        )
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/fig.png"))
        assert response.status_code == 502
        assert "location" not in response.headers

    def test_upstream_unreachable_502(self, monkeypatch):
        self._enable_cage(monkeypatch)

        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        self._install_transport(monkeypatch, handler)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/fig.png"))
        assert response.status_code == 502

    # ---- メソッド ----

    def test_post_method_not_allowed(self, monkeypatch):
        self._enable_cage(monkeypatch)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.post(self.create_url("/cage/fig.png"))
        assert response.status_code == 405


class TestExtFileBackend(AbstractPostgresTest):
    """`cage=file:///...` のローカル配信(#139)。"""

    BASE_PATH = "/ext"

    def _enable_root(self, monkeypatch, root):
        from nucllei.env import EXT_UPSTREAMS

        monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}")

    def _populate(self, tmp_path):
        root = tmp_path / "cage"
        (root / "static" / "figs").mkdir(parents=True)
        (root / "static" / "figs" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\npng-bytes")
        (root / "static" / "page.html").write_text("<script>1</script>")
        (root / "static" / "v.svg").write_text("<svg/>")
        (root / "static" / "noext").write_bytes(b"?")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        (root / "static" / "escape.txt").symlink_to(outside)
        return root

    def test_success_serves_local_file(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url("/cage/static/figs/a.png", query_params={"v": "1"})
            )
        assert response.status_code == 200
        assert response.content == b"\x89PNG\r\n\x1a\npng-bytes"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "content-disposition" not in response.headers
        # FileResponse: ブラウザの PDF ビューアが投げる Range に応える
        assert response.headers.get("accept-ranges") == "bytes"
        assert "etag" in response.headers
        assert "last-modified" in response.headers

    def test_range_request(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url("/cage/static/figs/a.png"),
                headers={"range": "bytes=0-3"},
            )
        assert response.status_code == 206
        assert response.content == b"\x89PNG"

    def test_unknown_extension_is_octet_stream(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/static/noext"))
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"

    def test_directory_404(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            for path in ("/cage/static", "/cage/static/", "/cage/static/figs"):
                response = self.fast_api_client.get(self.create_url(path))
                assert response.status_code == 404, path

    def test_missing_404(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url("/cage/static/figs/missing.png")
            )
        assert response.status_code == 404

    def test_missing_root_404(self, monkeypatch, tmp_path):
        # root 自体が無い配備(bind 忘れ)は全 404(起動時 WARNING は image_refs 側)
        self._enable_root(monkeypatch, tmp_path / "nowhere")
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/cage/static/a.png"))
        assert response.status_code == 404

    def test_traversal_400(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            for path in (
                "/cage/..%2Foutside.txt",
                "/cage/static/..%2F..%2Foutside.txt",
            ):
                response = self.fast_api_client.get(self.create_url(path))
                assert response.status_code == 400, path

    def test_symlink_outside_root_404(self, monkeypatch, tmp_path):
        # ".." を含まないのでゲートは通るが、resolve 後に root の外 → 404
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url("/cage/static/escape.txt")
            )
        assert response.status_code == 404

    def test_absolute_path_in_tail_404(self, monkeypatch, tmp_path):
        # "//" 由来の先頭 "/" で Path 結合に root を捨てさせない
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(
                self.create_url(f"/cage//{tmp_path}/outside.txt")
            )
        assert response.status_code == 404

    def test_active_type_forced_to_attachment(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        for path, ct in (
            ("/cage/static/page.html", "text/html"),
            ("/cage/static/v.svg", "image/svg+xml"),
        ):
            with mock_webui_user(id="1"):
                response = self.fast_api_client.get(self.create_url(path))
            assert response.status_code == 200, path
            assert response.headers["content-type"].startswith(ct), path
            assert response.headers["x-content-type-options"] == "nosniff", path
            assert response.headers.get("content-disposition") == "attachment", path

    def test_unauthenticated_request_rejected(self, monkeypatch, tmp_path):
        root = self._populate(tmp_path)
        self._enable_root(monkeypatch, root)
        response = self.fast_api_client.get(self.create_url("/cage/static/figs/a.png"))
        assert response.status_code == 403
