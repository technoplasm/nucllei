"""認証まわりの穴の回帰テスト(upstream リリースノート照合で発見、実装は自前)。

CORE 領域(認証)なので、通る経路・通らない経路の両方を固定する:

- trusted header 認証: 2 人目以降も自動登録される(1 人目で ENABLE_SIGNUP が
  False に落ちても、ヘッダ認証の登録は止まらない)
- trusted header 認証: cookie の JWT のユーザーとヘッダのメールが違えば 401
  (共有 PC で前の人のセッションが残っている)。一致なら 200
- OAuth ロール管理: 許可ロールにも admin ロールにも一致しなければ拒否
  (DEFAULT_USER_ROLE に落とさない)
- admin のユーザー追加で返る token に exp が付く
"""

import time
from datetime import timedelta

import pytest
from fastapi import HTTPException

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

HEADER = "X-Trusted-Email"


class TestTrustedHeaderAuth(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    @pytest.fixture(autouse=True)
    def _trusted_header(self, monkeypatch):
        import nucllei.routers.auths as auths_router
        import nucllei.utils.auth as auth_utils

        monkeypatch.setattr(auths_router, "WEBUI_AUTH_TRUSTED_EMAIL_HEADER", HEADER)
        monkeypatch.setattr(auth_utils, "WEBUI_AUTH_TRUSTED_EMAIL_HEADER", HEADER)
        config = self.fast_api_client.app.state.config
        saved = config.ENABLE_SIGNUP
        config.ENABLE_SIGNUP = True
        self.fast_api_client.cookies.clear()
        yield
        config.ENABLE_SIGNUP = saved
        self.fast_api_client.cookies.clear()

    def _signin(self, email):
        return self.fast_api_client.post(
            self.create_url("/signin"),
            json={"email": "", "password": ""},
            headers={HEADER: email},
        )

    def test_second_and_later_users_are_auto_registered(self):
        first = self._signin("first@hospital.example")
        assert first.status_code == 200
        assert first.json()["role"] == "admin"  # 初回ユーザーはアトミックに admin
        # 1 人目の登録で ENABLE_SIGNUP が落ちていても 2 人目・3 人目は通る
        self.fast_api_client.app.state.config.ENABLE_SIGNUP = False
        for email in ("second@hospital.example", "third@hospital.example"):
            response = self._signin(email)
            assert response.status_code == 200, response.text
            assert response.json()["email"] == email
            assert response.json()["role"] != "admin"

    def test_session_of_previous_user_is_rejected_for_other_header(self):
        from nucllei.utils.auth import create_token

        a = self._signin("alice@hospital.example").json()
        self._signin("bob@hospital.example")
        token = create_token(data={"id": a["id"]}, expires_delta=timedelta(hours=1))
        self.fast_api_client.cookies.clear()
        self.fast_api_client.cookies.set("token", token)

        # alice の cookie が残った端末を bob が使う → token 側を通さない
        response = self.fast_api_client.get(
            self.create_url(""), headers={HEADER: "bob@hospital.example"}
        )
        assert response.status_code == 401

        # 一致していれば従来どおり通る(大文字小文字は区別しない)
        response = self.fast_api_client.get(
            self.create_url(""), headers={HEADER: "Alice@Hospital.example"}
        )
        assert response.status_code == 200
        assert response.json()["id"] == a["id"]

        # 再サインインはヘッダ側(bob)の token を発行する
        response = self._signin("bob@hospital.example")
        assert response.json()["email"] == "bob@hospital.example"


class TestOAuthRoleManagement(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    @pytest.fixture(autouse=True)
    def _role_config(self):
        from nucllei.utils.oauth import auth_manager_config as cfg

        keys = (
            "ENABLE_OAUTH_ROLE_MANAGEMENT",
            "OAUTH_ROLES_CLAIM",
            "OAUTH_ALLOWED_ROLES",
            "OAUTH_ADMIN_ROLES",
            "DEFAULT_USER_ROLE",
        )
        saved = {k: getattr(cfg, k) for k in keys}
        cfg.ENABLE_OAUTH_ROLE_MANAGEMENT = True
        cfg.OAUTH_ROLES_CLAIM = "realm.roles"
        cfg.OAUTH_ALLOWED_ROLES = ["doctor"]
        cfg.OAUTH_ADMIN_ROLES = ["it-admin"]
        cfg.DEFAULT_USER_ROLE = "user"
        yield
        for k, v in saved.items():
            setattr(cfg, k, v)

    def _manager(self):
        from nucllei.utils.oauth import OAuthManager

        return OAuthManager.__new__(OAuthManager)  # provider 登録は不要

    def _roles(self, *roles):
        return {"realm": {"roles": list(roles)}}

    def test_matching_roles_are_assigned(self):
        m = self._manager()
        assert m.get_user_role(None, self._roles("doctor")) == "user"
        assert m.get_user_role(None, self._roles("doctor", "it-admin")) == "admin"

    def test_unmatched_roles_are_rejected_not_defaulted(self):
        m = self._manager()
        for user_data in (self._roles("visitor"), self._roles(), {}, {"realm": []}):
            with pytest.raises(HTTPException) as exc:
                m.get_user_role(None, user_data)
            assert exc.value.status_code == 403


class TestAddUserTokenExpiry(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    def _add(self, email):
        with mock_webui_user(role="admin"):
            response = self.fast_api_client.post(
                self.create_url("/add"),
                json={"name": "N", "email": email, "password": "pw", "role": "user"},
            )
        assert response.status_code == 200
        return response.json()["token"]

    def _exp(self, token):
        from nucllei.utils.auth import decode_token

        return decode_token(token)["exp"]

    def test_token_has_exp_even_when_sessions_are_unlimited(self):
        config = self.fast_api_client.app.state.config
        saved = config.SESSION_IDLE_TIMEOUT
        try:
            config.SESSION_IDLE_TIMEOUT = "-1"
            exp = self._exp(self._add("a@hospital.example"))
            assert time.time() < exp <= time.time() + 3600 + 5

            config.SESSION_IDLE_TIMEOUT = "10m"
            exp = self._exp(self._add("b@hospital.example"))
            assert exp <= time.time() + 600 + 5
        finally:
            config.SESSION_IDLE_TIMEOUT = saved
