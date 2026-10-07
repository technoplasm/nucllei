from typing import Optional
import logging

from nucllei.models.users import Users
from nucllei.models.groups import (
    Groups,
    GroupForm,
    GroupUpdateForm,
    GroupResponse,
)

from nucllei.constants import ERROR_MESSAGES
from nucllei.utils.group_names import (
    GROUP_NAME_CONFLICT_MESSAGE,
    GroupNameConflictError,
    GroupNameError,
    normalize_group_name_key,
    validate_group_name,
)
from fastapi import APIRouter, Depends, HTTPException, status

from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

############################
# Group name checks (#151)
############################


def _check_group_name(name: str, group_id: Optional[str] = None) -> str:
    """名前を検証(400)し、正規化キーが他グループと重複していないか(409)を見る。

    保存する名前(前後の空白を落としたもの)を返す。DB の unique index が最後の砦で、
    ここは利用者に分かるエラーを返すための事前チェック。
    """
    try:
        name = validate_group_name(name)
    except GroupNameError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    existing = Groups.get_group_by_name_key(normalize_group_name_key(name))
    if existing is not None and existing.id != group_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=GROUP_NAME_CONFLICT_MESSAGE
        )
    return name


############################
# GetFunctions
############################


@router.get("/", response_model=list[GroupResponse])
async def get_groups(user=Depends(get_verified_user)):
    if user.role == "admin":
        return Groups.get_groups()
    else:
        return Groups.get_groups_by_member_id(user.id)


############################
# CreateNewGroup
############################


@router.post("/create", response_model=Optional[GroupResponse])
async def create_new_group(form_data: GroupForm, user=Depends(get_admin_user)):
    form_data.name = _check_group_name(form_data.name)
    try:
        group = Groups.insert_new_group(user.id, form_data)
    except GroupNameConflictError:
        # 事前チェックと insert の間に同名が作られた(unique index 違反)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=GROUP_NAME_CONFLICT_MESSAGE
        )
    except Exception as e:
        log.exception(f"Error creating a new group: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )
    if not group:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error creating group"),
        )
    return group


############################
# GetGroupById
############################


@router.get("/id/{id}", response_model=Optional[GroupResponse])
async def get_group_by_id(id: str, user=Depends(get_admin_user)):
    group = Groups.get_group_by_id(id)
    if group:
        return group
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateGroupById
############################


@router.post("/id/{id}/update", response_model=Optional[GroupResponse])
async def update_group_by_id(
    id: str, form_data: GroupUpdateForm, user=Depends(get_admin_user)
):
    current = Groups.get_group_by_id(id)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )
    # 検証は改名時だけ(#151 以前の名前のままメンバー・権限を編集できるように)
    if form_data.name != current.name:
        form_data.name = _check_group_name(form_data.name, group_id=id)

    try:
        if form_data.user_ids:
            form_data.user_ids = Users.get_valid_user_ids(form_data.user_ids)

        group = Groups.update_group_by_id(id, form_data)
    except GroupNameConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=GROUP_NAME_CONFLICT_MESSAGE
        )
    except Exception as e:
        log.exception(f"Error updating group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )
    if not group:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error updating group"),
        )
    return group


############################
# DeleteGroupById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_group_by_id(id: str, user=Depends(get_admin_user)):
    try:
        result = Groups.delete_group_by_id(id)
        if result:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting group"),
            )
    except Exception as e:
        log.exception(f"Error deleting group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )
