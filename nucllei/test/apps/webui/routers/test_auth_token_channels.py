"""認証経路の分離(#144)の回帰テスト。

cookie `token` は人の JWT セッション専用、API キー(`sk-`)は
`Authorization: Bearer sk-…` 専用。旧実装は Authorization ヘッダが無いとき
cookie の値を拾い、`sk-` 始まりなら API キーとして認証していた
(2 つの認証経路が 1 つの cookie 名に同居)。

CORE 領域(認証)なので、通る経路・通らない経路の両方を固定する:

- cookie に `sk-` → 401(不正な token と同じ扱い)
- ヘッダに `sk-` → 200(API キー有効時。従来どおり)
- cookie に JWT → 200 / ヘッダに JWT → 200
- signout は cookie の中身に関係なく 200 で cookie を消す
"""

from datetime import timedelta

from test.util.abstract_integration_test import AbstractPostgresTest

API_KEY = "sk-" + "1" * 32


class TestAuthTokenChannels(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    def setup_method(self):
        super().setup_method()
        from nucllei.main import app

        config = app.state.config
        self._saved = (
            config.ENABLE_API_KEY,
            config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS,
        )
        config.ENABLE_API_KEY = True
        config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS = False
        self.fast_api_client.cookies.clear()

    def teardown_method(self):
        from nucllei.main import app

        config = app.state.config
        (
            config.ENABLE_API_KEY,
            config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS,
        ) = self._saved
        self.fast_api_client.cookies.clear()
        super().teardown_method()

    def _create_user_with_api_key(self):
        from nucllei.models.auths import Auths
        from nucllei.models.users import Users
        from nucllei.utils.auth import get_password_hash

        user = Auths.insert_new_auth(
            email="api.channel@hospital.example",
            password=get_password_hash("password"),
            name="API",
            profile_image_url="/user.png",
            role="user",
        )
        Users.update_user_api_key_by_id(user.id, API_KEY)
        return user

    def _jwt(self, user):
        from nucllei.utils.auth import create_token

        return create_token(data={"id": user.id}, expires_delta=timedelta(hours=1))

    def test_api_key_in_cookie_is_rejected(self):
        """cookie の `sk-` は API キーとして解釈しない — 不正 token と同じ 401。"""
        self._create_user_with_api_key()
        self.fast_api_client.cookies.set("token", API_KEY)

        response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 401, response.text

        # 応答は不正な JWT を cookie に入れたときと同一(経路の有無を区別させない)
        self.fast_api_client.cookies.set("token", "not-a-jwt")
        invalid = self.fast_api_client.get(self.create_url(""))
        assert invalid.status_code == 401
        assert response.json() == invalid.json()

    def test_api_key_in_header_is_accepted(self):
        user = self._create_user_with_api_key()

        response = self.fast_api_client.get(
            self.create_url(""), headers={"Authorization": f"Bearer {API_KEY}"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"] == user.id

    def test_jwt_in_cookie_is_accepted(self):
        user = self._create_user_with_api_key()
        self.fast_api_client.cookies.set("token", self._jwt(user))

        response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 200, response.text
        assert response.json()["id"] == user.id

    def test_jwt_in_header_is_accepted(self):
        user = self._create_user_with_api_key()

        response = self.fast_api_client.get(
            self.create_url(""),
            headers={"Authorization": f"Bearer {self._jwt(user)}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"] == user.id

    def test_header_wins_over_api_key_cookie(self):
        """ヘッダがあれば cookie は見ない(cookie の `sk-` が正規のヘッダ認証を壊さない)。"""
        user = self._create_user_with_api_key()
        self.fast_api_client.cookies.set("token", API_KEY)

        response = self.fast_api_client.get(
            self.create_url(""),
            headers={"Authorization": f"Bearer {self._jwt(user)}"},
        )
        assert response.status_code == 200, response.text

    def test_config_ignores_api_key_cookie(self):
        """`/api/config` は未認証でも読める — cookie の `sk-` は未ログイン扱い(401 にしない)。"""
        self._create_user_with_api_key()
        self.fast_api_client.cookies.set("token", API_KEY)

        response = self.fast_api_client.get("/api/config")
        assert response.status_code == 200, response.text

    def test_signout_unaffected(self):
        """signout は cookie の中身(JWT / `sk-`)に関係なく 200 で cookie を消す。"""
        user = self._create_user_with_api_key()

        for value in (self._jwt(user), API_KEY):
            self.fast_api_client.cookies.set("token", value)
            response = self.fast_api_client.get(self.create_url("/signout"))
            assert response.status_code == 200, response.text
            assert response.json() == {"status": True}
            cleared = [
                raw
                for raw in response.headers.get_list("set-cookie")
                if raw.startswith("token=")
            ]
            assert cleared and "max-age=0" in cleared[0].lower()
            self.fast_api_client.cookies.clear()
