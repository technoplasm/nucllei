"""ファイル/KB 参照の IDOR 回帰テスト(issue #40、GHSA-vjqm/vrhc/r472/4g37)。

チャット添付・model meta.knowledge・retrieval 経由で渡る file_id / collection は
クライアント制御。要求ユーザーが読取権限を持たない参照を解決してはならない。

- has_read_access_to_file / has_read_access_to_collection: file_id/collection 単位の突合
- filter_files_by_user_access: チャットの files 配列から不可視参照を除外
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestFileAccessIDOR(AbstractPostgresTest):
    BASE_PATH = "/api/v1/files"

    def setup_method(self):
        super().setup_method()
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

        # alice が所有する非公開ファイル
        self.Files.insert_new_file(
            "alice",
            FileForm(
                id="f-alice",
                filename="a.txt",
                path="/data/a.txt",
                meta={},
                data={"content": "alice-secret"},
            ),
        )

    # ---- has_read_access_to_file ----

    def test_owner_can_read(self):
        from open_webui.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("f-alice", self.alice) is True

    def test_other_user_cannot_read(self):
        from open_webui.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("f-alice", self.bob) is False

    def test_admin_can_read(self):
        from open_webui.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("f-alice", self.admin) is True

    def test_missing_file_denied(self):
        from open_webui.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("does-not-exist", self.bob) is False
        assert has_read_access_to_file(None, self.bob) is False

    def test_file_level_access_control_share(self):
        from open_webui.utils.access_control import has_read_access_to_file

        self.Files.insert_new_file(
            "alice",
            self.FileForm(
                id="f-shared",
                filename="s.txt",
                path="/data/s.txt",
                meta={},
                data={},
                access_control={"read": {"user_ids": ["bob"], "group_ids": []}},
            ),
        )
        assert has_read_access_to_file("f-shared", self.bob) is True
        # 共有されていない外科医(=carol 相当)には非公開のまま
        carol = self.Users.insert_new_user(
            id="carol", name="carol", email="carol@h.example", role="user"
        )
        assert has_read_access_to_file("f-shared", carol) is False

    # ---- has_read_access_to_collection ----

    def test_collection_file_prefix_delegates_to_file(self):
        from open_webui.utils.access_control import has_read_access_to_collection

        # `file-<id>` コレクションは対応 file の権限へ委譲
        assert has_read_access_to_collection("file-f-alice", self.alice) is True
        assert has_read_access_to_collection("file-f-alice", self.bob) is False

    def test_unknown_collection_denied(self):
        # KB は撤去済み(#52/#41)— file- 形式以外のコレクションは常に非公開扱い
        from open_webui.utils.access_control import has_read_access_to_collection

        assert has_read_access_to_collection("kb-nonexistent", self.bob) is False

    # ---- filter_files_by_user_access(チャット files 配列) ----

    def test_filter_drops_foreign_file_ref(self):
        from open_webui.utils.middleware import filter_files_by_user_access

        # bob が他人(alice)の file_id を注入 → 除外される
        files = [{"id": "f-alice"}, {"type": "text", "content": "inline"}]
        out = filter_files_by_user_access(files, self.bob)
        assert out == [{"type": "text", "content": "inline"}]

    def test_filter_keeps_own_file_ref(self):
        from open_webui.utils.middleware import filter_files_by_user_access

        files = [{"id": "f-alice"}]
        assert filter_files_by_user_access(files, self.alice) == files

    def test_filter_drops_foreign_collection_ref(self):
        from open_webui.utils.middleware import filter_files_by_user_access

        files = [
            {"type": "collection", "id": "file-f-alice"},
            {"collection_name": "file-f-alice"},
        ]
        assert filter_files_by_user_access(files, self.bob) == []

    def test_filter_server_generated_entries_kept(self):
        from open_webui.utils.middleware import filter_files_by_user_access

        # web_search / docs はこのリクエスト内でサーバー生成(DB 横断解決なし)→ 通す
        files = [
            {"type": "web_search", "name": "q", "urls": ["http://x"]},
            {"docs": [{"content": "c", "metadata": {}}], "name": "q"},
        ]
        assert filter_files_by_user_access(files, self.bob) == files

    def test_filter_admin_bypass(self):
        from open_webui.utils.middleware import filter_files_by_user_access

        files = [{"id": "f-alice"}]
        assert filter_files_by_user_access(files, self.admin) == files
