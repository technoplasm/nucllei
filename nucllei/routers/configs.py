from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict

from typing import Optional

from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.config import get_config, save_config
from nucllei.config import BannerModel
from nucllei.models.functions import Functions

from nucllei.utils.tools import get_tool_server_data, get_tool_servers_data


router = APIRouter()


############################
# AttachmentConfig
# llens(#67): 添付ファイル受け入れゲート(#52)の設定 API。retrieval router
# 撤去に伴い /retrieval/config から移設。
# llens(#69): グローバル Attachment Function(FUNCTION_ID)を追加。
############################


class AttachmentConfigForm(BaseModel):
    ATTACHMENT_MAX_SIZE: Optional[int]
    ATTACHMENT_MAX_COUNT: Optional[int]
    ATTACHMENT_ALLOWED_EXTENSIONS: list[str]
    ATTACHMENT_FUNCTION_ID: str = ""


def _attachment_config(request: Request):
    return {
        "ATTACHMENT_MAX_SIZE": request.app.state.config.ATTACHMENT_MAX_SIZE,
        "ATTACHMENT_MAX_COUNT": request.app.state.config.ATTACHMENT_MAX_COUNT,
        "ATTACHMENT_ALLOWED_EXTENSIONS": request.app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS,
        "ATTACHMENT_FUNCTION_ID": request.app.state.config.ATTACHMENT_FUNCTION_ID,
    }


@router.get("/attachment", response_model=AttachmentConfigForm)
async def get_attachment_config(request: Request, user=Depends(get_admin_user)):
    return _attachment_config(request)


@router.post("/attachment", response_model=AttachmentConfigForm)
async def set_attachment_config(
    request: Request, form_data: AttachmentConfigForm, user=Depends(get_admin_user)
):
    # llens(#69): function_id は保存時に実在検証(空 = 未設定は常に可)。
    # 実在しない/type 違いの id を DB に焼くと dispatch 側で黙って不発になるため 400 で弾く。
    function_id = form_data.ATTACHMENT_FUNCTION_ID.strip()
    if function_id:
        function = Functions.get_function_by_id(function_id)
        if not function or function.type != "attachment":
            raise HTTPException(
                status_code=400,
                detail=f"Attachment function not found: {function_id}",
            )

    request.app.state.config.ATTACHMENT_MAX_SIZE = form_data.ATTACHMENT_MAX_SIZE
    request.app.state.config.ATTACHMENT_MAX_COUNT = form_data.ATTACHMENT_MAX_COUNT
    request.app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS = (
        form_data.ATTACHMENT_ALLOWED_EXTENSIONS
    )
    request.app.state.config.ATTACHMENT_FUNCTION_ID = function_id
    return _attachment_config(request)


############################
# ImportConfig
############################


class ImportConfigForm(BaseModel):
    config: dict


@router.post("/import", response_model=dict)
async def import_config(form_data: ImportConfigForm, user=Depends(get_admin_user)):
    save_config(form_data.config)
    return get_config()


############################
# ExportConfig
############################


@router.get("/export", response_model=dict)
async def export_config(user=Depends(get_admin_user)):
    return get_config()


############################
# ToolServers Config
############################


class ToolServerConnection(BaseModel):
    url: str
    path: str
    auth_type: Optional[str]
    key: Optional[str]
    config: Optional[dict]

    model_config = ConfigDict(extra="allow")


class ToolServersConfigForm(BaseModel):
    TOOL_SERVER_CONNECTIONS: list[ToolServerConnection]


@router.get("/tool_servers", response_model=ToolServersConfigForm)
async def get_tool_servers_config(request: Request, user=Depends(get_admin_user)):
    return {
        "TOOL_SERVER_CONNECTIONS": request.app.state.config.TOOL_SERVER_CONNECTIONS,
    }


@router.post("/tool_servers", response_model=ToolServersConfigForm)
async def set_tool_servers_config(
    request: Request,
    form_data: ToolServersConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.TOOL_SERVER_CONNECTIONS = [
        connection.model_dump() for connection in form_data.TOOL_SERVER_CONNECTIONS
    ]

    request.app.state.TOOL_SERVERS = await get_tool_servers_data(
        request.app.state.config.TOOL_SERVER_CONNECTIONS
    )

    return {
        "TOOL_SERVER_CONNECTIONS": request.app.state.config.TOOL_SERVER_CONNECTIONS,
    }


@router.post("/tool_servers/verify")
async def verify_tool_servers_config(
    request: Request, form_data: ToolServerConnection, user=Depends(get_admin_user)
):
    """
    Verify the connection to the tool server.
    """
    try:

        token = None
        if form_data.auth_type == "bearer":
            token = form_data.key
        elif form_data.auth_type == "session":
            token = request.state.token.credentials

        url = f"{form_data.url}/{form_data.path}"
        return await get_tool_server_data(token, url)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to connect to the tool server: {str(e)}",
        )


############################
# SetDefaultModels
############################
class ModelsConfigForm(BaseModel):
    DEFAULT_MODELS: Optional[str]
    MODEL_ORDER_LIST: Optional[list[str]]


@router.get("/models", response_model=ModelsConfigForm)
async def get_models_config(request: Request, user=Depends(get_admin_user)):
    return {
        "DEFAULT_MODELS": request.app.state.config.DEFAULT_MODELS,
        "MODEL_ORDER_LIST": request.app.state.config.MODEL_ORDER_LIST,
    }


@router.post("/models", response_model=ModelsConfigForm)
async def set_models_config(
    request: Request, form_data: ModelsConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.DEFAULT_MODELS = form_data.DEFAULT_MODELS
    request.app.state.config.MODEL_ORDER_LIST = form_data.MODEL_ORDER_LIST
    return {
        "DEFAULT_MODELS": request.app.state.config.DEFAULT_MODELS,
        "MODEL_ORDER_LIST": request.app.state.config.MODEL_ORDER_LIST,
    }


class PromptSuggestion(BaseModel):
    title: list[str]
    content: str


class SetDefaultSuggestionsForm(BaseModel):
    suggestions: list[PromptSuggestion]


@router.post("/suggestions", response_model=list[PromptSuggestion])
async def set_default_suggestions(
    request: Request,
    form_data: SetDefaultSuggestionsForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    request.app.state.config.DEFAULT_PROMPT_SUGGESTIONS = data["suggestions"]
    return request.app.state.config.DEFAULT_PROMPT_SUGGESTIONS


############################
# SetBanners
############################


class SetBannersForm(BaseModel):
    banners: list[BannerModel]


@router.post("/banners", response_model=list[BannerModel])
async def set_banners(
    request: Request,
    form_data: SetBannersForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    request.app.state.config.BANNERS = data["banners"]
    return request.app.state.config.BANNERS


@router.get("/banners", response_model=list[BannerModel])
async def get_banners(
    request: Request,
    user=Depends(get_verified_user),
):
    return request.app.state.config.BANNERS
