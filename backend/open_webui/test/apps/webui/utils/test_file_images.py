"""内部 file URL 画像の base64 インライン化の回帰テスト(issue #69)。

画像添付は files テーブル経由に統一され、チャット payload の image_url には
"/api/v1/files/{id}/content" 形式の内部 URL が入る。air-gapped のためモデル側から
URL を fetch できず、モデルへ出る直前に inline_internal_image_urls が
data URL へ変換する(utils/file_images.py、process_chat_payload 末尾で発火)。

- 読取権限のある file だけ変換する(非公開 file は他人のリクエストでは無変換)
- 読めない・不在・storage 欠落はパーツをそのまま残す(404 情報を payload に混ぜない)
- data URL・外部 URL・文字列 content は触らない
"""

import base64
import os

from test.util.abstract_integration_test import AbstractPostgresTest

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")


class TestFileImagesInline(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_method(self):
        super().setup_method()
        from open_webui.config import UPLOAD_DIR
        from open_webui.models.users import Users
        from open_webui.models.files import Files, FileForm

        self.Users = Users
        self.Files = Files
        self.FileForm = FileForm

        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )
        self.bob = Users.insert_new_user(
            id="bob", name="bob", email="bob@h.example", role="user"
        )
        self.admin = Users.insert_new_user(
            id="admin", name="admin", email="admin@h.example", role="admin"
        )

        # alice 所有の画像 file(storage 実体付き・非公開)
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

    def _convert(self, messages, user):
        from open_webui.utils.file_images import inline_internal_image_urls

        return inline_internal_image_urls(messages, user)

    # ---- 変換される ----

    def test_owner_relative_url_converted(self):
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.alice
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url == f"data:image/png;base64,{PNG_B64}"
        # text パーツは不変
        assert messages[0]["content"][0] == {"type": "text", "text": "これは何の画像?"}

    def test_absolute_url_converted(self):
        messages = self._convert(
            self._messages("http://localhost:8080/api/v1/files/f-img/content"),
            self.alice,
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_url_with_query_converted(self):
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content?attachment=false"), self.alice
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_admin_can_convert_foreign_file(self):
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.admin
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_public_marker_file_converted_for_other_user(self):
        self.Files.update_file_access_control_by_id("f-img", {"read": {"public": True}})
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.bob
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_string_image_url_form_converted(self):
        # OpenAI 形式の {"url": ...} でなく素の文字列でも変換(shape は維持)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "/api/v1/files/f-img/content"}
                ],
            }
        ]
        messages = self._convert(messages, self.alice)
        assert messages[0]["content"][0]["image_url"] == (
            f"data:image/png;base64,{PNG_B64}"
        )

    def test_mime_fallback_from_extension(self):
        # meta.content_type が無い場合は拡張子から推定
        with open(f"{self._upload_dir()}/f-jpg_y.jpg", "wb") as f:
            f.write(PNG_BYTES)
        self.Files.insert_new_file(
            "alice",
            self.FileForm(
                id="f-jpg",
                filename="y.jpg",
                path=f"{self._upload_dir()}/f-jpg_y.jpg",
                meta={},
                data={},
            ),
        )
        messages = self._convert(
            self._messages("/api/v1/files/f-jpg/content"), self.alice
        )
        url = messages[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def _upload_dir(self):
        from open_webui.config import UPLOAD_DIR

        return UPLOAD_DIR

    # ---- 変換されない(スキップして payload そのまま) ----

    def test_unreadable_file_left_as_is(self):
        # 非公開 file は他人のリクエストでは変換されない(URL がそのまま残る)
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.bob
        )
        assert (
            messages[0]["content"][1]["image_url"]["url"]
            == "/api/v1/files/f-img/content"
        )

    def test_missing_file_left_as_is(self):
        messages = self._convert(
            self._messages("/api/v1/files/no-such/content"), self.alice
        )
        assert (
            messages[0]["content"][1]["image_url"]["url"]
            == "/api/v1/files/no-such/content"
        )

    def test_storage_missing_left_as_is(self):
        # DB 行はあるが storage 実体が消えている場合もスキップ(例外にしない)
        os.remove(self.img_path)
        messages = self._convert(
            self._messages("/api/v1/files/f-img/content"), self.alice
        )
        assert (
            messages[0]["content"][1]["image_url"]["url"]
            == "/api/v1/files/f-img/content"
        )

    def test_data_url_untouched(self):
        data_url = f"data:image/png;base64,{PNG_B64}"
        messages = self._convert(self._messages(data_url), self.alice)
        assert messages[0]["content"][1]["image_url"]["url"] == data_url

    def test_external_url_untouched(self):
        url = "https://example.com/images/cat.png"
        messages = self._convert(self._messages(url), self.alice)
        assert messages[0]["content"][1]["image_url"]["url"] == url

    def test_plain_string_content_untouched(self):
        messages = [{"role": "user", "content": "/api/v1/files/f-img/content"}]
        assert self._convert(messages, self.alice) == [
            {"role": "user", "content": "/api/v1/files/f-img/content"}
        ]
