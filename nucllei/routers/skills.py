from typing import Any, Optional

from pydantic import BaseModel

from nucllei.models.skills import (
    SkillFieldError,
    SkillForm,
    SkillUserResponse,
    SkillModel,
    Skills,
    HOOK_FIELDS,
    normalize_command,
    normalize_hooks,
)
from nucllei.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, status, Request
from nucllei.utils.auth import get_verified_user
from nucllei.utils.access_control import has_access, has_permission
from nucllei.utils.models import check_model_access

router = APIRouter()

# llens(#66、2026-07-10 改訂): skill editor は「私有ノート + 全体閲覧」モデル。
# - skill editor(workspace.skills 権限)= Skills メニュー表示 / 全 skill の閲覧(中身込み)/
#   作成(ただし必ず私有 = access_control={})/ 自分の skill のみ編集・削除。
# - ACL(公開・共有範囲)を触れるのは admin だけ — editor が勝手に public skill を
#   作れてはならない(当初の「共有レシピ帳」案はこの理由で撤回)。
# - tool_ids 付き skill は全操作 admin 専有(権力を運ぶのは tool — GHSA-4pcg 境界)。
#   before / after(読み込み時にサーバが実行する tool — llens#22)を 1 つでも持つものも同じ扱い。


def _is_skill_editor(request: Request, user) -> bool:
    return user.role == "admin" or has_permission(
        user.id, "workspace.skills", request.app.state.config.USER_PERMISSIONS
    )


def _normalize_hook_fields(form_data: SkillForm):
    """before / after を検証・正規化する(None = 省略はそのまま — 更新で既存値を保つ)。"""
    try:
        for field in HOOK_FIELDS:
            if getattr(form_data, field) is not None:
                setattr(
                    form_data, field, normalize_hooks(getattr(form_data, field), field)
                )
    except SkillFieldError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


def _require_editor(request: Request, user, *tool_id_sets):
    """editor ゲート。tool_id_sets(tool_ids・before・after)のいずれかが
    非空なら admin を要求する。"""
    if any(tool_id_sets) and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )
    if not _is_skill_editor(request, user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


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
async def get_skill_list(request: Request, user=Depends(get_verified_user)):
    # llens(#66): 管理一覧は editor 用 — 全 skill(中身込み)を返す。閲覧は全件、
    # 編集・削除は owner のみ(各 endpoint 側でゲート)。
    if not _is_skill_editor(request, user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )
    return Skills.get_skills()


############################
# CreateNewSkill
############################


@router.post("/create", response_model=Optional[SkillModel])
async def create_new_skill(
    request: Request, form_data: SkillForm, user=Depends(get_verified_user)
):
    _normalize_hook_fields(form_data)
    _require_editor(
        request, user, form_data.tool_ids, form_data.before, form_data.after
    )

    # editor の新規 skill は必ず私有(owner のみ)。公開・共有は admin の後続操作でだけ広がる。
    if user.role != "admin":
        form_data.access_control = {}

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
# PreviewSkill
############################


class SkillPreviewForm(BaseModel):
    # 編集中 (未保存) の本文と before / after
    content: str = ""
    before: Any = None
    after: Any = None
    # {{BASE_MODEL_ID}}・{{SKILLS}}・{{USER_RECIPES}} の元にする preset (省略で無し)
    model_id: Optional[str] = None


@router.post("/preview")
async def preview_skill(
    request: Request, form_data: SkillPreviewForm, user=Depends(get_verified_user)
):
    """skill の編集画面のプレビュー (2026-10-30 ken): 編集中の本文をリクエストした本人として展開し、
    before / after を書いた順に並べて返す (実行しない)。skill を編集できる人 (editor) だけ。
    model_id の preset は本人が使えるもの (admin は全部)。"""
    from nucllei.utils.prompt_preview import preview_skill as build_preview

    if not _is_skill_editor(request, user):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )
    try:
        before = normalize_hooks(form_data.before, "before")
        after = normalize_hooks(form_data.after, "after")
    except SkillFieldError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )
    model = None
    if form_data.model_id:
        model = request.app.state.MODELS.get(form_data.model_id)
        try:
            if model is None:
                raise Exception("Model not found")
            if user.role != "admin":
                check_model_access(user, model)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
    return await build_preview(user, form_data.content, before, after, model)


############################
# GetSkillById
############################


@router.get("/id/{id}", response_model=Optional[SkillModel])
async def get_skill_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    skill = Skills.get_skill_by_id(id)

    if skill:
        # 使える範囲(read ACL)か、編集のために開く editor
        if _is_skill_editor(request, user) or has_access(
            user.id, "read", skill.access_control
        ):
            return skill
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
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

    _normalize_hook_fields(form_data)
    # tool_ids / before / after は、既存・更新後のどちらかが非空なら admin 専有(editor による
    # 付与・剥奪・改変をすべて遮断)。before / after の省略は既存値を保つ。
    _require_editor(
        request,
        user,
        skill.tool_ids,
        form_data.tool_ids,
        skill.before,
        skill.after,
        form_data.before,
        form_data.after,
    )

    if user.role != "admin":
        # 編集できるのは自分の skill だけ(他人の skill は閲覧のみ)
        if skill.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        # ACL は admin 専有 — editor の更新では保存済みの値を温存する
        form_data.access_control = skill.access_control

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
async def delete_skill_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    skill = Skills.get_skill_by_id(id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    _require_editor(request, user, skill.tool_ids, skill.before, skill.after)

    # 削除も自分の skill だけ(他人の skill は閲覧のみ)
    if user.role != "admin" and skill.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Skills.delete_skill_by_id(id)
    return result
