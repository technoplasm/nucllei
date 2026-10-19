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
        from nucllei.models.users import Users
        from nucllei.models.files import Files, FileForm

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
        from nucllei.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("f-alice", self.alice) is True

    def test_other_user_cannot_read(self):
        from nucllei.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("f-alice", self.bob) is False

    def test_admin_can_read(self):
        from nucllei.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("f-alice", self.admin) is True

    def test_missing_file_denied(self):
        from nucllei.utils.access_control import has_read_access_to_file

        assert has_read_access_to_file("does-not-exist", self.bob) is False
        assert has_read_access_to_file(None, self.bob) is False

    def test_file_level_access_control_share(self):
        from nucllei.utils.access_control import has_read_access_to_file

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
        from nucllei.utils.access_control import has_read_access_to_collection

        # `file-<id>` コレクションは対応 file の権限へ委譲
        assert has_read_access_to_collection("file-f-alice", self.alice) is True
        assert has_read_access_to_collection("file-f-alice", self.bob) is False

    def test_unknown_collection_denied(self):
        # KB は撤去済み(#52/#41)— file- 形式以外のコレクションは常に非公開扱い
        from nucllei.utils.access_control import has_read_access_to_collection

        assert has_read_access_to_collection("kb-nonexistent", self.bob) is False

    # ---- filter_files_by_user_access(チャット files 配列) ----

    def test_filter_drops_foreign_file_ref(self):
        from nucllei.utils.middleware import filter_files_by_user_access

        # bob が他人(alice)の file_id を注入 → 除外される
        files = [{"id": "f-alice"}, {"type": "text", "content": "inline"}]
        out = filter_files_by_user_access(files, self.bob)
        assert out == [{"type": "text", "content": "inline"}]

    def test_filter_keeps_own_file_ref(self):
        from nucllei.utils.middleware import filter_files_by_user_access

        files = [{"id": "f-alice"}]
        assert filter_files_by_user_access(files, self.alice) == files

    def test_filter_drops_foreign_collection_ref(self):
        from nucllei.utils.middleware import filter_files_by_user_access

        files = [
            {"type": "collection", "id": "file-f-alice"},
            {"collection_name": "file-f-alice"},
        ]
        assert filter_files_by_user_access(files, self.bob) == []

    def test_filter_server_generated_entries_kept(self):
        from nucllei.utils.middleware import filter_files_by_user_access

        # docs 等のインライン内容(id/collection を持たずこのリクエスト内で完結)は
        # DB 横断解決を伴わないので通す(#40 の IDOR フィルタは file_id/collection の
        # 所有権だけを見る)。
        files = [
            {"docs": [{"content": "c", "metadata": {}}], "name": "q"},
            {"name": "inline", "content": "c"},
        ]
        assert filter_files_by_user_access(files, self.bob) == files

    def test_filter_admin_bypass(self):
        from nucllei.utils.middleware import filter_files_by_user_access

        files = [{"id": "f-alice"}]
        assert filter_files_by_user_access(files, self.admin) == files

    # ---- process_chat_payload: inlet が見る files ----

    def test_inlet_sees_only_readable_files_in_body_and_metadata(self, monkeypatch):
        """他人の file_id を files に入れて送っても、Filter の inlet が見る
        body["files"] / body["metadata"]["files"] / __metadata__["files"] のどれにも
        その id が無い(main.py は絞る前の一覧を metadata["files"] に入れて渡す —
        inlet 前に絞った一覧へ揃っていなければならない)。自分のファイルは残る。"""
        import asyncio
        import types

        from starlette.requests import Request

        from nucllei.main import app
        from nucllei.utils import middleware

        self.Files.insert_new_file(
            "bob",
            self.FileForm(
                id="f-bob",
                filename="b.txt",
                path="/data/b.txt",
                meta={},
                data={"content": "bob-own"},
            ),
        )

        seen = {}

        class SpyFilter:
            def inlet(self, body, __metadata__=None):
                seen["body_files"] = body.get("files")
                seen["body_metadata_files"] = (body.get("metadata") or {}).get("files")
                seen["dunder_metadata_files"] = (__metadata__ or {}).get("files")
                return body

        monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda m: ["spy"])
        monkeypatch.setattr(
            middleware.Functions,
            "get_function_by_id",
            lambda fid: types.SimpleNamespace(id=fid),
        )

        model = {"id": "idor", "name": "idor", "owned_by": "openai", "info": {}}
        saved_models = getattr(app.state, "MODELS", None)
        saved_spy = app.state.FUNCTIONS.get("spy")
        app.state.MODELS = {"idor": model}
        app.state.FUNCTIONS["spy"] = SpyFilter()
        try:
            request = Request(
                scope={
                    "type": "http",
                    "app": app,
                    "method": "POST",
                    "path": "/api/chat/completions",
                    "headers": [],
                    "query_string": b"",
                }
            )
            files = [
                {"type": "file", "id": "f-alice"},
                {"type": "file", "id": "f-bob"},
            ]
            form_data = {
                "model": "idor",
                "stream": True,
                "messages": [{"role": "user", "content": "読んで"}],
                "files": files,
            }
            # main.py chat_completion と同じ形: 絞る前の一覧を metadata にも入れる
            metadata = {"user_id": "bob", "model": model, "files": files}
            form_data["metadata"] = metadata
            asyncio.run(
                middleware.process_chat_payload(
                    request, form_data, self.bob, metadata, model, None
                )
            )
        finally:
            app.state.MODELS = saved_models
            if saved_spy is None:
                app.state.FUNCTIONS.pop("spy", None)
            else:
                app.state.FUNCTIONS["spy"] = saved_spy

        for key in ("body_files", "body_metadata_files", "dunder_metadata_files"):
            ids = [f.get("id") for f in seen[key] or []]
            assert "f-alice" not in ids, key
            assert ids == ["f-bob"], key
