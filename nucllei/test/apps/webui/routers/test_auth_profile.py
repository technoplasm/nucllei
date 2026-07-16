"""llens 標準認証プロファイル(院内完結)の回帰テスト(issue #7)。

_memos/auth-profile.md に定義した既定値・ブートストラップ挙動を固定する:
- 初回 signup したユーザーが admin になる
- 初回登録の瞬間に ENABLE_SIGNUP が自動で False になる(以後は事務が発行)
- 以後の signup は 403
- 管理者は /auths/add でユーザーを発行できる
- 外部 IdP(LDAP / OAuth signup)は既定で無効
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestLlensAuthProfile(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    def setup_method(self):
        super().setup_method()
        from nucllei.main import app

        # signup テストが ENABLE_SIGNUP を永続的に落とすため、
        # 各テストはブートストラップ前の状態(signup 可)から始める
        app.state.config.ENABLE_SIGNUP = True

    def test_external_idp_disabled_by_default(self):
        from nucllei.config import ENABLE_OAUTH_SIGNUP
        from nucllei.main import app

        assert app.state.config.ENABLE_LDAP is False
        # OAuth は app.state.config ではなく oauth マネージャが直接参照する
        assert ENABLE_OAUTH_SIGNUP.value is False
        assert app.state.config.ENABLE_LOGIN_FORM is True
        assert app.state.config.DEFAULT_USER_ROLE == "pending"

    def test_bootstrap_first_user_becomes_admin_and_signup_closes(self):
        from nucllei.main import app

        response = self.fast_api_client.post(
            self.create_url("/signup"),
            json={
                "name": "事務 管理者",
                "email": "admin@hospital.example",
                "password": "password",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["role"] == "admin"

        # 初回登録と同時に signup は自動で閉じる
        assert app.state.config.ENABLE_SIGNUP is False

        # 以後の signup は拒否される(事務による発行のみ)
        response = self.fast_api_client.post(
            self.create_url("/signup"),
            json={
                "name": "部外者",
                "email": "outsider@hospital.example",
                "password": "password",
            },
        )
        assert response.status_code == 403

    def test_signup_after_reopen_gets_pending_role(self):
        from nucllei.main import app

        # ブートストラップ(admin 作成)
        response = self.fast_api_client.post(
            self.create_url("/signup"),
            json={
                "name": "事務 管理者",
                "email": "admin@hospital.example",
                "password": "password",
            },
        )
        assert response.status_code == 200

        # 事務判断で signup を再開放した場合も、新規ユーザーは承認待ち
        app.state.config.ENABLE_SIGNUP = True
        response = self.fast_api_client.post(
            self.create_url("/signup"),
            json={
                "name": "研修医",
                "email": "resident@hospital.example",
                "password": "password",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "pending"

    def test_admin_issues_user_accounts(self):
        """事務(admin)による管理画面からのユーザー発行(/auths/add)。"""
        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/add"),
                json={
                    "name": "内科 一般",
                    "email": "naika@hospital.example",
                    "password": "password",
                    "role": "user",
                },
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["role"] == "user"
        assert data["email"] == "naika@hospital.example"

    def test_signin_returns_token(self):
        from nucllei.models.auths import Auths
        from nucllei.utils.auth import get_password_hash

        Auths.insert_new_auth(
            email="doctor@hospital.example",
            password=get_password_hash("password"),
            name="医師",
            profile_image_url="/user.png",
            role="user",
        )
        response = self.fast_api_client.post(
            self.create_url("/signin"),
            json={"email": "doctor@hospital.example", "password": "password"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["token"]
        assert data["token_type"] == "Bearer"
