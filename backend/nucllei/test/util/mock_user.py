from contextlib import contextmanager

from fastapi import FastAPI


@contextmanager
def mock_webui_user(**kwargs):
    # 旧構成では webui サブアプリ(nucllei.routers.webui)に override を当てていたが、
    # 現構成は単一アプリのため TestClient と同じ nucllei.main:app に当てる。
    from nucllei.main import app

    with mock_user(app, **kwargs):
        yield


@contextmanager
def mock_user(app: FastAPI, **kwargs):
    from nucllei.utils.auth import (
        get_current_user,
        get_verified_user,
        get_admin_user,
        get_current_user_by_api_key,
    )
    from nucllei.models.users import User

    def create_user():
        user_parameters = {
            "id": "1",
            "name": "John Doe",
            "email": "john.doe@nucllei.local",
            "role": "user",
            "profile_image_url": "/user.png",
            "last_active_at": 1627351200,
            "updated_at": 1627351200,
            "created_at": 162735120,
            **kwargs,
        }
        return User(**user_parameters)

    app.dependency_overrides = {
        get_current_user: create_user,
        get_verified_user: create_user,
        get_admin_user: create_user,
        get_current_user_by_api_key: create_user,
    }
    yield
    app.dependency_overrides = {}


@contextmanager
def mock_webui_user_strict(**kwargs):
    """llens(#55): get_current_user だけを差し替え、role ゲート
    (get_verified_user / get_admin_user)は実物を通す mock。
    「user role で admin endpoint を叩くと 401」のネガティブテストに使う
    (mock_webui_user は get_admin_user ごと差し替えるため素通りしてしまう)。
    """
    from nucllei.main import app
    from nucllei.utils.auth import get_current_user, get_current_user_by_api_key
    from nucllei.models.users import User

    def create_user():
        user_parameters = {
            "id": "1",
            "name": "John Doe",
            "email": "john.doe@nucllei.local",
            "role": "user",
            "profile_image_url": "/user.png",
            "last_active_at": 1627351200,
            "updated_at": 1627351200,
            "created_at": 162735120,
            **kwargs,
        }
        return User(**user_parameters)

    app.dependency_overrides = {
        get_current_user: create_user,
        get_current_user_by_api_key: create_user,
    }
    yield
    app.dependency_overrides = {}
