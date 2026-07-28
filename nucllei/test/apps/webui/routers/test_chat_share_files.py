"""チャット共有とファイル権限の回帰テスト(issue #41、_memos/chat-share-files.md)。

- share = 参照 file の read を認証ユーザー全員へ拡大(public マーカー)。
  共有者自身の file のみ public 化(他人の file_id 混入による権限拡大を防ぐ)。
- 共有解除では file の権限を戻さない(狭める操作はファイル管理機構 #69 のみ)。
- clone = file の実体コピー(cloner 所有・private)。元 file の削除・共有解除に耐える。
- chat.share(既定 on)/ chat.clone(既定 off)のサーバー側 enforcement。
"""

import os

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestChatShareFiles(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.config import UPLOAD_DIR
        from nucllei.models.users import Users
        from nucllei.models.files import Files, FileForm
        from nucllei.models.chats import ChatForm, Chats

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

        # alice 所有の添付(storage の実体ファイル付き)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        self.src_path = f"{UPLOAD_DIR}/f-alice_a.txt"
        with open(self.src_path, "w") as f:
            f.write("raw-content")
        Files.insert_new_file(
            "alice",
            FileForm(
                id="f-alice",
                filename="a.txt",
                path=self.src_path,
                meta={"name": "a.txt", "content_type": "text/plain", "size": 11},
                data={"content": "extracted-text"},
            ),
        )

    def _file_entry(self, file_id):
        return {
            "type": "file",
            "id": file_id,
            "url": f"/api/v1/files/{file_id}",
            "file": {"id": file_id},
        }

    def _insert_chat(self, owner="alice", file_ids=("f-alice",)):
        entries = [self._file_entry(fid) for fid in file_ids]
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

    def _share(self, chat_id, user_id="alice"):
        with mock_webui_user(id=user_id):
            response = self.fast_api_client.post(self.create_url(f"/{chat_id}/share"))
        return response

    def _set_chat_permission(self, key, value):
        from nucllei.main import app

        permissions = app.state.config.USER_PERMISSIONS
        permissions["chat"] = {**permissions.get("chat", {}), key: value}
        app.state.config.USER_PERMISSIONS = permissions

    # ---- share = public 化 ----

    def test_share_marks_files_public(self):
        from nucllei.utils.access_control import has_read_access_to_file

        chat = self._insert_chat()
        assert has_read_access_to_file("f-alice", self.bob) is False

        response = self._share(chat.id)
        assert response.status_code == 200

        file = self.Files.get_file_by_id("f-alice")
        assert (file.access_control or {}).get("read", {}).get("public") is True
        assert has_read_access_to_file("f-alice", self.bob) is True

    def test_share_does_not_publish_foreign_files(self):
        # alice の chat JSON に bob の file_id を混入させても public 化されない
        self.Files.insert_new_file(
            "bob",
            self.FileForm(
                id="f-bob", filename="b.txt", path="/data/b.txt", meta={}, data={}
            ),
        )
        chat = self._insert_chat(file_ids=("f-alice", "f-bob"))
        response = self._share(chat.id)
        assert response.status_code == 200

        assert self.Files.get_file_by_id("f-bob").access_control is None

    def test_shared_file_endpoints_readable_by_other_user(self):
        chat = self._insert_chat()
        self._share(chat.id)

        with mock_webui_user(id="bob"):
            meta = self.fast_api_client.get("/api/v1/files/f-alice")
            content = self.fast_api_client.get("/api/v1/files/f-alice/data/content")
        assert meta.status_code == 200
        assert content.status_code == 200
        assert content.json()["content"] == "extracted-text"

    def test_unshare_keeps_files_public(self):
        from nucllei.utils.access_control import has_read_access_to_file

        chat = self._insert_chat()
        self._share(chat.id)

        with mock_webui_user(id="alice"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat.id}/share"))
        assert response.status_code == 200

        # 原則: 共有解除で権限を狭めない(戻すのはファイル管理機構 #69 の明示操作)
        assert has_read_access_to_file("f-alice", self.bob) is True

    def test_reshare_publishes_newly_attached_files(self):
        chat = self._insert_chat()
        self._share(chat.id)

        # share 後に添付を追加 → 再共有で新規分も public 化
        self.Files.insert_new_file(
            "alice",
            self.FileForm(
                id="f-alice2", filename="a2.txt", path="/data/a2.txt", meta={}, data={}
            ),
        )
        updated = dict(self.Chats.get_chat_by_id(chat.id).chat)
        updated["files"] = updated.get("files", []) + [self._file_entry("f-alice2")]
        self.Chats.update_chat_by_id(chat.id, updated)

        response = self._share(chat.id)
        assert response.status_code == 200
        file = self.Files.get_file_by_id("f-alice2")
        assert (file.access_control or {}).get("read", {}).get("public") is True

    def test_share_skips_deleted_files(self):
        # 添付 2 file のうち 1 つを soft delete(#94)して共有 → active だけ public 化され、
        # deleted の access_control は据え置き(publish_chat_files が既定取得で skip)
        self.Files.insert_new_file(
            "alice",
            self.FileForm(
                id="f-alice-del", filename="d.txt", path="/data/d.txt", meta={}, data={}
            ),
        )
        chat = self._insert_chat(file_ids=("f-alice", "f-alice-del"))
        self.Files.soft_delete_file_by_id("f-alice-del", deleted_by="alice")

        response = self._share(chat.id)
        assert response.status_code == 200

        active = self.Files.get_file_by_id("f-alice")
        assert (active.access_control or {}).get("read", {}).get("public") is True
        deleted = self.Files.get_file_by_id("f-alice-del", include_deleted=True)
        assert deleted.deleted_at is not None
        assert deleted.access_control is None

    def test_middleware_filter_keeps_public_file(self):
        from nucllei.utils.middleware import filter_files_by_user_access

        chat = self._insert_chat()
        self._share(chat.id)

        files = [{"id": "f-alice", "type": "file"}]
        assert filter_files_by_user_access(files, self.bob) == files

    # ---- 権限フラグ(サーバー側 enforcement) ----

    def test_share_denied_when_flag_off(self):
        chat = self._insert_chat()
        self._set_chat_permission("share", False)
        try:
            response = self._share(chat.id)
            assert response.status_code == 401
        finally:
            self._set_chat_permission("share", True)

    def test_clone_denied_by_default(self):
        chat = self._insert_chat()
        self._share(chat.id)
        share_id = self.Chats.get_chat_by_id(chat.id).share_id

        with mock_webui_user(id="bob"):
            response = self.fast_api_client.post(
                self.create_url(f"/{share_id}/clone/shared")
            )
        assert response.status_code == 401

    # ---- clone = 実体コピー ----

    def _clone_as_bob(self, share_id):
        self._set_chat_permission("clone", True)
        try:
            with mock_webui_user(id="bob"):
                response = self.fast_api_client.post(
                    self.create_url(f"/{share_id}/clone/shared")
                )
        finally:
            self._set_chat_permission("clone", False)
        return response

    def test_clone_copies_files_to_cloner(self):
        chat = self._insert_chat()
        self._share(chat.id)
        share_id = self.Chats.get_chat_by_id(chat.id).share_id

        response = self._clone_as_bob(share_id)
        assert response.status_code == 200
        cloned = response.json()

        # file_id が新 id に差し替わっている(トップレベル + message 内)
        new_entry = cloned["chat"]["files"][0]
        new_id = new_entry["id"]
        assert new_id != "f-alice"
        assert new_entry["url"] == f"/api/v1/files/{new_id}"
        assert new_entry["file"]["id"] == new_id
        msg_entry = cloned["chat"]["history"]["messages"]["m1"]["files"][0]
        assert msg_entry["id"] == new_id

        # コピーは bob 所有・private・content/raw も複製
        copied = self.Files.get_file_by_id(new_id)
        assert copied.user_id == "bob"
        assert copied.access_control is None
        assert copied.data.get("content") == "extracted-text"
        assert copied.path != self.src_path
        with open(copied.path) as f:
            assert f.read() == "raw-content"

        # 元 file は不変(alice 所有・public マーカーのまま)
        src = self.Files.get_file_by_id("f-alice")
        assert src.user_id == "alice"
        assert (src.access_control or {}).get("read", {}).get("public") is True

    def test_clone_survives_source_deletion_and_unshare(self):
        from nucllei.utils.access_control import has_read_access_to_file

        chat = self._insert_chat()
        self._share(chat.id)
        share_id = self.Chats.get_chat_by_id(chat.id).share_id

        response = self._clone_as_bob(share_id)
        assert response.status_code == 200
        new_id = response.json()["chat"]["files"][0]["id"]

        # 元 owner が共有解除 + file 削除(#94: soft delete)しても clone 側は生きる
        with mock_webui_user(id="alice"):
            self.fast_api_client.delete(self.create_url(f"/{chat.id}/share"))
        self.Files.soft_delete_file_by_id("f-alice", deleted_by="alice")
        os.remove(self.src_path)

        copied = self.Files.get_file_by_id(new_id)
        assert copied is not None
        assert has_read_access_to_file(new_id, self.bob) is True
        with open(copied.path) as f:
            assert f.read() == "raw-content"

    def test_clone_leaves_unreadable_file_reference(self):
        # 第三者(carol)の private file が混ざった共有チャットを clone しても
        # コピーされず参照はそのまま(送信時に middleware が drop する従来挙動)
        self.Users.insert_new_user(
            id="carol", name="carol", email="carol@h.example", role="user"
        )
        self.Files.insert_new_file(
            "carol",
            self.FileForm(
                id="f-carol", filename="c.txt", path="/data/c.txt", meta={}, data={}
            ),
        )
        chat = self._insert_chat(file_ids=("f-alice", "f-carol"))
        self._share(chat.id)
        share_id = self.Chats.get_chat_by_id(chat.id).share_id

        response = self._clone_as_bob(share_id)
        assert response.status_code == 200
        entries = response.json()["chat"]["files"]
        ids = [entry["id"] for entry in entries]
        assert "f-carol" in ids  # 差し替えなし
        assert "f-alice" not in ids  # public 分は複製済み
