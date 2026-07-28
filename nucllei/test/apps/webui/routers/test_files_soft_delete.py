"""file 削除の soft delete / restore / purge(#94)の router 回帰。

- DELETE /files/{id}: soft delete(行と実体を残し deleted_at/deleted_by を立てる。冪等)
- POST /files/{id}/restore: admin 専有。deleted → active(purged/active は 400)
- POST /files/{id}/purge: admin 専有。実体削除 + 実体由来フィールドの破棄(active は 400)
- deleted の可視性: ユーザー一覧/検索から消える、/list の deleted 三値、
  GET /{id} の縮小 marker、content 配信の admin 限定
"""

from types import SimpleNamespace

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict


class TestFilesSoftDelete(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.files import Files

        cls.files = Files

    def _insert_file(
        self,
        fid,
        user_id="1",
        access_control=None,
        data=None,
        path=None,
        hash=None,
    ):
        from nucllei.models.files import FileForm

        file = self.files.insert_new_file(
            user_id,
            FileForm(
                id=fid,
                hash=hash,
                filename=f"{fid}.txt",
                path=path or f"/tmp/{fid}.txt",
                data=data or {},
                meta={"name": f"{fid}.txt", "size": 5},
                access_control=access_control,
            ),
        )
        assert file is not None
        return file

    def _mock_upload_dir(self, monkeypatch, tmp_path):
        from nucllei.storage import provider

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        monkeypatch.setattr(provider, "UPLOAD_DIR", str(upload_dir))
        return upload_dir

    def _get_raw(self, fid):
        return self.files.get_file_by_id(fid, include_deleted=True)

    # ---- DELETE /{id}(soft delete) ----

    def test_delete_keeps_row_and_storage(self, monkeypatch, tmp_path):
        upload_dir = self._mock_upload_dir(monkeypatch, tmp_path)
        real = upload_dir / "f_del.txt"
        real.write_bytes(b"hello")
        self._insert_file("f_del", user_id="1", path=str(real))

        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_del"))
        assert r.status_code == 200, r.text

        # 行は残り deleted_at/deleted_by が立つ。実体は消えない
        raw = self._get_raw("f_del")
        assert raw is not None
        assert raw.deleted_at is not None
        assert raw.deleted_by == "1"
        assert raw.purged_at is None
        assert real.exists()
        # 既定取得(tool 経路と同じ)からは消える
        assert self.files.get_file_by_id("f_del") is None

    def test_delete_is_idempotent(self):
        self._insert_file("f_re", user_id="2")
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_re"))
        assert r.status_code == 200, r.text
        first = self._get_raw("f_re")

        # admin による再実行も no-op 成功 — 元の deleted_at/deleted_by を上書きしない
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url("/f_re"))
        assert r.status_code == 200, r.text
        raw = self._get_raw("f_re")
        assert raw.deleted_at == first.deleted_at
        assert raw.deleted_by == "2"

    def test_delete_not_found(self):
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_missing"))
        assert r.status_code == 404, r.text

    def test_delete_non_owner_prohibited(self):
        self._insert_file("f_other", user_id="1")
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_other"))
        assert r.status_code == 404, r.text
        assert self._get_raw("f_other").deleted_at is None

    def test_delete_all_endpoint_removed(self):
        # DELETE /all は廃止 — /{id} に落ちて id="all" の 404 になる
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url("/all"))
        assert r.status_code in (404, 405), r.text

    # ---- deleted の可視性(一覧・検索) ----

    def test_deleted_hidden_from_listing_and_search(self):
        self._insert_file("f_live", user_id="1")
        self._insert_file("f_gone", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_gone"))
            assert r.status_code == 200, r.text

            r = self.fast_api_client.get(self.create_url(""))
            assert r.status_code == 200, r.text
            assert [f["id"] for f in r.json()] == ["f_live"]

            r = self.fast_api_client.get(
                self.create_url("/search", {"filename": "f_gone*"})
            )
            assert r.status_code == 404, r.text

        # admin の全件一覧(GET /)からも消える
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url(""))
        assert [f["id"] for f in r.json()] == ["f_live"]

    def test_list_deleted_param_admin(self):
        self._insert_file("f_a", user_id="1")
        self._insert_file("f_b", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_b"))
            assert r.status_code == 200, r.text

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/list"))
            assert {i["id"] for i in r.json()["items"]} == {"f_a"}

            r = self.fast_api_client.get(self.create_url("/list", {"deleted": "only"}))
            assert {i["id"] for i in r.json()["items"]} == {"f_b"}

            r = self.fast_api_client.get(
                self.create_url("/list", {"deleted": "include"})
            )
            assert {i["id"] for i in r.json()["items"]} == {"f_a", "f_b"}

            r = self.fast_api_client.get(self.create_url("/list", {"deleted": "bogus"}))
            assert r.status_code == 400, r.text

    def test_list_deleted_param_forbidden_for_non_admin(self):
        with mock_webui_user(id="1", role="user"):
            for value in ("only", "include"):
                r = self.fast_api_client.get(
                    self.create_url("/list", {"deleted": value})
                )
                assert r.status_code == 403, r.text
            # exclude(既定と同値)は許可
            r = self.fast_api_client.get(self.create_url("/list"))
            assert r.status_code == 200, r.text

    def test_uploaders_exclude_deleted(self):
        from nucllei.models.users import Users

        Users.insert_new_user(id="u_a", name="A", email="ua@x", role="user")
        Users.insert_new_user(id="u_b", name="B", email="ub@x", role="user")
        self._insert_file("f_ua1", user_id="u_a")
        self._insert_file("f_ua2", user_id="u_a")
        self._insert_file("f_ub1", user_id="u_b")

        # u_a の 1 件と u_b の唯一の file を削除
        with mock_webui_user(id="u_a", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_ua2"))
            assert r.status_code == 200, r.text
        with mock_webui_user(id="u_b", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_ub1"))
            assert r.status_code == 200, r.text

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/uploaders"))
        assert r.status_code == 200, r.text
        uploaders = {u["id"]: u for u in r.json()}
        # 全 file を削除した u_b は一覧から消え、u_a の件数は active 分のみ
        assert set(uploaders.keys()) == {"u_a"}
        assert uploaders["u_a"]["file_count"] == 1

    # ---- GET /{id}(縮小 marker / admin 全メタ) ----

    def test_get_deleted_owner_reduced_marker(self):
        # marker は owner 限定の {id, deleted_at} のみ(filename も返さない —
        # 表示名は FE がチャット JSON snapshot に持つ)
        self._insert_file("f_mark", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_mark"))
            assert r.status_code == 200, r.text
            r = self.fast_api_client.get(self.create_url("/f_mark"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == {"id", "deleted_at"}
        assert body["id"] == "f_mark"
        assert body["deleted_at"] is not None

    def test_get_deleted_shared_reader_404(self):
        # 削除前アクセス権(public マーカー)があっても owner 以外は 404 —
        # 凍結 ACL の評価は削除後にグループへ加わった者への開示になるため marker は owner 限定
        self._insert_file(
            "f_pub", user_id="1", access_control={"read": {"public": True}}
        )
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_pub"))
            assert r.status_code == 200, r.text
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_pub"))
        assert r.status_code == 404, r.text

    def test_get_deleted_stranger_404(self):
        self._insert_file("f_prv", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_prv"))
            assert r.status_code == 200, r.text
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_prv"))
        assert r.status_code == 404, r.text

    def test_get_deleted_admin_full_meta(self):
        self._insert_file("f_full", user_id="2")
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_full"))
            assert r.status_code == 200, r.text
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/f_full"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "f_full"
        assert body["user_id"] == "2"
        assert body["deleted_at"] is not None
        assert body["deleted_by"] == "2"
        assert body["purged_at"] is None
        assert body["meta"]["size"] == 5

    def test_get_purged_marker_and_admin_meta(self, monkeypatch, tmp_path):
        upload_dir = self._mock_upload_dir(monkeypatch, tmp_path)
        real = upload_dir / "f_pgm.txt"
        real.write_bytes(b"hello")
        self._insert_file(
            "f_pgm",
            user_id="2",
            path=str(real),
            access_control={"read": {"public": True}},
        )
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url("/f_pgm"))
            assert r.status_code == 200, r.text
            r = self.fast_api_client.post(self.create_url("/f_pgm/purge"))
            assert r.status_code == 200, r.text

        # owner: 縮小 marker のみ — purged_at/purged_by キーは載らない(purge 時点を漏らさない)
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_pgm"))
        assert r.status_code == 200, r.text
        assert set(r.json().keys()) == {"id", "deleted_at"}

        # admin: purge 監査情報つきの全メタ
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/f_pgm"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["purged_at"] is not None
        assert body["purged_by"] == "1"

        # 旧 public 読者: 404(marker は owner 限定)
        with mock_webui_user(id="3", role="user"):
            r = self.fast_api_client.get(self.create_url("/f_pgm"))
        assert r.status_code == 404, r.text

    # ---- GET /{id}/chats(deleted は admin の監査導線のみ) ----

    def _insert_chat(self, user_id, title, file_id):
        from nucllei.models.chats import ChatForm, Chats

        chat = Chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={
                    "title": title,
                    "history": {"currentId": "m1", "messages": {}},
                    # 検出は JSON 全文 LIKE なのでトップレベル files で代表させる
                    # (test_files_management.py の _insert_chat と同じ流儀)
                    "files": [{"id": file_id, "type": "file"}],
                }
            ),
        )
        assert chat is not None
        return chat

    def test_file_chats_deleted_admin_only(self):
        self._insert_file("f_fc", user_id="1")
        chat = self._insert_chat("1", "uses file", "f_fc")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_fc"))
            assert r.status_code == 200, r.text
            # owner でも非 admin は 404
            r = self.fast_api_client.get(self.create_url("/f_fc/chats"))
            assert r.status_code == 404, r.text
        # admin は監査導線(file 行 → 参照チャット)として見える
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/f_fc/chats"))
        assert r.status_code == 200, r.text
        assert [c["id"] for c in r.json()] == [chat.id]

    # ---- content 配信(deleted は admin のみ / purged は全員 404) ----

    def test_content_deleted_admin_only(self, monkeypatch, tmp_path):
        upload_dir = self._mock_upload_dir(monkeypatch, tmp_path)
        real = upload_dir / "f_cont.txt"
        real.write_bytes(b"hello")
        self._insert_file(
            "f_cont", user_id="1", path=str(real), data={"content": "hello"}
        )
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_cont"))
            assert r.status_code == 200, r.text

            # owner でも非 admin は 404
            r = self.fast_api_client.get(self.create_url("/f_cont/content"))
            assert r.status_code == 404, r.text
            r = self.fast_api_client.get(self.create_url("/f_cont/data/content"))
            assert r.status_code == 404, r.text
            r = self.fast_api_client.get(self.create_url("/f_cont/content/f_cont.txt"))
            assert r.status_code == 404, r.text
            r = self.fast_api_client.get(self.create_url("/f_cont/content/html"))
            assert r.status_code == 404, r.text

        # admin は実体がある限り閲覧できる
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.get(self.create_url("/f_cont/content"))
            assert r.status_code == 200, r.text
            assert r.content == b"hello"
            r = self.fast_api_client.get(self.create_url("/f_cont/data/content"))
            assert r.status_code == 200, r.text
            assert r.json() == {"content": "hello"}
            r = self.fast_api_client.get(self.create_url("/f_cont/content/f_cont.txt"))
            assert r.status_code == 200, r.text
            assert r.content == b"hello"

    def test_content_purged_404_for_everyone(self, monkeypatch, tmp_path):
        upload_dir = self._mock_upload_dir(monkeypatch, tmp_path)
        real = upload_dir / "f_pg.txt"
        real.write_bytes(b"hello")
        self._insert_file("f_pg", user_id="1", path=str(real))
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url("/f_pg"))
            assert r.status_code == 200, r.text
            r = self.fast_api_client.post(self.create_url("/f_pg/purge"))
            assert r.status_code == 200, r.text

            for path in (
                "/f_pg/content",
                "/f_pg/content/html",
                "/f_pg/content/f_pg.txt",
                "/f_pg/data/content",
            ):
                r = self.fast_api_client.get(self.create_url(path))
                assert r.status_code == 404, f"{path}: {r.text}"

    def test_update_endpoints_404_on_deleted(self):
        self._insert_file("f_upd", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_upd"))
            assert r.status_code == 200, r.text
        # 削除済み行への書き込み経路は admin にも無い
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(
                self.create_url("/f_upd/data/content/update"), json={"content": "x"}
            )
            assert r.status_code == 404, r.text
            r = self.fast_api_client.post(
                self.create_url("/f_upd/access_control/update"),
                json={"access_control": None},
            )
            assert r.status_code == 404, r.text
            r = self.fast_api_client.post(self.create_url("/f_upd/process"))
            assert r.status_code == 404, r.text

    # ---- POST /{id}/restore ----

    def test_restore_admin_only(self):
        self._insert_file("f_res", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_res"))
            assert r.status_code == 200, r.text
        # get_admin_user の実ゲートを通す(mock_webui_user は素通りするため strict)
        with mock_webui_user_strict(id="1", role="user"):
            r = self.fast_api_client.post(self.create_url("/f_res/restore"))
        assert r.status_code == 401, r.text
        assert self._get_raw("f_res").deleted_at is not None

    def test_restore_success(self):
        self._insert_file("f_back", user_id="2")
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_back"))
            assert r.status_code == 200, r.text
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url("/f_back/restore"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted_at"] is None
        assert body["deleted_by"] is None
        # 既定取得(ユーザー導線・tool 経路)に戻る
        assert self.files.get_file_by_id("f_back") is not None

    def test_restore_active_400(self):
        self._insert_file("f_act", user_id="1")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url("/f_act/restore"))
        assert r.status_code == 400, r.text

    def test_restore_purged_400(self, monkeypatch, tmp_path):
        upload_dir = self._mock_upload_dir(monkeypatch, tmp_path)
        real = upload_dir / "f_rp.txt"
        real.write_bytes(b"hello")
        self._insert_file("f_rp", user_id="1", path=str(real))
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url("/f_rp"))
            assert r.status_code == 200, r.text
            r = self.fast_api_client.post(self.create_url("/f_rp/purge"))
            assert r.status_code == 200, r.text
            r = self.fast_api_client.post(self.create_url("/f_rp/restore"))
        assert r.status_code == 400, r.text

    def test_restore_not_found(self):
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url("/f_missing/restore"))
        assert r.status_code == 404, r.text

    # ---- POST /{id}/purge ----

    def test_purge_admin_only(self):
        self._insert_file("f_pa", user_id="1")
        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_pa"))
            assert r.status_code == 200, r.text
        with mock_webui_user_strict(id="1", role="user"):
            r = self.fast_api_client.post(self.create_url("/f_pa/purge"))
        assert r.status_code == 401, r.text

    def test_purge_active_400(self):
        # active(未削除)は先に削除が必要
        self._insert_file("f_pact", user_id="1")
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url("/f_pact/purge"))
        assert r.status_code == 400, r.text
        assert self._get_raw("f_pact").purged_at is None

    def test_purge_success_drops_content_keeps_audit(self, monkeypatch, tmp_path):
        upload_dir = self._mock_upload_dir(monkeypatch, tmp_path)
        real = upload_dir / "f_ok.txt"
        real.write_bytes(b"hello")
        self._insert_file(
            "f_ok",
            user_id="2",
            path=str(real),
            data={"content": "extracted"},
            hash="deadbeef",
            access_control={"read": {"public": True}},
        )
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_ok"))
            assert r.status_code == 200, r.text
        assert real.exists()  # soft delete では実体は残る

        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url("/f_ok/purge"))
        assert r.status_code == 200, r.text
        assert not real.exists()  # purge で実体が消える

        raw = self._get_raw("f_ok")
        # 実体由来のフィールドは落ちる(data は抽出テキスト = 実体由来なので残さない)
        assert raw.filename is None
        assert raw.path is None
        assert raw.data is None
        assert raw.access_control is None
        assert raw.meta == {"size": 5}
        # 監査用に残るもの
        assert raw.id == "f_ok"
        assert raw.user_id == "2"
        assert raw.hash == "deadbeef"
        assert raw.deleted_at is not None
        assert raw.deleted_by == "2"
        assert raw.purged_at is not None
        assert raw.purged_by == "1"

    def test_purge_already_purged_400(self, monkeypatch, tmp_path):
        upload_dir = self._mock_upload_dir(monkeypatch, tmp_path)
        real = upload_dir / "f_pp.txt"
        real.write_bytes(b"hello")
        self._insert_file("f_pp", user_id="1", path=str(real))
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.delete(self.create_url("/f_pp"))
            assert r.status_code == 200, r.text
            r = self.fast_api_client.post(self.create_url("/f_pp/purge"))
            assert r.status_code == 200, r.text
            r = self.fast_api_client.post(self.create_url("/f_pp/purge"))
        assert r.status_code == 400, r.text

    def test_purge_not_found(self):
        with mock_webui_user(id="1", role="admin"):
            r = self.fast_api_client.post(self.create_url("/f_missing/purge"))
        assert r.status_code == 404, r.text

    # ---- has_read_access_to_file(tool 経路の遮断) ----

    def test_has_read_access_false_on_deleted_even_for_owner(self):
        from nucllei.utils.access_control import has_read_access_to_file

        self._insert_file(
            "f_ac", user_id="1", access_control={"read": {"public": True}}
        )
        owner = SimpleNamespace(id="1", role="user")
        other = SimpleNamespace(id="2", role="user")
        assert has_read_access_to_file("f_ac", owner) is True
        assert has_read_access_to_file("f_ac", other) is True

        with mock_webui_user(id="1", role="user"):
            r = self.fast_api_client.delete(self.create_url("/f_ac"))
            assert r.status_code == 200, r.text

        # get_file_by_id の既定除外により deleted は owner でも False
        assert has_read_access_to_file("f_ac", owner) is False
        assert has_read_access_to_file("f_ac", other) is False
