import logging
from typing import Optional

from nucllei.models.functions import (
    FunctionForm,
    FunctionModel,
    FunctionResponse,
    Functions,
)
from nucllei.utils.plugin import load_function_module_by_id, replace_imports
from nucllei.config import CACHE_DIR
from nucllei.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, status
from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


router = APIRouter()


def validate_function_metadata(form_data: FunctionForm) -> None:
    """llens(#45): function の frontmatter 退役に伴い、title(name)/description は
    DB フィールドが唯一の真実源。name が空なら 400 で保存を拒否する(tool 版 #44 と同型)。
    description は任意 — 実行対象の選択は per-model 割り当てで決まり、
    description は一覧 UI 用メタデータにすぎない。"""
    if not (form_data.name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Function name is required",
        )


############################
# GetFunctions
############################


def get_function_module(request: Request, function_id: str):
    """app.state.FUNCTIONS キャッシュ越しに function モジュールを得る(未ロードならロード)。"""
    if function_id in request.app.state.FUNCTIONS:
        return request.app.state.FUNCTIONS[function_id]
    function_module, _, _ = load_function_module_by_id(function_id)
    request.app.state.FUNCTIONS[function_id] = function_module
    return function_module


def function_has_valves(request: Request, function_id: str) -> bool:
    """一覧 UI の Valves ボタン disable 用。ロード失敗は「設定不可」に倒す。"""
    try:
        return hasattr(get_function_module(request, function_id), "Valves")
    except Exception:
        log.exception(f"Failed to load the function by id {function_id}")
        return False


@router.get("/", response_model=list[FunctionResponse])
async def get_functions(request: Request, user=Depends(get_verified_user)):
    functions = Functions.get_functions()
    return [
        FunctionResponse(
            **function.model_dump(),
            has_valves=function_has_valves(request, function.id),
        )
        for function in functions
    ]


############################
# ExportFunctions
############################


@router.get("/export", response_model=list[FunctionModel])
async def export_functions(user=Depends(get_admin_user)):
    return Functions.get_functions()


############################
# CreateNewFunction
############################


@router.post("/create", response_model=Optional[FunctionResponse])
async def create_new_function(
    request: Request, form_data: FunctionForm, user=Depends(get_admin_user)
):
    if not form_data.id.isidentifier():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only alphanumeric characters and underscores are allowed in the id",
        )

    validate_function_metadata(form_data)

    form_data.id = form_data.id.lower()

    function = Functions.get_function_by_id(form_data.id)
    if function is None:
        try:
            form_data.content = replace_imports(form_data.content)
            function_module, function_type, _ = load_function_module_by_id(
                form_data.id,
                content=form_data.content,
            )

            FUNCTIONS = request.app.state.FUNCTIONS
            FUNCTIONS[form_data.id] = function_module

            function = Functions.insert_new_function(user.id, function_type, form_data)

            function_cache_dir = CACHE_DIR / "functions" / form_data.id
            function_cache_dir.mkdir(parents=True, exist_ok=True)

            if function:
                return function
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error creating function"),
                )
        except Exception as e:
            log.exception(f"Failed to create a new function: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(e),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


############################
# GetFunctionById
############################


@router.get("/id/{id}", response_model=Optional[FunctionModel])
async def get_function_by_id(id: str, user=Depends(get_admin_user)):
    function = Functions.get_function_by_id(id)

    if function:
        return function
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


# llens(#46): /toggle(is_active)・/toggle/global(is_global)は撤去。
# function の適用制御はモデルの filterIds / actionIds 割り当て一本。


############################
# UpdateFunctionById
############################


@router.post("/id/{id}/update", response_model=Optional[FunctionModel])
async def update_function_by_id(
    request: Request, id: str, form_data: FunctionForm, user=Depends(get_admin_user)
):
    validate_function_metadata(form_data)

    try:
        form_data.content = replace_imports(form_data.content)
        function_module, function_type, _ = load_function_module_by_id(
            id, content=form_data.content
        )

        FUNCTIONS = request.app.state.FUNCTIONS
        FUNCTIONS[id] = function_module

        updated = {**form_data.model_dump(exclude={"id"}), "type": function_type}
        log.debug(updated)

        function = Functions.update_function_by_id(id, updated)

        if function:
            return function
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating function"),
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# DeleteFunctionById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_function_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    result = Functions.delete_function_by_id(id)

    if result:
        FUNCTIONS = request.app.state.FUNCTIONS
        if id in FUNCTIONS:
            del FUNCTIONS[id]

    return result


############################
# GetFunctionValves
############################


@router.get("/id/{id}/valves", response_model=Optional[dict])
async def get_function_valves_by_id(id: str, user=Depends(get_admin_user)):
    function = Functions.get_function_by_id(id)
    if function:
        try:
            valves = Functions.get_function_valves_by_id(id)
            return valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(e),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# GetFunctionValvesSpec
############################


@router.get("/id/{id}/valves/spec", response_model=Optional[dict])
async def get_function_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    function = Functions.get_function_by_id(id)
    if function:
        function_module = get_function_module(request, id)

        if hasattr(function_module, "Valves"):
            Valves = function_module.Valves
            return Valves.schema()
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateFunctionValves
############################


@router.post("/id/{id}/valves/update", response_model=Optional[dict])
async def update_function_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_admin_user)
):
    function = Functions.get_function_by_id(id)
    if function:
        function_module = get_function_module(request, id)

        if hasattr(function_module, "Valves"):
            Valves = function_module.Valves

            try:
                # null = UI の「Default」。保存は明示したフィールドだけ(exclude_unset)—
                # 未設定フィールドは読み出し側の Valves(**保存値) でコードの既定値に
                # 埋まるので、コード側の既定変更(filter の priority 等)が効き続ける。
                form_data = {k: v for k, v in form_data.items() if v is not None}
                valves = Valves(**form_data)
                Functions.update_function_valves_by_id(
                    id, valves.model_dump(exclude_unset=True)
                )
                return valves.model_dump()
            except Exception as e:
                log.exception(f"Error updating function values by id {id}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(e),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


# UserValves(per-user 設定)の GET/spec/update は撤去済み(2026-10-05、
# feature-triage「Filter / tool 機構の縮小」— 設定 UI も利用者も無かった)。
