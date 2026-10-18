"""送信直前の inline が VLM の受けない画像形式を image_url に載せない回帰テスト(2026-10-18)。

上流(openrouter の mimo 等)は bmp / gif / png / jpeg / webp しか受けず、それ以外の image_url は
400 でターンごと止まる。チャットの添付・feed_image の context_images・DB から送り直される履歴の
画像はどれも `utils/file_images._materialize` を通るので、保証はそこ 1 箇所に置く:

- png / jpeg / webp / gif / bmp(実バイトの magic で判定)はそのまま data URL
- SVG(resvg、子プロセス)/ TIFF / AVIF(Pillow)は PNG にして data URL
- SVG の外部参照(file:// / 絶対・相対パス / http)は読まない。画素は長辺 2048px まで、
  描画は時間で打ち切る
- 描けなかったもの・それ以外の image/* は "(<名前>: 画像に変換できなかった (形式 …))" の注記
"""

import asyncio
import base64
import io
import os

from PIL import Image

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.ext_mounts import mount_tree

SVG_BYTES = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
    b'<rect width="40" height="20" fill="#0000ff"/></svg>'
)


def _png(color=(0, 255, 0), size=(8, 8), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def _decoded(url: str) -> Image.Image:
    assert url.startswith("data:image/png;base64,"), url[:60]
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def _image_urls(messages) -> list[str]:
    return [
        part["image_url"]["url"]
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]


def _texts(messages) -> list[str]:
    return [
        part["text"]
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "text"
    ]


def _assert_only_vlm_types(messages):
    """image_url に載るのは VLM が受ける 5 形式の data URL だけ(型と実バイトの両方)。"""
    from nucllei.utils.image_refs import VLM_IMAGE_TYPES, sniff_vlm_image_type

    for url in _image_urls(messages):
        assert url.startswith("data:"), url[:80]
        media_type = url[5:].split(";", 1)[0]
        assert media_type in VLM_IMAGE_TYPES, media_type
        raw = base64.b64decode(url.split(",", 1)[1])
        assert sniff_vlm_image_type(raw) == media_type


class TestInlineConvertsUnsupportedImages(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_method(self):
        super().setup_method()
        from nucllei.config import UPLOAD_DIR
        from nucllei.models.users import Users
        from nucllei.models.files import Files, FileForm
        from nucllei.models.chats import ChatForm, Chats

        self.Files = Files
        self.FileForm = FileForm
        self.Chats = Chats
        self.ChatForm = ChatForm
        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        self.upload_dir = UPLOAD_DIR

    def _file(self, file_id, filename, raw, content_type=None):
        path = f"{self.upload_dir}/{file_id}_{filename}"
        with open(path, "wb") as f:
            f.write(raw)
        meta = {"name": filename, "size": len(raw)}
        if content_type:
            meta["content_type"] = content_type
        self.Files.insert_new_file(
            "alice",
            self.FileForm(id=file_id, filename=filename, path=path, meta=meta, data={}),
        )

    def _messages(self, *urls):
        return [
            {
                "role": "user",
                "content": [{"type": "text", "text": "これは何?"}]
                + [{"type": "image_url", "image_url": {"url": u}} for u in urls],
            }
        ]

    def _convert(self, messages):
        from nucllei.utils.file_images import inline_internal_image_urls

        return asyncio.run(inline_internal_image_urls(messages, self.alice))

    # ---- 変換して送る ----

    def test_svg_attachment_is_sent_as_png(self):
        """チャットに添付した SVG(type:'image' → /api/v1/files/{id}/content)は PNG で届く。
        長辺は下限 512px まで拡大して描く(アイコン級の SVG も読める大きさに)。"""
        self._file("f-svg", "fig.svg", SVG_BYTES, "image/svg+xml")
        messages = self._convert(self._messages("/api/v1/files/f-svg/content"))
        _assert_only_vlm_types(messages)
        image = _decoded(_image_urls(messages)[0])
        assert image.size == (512, 256)
        assert image.convert("RGB").getpixel((10, 10)) == (0, 0, 255)

    def test_svg_type_from_extension_when_meta_has_none(self):
        self._file("f-svg", "fig.svg", SVG_BYTES)
        messages = self._convert(self._messages("/api/v1/files/f-svg/content"))
        _assert_only_vlm_types(messages)
        assert len(_image_urls(messages)) == 1

    def test_tiff_is_sent_as_png_and_shrunk_to_the_cap(self):
        self._file(
            "f-tif",
            "scan.tif",
            _png((255, 0, 0), size=(4096, 64), fmt="TIFF"),
            "image/tiff",
        )
        messages = self._convert(self._messages("/api/v1/files/f-tif/content"))
        _assert_only_vlm_types(messages)
        image = _decoded(_image_urls(messages)[0])
        assert image.size == (2048, 32)
        assert image.convert("RGB").getpixel((5, 5)) == (255, 0, 0)

    def test_declared_type_does_not_decide_the_image_url_type(self):
        """型は実バイトで決める: 申告が SVG でも中身が PNG なら PNG のまま送る(申告どおりに
        SVG として描こうとしない)。逆向き(申告 PNG・中身が別物)は
        test_declared_vlm_type_with_other_bytes_is_not_sent_as_image。"""
        png = _png()
        self._file("f-a", "a.svg", png, "image/svg+xml")
        messages = self._convert(self._messages("/api/v1/files/f-a/content"))
        _assert_only_vlm_types(messages)
        assert _image_urls(messages) == [
            f"data:image/png;base64,{base64.b64encode(png).decode()}"
        ]

    def test_svg_data_url_is_converted_but_vlm_data_url_untouched(self):
        svg_url = "data:image/svg+xml;base64," + base64.b64encode(SVG_BYTES).decode()
        png_url = "data:image/png;base64," + base64.b64encode(_png()).decode()
        messages = self._convert(self._messages(svg_url, png_url))
        _assert_only_vlm_types(messages)
        urls = _image_urls(messages)
        assert urls[1] == png_url and urls[0] != svg_url

    # ---- SVG の安全側 ----

    def test_svg_external_references_are_not_read(self, tmp_path):
        """外部参照(絶対パス・file://・相対パス・http・入れ子の SVG data URL)は読まない —
        サーバのファイルを描かせる経路にしない。resvg は既定でパスの画像を読む(実測)ので、
        描く前に参照を落としている。参照先は真っ赤な PNG、描けば赤が出る。"""
        secret = tmp_path / "secret.png"
        secret.write_bytes(_png((255, 0, 0), size=(64, 64)))
        nested = base64.b64encode(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
            f'<image href="{secret}" width="10" height="10"/></svg>'.encode()
        ).decode()
        hrefs = [
            str(secret),
            f"file://{secret}",
            os.path.relpath(secret, os.getcwd()),
            "../../../../../../.." + str(secret),
            "http://127.0.0.1:9/x.png",
            f"data:image/svg+xml;base64,{nested}",
        ]
        for i, href in enumerate(hrefs):
            for attr in ("href", "xlink:href"):
                svg = (
                    '<svg xmlns="http://www.w3.org/2000/svg" '
                    'xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="100">'
                    f'<image {attr}="{href}" width="100" height="100"/>'
                    '<filter id="f"><feImage '
                    f'{attr}="{href}"/></filter>'
                    '<rect x="50" width="50" height="100" filter="url(#f)"/></svg>'
                ).encode()
                file_id = f"f-ext-{i}-{attr.replace(':', '-')}"
                self._file(file_id, "x.svg", svg, "image/svg+xml")
                messages = self._convert(
                    self._messages(f"/api/v1/files/{file_id}/content")
                )
                image = _decoded(_image_urls(messages)[0]).convert("RGB")
                colors = {c for _n, c in image.getcolors(1 << 20)}
                assert (255, 0, 0) not in colors, (href, attr)

        # 内部実体に参照を隠しても、展開してから落とす
        svg = (
            f'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY p "{secret}">]>'
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<image href="&p;" width="100" height="100"/></svg>'
        ).encode()
        self._file("f-ent", "x.svg", svg, "image/svg+xml")
        messages = self._convert(self._messages("/api/v1/files/f-ent/content"))
        image = _decoded(_image_urls(messages)[0]).convert("RGB")
        assert (255, 0, 0) not in {c for _n, c in image.getcolors(1 << 20)}

    def test_svg_inline_raster_data_url_is_still_drawn(self):
        """文書内に埋め込んだラスタ画像(data:image/png)は外部参照ではないので描く。"""
        png = base64.b64encode(_png((255, 0, 0), size=(16, 16))).decode()
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            f'<image href="data:image/png;base64,{png}" width="100" height="100"/></svg>'
        ).encode()
        self._file("f-emb", "x.svg", svg, "image/svg+xml")
        messages = self._convert(self._messages("/api/v1/files/f-emb/content"))
        image = _decoded(_image_urls(messages)[0]).convert("RGB")
        assert image.getpixel((50, 50)) == (255, 0, 0)

    def test_huge_svg_is_drawn_within_the_pixel_cap(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="1000000" height="500000">'
            b'<rect width="100%" height="100%" fill="#0000ff"/></svg>'
        )
        self._file("f-huge", "huge.svg", svg, "image/svg+xml")
        messages = self._convert(self._messages("/api/v1/files/f-huge/content"))
        assert _decoded(_image_urls(messages)[0]).size == (2048, 1024)

    def test_slow_svg_render_is_cut_off_to_a_note(self, monkeypatch):
        from nucllei.utils import image_refs

        monkeypatch.setattr(image_refs, "SVG_RENDER_TIMEOUT", 0.001)
        self._file("f-slow", "slow.svg", SVG_BYTES, "image/svg+xml")
        messages = self._convert(self._messages("/api/v1/files/f-slow/content"))
        assert _image_urls(messages) == []
        assert messages[0]["content"][1] == {
            "type": "text",
            "text": "(slow.svg: 画像に変換できなかった (形式 image/svg+xml))",
        }

    # ---- 描けないものは注記 ----

    def test_broken_svg_and_unknown_image_types_become_notes(self):
        self._file("f-bad", "broken.svg", b"<svg", "image/svg+xml")
        self._file(
            "f-heic", "photo.heic", b"\0\0\0\x18ftypheic" + b"\0" * 8, "image/heic"
        )
        self._file("f-badtif", "bad.tif", b"II*\x00garbage", "image/tiff")
        messages = self._convert(
            self._messages(
                "/api/v1/files/f-bad/content",
                "/api/v1/files/f-heic/content",
                "/api/v1/files/f-badtif/content",
            )
        )
        assert _image_urls(messages) == []
        assert _texts(messages)[1:] == [
            "(broken.svg: 画像に変換できなかった (形式 image/svg+xml))",
            "(photo.heic: 画像に変換できなかった (形式 image/heic))",
            "(bad.tif: 画像に変換できなかった (形式 image/tiff))",
        ]

    def test_declared_vlm_type_with_other_bytes_is_not_sent_as_image(self):
        """申告が PNG でも実バイトが VLM の形式でなければ image_url にしない。"""
        self._file("f-fake", "fake.png", b"not an image at all", "image/png")
        messages = self._convert(self._messages("/api/v1/files/f-fake/content"))
        assert _image_urls(messages) == []
        assert _texts(messages)[1] == (
            "(fake.png: 画像に変換できなかった (形式 image/png))"
        )

    # ---- DB に保存済みの会話(送り直される履歴)----

    def test_stored_chat_with_svg_attachment_and_context_images_does_not_send_svg(
        self, monkeypatch, tmp_path
    ):
        """d564f91e5 より前の feed_image が通した SVG の context_images(file_id / 内部 URL /
        `/ext/…` の参照)と、SVG の添付(files type:'image')が DB に保存された会話。続きの
        ターンで履歴を組み直して送る直前に、どれも PNG になる(image_url に SVG が載らない
        = 上流の 400 にならない)。"""
        from nucllei.utils.execution_resume import build_wire_messages

        self._file("f-svg", "fig.svg", SVG_BYTES, "image/svg+xml")
        root = tmp_path / "ext"
        (root / "cage").mkdir(parents=True)
        (root / "cage" / "fig.svg").write_bytes(SVG_BYTES)
        mount_tree(monkeypatch, root)

        chat = self.Chats.insert_new_chat(
            "alice",
            self.ChatForm(
                chat={
                    "title": "svg",
                    "history": {
                        "currentId": "m3",
                        "messages": {
                            "m1": {
                                "id": "m1",
                                "parentId": None,
                                "role": "user",
                                "content": "この図を見て",
                                "files": [
                                    {
                                        "type": "image",
                                        "id": "f-svg",
                                        "url": "/api/v1/files/f-svg/content",
                                    }
                                ],
                            },
                            "m2": {
                                "id": "m2",
                                "parentId": "m1",
                                "role": "assistant",
                                "content": "見ました。",
                                "content_blocks": [
                                    {
                                        "type": "tool_calls",
                                        "calls": [
                                            {
                                                "id": "c1",
                                                "type": "function",
                                                "function": {
                                                    "name": "feed_image",
                                                    "arguments": "{}",
                                                },
                                            }
                                        ],
                                        "results": [
                                            {
                                                "tool_call_id": "c1",
                                                "content": "目に入れた",
                                                "context_images": [
                                                    "f-svg",
                                                    "/api/v1/files/f-svg/content",
                                                    "/ext/cage/fig.svg",
                                                ],
                                            }
                                        ],
                                    },
                                    {"type": "text", "content": "見ました。"},
                                ],
                            },
                            "m3": {
                                "id": "m3",
                                "parentId": "m2",
                                "role": "user",
                                "content": "続けて",
                            },
                        },
                    },
                }
            ),
        )
        stored = self.Chats.get_chat_by_id(chat.id).chat
        wire = build_wire_messages(stored, self.alice, "m3")
        # 組み直した履歴には SVG を指す image_url が 4 つ(添付 1 + context_images 3)
        assert len(_image_urls(wire)) == 4

        messages = self._convert(wire)
        _assert_only_vlm_types(messages)
        urls = _image_urls(messages)
        assert len(urls) == 4
        for url in urls:
            assert _decoded(url).convert("RGB").getpixel((10, 10)) == (0, 0, 255)
