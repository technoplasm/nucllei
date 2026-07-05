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

    skill = Skills.get_skill_by_command(form_data.command)
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
        detail=ERROR_MESSAGES.COMMAND_TAKEN,
    )


############################
# GetSkillByCommand
############################


@router.get("/command/{command}", response_model=Optional[SkillModel])
async def get_skill_by_command(command: str, user=Depends(get_verified_user)):
    skill = Skills.get_skill_by_command(command)

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
# UpdateSkillByCommand
############################


@router.post("/command/{command}/update", response_model=Optional[SkillModel])
async def update_skill_by_command(
    request: Request,
    command: str,
    form_data: SkillForm,
    user=Depends(get_verified_user),
):
    skill = Skills.get_skill_by_command(command)
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

    skill = Skills.update_skill_by_command(command, form_data)
    if skill:
        return skill
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# DeleteSkillByCommand
############################


@router.delete("/command/{command}/delete", response_model=bool)
async def delete_skill_by_command(command: str, user=Depends(get_verified_user)):
    skill = Skills.get_skill_by_command(command)
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

    result = Skills.delete_skill_by_command(command)
    return result
