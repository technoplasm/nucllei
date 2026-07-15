"""ファイル管理 API(#69 BE-2)の router 回帰。

- GET /files/list: ページング一覧(ソート・limit/offset・total・スコープ・user_ids)
- GET /files/uploaders: file を持つユーザー一覧(admin 専有)
- POST /files/{id}/access_control/update: 共有状態のフル編集(owner/admin のみ、shape 検証)
- accessed_at: content 取得でタッチされ、一覧・メタ取得では更新されない
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict


class TestFilesManagement(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.files import Files
        from open_webui.models.users import Users

        cls.files = Files
        cls.users = Users

    def _insert_file(
        self, fid, user_id="1", access_control=None, data=None, filename=None
    ):
        from open_webui.models.files import FileForm

        file = self.files.insert_new_file(
            user_id,
            FileForm(
                id=fid,
                filename=filename or f"{fid}.txt",
                path=f"/tmp/{fid}.txt",
                data=data or {},
                meta={"name": filename or f"{fid}.txt", "size": 5},
                access_control=access_control,
            ),
        )
        assert file is not None
        return file

    def _set_times(self, fid, created_at=None, accessed_at=None):
        from open_webui.internal.db import get_db
        from open_webui.models.files import File

        values = {}
        if created_at is not None:
            values["created_at"] = created_at
        if accessed_at is not None:
            values["accessed_at"] = accessed_at
        with get_db() as db:
            db.query(File).filter_by(id=fid).update(values)
            db.commit()

    # ---- GET /files/list ----

    def test_list_sort_by_created_at(self):
        for fid, ts in (("f_a", 100), ("f_b", 300), ("f_c", 200)):
            self._insert_file(fid)
            self._set_times(fid, created_at=ts)
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/list"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3
        assert [item["id"] for item in body["items"]] == ["f_b", "f_c", "f_a"]

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/list", {"direction": "asc"}))
        assert [item["id"] for item in r.json()["items"]] == ["f_a", "f_c", "f_b"]

    def test_list_sort_by_accessed_at(self):
        for fid, ts in (("f_x", 300), ("f_y", 100), ("f_z", 200)):
            self._insert_file(fid)
            self._set_times(fid, created_at=1, accessed_at=ts)
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/list", {"order_by": "accessed_at"})
            )
        assert r.status_code == 200, r.text
        assert [item["id"] for item in r.json()["items"]] == ["f_x", "f_z", "f_y"]

    def test_list_sort_by_filename(self):
        # 大文字小文字を無視した名前順
        for fid, name in (("f_1", "banana.txt"), ("f_2", "Apple.txt"), ("f_3", "cherry.txt")):
            self._insert_file(fid, filename=name)
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/list", {"order_by": "filename", "direction": "asc"})
            )
        assert r.status_code == 200, r.text
        assert [item["id"] for item in r.json()["items"]] == ["f_2", "f_1", "f_3"]

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/list", {"order_by": "filename", "direction": "desc"})
            )
        assert [item["id"] for item in r.json()["items"]] == ["f_3", "f_1", "f_2"]

    def test_list_limit_offset_and_total(self):
        for i in range(5):
            self._insert_file(f"f_{i}")
            self._set_times(f"f_{i}", created_at=100 + i)
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/list", {"limit": 2, "offset": 1})
            )
        body = r.json()
        # total は filter 適用後・ページング前の全件数
        assert body["total"] == 5
        assert [item["id"] for item in body["items"]] == ["f_3", "f_2"]

    def test_list_invalid_order_by_rejected(self):
        with mock_webui_user(id="1"):
            r = self.fast_api_client.get(
                self.create_url("/list", {"order_by": "updated_at"})
            )
        assert r.status_code == 400, r.text

    def test_list_non_admin_sees_only_own_files(self):
        self._insert_file(
            "f_mine", user_id="1", data={"content": "x", "status": "success"}
        )
        # 全体公開マーカー付きでも一覧は自分の file のみ
        self._insert_file(
            "f_theirs", user_id="2", access_control={"read": {"public": True}}
        )
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.get(self.create_url("/list"))
        body = r.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == "f_mine"
        # 軽量形: data.content は落ち、status と meta は残る
        assert item["data"] == {"status": "success"}
        assert item["meta"]["name"] == "f_mine.txt"

    def test_list_user_ids_forbidden_for_non_admin(self):
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.get(self.create_url("/list", {"user_ids": "2"}))
        assert r.status_code == 403, r.text

    def test_list_admin_user_ids_filter(self):
        self._insert_file("f_u1", user_id="1")
        self._insert_file("f_u2a", user_id="2")
        self._insert_file("f_u2b", user_id="2")
        self._insert_file("f_u3", user_id="3")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/list", {"user_ids": "2,3"}))
        body = r.json()
        assert body["total"] == 3
        assert {item["id"] for item in body["items"]} == {"f_u2a", "f_u2b", "f_u3"}

    def test_list_filename_filter(self):
        self._insert_file("f_rep", filename="report_2026.pdf")
        self._insert_file("f_memo", filename="memo.txt")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(
                self.create_url("/list", {"filename": "report"})
            )
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "f_rep"

    # ---- GET /files/uploaders ----

    def test_uploaders_admin(self):
        self.users.insert_new_user(id="1", name="Alice", email="a@x", role="admin")
        self.users.insert_new_user(id="2", name="Bob", email="b@x", role="user")
        self.users.insert_new_user(id="3", name="NoFiles", email="n@x", role="user")
        self._insert_file("f_a1", user_id="1")
        self._insert_file("f_b1", user_id="2")
        self._insert_file("f_b2", user_id="2")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/uploaders"))
        assert r.status_code == 200, r.text
        uploaders = {u["id"]: u for u in r.json()}
        # file を 1 件も持たないユーザーは含まれない
        assert set(uploaders.keys()) == {"1", "2"}
        assert uploaders["2"] == {
            "id": "2",
            "name": "Bob",
            "email": "b@x",
            "file_count": 2,
        }
        assert uploaders["1"]["file_count"] == 1

    def test_uploaders_non_admin_prohibited(self):
        # get_admin_user の実ゲートを通す(mock_webui_user は素通りするため strict)
        with mock_webui_user_strict(id="1", role="user"):
            r = self.fast_api_client.get(self.create_url("/uploaders"))
        assert r.status_code == 401, r.text

    # ---- POST /files/{id}/access_control/update ----

    def _update_acl(self, fid, access_control):
        return self.fast_api_client.post(
            self.create_url(f"/{fid}/access_control/update"),
            json={"access_control": access_control},
        )

    def test_owner_sets_and_clears_public_marker(self):
        self._insert_file("f_pub", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self._update_acl("f_pub", {"read": {"public": True}})
            assert r.status_code == 200, r.text
            assert r.json()["access_control"] == {"read": {"public": True}}

            # null に戻す = 非公開(owner のみ)
            r = self._update_acl("f_pub", None)
            assert r.status_code == 200, r.text
            assert r.json()["access_control"] is None
        assert self.files.get_file_by_id("f_pub").access_control is None

    def test_owner_sets_group_acl(self):
        self._insert_file("f_grp", user_id="1")
        acl = {"read": {"group_ids": ["g1"], "user_ids": ["9"]}}
        with mock_webui_user(id="1", role="user"):
            r = self._update_acl("f_grp", acl)
        assert r.status_code == 200, r.text
        assert self.files.get_file_by_id("f_grp").access_control == acl

    def test_non_owner_cannot_edit_acl_even_if_readable(self):
        # read 可(全体公開)でも ACL 編集は owner/admin のみ — 404 でなく 403
        self._insert_file(
            "f_shared", user_id="1", access_control={"read": {"public": True}}
        )
        with mock_webui_user(id="2", role="user"):
            r = self._update_acl("f_shared", None)
        assert r.status_code == 403, r.text
        assert self.files.get_file_by_id("f_shared").access_control == {
            "read": {"public": True}
        }

    def test_admin_can_edit_others_acl(self):
        self._insert_file("f_adm", user_id="2")
        with mock_webui_user(id="1", role="admin"):
            r = self._update_acl("f_adm", {"read": {"public": True}})
        assert r.status_code == 200, r.text
        assert self.files.get_file_by_id("f_adm").access_control == {
            "read": {"public": True}
        }

    def test_invalid_acl_shapes_rejected(self):
        self._insert_file("f_bad", user_id="1")
        invalid_shapes = [
            {},  # read キー必須
            {"write": {"user_ids": []}},  # read 以外のトップキー
            {"read": {"public": True}, "write": {}},  # 余分なトップキー
            {"read": {"foo": []}},  # read 内の想定外キー
            {"read": {"public": "yes"}},  # public が bool でない
            {"read": {"group_ids": "g1"}},  # list でない
            {"read": {"user_ids": [1, 2]}},  # str でない要素
        ]
        with mock_webui_user(id="1", role="user"):
            for shape in invalid_shapes:
                r = self._update_acl("f_bad", shape)
                assert r.status_code == 400, f"{shape}: {r.text}"
        # 一度も書き換わっていない
        assert self.files.get_file_by_id("f_bad").access_control is None

    def test_acl_update_not_found(self):
        with mock_webui_user(id="1", role="user"):
            r = self._update_acl("f_none", None)
        assert r.status_code == 404, r.text

    # ---- accessed_at ----

    def test_accessed_at_touched_by_content_fetch(self):
        self._insert_file("f_touch", user_id="1", data={"content": "hello"})
        self._set_times("f_touch", accessed_at=100)
        with mock_webui_user(id="1"):
            r = self.fast_api_client.get(self.create_url("/f_touch/data/content"))
        assert r.status_code == 200, r.text
        assert r.json() == {"content": "hello"}
        assert self.files.get_file_by_id("f_touch").accessed_at > 100

    def test_accessed_at_not_touched_by_listing(self):
        self._insert_file("f_still", user_id="1")
        self._set_times("f_still", accessed_at=100)
        with mock_webui_user(id="1"):
            r = self.fast_api_client.get(self.create_url("/list"))
            assert r.status_code == 200, r.text
            # メタ取得(GET /{id})でもタッチしない
            r = self.fast_api_client.get(self.create_url("/f_still"))
            assert r.status_code == 200, r.text
        assert self.files.get_file_by_id("f_still").accessed_at == 100

    def test_accessed_at_set_on_insert(self):
        file = self._insert_file("f_new", user_id="1")
        assert file.accessed_at == file.created_at

    # ---- GET /{id}/chats(file を参照するチャット導線) ----

    def _insert_chat(self, user_id, title, file_id=None):
        from open_webui.models.chats import ChatForm, Chats

        chat = Chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={
                    "title": title,
                    "history": {"currentId": "m1", "messages": {}},
                    # 実際の添付は message.files / chat.files 双方にあり得る —
                    # 検出は JSON 全文 LIKE なのでトップレベル files で代表させる
                    "files": [{"id": file_id, "type": "file"}] if file_id else [],
                }
            ),
        )
        assert chat is not None
        return chat

    def test_file_chats_lists_own_referencing_chats(self):
        self._insert_file("f_ref-0000-uuid", user_id="1")
        c1 = self._insert_chat("1", "uses file", file_id="f_ref-0000-uuid")
        self._insert_chat("1", "unrelated chat")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_ref-0000-uuid/chats"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert [c["id"] for c in body] == [c1.id]
        assert body[0]["title"] == "uses file"
        assert body[0]["user_id"] == "1"
        assert "updated_at" in body[0]

    def test_file_chats_non_admin_sees_only_own_chats(self):
        # 全体公開 file は他人も読めるが、返るチャットは本人の分だけ
        self._insert_file(
            "f_pub-0000-uuid", user_id="1", access_control={"read": {"public": True}}
        )
        self._insert_chat("1", "owner's chat", file_id="f_pub-0000-uuid")
        mine = self._insert_chat("2", "my chat", file_id="f_pub-0000-uuid")
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_pub-0000-uuid/chats"))
        assert r.status_code == 200, r.text
        assert [c["id"] for c in r.json()] == [mine.id]

    def test_file_chats_admin_sees_all_users_chats(self):
        # admin は全ユーザー分(他人のチャットは FE が /s/{id} に振り分ける)
        self._insert_file("f_adm-0000-uuid", user_id="2")
        theirs = self._insert_chat("2", "their chat", file_id="f_adm-0000-uuid")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/f_adm-0000-uuid/chats"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert [c["id"] for c in body] == [theirs.id]
        assert body[0]["user_id"] == "2"

    def test_file_chats_requires_file_read_access(self):
        # 非公開 file は他人からは 404(存在も明かさない)
        self._insert_file("f_prv-0000-uuid", user_id="1")
        self._insert_chat("2", "my chat", file_id="f_prv-0000-uuid")
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_prv-0000-uuid/chats"))
        assert r.status_code == 404, r.text

    def test_file_chats_not_found(self):
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_missing/chats"))
        assert r.status_code == 404, r.text
