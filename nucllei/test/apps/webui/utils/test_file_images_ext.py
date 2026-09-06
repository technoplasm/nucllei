""" "/" 始まり参照のオンデマンド解決の回帰テスト(#97 / #139)。

feed_image が "/ext/..." "/mnt/..." "/workspace/..." の参照を sink に積むと
context_images に文字列のまま永続し、送信直前の inline_internal_image_urls が
utils/image_refs.py で実体化して data URL にする(File は発行しない — Files に
入るのはユーザーアップロードと sandbox 成果物のみ)。

- "/ext/{name}/..." の backend は env EXT_UPSTREAMS 一本(未知 name は解決しない)。
  http backend はサーバ側 httpx fetch。ここは cage を http に向けて従来経路を固定する
- per-user のアクセス検査はしない(公開範囲は admin 管理の EXT_UPSTREAMS / RO
  マウント、workspace は呼び出しユーザーのもの、が境界)
- 画像でも PDF でもない・EXT_INLINE_MAX_BYTES 超過・上流不達・redirect・root 外・
  不在はすべて失敗 → 既存の "(image unavailable)" 置換に合流(fail closed)
- PDF は "#page=N"(既定 1)のページを PNG 化する
- 相対 "/..." のみ対象(絶対 URL は外部 URL として無変更)
- 従来の file_id 経路("/api/v1/files/{id}/content")は無変更
"""

import asyncio
import base64
import io
import os

import httpx
import pypdfium2 as pdfium

from test.util.abstract_integration_test import AbstractPostgresTest

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")

UNAVAILABLE = {"type": "text", "text": "(image unavailable)"}

UPSTREAM = "http://upstream.test"


def _pdf_bytes(pages: int) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(100, 100)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


class TestFileImagesExtInline(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_method(self):
        super().setup_method()
        from nucllei.config import UPLOAD_DIR
        from nucllei.models.users import Users
        from nucllei.models.files import Files, FileForm

        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )

        # 従来 file_id 経路の回帰用: alice 所有の画像 file(storage 実体付き)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        self.img_path = f"{UPLOAD_DIR}/f-img_x.png"
        with open(self.img_path, "wb") as f:
            f.write(PNG_BYTES)
        Files.insert_new_file(
            "alice",
            FileForm(
                id="f-img",
                filename="x.png",
                path=self.img_path,
                meta={"name": "x.png", "content_type": "image/png", "size": 24},
                data={},
            ),
        )

    def _enable_cage(self, monkeypatch):
        from nucllei.env import EXT_UPSTREAMS

        monkeypatch.setitem(EXT_UPSTREAMS, "cage", UPSTREAM)

    def _install_transport(self, monkeypatch, handler):
        from nucllei.utils import ext_upstreams

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False
        )
        monkeypatch.setattr(ext_upstreams, "_client", client)

    def _png_handler(self, seen=None):
        def handler(request):
            if seen is not None:
                seen.append(request)
            return httpx.Response(
                200, content=PNG_BYTES, headers={"content-type": "image/png"}
            )

        return handler

    def _messages(self, url):
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "これは何の画像?"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ]

    def _convert(self, messages, user=None):
        from nucllei.utils.file_images import inline_internal_image_urls

        return asyncio.run(inline_internal_image_urls(messages, user or self.alice))

    # ---- 変換される ----

    def test_ext_url_converted_to_data_url(self, monkeypatch):
        self._enable_cage(monkeypatch)
        seen = []
        self._install_transport(monkeypatch, self._png_handler(seen))
        messages = self._convert(self._messages("/ext/cage/figs/a.png"))
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url == f"data:image/png;base64,{PNG_B64}"
        assert str(seen[0].url) == f"{UPSTREAM}/figs/a.png"

    def test_ext_url_query_passed_through(self, monkeypatch):
        self._enable_cage(monkeypatch)
        seen = []
        self._install_transport(monkeypatch, self._png_handler(seen))
        messages = self._convert(self._messages("/ext/cage/figs/a.png?rev=2"))
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert str(seen[0].url) == f"{UPSTREAM}/figs/a.png?rev=2"

    def test_same_ext_url_fetched_once(self, monkeypatch):
        self._enable_cage(monkeypatch)
        seen = []
        self._install_transport(monkeypatch, self._png_handler(seen))
        messages = self._messages("/ext/cage/figs/a.png")
        messages[0]["content"].append(
            {"type": "image_url", "image_url": {"url": "/ext/cage/figs/a.png"}}
        )
        messages = self._convert(messages)
        assert len(seen) == 1
        assert messages[0]["content"][1]["image_url"]["url"].startswith("data:")
        assert messages[0]["content"][2]["image_url"]["url"].startswith("data:")

    def test_mixed_file_and_ext_entries(self, monkeypatch):
        # file_id 経路(認可検査あり)と "/ext/..." 経路(EXT_UPSTREAMS が真実源)の混在
        self._enable_cage(monkeypatch)
        self._install_transport(monkeypatch, self._png_handler())
        messages = self._messages("/api/v1/files/f-img/content")
        messages[0]["content"].append(
            {"type": "image_url", "image_url": {"url": "/ext/cage/figs/a.png"}}
        )
        messages = self._convert(messages)
        assert messages[0]["content"][1]["image_url"]["url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )
        assert messages[0]["content"][2]["image_url"]["url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )

    # ---- 失敗はすべて "(image unavailable)" に合流 ----

    def test_unknown_name_unavailable(self, monkeypatch):
        # EXT_UPSTREAMS 未設定(機能無効)= 未知 name と同じ扱い
        self._install_transport(monkeypatch, self._png_handler())
        messages = self._convert(self._messages("/ext/cage/figs/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_ext_http_pdf_rasterized(self, monkeypatch):
        # http backend でも PDF はページ画像化(File を作らない — #139)
        self._enable_cage(monkeypatch)
        self._install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200,
                content=_pdf_bytes(2),
                headers={"content-type": "application/pdf"},
            ),
        )
        messages = self._convert(self._messages("/ext/cage/static/doc.pdf#page=2"))
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_non_image_content_type_unavailable(self, monkeypatch):
        self._enable_cage(monkeypatch)
        self._install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, content=b"<html>", headers={"content-type": "text/html"}
            ),
        )
        messages = self._convert(self._messages("/ext/cage/index.html"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_size_limit_exceeded_unavailable(self, monkeypatch):
        self._enable_cage(monkeypatch)
        self._install_transport(monkeypatch, self._png_handler())
        monkeypatch.setattr(
            "nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", len(PNG_BYTES) - 1
        )
        messages = self._convert(self._messages("/ext/cage/figs/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_size_limit_aborts_mid_stream(self, monkeypatch):
        # 全部読んでから測るのでなく、上限超過時点でストリームを打ち切ること
        # (巨大上流で全量バッファしない)を、チャンク消費数で固定する
        self._enable_cage(monkeypatch)
        pulled = {"chunks": 0}

        async def body():
            for _ in range(10):
                pulled["chunks"] += 1
                yield b"x" * 8

        self._install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, content=body(), headers={"content-type": "image/png"}
            ),
        )
        # 8B/chunk、上限 20B → 3 チャンク目(24B)で超過検知して中断
        monkeypatch.setattr("nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", 20)
        messages = self._convert(self._messages("/ext/cage/big.png"))
        assert messages[0]["content"][1] == UNAVAILABLE
        assert pulled["chunks"] < 10  # 全チャンクは読まない(mid-stream 中断)

    def test_control_char_path_unavailable(self, monkeypatch):
        # メッセージ JSON 由来の URL は Starlette デコードを経ないため、制御文字が
        # 生で入りうる。共有ゲートが弾き "(image unavailable)" に合流する(500 化しない)
        self._enable_cage(monkeypatch)
        self._install_transport(monkeypatch, self._png_handler())
        messages = self._convert(self._messages("/ext/cage/a\r\nHost: evil/y.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_upstream_unreachable_unavailable(self, monkeypatch):
        self._enable_cage(monkeypatch)

        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        self._install_transport(monkeypatch, handler)
        messages = self._convert(self._messages("/ext/cage/figs/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_upstream_redirect_unavailable(self, monkeypatch):
        self._enable_cage(monkeypatch)
        self._install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                302, headers={"location": f"{UPSTREAM}/moved.png"}
            ),
        )
        messages = self._convert(self._messages("/ext/cage/figs/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_traversal_path_unavailable(self, monkeypatch):
        # traversal 拒否はプロキシ router と同基準(共通ヘルパ)
        self._enable_cage(monkeypatch)
        self._install_transport(monkeypatch, self._png_handler())
        messages = self._convert(self._messages("/ext/cage/../secret.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    # ---- 対象外は無変更 ----

    def test_absolute_ext_like_url_untouched(self, monkeypatch):
        # 対象は相対 "/ext/..." のみ。絶対 URL は外部 URL として触らない
        self._enable_cage(monkeypatch)
        self._install_transport(monkeypatch, self._png_handler())
        url = "http://elsewhere.example/ext/cage/figs/a.png"
        messages = self._convert(self._messages(url))
        assert messages[0]["content"][1]["image_url"]["url"] == url


class TestFileImagesFsRefs(AbstractPostgresTest):
    """fs 参照("/mnt/..." / "/workspace/..." / file backend の "/ext/...")の解決(#139)。

    RO マウントの root は module 属性 `image_refs.RO_MOUNT_ROOT`(呼び出し時に読む)
    なので tmp_path に差し替える。sandbox は `sandbox_post` を差し替えて切り離す。
    """

    BASE_PATH = "/api/v1/files"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.users import Users

        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )

    def _mount(self, monkeypatch, tmp_path):
        from nucllei.utils import image_refs

        root = tmp_path / "mnt"
        (root / "cage" / "static").mkdir(parents=True)
        (root / "cage" / "static" / "a.png").write_bytes(PNG_BYTES)
        (root / "cage" / "static" / "doc.pdf").write_bytes(_pdf_bytes(2))
        (root / "cage" / "static" / "note.txt").write_text("text")
        outside = tmp_path / "outside.png"
        outside.write_bytes(PNG_BYTES)
        (root / "cage" / "static" / "escape.png").symlink_to(outside)
        monkeypatch.setattr(image_refs, "RO_MOUNT_ROOT", root)
        return root

    def _messages(self, url):
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "これは何?"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ]

    def _convert(self, messages):
        from nucllei.utils.file_images import inline_internal_image_urls

        return asyncio.run(inline_internal_image_urls(messages, self.alice))

    def _url(self, messages):
        return messages[0]["content"][1]["image_url"]["url"]

    # ---- /mnt(RO マウント、nucllei のローカル読み)----

    def test_mount_image_to_data_url(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static/a.png"))
        assert self._url(messages) == f"data:image/png;base64,{PNG_B64}"

    def test_mount_pdf_page_rasterized(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static/doc.pdf#page=2"))
        url = self._url(messages)
        assert url.startswith("data:image/png;base64,")
        png = base64.b64decode(url.split(",", 1)[1])
        assert png.startswith(b"\x89PNG")

    def test_mount_pdf_default_first_page(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static/doc.pdf"))
        assert self._url(messages).startswith("data:image/png;base64,")

    def test_mount_pdf_page_out_of_range_unavailable(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static/doc.pdf#page=3"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_non_image_unavailable(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static/note.txt"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_missing_unavailable(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static/nope.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_directory_unavailable(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_outside_root_unavailable(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        # root の外(traversal / symlink 越え / 別の絶対パス)はすべて読まない
        for ref in (
            f"{root}/cage/../../outside.png",
            f"{root}/cage/static/escape.png",
            f"{tmp_path}/outside.png",
        ):
            messages = self._convert(self._messages(ref))
            assert messages[0]["content"][1] == UNAVAILABLE, ref

    def test_mount_size_limit_unavailable(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "nucllei.utils.image_refs.EXT_INLINE_MAX_BYTES", len(PNG_BYTES) - 1
        )
        messages = self._convert(self._messages(f"{root}/cage/static/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    # ---- /ext の file backend(root 内をローカル読み、tail 一致)----

    def test_ext_file_backend_image(self, monkeypatch, tmp_path):
        from nucllei.env import EXT_UPSTREAMS

        root = self._mount(monkeypatch, tmp_path)
        monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
        messages = self._convert(self._messages("/ext/cage/static/a.png"))
        assert self._url(messages) == f"data:image/png;base64,{PNG_B64}"

    def test_ext_file_backend_symlink_escape_unavailable(self, monkeypatch, tmp_path):
        from nucllei.env import EXT_UPSTREAMS

        root = self._mount(monkeypatch, tmp_path)
        monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{root}/cage")
        messages = self._convert(self._messages("/ext/cage/static/escape.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    # ---- /workspace(sandbox /files/read)----

    def _install_sandbox(self, monkeypatch, files: dict, url="http://sandbox.test"):
        from nucllei.builtin_tools import sandbox

        calls = []

        async def fake_post(path, payload, timeout_s):
            calls.append((path, payload))
            data = files.get(payload["path"])
            if data is None:
                raise RuntimeError("sandbox HTTP 404: not a file")
            return {
                "name": os.path.basename(payload["path"]),
                "size": len(data),
                "b64": base64.b64encode(data).decode(),
            }

        monkeypatch.setattr(sandbox, "SANDBOX_URL", url)
        monkeypatch.setattr(sandbox, "sandbox_post", fake_post)
        return calls

    def test_workspace_image_via_sandbox(self, monkeypatch):
        calls = self._install_sandbox(monkeypatch, {"out/plot.png": PNG_BYTES})
        messages = self._convert(self._messages("/workspace/out/plot.png"))
        assert self._url(messages) == f"data:image/png;base64,{PNG_B64}"
        # 呼び出しユーザーの workspace、prefix を落とした相対 path
        assert calls == [("/files/read", {"user_id": "alice", "path": "out/plot.png"})]

    def test_workspace_pdf_page(self, monkeypatch):
        self._install_sandbox(monkeypatch, {"out/r.pdf": _pdf_bytes(1)})
        messages = self._convert(self._messages("/workspace/out/r.pdf#page=1"))
        assert self._url(messages).startswith("data:image/png;base64,")

    def test_workspace_missing_unavailable(self, monkeypatch):
        self._install_sandbox(monkeypatch, {})
        messages = self._convert(self._messages("/workspace/out/nope.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_workspace_without_sandbox_url_unavailable(self, monkeypatch):
        calls = self._install_sandbox(monkeypatch, {"a.png": PNG_BYTES}, url="")
        messages = self._convert(self._messages("/workspace/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE
        assert calls == []

    # ---- 対象外 ----

    def test_unknown_root_unavailable(self, monkeypatch, tmp_path):
        # "/" 始まりだが、どの名前空間でもない → 解決しない(fail closed)
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/etc/hostname"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_absolute_and_data_urls_untouched(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        for url in ("https://his.example/x.png", "data:image/png;base64,AAAA"):
            messages = self._convert(self._messages(url))
            assert self._url(messages) == url

    # ---- file_id の PDF("/api/v1/files/{id}/content#page=N"、File を作らない #139)----

    def _insert_pdf_file(self, owner="alice", file_id="f-pdf"):
        from nucllei.config import UPLOAD_DIR
        from nucllei.models.files import Files, FileForm

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        path = f"{UPLOAD_DIR}/{file_id}_doc.pdf"
        with open(path, "wb") as f:
            f.write(_pdf_bytes(2))
        Files.insert_new_file(
            owner,
            FileForm(
                id=file_id,
                filename="doc.pdf",
                path=path,
                meta={"name": "doc.pdf", "content_type": "application/pdf", "size": 1},
                data={},
            ),
        )

    def test_file_pdf_page_rasterized(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        self._insert_pdf_file()
        messages = self._convert(self._messages("/api/v1/files/f-pdf/content#page=2"))
        assert self._url(messages).startswith("data:image/png;base64,")

    def test_file_pdf_page_out_of_range_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        self._insert_pdf_file()
        messages = self._convert(self._messages("/api/v1/files/f-pdf/content#page=3"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_file_pdf_page_other_owner_unavailable(self, monkeypatch, tmp_path):
        from nucllei.models.users import Users

        Users.insert_new_user(id="bob", name="bob", email="bob@h.example", role="user")
        self._mount(monkeypatch, tmp_path)
        self._insert_pdf_file(owner="bob")
        messages = self._convert(self._messages("/api/v1/files/f-pdf/content#page=1"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_control_char_unavailable(self, monkeypatch, tmp_path):
        # NUL は Path.resolve() が ValueError を投げる — 500 ではなく unavailable に閉じる
        root = self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{root}/cage/static/a\x00.png"))
        assert messages[0]["content"][1] == UNAVAILABLE
