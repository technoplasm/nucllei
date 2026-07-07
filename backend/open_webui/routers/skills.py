from typing import Optional

from open_webui.models.skills import (
    SkillForm,
    SkillUserResponse,
    SkillModel,
    Skills,
    normalize_command,
)
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, status, Request
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access, has_permission

router = APIRouter()

############################
# GetSkills
############################


@router.get("/", response_model=list[SkillModel])
async def get_skills(user=Depends(get_verified_user)):
    if user.role == "admin":
        skills = Skills.get_skills()
    else:
        skills = Skills.get_skills_by_user_id(user.id, "read")

    return skills


@router.get("/list", response_model=list[SkillUserResponse])
async def get_skill_list(user=Depends(get_verified_user)):
    if user.role == "admin":
        skills = Skills.get_skills()
    else:
        skills = Skills.get_skills_by_user_id(user.id, "write")

    return skills


############################
# CreateNewSkill
############################


def _check_write_permission(request: Request, form_data: SkillForm, user):
    """作成/更新の権限ゲート。

    - tool_ids 非空: admin のみ(新設ゲート。tool 紐づき skill は admin 専用)。
    - tool_ids 空: 既存パターン(admin バイパス or workspace.skills 権限)。
    """
    if form_data.tool_ids:
        if user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.UNAUTHORIZED,
            )
        return

    if user.role != "admin" and not has_permission(
        user.id, "workspace.skills", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


@router.post("/create", response_model=Optional[SkillModel])
async def create_new_skill(
    request: Request, form_data: SkillForm, user=Depends(get_verified_user)
):
    _check_write_permission(request, form_data, user)

    # llens(#43): id が唯一の可読キー兼発火キー(旧 command)。正規化して衝突判定。
    form_data.id = normalize_command(form_data.id)
    if not form_data.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Skill id is required"),
        )

    skill = Skills.get_skill_by_id(form_data.id)
    if skill is None:
        skill = Skills.insert_new_skill(user.id, form_data)

        if skill:
            return skill
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.ID_TAKEN,
    )


############################
# GetSkillById
############################


@router.get("/id/{id}", response_model=Optional[SkillModel])
async def get_skill_by_id(id: str, user=Depends(get_verified_user)):
    skill = Skills.get_skill_by_id(id)

    if skill:
        if (
            user.role == "admin"
            or skill.user_id == user.id
            or has_access(user.id, "read", skill.access_control)
        ):
            return skill
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateSkillById
############################


@router.post("/id/{id}/update", response_model=Optional[SkillModel])
async def update_skill_by_id(
    request: Request,
    id: str,
    form_data: SkillForm,
    user=Depends(get_verified_user),
):
    skill = Skills.get_skill_by_id(id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # Is the user the original creator, in a group with write access, or an admin
    if (
        skill.user_id != user.id
        and not has_access(user.id, "write", skill.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    # tool 紐づき skill(tool_ids 非空)への更新は admin のみ(新設ゲート)。
    _check_write_permission(request, form_data, user)

    # llens(#43): id は不変(可読 id の変更はフロントで削除→再作成)。ここでは中身のみ更新。
    skill = Skills.update_skill_by_id(id, form_data)
    if skill:
        return skill
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# DeleteSkillById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_skill_by_id(id: str, user=Depends(get_verified_user)):
    skill = Skills.get_skill_by_id(id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not has_access(user.id, "write", skill.access_control)
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Skills.delete_skill_by_id(id)
    return result
