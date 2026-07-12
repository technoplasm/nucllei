"""画像 file のチャット共有/clone 回帰テスト(issue #69)。

画像添付が files テーブル経由に統一されたことで、chat JSON の type=="image"
エントリ(id 付き)も file 参照として扱う:
- get_file_ids_from_chat が image エントリの id を収集する
  (base64 直入れ時代の旧エントリは id が無いので収集されない)
- share = publish_chat_files が画像 file も public 化する
- clone = 画像 file も実体コピーし、url を "/api/v1/files/{id}/content" 形式で差し替える
"""

import os

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image-bytes"


class TestChatImageFiles(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from open_webui.config import UPLOAD_DIR
        from open_webui.models.users import Users
        from open_webui.models.files import Files, FileForm
        from open_webui.models.chats import ChatForm, Chats

        self.Users = Users
        self.Files = Files
        self.FileForm = FileForm
        self.Chats = Chats
        self.ChatForm = ChatForm

        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )
        self.bob = Users.insert_new_user(
            id="bob", name="bob", email="bob@h.example", role="user"
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

    def _image_entry(self, file_id):
        return {
            "type": "image",
            "id": file_id,
            "url": f"/api/v1/files/{file_id}/content",
        }

    def _legacy_image_entry(self):
        # base64 直入れ時代の旧エントリ(id を持たない)
        return {"type": "image", "url": "data:image/png;base64,AAAA"}

    def _insert_chat(self, entries, owner="alice"):
        return self.Chats.insert_new_chat(
            owner,
            self.ChatForm(
                chat={
                    "title": "c1",
                    "files": list(entries),
                    "history": {
                        "currentId": "m1",
                        "messages": {
                            "m1": {
                                "id": "m1",
                                "role": "user",
                                "content": "hi",
                                "files": list(entries),
                            }
                        },
                    },
                }
            ),
        )

    def _set_chat_permission(self, key, value):
        from open_webui.main import app

        permissions = app.state.config.USER_PERMISSIONS
        permissions["chat"] = {**permissions.get("chat", {}), key: value}
        app.state.config.USER_PERMISSIONS = permissions

    # ---- get_file_ids_from_chat ----

    def test_collects_image_ids_and_skips_legacy_entries(self):
        from open_webui.routers.chats import get_file_ids_from_chat

        chat = {
            "files": [
                self._image_entry("f-img"),
                self._legacy_image_entry(),
                {"type": "file", "id": "f-doc"},
            ],
            "history": {
                "messages": {
                    "m1": {"files": [self._image_entry("f-img")]},  # 重複は 1 回
                    "m2": {"files": [self._image_entry("f-img2")]},
                }
            },
        }
        assert get_file_ids_from_chat(chat) == ["f-img", "f-doc", "f-img2"]

    # ---- share = publish_chat_files ----

    def test_share_publishes_image_file(self):
        from open_webui.utils.access_control import has_read_access_to_file

        chat = self._insert_chat([self._image_entry("f-img")])
        assert has_read_access_to_file("f-img", self.bob) is False

        with mock_webui_user(id="alice"):
            response = self.fast_api_client.post(self.create_url(f"/{chat.id}/share"))
        assert response.status_code == 200

        file = self.Files.get_file_by_id("f-img")
        assert (file.access_control or {}).get("read", {}).get("public") is True
        assert has_read_access_to_file("f-img", self.bob) is True

    # ---- clone = 実体コピー + url 差し替え ----

    def test_clone_copies_image_file_and_rewrites_content_url(self):
        chat = self._insert_chat([self._image_entry("f-img")])
        with mock_webui_user(id="alice"):
            self.fast_api_client.post(self.create_url(f"/{chat.id}/share"))
        share_id = self.Chats.get_chat_by_id(chat.id).share_id

        self._set_chat_permission("clone", True)
        try:
            with mock_webui_user(id="bob"):
                response = self.fast_api_client.post(
                    self.create_url(f"/{share_id}/clone/shared")
                )
        finally:
            self._set_chat_permission("clone", False)
        assert response.status_code == 200
        cloned = response.json()

        entry = cloned["chat"]["files"][0]
        new_id = entry["id"]
        assert new_id != "f-img"
        # image エントリの url は content URL 形式のまま新 id を指す
        assert entry["url"] == f"/api/v1/files/{new_id}/content"
        msg_entry = cloned["chat"]["history"]["messages"]["m1"]["files"][0]
        assert msg_entry["id"] == new_id
        assert msg_entry["url"] == f"/api/v1/files/{new_id}/content"

        # コピーは bob 所有・private・raw 複製
        copied = self.Files.get_file_by_id(new_id)
        assert copied.user_id == "bob"
        assert copied.access_control is None
        assert copied.path != self.img_path
        with open(copied.path, "rb") as f:
            assert f.read() == PNG_BYTES

        # コピー先はモデル送信時にインライン化できる(clone → 再送の end-to-end)
        from open_webui.utils.file_images import inline_internal_image_urls

        messages = [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": entry["url"]}}],
            }
        ]
        inline_internal_image_urls(messages, self.bob)
        assert messages[0]["content"][0]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
