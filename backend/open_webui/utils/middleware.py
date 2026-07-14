import time
import logging
import sys

import asyncio
import json
import ast

from uuid import uuid4
from typing import Optional
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request
from starlette.responses import StreamingResponse


from open_webui.models.chats import Chats
from open_webui.models.users import Users
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
    get_active_status_by_user_id,
)
from open_webui.routers.tasks import (
    generate_title,
    generate_image_prompt,
)
from open_webui.routers.images import image_generations, GenerateImageForm
from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
)

from open_webui.utils.webhook import post_webhook


from open_webui.models.users import UserModel
from open_webui.models.functions import Functions

from open_webui.retrieval.utils import get_sources_from_files


from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import (
    get_task_model_id,
)
from open_webui.utils.misc import (
    get_message_list,
    add_or_update_system_message,
    get_last_user_message,
    get_last_assistant_message,
    prepend_to_first_user_message_content,
    convert_logit_bias_input_to_json,
)
from open_webui.utils.skills import (
    apply_skill_command,
    build_load_skill_tool,
    current_skill_activation_seeds,
    skill_activation_seed_block,
)
from open_webui.utils.access_control import (
    has_read_access_to_file,
    has_read_access_to_collection,
)
from open_webui.utils.tools import get_tools
from open_webui.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)
from open_webui.utils.file_images import inline_internal_image_urls
from open_webui.utils.content_blocks import (
    convert_content_blocks_to_messages,
    serialize_text_blocks,
    snapshot_content_blocks,
)
from open_webui.utils.response_assembler import (
    CHANGED_TEXT,
    MISMATCH_MISSING_TOOL_CALLS,
    MISMATCH_UNEXPECTED_FINISH,
    ContentBlocksAssembler,
)

from open_webui.tasks import create_task

from open_webui.env import (
    SRC_LOG_LEVELS,
    GLOBAL_LOG_LEVEL,
    CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS,
    ENABLE_REALTIME_CHAT_SAVE,
)
from open_webui.constants import TASKS


logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


async def chat_image_generation_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    __event_emitter__ = extra_params["__event_emitter__"]
    await __event_emitter__(
        {
            "type": "status",
            "data": {"description": "Generating an image", "done": False},
        }
    )

    messages = form_data["messages"]
    user_message = get_last_user_message(messages)

    prompt = user_message

    if request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION:
        try:
            res = await generate_image_prompt(
                request,
                {
                    "model": form_data["model"],
                    "messages": messages,
                },
                user,
            )

            response = res["choices"][0]["message"]["content"]

            try:
                bracket_start = response.find("{")
                bracket_end = response.rfind("}") + 1

                if bracket_start == -1 or bracket_end == -1:
                    raise Exception("No JSON object found in the response")

                response = response[bracket_start:bracket_end]
                response = json.loads(response)
                prompt = response.get("prompt", [])
            except Exception:
                prompt = user_message

        except Exception as e:
            log.exception(e)
            prompt = user_message

    system_message_content = ""

    try:
        images = await image_generations(
            request=request,
            form_data=GenerateImageForm(**{"prompt": prompt}),
            user=user,
        )

        await __event_emitter__(
            {
                "type": "status",
                "data": {"description": "Generated an image", "done": True},
            }
        )

        await __event_emitter__(
            {
                "type": "files",
                "data": {
                    "files": [
                        {
                            "type": "image",
                            "url": image["url"],
                        }
                        for image in images
                    ]
                },
            }
        )

        system_message_content = "<context>User is shown the generated image, tell the user that the image has been generated</context>"
    except Exception as e:
        log.exception(e)
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "description": "An error occurred while generating an image",
                    "done": True,
                },
            }
        )

        system_message_content = "<context>Unable to generate an image, tell the user that an error occurred</context>"

    if system_message_content:
        form_data["messages"] = add_or_update_system_message(
            system_message_content, form_data["messages"]
        )

    return form_data


def _file_entry_is_readable(file: dict, user: UserModel) -> bool:
    file_type = file.get("type")

    # サーバー生成・インライン内容(この 1 リクエスト内で完結し DB 横断解決を伴わない)は許可。
    if file.get("docs") is not None:
        return True

    if file_type == "collection":
        # BYPASS モード: data.file_ids を直接展開する経路
        for fid in (file.get("data", {}) or {}).get("file_ids", []) or []:
            if not has_read_access_to_file(fid, user):
                return False

        if file.get("legacy") or file.get("collection_names"):
            names = file.get("collection_names", []) or []
        elif file.get("id"):
            names = [file.get("id")]
        else:
            names = []
        return all(has_read_access_to_collection(name, user) for name in names)

    if file.get("collection_name"):
        return has_read_access_to_collection(file.get("collection_name"), user)
    if file.get("id"):
        return has_read_access_to_file(file.get("id"), user)

    # id/collection を持たない(サーバー生成のインライン)項目は許可。
    return True


def get_model_context_size(request, model: dict) -> Optional[int]:
    """llens(#57): モデルの context 長(max_model_len)を解決する。

    SGLang の /v1/models が返す max_model_len は raw model
    (app.state.MODELS[id]["openai"])に保持されている。preset モデル
    (base_model_id 持ち)は raw を持たないため base モデルを辿る。
    """
    ctx = (model.get("openai") or {}).get("max_model_len")
    if ctx is None:
        base_id = (model.get("info") or {}).get("base_model_id")
        if base_id:
            base = request.app.state.MODELS.get(base_id) or {}
            ctx = (base.get("openai") or {}).get("max_model_len")
    return ctx


def filter_files_by_user_access(files: list, user: UserModel) -> list:
    """
    llens(#40, GHSA-vjqm/vrhc): チャットリクエストの file/collection 参照は
    クライアント制御(direct モードの model.meta.knowledge も含む)。要求ユーザーが
    読取権限を持たない項目をここで除外し、他ユーザーのファイル横断読取(IDOR)を防ぐ。
    """
    if not files:
        return files
    if user.role == "admin":
        return files

    accessible = []
    for file in files:
        if not isinstance(file, dict):
            continue
        if _file_entry_is_readable(file, user):
            accessible.append(file)
        else:
            log.warning(
                "filter_files_by_user_access: dropping inaccessible file reference "
                f"(user_id={user.id}, id={file.get('id')}, "
                f"collection_name={file.get('collection_name')}, type={file.get('type')})"
            )
    return accessible


async def chat_completion_files_handler(
    request: Request, body: dict, user: UserModel
) -> tuple[dict, dict[str, list]]:
    sources = []

    if files := body.get("metadata", {}).get("files", None):
        # RAG 撤去(#52): クエリ生成・ベクタ検索は廃止。添付は data.content を全文注入する。
        try:
            # Offload get_sources_from_files to a separate thread
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor() as executor:
                sources = await loop.run_in_executor(
                    executor,
                    lambda: get_sources_from_files(request=request, files=files),
                )
        except Exception as e:
            log.exception(e)

        log.debug(f"files:sources: {sources}")

    return body, {"sources": sources}


def apply_params_to_form_data(form_data, model):
    params = form_data.pop("params", {})
    if model.get("ollama"):
        form_data["options"] = params

        if "format" in params:
            form_data["format"] = params["format"]

        if "keep_alive" in params:
            form_data["keep_alive"] = params["keep_alive"]
    else:
        if "seed" in params and params["seed"] is not None:
            form_data["seed"] = params["seed"]

        if "stop" in params and params["stop"] is not None:
            form_data["stop"] = params["stop"]

        if "temperature" in params and params["temperature"] is not None:
            form_data["temperature"] = params["temperature"]

        if "max_tokens" in params and params["max_tokens"] is not None:
            form_data["max_tokens"] = params["max_tokens"]

        if "top_p" in params and params["top_p"] is not None:
            form_data["top_p"] = params["top_p"]

        if "frequency_penalty" in params and params["frequency_penalty"] is not None:
            form_data["frequency_penalty"] = params["frequency_penalty"]

        if "reasoning_effort" in params and params["reasoning_effort"] is not None:
            form_data["reasoning_effort"] = params["reasoning_effort"]

        if "logit_bias" in params and params["logit_bias"] is not None:
            try:
                form_data["logit_bias"] = json.loads(
                    convert_logit_bias_input_to_json(params["logit_bias"])
                )
            except Exception as e:
                print(f"Error parsing logit_bias: {e}")

    return form_data


def get_model_tool_ids(model) -> list[str]:
    """llens(#54): tool はモデル割り当て(meta.toolIds)一本 — サーバー側で解決する。"""
    return (model or {}).get("info", {}).get("meta", {}).get("toolIds", []) or []


async def process_chat_payload(request, form_data, user, metadata, model):

    form_data = apply_params_to_form_data(form_data, model)
    log.debug(f"form_data: {form_data}")

    # サーバーサイド `/command` 発火(issue #35)。フィルタや RAG より前に、
    # 合成の load_skill tool exchange(call + 展開済み content の result)を
    # ヒットした user メッセージの直後へ注入する。
    form_data, skill_activations = apply_skill_command(form_data, user, model)

    # 今回のターンの発火は UI 表示用に応答先頭の tool_calls ブロックとして seed する
    # (process_chat_response 側。synthetic マーカ付きでモデル履歴へは再送しない —
    # 実体は上で form_data["messages"] に注入済み)。result は tool ロード後に
    # 確定するため、metadata への搭載は後段(tools 合流の後)で行う。

    # llens(#40): クライアント供給の file/collection 参照を読取権限で絞る(IDOR 防止)。
    # 後段で追加されるサーバー生成項目より前に絞ることで、被害者ファイルを指す
    # 偽装 file_id / collection がソース解決に到達しないようにする。
    if form_data.get("files"):
        form_data["files"] = filter_files_by_user_access(form_data["files"], user)

    event_emitter = get_event_emitter(metadata)
    event_call = get_event_call(metadata)

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_call,
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
        # llens(#18-3): 会話スコープの id を直接受け取れるようにする(0.9 系 tool の
        # 外形互換。__metadata__ 経由でも取れるが、sandbox 等の自前ツールは
        # __chat_id__/__message_id__ を引数に取る)
        "__chat_id__": metadata.get("chat_id"),
        "__message_id__": metadata.get("message_id"),
    }

    # Initialize events to store additional event to be sent to the client
    # Initialize contexts and citation
    models = request.app.state.MODELS

    task_model_id = get_task_model_id(
        form_data["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    events = []
    sources = []

    user_message = get_last_user_message(form_data["messages"])
    model_knowledge = model.get("info", {}).get("meta", {}).get("knowledge", False)

    if model_knowledge:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": False,
                },
            }
        )

        knowledge_files = []
        for item in model_knowledge:
            if item.get("collection_name"):
                knowledge_files.append(
                    {
                        "id": item.get("collection_name"),
                        "name": item.get("name"),
                        "legacy": True,
                    }
                )
            elif item.get("collection_names"):
                knowledge_files.append(
                    {
                        "name": item.get("name"),
                        "type": "collection",
                        "collection_names": item.get("collection_names"),
                        "legacy": True,
                    }
                )
            else:
                knowledge_files.append(item)

        files = form_data.get("files", [])
        files.extend(knowledge_files)
        form_data["files"] = files

    # 送信された variables は本経路では使わないが、payload からは除く
    form_data.pop("variables", None)

    # Process the form_data through the pipeline
    try:
        form_data = await process_pipeline_inlet_filter(
            request, form_data, user, models
        )
    except Exception as e:
        raise e

    try:
        filter_functions = [
            Functions.get_function_by_id(filter_id)
            for filter_id in get_sorted_filter_ids(model)
        ]

        form_data, flags = await process_filter_functions(
            request=request,
            filter_functions=filter_functions,
            filter_type="inlet",
            form_data=form_data,
            extra_params=extra_params,
        )
    except Exception as e:
        raise Exception(f"Error: {e}")

    # llens(#23): code_interpreter 機能は撤去 — コード実行は tool(外部 sandbox)一本。
    # タグ検出による tool 外実行の裏道は残さない。
    features = form_data.pop("features", None)
    if features:
        if "image_generation" in features and features["image_generation"]:
            form_data = await chat_image_generation_handler(
                request, form_data, extra_params, user
            )

    # llens(#54): チャット単位の tool 切り替えは撤去 — tool はモデル割り当て
    # (meta.toolIds)一本。クライアント申告の tool_ids は読まずに捨て、
    # サーバー側で解決する(per-user の read ACL は get_tools が enforce)。
    form_data.pop("tool_ids", None)
    tool_ids = get_model_tool_ids(model)
    files = form_data.pop("files", None)

    # Remove files duplicates
    if files:
        files = list({json.dumps(f, sort_keys=True): f for f in files}.values())

    metadata = {
        **metadata,
        "tool_ids": tool_ids,
        "files": files,
    }
    form_data["metadata"] = metadata

    # Server side tools
    tool_ids = metadata.get("tool_ids", None)

    log.debug(f"{tool_ids=}")

    tools_dict = {}

    tools_extra_params = {
        **extra_params,
        "__model__": models[task_model_id],
        "__messages__": form_data["messages"],
        "__files__": metadata.get("files", []),
    }

    # llens(#28): get_tools はモジュール exec + DB read の同期処理。イベントループを
    # 塞がないようワーカースレッドで実行する(以下 3 箇所とも)。
    if tool_ids:
        tools_dict = await asyncio.to_thread(
            get_tools, request, tool_ids, user, tools_extra_params
        )

    # llens(#35 step2b): 発火済み skill の tool_ids を遅延ロードして tools に加える
    # (`/command` 入口。会話持続 = 毎リクエスト再導出なので以降のターンでも載り続ける)。
    # id は skill 行(tool 紐づき skill は admin のみ作成可)由来なので per-tool の
    # アクセス検証はスキップ — skill の read 可視性が認可の単位。
    skill_tool_ids = []
    for a in skill_activations:
        for tool_id in a["tool_ids"]:
            if tool_id not in skill_tool_ids:
                skill_tool_ids.append(tool_id)
    if skill_tool_ids:
        skill_tools = await asyncio.to_thread(
            get_tools,
            request,
            skill_tool_ids,
            user,
            tools_extra_params,
            enforce_access_control=False,
        )
        for name, tool in skill_tools.items():
            if name in tools_dict:
                log.warning(f"skill tool '{name}' collides with an existing tool")
                continue
            tools_dict[name] = tool

        # 合成 exchange の result に「導入された tool 名」を書き戻す
        # (result_message は form_data["messages"] 内の同一 dict)。
        for a in skill_activations:
            if not a["tool_ids"]:
                continue
            names = [
                name
                for name, tool in skill_tools.items()
                if tool.get("tool_id") in a["tool_ids"]
            ]
            a["result"] = json.dumps(
                {"result": a["content"], "tools": names}, ensure_ascii=False
            )
            a["result_message"]["content"] = a["result"]

    # 今回のターンの発火を UI seed 用に metadata へ(result 確定後)。
    current_skill_activations = current_skill_activation_seeds(skill_activations)
    if current_skill_activations:
        metadata["skill_activations"] = current_skill_activations

    # llens(#35 step2b): load_skill 発火時の tool 遅延取り込み(入口 2)。
    # tools_dict(= metadata["tools"])と form_data["tools"] は native ループが
    # 毎イテレーション参照する同一オブジェクトなので、ここで足せば次の
    # イテレーションからモデルに見える。
    async def inject_skill_tools(skill):
        loaded = []
        skill_tools = await asyncio.to_thread(
            get_tools,
            request,
            skill.tool_ids,
            user,
            tools_extra_params,
            enforce_access_control=False,
        )
        for name, tool in skill_tools.items():
            if name in tools_dict:
                continue
            tools_dict[name] = tool
            if "tools" in form_data:
                form_data["tools"].append(
                    {"type": "function", "function": tool.get("spec", {})}
                )
            loaded.append(name)
        return loaded

    # llens(#35 step2a): preset に紐づく skill の manifest を単一擬似 tool
    # load_skill として常載する(tool result = 展開済み content)。
    load_skill_tool = build_load_skill_tool(model, user, load_tools=inject_skill_tools)
    if load_skill_tool is not None:
        if "load_skill" in tools_dict:
            log.warning(
                "tool 'load_skill' is shadowed by the skill manifest pseudo-tool"
            )
        tools_dict["load_skill"] = load_skill_tool

    if tools_dict:
        # llens: native function calling のみサポート(issue #18)。
        # ツールがあれば常にモデルへ tool spec を渡す(function_calling パラメータは無視)。
        metadata["tools"] = tools_dict
        form_data["tools"] = [
            {"type": "function", "function": tool.get("spec", {})}
            for tool in tools_dict.values()
        ]

    try:
        form_data, flags = await chat_completion_files_handler(request, form_data, user)
        sources.extend(flags.get("sources", []))
    except Exception as e:
        log.exception(e)

    # If context is not empty, insert it into the messages
    if len(sources) > 0:
        context_string = ""
        citated_file_idx = {}
        for _, source in enumerate(sources, 1):
            if "document" in source:
                for doc_context, doc_meta in zip(
                    source["document"], source["metadata"]
                ):
                    file_id = doc_meta.get("file_id")
                    if file_id not in citated_file_idx:
                        citated_file_idx[file_id] = len(citated_file_idx) + 1
                    # llens(#52): file_id と抽出状態(成功/失敗/未抽出)をタグに載せ、モデルが
                    # 状態を理解し file_id を tool で扱えるようにする。
                    status = doc_meta.get("extraction_status")
                    attrs = f'id="{citated_file_idx[file_id]}"'
                    if file_id:
                        attrs += f' file_id="{file_id}"'
                    if status == "failed":
                        attrs += ' extraction="failed"'
                        body = doc_context or ""
                    elif not (doc_context or "").strip() and status != "success":
                        attrs += ' extraction="none"'
                        body = "(このファイルはテキスト抽出されていません。file_id を使って tool で読めます)"
                    else:
                        body = doc_context
                    context_string += f"<source {attrs}>{body}</source>\n"

        context_string = context_string.strip()
        prompt = get_last_user_message(form_data["messages"])

        if prompt is None:
            raise Exception("No user message found")

        # llens(#52): RAG_TEMPLATE の「包む機構」は廃止。抽出内容(<source> タグ)は
        # Attachment が完全に制御するため、指示スキャフォールドで包まず context_string を
        # そのまま注入する。参照の扱い(引用ルール等)が要れば preset system prompt(prism)が持つ。
        # Workaround for Ollama 2.0+ system prompt issue
        if model.get("owned_by") == "ollama":
            form_data["messages"] = prepend_to_first_user_message_content(
                context_string,
                form_data["messages"],
            )
        else:
            form_data["messages"] = add_or_update_system_message(
                context_string,
                form_data["messages"],
            )

    # llens(#69): 画像添付は files 経由に統一。内部 file URL("/api/v1/files/{id}/content")
    # の image_url は air-gapped のためモデル側から fetch できない — モデルへ出る直前の
    # ここで base64 data URL に変換する。native tool ループの再送(process_chat_response)も
    # form_data["messages"] を再利用するため、変換はこの 1 箇所で足りる。
    form_data["messages"] = await inline_internal_image_urls(
        form_data["messages"], user
    )

    # llens(#57): streaming でも実測 usage を受け取る。OpenAI 互換サーバーは
    # stream_options.include_usage が無いと usage チャンクを送らない(SGLang 実測済み)。
    if form_data.get("stream"):
        stream_options = form_data.get("stream_options") or {}
        stream_options["include_usage"] = True
        form_data["stream_options"] = stream_options

    # If there are citations, add them to the data_items
    sources = [source for source in sources if source.get("source", {}).get("name", "")]

    if len(sources) > 0:
        events.append({"sources": sources})

    if model_knowledge:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": True,
                    "hidden": True,
                },
            }
        )

    return form_data, metadata, events



def blocks_event_data(content_blocks) -> dict:
    """content_blocks の現在形を chat:completion イベント / 永続化ペイロードにする。"""
    return {
        "content": serialize_text_blocks(content_blocks),
        "content_blocks": snapshot_content_blocks(content_blocks),
    }


async def run_response_background_tasks(request, user, metadata, tasks, event_emitter):
    """応答確定後のバックグラウンドタスク(現状はタイトル生成のみ)。"""
    message_map = Chats.get_messages_by_chat_id(metadata["chat_id"])
    message = message_map.get(metadata["message_id"]) if message_map else None

    if not message:
        return

    messages = get_message_list(message_map, message.get("id"))

    if not (tasks and messages):
        return

    if TASKS.TITLE_GENERATION in tasks:
        if tasks[TASKS.TITLE_GENERATION]:
            res = await generate_title(
                request,
                {
                    "model": message["model"],
                    "messages": messages,
                    "chat_id": metadata["chat_id"],
                },
                user,
            )

            if res and isinstance(res, dict):
                if len(res.get("choices", [])) == 1:
                    # llens(#68): content は reasoning がトークン予算を
                    # 使い切ると null になる(.get の既定値は「キーが無い」
                    # 時しか効かない)。None はここで空文字に落とす。
                    title_string = (
                        res.get("choices", [])[0].get("message", {}).get("content")
                    ) or ""
                else:
                    title_string = ""

                title_string = title_string[
                    title_string.find("{") : title_string.rfind("}") + 1
                ]

                try:
                    title = json.loads(title_string).get("title", "New Chat")
                except Exception:
                    title = ""

                if not title:
                    title = messages[0].get("content", "New Chat")

                Chats.update_chat_title_by_id(metadata["chat_id"], title)

                await event_emitter(
                    {
                        "type": "chat:title",
                        "data": title,
                    }
                )
        elif len(messages) == 2:
            title = messages[0].get("content", "New Chat")

            Chats.update_chat_title_by_id(metadata["chat_id"], title)

            await event_emitter(
                {
                    "type": "chat:title",
                    "data": message.get("content", "New Chat"),
                }
            )


async def handle_non_streaming_response(
    request, response, user, metadata, model, tasks, event_emitter
):
    """非ストリーム応答の永続化・イベント払い出し・後処理。"""
    if "error" in response:
        error = response["error"].get("detail", response["error"])
        Chats.upsert_message_to_chat_by_id_and_message_id(
            metadata["chat_id"],
            metadata["message_id"],
            {
                "error": {"content": error},
            },
        )

    if "selected_model_id" in response:
        Chats.upsert_message_to_chat_by_id_and_message_id(
            metadata["chat_id"],
            metadata["message_id"],
            {
                "selectedModelId": response["selected_model_id"],
            },
        )

    # llens(#57): 非 stream 応答の実測 usage も永続化
    usage = response.get("usage", {})
    if usage and metadata.get("chat_id") and metadata.get("message_id"):
        await asyncio.to_thread(
            Chats.record_usage_by_id_and_message_id,
            metadata["chat_id"],
            metadata["message_id"],
            usage,
            context_size=get_model_context_size(request, model),
        )

    choices = response.get("choices", [])
    if choices and choices[0].get("message", {}).get("content"):
        content = response["choices"][0]["message"]["content"]

        if content:
            await event_emitter(
                {
                    "type": "chat:completion",
                    "data": response,
                }
            )

            title = Chats.get_chat_title_by_id(metadata["chat_id"])

            await event_emitter(
                {
                    "type": "chat:completion",
                    "data": {
                        "done": True,
                        "content": content,
                        "title": title,
                    },
                }
            )

            # Save message in the database
            Chats.upsert_message_to_chat_by_id_and_message_id(
                metadata["chat_id"],
                metadata["message_id"],
                {
                    "content": content,
                },
            )

            # Send a webhook notification if the user is not active
            if get_active_status_by_user_id(user.id) is None:
                webhook_url = Users.get_user_webhook_url_by_id(user.id)
                if webhook_url:
                    await post_webhook(
                        request.app.state.WEBUI_NAME,
                        webhook_url,
                        f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                        {
                            "action": "chat",
                            "message": content,
                            "title": title,
                            "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                        },
                    )

            await run_response_background_tasks(
                request, user, metadata, tasks, event_emitter
            )

    return response


async def consume_model_stream(
    request,
    response,
    assembler,
    *,
    model,
    metadata,
    filter_functions,
    extra_params,
    event_emitter,
):
    """モデル応答 SSE を読み切り assembler へ畳み込む。組めた tool calls を返す。

    行パース → stream filter → assembler(デルタ畳み込み)→ emit / 永続化。
    ストリーム終端で reasoning の閉じ規律③と finish_reason 整合ガード
    (llens #18-2)を適用する。
    """
    assembler.start_stream()

    async for line in response.body_iterator:
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        data = line

        # Skip empty lines
        if not data.strip():
            continue

        # "data:" is the prefix for each event
        if not data.startswith("data:"):
            continue

        # Remove the prefix
        data = data[len("data:") :].strip()

        try:
            data = json.loads(data)

            data, _ = await process_filter_functions(
                request=request,
                filter_functions=filter_functions,
                filter_type="stream",
                form_data=data,
                extra_params=extra_params,
            )

            if data:
                if "event" in data:
                    await event_emitter(data.get("event", {}))

                if "selected_model_id" in data:
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "selectedModelId": data["selected_model_id"],
                        },
                    )
                else:
                    choices = data.get("choices", [])
                    if not choices:
                        error = data.get("error", {})
                        if error:
                            await event_emitter(
                                {
                                    "type": "chat:completion",
                                    "data": {
                                        "error": error,
                                    },
                                }
                            )
                        usage = data.get("usage", {})
                        if usage:
                            # llens(#57): 実測 usage(真のコンテキスト量)を
                            # ターン/チャット単位で永続化。tool ループの
                            # 各呼び出しがここを通る(completion は加算)。
                            if metadata.get("chat_id") and metadata.get("message_id"):
                                await asyncio.to_thread(
                                    Chats.record_usage_by_id_and_message_id,
                                    metadata["chat_id"],
                                    metadata["message_id"],
                                    usage,
                                    context_size=get_model_context_size(
                                        request, model
                                    ),
                                )
                            await event_emitter(
                                {
                                    "type": "chat:completion",
                                    "data": {
                                        "usage": usage,
                                    },
                                }
                            )
                        continue

                    assembler.note_finish_reason(choices[0].get("finish_reason"))

                    changed = assembler.handle_delta(choices[0].get("delta", {}))

                    if changed:
                        snapshot = blocks_event_data(assembler.content_blocks)

                        # tool_calls / reasoning の変更は常に snapshot を emit する。
                        # text は ENABLE_REALTIME_CHAT_SAVE 時のみ DB 直書き
                        # (イベントはフレーム素通し)— v0.6.5 の挙動踏襲。
                        if changed - {CHANGED_TEXT}:
                            data = snapshot
                        if CHANGED_TEXT in changed:
                            if ENABLE_REALTIME_CHAT_SAVE:
                                Chats.upsert_message_to_chat_by_id_and_message_id(
                                    metadata["chat_id"],
                                    metadata["message_id"],
                                    snapshot,
                                )
                            else:
                                data = snapshot

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": data,
                    }
                )
        except Exception as e:
            done = "data: [DONE]" in line
            if done:
                pass
            else:
                log.debug("Error: ", e)
                continue

    response_tool_calls, mismatch = assembler.finish_stream()

    if mismatch == MISMATCH_MISSING_TOOL_CALLS:
        # llens(#18-2): "tool_calls" 終了なのにデルタから 1 つも組めていない
        # (チャンク境界などの取りこぼし = 無音で止まる最悪の失敗モード)—
        # 黙って通常終了せず、チャット内エラーで可視化する。
        log.error(
            "finish_reason=tool_calls but no tool calls were "
            "reconstructed from the stream (chunk boundary loss?)"
        )
        await event_emitter(
            {
                "type": "chat:completion",
                "data": {
                    "error": {
                        "content": (
                            "Model ended with tool_calls but no tool "
                            "call could be reconstructed from the "
                            "stream. The response may be incomplete."
                        )
                    },
                },
            }
        )
    elif mismatch == MISMATCH_UNEXPECTED_FINISH:
        # 逆方向の不整合は実行を止めるほどではない(実害はモデルの
        # 意図解釈のみ)— ログで観測可能にするに留める。
        log.warning(f"tool calls parsed but finish_reason={assembler.finish_reason!r}")

    if response.background:
        await response.background()

    return response_tool_calls


async def execute_tool_calls(response_tool_calls, tools):
    """tool call 群を順に実行し、tool result のリストを返す。"""
    results = []
    for tool_call in response_tool_calls:
        tool_call_id = tool_call.get("id", "")
        tool_name = tool_call.get("function", {}).get("name", "")

        tool_function_params = {}
        try:
            # json.loads cannot be used because some models do not produce valid JSON
            tool_function_params = ast.literal_eval(
                tool_call.get("function", {}).get("arguments", "{}")
            )
        except Exception as e:
            log.debug(e)
            # Fallback to JSON parsing
            try:
                tool_function_params = json.loads(
                    tool_call.get("function", {}).get("arguments", "{}")
                )
            except Exception as e:
                log.debug(
                    f"Error parsing tool call arguments: {tool_call.get('function', {}).get('arguments', '{}')}"
                )

        tool_result = None

        if tool_name not in tools:
            # llens(#18-2): tools 配列に無い tool の呼び出し。
            # 履歴に残った過去の tool call を真似るケース
            # (skill 削除・権限剥奪・分岐)や manifest 外の名前で
            # 起きうる。黙って None を返さず、モデルが軌道修正
            # できる明示メッセージを返す(例外にもしない)。
            log.warning(f"model called unavailable tool '{tool_name}'")
            tool_result = (
                f"tool not available: {tool_name}. "
                "Use only the tools currently provided."
            )
        else:
            tool = tools[tool_name]
            spec = tool.get("spec", {})

            try:
                allowed_params = (
                    spec.get("parameters", {}).get("properties", {}).keys()
                )

                tool_function_params = {
                    k: v
                    for k, v in tool_function_params.items()
                    if k in allowed_params
                }

                tool_function = tool["callable"]
                tool_result = await tool_function(**tool_function_params)

            except Exception as e:
                tool_result = str(e)

        tool_result_files = []
        if isinstance(tool_result, list):
            for item in tool_result:
                # check if string
                if isinstance(item, str) and item.startswith("data:"):
                    tool_result_files.append(item)
                    tool_result.remove(item)

        if isinstance(tool_result, dict) or isinstance(tool_result, list):
            tool_result = json.dumps(tool_result, indent=2)

        results.append(
            {
                "tool_call_id": tool_call_id,
                "content": tool_result,
                **({"files": tool_result_files} if tool_result_files else {}),
            }
        )

    return results


async def process_chat_response(
    request, response, form_data, user, metadata, model, events, tasks
):
    event_emitter = None
    event_caller = None
    if (
        "session_id" in metadata
        and metadata["session_id"]
        and "chat_id" in metadata
        and metadata["chat_id"]
        and "message_id" in metadata
        and metadata["message_id"]
    ):
        event_emitter = get_event_emitter(metadata)
        event_caller = get_event_call(metadata)

    # Non-streaming response
    if not isinstance(response, StreamingResponse):
        if event_emitter:
            return await handle_non_streaming_response(
                request, response, user, metadata, model, tasks, event_emitter
            )
        else:
            return response

    # Non standard response
    if not any(
        content_type in response.headers["Content-Type"]
        for content_type in ["text/event-stream", "application/x-ndjson"]
    ):
        return response

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
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
    filter_functions = [
        Functions.get_function_by_id(filter_id)
        for filter_id in get_sorted_filter_ids(model)
    ]

    # Streaming response
    if event_emitter and event_caller:
        task_id = str(uuid4())  # Create a unique task ID.
        model_id = form_data.get("model", "")

        Chats.upsert_message_to_chat_by_id_and_message_id(
            metadata["chat_id"],
            metadata["message_id"],
            {
                "model": model_id,
            },
        )

        # Handle as a background task
        async def post_response_handler(response, events):
            message = Chats.get_message_by_id_and_message_id(
                metadata["chat_id"], metadata["message_id"]
            )

            tool_calls = []

            last_assistant_message = None
            try:
                if form_data["messages"][-1]["role"] == "assistant":
                    last_assistant_message = get_last_assistant_message(
                        form_data["messages"]
                    )
            except Exception:
                pass

            # llens(#35): この turn の `/command` 発火を、モデル自律起動と同じ
            # 「View Result from load_skill」として応答先頭に表示する。
            seed_blocks = []
            if metadata.get("skill_activations"):
                seed_blocks.append(
                    skill_activation_seed_block(metadata["skill_activations"])
                )

            # llens(#17/#23): デルタ → content_blocks の組み立ては assembler
            # (utils/response_assembler.py)へ分離。reasoning は SGLang reasoning
            # parser 有効が接続契約で、常に reasoning_content デルタとして届く
            # (content 中のタグ検出経路は撤去済み — _memos/rendering-design.md §3)。
            assembler = ContentBlocksAssembler(
                initial_content=(
                    message.get("content", "")
                    if message
                    else last_assistant_message if last_assistant_message else ""
                ),
                seed_blocks=seed_blocks,
                tools=metadata.get("tools", {}),
            )

            try:
                for event in events:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": event,
                        }
                    )

                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            **event,
                        },
                    )

                stream_kwargs = {
                    "model": model,
                    "metadata": metadata,
                    "filter_functions": filter_functions,
                    "extra_params": extra_params,
                    "event_emitter": event_emitter,
                }

                response_tool_calls = await consume_model_stream(
                    request, response, assembler, **stream_kwargs
                )
                if response_tool_calls:
                    tool_calls.append(response_tool_calls)

                # llens: 反復上限は環境変数で設定(issue #18)。-1 で無制限。
                max_tool_call_iterations = CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS
                tool_call_iterations = 0

                while len(tool_calls) > 0 and (
                    max_tool_call_iterations < 0
                    or tool_call_iterations < max_tool_call_iterations
                ):
                    tool_call_iterations += 1

                    response_tool_calls = tool_calls.pop(0)

                    tools = metadata.get("tools", {})

                    # llens(#20): render(表示ディスクリプタ)付与と open ブロックの
                    # 再追加ガードは assembler が冪等に行う(通常はストリーム中に
                    # 付与・追加済み。ここは保険)。
                    assembler.ensure_tool_calls_block(response_tool_calls)

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": blocks_event_data(assembler.content_blocks),
                        }
                    )

                    results = await execute_tool_calls(
                        response_tool_calls, tools
                    )

                    assembler.set_tool_results(results)

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": blocks_event_data(assembler.content_blocks),
                        }
                    )

                    try:
                        res = await generate_chat_completion(
                            request,
                            {
                                "model": model_id,
                                "stream": True,
                                # llens(#57): tool ループの各呼び出しでも usage を受け取る
                                "stream_options": {"include_usage": True},
                                "tools": form_data["tools"],
                                "messages": [
                                    *form_data["messages"],
                                    *convert_content_blocks_to_messages(
                                        assembler.content_blocks
                                    ),
                                ],
                            },
                            user,
                        )

                        if isinstance(res, StreamingResponse):
                            followup_tool_calls = await consume_model_stream(
                                request, res, assembler, **stream_kwargs
                            )
                            if followup_tool_calls:
                                tool_calls.append(followup_tool_calls)
                        else:
                            break
                    except Exception as e:
                        log.debug(e)
                        break

                if len(tool_calls) > 0:
                    # llens: 反復上限に到達(issue #18)。黙って打ち切らず
                    # チャット内にエラーを表示する。ループ内 break は現ターンの
                    # tool_calls を pop 済み・新 group 未ストリームで空になるため、
                    # ここに来るのは上限到達時のみ。
                    log.warning(
                        "Tool call iteration limit reached: "
                        f"{max_tool_call_iterations} "
                        "(CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS)"
                    )
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "error": {
                                    "content": (
                                        "Tool call iteration limit reached "
                                        f"({max_tool_call_iterations}). "
                                        "Response stopped. Increase "
                                        "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS "
                                        "to allow more tool calls."
                                    )
                                },
                            },
                        }
                    )

                title = Chats.get_chat_title_by_id(metadata["chat_id"])
                data = {
                    "done": True,
                    **blocks_event_data(assembler.content_blocks),
                    "title": title,
                }

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        blocks_event_data(assembler.content_blocks),
                    )

                # Send a webhook notification if the user is not active
                if get_active_status_by_user_id(user.id) is None:
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{assembler.content}",
                            {
                                "action": "chat",
                                "message": assembler.content,
                                "title": title,
                                "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": data,
                    }
                )

                await run_response_background_tasks(
                    request, user, metadata, tasks, event_emitter
                )
            except asyncio.CancelledError:
                log.warning("Task was cancelled!")
                await event_emitter({"type": "task-cancelled"})

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        blocks_event_data(assembler.content_blocks),
                    )

            if response.background is not None:
                await response.background()

        # background_tasks.add_task(post_response_handler, response, events)
        task_id, _ = create_task(
            post_response_handler(response, events), id=metadata["chat_id"]
        )
        return {"status": True, "task_id": task_id}

    else:
        # Fallback to the original response
        async def stream_wrapper(original_generator, events):
            def wrap_item(item):
                return f"data: {item}\n\n"

            for event in events:
                event, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=event,
                    extra_params=extra_params,
                )

                if event:
                    yield wrap_item(json.dumps(event))

            async for data in original_generator:
                data, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=data,
                    extra_params=extra_params,
                )

                if data:
                    yield data

        return StreamingResponse(
            stream_wrapper(response.body_iterator, events),
            headers=dict(response.headers),
            background=response.background,
        )
