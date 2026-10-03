"""認証付き参照資料の配信ゲート /ext/** の回帰テスト(#97 / #139 / 2026-10-03 設定撤去)。

cage 等が配信する文書内の図・PDF を、同一オリジン相対 URL("/ext/...")としてブラウザが
session cookie 付きで解決できるようにする静的配信。配信元に設定は無く、nucllei の `/mnt`
(`image_refs.RO_MOUNT_ROOT` — テストは tmp_path に差し替える)をそのまま配信する:
`/mnt/cage/static/X` ⇔ `/ext/cage/static/X`。

- `/mnt` が空(何も mount していない)なら全 404
- セッション認証必須(get_verified_user)。未認証は素通りさせない
- percent デコード後に ".." セグメント / バックスラッシュを含むパスは 400
- 制御文字パスは fail-closed(400 か、ASGI 層が先に弾く 404)
- `/mnt` の外(traversal / symlink 越え)・ディレクトリ(`/ext/` 自体を含む)・不在は 404
- content-type は拡張子から、アクティブ型(HTML/SVG/JS)は添付 + nosniff
- FileResponse(Range / etag / last-modified)
- GET(HEAD は Starlette 既定で同経路)。POST 等は 405

2026-10-03 までの http 中継 backend(`EXT_UPSTREAMS=name=http://…`)と `EXT_FILE_SCOPES` は
撤去(配備が `/mnt` に mount した物がそのまま配信範囲)。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict


class TestExtGate(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def _mount(self, monkeypatch, tmp_path):
        """tmp の `/mnt`。配信される物とされない物を置く。"""
        from nucllei.utils import image_refs

        root = tmp_path / "mnt"
        (root / "cage" / "static" / "figs").mkdir(parents=True)
        (root / "cage" / "static" / "figs" / "a.png").write_bytes(
            b"\x89PNG\r\n\x1a\npng-bytes"
        )
        (root / "cage" / "static" / "page.html").write_text("<script>1</script>")
        (root / "cage" / "static" / "v.svg").write_text("<svg/>")
        (root / "cage" / "static" / "noext").write_bytes(b"?")
        (root / "cage" / "static" / "図").mkdir()
        (root / "cage" / "static" / "図" / "手順 1.png").write_bytes(
            b"\x89PNG\r\n\x1a\nj"
        )
        (root / "shared").mkdir()
        (root / "shared" / "memo.txt").write_text("m")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        (root / "cage" / "static" / "escape.txt").symlink_to(outside)
        # mount の中を指す symlink(実体が中なので配信される)
        (root / "shared" / "fig.png").symlink_to(
            root / "cage" / "static" / "figs" / "a.png"
        )
        monkeypatch.setattr(image_refs, "RO_MOUNT_ROOT", root)
        return root

    def _get(self, path, **kw):
        with mock_webui_user(id="1"):
            return self.fast_api_client.get(self.create_url(path), **kw)

    # ---- /mnt が配信範囲 ----

    def test_nothing_mounted_is_404(self, monkeypatch, tmp_path):
        from nucllei.utils import image_refs

        monkeypatch.setattr(image_refs, "RO_MOUNT_ROOT", tmp_path / "nowhere")
        assert self._get("/cage/static/figs/a.png").status_code == 404
        with mock_webui_user(id="1"):
            for url in ("/ext/", "/ext"):
                assert self.fast_api_client.get(url).status_code == 404, url

    # ---- 認証 ----

    def test_unauthenticated_request_rejected(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        # 資格情報なし(cookie も Authorization も無い)は素通りさせない
        response = self.fast_api_client.get(self.create_url("/cage/static/figs/a.png"))
        assert response.status_code == 403
        for url in ("/ext/", "/ext", "/ext/cage/"):
            assert self.fast_api_client.get(url).status_code == 403, url

    def test_pending_user_401(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        # get_verified_user 実物を通す strict mock: pending role は 401
        with mock_webui_user_strict(id="1", role="pending"):
            response = self.fast_api_client.get(
                self.create_url("/cage/static/figs/a.png")
            )
        assert response.status_code == 401

    # ---- 配信 ----

    def test_success_serves_mounted_file(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        response = self._get("/cage/static/figs/a.png", params={"v": "1"})
        assert response.status_code == 200
        assert response.content == b"\x89PNG\r\n\x1a\npng-bytes"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "content-disposition" not in response.headers
        # FileResponse: ブラウザの PDF ビューアが投げる Range に応える
        assert response.headers.get("accept-ranges") == "bytes"
        assert "etag" in response.headers
        assert "last-modified" in response.headers
        # 何でも: /mnt に見えている物は全部(cage に限らない)
        assert self._get("/shared/memo.txt").content == b"m"

    def test_percent_encoded_japanese_path(self, monkeypatch, tmp_path):
        # 文書に焼かれた URL の形(percent-encoded)。Starlette が decode した path で当たる
        self._mount(monkeypatch, tmp_path)
        response = self._get("/cage/static/%E5%9B%B3/%E6%89%8B%E9%A0%86%201.png")
        assert response.status_code == 200
        assert response.content == b"\x89PNG\r\n\x1a\nj"

    def test_symlink_into_mount_is_served_symlink_out_is_not(
        self, monkeypatch, tmp_path
    ):
        # 判定は resolve 後の実体: mount の外へ出る symlink → 404、中を指す symlink は配信
        self._mount(monkeypatch, tmp_path)
        assert self._get("/cage/static/escape.txt").status_code == 404
        assert self._get("/shared/fig.png").status_code == 200

    def test_mount_itself_may_be_a_symlink(self, monkeypatch, tmp_path):
        # 配備では /mnt(の下)が bind = symlink でありうる。閉じ込めは resolve 済み同士で比べる
        from nucllei.utils import image_refs

        root = self._mount(monkeypatch, tmp_path)
        link = tmp_path / "mnt-link"
        link.symlink_to(root)
        monkeypatch.setattr(image_refs, "RO_MOUNT_ROOT", link)
        assert self._get("/cage/static/figs/a.png").status_code == 200
        assert self._get("/cage/static/escape.txt").status_code == 404

    def test_range_request(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        response = self._get("/cage/static/figs/a.png", headers={"range": "bytes=0-3"})
        assert response.status_code == 206
        assert response.content == b"\x89PNG"

    def test_unknown_extension_is_octet_stream(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        response = self._get("/cage/static/noext")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"

    def test_directory_404(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        for path in ("/cage", "/cage/static", "/cage/static/", "/cage/static/図"):
            assert self._get(path).status_code == 404, path
        # /ext 自体も mount = ディレクトリ(resolver は not_found)。create_url は
        # 末尾 "/" を落とすので直に書く
        with mock_webui_user(id="1"):
            for url in ("/ext/", "/ext", "/ext/cage/", "/ext/cage/static/"):
                assert self.fast_api_client.get(url).status_code == 404, url

    def test_missing_404(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        assert self._get("/cage/static/figs/missing.png").status_code == 404
        assert self._get("/nothere/a.png").status_code == 404

    # ---- path traversal ----

    def test_traversal_400(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        for path in (
            "/..%2Foutside.txt",
            "/cage/..%2F..%2Foutside.txt",
            "/cage/..%252Foutside.txt",
            "/cage/a%5Cb.png",
        ):
            assert self._get(path).status_code == 400, path

    def test_control_chars_rejected(self, monkeypatch, tmp_path):
        # CRLF / NUL / TAB は fail-closed であること(500 やパススルーにしない)。
        # ASGI 層が先に弾けば 404、届けば共有ゲートで 400 — どちらも安全側(#97 review)
        self._mount(monkeypatch, tmp_path)
        for encoded in ("a%0d%0ab.png", "a%00b.png", "a%09b.png"):
            response = self._get(f"/cage/{encoded}")
            assert response.status_code in (400, 404), (encoded, response.status_code)

    def test_absolute_path_in_tail_404(self, monkeypatch, tmp_path):
        # "//" 由来の先頭 "/" で Path 結合に mount を捨てさせない
        self._mount(monkeypatch, tmp_path)
        assert self._get(f"//{tmp_path}/outside.txt").status_code == 404

    def test_host_like_paths_stay_local(self, monkeypatch, tmp_path):
        # 絶対 URL・protocol-relative・"@" authority を path に混ぜても、ただのローカルの
        # 相対パス(不在 = 404)— どこにも接続しない
        self._mount(monkeypatch, tmp_path)
        for path in ("http://evil.com/x", "//evil.com/x", "a@evil.com/x"):
            assert self._get(f"/{path}").status_code == 404, path

    # ---- アクティブ型の実行防止(nosniff + 添付) ----

    def test_active_type_forced_to_attachment(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        for path, ct in (
            ("/cage/static/page.html", "text/html"),
            ("/cage/static/v.svg", "image/svg+xml"),
        ):
            response = self._get(path)
            assert response.status_code == 200, path
            assert response.headers["content-type"].startswith(ct), path
            assert response.headers["x-content-type-options"] == "nosniff", path
            assert response.headers.get("content-disposition") == "attachment", path

    # ---- メソッド ----

    def test_post_method_not_allowed(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.post(
                self.create_url("/cage/static/figs/a.png")
            )
        assert response.status_code == 405
