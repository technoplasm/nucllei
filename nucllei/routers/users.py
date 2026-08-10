import logging
from typing import Optional

from nucllei.models.auths import Auths
from nucllei.models.groups import Groups
from nucllei.models.chats import Chats
from nucllei.models.users import (
    UserModel,
    UserRoleUpdateForm,
    Users,
    UserSettings,
    UserUpdateForm,
    merge_additional_user_meta,
)


from nucllei.constants import ERROR_MESSAGES
from nucllei.env import SRC_LOG_LEVELS
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from nucllei.utils.auth import get_admin_user, get_password_hash, get_verified_user
from nucllei.utils.access_control import get_permissions

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()

############################
# GetUsers
############################


@router.get("/", response_model=list[UserModel])
async def get_users(
    skip: Optional[int] = None,
    limit: Optional[int] = None,
    user=Depends(get_admin_user),
):
    return Users.get_users(skip, limit)


############################
# User Search(ACL 用)
############################


@router.get("/search")
async def search_users(
    query: str = Query("", max_length=100),
    ids: Optional[str] = Query(None, max_length=2000),
    user=Depends(get_verified_user),
):
    """llens(#85): AccessControl のユーザー指定用ディレクトリ検索(verified 向け)。

    - query: 名前/メールの部分一致(pending は対象外)。空なら []
    - ids: カンマ区切り id 群の名前解決(既存 ACL の user_ids チップ表示用)
    返すのは最小フィールド(id / name / email)のみ — 院内ディレクトリとしての
    開示は許容する判断(2026-07-19)。full な UserModel は admin 専用の / に残す。
    """
    if ids is not None:
        id_list = [i for i in ids.split(",") if i][:100]
        users = Users.get_users_by_user_ids(id_list)
    elif query.strip():
        users = Users.search_active_users(query.strip())
    else:
        users = []
    return [{"id": u.id, "name": u.name, "email": u.email} for u in users]


############################
# User Groups
############################


@router.get("/groups")
async def get_user_groups(user=Depends(get_verified_user)):
    return Groups.get_groups_by_member_id(user.id)


############################
# User Permissions
############################


@router.get("/permissions")
async def get_user_permissisions(request: Request, user=Depends(get_verified_user)):
    user_permissions = get_permissions(
        user.id, request.app.state.config.USER_PERMISSIONS
    )

    return user_permissions


############################
# User Default Permissions
############################
# llens: 実際の USER_PERMISSIONS 構造(config.DEFAULT_USER_PERMISSIONS)と同期を保つこと。
# ここが古いと保存(POST)時に pydantic がキーを落とし、admin の既定権限編集が壊れる
# (旧 prompts 残骸で workspace.skills が消えていた)。
class WorkspacePermissions(BaseModel):
    # llens(#66): workspace.skills = skill editor ロール。models/tools/knowledge と
    # sharing.* は 3 層化で廃止(model/tool/function の定義変更は admin 専有)。
    skills: bool = False


class ChatPermissions(BaseModel):
    controls: bool = True
    file_upload: bool = True
    delete: bool = True
    edit: bool = True
    share: bool = True
    clone: bool = False
    multiple_models: bool = True


class UserPermissions(BaseModel):
    workspace: WorkspacePermissions
    chat: ChatPermissions


@router.get("/default/permissions", response_model=UserPermissions)
async def get_default_user_permissions(request: Request, user=Depends(get_admin_user)):
    return {
        "workspace": WorkspacePermissions(
            **request.app.state.config.USER_PERMISSIONS.get("workspace", {})
        ),
        "chat": ChatPermissions(
            **request.app.state.config.USER_PERMISSIONS.get("chat", {})
        ),
    }


@router.post("/default/permissions")
async def update_default_user_permissions(
    request: Request, form_data: UserPermissions, user=Depends(get_admin_user)
):
    request.app.state.config.USER_PERMISSIONS = form_data.model_dump()
    return request.app.state.config.USER_PERMISSIONS


############################
# UpdateUserRole
############################


@router.post("/update/role", response_model=Optional[UserModel])
async def update_user_role(form_data: UserRoleUpdateForm, user=Depends(get_admin_user)):
    # llens(#31): disabled = アカウント無効化。get_verified_user が user/admin 以外を
    # 弾くため API は全遮断され、FE では AccountPending 相当の案内画面に落ちる。
    # user の hard delete は撤去済み(チャット証憑の保全)— 無効化がその代替。
    if form_data.role not in ("pending", "user", "admin", "disabled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role must be one of: pending, user, admin, disabled",
        )

    if user.id != form_data.id and form_data.id != Users.get_first_user().id:
        return Users.update_user_role_by_id(form_data.id, form_data.role)

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ERROR_MESSAGES.ACTION_PROHIBITED,
    )


############################
# GetUserSettingsBySessionUser
############################


@router.get("/user/settings", response_model=Optional[UserSettings])
async def get_user_settings_by_session_user(user=Depends(get_verified_user)):
    user = Users.get_user_by_id(user.id)
    if user:
        return user.settings
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# UpdateUserSettingsBySessionUser
############################


@router.post("/user/settings/update", response_model=UserSettings)
async def update_user_settings_by_session_user(
    form_data: UserSettings, user=Depends(get_verified_user)
):
    user = Users.update_user_settings_by_id(user.id, form_data.model_dump())
    if user:
        return user.settings
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


# llens(#55): 本人による info の読み書き endpoint(/user/info, /user/info/update)は撤去。
# info は外部システムへの参照権限マーカー(integrity 必須)のため編集は admin 専有
# (permissions.md G3)。唯一の消費者だった Settings の geolocation 保存も機能ごと撤去。


############################
# AdditionalUserMetaFields
############################


@router.get("/meta/fields")
async def get_additional_user_meta_fields(
    request: Request, user=Depends(get_admin_user)
):
    """llens(#55): 宣言済みの追加 meta キー(仮想カラム)定義を admin UI へ配る。"""
    return request.app.state.config.ADDITIONAL_USER_META


############################
# GetUserById
############################


# llens(wire v2): USER_POOL プレゼンス由来の active フィールドは socket 基盤と共に撤去。
class UserResponse(BaseModel):
    name: str
    profile_image_url: str


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(user_id: str, user=Depends(get_verified_user)):
    # Check if user_id is a shared chat
    # If it is, get the user_id from the chat
    if user_id.startswith("shared-"):
        chat_id = user_id.replace("shared-", "")
        chat = Chats.get_chat_by_id(chat_id)
        if chat:
            user_id = chat.user_id
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.USER_NOT_FOUND,
            )

    user = Users.get_user_by_id(user_id)

    if user:
        return UserResponse(
            **{
                "name": user.name,
                "profile_image_url": user.profile_image_url,
            }
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# UpdateUserById
############################


@router.post("/{user_id}/update", response_model=Optional[UserModel])
async def update_user_by_id(
    request: Request,
    user_id: str,
    form_data: UserUpdateForm,
    session_user=Depends(get_admin_user),
):
    user = Users.get_user_by_id(user_id)

    if user:
        if form_data.email.lower() != user.email:
            email_user = Users.get_user_by_email(form_data.email.lower())
            if email_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.EMAIL_TAKEN,
                )

        if form_data.password:
            hashed = get_password_hash(form_data.password)
            log.debug(f"hashed: {hashed}")
            Auths.update_user_password_by_id(user_id, hashed)

        update_payload = {
            "name": form_data.name,
            "email": form_data.email.lower(),
            "profile_image_url": form_data.profile_image_url,
        }

        # llens(#55): 追加 meta は宣言済みキーのみ受理(詳細は merge_additional_user_meta)
        if form_data.info is not None:
            update_payload["info"] = merge_additional_user_meta(
                request.app.state.config.ADDITIONAL_USER_META,
                user.info,
                form_data.info,
            )

        Auths.update_email_by_id(user_id, form_data.email.lower())
        updated_user = Users.update_user_by_id(user_id, update_payload)

        if updated_user:
            return updated_user

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.USER_NOT_FOUND,
    )


# llens(#31): DELETE /{user_id}(user の hard delete)はエンドポイントごと撤去。
# チャットの物理削除(監査証憑の破壊)・file の孤児化を伴うため、無効化(role=disabled)で
# 代替する。FE 導線は #69 で撤去済み(暫定運用だった role=pending 落としの置き換え)。
