import logging
import sys

import asyncio
import json
import ast

from typing import Optional
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request
from starlette.responses import StreamingResponse


from nucllei.models.chats import Chats
from nucllei.models.users import Users
from nucllei.utils.completion_events import (
    make_event_call,
    make_event_emitter,
    register_stream,
    release_stream,
    sse_event_generator,
)
from nucllei.routers.tasks import (
    generate_title,
)
from nucllei.routers.pipelines import (
    process_pipeline_inlet_filter,
)

from nucllei.utils.webhook import post_webhook


from nucllei.models.users import UserModel
from nucllei.models.functions import Functions

from nucllei.retrieval.utils import get_sources_from_files


from nucllei.utils.chat import generate_chat_completion
from nucllei.utils.task import (
    get_task_model_id,
)
from nucllei.utils.misc import (
    get_message_list,
    add_or_update_system_message,
    get_last_user_message,
    get_last_assistant_message,
    prepend_to_first_user_message_content,
    convert_logit_bias_input_to_json,
)
from nucllei.utils.skills import (
    apply_skill_command,
    build_load_skill_tool,
    current_skill_activation_seeds,
    skill_activation_seed_block,
)
from nucllei.utils.access_control import (
    has_read_access_to_file,
    has_read_access_to_collection,
)
from nucllei.utils.tools import get_tools
from nucllei.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)
from nucllei.utils.file_images import inline_internal_image_urls
from nucllei.utils.content_blocks import (
    convert_content_blocks_to_messages,
    serialize_text_blocks,
    snapshot_content_blocks,
)
from nucllei.utils.response_assembler import (
    MISMATCH_MISSING_TOOL_CALLS,
    MISMATCH_UNEXPECTED_FINISH,
    ContentBlocksAssembler,
)

from nucllei.tasks import create_task

from nucllei.env import (
    SRC_LOG_LEVELS,
    GLOBAL_LOG_LEVEL,
    CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS,
    CHAT_TOOL_EXECUTION_TIMEOUT,
)
from nucllei.constants import TASKS


logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# テキスト連続中のスナップショット emit + DB 永続化の間引き間隔(秒)。
# 純追記はデルタイベントで流れるため、これは途中参加(リロード再アタッチ)の
# 収束と障害時の取りこぼし上限を決めるだけの値。
SNAPSHOT_INTERVAL = 2.0


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
    クライアント制御。要求ユーザーが読取権限を持たない項目をここで除外し、
    他ユーザーのファイル横断読取(IDOR)を防ぐ。
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
                log.warning(f"Error parsing logit_bias: {e}")

    return form_data


def get_model_tool_ids(model) -> list[str]:
    """llens(#54): tool はモデル割り当て(meta.toolIds)一本 — サーバー側で解決する。"""
    return (model or {}).get("info", {}).get("meta", {}).get("toolIds", []) or []


def is_usage_requested(model) -> bool:
    """実測 usage(stream_options.include_usage)を要求するか(issue #57 / #70)。

    既定 ON(キー不在は有効)。stream_options を未知パラメータとして弾く
    OpenAI 互換実装に繋ぐ配備向けの opt-out として、モデルの
    capabilities.usage=False のときだけ要求しない(2026-07-15 決定)。
    横展開先は SGLang/閉域とは限らない — 「うちでは通る」を理由にこの
    escape hatch を消さないこと。
    """
    meta = (model or {}).get("info", {}).get("meta", {}) or {}
    capabilities = meta.get("capabilities") or {}
    return capabilities.get("usage", True) is not False


async def process_chat_payload(
    request, form_data, user, metadata, model, events_stream
):

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

    event_emitter = make_event_emitter(events_stream, metadata)
    event_call = make_event_call(events_stream)

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

    # llens(#23): model.meta.knowledge の展開は撤去 — knowledge 機能は #52 で
    # route/backend ごと撤去済みで、fresh DB では meta.knowledge を作る面が無い
    # (旧データ互換を持たない — CLAUDE.md 絶対制約)。

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

    # llens(#23/#76): code_interpreter・ネイティブ画像生成は機能ごと撤去済み —
    # コード実行も画像生成も tool(外部)一本。features はもう解釈しない
    # (外部 API クライアントが送ってきても上流モデルに漏らさないよう剥がすだけ)。
    form_data.pop("features", None)

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
    # の image_url は推論エンジン側から到達・認証できる保証がない — モデルへ出る直前の
    # ここで base64 data URL に変換する。native tool ループの再送(process_chat_response)も
    # form_data["messages"] を再利用するため、変換はこの 1 箇所で足りる。
    form_data["messages"] = await inline_internal_image_urls(
        form_data["messages"], user
    )

    # llens(#57): streaming でも実測 usage を受け取る。OpenAI 互換サーバーは
    # stream_options.include_usage が無いと usage チャンクを送らない(SGLang 実測済み)。
    # capabilities.usage=False のモデルは要求しない(弾く互換 API 向けの opt-out — 2026-07-15)。
    if form_data.get("stream") and is_usage_requested(model):
        stream_options = form_data.get("stream_options") or {}
        stream_options["include_usage"] = True
        form_data["stream_options"] = stream_options

    # If there are citations, add them to the data_items
    sources = [source for source in sources if source.get("source", {}).get("name", "")]

    if len(sources) > 0:
        events.append({"sources": sources})

    return form_data, metadata, events


def blocks_event_data(content_blocks) -> dict:
    """content_blocks の現在形を chat:completion イベント / 永続化ペイロードにする。"""
    return {
        "content": serialize_text_blocks(content_blocks),
        "content_blocks": snapshot_content_blocks(content_blocks),
    }


async def apply_fallback_title_on_abnormal_end(metadata, tasks, event_emitter):
    """異常終了(中断・エラー)時のフォールバックタイトル。モデルは呼ばない。

    この turn でタイトル生成が予約されていた(= 最初の応答ターン)のに応答が
    確定しなかった場合、最初のユーザー発話の先頭 50 文字をタイトルにする。
    LLM を呼ばないのは、エラー経路では呼び先が死んでいる可能性が高く二次ハングの
    温床になるため(cancel 経路も分岐を増やさず同じ扱い)。同チャットの応答が
    後で正常完了すれば通常のタイトル生成が上書きする。
    """
    if not (tasks and TASKS.TITLE_GENERATION in tasks):
        return

    message_map = Chats.get_messages_by_chat_id(metadata["chat_id"])
    message = message_map.get(metadata["message_id"]) if message_map else None
    if not message:
        return

    messages = get_message_list(message_map, message.get("id"))
    if not messages:
        return

    title = " ".join((messages[0].get("content") or "").split())[:50]
    if not title:
        return

    Chats.update_chat_title_by_id(metadata["chat_id"], title)
    await event_emitter(
        {
            "type": "chat:title",
            "data": title,
        }
    )


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
            # タイトル自動生成 OFF: 最初のユーザー発話をタイトルにする(仕様 2026-07-17
            # 決定 — New Chat が並ぶよりよい)。emit は DB と同じ値を流す
            # (旧実装は応答本文を流していて表示が食い違った)
            title = messages[0].get("content", "New Chat")

            Chats.update_chat_title_by_id(metadata["chat_id"], title)

            await event_emitter(
                {
                    "type": "chat:title",
                    "data": title,
                }
            )


async def _persist_message_snapshot(metadata, snapshot):
    if metadata.get("chat_id") and metadata.get("message_id"):
        await asyncio.to_thread(
            Chats.upsert_message_to_chat_by_id_and_message_id,
            metadata["chat_id"],
            metadata["message_id"],
            snapshot,
        )


async def _record_usage(request, model, metadata, usage, event_emitter):
    """実測 usage(llens #57)の永続化 + イベント。tool ループの各呼び出しで加算。"""
    if metadata.get("chat_id") and metadata.get("message_id"):
        await asyncio.to_thread(
            Chats.record_usage_by_id_and_message_id,
            metadata["chat_id"],
            metadata["message_id"],
            usage,
            context_size=get_model_context_size(request, model),
        )
    await event_emitter(
        {
            "type": "chat:completion",
            "data": {"usage": usage},
        }
    )


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

    wire v2 の emit 規約:
    - ストリーム開始時に無条件で初回スナップショット(seed blocks の表示と
      フロントの「追記先」を保証する)
    - 純追記フレームは {"delta": {"block", "content"}} の小粒イベント
    - 構造変化(ブロック開閉・tool_calls)はスナップショット + DB 永続化
    - テキスト連続中も SNAPSHOT_INTERVAL ごとにスナップショット + 永続化
      (途中参加の収束と障害時の取りこぼし上限)

    ストリーム終端で reasoning の閉じ規律③と finish_reason 整合ガード
    (llens #18-2)を適用する。
    """
    assembler.start_stream()

    loop = asyncio.get_running_loop()
    last_snapshot_at = loop.time()

    async def emit_snapshot(persist):
        nonlocal last_snapshot_at
        snapshot = blocks_event_data(assembler.content_blocks)
        await event_emitter(
            {
                "type": "chat:completion",
                "data": snapshot,
            }
        )
        if persist:
            await _persist_message_snapshot(metadata, snapshot)
        last_snapshot_at = loop.time()

    await emit_snapshot(persist=False)

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

            if not data:
                continue

            if "event" in data:
                await event_emitter(data.get("event", {}))

            choices = data.get("choices", [])
            if not choices:
                error = data.get("error", {})
                if error:
                    # エラー形の一元化: {"error": {"content": str}}
                    if not isinstance(error, dict) or "content" not in error:
                        error = {"content": str(error)}
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {"error": error},
                        }
                    )
                usage = data.get("usage", {})
                if usage:
                    await _record_usage(request, model, metadata, usage, event_emitter)
                continue

            assembler.note_finish_reason(choices[0].get("finish_reason"))

            changed = assembler.handle_delta(choices[0].get("delta", {}))

            if changed:
                frame = assembler.last_frame
                if frame["structural"]:
                    await emit_snapshot(persist=True)
                elif loop.time() - last_snapshot_at >= SNAPSHOT_INTERVAL:
                    await emit_snapshot(persist=True)
                else:
                    for append in frame["appends"]:
                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {"delta": append},
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


async def ingest_model_response_json(
    request,
    response,
    assembler,
    *,
    model,
    metadata,
    event_emitter,
):
    """非ストリーム JSON 応答を合成 1 フレームとして assembler へ畳み込む。

    stream=False(stream_response param での opt-out)でもクライアント契約
    (task + SSE イベント)を同一に保つための正規化。tool_calls も畳み込む
    ため、非ストリームモデルでも tool ループが機能する。
    """
    assembler.start_stream()

    if "error" in response:
        error = response["error"]
        if isinstance(error, dict):
            error = error.get("detail") or error.get("content") or str(error)
        await event_emitter(
            {
                "type": "chat:completion",
                "data": {"error": {"content": str(error)}},
            }
        )

    choices = response.get("choices", [])
    message = (choices[0].get("message") or {}) if choices else {}

    delta = {}
    if message.get("content"):
        delta["content"] = message["content"]
    if message.get("reasoning_content"):
        delta["reasoning_content"] = message["reasoning_content"]
    if message.get("tool_calls"):
        # 非ストリーム形の tool_calls は完成形で index を持たない — 合成する
        delta["tool_calls"] = [
            {**call, "index": call.get("index", i)}
            for i, call in enumerate(message["tool_calls"])
        ]

    if delta:
        assembler.handle_delta(delta)
    if choices:
        assembler.note_finish_reason(choices[0].get("finish_reason"))

    snapshot = blocks_event_data(assembler.content_blocks)
    await event_emitter({"type": "chat:completion", "data": snapshot})
    await _persist_message_snapshot(metadata, snapshot)

    usage = response.get("usage", {})
    if usage:
        await _record_usage(request, model, metadata, usage, event_emitter)

    response_tool_calls, mismatch = assembler.finish_stream()
    if mismatch == MISMATCH_MISSING_TOOL_CALLS:
        log.error("finish_reason=tool_calls but response carried no tool calls")
    return response_tool_calls


async def execute_tool_calls(response_tool_calls, tools):
    """tool call 群を順に実行し、tool result のリストを返す。"""
    results = []
    for tool_call in response_tool_calls:
        tool_call_id = tool_call.get("id", "")
        tool_name = tool_call.get("function", {}).get("name", "")

        tool_function_params = {}
        try:
            # 一次は厳密 JSON(OpenAI 互換の正規形。Kimi 実測も厳密 JSON —
            # _memos/model-frames.md)。Python リテラル風を出すモデル向けの
            # フォールバックとして ast.literal_eval を残す(モデル多様性)。
            tool_function_params = json.loads(
                tool_call.get("function", {}).get("arguments", "{}")
            )
        except Exception as e:
            log.debug(e)
            try:
                tool_function_params = ast.literal_eval(
                    tool_call.get("function", {}).get("arguments", "{}")
                )
            except Exception:
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
                allowed_params = spec.get("parameters", {}).get("properties", {}).keys()

                tool_function_params = {
                    k: v for k, v in tool_function_params.items() if k in allowed_params
                }

                tool_function = tool["callable"]
                # llens: システム側の権威的タイムアウト(sandbox 等の実行側は
                # 自前上限を持たなくてよい)。sync tool はワーカースレッド実行
                # (#28)のため wait_for の cancel で待ちだけが外れ、スレッド自体は
                # 走り切ってから回収される(有界リーク — turn の進行を優先)。
                if CHAT_TOOL_EXECUTION_TIMEOUT > 0:
                    tool_result = await asyncio.wait_for(
                        tool_function(**tool_function_params),
                        timeout=CHAT_TOOL_EXECUTION_TIMEOUT,
                    )
                else:
                    tool_result = await tool_function(**tool_function_params)

            except asyncio.TimeoutError:
                log.warning(
                    f"tool execution timed out: {tool_name} "
                    f"(> {CHAT_TOOL_EXECUTION_TIMEOUT}s)"
                )
                # モデル向け文字列は日本語(_memos/model-frames.md の言語規約)
                tool_result = (
                    f"ツール {tool_name} の実行が {CHAT_TOOL_EXECUTION_TIMEOUT} 秒で"
                    "タイムアウトしました(システム側上限)。"
                )
            except Exception as e:
                tool_result = str(e)

        tool_result_files = []
        if isinstance(tool_result, list):
            # data URL(生成ファイル)を結果から抜き出す。
            # 旧実装はイテレーション中に list.remove しており要素を取りこぼしていた。
            tool_result_files = [
                item
                for item in tool_result
                if isinstance(item, str) and item.startswith("data:")
            ]
            if tool_result_files:
                tool_result = [
                    item for item in tool_result if item not in tool_result_files
                ]

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
    request,
    response,
    form_data,
    user,
    metadata,
    model,
    events,
    tasks,
    events_stream=None,
    queue=None,
):
    """モデル応答をクライアント契約へ変換する(wire v2)。

    - chat メタデータあり(events_stream 供給): バックグラウンドタスクが応答を
      消費・組み立て・永続化し、POST 応答はそのタスクのイベントバスを購読する
      SSE になる。クライアント切断はタスクに影響しない(再アタッチは
      GET /api/tasks/{task_id}/events)。stream/非 stream ともこの契約に統一。
    - chat メタデータなし(headless API): 現行どおり OpenAI 互換フレームを
      素通しする(stream filter だけ適用)。
    """
    filter_functions = [
        Functions.get_function_by_id(filter_id)
        for filter_id in get_sorted_filter_ids(model)
    ]

    # --- headless(chat メタデータ無し)経路: 素通し -----------------------
    if events_stream is None:
        if not isinstance(response, StreamingResponse):
            return response

        extra_params = {
            "__event_emitter__": None,
            "__event_call__": None,
            "__user__": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "info": getattr(user, "info", None) or {},
            },
            "__metadata__": metadata,
            "__request__": request,
            "__model__": model,
        }

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

    # --- chat 経路: タスク + SSE 購読 --------------------------------------
    event_emitter = make_event_emitter(events_stream, metadata)
    event_caller = make_event_call(events_stream)

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

    model_id = form_data.get("model", "")

    await _persist_message_snapshot(metadata, {"model": model_id})

    async def post_response_handler(response, events):
        message = await asyncio.to_thread(
            Chats.get_message_by_id_and_message_id,
            metadata["chat_id"],
            metadata["message_id"],
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
                await _persist_message_snapshot(metadata, {**event})

            stream_kwargs = {
                "model": model,
                "metadata": metadata,
                "filter_functions": filter_functions,
                "extra_params": extra_params,
                "event_emitter": event_emitter,
            }
            ingest_kwargs = {
                "model": model,
                "metadata": metadata,
                "event_emitter": event_emitter,
            }

            if isinstance(response, StreamingResponse):
                response_tool_calls = await consume_model_stream(
                    request, response, assembler, **stream_kwargs
                )
            else:
                response_tool_calls = await ingest_model_response_json(
                    request, response, assembler, **ingest_kwargs
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

                results = await execute_tool_calls(response_tool_calls, tools)

                assembler.set_tool_results(results)

                snapshot = blocks_event_data(assembler.content_blocks)
                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": snapshot,
                    }
                )
                await _persist_message_snapshot(metadata, snapshot)

                try:
                    res = await generate_chat_completion(
                        request,
                        {
                            "model": model_id,
                            # 追撃呼び出しは元リクエストの stream 指定を尊重する
                            # (stream 非対応モデル向けの opt-out を殺さない)
                            "stream": form_data.get("stream", True),
                            # llens(#57): tool ループの各呼び出しでも usage を受け取る
                            # (capabilities.usage=False のモデルは opt-out)
                            **(
                                {"stream_options": {"include_usage": True}}
                                if form_data.get("stream", True)
                                and is_usage_requested(model)
                                else {}
                            ),
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
                    else:
                        followup_tool_calls = await ingest_model_response_json(
                            request, res, assembler, **ingest_kwargs
                        )
                    if followup_tool_calls:
                        tool_calls.append(followup_tool_calls)
                except Exception as e:
                    # llens: 追撃呼び出しの失敗を黙って正常終了にしない
                    # (tool 実行後に続きが来ないまま done になる無言死)。
                    # エラーをチャット内に出し、message にも永続化する。
                    log.exception("follow-up model call failed in tool loop")
                    error_data = {
                        "error": {
                            "content": (
                                "Follow-up model call after tool execution "
                                f"failed: {e}"
                            )
                        },
                    }
                    await event_emitter({"type": "chat:completion", "data": error_data})
                    await _persist_message_snapshot(metadata, error_data)
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

            title = await asyncio.to_thread(
                Chats.get_chat_title_by_id, metadata["chat_id"]
            )
            final_snapshot = blocks_event_data(assembler.content_blocks)
            data = {
                "done": True,
                **final_snapshot,
                "title": title,
            }

            await _persist_message_snapshot(metadata, final_snapshot)

            # webhook(triage KEEP): 「この応答を誰も見ていなかった」場合に通知。
            # 旧実装のプレゼンス(USER_POOL)でなく、完了時点のイベント購読者の
            # 有無で判定する(意味的により正確)。
            if not events_stream.has_subscribers:
                webhook_url = await asyncio.to_thread(
                    Users.get_user_webhook_url_by_id, user.id
                )
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
            # llens: 中断でも open な reasoning を閉じてから保存・emit する。
            # done=false のブロックが DB に残ると「Thinking…」スピナーが
            # リロード後も回り続ける。順序が要件: 先に task-cancelled で
            # フロントに interrupted マークを立てさせ、その後の done イベントで
            # completed 経路(outlet filter)が interrupted 込みで走る。
            log.warning("Task was cancelled!")
            assembler.close_open_reasoning_block()
            data = {
                "done": True,
                **blocks_event_data(assembler.content_blocks),
            }

            await event_emitter({"type": "task-cancelled"})
            await event_emitter({"type": "chat:completion", "data": data})
            await _persist_message_snapshot(metadata, data)

            await apply_fallback_title_on_abnormal_end(metadata, tasks, event_emitter)
        except Exception as e:
            # llens: 上流タイムアウト(sock_read)等の途中失敗を黙って死なせない。
            # ここで error を emit しないと task だけが消え、フロントは
            # 「Preparing…」のまま固まる(ollama 無応答障害で実証)。error は
            # message にも永続化し、リロード後もエラーとして見えるようにする。
            log.exception("Chat response task failed mid-stream")
            assembler.close_open_reasoning_block()
            # done も同時に立てる: フロントの done 経路(completed → outlet
            # filter)を通し、outlet が message.error で成否を判別できるようにする
            data = {
                "error": {"content": f"Connection to the model was lost: {e}"},
                "done": True,
                **blocks_event_data(assembler.content_blocks),
            }

            await event_emitter({"type": "chat:completion", "data": data})
            await _persist_message_snapshot(metadata, data)

            await apply_fallback_title_on_abnormal_end(metadata, tasks, event_emitter)

        if isinstance(response, StreamingResponse) and response.background is not None:
            await response.background()

    task_id, task = create_task(
        post_response_handler(response, events), id=metadata["chat_id"]
    )
    register_stream(task_id, events_stream)
    # タスク終端(title 生成まで含む)でバスを閉じ、全購読 SSE を EOF させる。
    # クライアントは done ではなく EOF を終端として扱う(wire v2 規約)。
    task.add_done_callback(lambda t: release_stream(task_id))

    first_events = [
        {
            "chat_id": metadata.get("chat_id"),
            "message_id": metadata.get("message_id"),
            "data": {"type": "task", "data": {"task_id": task_id}},
        }
    ]

    return StreamingResponse(
        sse_event_generator(events_stream, queue, first_events=first_events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 等のリバースプロキシでの応答バッファリングを抑止
            "X-Accel-Buffering": "no",
        },
    )
