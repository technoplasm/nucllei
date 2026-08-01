""" "/ext/{name}/..." 画像のオンデマンド解決の回帰テスト(#97)。

feed_image が "/ext/..." パスを sink に積むと context_images に URL のまま永続し、
送信直前の inline_internal_image_urls がサーバ側 httpx fetch で data URL 化する
(File は発行しない — Files に入るのはユーザーアップロードと sandbox 成果物のみ)。

- 上流 allowlist は env EXT_UPSTREAMS 一本(未知 name は解決しない)
- per-user のアクセス検査はしない(上流の公開範囲は admin 管理の EXT_UPSTREAMS が真実源)
- content-type が image/* でない・EXT_INLINE_MAX_BYTES 超過・上流不達・redirect は
  すべて失敗 → 既存の "(image unavailable)" 置換に合流
- 相対 "/ext/..." のみ対象(絶対 URL は外部 URL として無変更)
- 従来の file_id 経路("/api/v1/files/{id}/content")は無変更
"""

import asyncio
import base64
import os

import httpx

from test.util.abstract_integration_test import AbstractPostgresTest

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")

UNAVAILABLE = {"type": "text", "text": "(image unavailable)"}

UPSTREAM = "http://upstream.test"


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
            "nucllei.utils.file_images.EXT_INLINE_MAX_BYTES", len(PNG_BYTES) - 1
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
        monkeypatch.setattr("nucllei.utils.file_images.EXT_INLINE_MAX_BYTES", 20)
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
