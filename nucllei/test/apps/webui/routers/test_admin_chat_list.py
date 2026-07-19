"""admin チャット閲覧ビュー(#19 一覧)の router 回帰。

- GET /chats/admin/list: ページング一覧(ソート・limit/offset・total・フィルタ・軽量行の導出列)
- GET /chats/admin/users: チャットを持つユーザー一覧(admin 専有)
- GET /chats/admin/models: 使用モデル一覧(admin 専有)
- ENABLE_ADMIN_CHAT_ACCESS=False で全 endpoint が閉じる
"""

from unittest.mock import patch

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict


class TestAdminChatList(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats
        from nucllei.models.users import Users

        cls.chats = Chats
        cls.users = Users

    def _insert_chat(
        self,
        user_id="1",
        title="chat",
        models=None,
        messages=None,
        files=None,
        usage=None,
        created_at=None,
        updated_at=None,
    ):
        from nucllei.internal.db import get_db
        from nucllei.models.chats import Chat, ChatForm

        chat_json = {
            "title": title,
            "models": models or [],
            "messages": messages or [],
            "history": {"currentId": None, "messages": {}},
        }
        if files is not None:
            chat_json["files"] = files

        chat = self.chats.insert_new_chat(user_id, ChatForm(chat=chat_json))
        assert chat is not None

        values = {}
        if usage is not None:
            values["meta"] = {"usage": usage}
        if created_at is not None:
            values["created_at"] = created_at
        if updated_at is not None:
            values["updated_at"] = updated_at
        if values:
            with get_db() as db:
                db.query(Chat).filter_by(id=chat.id).update(values)
                db.commit()
        return chat

    # ---- gating ----

    def test_non_admin_prohibited(self):
        # get_admin_user の実ゲートを通す(mock_webui_user は素通りするため strict)
        for path in ("/admin/list", "/admin/users", "/admin/models"):
            with mock_webui_user_strict(id="1", role="user"):
                r = self.fast_api_client.get(self.create_url(path))
            assert r.status_code == 401, f"{path}: {r.text}"

    def test_closed_when_admin_chat_access_disabled(self):
        for path in ("/admin/list", "/admin/users", "/admin/models"):
            with patch("nucllei.routers.chats.ENABLE_ADMIN_CHAT_ACCESS", False):
                with mock_webui_user(id="1", role="admin"):
                    r = self.fast_api_client.get(self.create_url(path))
            assert r.status_code == 401, f"{path}: {r.text}"

    # ---- GET /chats/admin/list ----

    def test_list_default_sort_and_paging(self):
        ids = {}
        for title, ts in (("c_old", 100), ("c_new", 300), ("c_mid", 200)):
            chat = self._insert_chat(title=title, updated_at=ts, created_at=ts)
            ids[title] = chat.id
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/list"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3
        # デフォルトは updated_at desc
        assert [i["title"] for i in body["items"]] == ["c_new", "c_mid", "c_old"]

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"limit": 1, "offset": 1})
            )
        body = r.json()
        # total は filter 適用後・ページング前の件数
        assert body["total"] == 3
        assert [i["title"] for i in body["items"]] == ["c_mid"]

    def test_list_order_by_created_at_asc(self):
        for title, created, updated in (
            ("c_a", 300, 1),
            ("c_b", 100, 2),
            ("c_c", 200, 3),
        ):
            self._insert_chat(title=title, created_at=created, updated_at=updated)
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url(
                    "/admin/list", {"order_by": "created_at", "direction": "asc"}
                )
            )
        assert [i["title"] for i in r.json()["items"]] == ["c_b", "c_c", "c_a"]

    def test_list_invalid_params_rejected(self):
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"order_by": "title"})
            )
            assert r.status_code == 400, r.text
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"direction": "up"})
            )
            assert r.status_code == 400, r.text

    def test_list_user_filter(self):
        self._insert_chat(user_id="1", title="mine")
        self._insert_chat(user_id="2", title="theirs_a")
        self._insert_chat(user_id="2", title="theirs_b")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"user_id": "2"})
            )
        body = r.json()
        assert body["total"] == 2
        assert {i["title"] for i in body["items"]} == {"theirs_a", "theirs_b"}

    def test_list_deleted_filter(self):
        # llens(#31): soft delete 済みは既定で一覧に出る(deleted_at 付き)。exclude/only で絞れる
        self._insert_chat(title="kept")
        removed = self._insert_chat(title="removed")
        assert self.chats.delete_chat_by_id(removed.id)

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/list"))
            body = r.json()
            assert body["total"] == 2
            by_title = {i["title"]: i for i in body["items"]}
            assert by_title["removed"]["deleted_at"] is not None
            assert by_title["kept"]["deleted_at"] is None

            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"deleted": "exclude"})
            )
            body = r.json()
            assert body["total"] == 1
            assert body["items"][0]["title"] == "kept"

            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"deleted": "only"})
            )
            body = r.json()
            assert body["total"] == 1
            assert body["items"][0]["title"] == "removed"

            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"deleted": "bogus"})
            )
            assert r.status_code == 400, r.text

    def test_list_title_filter_case_insensitive(self):
        self._insert_chat(title="Weekly Report 2026")
        self._insert_chat(title="memo")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"title": "report"})
            )
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "Weekly Report 2026"

    def test_list_model_filter(self):
        self._insert_chat(title="kimi_only", models=["kimi-k2.6"])
        self._insert_chat(title="both", models=["kimi-k2.6", "gpt-oss:20b"])
        self._insert_chat(title="none", models=[])
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"model": "kimi-k2.6"})
            )
            assert {i["title"] for i in r.json()["items"]} == {"kimi_only", "both"}
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"model": "gpt-oss:20b"})
            )
            assert {i["title"] for i in r.json()["items"]} == {"both"}

    def test_list_updated_range_filter(self):
        for title, ts in (("c_100", 100), ("c_200", 200), ("c_300", 300)):
            self._insert_chat(title=title, updated_at=ts)
        with mock_webui_user(id="1", role="admin"):
            # 両端含む
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"updated_from": 100, "updated_to": 200})
            )
            assert {i["title"] for i in r.json()["items"]} == {"c_100", "c_200"}
            # 片側だけでも効く
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"updated_from": 201})
            )
            assert {i["title"] for i in r.json()["items"]} == {"c_300"}
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"updated_to": 199})
            )
            assert {i["title"] for i in r.json()["items"]} == {"c_100"}

    # ---- 軽量行の導出列 ----

    def test_item_derived_fields(self):
        self.users.insert_new_user(id="2", name="Bob", email="b@x", role="user")
        usage = {
            "prompt_tokens": 1179,
            "completion_tokens": 766,
            "context_size": 262144,
        }
        self._insert_chat(
            user_id="2",
            title="derived",
            models=["kimi-k2.6"],
            messages=[
                {
                    "role": "user",
                    "content": "  first\n question  ",
                    "files": [{"type": "file", "id": "f1"}],
                },
                {"role": "assistant", "content": "answer"},
                {
                    "role": "user",
                    "content": "second",
                    "files": [{"type": "file", "id": "f1"}],  # 重複はユニーク化
                },
            ],
            files=[
                {"type": "file", "id": "f1"},
                {"type": "image", "id": "f2"},
                {"type": "collection"},  # 対象外 type は数えない
            ],
            usage=usage,
        )
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/list"))
        item = r.json()["items"][0]
        assert item["user_name"] == "Bob"
        assert item["title"] == "derived"
        # snippet は最初の user メッセージ冒頭(空白は畳む)。chat JSON 全体は返さない
        assert item["snippet"] == "first question"
        assert "chat" not in item
        assert item["models"] == ["kimi-k2.6"]
        assert item["message_count"] == 3
        assert item["turn_count"] == 2
        assert item["file_count"] == 2
        assert item["usage"] == usage
        assert item["archived"] is False
        assert item["share_id"] is None

    def test_item_defaults_for_old_chat(self):
        # usage 未記録(#57 以前)・メッセージ無しの旧チャットでも壊れない
        self._insert_chat(title="bare")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/list"))
        item = r.json()["items"][0]
        assert item["usage"] is None
        assert item["snippet"] == ""
        assert item["message_count"] == 0
        assert item["turn_count"] == 0
        assert item["file_count"] == 0

    def test_snippet_from_content_blocks(self):
        self._insert_chat(
            title="blocks",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "text", "text": "world"},
                    ],
                }
            ],
        )
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/list"))
        assert r.json()["items"][0]["snippet"] == "hello world"

    def test_snippet_truncated(self):
        self._insert_chat(
            title="long",
            messages=[{"role": "user", "content": "x" * 500}],
        )
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/list"))
        assert r.json()["items"][0]["snippet"] == "x" * 160

    # ---- GET /chats/admin/users ----

    def test_admin_users(self):
        self.users.insert_new_user(id="1", name="Alice", email="a@x", role="admin")
        self.users.insert_new_user(id="2", name="Bob", email="b@x", role="user")
        self.users.insert_new_user(id="3", name="NoChats", email="n@x", role="user")
        self._insert_chat(user_id="1")
        self._insert_chat(user_id="2")
        self._insert_chat(user_id="2")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/users"))
        assert r.status_code == 200, r.text
        owners = {u["id"]: u for u in r.json()}
        # チャットを 1 件も持たないユーザーは含まれない
        assert set(owners.keys()) == {"1", "2"}
        assert owners["2"] == {
            "id": "2",
            "name": "Bob",
            "email": "b@x",
            "chat_count": 2,
        }
        assert owners["1"]["chat_count"] == 1

    # ---- GET /chats/admin/models ----

    def test_admin_models(self):
        self._insert_chat(models=["kimi-k2.6"])
        self._insert_chat(models=["kimi-k2.6", "gpt-oss:20b"])
        self._insert_chat(models=[])
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/admin/models"))
        assert r.status_code == 200, r.text
        assert r.json() == [
            {"model": "kimi-k2.6", "chat_count": 2},
            {"model": "gpt-oss:20b", "chat_count": 1},
        ]

    # ---- admin からの共有権限編集(#19 残スコープ) ----

    def test_admin_can_share_and_unshare_other_users_chat(self):
        chat = self._insert_chat(user_id="2", title="other's chat")

        # admin(非 owner)が共有化できる
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url(f"/{chat.id}/share"))
        assert r.status_code == 200, r.text
        assert r.json()["id"] != chat.id  # 返るのは共有スナップショット

        refreshed = self.chats.get_chat_by_id(chat.id)
        assert refreshed.share_id is not None

        # admin(非 owner)が共有解除できる
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url(f"/{chat.id}/share"))
        assert r.status_code == 200, r.text
        assert r.json() is True
        assert self.chats.get_chat_by_id(chat.id).share_id is None

    def test_admin_share_closed_when_admin_chat_access_disabled(self):
        chat = self._insert_chat(user_id="2")
        with patch("nucllei.routers.chats.ENABLE_ADMIN_CHAT_ACCESS", False):
            with mock_webui_user(id="1", role="admin"):
                r = self.fast_api_client.post(self.create_url(f"/{chat.id}/share"))
            assert r.status_code == 401, r.text
            with mock_webui_user(id="1", role="admin"):
                r = self.fast_api_client.delete(self.create_url(f"/{chat.id}/share"))
            assert r.status_code == 401, r.text
        assert self.chats.get_chat_by_id(chat.id).share_id is None

    def test_user_cannot_share_others_chat(self):
        chat = self._insert_chat(user_id="2")
        with mock_webui_user(id="3", role="user"):
            r = self.fast_api_client.post(self.create_url(f"/{chat.id}/share"))
        assert r.status_code == 401, r.text
        with mock_webui_user(id="3", role="user"):
            r = self.fast_api_client.delete(self.create_url(f"/{chat.id}/share"))
        assert r.status_code == 401, r.text

    def test_admin_can_get_others_chat_by_id(self):
        # ShareChatModal が share_id を読む経路。ゲート off では従来どおり 401
        chat = self._insert_chat(user_id="2")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url(f"/{chat.id}"))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == chat.id

        with patch("nucllei.routers.chats.ENABLE_ADMIN_CHAT_ACCESS", False):
            with mock_webui_user(id="1", role="admin"):
                r = self.fast_api_client.get(self.create_url(f"/{chat.id}"))
            assert r.status_code == 401, r.text

    def test_shared_filter(self):
        shared_chat = self._insert_chat(user_id="2", title="shared one")
        self._insert_chat(user_id="2", title="not shared")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url(f"/{shared_chat.id}/share"))
        assert r.status_code == 200, r.text

        # only = 共有中のみ(共有スナップショット行自体は user_id が實 owner でないため
        # admin 一覧には出るが、share_id を持つのは元チャットだけ)
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"shared": "only"})
            )
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [item["title"] for item in items] == ["shared one"]
        assert items[0]["share_id"] is not None

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"shared": "exclude"})
            )
        assert r.status_code == 200, r.text
        assert all(item["share_id"] is None for item in r.json()["items"])

        # 不正値は 400
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/admin/list", {"shared": "bogus"})
            )
        assert r.status_code == 400, r.text
