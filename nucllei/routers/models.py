from typing import Optional

from nucllei.models.models import (
    ModelForm,
    ModelModel,
    ModelResponse,
    ModelUserResponse,
    Models,
)
from nucllei.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, status


from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.utils.access_control import has_access


router = APIRouter()


###########################
# GetModels
###########################


@router.get("/", response_model=list[ModelUserResponse])
async def get_models(id: Optional[str] = None, user=Depends(get_verified_user)):
    # llens(#66): 非 admin は read ACL(picker 表示)で絞る。定義の変更は admin 専有。
    if user.role == "admin":
        return Models.get_models()
    else:
        return Models.get_models_by_user_id(user.id, "read")


# llens(#82): 旧 GET /base(base_model_id が null の Models 行 = 生モデル直接編集の産物)は
# 概念ごと廃止 — 行は base_model_id 必須になり、この一覧は常に空になるため endpoint も撤去。
# 接続由来の生モデル一覧(preset の base 選択用)は main.py の GET /api/models/base。


############################
# CreateNewModel
############################


# llens(#66): model 定義の作成・変更・削除は admin 専有(workspace.models 権限は廃止)。
# llens(#82): Models 行は base_model_id 必須(= preset のみ)。生モデル(接続由来)の id に
# base 無し行を作る旧経路は「生モデルを普通のモデルとして露出させる」ため概念ごと廃止。
def _require_base_model_id(form_data: ModelForm):
    if not form_data.base_model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="モデルはベースモデルを指定した preset として作成してください(生モデルの直接登録・編集はできません)。",
        )


@router.post("/create", response_model=Optional[ModelModel])
async def create_new_model(
    request: Request,
    form_data: ModelForm,
    user=Depends(get_admin_user),
):
    _require_base_model_id(form_data)

    model = Models.get_model_by_id(form_data.id)
    if model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.MODEL_ID_TAKEN,
        )

    else:
        model = Models.insert_new_model(form_data, user.id)
        if model:
            return model
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.DEFAULT(),
            )


###########################
# GetModelById
###########################


# Note: We're not using the typical url path param here, but instead using a query parameter to allow '/' in the id
@router.get("/model", response_model=Optional[ModelResponse])
async def get_model_by_id(id: str, user=Depends(get_verified_user)):
    model = Models.get_model_by_id(id)
    if model:
        if user.role == "admin" or has_access(user.id, "read", model.access_control):
            return model
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# ToggelModelById
############################


@router.post("/model/toggle", response_model=Optional[ModelResponse])
async def toggle_model_by_id(id: str, user=Depends(get_admin_user)):
    model = Models.get_model_by_id(id)
    if model:
        model = Models.toggle_model_by_id(id)

        if model:
            return model
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating function"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateModelById
############################


@router.post("/model/update", response_model=Optional[ModelModel])
async def update_model_by_id(
    id: str,
    form_data: ModelForm,
    user=Depends(get_admin_user),
):
    _require_base_model_id(form_data)

    model = Models.get_model_by_id(id)

    if not model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    model = Models.update_model_by_id(id, form_data)
    return model


############################
# DeleteModelById
############################


@router.delete("/model/delete", response_model=bool)
async def delete_model_by_id(id: str, user=Depends(get_admin_user)):
    model = Models.get_model_by_id(id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    result = Models.delete_model_by_id(id)
    return result


@router.delete("/delete/all", response_model=bool)
async def delete_all_models(user=Depends(get_admin_user)):
    result = Models.delete_all_models()
    return result
