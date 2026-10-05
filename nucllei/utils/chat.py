import logging
import sys

from typing import Any
import inspect

from fastapi import Request
from starlette.responses import StreamingResponse


from nucllei.functions import generate_function_chat_completion

from nucllei.routers.openai import (
    generate_chat_completion as generate_openai_chat_completion,
)

from nucllei.routers.ollama import (
    generate_chat_completion as generate_ollama_chat_completion,
)

from nucllei.routers.pipelines import (
    process_pipeline_outlet_filter,
)

from nucllei.models.functions import Functions


from nucllei.utils.plugin import load_function_module_by_id
from nucllei.utils.models import get_all_models, check_model_access
from nucllei.utils.payload import convert_payload_openai_to_ollama
from nucllei.utils.response import (
    convert_response_ollama_to_openai,
    convert_streaming_response_ollama_to_openai,
)
from nucllei.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)

from nucllei.env import SRC_LOG_LEVELS, GLOBAL_LOG_LEVEL

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
):
    log.debug(f"generate_chat_completion: {form_data}")

    if hasattr(request.state, "metadata"):
        if "metadata" not in form_data:
            form_data["metadata"] = request.state.metadata
        else:
            form_data["metadata"] = {
                **form_data["metadata"],
                **request.state.metadata,
            }

    models = request.app.state.MODELS

    model_id = form_data["model"]
    model = models.get(model_id)
    if model is None:
        # llens(#107 仕様変更 2026-08-14): 無効 preset の agent session 子ターン
        # (初回 + tool ループ追撃)。is_active=False の preset は MODELS に載らない
        # ため、subagent が合成して metadata.model に積んだ dict を使う。
        # metadata.model はサーバー内部経路しか書かない(/api/chat/completions は
        # metadata を全置換してから来る)ので client 制御にはならないが、
        # agent_session マーカ + id 一致まで要求して経路を子ターンに閉じる。
        # 下の check_model_access は DB 行(is_active 非依存)で従来どおり効く。
        metadata = form_data.get("metadata") or {}
        candidate = metadata.get("model")
        if (
            metadata.get("agent_session")
            and candidate
            and candidate.get("id") == model_id
        ):
            model = candidate
        else:
            raise Exception("Model not found")

    # Check if user has access to the model
    if user.role == "user":
        check_model_access(user, model)

    # llens(#48): arena(evaluation)モデルは撲滅済み。owned_by=="arena" の分岐を撤去。

    if model.get("pipe"):
        return await generate_function_chat_completion(
            request, form_data, user=user, models=models
        )
    if model.get("owned_by") == "ollama":
        # Using /ollama/api/chat endpoint
        form_data = convert_payload_openai_to_ollama(form_data)
        response = await generate_ollama_chat_completion(
            request=request,
            form_data=form_data,
            user=user,
        )
        if form_data.get("stream"):
            response.headers["content-type"] = "text/event-stream"
            return StreamingResponse(
                convert_streaming_response_ollama_to_openai(response),
                headers=dict(response.headers),
                background=response.background,
            )
        else:
            return convert_response_ollama_to_openai(response)
    else:
        return await generate_openai_chat_completion(
            request=request,
            form_data=form_data,
            user=user,
        )


chat_completion = generate_chat_completion


def _make_buffered_event_handlers():
    """completed / actions 経路用のイベントバッファ(llens(wire v2))。

    これらのハンドラは completion タスク終了後の別 POST 内で走るため、
    ライブなイベントバス(completion_events)は存在しない。__event_emitter__ は
    ローカルの events リストへ積むだけとし、呼び出し側が応答 JSON の "events" に
    同梱して返す(FE が応答から適用する)。

    completed/actions 経路では __event_call__ は使えない(バッファ応答のため、
    クライアントとの双方向往復が成立しない)— 呼ばれたら warning を出して
    None を返す no-op stub を渡す。
    """
    events: list = []

    async def __event_emitter__(event_data):
        events.append(event_data)

    async def __event_call__(event_data):
        log.warning(
            "__event_call__ は completed/actions 経路では使えない(バッファ応答のため)。"
            "None を返す。"
        )
        return None

    return events, __event_emitter__, __event_call__


async def chat_completed(request: Request, form_data: dict, user: Any):
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    models = request.app.state.MODELS

    data = form_data
    model_id = data["model"]
    if model_id not in models:
        raise Exception("Model not found")

    model = models[model_id]

    try:
        data = await process_pipeline_outlet_filter(request, data, user, models)
    except Exception as e:
        return Exception(f"Error: {e}")

    metadata = {
        "chat_id": data["chat_id"],
        "message_id": data["id"],
        "user_id": user.id,
    }

    events, __event_emitter__, __event_call__ = _make_buffered_event_handlers()

    extra_params = {
        "__event_emitter__": __event_emitter__,
        "__event_call__": __event_call__,
        "__user__": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            # llens(#55): 追加 meta(外部参照権限マーカー)を opaque に露出
            "info": getattr(user, "info", None) or {},
        },
        "__metadata__": metadata,
        "__request__": request,
        "__model__": model,
    }

    try:
        filter_functions = [
            Functions.get_function_by_id(filter_id)
            for filter_id in get_sorted_filter_ids(model)
        ]

        result, _ = await process_filter_functions(
            request=request,
            filter_functions=filter_functions,
            filter_type="outlet",
            form_data=data,
            extra_params=extra_params,
        )
        if isinstance(result, dict):
            result = {**result, "events": events}
        return result
    except Exception as e:
        return Exception(f"Error: {e}")


async def chat_action(request: Request, action_id: str, form_data: dict, user: Any):
    if "." in action_id:
        action_id, sub_action_id = action_id.split(".")
    else:
        sub_action_id = None

    action = Functions.get_function_by_id(action_id)
    if not action:
        raise Exception(f"Action not found: {action_id}")

    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    models = request.app.state.MODELS

    data = form_data
    model_id = data["model"]

    if model_id not in models:
        raise Exception("Model not found")
    model = models[model_id]

    events, __event_emitter__, __event_call__ = _make_buffered_event_handlers()

    if action_id in request.app.state.FUNCTIONS:
        function_module = request.app.state.FUNCTIONS[action_id]
    else:
        function_module, _, _ = load_function_module_by_id(action_id)
        request.app.state.FUNCTIONS[action_id] = function_module

    if hasattr(function_module, "valves") and hasattr(function_module, "Valves"):
        valves = Functions.get_function_valves_by_id(action_id)
        function_module.valves = function_module.Valves(**(valves if valves else {}))

    if hasattr(function_module, "action"):
        try:
            action = function_module.action

            # Get the signature of the function
            sig = inspect.signature(action)
            params = {"body": data}

            # Extra parameters to be passed to the function
            extra_params = {
                "__model__": model,
                "__id__": sub_action_id if sub_action_id is not None else action_id,
                "__event_emitter__": __event_emitter__,
                "__event_call__": __event_call__,
                "__request__": request,
            }

            # Add extra params in contained in function signature
            for key, value in extra_params.items():
                if key in sig.parameters:
                    params[key] = value

            if "__user__" in sig.parameters:
                __user__ = {
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "role": user.role,
                    # llens(#55): 追加 meta(外部参照権限マーカー)を opaque に露出
                    "info": getattr(user, "info", None) or {},
                }

                try:
                    if hasattr(function_module, "UserValves"):
                        __user__["valves"] = function_module.UserValves(
                            **Functions.get_user_valves_by_id_and_user_id(
                                action_id, user.id
                            )
                        )
                except Exception as e:
                    log.exception(f"Failed to get user values: {e}")

                params = {**params, "__user__": __user__}

            if inspect.iscoroutinefunction(action):
                data = await action(**params)
            else:
                data = action(**params)

        except Exception as e:
            return Exception(f"Error: {e}")

    # llens(wire v2): バッファしたイベントを応答 JSON に同梱(FE が適用する)。
    # action が None を返す(emit だけして戻り値なし)場合もイベントは届ける。
    if isinstance(data, dict):
        return {**data, "events": events}
    if data is None:
        return {"events": events}
    return data
