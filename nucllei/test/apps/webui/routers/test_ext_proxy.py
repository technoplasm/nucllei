"""認証付き参照資料の配信ゲート /ext/** の回帰テスト(#97 / #139 / #146)。

入口は env `EXT_MOUNTS` 1 本の対応表(2026-10-05): `/ext/<name>` → 値。値が `http(s)://` なら
proxy(TestExtProxy — 上流は httpx.MockTransport の fake)、絶対パスなら静的配信(TestExtGate —
テストは `mount_tree` で tmp の木の各ディレクトリを mount にする)。表に無い name は 404。
2026-10-03〜05 の `EXT_DIR`(置いた物を暗黙に全部配る)は撤去。

静的:

- 表が空なら全 404。nucllei 自身の `/mnt` `/ext` は見ない(表に書いた dir だけ)
- セッション認証必須(get_verified_user)。未認証は素通りさせない
- percent デコード後に ".." セグメント / バックスラッシュを含むパスは 400
- 制御文字パスは fail-closed(400 か、ASGI 層が先に弾く 404)
- mount の外(traversal / symlink 越え)・ディレクトリ(`/ext/` 自体を含む)・不在は 404
- content-type は拡張子から。HTML(`.html`/`.htm`)は厳しい CSP 付きで inline、SVG/JS は添付 + nosniff
- 補完(`X.html` / `X/index.html`)は無い — 2026-10-05 に一度入れ、doc の HTML を cage の proxy に
  持たせたので撤去
- FileResponse(Range / etag / last-modified)
- GET / HEAD。POST 等は 405
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.ext_mounts import mount_tree
from test.util.mock_user import mock_webui_user, mock_webui_user_strict


class TestExtGate(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def _mount(self, monkeypatch, tmp_path):
        """tmp の木 `root/<name>/…` を静的 mount に。配信される物とされない物を置く。"""

        root = tmp_path / "data" / "ext"
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
        # 同じ mount の中を指す symlink(実体が中なので配信される)
        (root / "cage" / "static" / "alias.png").symlink_to(
            root / "cage" / "static" / "figs" / "a.png"
        )
        # 別の mount を指す symlink は、その mount(shared)の外 = 配信しない
        (root / "shared" / "fig.png").symlink_to(
            root / "cage" / "static" / "figs" / "a.png"
        )
        mount_tree(monkeypatch, root)
        return root

    def _get(self, path, **kw):
        with mock_webui_user(id="1"):
            return self.fast_api_client.get(self.create_url(path), **kw)

    # ---- 表に書いた物だけが配信範囲 ----

    def test_nothing_mounted_is_404(self, monkeypatch, tmp_path):
        mount_tree(monkeypatch, tmp_path / "nowhere")
        assert self._get("/cage/static/figs/a.png").status_code == 404
        with mock_webui_user(id="1"):
            for url in ("/ext/", "/ext"):
                assert self.fast_api_client.get(url).status_code == 404, url

    def test_unlisted_name_is_404_even_if_the_fs_has_it(self, monkeypatch, tmp_path):
        """表に無い name は、nucllei の fs に同じパスが在っても 404(暗黙に配らない。host 直起動で
        host の `/mnt` 等を晒さない — ken 指摘 2026-10-03)。表に書いて初めて 200。"""
        from nucllei import env

        host_mnt = tmp_path / "host-mnt" / "x"
        host_mnt.mkdir(parents=True)
        (host_mnt / "a.txt").write_text("nfs")
        monkeypatch.setattr(env, "EXT_MOUNTS", {})
        assert self._get("/x/a.txt").status_code == 404
        monkeypatch.setattr(env, "EXT_MOUNTS", {"x": str(host_mnt)})
        response = self._get("/x/a.txt")
        assert response.status_code == 200 and response.content == b"nfs"

    def test_mount_root_may_be_a_symlink_contents_stay_inside(
        self, monkeypatch, tmp_path
    ):
        """静的の値(絶対パス)自体は symlink でもよい(resolve した先が root)。mount の中身の
        symlink は root の外へ出られない(ファイルもディレクトリも)。"""
        from nucllei import env

        content = tmp_path / "llens" / "cage" / "data" / "static"
        (content / "figs").mkdir(parents=True)
        (content / "figs" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nmounted")
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        (content / "escape.txt").symlink_to(secret)  # 中身の木から外へ
        (content / "etc").symlink_to(tmp_path)  # 中身の木のディレクトリ symlink も外へ
        link = tmp_path / "static-link"
        link.symlink_to(content)
        monkeypatch.setattr(env, "EXT_MOUNTS", {"static": str(link)})

        assert self._get("/static/figs/a.png").content.endswith(b"mounted")
        assert self._get("/static/escape.txt").status_code == 404
        assert self._get("/static/etc/secret.txt").status_code == 404
        assert self._get("/static").status_code == 404
        assert self._get("/static/figs").status_code == 404

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
        # 表に書いた name なら何でも(cage に限らない)
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
        # 判定は resolve 後の実体: mount の外へ出る symlink → 404、中を指す symlink は配信。
        # 別の name の mount を指す symlink も、その name の root の外なので 404
        self._mount(monkeypatch, tmp_path)
        assert self._get("/cage/static/escape.txt").status_code == 404
        assert self._get("/cage/static/alias.png").status_code == 200
        assert self._get("/shared/fig.png").status_code == 404

    def test_mount_itself_may_be_a_symlink(self, monkeypatch, tmp_path):
        # 値のパスの途中が symlink でもよい。閉じ込めは resolve 済み同士で比べる

        root = self._mount(monkeypatch, tmp_path)
        link = tmp_path / "ext-link"
        link.symlink_to(root)
        mount_tree(monkeypatch, link)
        assert self._get("/cage/static/figs/a.png").status_code == 200
        assert self._get("/cage/static/escape.txt").status_code == 404

    def test_head_is_served(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        response = self._get_head("/cage/static/figs/a.png")
        assert response.status_code == 200 and response.content == b""
        assert response.headers["content-type"] == "image/png"

    def _get_head(self, path):
        with mock_webui_user(id="1"):
            return self.fast_api_client.head(self.create_url(path))

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

    def test_markdown_and_text_are_served_as_plain_text(self, monkeypatch, tmp_path):
        """#145 項目 1: `.md` / `.txt` はブラウザでそのまま読める text/plain(UTF-8)。
        mimetypes が `.md` を知らない環境で octet-stream + nosniff になり、リンクを押すと
        ダウンロードになっていた。アクティブ型ではないので添付にしない(nosniff は残す)。"""
        root = self._mount(monkeypatch, tmp_path)
        (root / "cage" / "static" / "doc.md").write_text("# 見出し", encoding="utf-8")
        (root / "cage" / "static" / "UPPER.MD").write_text("x", encoding="utf-8")
        for path in (
            "/cage/static/doc.md",
            "/cage/static/UPPER.MD",
            "/shared/memo.txt",
        ):
            response = self._get(path)
            assert response.status_code == 200, path
            assert response.headers["content-type"] == "text/plain; charset=utf-8", path
            assert response.headers["x-content-type-options"] == "nosniff", path
            assert "content-disposition" not in response.headers, path
        assert self._get("/cage/static/doc.md").content.decode("utf-8") == "# 見出し"

    # ---- アクティブ型の実行防止(nosniff + 添付) ----

    def test_active_type_forced_to_attachment(self, monkeypatch, tmp_path):
        # HTML は下の CSP 付き inline(2026-10-05)。それ以外のアクティブ型は従来どおり添付
        self._mount(monkeypatch, tmp_path)
        response = self._get("/cage/static/v.svg")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers.get("content-disposition") == "attachment"

    # ---- HTML の CSP(2026-10-05)と補完が無いこと ----

    def _mount_pages(self, monkeypatch, tmp_path):
        """cage がビルドした doc のページの形(`<住所>.html` / `<住所>/index.html`)を静的の
        `cage` mount の `doc/` に置く。"""
        root = self._mount(monkeypatch, tmp_path)
        doc = root / "cage" / "doc"
        (doc / "院内マニュアル" / "医療安全管理マニュアル").mkdir(parents=True)
        (doc / "院内マニュアル" / "index.html").write_text(
            "dir-index", encoding="utf-8"
        )
        (doc / "院内マニュアル" / "医療安全管理マニュアル" / "index.html").write_text(
            "anzen-index", encoding="utf-8"
        )
        (doc / "院内マニュアル" / "電話番号簿.html").write_text("tel", encoding="utf-8")
        # X.html と X/index.html が両方ある → X.html が先
        (doc / "both").mkdir()
        (doc / "both" / "index.html").write_text("both-dir", encoding="utf-8")
        (doc / "both.html").write_text("both-file", encoding="utf-8")
        # 実在のファイル X は補完より先
        (doc / "plain").write_text("plain-file", encoding="utf-8")
        (doc / "plain.html").write_text("plain-html", encoding="utf-8")
        (doc / "nothing").mkdir()
        (doc / "memo.md").write_text("# memo", encoding="utf-8")
        outside = tmp_path / "outside-pages"
        outside.mkdir()
        (outside / "index.html").write_text("secret", encoding="utf-8")
        (outside / "s.html").write_text("secret", encoding="utf-8")
        (doc / "esc").symlink_to(outside)  # mount の中から外へのディレクトリ symlink
        (doc / "leak.html").symlink_to(outside / "s.html")
        return doc

    def test_no_completion(self, monkeypatch, tmp_path):
        """静的配信に `X` → `X.html` → `X/index.html` の補完は無い(2026-10-05 に一度入れて撤去 —
        doc の HTML は cage が中継で返す)。実在のファイルだけが 200、ディレクトリは 404。"""
        self._mount_pages(monkeypatch, tmp_path)
        for path in (
            "/cage/doc/院内マニュアル/電話番号簿",
            "/cage/doc/院内マニュアル/医療安全管理マニュアル",
            "/cage/doc/院内マニュアル",
            "/cage/doc/院内マニュアル/",
            "/cage/doc/both",
            "/cage/doc/nothing",
            "/cage/doc/esc",
            "/cage/doc/esc/",
            "/cage/doc/leak.html",  # mount の外を指す
        ):
            assert self._get(path).status_code == 404, path
        for path, body in {
            "/cage/doc/院内マニュアル/電話番号簿.html": "tel",
            "/cage/doc/院内マニュアル/index.html": "dir-index",
            "/cage/doc/plain": "plain-file",
        }.items():
            response = self._get(path)
            assert response.status_code == 200 and response.text == body, path

    def test_html_gets_strict_csp(self, monkeypatch, tmp_path):
        from nucllei.routers.ext import HTML_CSP

        self._mount_pages(monkeypatch, tmp_path)
        assert HTML_CSP == (
            "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
        for path in (
            "/cage/static/page.html",
            "/cage/doc/院内マニュアル/index.html",
            "/cage/doc/院内マニュアル/電話番号簿.html",
        ):
            response = self._get(path)
            assert response.status_code == 200, path
            assert response.headers["content-type"] == "text/html; charset=utf-8", path
            assert response.headers["content-security-policy"] == HTML_CSP, path
            assert response.headers["x-content-type-options"] == "nosniff", path
            assert "content-disposition" not in response.headers, path
        # .htm も同じ
        (tmp_path / "data" / "ext" / "cage" / "static" / "old.htm").write_text("x")
        response = self._get("/cage/static/old.htm")
        assert response.headers["content-security-policy"] == HTML_CSP
        # HTML 以外には付けない
        for path in ("/cage/static/figs/a.png", "/cage/doc/memo.md"):
            assert "content-security-policy" not in self._get(path).headers, path

    def test_markdown_unchanged_regardless_of_accept(self, monkeypatch, tmp_path):
        """`.md` は Accept に関わらず text/plain(HTML 化はしない — cage がビルドする)。"""
        self._mount_pages(monkeypatch, tmp_path)
        for accept in ("*/*", "text/html,application/xhtml+xml,*/*;q=0.8"):
            response = self._get("/cage/doc/memo.md", headers={"accept": accept})
            assert response.headers["content-type"] == "text/plain; charset=utf-8"
            assert response.text == "# memo"

    def test_pdf_range_and_etag_unchanged(self, monkeypatch, tmp_path):
        root = self._mount_pages(monkeypatch, tmp_path)
        (root / "a.pdf").write_bytes(b"%PDF-1.4 body")
        full = self._get("/cage/doc/a.pdf")
        assert full.headers["content-type"] == "application/pdf"
        assert "etag" in full.headers and full.headers["accept-ranges"] == "bytes"
        assert "content-security-policy" not in full.headers
        part = self._get("/cage/doc/a.pdf", headers={"range": "bytes=0-3"})
        assert part.status_code == 206 and part.content == b"%PDF"

    # ---- メソッド ----

    def test_post_method_not_allowed(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        with mock_webui_user(id="1"):
            response = self.fast_api_client.post(
                self.create_url("/cage/static/figs/a.png")
            )
        assert response.status_code == 405


class TestExtProxy(AbstractPostgresTest):
    """`EXT_UPSTREAMS` の name は上流 HTTP へ中継する(#146、2026-10-05)。上流は
    httpx.MockTransport の fake(実ネットワークに出ない)。"""

    BASE_PATH = "/ext"
    BASE = "http://cage.test:13303/root"

    def _setup(self, monkeypatch, tmp_path, handler):
        import httpx

        from nucllei.utils import ext_upstreams

        root = tmp_path / "data" / "ext"
        (root / "shared").mkdir(parents=True, exist_ok=True)
        (root / "shared" / "memo.txt").write_text("m")
        # EXT_MOUNTS="shared=<root>/shared,cage=http://cage.test:13303/root"
        mount_tree(monkeypatch, root, cage=self.BASE)
        seen: list[httpx.Request] = []

        def record(request):
            seen.append(request)
            return handler(request)

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(record),
            follow_redirects=False,
            cookies=ext_upstreams._NoCookies(),
        )
        monkeypatch.setattr(ext_upstreams, "_client", client)
        return seen

    @staticmethod
    def _page(request):
        import httpx

        body = b"0123456789"
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304, headers={"etag": '"v1"'})
        common = {
            "etag": '"v1"',
            "last-modified": "Mon, 05 Oct 2026 00:00:00 GMT",
            "accept-ranges": "bytes",
            "cache-control": "max-age=60",
            "set-cookie": "upstream=1; Path=/",
            "x-internal": "leak",
            "content-type": "application/pdf",
        }
        if request.headers.get("range") == "bytes=0-3":
            return httpx.Response(
                206,
                content=body[:4],
                headers={**common, "content-range": "bytes 0-3/10"},
            )
        return httpx.Response(200, content=body, headers=common)

    def _get(self, path, method="GET", **kw):
        with mock_webui_user(id="1"):
            return self.fast_api_client.request(method, self.create_url(path), **kw)

    # ---- 認証 ----

    def test_unauthenticated_rejected_and_not_forwarded(self, monkeypatch, tmp_path):
        seen = self._setup(monkeypatch, tmp_path, self._page)
        assert self.fast_api_client.get("/ext/cage/a.pdf").status_code == 403
        with mock_webui_user_strict(id="1", role="pending"):
            assert self.fast_api_client.get("/ext/cage/a.pdf").status_code == 401
        assert seen == []

    # ---- 中継の基本 ----

    def test_forwards_path_and_query_under_base(self, monkeypatch, tmp_path):
        seen = self._setup(monkeypatch, tmp_path, self._page)
        response = self._get("/cage/doc/a.pdf", params={"v": "1", "q": "x y"})
        assert response.status_code == 200 and response.content == b"0123456789"
        assert str(seen[-1].url) == f"{self.BASE}/doc/a.pdf?v=1&q=x+y"
        assert seen[-1].method == "GET"
        # 日本語の住所は percent-encoded で上流へ。name 直下(末尾 "/" 無しも)は <base>/
        self._get("/cage/doc/院内マニュアル/医療安全 管理")
        assert seen[-1].url.raw_path.decode() == (
            "/root/doc/%E9%99%A2%E5%86%85%E3%83%9E%E3%83%8B%E3%83%A5%E3%82%A2%E3%83%AB"
            "/%E5%8C%BB%E7%99%82%E5%AE%89%E5%85%A8%20%E7%AE%A1%E7%90%86"
        )
        for path in ("/ext/cage", "/ext/cage/"):
            with mock_webui_user(id="1"):
                assert self.fast_api_client.get(path).status_code == 200
            assert str(seen[-1].url) == f"{self.BASE}/"
        # path に紛れた "?" "#" "@" ":" は path の中に閉じる(base の外へ出ない)
        self._get("/cage/a%3Fb%23c@evil.com:80/x")
        assert seen[-1].url.host == "cage.test"
        assert seen[-1].url.raw_path.decode() == "/root/a%3Fb%23c%40evil.com%3A80/x"

    def test_head_is_forwarded_as_head(self, monkeypatch, tmp_path):
        seen = self._setup(monkeypatch, tmp_path, self._page)
        response = self._get("/cage/a.pdf", method="HEAD")
        assert response.status_code == 200
        assert seen[-1].method == "HEAD"
        with mock_webui_user(id="1"):
            assert self.fast_api_client.post("/ext/cage/a.pdf").status_code == 405
        assert len(seen) == 1

    def test_range_206_and_etag_304_pass_through(self, monkeypatch, tmp_path):
        seen = self._setup(monkeypatch, tmp_path, self._page)
        part = self._get("/cage/a.pdf", headers={"range": "bytes=0-3"})
        assert part.status_code == 206 and part.content == b"0123"
        assert part.headers["content-range"] == "bytes 0-3/10"
        assert part.headers["accept-ranges"] == "bytes"
        assert seen[-1].headers["range"] == "bytes=0-3"
        full = self._get("/cage/a.pdf")
        assert full.headers["etag"] == '"v1"'
        assert full.headers["last-modified"] == "Mon, 05 Oct 2026 00:00:00 GMT"
        assert full.headers["cache-control"] == "max-age=60"
        assert full.headers["content-length"] == "10"
        assert full.headers["content-type"] == "application/pdf"
        assert full.headers["x-content-type-options"] == "nosniff"
        cached = self._get("/cage/a.pdf", headers={"if-none-match": '"v1"'})
        assert cached.status_code == 304 and cached.content == b""
        assert seen[-1].headers["if-none-match"] == '"v1"'

    def test_upstream_404_passes_through(self, monkeypatch, tmp_path):
        import httpx

        self._setup(monkeypatch, tmp_path, lambda r: httpx.Response(404, text="nf"))
        assert self._get("/cage/doc/missing").status_code == 404

    def test_request_headers_are_allowlisted(self, monkeypatch, tmp_path):
        seen = self._setup(monkeypatch, tmp_path, self._page)
        self._get(
            "/cage/a.pdf",
            headers={
                "cookie": "token=secret; other=1",
                "authorization": "Bearer secret",
                "x-forwarded-for": "10.0.0.1",
                "accept": "text/html",
                "accept-language": "ja",
                "if-modified-since": "Mon, 05 Oct 2026 00:00:00 GMT",
                "accept-encoding": "gzip",
            },
        )
        sent = seen[-1].headers
        assert "cookie" not in sent
        assert "authorization" not in sent
        assert "x-forwarded-for" not in sent
        assert sent["accept"] == "text/html"
        assert sent["accept-language"] == "ja"
        assert sent["if-modified-since"] == "Mon, 05 Oct 2026 00:00:00 GMT"
        assert sent["accept-encoding"] == "identity"

    def test_response_headers_are_allowlisted(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, self._page)
        response = self._get("/cage/a.pdf")
        assert "set-cookie" not in response.headers
        assert "x-internal" not in response.headers
        assert "upstream" not in self.fast_api_client.cookies

    # ---- HTML / アクティブ型 ----

    def test_html_gets_csp_overriding_upstream(self, monkeypatch, tmp_path):
        import httpx

        from nucllei.routers.ext import HTML_CSP

        def handler(request):
            return httpx.Response(
                200,
                text="<h1 id='a'>doc</h1>",
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "content-security-policy": "default-src *",
                    "x-content-type-options": "bogus",
                    "content-disposition": "inline",
                },
            )

        self._setup(monkeypatch, tmp_path, handler)
        response = self._get("/cage/doc/院内マニュアル/医療安全管理マニュアル")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["content-security-policy"] == HTML_CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.text == "<h1 id='a'>doc</h1>"

    def test_svg_is_attachment(self, monkeypatch, tmp_path):
        import httpx

        self._setup(
            monkeypatch,
            tmp_path,
            lambda r: httpx.Response(
                200, text="<svg/>", headers={"content-type": "image/svg+xml"}
            ),
        )
        response = self._get("/cage/static/v.svg")
        assert response.headers["content-disposition"] == "attachment"
        assert "content-security-policy" not in response.headers

    # ---- リダイレクト ----

    def test_redirect_under_base_is_rewritten(self, monkeypatch, tmp_path):
        import httpx

        targets = {
            "/root/doc/x": "/root/doc/x/",  # 絶対パス
            "/root/doc/y": "z.html?a=1",  # 相対
            "/root/doc/w": f"{self.BASE}/doc/%E5%9B%B3",  # 絶対 URL
            "/root/doc/top": "HTTP://CAGE.TEST:13303/root",  # base 自身(大文字の host)
        }

        def handler(request):
            return httpx.Response(
                302 if request.url.path != "/root/doc/x" else 301,
                headers={
                    "location": targets[request.url.path],
                    "set-cookie": "s=1",
                },
            )

        self._setup(monkeypatch, tmp_path, handler)
        expected = {
            "/cage/doc/x": (301, "/ext/cage/doc/x/"),
            "/cage/doc/y": (302, "/ext/cage/doc/z.html?a=1"),
            "/cage/doc/w": (302, "/ext/cage/doc/%E5%9B%B3"),
            "/cage/doc/top": (302, "/ext/cage/"),
        }
        for path, (status, location) in expected.items():
            response = self._get(path, follow_redirects=False)
            assert response.status_code == status, path
            assert response.headers["location"] == location, path
            assert "set-cookie" not in response.headers, path

    def test_redirect_outside_base_is_502(self, monkeypatch, tmp_path):
        import httpx

        targets = {
            "/root/a": "http://other.test/root/a",  # 別 host
            "/root/b": "https://cage.test:13303/root/b",  # 別 scheme
            "/root/c": "/rootx/c",  # base の prefix だけ一致
            "/root/d": "/elsewhere",
            "/root/e": "../outside",  # 相対で base の外へ
            "/root/f": "/root/%2e%2e/x",  # encoded の ..
        }

        def handler(request):
            path = request.url.path
            if path == "/root/none":
                return httpx.Response(302)
            return httpx.Response(302, headers={"location": targets[path]})

        self._setup(monkeypatch, tmp_path, handler)
        for path in (*[p.replace("/root", "/cage") for p in targets], "/cage/none"):
            response = self._get(path, follow_redirects=False)
            assert response.status_code == 502, path
            assert "location" not in response.headers, path

    # ---- パスの拒否 ----

    def test_traversal_400_not_forwarded(self, monkeypatch, tmp_path):
        seen = self._setup(monkeypatch, tmp_path, self._page)
        for path in (
            "/cage/..%2Fsecret",
            "/cage/doc/..%2F..%2Fsecret",
            "/cage/..%252Fsecret",
            "/cage/a%5Cb",
        ):
            assert self._get(path).status_code == 400, path
        for encoded in ("a%0d%0ab", "a%00b", "a%09b"):
            assert self._get(f"/cage/{encoded}").status_code in (400, 404), encoded
        assert seen == []

    # ---- 上流の不調 ----

    def test_upstream_down_502_and_timeout_504(self, monkeypatch, tmp_path):
        import httpx

        def down(request):
            raise httpx.ConnectError("refused", request=request)

        self._setup(monkeypatch, tmp_path, down)
        assert self._get("/cage/a.pdf").status_code == 502

        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        self._setup(monkeypatch, tmp_path, slow)
        assert self._get("/cage/a.pdf").status_code == 504

    # ---- 静的の name と並ぶ ----

    def test_static_name_next_to_proxy_name(self, monkeypatch, tmp_path):
        """同じ表に静的の name(shared)と proxy の name(cage)が並ぶ。静的は上流に行かない。
        表に無い name はどちらでもなく 404。"""
        seen = self._setup(monkeypatch, tmp_path, self._page)
        response = self._get("/shared/memo.txt")
        assert response.status_code == 200 and response.content == b"m"
        assert self._get("/shared/nothere.txt").status_code == 404
        assert self._get("/nothere/a.pdf").status_code == 404
        assert seen == []
        assert self._get("/cage/a.pdf").status_code == 200
        assert len(seen) == 1
