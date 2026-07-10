"""ユーザー追加 meta(仮想カラム)の回帰テスト(issue #55、permissions.md G3)。

- ADDITIONAL_USER_META で宣言したキーだけを admin が編集できる(未宣言キーは無視・温存)。
- 空値 = キー削除(マーカー無し = 外部システム側で未認可と解釈)。
- 本人による info 書き込み endpoint は撤去済み(G3: integrity 必須のため admin 専有)。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user, mock_webui_user_strict

FIELDS = [{"key": "carte_user_id", "label": "カルテ利用者ID"}]


class TestUserMeta(AbstractPostgresTest):
    BASE_PATH = "/api/v1/users"

    def setup_method(self):
        super().setup_method()
        from open_webui.main import app
        from open_webui.models.users import Users

        self.Users = Users
        app.state.config.ADDITIONAL_USER_META = FIELDS

        self.alice = Users.insert_new_user(
            id="alice", name="alice", email="alice@h.example", role="user"
        )

    def teardown_method(self):
        from open_webui.main import app

        app.state.config.ADDITIONAL_USER_META = []
        super().teardown_method()

    def _update_form(self, **info):
        return {
            "name": "alice",
            "email": "alice@h.example",
            "profile_image_url": "/user.png",
            "info": info,
        }

    # ---- meta fields 配布 ----

    def test_meta_fields_admin_only(self):
        with mock_webui_user(id="admin", role="admin"):
            response = self.fast_api_client.get(self.create_url("/meta/fields"))
        assert response.status_code == 200
        assert response.json() == FIELDS

        with mock_webui_user_strict(id="alice", role="user"):
            response = self.fast_api_client.get(self.create_url("/meta/fields"))
        assert response.status_code == 401

    # ---- admin による編集 ----

    def test_admin_sets_declared_key(self):
        with mock_webui_user(id="admin", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/alice/update"),
                json=self._update_form(carte_user_id="C-123"),
            )
        assert response.status_code == 200
        assert self.Users.get_user_by_id("alice").info == {"carte_user_id": "C-123"}

    def test_empty_value_removes_key(self):
        self.Users.update_user_by_id("alice", {"info": {"carte_user_id": "C-123"}})

        with mock_webui_user(id="admin", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/alice/update"),
                json=self._update_form(carte_user_id="  "),
            )
        assert response.status_code == 200
        assert self.Users.get_user_by_id("alice").info == {}

    def test_undeclared_key_ignored_and_preserved(self):
        # 既存の未宣言キーは温存(opaque)、リクエスト中の未宣言キーは無視
        self.Users.update_user_by_id("alice", {"info": {"legacy": "keep"}})

        with mock_webui_user(id="admin", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/alice/update"),
                json=self._update_form(carte_user_id="C-9", injected="evil"),
            )
        assert response.status_code == 200
        assert self.Users.get_user_by_id("alice").info == {
            "legacy": "keep",
            "carte_user_id": "C-9",
        }

    def test_update_without_info_leaves_info_untouched(self):
        self.Users.update_user_by_id("alice", {"info": {"carte_user_id": "C-123"}})

        form = self._update_form()
        del form["info"]
        with mock_webui_user(id="admin", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/alice/update"), json=form
            )
        assert response.status_code == 200
        assert self.Users.get_user_by_id("alice").info == {"carte_user_id": "C-123"}

    def test_non_admin_cannot_update(self):
        with mock_webui_user_strict(id="alice", role="user"):
            response = self.fast_api_client.post(
                self.create_url("/alice/update"),
                json=self._update_form(carte_user_id="self-grant"),
            )
        assert response.status_code == 401
        assert self.Users.get_user_by_id("alice").info is None

    # ---- G3: 本人書き込み endpoint の撤去 ----

    def test_session_user_info_endpoints_removed(self):
        with mock_webui_user_strict(id="alice", role="user"):
            update = self.fast_api_client.post(
                self.create_url("/user/info/update"),
                json={"carte_user_id": "self-grant"},
            )
            read = self.fast_api_client.get(self.create_url("/user/info"))
        assert update.status_code in (404, 405)
        # GET は SPA catch-all(index.html)に落ちるため「JSON API として不在」を確認する
        assert read.status_code in (404, 405) or "text/html" in read.headers.get(
            "content-type", ""
        )
        assert self.Users.get_user_by_id("alice").info is None
