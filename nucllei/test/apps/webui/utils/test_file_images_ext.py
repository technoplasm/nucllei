"""参照(URL / sandbox fs パス)のオンデマンド解決の回帰テスト(#97 / #139 / 2026-10-03)。

feed_image が "https://…/ext/..." "/ext/..." "/workspace/..." の参照を sink に積むと
context_images に文字列のまま永続し、送信直前の inline_internal_image_urls が
utils/image_refs.py で実体化して data URL にする(File は発行しない — Files に
入るのはユーザーアップロードと sandbox 成果物のみ)。

保存済みの参照文字列(image_url の url)の振り分け(image_refs の module docstring の表。
先頭で機械的に分かれる):

- スキーム付き URL: path が "/ext/{rest}" なら host を問わず `EXT_MOUNTS` の静的 mount の `{rest}`
  (= 静的 mount の `{rest}`)をローカル読み(`EXT_MOUNTS` の静的 mount。proxy の name は上流へ GET — 2026-10-05)。それ以外は一般の URL
  としてそのホストに直接(断るのはこのサーバ自身だけ)
- "/ext/{rest}" = 同一サーバの相対 URL。絶対形と同じ `EXT_MOUNTS` の静的 mount で解決する
  (2026-10-03 — #139 は「相対 /ext/ は受けない」だったのを覆した)。sandbox の RO マウント
  `/ext/{rest}` と同じ文字列なので、保存済み参照としては URL として読めば同じ物に当たる
- "/workspace/..." = sandbox の fs(呼び出しユーザーの workspace)
- per-user のアクセス検査はしない(公開範囲は配備が `EXT_MOUNTS` の静的 mount に置いた物、workspace は
  呼び出しユーザーのもの、が境界)
- 画像でも PDF でもない・FETCH_MAX_BYTES 超過・上流不達・redirect・root 外・
  不在はすべて失敗 → 既存の "(image unavailable)" 置換に合流(fail closed)
- PDF は "#page=N"(既定 1)のページを PNG 化する(URL でも同じ)
- 従来の file_id 経路("/api/v1/files/{id}/content")は無変更
"""

import asyncio
import base64
import io
import ipaddress
import os

import httpx
import pypdfium2 as pdfium

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.ext_mounts import mount_tree

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")

UNAVAILABLE = {
    "type": "text",
    "text": "(ここにあった画像は読み込めなかった — 中身は見えていない)",
}

# 自オリジンの表示 URL(cage の PUBLIC_BASE がこの形で文書に焼き込まれる)
SELF = "https://llens.test"

# 一般の URL のホスト(DNS は差し替える。実ネットワークに出ない)
GENERAL = "http://intra.test"


def _pdf_bytes(pages: int) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(100, 100)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


class TestFileImagesExtInline(AbstractPostgresTest):
    """`/ext/{rest}`(絶対・相対)と一般の URL の送信直前 inline。"""

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

    def _mount(self, monkeypatch, tmp_path):
        """tmp の `EXT_MOUNTS` の静的 mount(= /ext の配信元)。"""

        root = tmp_path / "data" / "ext"
        (root / "cage" / "figs").mkdir(parents=True)
        (root / "cage" / "figs" / "a.png").write_bytes(PNG_BYTES)
        (root / "cage" / "figs" / "図").mkdir()
        (root / "cage" / "figs" / "図" / "図.png").write_bytes(PNG_BYTES)
        (root / "cage" / "static").mkdir()
        (root / "cage" / "static" / "doc.pdf").write_bytes(_pdf_bytes(2))
        (root / "cage" / "index.html").write_text("<html>")
        outside = tmp_path / "outside.png"
        outside.write_bytes(PNG_BYTES)
        (root / "cage" / "figs" / "escape.png").symlink_to(outside)
        mount_tree(monkeypatch, root)
        return root

    def _self(self, monkeypatch):
        """自分自身の発見と DNS を差し替える(一般の URL 用)。"""
        from nucllei.utils import ext_upstreams

        found = frozenset({ipaddress.ip_address("10.106.0.5")})
        monkeypatch.setattr(ext_upstreams, "discover_self_addresses", lambda: found)
        ext_upstreams.reset_self_addresses()

        async def fake_lookup(host):
            return {"intra.test": ["172.16.5.5"]}.get(host, [])

        monkeypatch.setattr(ext_upstreams, "lookup_host", fake_lookup)

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

    def test_ext_url_converted_to_data_url(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        seen = []
        self._install_transport(monkeypatch, self._png_handler(seen))
        messages = self._convert(self._messages(f"{SELF}/ext/cage/figs/a.png"))
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url == f"data:image/png;base64,{PNG_B64}"
        assert seen == []  # ローカル読み。どこにも接続しない

    def test_ext_url_query_and_fragment_ignored(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        for url in (
            f"{SELF}/ext/cage/figs/a.png?rev=2",
            "/ext/cage/figs/a.png?rev=2#sec",
        ):
            messages = self._convert(self._messages(url))
            assert messages[0]["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            ), url

    def test_mixed_file_and_ext_entries(self, monkeypatch, tmp_path):
        # file_id 経路(認可検査あり)と "/ext/..." 経路(静的 mount)の混在
        self._mount(monkeypatch, tmp_path)
        messages = self._messages("/api/v1/files/f-img/content")
        messages[0]["content"].append(
            {"type": "image_url", "image_url": {"url": f"{SELF}/ext/cage/figs/a.png"}}
        )
        messages = self._convert(messages)
        assert messages[0]["content"][1]["image_url"]["url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )
        assert messages[0]["content"][2]["image_url"]["url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )

    def test_ext_pdf_rasterized(self, monkeypatch, tmp_path):
        # PDF はページ画像化(File を作らない — #139)。相対でも絶対でも
        self._mount(monkeypatch, tmp_path)
        for url in (
            f"{SELF}/ext/cage/static/doc.pdf#page=2",
            "/ext/cage/static/doc.pdf#page=2",
            "/ext/cage/static/doc.pdf",
        ):
            messages = self._convert(self._messages(url))
            assert messages[0]["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            ), url

    def test_ext_url_any_host_resolved(self, monkeypatch, tmp_path):
        # path が /ext/{rest} なら host を問わず 静的 mount で解決する(与えられた host には接続しない)。
        # 大文字 scheme・port 付きでも同じ
        self._mount(monkeypatch, tmp_path)
        seen = []
        self._install_transport(monkeypatch, self._png_handler(seen))
        for url in (
            "http://elsewhere.example/ext/cage/figs/a.png",
            "HTTPS://host/ext/cage/figs/a.png",
            "https://host:8443/ext/cage/figs/a.png",
        ):
            messages = self._convert(self._messages(url))
            assert messages[0]["content"][1]["image_url"]["url"].startswith(
                "data:"
            ), url
        assert seen == []

    def test_relative_ext_in_assistant_and_tool_image_messages(
        self, monkeypatch, tmp_path
    ):
        # user / assistant どちらの image_url でも同じ(role を見ない)。context_images に
        # 相対のまま永続し、毎ターンの送信でここが解決する(#142)
        self._mount(monkeypatch, tmp_path)
        messages = [
            {
                "role": role,
                "content": [
                    {"type": "text", "text": "図"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "/ext/cage/figs/a.png"},
                    },
                ],
            }
            for role in ("user", "assistant")
        ]
        for message in self._convert(messages):
            assert message["content"][1]["image_url"]["url"].startswith("data:")

    def test_percent_encoded_path(self, monkeypatch, tmp_path):
        # 文書に焼かれた URL は日本語ディレクトリを percent-encoded で持つ。ブラウザ側
        # (routers/ext.py、Starlette が decode)と同じファイルに当たること
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(
            self._messages("https://any.host/ext/cage/figs/%E5%9B%B3/%E5%9B%B3.png")
        )
        assert messages[0]["content"][1]["image_url"]["url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )

    # ---- 失敗はすべて "(image unavailable)" に合流 ----

    def test_nothing_mounted_unavailable(self, monkeypatch, tmp_path):

        mount_tree(monkeypatch, tmp_path / "nowhere")
        messages = self._convert(self._messages(f"{SELF}/ext/cage/figs/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_non_image_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages(f"{SELF}/ext/cage/index.html"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_size_limit_exceeded_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "nucllei.utils.image_refs.FETCH_MAX_BYTES", len(PNG_BYTES) - 1
        )
        messages = self._convert(self._messages(f"{SELF}/ext/cage/figs/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_closed_forms_unavailable(self, monkeypatch, tmp_path):
        # 不在・ディレクトリ・静的 mount の外へ出る symlink・traversal(encode 済み / NUL)・
        # 制御文字・name の取れない形・"/ext/" でない "/" 始まり・protocol-relative
        self._mount(monkeypatch, tmp_path)
        seen = []
        self._install_transport(monkeypatch, self._png_handler(seen))
        for url in (
            f"{SELF}/ext/cage/figs/nope.png",
            f"{SELF}/ext/cage/figs/escape.png",
            f"{SELF}/ext/cage/figs",
            f"{SELF}/ext/cage/",
            f"{SELF}/ext/",
            f"{SELF}/ext/cage/../secret.png",
            "https://host/ext/cage/%2e%2e/x.png",
            "https://host/ext/cage/a\x00.png",
            "https://host/ext/cage/a%00.png",
            f"{SELF}/ext/cage/a\r\nHost: evil/y.png",
            "/ext/nothere/a.png",
            "/ext/cage/../a.png",
            "/ext/",
            "/ext/nothere",
            "/extra/a.png",
            "//elsewhere.example/ext/cage/a.png",
        ):
            messages = self._convert(self._messages(url))
            assert messages[0]["content"][1] == UNAVAILABLE, url
        assert seen == []

    # ---- 一般の URL(/ext/ 形でない絶対 URL)----

    def test_general_url_fetched_and_inlined(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        self._self(monkeypatch)
        seen = []
        self._install_transport(monkeypatch, self._png_handler(seen))
        messages = self._convert(self._messages(f"{GENERAL}/figs/a.png?rev=2"))
        assert messages[0]["content"][1]["image_url"]["url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )
        # 接続先は解決したアドレスに固定、Host は元のまま。cookie は送らない
        assert str(seen[0].url) == "http://172.16.5.5/figs/a.png?rev=2"
        assert seen[0].headers["host"] == "intra.test"
        assert "cookie" not in seen[0].headers

    def test_general_pdf_page_via_url(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        self._self(monkeypatch)
        self._install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, content=_pdf_bytes(2), headers={"content-type": "application/pdf"}
            ),
        )
        messages = self._convert(self._messages(f"{GENERAL}/x.pdf#page=2"))
        assert messages[0]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )

    def test_general_failures_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        self._self(monkeypatch)
        cases = (
            ("a.png", httpx.Response(404)),
            ("a.png", httpx.Response(302)),  # Location 無し = 辿れない redirect
            (
                "index.html",
                httpx.Response(
                    200, content=b"<html>", headers={"content-type": "text/html"}
                ),
            ),
        )
        for name, response in cases:
            self._install_transport(monkeypatch, lambda r, response=response: response)
            messages = self._convert(self._messages(f"{GENERAL}/{name}"))
            assert messages[0]["content"][1] == UNAVAILABLE, response

        def unreachable(request):
            raise httpx.ConnectError("connection refused", request=request)

        self._install_transport(monkeypatch, unreachable)
        assert self._convert(self._messages(f"{GENERAL}/a.png"))[0]["content"][1] == (
            UNAVAILABLE
        )
        # 自分自身は繋がない
        self._install_transport(monkeypatch, self._png_handler())
        messages = self._convert(self._messages("http://127.0.0.1:3000/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_size_limit_aborts_mid_stream(self, monkeypatch, tmp_path):
        # 全部読んでから測るのでなく、上限超過時点でストリームを打ち切ること
        # (巨大上流で全量バッファしない)を、チャンク消費数で固定する
        self._mount(monkeypatch, tmp_path)
        self._self(monkeypatch)
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
        monkeypatch.setattr("nucllei.utils.image_refs.FETCH_MAX_BYTES", 20)
        messages = self._convert(self._messages(f"{GENERAL}/big.png"))
        assert messages[0]["content"][1] == UNAVAILABLE
        assert pulled["chunks"] < 10  # 全チャンクは読まない(mid-stream 中断)


class TestFileImagesFsRefs(AbstractPostgresTest):
    """fs 参照("/ext/..." / "/workspace/...")の解決(#139)。`/ext/...` は sandbox の RO
    マウントと URL が同じ文字列で、nucllei は `EXT_MOUNTS` の静的 mount を読む(2026-10-03 までは `/mnt`)。

    `EXT_MOUNTS` は `nucllei.env.EXT_MOUNTS`(呼び出し時に読む)なので tmp_path に
    差し替える。sandbox は `sandbox_post` を差し替えて切り離す。
    """

    BASE_PATH = "/api/v1/files"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.users import Users

        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )

    def _mount(self, monkeypatch, tmp_path):

        root = tmp_path / "data" / "ext"
        (root / "cage" / "static").mkdir(parents=True)
        (root / "cage" / "static" / "a.png").write_bytes(PNG_BYTES)
        (root / "cage" / "static" / "doc.pdf").write_bytes(_pdf_bytes(2))
        (root / "cage" / "static" / "note.txt").write_text("text")
        outside = tmp_path / "outside.png"
        outside.write_bytes(PNG_BYTES)
        (root / "cage" / "static" / "escape.png").symlink_to(outside)
        mount_tree(monkeypatch, root)
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

    # ---- /ext(RO マウント = 静的 mount、nucllei のローカル読み)----

    def test_mount_image_to_data_url(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/ext/cage/static/a.png"))
        assert self._url(messages) == f"data:image/png;base64,{PNG_B64}"

    def test_mount_pdf_page_rasterized(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/ext/cage/static/doc.pdf#page=2"))
        url = self._url(messages)
        assert url.startswith("data:image/png;base64,")
        png = base64.b64decode(url.split(",", 1)[1])
        assert png.startswith(b"\x89PNG")

    def test_mount_pdf_default_first_page(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/ext/cage/static/doc.pdf"))
        assert self._url(messages).startswith("data:image/png;base64,")

    def test_mount_pdf_page_out_of_range_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/ext/cage/static/doc.pdf#page=3"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_non_image_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/ext/cage/static/note.txt"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_missing_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/ext/cage/static/nope.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_directory_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        messages = self._convert(self._messages("/ext/cage/static"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_mount_outside_root_unavailable(self, monkeypatch, tmp_path):
        root = self._mount(monkeypatch, tmp_path)
        # root の外(traversal / symlink 越え / nucllei 側の実体のパス / 別の絶対パス /
        # 旧 `/mnt`)はすべて読まない
        for ref in (
            "/ext/cage/../../outside.png",
            "/ext/cage/static/escape.png",
            f"{root}/cage/static/a.png",
            f"{tmp_path}/outside.png",
            "/mnt/cage/static/a.png",
        ):
            messages = self._convert(self._messages(ref))
            assert messages[0]["content"][1] == UNAVAILABLE, ref

    def test_mount_size_limit_unavailable(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "nucllei.utils.image_refs.FETCH_MAX_BYTES", len(PNG_BYTES) - 1
        )
        messages = self._convert(self._messages("/ext/cage/static/a.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    # ---- 同じ実体を相対 / 絶対 URL で(静的 mount をローカル読み)----

    def test_same_file_via_ext_url_and_mount_path(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        for ref in (
            "/ext/cage/static/a.png",
            "https://any.host/ext/cage/static/a.png",
        ):
            messages = self._convert(self._messages(ref))
            assert self._url(messages) == f"data:image/png;base64,{PNG_B64}", ref
        for closed in ("/ext/cage/static/escape.png", "/ext/cage/static/nope.png"):
            messages = self._convert(self._messages(closed))
            assert messages[0]["content"][1] == UNAVAILABLE, closed

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
        # 呼び出しユーザーの workspace、prefix を落とした相対 path。上限 (resolver の既定
        # FETCH_MAX_BYTES) も sandbox に渡す — 大きなファイルを転送してから断らない
        from nucllei.env import FETCH_MAX_BYTES

        assert calls == [
            (
                "/files/read",
                {
                    "user_id": "alice",
                    "path": "out/plot.png",
                    "file_limit_bytes": FETCH_MAX_BYTES,
                },
            )
        ]

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

    def test_data_url_untouched(self, monkeypatch, tmp_path):
        self._mount(monkeypatch, tmp_path)
        url = "data:image/png;base64,AAAA"
        messages = self._convert(self._messages(url))
        assert self._url(messages) == url

    def test_unreachable_absolute_url_unavailable(self, monkeypatch, tmp_path):
        # /ext/ 形でない絶対 URL は一般の URL — 届かなければ unavailable(URL のまま残さない)
        from nucllei.utils import ext_upstreams

        self._mount(monkeypatch, tmp_path)

        async def no_dns(host):
            return []

        monkeypatch.setattr(ext_upstreams, "lookup_host", no_dns)
        messages = self._convert(self._messages("https://his.example/x.png"))
        assert messages[0]["content"][1] == UNAVAILABLE

    def test_other_url_forms_unavailable(self, monkeypatch, tmp_path):
        # プロトコル相対・http(s) 以外のスキーム・相対パスも unavailable(URL のまま残さない)
        self._mount(monkeypatch, tmp_path)
        for url in (
            "//his.example/x.png",
            "ftp://h/x.png",
            "file:///etc/x.png",
            "x.png",
        ):
            messages = self._convert(self._messages(url))
            assert messages[0]["content"][1] == UNAVAILABLE, url

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


# ---- split_page / with_page(URL でも fs パスでも同じ、番兵 e)----


def test_split_page_on_url_and_path():
    from nucllei.utils.image_refs import split_page, with_page

    assert split_page("https://h/ext/cage/x.pdf#page=3") == (
        "https://h/ext/cage/x.pdf",
        3,
    )
    assert split_page("https://h/x.pdf?rev=2#page=12") == ("https://h/x.pdf?rev=2", 12)
    assert split_page("/ext/cage/x.pdf#page=1") == ("/ext/cage/x.pdf", 1)
    # 末尾の "#page=N" だけを見る — URL 自身の fragment には触らない
    assert split_page("https://h/x.pdf#sec2") == ("https://h/x.pdf#sec2", None)
    assert split_page("https://h/x.pdf#sec2#page=4") == ("https://h/x.pdf#sec2", 4)
    assert with_page("https://h/x.pdf#page=2", 5) == "https://h/x.pdf#page=5"
    assert with_page("https://h/x.pdf", 5) == "https://h/x.pdf#page=5"
