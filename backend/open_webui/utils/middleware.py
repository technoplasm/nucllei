import time
import logging
import sys
import os
import base64

import asyncio
from aiocache import cached
import random
import json
import html
import inspect
import re
import ast

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request, HTTPException
from starlette.responses import Response, StreamingResponse


from open_webui.models.chats import Chats
from open_webui.models.users import Users
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
    get_active_status_by_user_id,
)
from open_webui.routers.tasks import (
    generate_queries,
    generate_title,
    generate_image_prompt,
    generate_chat_tags,
)
from open_webui.routers.retrieval import process_web_search, SearchForm
from open_webui.routers.images import image_generations, GenerateImageForm
from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
    process_pipeline_outlet_filter,
)

from open_webui.utils.webhook import post_webhook


from open_webui.models.users import UserModel
from open_webui.models.functions import Functions
from open_webui.models.models import Models

from open_webui.retrieval.utils import get_sources_from_files


from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.task import (
    get_task_model_id,
    rag_template,
    prompt_template,
)
from open_webui.utils.misc import (
    deep_update,
    get_message_list,
    add_or_update_system_message,
    add_or_update_user_message,
    get_last_user_message,
    get_last_assistant_message,
    prepend_to_first_user_message_content,
    convert_logit_bias_input_to_json,
)
from open_webui.models.skills import Skills, normalize_command
from open_webui.utils.access_control import (
    has_access,
    has_read_access_to_file,
    has_read_access_to_collection,
)
from open_webui.utils.tools import get_tools
from open_webui.utils.tool_render import extract_render_from_spec
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)
from open_webui.utils.code_interpreter import execute_code_jupyter
from open_webui.utils.content_blocks import (
    serialize_text_blocks,
    snapshot_content_blocks,
)

from open_webui.tasks import create_task

from open_webui.config import (
    CACHE_DIR,
    DEFAULT_CODE_INTERPRETER_PROMPT,
)
from open_webui.env import (
    SRC_LOG_LEVELS,
    GLOBAL_LOG_LEVEL,
    BYPASS_MODEL_ACCESS_CONTROL,
    CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS,
    ENABLE_REALTIME_CHAT_SAVE,
)
from open_webui.constants import TASKS


logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


async def chat_web_search_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    event_emitter = extra_params["__event_emitter__"]
    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search",
                "description": "Generating search query",
                "done": False,
            },
        }
    )

    messages = form_data["messages"]
    user_message = get_last_user_message(messages)

    queries = []
    try:
        res = await generate_queries(
            request,
            {
                "model": form_data["model"],
                "messages": messages,
                "prompt": user_message,
                "type": "web_search",
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
            queries = json.loads(response)
            queries = queries.get("queries", [])
        except Exception as e:
            queries = [response]

    except Exception as e:
        log.exception(e)
        queries = [user_message]

    if len(queries) == 0:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "No search query generated",
                    "done": True,
                },
            }
        )
        return form_data

    all_results = []

    for searchQuery in queries:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": 'Searching "{{searchQuery}}"',
                    "query": searchQuery,
                    "done": False,
                },
            }
        )

        try:
            results = await process_web_search(
                request,
                SearchForm(
                    **{
                        "query": searchQuery,
                    }
                ),
                user=user,
            )

            if results:
                all_results.append(results)
                files = form_data.get("files", [])

                if results.get("collection_names"):
                    for col_idx, collection_name in enumerate(
                        results.get("collection_names")
                    ):
                        files.append(
                            {
                                "collection_name": collection_name,
                                "name": searchQuery,
                                "type": "web_search",
                                "urls": [results["filenames"][col_idx]],
                            }
                        )
                elif results.get("docs"):
                    # Invoked when bypass embedding and retrieval is set to True
                    docs = results["docs"]

                    if len(docs) == len(results["filenames"]):
                        # the number of docs and filenames (urls) should be the same
                        for doc_idx, doc in enumerate(docs):
                            files.append(
                                {
                                    "docs": [doc],
                                    "name": searchQuery,
                                    "type": "web_search",
                                    "urls": [results["filenames"][doc_idx]],
                                }
                            )
                    else:
                        # edge case when the number of docs and filenames (urls) are not the same
                        # this should not happen, but if it does, we will just append the docs
                        files.append(
                            {
                                "docs": results.get("docs", []),
                                "name": searchQuery,
                                "type": "web_search",
                                "urls": results["filenames"],
                            }
                        )

                form_data["files"] = files
        except Exception as e:
            log.exception(e)
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": 'Error searching "{{searchQuery}}"',
                        "query": searchQuery,
                        "done": True,
                        "error": True,
                    },
                }
            )

    if all_results:
        urls = []
        for results in all_results:
            if "filenames" in results:
                urls.extend(results["filenames"])

        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "Searched {{count}} sites",
                    "urls": urls,
                    "done": True,
                },
            }
        )
    else:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "No search results found",
                    "done": True,
                    "error": True,
                },
            }
        )

    return form_data


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
    negative_prompt = ""

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
            except Exception as e:
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
                    "description": f"An error occurred while generating an image",
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
    if file_type in ("web_search", "text"):
        return True
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
        queries = []
        try:
            queries_response = await generate_queries(
                request,
                {
                    "model": body["model"],
                    "messages": body["messages"],
                    "type": "retrieval",
                },
                user,
            )
            queries_response = queries_response["choices"][0]["message"]["content"]

            try:
                bracket_start = queries_response.find("{")
                bracket_end = queries_response.rfind("}") + 1

                if bracket_start == -1 or bracket_end == -1:
                    raise Exception("No JSON object found in the response")

                queries_response = queries_response[bracket_start:bracket_end]
                queries_response = json.loads(queries_response)
            except Exception as e:
                queries_response = {"queries": [queries_response]}

            queries = queries_response.get("queries", [])
        except:
            pass

        if len(queries) == 0:
            queries = [get_last_user_message(body["messages"])]

        try:
            # Offload get_sources_from_files to a separate thread
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor() as executor:
                sources = await loop.run_in_executor(
                    executor,
                    lambda: get_sources_from_files(
                        request=request,
                        files=files,
                        queries=queries,
                        embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                            query, prefix=prefix, user=user
                        ),
                        k=request.app.state.config.TOP_K,
                        reranking_function=request.app.state.rf,
                        k_reranker=request.app.state.config.TOP_K_RERANKER,
                        r=request.app.state.config.RELEVANCE_THRESHOLD,
                        hybrid_search=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
                        full_context=request.app.state.config.RAG_FULL_CONTEXT,
                    ),
                )
        except Exception as e:
            log.exception(e)

        log.debug(f"rag_contexts:sources: {sources}")

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


# 先頭行の行頭にある `/command` トークンを検出する正規表現(issue #35)。
SKILL_COMMAND_PATTERN = re.compile(r"^/([a-zA-Z0-9_-]+)")


def is_skill_visible(skill, user) -> bool:
    """read 可視性(access_control read。作成者は常に可視)。"""
    return skill.user_id == user.id or has_access(user.id, "read", skill.access_control)


def activate_skill(skill, user) -> str:
    """skill 発火の単一パス(issue #35): content を展開して返す。

    `/command` 起動(apply_skill_command)と load_skill 起動の両方がここを通る。
    変数はサーバー既知のもののみ({{USER_NAME}} / {{CURRENT_DATE}} 等。ブラウザ専用変数は無し)、
    毎リクエスト再展開(発火時点への固定はしない — _memos/skill-design.md「発火の有効範囲」)。

    tool_ids の遅延取り込み(#35 step2b)は呼び出し側が担う:
    `/command` は process_chat_payload の skill_tool_ids 集約、load_skill は
    build_load_skill_tool の load_tools フック(どちらも get_tools を
    enforce_access_control=False で呼ぶ — skill の read 可視性が認可の単位)。
    """
    return prompt_template(skill.content, user_name=user.name)


def apply_skill_command(form_data, user):
    """user メッセージ先頭の `/command` を「load_skill を呼んだことにして」発火する(issue #35)。

    ヒットした user メッセージは**生のまま**残し、直後に合成の
    assistant tool_call(load_skill)+ tool result(展開済み content)を注入する。
    モデル自律起動(入口 2)と wire 上も完全に対称になり、モデルは「ロード済み」を
    履歴で知るので冗長ロードも起きない。UI も既存の tool_calls 描画がそのまま使える。

    ペイロード内の**全 user メッセージ**を毎リクエスト走査する。発火はその会話
    (現在のメッセージチェーン)で持続し、会話そのものが唯一の状態(2026-07-06 改訂)。

    - 各 user メッセージの先頭行の行頭が `/([a-zA-Z0-9_-]+)` なら、その command で
      read 可視な skill を lookup する。
    - ミス: 何もしない(通常テキストとして素通し。エラーにしない)。

    戻り値は (form_data, 発火リスト)。発火リストの要素は
    {call_id, command, title, content(展開済み), current(最後の user メッセージでの発火か)}。
    current な発火は UI 表示用に応答先頭の tool_calls ブロック(synthetic)にも seed される。
    """
    activations = []

    messages = form_data.get("messages", [])
    last_user_idx = None
    for idx, message in enumerate(messages):
        if message.get("role") == "user":
            last_user_idx = idx

    new_messages = []
    for idx, message in enumerate(messages):
        new_messages.append(message)

        if message.get("role") != "user":
            continue

        # content は文字列 or マルチパート(text パート)。text 部分を取り出す。
        content = message.get("content")
        if isinstance(content, list):
            text = next(
                (
                    item.get("text", "")
                    for item in content
                    if item.get("type") == "text"
                ),
                None,
            )
        else:
            text = content

        if not isinstance(text, str):
            continue

        match = SKILL_COMMAND_PATTERN.match(text)
        if not match:
            continue

        skill = Skills.get_skill_by_command(match.group(1))
        if skill is None or not is_skill_visible(skill, user):
            continue

        expanded = activate_skill(skill, user)
        # id はモデル由来の call(functions.load_skill:N)と衝突しない自前形式。
        call_id = f"skill:{skill.command}:{idx}"
        arguments = json.dumps({"skill_command": skill.command}, ensure_ascii=False)
        # 戻り値は構造化 JSON {result, tools}(#35): 導入された tool 名は
        # 後段(process_chat_payload の skill tools ロード)で書き足される。
        result_content = json.dumps(
            {"result": expanded, "tools": []}, ensure_ascii=False
        )

        new_messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "load_skill", "arguments": arguments},
                    }
                ],
            }
        )
        result_message = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": result_content,
        }
        new_messages.append(result_message)

        activations.append(
            {
                "call_id": call_id,
                "command": skill.command,
                "title": skill.title,
                "content": expanded,
                "result": result_content,
                # 導入 tool 名を後段で書き戻すための参照(dict は共有される)
                "result_message": result_message,
                "tool_ids": skill.tool_ids or [],
                "current": idx == last_user_idx,
            }
        )

    form_data["messages"] = new_messages
    return form_data, activations


def build_load_skill_tool(model, user, load_tools=None):
    """preset `meta.skillIds` ∩ ユーザー可視 から擬似 tool `load_skill` を合成する(#35 step2a)。

    - manifest の識別子は **command 一本**(id と name の二重露出はモデルが name を
      渡して not found になる罠 — _memos/skill-design.md step2a 教訓)。
    - description が空の skill はモデルに露出させない(説明の無い skill は選べない)。
    - `/command` で発火済みの skill を除外する必要は無い — 発火は合成 tool exchange
      として履歴に見えるので、モデルは「ロード済み」を自然に知る。
    - 対象が 1 つも無ければ None(load_skill 自体を載せない)。

    戻り値は native ループ互換の tool エントリ {"spec", "callable"}。
    tool result = activate_skill の展開済み content。
    load_tools(skill) は tool 遅延取り込みの **async** フック(#35 step2b / #28):
    skill.tool_ids が非空なら content を返す前に await され、呼び出し側
    (process_chat_payload)が tools_dict / form_data["tools"] に注入する。
    """
    skill_ids = (model.get("info", {}).get("meta", {}) or {}).get("skillIds", [])
    if not skill_ids:
        return None

    manifest = {}
    for skill_id in skill_ids:
        skill = Skills.get_skill_by_id(skill_id)
        if (
            skill is not None
            and (skill.description or "").strip()
            and is_skill_visible(skill, user)
        ):
            manifest[skill.command] = skill

    if not manifest:
        return None

    listing = "\n".join(
        f"- {command}: {skill.description.strip()}"
        for command, skill in manifest.items()
    )
    spec = {
        "name": "load_skill",
        "description": (
            "Load a skill: returns instructions you must follow to perform "
            "a specific kind of task. Available skills:\n" + listing
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_command": {
                    "type": "string",
                    "description": "Command of the skill to load.",
                    "enum": list(manifest.keys()),
                }
            },
            "required": ["skill_command"],
        },
    }

    async def load_skill(skill_command: str = ""):
        skill = manifest.get(normalize_command(skill_command))
        if skill is None:
            return f"skill not found: {skill_command}"
        loaded_tool_names = []
        if load_tools is not None and skill.tool_ids:
            # load_tools は async フック(#28: 中で get_tools をワーカースレッド実行)
            loaded_tool_names = await load_tools(skill) or []
        # 戻り値は構造化 JSON {result, tools}(#35): 指示本文と「何が導入されたか」を
        # モデル/UI の双方に見せる。str のまま返す(ループは str を素通しする)。
        return json.dumps(
            {"result": activate_skill(skill, user), "tools": loaded_tool_names},
            ensure_ascii=False,
        )

    # render.result = skill(#20 のディスクリプタ駆動): フロントは {result, tools} を
    # パースし、result を markdown ハイライト + tools を一覧で描画する。
    return {
        "spec": spec,
        "callable": load_skill,
        "render": {"result": {"kind": "skill"}},
    }


async def process_chat_payload(request, form_data, user, metadata, model):

    form_data = apply_params_to_form_data(form_data, model)
    log.debug(f"form_data: {form_data}")

    # サーバーサイド `/command` 発火(issue #35)。フィルタや RAG より前に、
    # 合成の load_skill tool exchange(call + 展開済み content の result)を
    # ヒットした user メッセージの直後へ注入する。
    form_data, skill_activations = apply_skill_command(form_data, user)

    # 今回のターンの発火は UI 表示用に応答先頭の tool_calls ブロックとして seed する
    # (process_chat_response 側。synthetic マーカ付きでモデル履歴へは再送しない —
    # 実体は上で form_data["messages"] に注入済み)。result は tool ロード後に
    # 確定するため、metadata への搭載は後段(tools 合流の後)で行う。

    # llens(#40): クライアント供給の file/collection 参照を読取権限で絞る(IDOR 防止)。
    # 後段で追加される web_search 等のサーバー生成項目より前に絞ることで、被害者ファイル
    # を指す偽装 file_id / collection がソース解決に到達しないようにする。
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
        },
        "__metadata__": metadata,
        "__request__": request,
        "__model__": model,
    }

    # Initialize events to store additional event to be sent to the client
    # Initialize contexts and citation
    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        models = {
            request.state.model["id"]: request.state.model,
        }
    else:
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

        # llens(#40, GHSA-vjqm): direct モードでは model 全体がクライアント供給のため、
        # model.meta.knowledge も偽装され得る。この経路の knowledge 参照は読取権限で絞る。
        # 非 direct(DB 由来の管理者設定モデル)は model アクセス検証済みで信頼するため素通し。
        if metadata.get("direct"):
            knowledge_files = filter_files_by_user_access(knowledge_files, user)

        files = form_data.get("files", [])
        files.extend(knowledge_files)
        form_data["files"] = files

    variables = form_data.pop("variables", None)

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

    features = form_data.pop("features", None)
    if features:
        if "web_search" in features and features["web_search"]:
            form_data = await chat_web_search_handler(
                request, form_data, extra_params, user
            )

        if "image_generation" in features and features["image_generation"]:
            form_data = await chat_image_generation_handler(
                request, form_data, extra_params, user
            )

        if "code_interpreter" in features and features["code_interpreter"]:
            form_data["messages"] = add_or_update_user_message(
                (
                    request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE
                    if request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE != ""
                    else DEFAULT_CODE_INTERPRETER_PROMPT
                ),
                form_data["messages"],
            )

    tool_ids = form_data.pop("tool_ids", None)
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
    # Client side tools
    tool_servers = metadata.get("tool_servers", None)

    log.debug(f"{tool_ids=}")
    log.debug(f"{tool_servers=}")

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
    current_skill_activations = [
        {"call_id": a["call_id"], "command": a["command"], "result": a["result"]}
        for a in skill_activations
        if a["current"]
    ]
    if current_skill_activations:
        metadata["skill_activations"] = current_skill_activations

    if tool_servers:
        for tool_server in tool_servers:
            tool_specs = tool_server.pop("specs", [])

            for tool in tool_specs:
                tools_dict[tool["name"]] = {
                    "spec": tool,
                    "direct": True,
                    "server": tool_server,
                }
                # llens(#20): contentMediaType → 表示ディスクリプタ
                render = extract_render_from_spec(tool)
                if render:
                    tools_dict[tool["name"]]["render"] = render

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
                    context_string += f'<source id="{citated_file_idx[file_id]}">{doc_context}</source>\n'

        context_string = context_string.strip()
        prompt = get_last_user_message(form_data["messages"])

        if prompt is None:
            raise Exception("No user message found")
        if (
            request.app.state.config.RELEVANCE_THRESHOLD == 0
            and context_string.strip() == ""
        ):
            log.debug(
                f"With a 0 relevancy threshold for RAG, the context cannot be empty"
            )

        # Workaround for Ollama 2.0+ system prompt issue
        # TODO: replace with add_or_update_system_message
        if model.get("owned_by") == "ollama":
            form_data["messages"] = prepend_to_first_user_message_content(
                rag_template(
                    request.app.state.config.RAG_TEMPLATE, context_string, prompt
                ),
                form_data["messages"],
            )
        else:
            form_data["messages"] = add_or_update_system_message(
                rag_template(
                    request.app.state.config.RAG_TEMPLATE, context_string, prompt
                ),
                form_data["messages"],
            )

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


async def process_chat_response(
    request, response, form_data, user, metadata, model, events, tasks
):
    async def background_tasks_handler():
        message_map = Chats.get_messages_by_chat_id(metadata["chat_id"])
        message = message_map.get(metadata["message_id"]) if message_map else None

        if message:
            messages = get_message_list(message_map, message.get("id"))

            if tasks and messages:
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
                                title_string = (
                                    res.get("choices", [])[0]
                                    .get("message", {})
                                    .get("content", message.get("content", "New Chat"))
                                )
                            else:
                                title_string = ""

                            title_string = title_string[
                                title_string.find("{") : title_string.rfind("}") + 1
                            ]

                            try:
                                title = json.loads(title_string).get(
                                    "title", "New Chat"
                                )
                            except Exception as e:
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

                if TASKS.TAGS_GENERATION in tasks and tasks[TASKS.TAGS_GENERATION]:
                    res = await generate_chat_tags(
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
                            tags_string = (
                                res.get("choices", [])[0]
                                .get("message", {})
                                .get("content", "")
                            )
                        else:
                            tags_string = ""

                        tags_string = tags_string[
                            tags_string.find("{") : tags_string.rfind("}") + 1
                        ]

                        try:
                            tags = json.loads(tags_string).get("tags", [])
                            Chats.update_chat_tags_by_id(
                                metadata["chat_id"], tags, user
                            )

                            await event_emitter(
                                {
                                    "type": "chat:tags",
                                    "data": tags,
                                }
                            )
                        except Exception as e:
                            pass

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
                            post_webhook(
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

                    await background_tasks_handler()

            return response
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

        def split_content_and_whitespace(content):
            content_stripped = content.rstrip()
            original_whitespace = (
                content[len(content_stripped) :]
                if len(content) > len(content_stripped)
                else ""
            )
            return content_stripped, original_whitespace

        def is_opening_code_block(content):
            backtick_segments = content.split("```")
            # Even number of segments means the last backticks are opening a new block
            return len(backtick_segments) > 1 and len(backtick_segments) % 2 == 0

        # Handle as a background task
        async def post_response_handler(response, events):
            def serialize_content_blocks(content_blocks, raw=False):
                content = ""

                for block in content_blocks:
                    if block["type"] == "text":
                        content = f"{content}{block['content'].strip()}\n"
                    elif block["type"] == "tool_calls":
                        attributes = block.get("attributes", {})

                        tool_calls = block.get("content", [])
                        results = block.get("results", [])

                        if results:

                            tool_calls_display_content = ""
                            for tool_call in tool_calls:

                                tool_call_id = tool_call.get("id", "")
                                tool_name = tool_call.get("function", {}).get(
                                    "name", ""
                                )
                                tool_arguments = tool_call.get("function", {}).get(
                                    "arguments", ""
                                )

                                tool_result = None
                                tool_result_files = None
                                for result in results:
                                    if tool_call_id == result.get("tool_call_id", ""):
                                        tool_result = result.get("content", None)
                                        tool_result_files = result.get("files", None)
                                        break

                                if tool_result:
                                    tool_calls_display_content = f'{tool_calls_display_content}\n<details type="tool_calls" done="true" id="{tool_call_id}" name="{tool_name}" arguments="{html.escape(json.dumps(tool_arguments))}" result="{html.escape(json.dumps(tool_result))}" files="{html.escape(json.dumps(tool_result_files)) if tool_result_files else ""}">\n<summary>Tool Executed</summary>\n</details>\n'
                                else:
                                    tool_calls_display_content = f'{tool_calls_display_content}\n<details type="tool_calls" done="false" id="{tool_call_id}" name="{tool_name}" arguments="{html.escape(json.dumps(tool_arguments))}">\n<summary>Executing...</summary>\n</details>'

                            if not raw:
                                content = f"{content}\n{tool_calls_display_content}\n\n"
                        else:
                            tool_calls_display_content = ""

                            for tool_call in tool_calls:
                                tool_call_id = tool_call.get("id", "")
                                tool_name = tool_call.get("function", {}).get(
                                    "name", ""
                                )
                                tool_arguments = tool_call.get("function", {}).get(
                                    "arguments", ""
                                )

                                tool_calls_display_content = f'{tool_calls_display_content}\n<details type="tool_calls" done="false" id="{tool_call_id}" name="{tool_name}" arguments="{html.escape(json.dumps(tool_arguments))}">\n<summary>Executing...</summary>\n</details>'

                            if not raw:
                                content = f"{content}\n{tool_calls_display_content}\n\n"

                    elif block["type"] == "reasoning":
                        reasoning_display_content = "\n".join(
                            (f"> {line}" if not line.startswith(">") else line)
                            for line in block["content"].splitlines()
                        )

                        reasoning_duration = block.get("duration", None)

                        if reasoning_duration is not None:
                            if raw:
                                content = f'{content}\n<{block["start_tag"]}>{block["content"]}<{block["end_tag"]}>\n'
                            else:
                                content = f'{content}\n<details type="reasoning" done="true" duration="{reasoning_duration}">\n<summary>Thought for {reasoning_duration} seconds</summary>\n{reasoning_display_content}\n</details>\n'
                        else:
                            if raw:
                                content = f'{content}\n<{block["start_tag"]}>{block["content"]}<{block["end_tag"]}>\n'
                            else:
                                content = f'{content}\n<details type="reasoning" done="false">\n<summary>Thinking…</summary>\n{reasoning_display_content}\n</details>\n'

                    elif block["type"] == "code_interpreter":
                        attributes = block.get("attributes", {})
                        output = block.get("output", None)
                        lang = attributes.get("lang", "")

                        content_stripped, original_whitespace = (
                            split_content_and_whitespace(content)
                        )
                        if is_opening_code_block(content_stripped):
                            # Remove trailing backticks that would open a new block
                            content = (
                                content_stripped.rstrip("`").rstrip()
                                + original_whitespace
                            )
                        else:
                            # Keep content as is - either closing backticks or no backticks
                            content = content_stripped + original_whitespace

                        if output:
                            output = html.escape(json.dumps(output))

                            if raw:
                                content = f'{content}\n<code_interpreter type="code" lang="{lang}">\n{block["content"]}\n</code_interpreter>\n```output\n{output}\n```\n'
                            else:
                                content = f'{content}\n<details type="code_interpreter" done="true" output="{output}">\n<summary>Analyzed</summary>\n```{lang}\n{block["content"]}\n```\n</details>\n'
                        else:
                            if raw:
                                content = f'{content}\n<code_interpreter type="code" lang="{lang}">\n{block["content"]}\n</code_interpreter>\n'
                            else:
                                content = f'{content}\n<details type="code_interpreter" done="false">\n<summary>Analyzing...</summary>\n```{lang}\n{block["content"]}\n```\n</details>\n'

                    else:
                        block_content = str(block["content"]).strip()
                        content = f"{content}{block['type']}: {block_content}\n"

                return content.strip()

            def convert_content_blocks_to_messages(content_blocks):
                # llens(#18-2): reasoning ブロックはモデルへ再送しない。
                # serialize_content_blocks(raw=False) は reasoning を UI 用
                # <details type="reasoning"> マークアップで包むため、そのまま
                # assistant content に入れると自ターンの思考内容と UI 断片が
                # 次ターンのプロンプトに漏れる。モデル向け履歴には
                # 実際に出力された text/tool_calls/結果のみを載せる。
                def model_facing_content(blocks):
                    return serialize_content_blocks(
                        [b for b in blocks if b["type"] != "reasoning"]
                    )

                messages = []

                temp_blocks = []
                for idx, block in enumerate(content_blocks):
                    if block.get("synthetic"):
                        # `/command` 発火の UI 表示用ブロック(#35)。実体の
                        # tool exchange は form_data["messages"] に注入済みなので、
                        # ここで変換すると二重になる。
                        continue
                    if block["type"] == "tool_calls":
                        messages.append(
                            {
                                "role": "assistant",
                                "content": model_facing_content(temp_blocks),
                                # llens(#20): render(表示ディスクリプタ)・index 等の
                                # 内部キーをモデル向け履歴に漏らさず、OpenAI 形の
                                # {id, type, function} だけを綴じ込む
                                "tool_calls": [
                                    {
                                        "id": call.get("id"),
                                        "type": call.get("type", "function"),
                                        "function": call.get("function", {}),
                                    }
                                    for call in block.get("content", [])
                                ],
                            }
                        )

                        results = block.get("results", [])

                        for result in results:
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": result["tool_call_id"],
                                    "content": result["content"],
                                }
                            )
                        temp_blocks = []
                    else:
                        temp_blocks.append(block)

                if temp_blocks:
                    content = model_facing_content(temp_blocks)
                    if content:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": content,
                            }
                        )

                return messages

            def tag_content_handler(content_type, tags, content, content_blocks):
                end_flag = False

                def extract_attributes(tag_content):
                    """Extract attributes from a tag if they exist."""
                    attributes = {}
                    if not tag_content:  # Ensure tag_content is not None
                        return attributes
                    # Match attributes in the format: key="value" (ignores single quotes for simplicity)
                    matches = re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)
                    for key, value in matches:
                        attributes[key] = value
                    return attributes

                if content_blocks[-1]["type"] == "text":
                    for start_tag, end_tag in tags:
                        # Match start tag e.g., <tag> or <tag attr="value">
                        start_tag_pattern = rf"<{re.escape(start_tag)}(\s.*?)?>"
                        match = re.search(start_tag_pattern, content)
                        if match:
                            attr_content = (
                                match.group(1) if match.group(1) else ""
                            )  # Ensure it's not None
                            attributes = extract_attributes(
                                attr_content
                            )  # Extract attributes safely

                            # Capture everything before and after the matched tag
                            before_tag = content[
                                : match.start()
                            ]  # Content before opening tag
                            after_tag = content[
                                match.end() :
                            ]  # Content after opening tag

                            # Remove the start tag and after from the currently handling text block
                            content_blocks[-1]["content"] = content_blocks[-1][
                                "content"
                            ].replace(match.group(0) + after_tag, "")

                            if before_tag:
                                content_blocks[-1]["content"] = before_tag

                            if not content_blocks[-1]["content"]:
                                content_blocks.pop()

                            # Append the new block
                            content_blocks.append(
                                {
                                    "type": content_type,
                                    "start_tag": start_tag,
                                    "end_tag": end_tag,
                                    "attributes": attributes,
                                    "content": "",
                                    "started_at": time.time(),
                                }
                            )

                            if after_tag:
                                content_blocks[-1]["content"] = after_tag

                            break
                elif content_blocks[-1]["type"] == content_type:
                    start_tag = content_blocks[-1]["start_tag"]
                    end_tag = content_blocks[-1]["end_tag"]
                    # Match end tag e.g., </tag>
                    end_tag_pattern = rf"<{re.escape(end_tag)}>"

                    # Check if the content has the end tag
                    if re.search(end_tag_pattern, content):
                        end_flag = True

                        block_content = content_blocks[-1]["content"]
                        # Strip start and end tags from the content
                        start_tag_pattern = rf"<{re.escape(start_tag)}(.*?)>"
                        block_content = re.sub(
                            start_tag_pattern, "", block_content
                        ).strip()

                        end_tag_regex = re.compile(end_tag_pattern, re.DOTALL)
                        split_content = end_tag_regex.split(block_content, maxsplit=1)

                        # Content inside the tag
                        block_content = (
                            split_content[0].strip() if split_content else ""
                        )

                        # Leftover content (everything after `</tag>`)
                        leftover_content = (
                            split_content[1].strip() if len(split_content) > 1 else ""
                        )

                        if block_content:
                            content_blocks[-1]["content"] = block_content
                            content_blocks[-1]["ended_at"] = time.time()
                            content_blocks[-1]["duration"] = int(
                                content_blocks[-1]["ended_at"]
                                - content_blocks[-1]["started_at"]
                            )

                            # Reset the content_blocks by appending a new text block
                            if content_type != "code_interpreter":
                                if leftover_content:

                                    content_blocks.append(
                                        {
                                            "type": "text",
                                            "content": leftover_content,
                                        }
                                    )
                                else:
                                    content_blocks.append(
                                        {
                                            "type": "text",
                                            "content": "",
                                        }
                                    )

                        else:
                            # Remove the block if content is empty
                            content_blocks.pop()

                            if leftover_content:
                                content_blocks.append(
                                    {
                                        "type": "text",
                                        "content": leftover_content,
                                    }
                                )
                            else:
                                content_blocks.append(
                                    {
                                        "type": "text",
                                        "content": "",
                                    }
                                )

                        # Clean processed content
                        content = re.sub(
                            rf"<{re.escape(start_tag)}(.*?)>(.|\n)*?<{re.escape(end_tag)}>",
                            "",
                            content,
                            flags=re.DOTALL,
                        )

                return content, content_blocks, end_flag

            def close_open_reasoning_block(content_blocks):
                # llens: reasoning の終了は content 到着だけでなく「次の非 reasoning
                # シグナル(tool_calls / ストリーム終端)」でも確定させる。Kimi は
                # content デルタ無しで tool_calls や切断に直行することがあり
                # (fixture: tool_call_no_args / aborted_client_cut)、閉じ損ねると
                # done=false の「Thinking…」が永続化される。
                if (
                    content_blocks
                    and content_blocks[-1]["type"] == "reasoning"
                    and "ended_at" not in content_blocks[-1]
                ):
                    block = content_blocks[-1]
                    block["ended_at"] = time.time()
                    block["duration"] = int(block["ended_at"] - block["started_at"])
                    return True
                return False

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
            except Exception as e:
                pass

            content = (
                message.get("content", "")
                if message
                else last_assistant_message if last_assistant_message else ""
            )

            content_blocks = []

            # llens(#35): この turn の `/command` 発火を、モデル自律起動と同じ
            # 「View Result from load_skill」として応答先頭に表示する。
            # synthetic マーカ付き = モデル向け履歴には再送しない
            # (実体の tool exchange は apply_skill_command が messages に注入済み)。
            if metadata.get("skill_activations"):
                content_blocks.append(
                    {
                        "type": "tool_calls",
                        "synthetic": True,
                        "content": [
                            {
                                "id": a["call_id"],
                                "type": "function",
                                "function": {
                                    "name": "load_skill",
                                    "arguments": json.dumps(
                                        {"skill_command": a["command"]},
                                        ensure_ascii=False,
                                    ),
                                },
                                # {result, tools} を skill 表示で描画(#20/#35)
                                "render": {"result": {"kind": "skill"}},
                            }
                            for a in metadata["skill_activations"]
                        ],
                        "results": [
                            {"tool_call_id": a["call_id"], "content": a["result"]}
                            for a in metadata["skill_activations"]
                        ],
                    }
                )

            content_blocks.append(
                {
                    "type": "text",
                    "content": content,
                }
            )

            # llens(#17): reasoning は SGLang reasoning parser 有効が接続契約で、
            # 常に reasoning_content デルタとして届く。content 中の生 <think> タグ
            # 検出(旧 DETECT_REASONING)は契約外につき削除(_memos/rendering-design.md §3)。
            DETECT_SOLUTION = True
            DETECT_CODE_INTERPRETER = metadata.get("features", {}).get(
                "code_interpreter", False
            )

            code_interpreter_tags = [("code_interpreter", "/code_interpreter")]

            solution_tags = [("|begin_of_solution|", "|end_of_solution|")]

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

                async def stream_body_handler(response):
                    nonlocal content
                    nonlocal content_blocks

                    response_tool_calls = []
                    # llens(#18-2): 終端フレームの finish_reason("stop" | "tool_calls")。
                    # パース結果との整合ガードに使う(ハンドラ末尾)。
                    finish_reason = None

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
                                    model_id = data["selected_model_id"]
                                    Chats.upsert_message_to_chat_by_id_and_message_id(
                                        metadata["chat_id"],
                                        metadata["message_id"],
                                        {
                                            "selectedModelId": model_id,
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
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "usage": usage,
                                                    },
                                                }
                                            )
                                        continue

                                    if choices[0].get("finish_reason"):
                                        finish_reason = choices[0]["finish_reason"]

                                    delta = choices[0].get("delta", {})
                                    delta_tool_calls = delta.get("tool_calls", None)

                                    if delta_tool_calls:
                                        close_open_reasoning_block(content_blocks)

                                        for delta_tool_call in delta_tool_calls:
                                            tool_call_index = delta_tool_call.get(
                                                "index"
                                            )

                                            if tool_call_index is not None:
                                                # Check if the tool call already exists
                                                current_response_tool_call = None
                                                for (
                                                    response_tool_call
                                                ) in response_tool_calls:
                                                    if (
                                                        response_tool_call.get("index")
                                                        == tool_call_index
                                                    ):
                                                        current_response_tool_call = (
                                                            response_tool_call
                                                        )
                                                        break

                                                if current_response_tool_call is None:
                                                    # Add the new tool call
                                                    response_tool_calls.append(
                                                        delta_tool_call
                                                    )
                                                else:
                                                    # Update the existing tool call
                                                    delta_name = delta_tool_call.get(
                                                        "function", {}
                                                    ).get("name")
                                                    delta_arguments = (
                                                        delta_tool_call.get(
                                                            "function", {}
                                                        ).get("arguments")
                                                    )

                                                    if delta_name:
                                                        current_response_tool_call[
                                                            "function"
                                                        ]["name"] += delta_name

                                                    if delta_arguments:
                                                        current_response_tool_call[
                                                            "function"
                                                        ][
                                                            "arguments"
                                                        ] += delta_arguments

                                        # llens(#20 ステップ5): 蓄積中の tool_calls を
                                        # open ブロックとして content_blocks に載せ、部分
                                        # arguments 込みの snapshot を毎フレーム emit する
                                        # (フロントは寛容パーサで逐次描画)。ブロックの
                                        # content は response_tool_calls と同一オブジェクト
                                        # なので、以後の累積は自動で snapshot に反映される。
                                        if not (
                                            content_blocks
                                            and content_blocks[-1]["type"]
                                            == "tool_calls"
                                            and content_blocks[-1]["content"]
                                            is response_tool_calls
                                        ):
                                            content_blocks.append(
                                                {
                                                    "type": "tool_calls",
                                                    "content": response_tool_calls,
                                                }
                                            )

                                        # 表示ディスクリプタは name が届いた時点で付与
                                        # (Kimi は初回フレームで id/name が確定する)
                                        tools = metadata.get("tools", {})
                                        for tool_call in response_tool_calls:
                                            if "render" in tool_call:
                                                continue
                                            tool_name = tool_call.get(
                                                "function", {}
                                            ).get("name", "")
                                            tool_render = (
                                                tools.get(tool_name) or {}
                                            ).get("render")
                                            if tool_render:
                                                tool_call["render"] = tool_render

                                        data = {
                                            "content": serialize_text_blocks(
                                                content_blocks
                                            ),
                                            "content_blocks": snapshot_content_blocks(
                                                content_blocks
                                            ),
                                        }

                                    value = delta.get("content")

                                    reasoning_content = delta.get(
                                        "reasoning_content"
                                    ) or delta.get("reasoning")
                                    if reasoning_content:
                                        if (
                                            not content_blocks
                                            or content_blocks[-1]["type"] != "reasoning"
                                        ):
                                            reasoning_block = {
                                                "type": "reasoning",
                                                "start_tag": "think",
                                                "end_tag": "/think",
                                                "attributes": {
                                                    "type": "reasoning_content"
                                                },
                                                "content": "",
                                                "started_at": time.time(),
                                            }
                                            content_blocks.append(reasoning_block)
                                        else:
                                            reasoning_block = content_blocks[-1]

                                        reasoning_block["content"] += reasoning_content

                                        data = {
                                            "content": serialize_text_blocks(
                                                content_blocks
                                            ),
                                            "content_blocks": snapshot_content_blocks(
                                                content_blocks
                                            ),
                                        }

                                    if value:
                                        if (
                                            content_blocks
                                            and content_blocks[-1]["type"]
                                            == "reasoning"
                                            and content_blocks[-1]
                                            .get("attributes", {})
                                            .get("type")
                                            == "reasoning_content"
                                        ):
                                            reasoning_block = content_blocks[-1]
                                            reasoning_block["ended_at"] = time.time()
                                            reasoning_block["duration"] = int(
                                                reasoning_block["ended_at"]
                                                - reasoning_block["started_at"]
                                            )

                                            content_blocks.append(
                                                {
                                                    "type": "text",
                                                    "content": "",
                                                }
                                            )

                                        content = f"{content}{value}"
                                        # llens(#20 ステップ5): tool_calls ブロックが
                                        # ストリーム中から存在するため、直後に content
                                        # デルタが来たら新しい text ブロックを開く
                                        if not content_blocks or content_blocks[-1][
                                            "type"
                                        ] not in ("text", "reasoning"):
                                            content_blocks.append(
                                                {
                                                    "type": "text",
                                                    "content": "",
                                                }
                                            )

                                        content_blocks[-1]["content"] = (
                                            content_blocks[-1]["content"] + value
                                        )

                                        if DETECT_CODE_INTERPRETER:
                                            content, content_blocks, end = (
                                                tag_content_handler(
                                                    "code_interpreter",
                                                    code_interpreter_tags,
                                                    content,
                                                    content_blocks,
                                                )
                                            )

                                            if end:
                                                break

                                        if DETECT_SOLUTION:
                                            content, content_blocks, _ = (
                                                tag_content_handler(
                                                    "solution",
                                                    solution_tags,
                                                    content,
                                                    content_blocks,
                                                )
                                            )

                                        if ENABLE_REALTIME_CHAT_SAVE:
                                            # Save message in the database
                                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata["chat_id"],
                                                metadata["message_id"],
                                                {
                                                    "content": serialize_text_blocks(
                                                        content_blocks
                                                    ),
                                                    "content_blocks": snapshot_content_blocks(
                                                        content_blocks
                                                    ),
                                                },
                                            )
                                        else:
                                            data = {
                                                "content": serialize_text_blocks(
                                                    content_blocks
                                                ),
                                                "content_blocks": snapshot_content_blocks(
                                                    content_blocks
                                                ),
                                            }

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

                    close_open_reasoning_block(content_blocks)

                    if content_blocks:
                        # Clean up the last text block
                        if content_blocks[-1]["type"] == "text":
                            content_blocks[-1]["content"] = content_blocks[-1][
                                "content"
                            ].strip()

                            if not content_blocks[-1]["content"]:
                                content_blocks.pop()

                                if not content_blocks:
                                    content_blocks.append(
                                        {
                                            "type": "text",
                                            "content": "",
                                        }
                                    )

                    # llens(#18-2): finish_reason とパース結果の整合ガード。
                    # "tool_calls" 終了なのにデルタから 1 つも組めていなければ、
                    # チャンク境界などで取りこぼしている(無音で止まる最悪の失敗
                    # モード)— 黙って通常終了せず、チャット内エラーで可視化する。
                    if finish_reason == "tool_calls" and not response_tool_calls:
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
                    elif response_tool_calls and finish_reason not in (
                        "tool_calls",
                        None,
                    ):
                        # 逆方向の不整合は実行を止めるほどではない(実害はモデルの
                        # 意図解釈のみ)— ログで観測可能にするに留める。
                        log.warning(
                            f"tool calls parsed but finish_reason={finish_reason!r}"
                        )

                    if response_tool_calls:
                        tool_calls.append(response_tool_calls)

                    if response.background:
                        await response.background()

                await stream_body_handler(response)

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

                    # llens(#20): 型駆動の表示ディスクリプタを call に同梱する。
                    # snapshot_content_blocks は calls をそのまま通すので、ここで
                    # 付ければイベント・永続化(リロード)双方に render が乗る。
                    # (通常はストリーム中に付与済み。冪等な保険)
                    for tool_call in response_tool_calls:
                        tool_name = tool_call.get("function", {}).get("name", "")
                        tool_render = (tools.get(tool_name) or {}).get("render")
                        if tool_render:
                            tool_call["render"] = tool_render

                    # llens(#20 ステップ5): ストリーム中に open ブロックとして
                    # 追加済み(content が同一オブジェクト)なら再追加しない
                    if not (
                        content_blocks
                        and content_blocks[-1]["type"] == "tool_calls"
                        and content_blocks[-1]["content"] is response_tool_calls
                    ):
                        content_blocks.append(
                            {
                                "type": "tool_calls",
                                "content": response_tool_calls,
                            }
                        )

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_text_blocks(content_blocks),
                                "content_blocks": snapshot_content_blocks(
                                    content_blocks
                                ),
                            },
                        }
                    )

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
                                    spec.get("parameters", {})
                                    .get("properties", {})
                                    .keys()
                                )

                                tool_function_params = {
                                    k: v
                                    for k, v in tool_function_params.items()
                                    if k in allowed_params
                                }

                                if tool.get("direct", False):
                                    tool_result = await event_caller(
                                        {
                                            "type": "execute:tool",
                                            "data": {
                                                "id": str(uuid4()),
                                                "name": tool_name,
                                                "params": tool_function_params,
                                                "server": tool.get("server", {}),
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                            },
                                        }
                                    )

                                else:
                                    tool_function = tool["callable"]
                                    tool_result = await tool_function(
                                        **tool_function_params
                                    )

                            except Exception as e:
                                tool_result = str(e)

                        tool_result_files = []
                        if isinstance(tool_result, list):
                            for item in tool_result:
                                # check if string
                                if isinstance(item, str) and item.startswith("data:"):
                                    tool_result_files.append(item)
                                    tool_result.remove(item)

                        if isinstance(tool_result, dict) or isinstance(
                            tool_result, list
                        ):
                            tool_result = json.dumps(tool_result, indent=2)

                        results.append(
                            {
                                "tool_call_id": tool_call_id,
                                "content": tool_result,
                                **(
                                    {"files": tool_result_files}
                                    if tool_result_files
                                    else {}
                                ),
                            }
                        )

                    content_blocks[-1]["results"] = results

                    content_blocks.append(
                        {
                            "type": "text",
                            "content": "",
                        }
                    )

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_text_blocks(content_blocks),
                                "content_blocks": snapshot_content_blocks(
                                    content_blocks
                                ),
                            },
                        }
                    )

                    try:
                        res = await generate_chat_completion(
                            request,
                            {
                                "model": model_id,
                                "stream": True,
                                "tools": form_data["tools"],
                                "messages": [
                                    *form_data["messages"],
                                    *convert_content_blocks_to_messages(content_blocks),
                                ],
                            },
                            user,
                        )

                        if isinstance(res, StreamingResponse):
                            await stream_body_handler(res)
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

                if DETECT_CODE_INTERPRETER:
                    MAX_RETRIES = 5
                    retries = 0

                    while (
                        content_blocks[-1]["type"] == "code_interpreter"
                        and retries < MAX_RETRIES
                    ):
                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_text_blocks(content_blocks),
                                    "content_blocks": snapshot_content_blocks(
                                        content_blocks
                                    ),
                                },
                            }
                        )

                        retries += 1
                        log.debug(f"Attempt count: {retries}")

                        output = ""
                        try:
                            if content_blocks[-1]["attributes"].get("type") == "code":
                                code = content_blocks[-1]["content"]

                                if (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "pyodide"
                                ):
                                    output = await event_caller(
                                        {
                                            "type": "execute:python",
                                            "data": {
                                                "id": str(uuid4()),
                                                "code": code,
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                            },
                                        }
                                    )
                                elif (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "jupyter"
                                ):
                                    output = await execute_code_jupyter(
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
                                        code,
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "token"
                                            else None
                                        ),
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "password"
                                            else None
                                        ),
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
                                    )
                                else:
                                    output = {
                                        "stdout": "Code interpreter engine not configured."
                                    }

                                log.debug(f"Code interpreter output: {output}")

                                if isinstance(output, dict):
                                    stdout = output.get("stdout", "")

                                    if isinstance(stdout, str):
                                        stdoutLines = stdout.split("\n")
                                        for idx, line in enumerate(stdoutLines):
                                            if "data:image/png;base64" in line:
                                                id = str(uuid4())

                                                # ensure the path exists
                                                os.makedirs(
                                                    os.path.join(CACHE_DIR, "images"),
                                                    exist_ok=True,
                                                )

                                                image_path = os.path.join(
                                                    CACHE_DIR,
                                                    f"images/{id}.png",
                                                )

                                                with open(image_path, "wb") as f:
                                                    f.write(
                                                        base64.b64decode(
                                                            line.split(",")[1]
                                                        )
                                                    )

                                                stdoutLines[idx] = (
                                                    f"![Output Image {idx}](/cache/images/{id}.png)"
                                                )

                                        output["stdout"] = "\n".join(stdoutLines)

                                    result = output.get("result", "")

                                    if isinstance(result, str):
                                        resultLines = result.split("\n")
                                        for idx, line in enumerate(resultLines):
                                            if "data:image/png;base64" in line:
                                                id = str(uuid4())

                                                # ensure the path exists
                                                os.makedirs(
                                                    os.path.join(CACHE_DIR, "images"),
                                                    exist_ok=True,
                                                )

                                                image_path = os.path.join(
                                                    CACHE_DIR,
                                                    f"images/{id}.png",
                                                )

                                                with open(image_path, "wb") as f:
                                                    f.write(
                                                        base64.b64decode(
                                                            line.split(",")[1]
                                                        )
                                                    )

                                                resultLines[idx] = (
                                                    f"![Output Image {idx}](/cache/images/{id}.png)"
                                                )

                                        output["result"] = "\n".join(resultLines)
                        except Exception as e:
                            output = str(e)

                        content_blocks[-1]["output"] = output

                        content_blocks.append(
                            {
                                "type": "text",
                                "content": "",
                            }
                        )

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_text_blocks(content_blocks),
                                    "content_blocks": snapshot_content_blocks(
                                        content_blocks
                                    ),
                                },
                            }
                        )

                        try:
                            res = await generate_chat_completion(
                                request,
                                {
                                    "model": model_id,
                                    "stream": True,
                                    "messages": [
                                        *form_data["messages"],
                                        {
                                            "role": "assistant",
                                            "content": serialize_content_blocks(
                                                content_blocks, raw=True
                                            ),
                                        },
                                    ],
                                },
                                user,
                            )

                            if isinstance(res, StreamingResponse):
                                await stream_body_handler(res)
                            else:
                                break
                        except Exception as e:
                            log.debug(e)
                            break

                title = Chats.get_chat_title_by_id(metadata["chat_id"])
                data = {
                    "done": True,
                    "content": serialize_text_blocks(content_blocks),
                    "content_blocks": snapshot_content_blocks(content_blocks),
                    "title": title,
                }

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": serialize_text_blocks(content_blocks),
                            "content_blocks": snapshot_content_blocks(content_blocks),
                        },
                    )

                # Send a webhook notification if the user is not active
                if get_active_status_by_user_id(user.id) is None:
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        post_webhook(
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

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": data,
                    }
                )

                await background_tasks_handler()
            except asyncio.CancelledError:
                log.warning("Task was cancelled!")
                await event_emitter({"type": "task-cancelled"})

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": serialize_text_blocks(content_blocks),
                            "content_blocks": snapshot_content_blocks(content_blocks),
                        },
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
