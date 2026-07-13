from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestAuths(AbstractPostgresTest):
    BASE_PATH = "/api/v1/auths"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.auths import Auths
        from open_webui.models.users import Users

        cls.users = Users
        cls.auths = Auths

    def test_get_session_user(self):
        with mock_webui_user():
            response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 200
        data = response.json()
        # 応答には permissions / expires_at 等も含まれるため、基本項目のみ検証する
        assert data["id"] == "1"
        assert data["name"] == "John Doe"
        assert data["email"] == "john.doe@openwebui.com"
        assert data["role"] == "user"
        assert data["profile_image_url"] == "/user.png"

    def test_ldap_empty_password_rejected(self):
        """GHSA-2r4p-jpmg-48f4: 空パスワードの LDAP simple bind を拒否する。

        空パスワードは多くの LDAP サーバで unauthenticated bind として成功扱いになり
        認証バイパスになる。ガードは bind に至る前に発火し、汎用資格情報エラー
        (INVALID_CRED)を返す — LDAP サーバへは一切接続しない。
        """
        from open_webui.constants import ERROR_MESSAGES

        config = self.fast_api_client.app.state.config
        original = config.ENABLE_LDAP
        config.ENABLE_LDAP = True
        try:
            response = self.fast_api_client.post(
                self.create_url("/ldap"),
                json={"user": "someone", "password": ""},
            )
        finally:
            config.ENABLE_LDAP = original

        assert response.status_code == 400
        # detail が INVALID_CRED であることが「ガードが発火した(LDAP 接続前に弾いた)」証拠。
        assert response.json()["detail"] == ERROR_MESSAGES.INVALID_CRED

    def test_update_profile(self):
        from open_webui.utils.auth import get_password_hash

        user = self.auths.insert_new_auth(
            email="john.doe@openwebui.com",
            password=get_password_hash("old_password"),
            name="John Doe",
            profile_image_url="/user.png",
            role="user",
        )

        with mock_webui_user(id=user.id):
            response = self.fast_api_client.post(
                self.create_url("/update/profile"),
                json={"name": "John Doe 2", "profile_image_url": "/user2.png"},
            )
        assert response.status_code == 200
        db_user = self.users.get_user_by_id(user.id)
        assert db_user.name == "John Doe 2"
        assert db_user.profile_image_url == "/user2.png"

    def test_update_password(self):
        from open_webui.utils.auth import get_password_hash

        user = self.auths.insert_new_auth(
            email="john.doe@openwebui.com",
            password=get_password_hash("old_password"),
            name="John Doe",
            profile_image_url="/user.png",
            role="user",
        )

        with mock_webui_user(id=user.id):
            response = self.fast_api_client.post(
                self.create_url("/update/password"),
                json={"password": "old_password", "new_password": "new_password"},
            )
        assert response.status_code == 200

        old_auth = self.auths.authenticate_user(
            "john.doe@openwebui.com", "old_password"
        )
        assert old_auth is None
        new_auth = self.auths.authenticate_user(
            "john.doe@openwebui.com", "new_password"
        )
        assert new_auth is not None

    def test_signin(self):
        from open_webui.utils.auth import get_password_hash

        user = self.auths.insert_new_auth(
            email="john.doe@openwebui.com",
            password=get_password_hash("password"),
            name="John Doe",
            profile_image_url="/user.png",
            role="user",
        )
        response = self.fast_api_client.post(
            self.create_url("/signin"),
            json={"email": "john.doe@openwebui.com", "password": "password"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == user.id
        assert data["name"] == "John Doe"
        assert data["email"] == "john.doe@openwebui.com"
        assert data["role"] == "user"
        assert data["profile_image_url"] == "/user.png"
        assert data["token"] is not None and len(data["token"]) > 0
        assert data["token_type"] == "Bearer"

    def test_signup(self):
        response = self.fast_api_client.post(
            self.create_url("/signup"),
            json={
                "name": "John Doe",
                "email": "john.doe@openwebui.com",
                "password": "password",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] is not None and len(data["id"]) > 0
        assert data["name"] == "John Doe"
        assert data["email"] == "john.doe@openwebui.com"
        assert data["role"] in ["admin", "user", "pending"]
        assert data["profile_image_url"] == "/user.png"
        assert data["token"] is not None and len(data["token"]) > 0
        assert data["token_type"] == "Bearer"

    def test_add_user(self):
        with mock_webui_user():
            response = self.fast_api_client.post(
                self.create_url("/add"),
                json={
                    "name": "John Doe 2",
                    "email": "john.doe2@openwebui.com",
                    "password": "password2",
                    "role": "admin",
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] is not None and len(data["id"]) > 0
        assert data["name"] == "John Doe 2"
        assert data["email"] == "john.doe2@openwebui.com"
        assert data["role"] == "admin"
        assert data["profile_image_url"] == "/user.png"
        assert data["token"] is not None and len(data["token"]) > 0
        assert data["token_type"] == "Bearer"

    def test_get_admin_details(self):
        self.auths.insert_new_auth(
            email="john.doe@openwebui.com",
            password="password",
            name="John Doe",
            profile_image_url="/user.png",
            role="admin",
        )
        with mock_webui_user():
            response = self.fast_api_client.get(self.create_url("/admin/details"))

        assert response.status_code == 200
        assert response.json() == {
            "name": "John Doe",
            "email": "john.doe@openwebui.com",
        }

    def test_create_api_key_(self):
        user = self.auths.insert_new_auth(
            email="john.doe@openwebui.com",
            password="password",
            name="John Doe",
            profile_image_url="/user.png",
            role="admin",
        )
        with mock_webui_user(id=user.id):
            response = self.fast_api_client.post(self.create_url("/api_key"))
        assert response.status_code == 200
        data = response.json()
        assert data["api_key"] is not None
        assert len(data["api_key"]) > 0

    def test_delete_api_key(self):
        user = self.auths.insert_new_auth(
            email="john.doe@openwebui.com",
            password="password",
            name="John Doe",
            profile_image_url="/user.png",
            role="admin",
        )
        self.users.update_user_api_key_by_id(user.id, "abc")
        with mock_webui_user(id=user.id):
            response = self.fast_api_client.delete(self.create_url("/api_key"))
        assert response.status_code == 200
        assert response.json() == True
        db_user = self.users.get_user_by_id(user.id)
        assert db_user.api_key is None

    def test_get_api_key(self):
        user = self.auths.insert_new_auth(
            email="john.doe@openwebui.com",
            password="password",
            name="John Doe",
            profile_image_url="/user.png",
            role="admin",
        )
        self.users.update_user_api_key_by_id(user.id, "abc")
        with mock_webui_user(id=user.id):
            response = self.fast_api_client.get(self.create_url("/api_key"))
        assert response.status_code == 200
        assert response.json() == {"api_key": "abc"}

    # -----------------------------------------------------------------
    # GHSA-h3ww-q6xx-w7x3: 初回ユーザーの admin 昇格をアトミックに(#38 残)
    # -----------------------------------------------------------------

    def test_first_user_promoted_to_admin_atomically(self):
        """初回ユーザーは insert 内で admin に昇格する。

        呼び出し側が既定ロール(user)を渡しても、user 数 0 の判定が insert と同一
        トランザクションで行われ admin に確定する。role 決定の権威が insert 内にある
        ことが、並行初回ログインでの複数 admin 化を防ぐ核心。
        """
        from open_webui.utils.auth import get_password_hash

        user = self.auths.insert_new_auth(
            email="first@openwebui.com",
            password=get_password_hash("x"),
            name="First",
            role="user",
            promote_first_user_to_admin=True,
        )
        assert user.role == "admin"

    def test_subsequent_user_not_promoted(self):
        """2 人目以降は promote フラグがあっても admin にならない。

        count が「呼び出し側の事前読取」ではなく「insert 時点」で評価されるため、
        先着 1 人だけが admin になる(= 競合しても複数 admin が生じない)。
        """
        from open_webui.utils.auth import get_password_hash

        self.auths.insert_new_auth(
            email="a@openwebui.com",
            password=get_password_hash("x"),
            name="A",
            role="user",
            promote_first_user_to_admin=True,
        )
        second = self.auths.insert_new_auth(
            email="b@openwebui.com",
            password=get_password_hash("x"),
            name="B",
            role="user",
            promote_first_user_to_admin=True,
        )
        assert second.role == "user"

    def test_promote_flag_off_keeps_passed_role(self):
        """promote フラグ無しは従来どおり渡された role をそのまま使う(空 DB でも昇格しない)。"""
        from open_webui.utils.auth import get_password_hash

        user = self.auths.insert_new_auth(
            email="x@openwebui.com",
            password=get_password_hash("x"),
            name="X",
            role="user",
        )
        assert user.role == "user"

    # -----------------------------------------------------------------
    # GHSA-7rw5-9f7q-xj36: ログインタイミングによるアカウント列挙の防止(#38 残)
    # -----------------------------------------------------------------

    def test_authenticate_missing_user_runs_timing_equalizer(self):
        """不在ユーザーでも bcrypt 検証(タイミング一定化)を実行する。

        旧実装は auth 行が無いと即 return し bcrypt を回さなかったため、応答時間差で
        メール存在有無を列挙できた。修正後は固定ダミーハッシュに対して verify_password を
        1 回呼ぶ。これを spy で確認する(旧コードでは呼ばれず本テストは落ちる)。
        """
        import open_webui.models.auths as auths_mod

        calls = []
        real = auths_mod.verify_password

        def spy(pw, h):
            calls.append(h)
            return real(pw, h)

        auths_mod.verify_password = spy
        try:
            result = self.auths.authenticate_user(
                "nonexistent@openwebui.com", "whatever"
            )
        finally:
            auths_mod.verify_password = real

        assert result is None
        assert calls == [auths_mod._TIMING_EQUALIZER_HASH]

    def test_authenticate_existing_user_wrong_password_no_equalizer(self):
        """実在ユーザーの誤パスワードは実ハッシュを検証する(ダミーは使わない)= 対称性の確認。"""
        import open_webui.models.auths as auths_mod
        from open_webui.utils.auth import get_password_hash

        self.auths.insert_new_auth(
            email="real@openwebui.com",
            password=get_password_hash("correct-horse"),
            name="Real",
            role="user",
        )

        calls = []
        real = auths_mod.verify_password

        def spy(pw, h):
            calls.append(h)
            return real(pw, h)

        auths_mod.verify_password = spy
        try:
            result = self.auths.authenticate_user("real@openwebui.com", "wrong")
        finally:
            auths_mod.verify_password = real

        assert result is None
        assert len(calls) == 1
        assert calls[0] != auths_mod._TIMING_EQUALIZER_HASH

    # -----------------------------------------------------------------
    # GHSA-57q6-fvp4-pqmm / CVE-2026-45339: API キーの制限エンドポイント回避(#38 残)
    # -----------------------------------------------------------------

    def test_api_key_empty_allowlist_denies_all(self):
        """制限有効 + allowed_endpoints 空/末尾カンマ は「全許可」ではなく「全拒否」。

        旧実装は "".split(",") == [""] で `path.startswith("/")` が全 path に一致し、
        制限を有効化しても実質全許可になった。空要素を除去して全拒否(403)にする。
        明示的に許可した endpoint のみ 200。
        """
        from open_webui.utils.auth import get_password_hash

        user = self.auths.insert_new_auth(
            email="api.user@openwebui.com",
            password=get_password_hash("x"),
            name="API User",
            role="user",
        )
        api_key = "sk-" + "0" * 32
        self.users.update_user_api_key_by_id(user.id, api_key)

        config = self.fast_api_client.app.state.config
        saved = (
            config.ENABLE_API_KEY,
            config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS,
            config.API_KEY_ALLOWED_ENDPOINTS,
        )
        config.ENABLE_API_KEY = True
        config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS = True
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            # 空リスト → 全拒否
            config.API_KEY_ALLOWED_ENDPOINTS = ""
            r = self.fast_api_client.get(self.create_url(""), headers=headers)
            assert r.status_code == 403

            # 末尾カンマ(空要素混入)でも全許可にならない
            config.API_KEY_ALLOWED_ENDPOINTS = "/api/v1/models,"
            r = self.fast_api_client.get(self.create_url(""), headers=headers)
            assert r.status_code == 403

            # 明示的に許可した endpoint は通る
            config.API_KEY_ALLOWED_ENDPOINTS = "/api/v1/auths"
            r = self.fast_api_client.get(self.create_url(""), headers=headers)
            assert r.status_code == 200
        finally:
            (
                config.ENABLE_API_KEY,
                config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS,
                config.API_KEY_ALLOWED_ENDPOINTS,
            ) = saved

    def test_create_admin_cli(self):
        """llens: 公開オンボーディングの代わりの CLI 管理者作成(open-webui create-admin)。

        role=admin で作成され、email は小文字化。重複メール・短いパスワード・
        不正な email 形式は非ゼロ終了で拒否する。
        """
        from typer.testing import CliRunner
        from open_webui import app as cli_app

        runner = CliRunner()

        # 正常系: admin を作成(email は小文字化される)
        result = runner.invoke(
            cli_app,
            [
                "create-admin",
                "--email",
                "Ops@Llens.Local",
                "--name",
                "Ops",
                "--password",
                "supersecret1",
            ],
        )
        assert result.exit_code == 0, result.output
        admin = self.users.get_user_by_email("ops@llens.local")
        assert admin is not None
        assert admin.role == "admin"
        assert admin.name == "Ops"

        # 重複メールは拒否
        dup = runner.invoke(
            cli_app,
            [
                "create-admin",
                "--email",
                "ops@llens.local",
                "--password",
                "supersecret1",
            ],
        )
        assert dup.exit_code == 1

        # 短いパスワードは拒否(作成されない)
        short = runner.invoke(
            cli_app,
            ["create-admin", "--email", "new@llens.local", "--password", "short"],
        )
        assert short.exit_code == 1
        assert self.users.get_user_by_email("new@llens.local") is None

        # 不正な email 形式は拒否
        bad = runner.invoke(
            cli_app,
            ["create-admin", "--email", "notanemail", "--password", "supersecret1"],
        )
        assert bad.exit_code == 1
