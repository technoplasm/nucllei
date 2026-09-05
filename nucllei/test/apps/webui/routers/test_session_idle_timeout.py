"""セッションのアイドルタイムアウト(スライディング期限)の回帰テスト。

共有 PC(北大病院の使い回し端末)の離席対策。`JWT_EXPIRES_IN` の意味を
「発行からの絶対期限」から「**無操作**でこの時間が経ったら切れる」へ変えた。
設計と配備者向けの説明は `_memos/auth-profile.md`「共有 PC 配備」節。

CORE 領域(認証)なので、延命が起きる条件・**起きない**条件の両方を固定する:

- 延命するのは「人の操作を伴うリクエスト」(FE が `X-Nucllei-Activity` を付ける)だけ。
  ヘッダの無いリクエスト(チャット画面の 5 秒ポーリング等)では延命しない
  = タブを開いたまま離席しても永久ログインにならない。
- 発行するのは token が**寿命の半分**を過ぎたときだけ(毎リクエスト発行しない)。
- API キー(`sk-`)経路では延命しない(cookie セッション限定)。
- `JWT_EXPIRES_IN` が `-1`(既定 = 無期限)なら何もしない = 既定の挙動は不変。
- 延命 cookie の属性は signin の発行箇所と同一(80/443 併用の配備で壊れないこと)。
"""

import time
from datetime import timedelta

import pytest

from test.util.abstract_integration_test import AbstractPostgresTest

ACTIVITY_HEADER = "X-Nucllei-Activity"


def _set_cookie_header(response):
    """応答の Set-Cookie のうち token のものを返す(無ければ None)。"""
    for raw in response.headers.get_list("set-cookie"):
        if raw.startswith("token="):
            return raw
    return None


def _issued_token(response) -> str:
    """再発行された token 本体。

    TestClient の cookie jar は手で入れた cookie(domain 無し)と応答由来のもの
    (domain=testserver)が併存して CookieConflict になるため、jar ではなく
    Set-Cookie ヘッダから読む。
    """
    return _set_cookie_header(response).split(";")[0][len("token=") :]


class TestSessionIdleTimeout(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    def setup_method(self):
        super().setup_method()
        from nucllei.main import app

        self._original_expiry = app.state.config.JWT_EXPIRES_IN
        self.fast_api_client.cookies.clear()

    def teardown_method(self):
        from nucllei.main import app

        app.state.config.JWT_EXPIRES_IN = self._original_expiry
        self.fast_api_client.cookies.clear()
        super().teardown_method()

    # ---- helpers -------------------------------------------------

    def _create_user(self, email="doctor@hospital.example"):
        from nucllei.models.auths import Auths
        from nucllei.utils.auth import get_password_hash

        return Auths.insert_new_auth(
            email=email,
            password=get_password_hash("password"),
            name="医師",
            profile_image_url="/user.png",
            role="user",
        )

    def _login_with(self, user, remaining: timedelta):
        """`remaining` だけ寿命が残っている token を cookie に載せる。"""
        from nucllei.utils.auth import create_token

        token = create_token(data={"id": user.id}, expires_delta=remaining)
        self.fast_api_client.cookies.set("token", token)
        return token

    def _get_me(self, activity: bool):
        headers = {ACTIVITY_HEADER: "1"} if activity else {}
        return self.fast_api_client.get(self.create_url(""), headers=headers)

    # ---- 既定(無期限)は挙動を変えない --------------------------

    def test_default_is_unlimited_and_never_renews(self):
        """既定 `JWT_EXPIRES_IN=-1` では、操作ヘッダが付いても何も起きない。"""
        from nucllei.main import app

        assert app.state.config.JWT_EXPIRES_IN == "-1"

        user = self._create_user()
        self._login_with(user, remaining=timedelta(seconds=30))

        response = self._get_me(activity=True)
        assert response.status_code == 200, response.text
        assert _set_cookie_header(response) is None

    def test_config_exposes_idle_timeout_seconds(self):
        """FE 側タイマの TTL は `/api/config` で既存 `JWT_EXPIRES_IN` を露出する。

        設定項目は増やさない — 既にある値を秒で見せるだけ。無期限なら null。
        ログイン画面(未認証)からも読めること。
        """
        from nucllei.main import app

        response = self.fast_api_client.get("/api/config")
        assert response.status_code == 200, response.text
        assert response.json()["session_idle_timeout"] is None

        app.state.config.JWT_EXPIRES_IN = "4h"
        response = self.fast_api_client.get("/api/config")
        assert response.json()["session_idle_timeout"] == 4 * 60 * 60

    # ---- 延命する / しない -------------------------------------

    def test_renews_only_with_activity_header(self):
        from nucllei.main import app
        from nucllei.utils.auth import decode_token

        app.state.config.JWT_EXPIRES_IN = "1h"
        user = self._create_user()

        # 寿命(1h)の半分を過ぎている token。ヘッダが無ければ延命しない
        # (= チャット画面の 5 秒ポーリングでは切れる時計が止まらない)
        old_token = self._login_with(user, remaining=timedelta(minutes=5))
        response = self._get_me(activity=False)
        assert response.status_code == 200, response.text
        assert _set_cookie_header(response) is None

        # 人の操作があったリクエストでだけ再発行される
        self.fast_api_client.cookies.set("token", old_token)
        response = self._get_me(activity=True)
        assert response.status_code == 200, response.text
        assert _set_cookie_header(response) is not None

        new_token = _issued_token(response)
        assert new_token != old_token
        # 新しい期限は「今から 1h」(スライディング)
        exp = decode_token(new_token)["exp"]
        assert 3500 < exp - int(time.time()) <= 3600

    def test_no_renewal_while_more_than_half_the_lifetime_remains(self):
        """毎リクエスト発行しない — 半分を過ぎるまでは何もしない。"""
        from nucllei.main import app

        app.state.config.JWT_EXPIRES_IN = "1h"
        user = self._create_user()
        self._login_with(user, remaining=timedelta(minutes=59))

        response = self._get_me(activity=True)
        assert response.status_code == 200, response.text
        assert _set_cookie_header(response) is None

    def test_renewal_is_not_limited_to_the_auths_endpoint(self):
        """延命は認証依存関数(= 全 endpoint 共通)で起きる。

        `GET /api/v1/auths/` 専用の細工ではないことを、別 endpoint で固定する。
        """
        from nucllei.main import app

        app.state.config.JWT_EXPIRES_IN = "1h"
        user = self._create_user()
        self._login_with(user, remaining=timedelta(minutes=5))

        response = self.fast_api_client.get(
            "/api/v1/chats/", headers={ACTIVITY_HEADER: "1"}
        )
        assert response.status_code == 200, response.text
        assert _set_cookie_header(response) is not None

    def test_expired_token_is_rejected_not_renewed(self):
        """寿命を過ぎた token は、操作ヘッダが付いていても復活しない。"""
        from nucllei.main import app

        app.state.config.JWT_EXPIRES_IN = "1h"
        user = self._create_user()
        self._login_with(user, remaining=timedelta(seconds=-10))

        response = self._get_me(activity=True)
        assert response.status_code in (401, 403), response.text
        assert _set_cookie_header(response) is None

    def test_token_without_exp_gets_bounded_on_next_activity(self):
        """無期限だった頃の token は、設定変更後の最初の操作で期限付きになる。"""
        from nucllei.main import app
        from nucllei.utils.auth import create_token, decode_token

        user = self._create_user()
        self.fast_api_client.cookies.set(
            "token", create_token(data={"id": user.id}, expires_delta=None)
        )
        app.state.config.JWT_EXPIRES_IN = "1h"

        response = self._get_me(activity=True)
        assert response.status_code == 200, response.text
        assert decode_token(_issued_token(response))["exp"]

    # ---- API キー経路 -------------------------------------------

    def test_api_key_session_is_not_renewed(self):
        """API キー認証では cookie を発行しない(cookie セッション限定)。"""
        from nucllei.main import app
        from nucllei.models.users import Users

        app.state.config.JWT_EXPIRES_IN = "1h"
        user = self._create_user()
        Users.update_user_api_key_by_id(user.id, "sk-idle-timeout-test")

        response = self.fast_api_client.get(
            self.create_url(""),
            headers={
                "Authorization": "Bearer sk-idle-timeout-test",
                ACTIVITY_HEADER: "1",
            },
        )
        assert response.status_code == 200, response.text
        assert _set_cookie_header(response) is None

    # ---- cookie 属性 --------------------------------------------

    def test_renewal_cookie_attributes_match_signin(self):
        """再発行 cookie の属性は signin の発行箇所と同一でなければならない。

        `httponly` / `samesite` / `secure` がずれると 80/443 併用の配備で壊れる。
        """
        from nucllei.main import app

        app.state.config.JWT_EXPIRES_IN = "1h"
        user = self._create_user()

        signin = self.fast_api_client.post(
            self.create_url("/signin"),
            json={"email": "doctor@hospital.example", "password": "password"},
        )
        assert signin.status_code == 200, signin.text
        signin_attrs = _attributes(_set_cookie_header(signin))

        self._login_with(user, remaining=timedelta(minutes=5))
        renewal = self._get_me(activity=True)
        renewal_attrs = _attributes(_set_cookie_header(renewal))

        assert renewal_attrs == signin_attrs
        assert "httponly" in renewal_attrs
        assert renewal_attrs.get("samesite") == "lax"
        assert "secure" not in renewal_attrs  # 既定(HTTP 配備)

    # ---- GET /api/v1/auths/ は token を発行しない ----------------

    def test_get_session_user_no_longer_issues_a_token(self):
        """ページ読み込みだけで無期限に延命できる経路を塞いだこと。"""
        from nucllei.main import app

        app.state.config.JWT_EXPIRES_IN = "1h"
        user = self._create_user()
        token = self._login_with(user, remaining=timedelta(minutes=59))

        response = self._get_me(activity=False)
        assert response.status_code == 200, response.text

        body = response.json()
        assert "token" not in body
        assert "token_type" not in body
        assert body["expires_at"] is not None
        assert _set_cookie_header(response) is None
        assert token  # cookie は書き換えられていない(Set-Cookie が無い)


def _attributes(set_cookie: str) -> dict:
    """`Set-Cookie` を属性名(小文字)→値の dict にする。値・期限は比較対象外。"""
    attrs = {}
    for part in set_cookie.split(";")[1:]:
        name, _, value = part.strip().partition("=")
        name = name.lower()
        if name in {"expires", "max-age"}:
            continue
        attrs[name] = value.lower()
    return attrs


@pytest.mark.parametrize("expiry,expected", [("-1", None), ("0", None), ("30m", 1800)])
def test_session_idle_timeout_seconds(expiry, expected):
    """`JWT_EXPIRES_IN` → 秒の変換(DB 不要の単体)。"""
    from nucllei.utils.auth import session_idle_timeout_seconds

    assert session_idle_timeout_seconds(expiry) == expected
