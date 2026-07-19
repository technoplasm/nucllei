import uuid

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestChats(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_class(cls):
        super().setup_class()

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import ChatForm, Chats

        self.chats = Chats
        self.chats.insert_new_chat(
            "2",
            ChatForm(
                **{
                    "chat": {
                        "name": "chat1",
                        "description": "chat1 description",
                        "tags": ["tag1", "tag2"],
                        "history": {"currentId": "1", "messages": []},
                    }
                }
            ),
        )

    def test_get_session_user_chat_list(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        first_chat = response.json()[0]
        assert first_chat["id"] is not None
        assert first_chat["title"] == "New Chat"
        assert first_chat["created_at"] is not None
        assert first_chat["updated_at"] is not None

    def test_delete_all_user_chats(self):
        # llens(#31): soft delete — 行は残り(監査証憑)、ユーザー向け一覧からは消える
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url("/"))
        assert response.status_code == 200

        remaining = self.chats.get_chats()
        assert len(remaining) == 1
        assert remaining[0].deleted_at is not None

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.status_code == 200
        assert response.json() == []

    def test_get_user_chat_list_by_user_id(self):
        with mock_webui_user(id="3"):
            response = self.fast_api_client.get(self.create_url("/list/user/2"))
        assert response.status_code == 200
        first_chat = response.json()[0]
        assert first_chat["id"] is not None
        assert first_chat["title"] == "New Chat"
        assert first_chat["created_at"] is not None
        assert first_chat["updated_at"] is not None

    def test_create_new_chat(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/new"),
                json={
                    "chat": {
                        "name": "chat2",
                        "description": "chat2 description",
                        "tags": ["tag1", "tag2"],
                    }
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["archived"] is False
        assert data["chat"] == {
            "name": "chat2",
            "description": "chat2 description",
            "tags": ["tag1", "tag2"],
        }
        assert data["user_id"] == "2"
        assert data["id"] is not None
        assert data["share_id"] is None
        assert data["title"] == "New Chat"
        assert data["updated_at"] is not None
        assert data["created_at"] is not None
        assert len(self.chats.get_chats()) == 2

    def test_get_user_chats(self):
        self.test_get_session_user_chat_list()

    def test_get_user_archived_chats(self):
        self.chats.archive_all_chats_by_user_id("2")
        from nucllei.internal.db import Session

        Session.commit()
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/all/archived"))
        assert response.status_code == 200
        first_chat = response.json()[0]
        assert first_chat["id"] is not None
        assert first_chat["title"] == "New Chat"
        assert first_chat["created_at"] is not None
        assert first_chat["updated_at"] is not None

    def test_get_all_user_chats_in_db(self):
        with mock_webui_user(id="4"):
            response = self.fast_api_client.get(self.create_url("/all/db"))
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_get_archived_session_user_chat_list(self):
        self.test_get_user_archived_chats()

    def test_archive_all_chats(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(self.create_url("/archive/all"))
        assert response.status_code == 200
        assert len(self.chats.get_archived_chats_by_user_id("2")) == 1

    def test_get_shared_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        self.chats.update_chat_share_id_by_id(chat_id, chat_id)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/share/{chat_id}"))
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == chat_id
        assert data["chat"] == {
            "name": "chat1",
            "description": "chat1 description",
            "tags": ["tag1", "tag2"],
            "history": {"currentId": "1", "messages": []},
        }
        assert data["id"] == chat_id
        assert data["share_id"] == chat_id
        assert data["title"] == "New Chat"

    def test_get_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == chat_id
        assert data["chat"] == {
            "name": "chat1",
            "description": "chat1 description",
            "tags": ["tag1", "tag2"],
            "history": {"currentId": "1", "messages": []},
        }
        assert data["share_id"] is None
        assert data["title"] == "New Chat"
        assert data["user_id"] == "2"

    def test_update_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{chat_id}"),
                json={
                    "chat": {
                        "name": "chat2",
                        "description": "chat2 description",
                        "tags": ["tag2", "tag4"],
                        "title": "Just another title",
                    }
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == chat_id
        assert data["chat"] == {
            "name": "chat2",
            "title": "Just another title",
            "description": "chat2 description",
            "tags": ["tag2", "tag4"],
            "history": {"currentId": "1", "messages": []},
        }
        assert data["share_id"] is None
        assert data["title"] == "Just another title"
        assert data["user_id"] == "2"

    def test_delete_chat_by_id(self):
        # llens(#31): soft delete — 行は残り(deleted_at)、本人の取得・一覧からは消える
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200
        assert response.json() is True

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat is not None
        assert chat.deleted_at is not None

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert response.status_code == 401

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/"))
        assert response.json() == []

    def test_delete_chat_unshares_it(self):
        # llens(#31): 削除で share_id が外れ、共有コピー行も消える(/s/ が開けなくなる)
        chat_id = self.chats.get_chats()[0].id
        share_id = str(uuid.uuid4())
        self.chats.update_chat_share_id_by_id(chat_id, share_id)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.share_id is None
        assert self.chats.get_chat_by_share_id(share_id) is None

    def test_deleted_chat_admin_only_view_via_share_endpoint(self):
        # llens(#19): 削除済みチャットは admin だけが /share/{chat_id} の直参照で
        # 閲覧できる(deleted_at で「削除済み」を明示)。非 admin には存在しない扱い。
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200

        with mock_webui_user(id="3", role="admin"):
            response = self.fast_api_client.get(self.create_url(f"/share/{chat_id}"))
        assert response.status_code == 200
        assert response.json()["deleted_at"] is not None

        with mock_webui_user(id="4", role="user"):
            response = self.fast_api_client.get(self.create_url(f"/share/{chat_id}"))
        assert response.status_code == 401

    def test_deleted_chat_not_found_via_get_chat(self):
        # llens(#19): /chats/{id}(/c/ の裏)は admin フォールバックでも削除済みを返さない
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}"))
        assert response.status_code == 200

        with mock_webui_user(id="3", role="admin"):
            response = self.fast_api_client.get(self.create_url(f"/{chat_id}"))
        assert response.status_code == 401

    def test_clone_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{chat_id}/clone"), json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] != chat_id
        assert data["chat"] == {
            "branchPointMessageId": "1",
            "description": "chat1 description",
            "history": {"currentId": "1", "messages": []},
            "name": "chat1",
            "originalChatId": chat_id,
            "tags": ["tag1", "tag2"],
            "title": "Clone of New Chat",
        }
        assert data["share_id"] is None
        assert data["title"] == "Clone of New Chat"
        assert data["user_id"] == "2"

    def test_archive_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(self.create_url(f"/{chat_id}/archive"))
        assert response.status_code == 200

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.archived is True

    def test_share_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(self.create_url(f"/{chat_id}/share"))
        assert response.status_code == 200

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.share_id is not None

    def test_delete_shared_chat_by_id(self):
        chat_id = self.chats.get_chats()[0].id
        share_id = str(uuid.uuid4())
        self.chats.update_chat_share_id_by_id(chat_id, share_id)
        with mock_webui_user(id="2"):
            response = self.fast_api_client.delete(self.create_url(f"/{chat_id}/share"))
        assert response.status_code

        chat = self.chats.get_chat_by_id(chat_id)
        assert chat.share_id is None


class TestChatAppendOnlyHistory(AbstractPostgresTest):
    """llens(#31): メッセージ履歴の append-only ガード。

    監査証憑の保全のため、POST /{id} の全量更新で「既存メッセージの消失」と
    「content の非前方一致な書き換え」を 400 で拒否する。追記・ブランチ追加・
    メタ情報(title 等)の変更は通す。
    """

    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import ChatForm, Chats

        self.chats = Chats
        self.history = {
            "currentId": "m2",
            "messages": {
                "m1": {
                    "id": "m1",
                    "parentId": None,
                    "childrenIds": ["m2"],
                    "role": "user",
                    "content": "hello",
                },
                "m2": {
                    "id": "m2",
                    "parentId": "m1",
                    "childrenIds": [],
                    "role": "assistant",
                    "content": "hi there",
                },
            },
        }
        chat = self.chats.insert_new_chat(
            "2", ChatForm(**{"chat": {"title": "t", "history": self.history}})
        )
        self.chat_id = chat.id

    def _update(self, history: dict, extra: dict | None = None):
        with mock_webui_user(id="2"):
            return self.fast_api_client.post(
                self.create_url(f"/{self.chat_id}"),
                json={"chat": {"history": history, **(extra or {})}},
            )

    def _history(self, mutate) -> dict:
        import copy

        history = copy.deepcopy(self.history)
        mutate(history)
        return history

    def test_removing_message_is_rejected(self):
        def mutate(h):
            del h["messages"]["m2"]
            h["messages"]["m1"]["childrenIds"] = []
            h["currentId"] = "m1"

        response = self._update(self._history(mutate))
        assert response.status_code == 400
        # DB は無傷
        chat = self.chats.get_chat_by_id(self.chat_id)
        assert set(chat.chat["history"]["messages"].keys()) == {"m1", "m2"}

    def test_rewriting_content_is_rejected(self):
        def mutate(h):
            h["messages"]["m1"]["content"] = "HELLO (rewritten)"

        response = self._update(self._history(mutate))
        assert response.status_code == 400
        chat = self.chats.get_chat_by_id(self.chat_id)
        assert chat.chat["history"]["messages"]["m1"]["content"] == "hello"

    def test_appending_content_is_allowed(self):
        # ストリーミング終端の保存等: 旧 content が前方一致で保存される追記は通す
        def mutate(h):
            h["messages"]["m2"]["content"] = "hi there, how can I help?"

        response = self._update(self._history(mutate))
        assert response.status_code == 200

    def test_adding_branch_is_allowed(self):
        # 「編集して送信」= 兄弟ブランチの追加(既存メッセージは無傷)
        def mutate(h):
            h["messages"]["m3"] = {
                "id": "m3",
                "parentId": "m1",
                "childrenIds": [],
                "role": "assistant",
                "content": "alternative answer",
            }
            h["messages"]["m1"]["childrenIds"] = ["m2", "m3"]
            h["currentId"] = "m3"

        response = self._update(self._history(mutate))
        assert response.status_code == 200

    def test_metadata_only_update_is_allowed(self):
        response = self._update(self._history(lambda h: None), {"title": "renamed"})
        assert response.status_code == 200
        assert response.json()["chat"]["title"] == "renamed"

    def test_message_overwrite_endpoint_is_removed(self):
        # llens(#31): POST /{id}/messages/{message_id}(無痕跡上書き API)は撤去済み
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url(f"/{self.chat_id}/messages/m1"),
                json={"content": "overwritten"},
            )
        assert response.status_code in (404, 405)
