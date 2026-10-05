"""認証付き参照資料の配信ゲート /ext/** の回帰テスト(#97 / #139 / 2026-10-03 設定撤去)。

cage 等が配信する文書内の図・PDF を、同一オリジン相対 URL("/ext/...")としてブラウザが
session cookie 付きで解決できるようにする静的配信。配信元に設定は無く、nucllei の
`EXT_DIR` = `EXT_DIR`(`image_refs.EXT_DIR` — テストは tmp_path に差し替える)を
そのまま配信する: `ext/cage/static/X` ⇔ `/ext/cage/static/X`(sandbox も同じ物を
同じ文字列 `/ext/cage/static/X` に見る)。2026-10-03 の朝まではコンテナの `/mnt` 固定だった —
nucllei は docker 専用でなく、host 直起動で host の `/mnt` を晒すので `DATA_DIR` の中に改めた。

- `EXT_DIR` が空(何も置いていない)なら全 404。nucllei 自身の `/mnt` `/ext` は見ない
- セッション認証必須(get_verified_user)。未認証は素通りさせない
- percent デコード後に ".." セグメント / バックスラッシュを含むパスは 400
- 制御文字パスは fail-closed(400 か、ASGI 層が先に弾く 404)
- `EXT_DIR` の外(traversal / symlink 越え)・ディレクトリ(`/ext/` 自体を含む)・不在は 404
- content-type は拡張子から。HTML(`.html`/`.htm`)は厳しい CSP 付きで inline、SVG/JS は添付 + nosniff
- URL の契約(2026-10-05): `X` → `X.html` → `X/index.html` の補完(mount の中に閉じる、一覧は出さない)
- FileResponse(Range / etag / last-modified)
- GET(HEAD は Starlette 既定で同経路)。POST 等は 405

2026-10-03 までの http 中継 backend(`EXT_UPSTREAMS=name=http://…`)と `EXT_FILE_SCOPES` は
撤去(配備が `EXT_DIR` に置いた物がそのまま配信範囲)。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict


class TestExtGate(AbstractPostgresTest):
    BASE_PATH = "/ext"

    def _mount(self, monkeypatch, tmp_path):
        """tmp の `EXT_DIR`。配信される物とされない物を置く。"""
        from nucllei.utils import image_refs

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
        # mount の中を指す symlink(実体が中なので配信される)
        (root / "shared" / "fig.png").symlink_to(
            root / "cage" / "static" / "figs" / "a.png"
        )
        monkeypatch.setattr(image_refs, "EXT_DIR", root)
        return root

    def _get(self, path, **kw):
        with mock_webui_user(id="1"):
            return self.fast_api_client.get(self.create_url(path), **kw)

    # ---- EXT_DIR が配信範囲 ----

    def test_nothing_mounted_is_404(self, monkeypatch, tmp_path):
        from nucllei.utils import image_refs

        monkeypatch.setattr(image_refs, "EXT_DIR", tmp_path / "nowhere")
        assert self._get("/cage/static/figs/a.png").status_code == 404
        with mock_webui_user(id="1"):
            for url in ("/ext/", "/ext"):
                assert self.fast_api_client.get(url).status_code == 404, url

    def test_host_mnt_is_never_served(self, monkeypatch, tmp_path):
        """served root は `EXT_DIR` だけ — host(= nucllei プロセス)の `/mnt/X` や `/ext/X` に
        同名のファイルが在っても `/ext/X` では出ない(host 直起動で NFS 等を晒さない。
        ken 指摘 2026-10-03)。`EXT_DIR/X` を置いて初めて 200。"""
        from nucllei.utils import image_refs

        root = tmp_path / "data" / "ext"
        root.mkdir(parents=True)
        monkeypatch.setattr(image_refs, "EXT_DIR", root)
        # 「host の /mnt に在る」を模す: EXT_DIR の外にある物を、URL の rest と同じ相対で用意する
        host_mnt = tmp_path / "host-mnt" / "x"
        host_mnt.mkdir(parents=True)
        (host_mnt / "a.txt").write_text("nfs")
        probe = "/x/a.txt"
        assert self._get(probe).status_code == 404
        # EXT_DIR の中に置けば(symlink でもよい)出る
        (root / "x").symlink_to(host_mnt)
        response = self._get(probe)
        assert response.status_code == 200 and response.content == b"nfs"

    def test_directory_symlink_in_ext_dir_is_a_mount(self, monkeypatch, tmp_path):
        """host 直起動の規約: `ext/cage/static → <どこか>` のような **ディレクトリの
        symlink** は配備が置いた mount(Docker の volume と同じ役)— 先を配信する。ただし
        mount の中身(文書側の木)の symlink は mount の外へ出られず、ファイルの symlink は
        mount にならない(`image_refs.served_mount`)。"""
        from nucllei.utils import image_refs

        root = tmp_path / "data" / "ext"
        (root / "cage").mkdir(parents=True)
        monkeypatch.setattr(image_refs, "EXT_DIR", root)
        content = tmp_path / "llens" / "cage" / "data" / "static"
        (content / "figs").mkdir(parents=True)
        (content / "figs" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nmounted")
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        (content / "escape.txt").symlink_to(secret)  # 中身の木から外へ
        (content / "etc").symlink_to(tmp_path)  # 中身の木のディレクトリ symlink も外へ
        (root / "cage" / "static").symlink_to(content)  # 配備が置いた mount
        (root / "one.txt").symlink_to(secret)  # ファイルの symlink は mount にならない

        assert self._get("/cage/static/figs/a.png").content.endswith(b"mounted")
        assert self._get("/cage/static/escape.txt").status_code == 404
        assert self._get("/cage/static/etc/secret.txt").status_code == 404
        assert self._get("/one.txt").status_code == 404
        assert self._get("/cage/static").status_code == 404

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
        # 何でも: EXT_DIR に見えている物は全部(cage に限らない)
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
        # host 直起動では ext/(の下)が symlink でありうる。閉じ込めは resolve 済み同士で比べる
        from nucllei.utils import image_refs

        root = self._mount(monkeypatch, tmp_path)
        link = tmp_path / "ext-link"
        link.symlink_to(root)
        monkeypatch.setattr(image_refs, "EXT_DIR", link)
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

    # ---- URL の契約(2026-10-05): .html の補完と CSP ----

    def _mount_pages(self, monkeypatch, tmp_path):
        """cage がビルドした doc のページの形: `<住所>.html` / `<住所>/index.html`。
        `ext/cage/doc` は配備が置いた mount(ディレクトリ symlink)。"""
        root = self._mount(monkeypatch, tmp_path)
        doc = tmp_path / "llens" / "cage" / "build" / "doc"
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
        (root / "cage" / "doc").symlink_to(doc)
        return doc

    def test_completion_order(self, monkeypatch, tmp_path):
        self._mount_pages(monkeypatch, tmp_path)
        cases = {
            "/cage/doc/院内マニュアル/電話番号簿": "tel",  # X.html
            "/cage/doc/院内マニュアル/医療安全管理マニュアル": "anzen-index",  # X/index.html
            "/cage/doc/院内マニュアル/医療安全管理マニュアル/": "anzen-index",
            "/cage/doc/院内マニュアル": "dir-index",
            "/cage/doc/both": "both-file",  # X.html が X/index.html より先
            "/cage/doc/both/": "both-file",
            "/cage/doc/plain": "plain-file",  # 実在のファイルはそのまま
            "/cage/doc/院内マニュアル/電話番号簿.html": "tel",
        }
        for path, body in cases.items():
            response = self._get(path)
            assert response.status_code == 200, path
            assert response.text == body, path
        # percent-encoded の住所でも同じ
        assert (
            self._get(
                "/cage/doc/%E9%99%A2%E5%86%85%E3%83%9E%E3%83%8B%E3%83%A5%E3%82%A2%E3%83%AB"
            ).text
            == "dir-index"
        )

    def test_completion_missing_dir_and_outside_404(self, monkeypatch, tmp_path):
        self._mount_pages(monkeypatch, tmp_path)
        for path in (
            "/cage/doc/nothere",
            "/cage/doc/nothing",  # index.html の無いディレクトリ: 一覧は出さない
            "/cage/doc/nothing/",
            "/cage/doc/esc",  # mount の外の index.html
            "/cage/doc/esc/",
            "/cage/doc/esc/s",
            "/cage/doc/leak",  # mount の外を指す X.html
            "/cage/doc/memo",  # .md は補完しない
        ):
            assert self._get(path).status_code == 404, path

    def test_html_gets_strict_csp(self, monkeypatch, tmp_path):
        from nucllei.routers.ext import HTML_CSP

        self._mount_pages(monkeypatch, tmp_path)
        assert HTML_CSP == (
            "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        )
        for path in (
            "/cage/static/page.html",
            "/cage/doc/院内マニュアル",
            "/cage/doc/院内マニュアル/電話番号簿",
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
