import logging
import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_db
from open_webui.models.users import User, UserModel, Users
from open_webui.env import SRC_LOG_LEVELS
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, String, Text, text
from open_webui.utils.auth import verify_password

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

# llens(GHSA-7rw5-9f7q-xj36): ユーザー不在時でも bcrypt 検証相当の時間を消費するための
# 固定ダミーハッシュ。応答時間差によるアカウント列挙を防ぐ(タイミング一定化)。
# 実在ユーザーのパスワードとは無関係な使い捨て値。
_TIMING_EQUALIZER_HASH = "$2b$12$.AKkThJsParPhHKIn7aQK.QAbGS6Z.uqlBiDYJrwpl.z1UY.0jHgW"

# llens(GHSA-h3ww-q6xx-w7x3): 初回ユーザーの admin 昇格を直列化する Postgres advisory lock キー。
_FIRST_USER_ADVISORY_LOCK_KEY = 0x11E5_0001

####################
# DB MODEL
####################


class Auth(Base):
    __tablename__ = "auth"

    id = Column(String, primary_key=True)
    email = Column(String)
    password = Column(Text)
    active = Column(Boolean)


class AuthModel(BaseModel):
    id: str
    email: str
    password: str
    active: bool = True


####################
# Forms
####################


class Token(BaseModel):
    token: str
    token_type: str


class ApiKey(BaseModel):
    api_key: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    profile_image_url: str


class SigninResponse(Token, UserResponse):
    pass


class SigninForm(BaseModel):
    email: str
    password: str


class LdapForm(BaseModel):
    user: str
    password: str


class ProfileImageUrlForm(BaseModel):
    profile_image_url: str


class UpdateProfileForm(BaseModel):
    profile_image_url: str
    name: str


class UpdatePasswordForm(BaseModel):
    password: str
    new_password: str


class SignupForm(BaseModel):
    name: str
    email: str
    password: str
    profile_image_url: Optional[str] = "/user.png"


class AddUserForm(SignupForm):
    role: Optional[str] = "pending"


class AuthsTable:
    def insert_new_auth(
        self,
        email: str,
        password: str,
        name: str,
        profile_image_url: str = "/user.png",
        role: str = "pending",
        oauth_sub: Optional[str] = None,
        promote_first_user_to_admin: bool = False,
    ) -> Optional[UserModel]:
        # llens(GHSA-h3ww-q6xx-w7x3): auth と user を単一トランザクションで作成する
        # (従来は Users.insert_new_user が別セッションで先行コミットしていた)。
        # promote_first_user_to_admin=True の初回ログイン経路(signup/LDAP/OAuth)では、
        # 「user 数 0 → admin」の判定と挿入を同一トランザクション + advisory lock で直列化し、
        # 並行初回ログインで複数 admin が生じる競合を排除する。role はここで確定させるのが要点。
        with get_db() as db:
            log.info("insert_new_auth")

            if promote_first_user_to_admin:
                if db.bind.dialect.name == "postgresql":
                    db.execute(
                        text("SELECT pg_advisory_xact_lock(:k)"),
                        {"k": _FIRST_USER_ADVISORY_LOCK_KEY},
                    )
                if db.query(User).count() == 0:
                    role = "admin"

            id = str(uuid.uuid4())

            auth = AuthModel(
                **{"id": id, "email": email, "password": password, "active": True}
            )
            result = Auth(**auth.model_dump())
            db.add(result)

            now = int(time.time())
            user = UserModel(
                **{
                    "id": id,
                    "name": name,
                    "email": email,
                    "role": role,
                    "profile_image_url": profile_image_url,
                    "last_active_at": now,
                    "created_at": now,
                    "updated_at": now,
                    "oauth_sub": oauth_sub,
                }
            )
            user_result = User(**user.model_dump())
            db.add(user_result)

            db.commit()
            db.refresh(result)
            db.refresh(user_result)

            if result and user_result:
                return UserModel.model_validate(user_result)
            else:
                return None

    def authenticate_user(self, email: str, password: str) -> Optional[UserModel]:
        log.info(f"authenticate_user: {email}")
        try:
            with get_db() as db:
                auth = db.query(Auth).filter_by(email=email, active=True).first()
                if auth:
                    if verify_password(password, auth.password):
                        user = Users.get_user_by_id(auth.id)
                        return user
                    else:
                        return None
                else:
                    # llens(GHSA-7rw5-9f7q-xj36): ユーザー不在でも bcrypt 検証相当の時間を
                    # 消費し、応答時間差によるアカウント列挙を防ぐ(タイミング一定化)。
                    verify_password(password, _TIMING_EQUALIZER_HASH)
                    return None
        except Exception:
            return None

    def authenticate_user_by_api_key(self, api_key: str) -> Optional[UserModel]:
        log.info(f"authenticate_user_by_api_key: {api_key}")
        # if no api_key, return None
        if not api_key:
            return None

        try:
            user = Users.get_user_by_api_key(api_key)
            return user if user else None
        except Exception:
            return False

    def authenticate_user_by_trusted_header(self, email: str) -> Optional[UserModel]:
        log.info(f"authenticate_user_by_trusted_header: {email}")
        try:
            with get_db() as db:
                auth = db.query(Auth).filter_by(email=email, active=True).first()
                if auth:
                    user = Users.get_user_by_id(auth.id)
                    return user
        except Exception:
            return None

    def update_user_password_by_id(self, id: str, new_password: str) -> bool:
        try:
            with get_db() as db:
                result = (
                    db.query(Auth).filter_by(id=id).update({"password": new_password})
                )
                db.commit()
                return True if result == 1 else False
        except Exception:
            return False

    def update_email_by_id(self, id: str, email: str) -> bool:
        try:
            with get_db() as db:
                result = db.query(Auth).filter_by(id=id).update({"email": email})
                db.commit()
                return True if result == 1 else False
        except Exception:
            return False

    def delete_auth_by_id(self, id: str) -> bool:
        try:
            with get_db() as db:
                # Delete User
                result = Users.delete_user_by_id(id)

                if result:
                    db.query(Auth).filter_by(id=id).delete()
                    db.commit()

                    return True
                else:
                    return False
        except Exception:
            return False


Auths = AuthsTable()
