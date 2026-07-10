from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse

from pydantic import BaseModel
from typing import Optional
import logging
import re

from open_webui.utils.chat import generate_chat_completion

# llens(#67): autocomplete / emoji / moa の生成 endpoint は撤去
from open_webui.utils.task import (
    title_generation_template,
    image_prompt_generation_template,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.constants import TASKS

from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.utils.task import get_task_model_id

from open_webui.config import (
    DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
)
from open_webui.env import SRC_LOG_LEVELS, BYPASS_MODEL_ACCESS_CONTROL
from open_webui.utils.models import check_model_access


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


def _check_task_model_access(user, models: dict, model_id: str) -> None:
    """llens(#66 G2): task endpoint も model の read ACL を検証する。

    従来は存在チェックのみで、picker に出ないモデル(read ACL 外)を task 経由で
    叩けた。チャット本線(main.py chat_completion)と同じ判定に合わせる。
    """
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        )
    if not BYPASS_MODEL_ACCESS_CONTROL and user.role == "user":
        try:
            check_model_access(user, models[model_id])
        except Exception:
            # 存在は明かさない(ACL 外 = 見えないモデルとして 404)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model not found",
            )


##################################
#
# Task Endpoints
#
##################################


@router.get("/config")
async def get_task_config(request: Request, user=Depends(get_verified_user)):
    return {
        "TASK_MODEL": request.app.state.config.TASK_MODEL,
        "TASK_MODEL_EXTERNAL": request.app.state.config.TASK_MODEL_EXTERNAL,
        "TITLE_GENERATION_PROMPT_TEMPLATE": request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE,
        "IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE": request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
        "ENABLE_TITLE_GENERATION": request.app.state.config.ENABLE_TITLE_GENERATION,
    }


class TaskConfigForm(BaseModel):
    TASK_MODEL: Optional[str]
    TASK_MODEL_EXTERNAL: Optional[str]
    ENABLE_TITLE_GENERATION: bool
    TITLE_GENERATION_PROMPT_TEMPLATE: str
    IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE: str


@router.post("/config/update")
async def update_task_config(
    request: Request, form_data: TaskConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.TASK_MODEL = form_data.TASK_MODEL
    request.app.state.config.TASK_MODEL_EXTERNAL = form_data.TASK_MODEL_EXTERNAL
    request.app.state.config.ENABLE_TITLE_GENERATION = form_data.ENABLE_TITLE_GENERATION
    request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE = (
        form_data.TITLE_GENERATION_PROMPT_TEMPLATE
    )

    request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE = (
        form_data.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE
    )

    return {
        "TASK_MODEL": request.app.state.config.TASK_MODEL,
        "TASK_MODEL_EXTERNAL": request.app.state.config.TASK_MODEL_EXTERNAL,
        "ENABLE_TITLE_GENERATION": request.app.state.config.ENABLE_TITLE_GENERATION,
        "TITLE_GENERATION_PROMPT_TEMPLATE": request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE,
        "IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE": request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE,
    }


@router.post("/title/completions")
async def generate_title(
    request: Request, form_data: dict, user=Depends(get_verified_user)
):

    if not request.app.state.config.ENABLE_TITLE_GENERATION:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"detail": "Title generation is disabled"},
        )

    models = request.app.state.MODELS

    model_id = form_data["model"]
    _check_task_model_access(user, models, model_id)

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(
        f"generating chat title using model {task_model_id} for user {user.email} "
    )

    if request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE != "":
        template = request.app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE

    messages = form_data["messages"]

    # Remove reasoning details from the messages
    for message in messages:
        message["content"] = re.sub(
            r"<details\s+type=\"reasoning\"[^>]*>.*?<\/details>",
            "",
            message["content"],
            flags=re.S,
        ).strip()

    content = title_generation_template(
        template,
        messages,
        {
            "name": user.name,
            "location": user.info.get("location") if user.info else None,
        },
    )

    payload = {
        "model": task_model_id,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        # llens(#68): Kimi は reasoning が常時出て title 1 つに 1000 トークン超を
        # 消費し得る(実測 reasoning_tokens=1002 で finish_reason=length →
        # content=null → 全チャット "New Chat" になっていた)。予算を 4000 に拡大。
        **(
            {"max_tokens": 4000}
            if models[task_model_id].get("owned_by") == "ollama"
            else {
                "max_completion_tokens": 4000,
            }
        ),
        "metadata": {
            **(request.state.metadata if hasattr(request.state, "metadata") else {}),
            "task": str(TASKS.TITLE_GENERATION),
            "task_body": form_data,
            "chat_id": form_data.get("chat_id", None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error("Exception occurred", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "An internal error has occurred."},
        )


@router.post("/image_prompt/completions")
async def generate_image_prompt(
    request: Request, form_data: dict, user=Depends(get_verified_user)
):
    models = request.app.state.MODELS

    model_id = form_data["model"]
    _check_task_model_access(user, models, model_id)

    # Check if the user has a custom task model
    # If the user has a custom task model, use that model
    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    log.debug(
        f"generating image prompt using model {task_model_id} for user {user.email} "
    )

    if request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE != "":
        template = request.app.state.config.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE
    else:
        template = DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE

    content = image_prompt_generation_template(
        template,
        form_data["messages"],
        user={
            "name": user.name,
        },
    )

    payload = {
        "model": task_model_id,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "metadata": {
            **(request.state.metadata if hasattr(request.state, "metadata") else {}),
            "task": str(TASKS.IMAGE_PROMPT_GENERATION),
            "task_body": form_data,
            "chat_id": form_data.get("chat_id", None),
        },
    }

    # Process the payload through the pipeline
    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
    except Exception as e:
        raise e

    try:
        return await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as e:
        log.error("Exception occurred", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "An internal error has occurred."},
        )
