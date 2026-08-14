import logging
import sys

import asyncio
import json
import ast
import threading
import time

from typing import Optional
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request
from starlette.responses import StreamingResponse


from nucllei.models.chats import Chats
from nucllei.models.users import Users
from nucllei.utils.completion_events import (
    make_event_call,
    make_event_emitter,
    make_tool_event_emitter,
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
    replay_skill_tool_ids,
    skill_activation_seed_block,
)
from nucllei.utils.subagent import build_subagent_tools
from nucllei.models.executions import Executions
from nucllei.utils.exec_tools import (
    EXEC_RECALL_TOOL_NAMES,
    entry_is_backgroundable,
    exec_recall_context_ok,
    exec_spawn_context_ok,
    mount_recall_tools,
    spec_for_model,
)
from nucllei.utils.executions import (
    normalize_tool_result,
    spawn_execution,
    truncate_tool_result,
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
from nucllei.utils import tool_output
from nucllei.utils.tool_output import ToolOutputSink
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
from nucllei.utils.sse import sse_line

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

# ストリーミング中の累積 usage(chat:usage — issue #102)の emit 間引き間隔(秒)。
# leading-edge throttle(最初の 1 発は即時、以降は間隔内を捨てる)。tail flush は
# 持たない — 最終真値は既存の chat:completion {usage}(最終フレーム)が無条件で届く。
USAGE_EMIT_INTERVAL = 0.5


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

    max_model_len は OpenAI の /v1/models 仕様には無い SGLang 拡張で、間に
    proxy が挟まると落ちる(LiteLLM v1.82.3 実測 — id/object/created/owned_by
    の 4 キーしか返さず、context 長が既知の商用モデルでも同じ)。そういう配備
    向けの**最後の手段**として meta.context_size(モデルへ手で設定)を見る。
    実測値が取れるならそちらが真 — 手動値は stale になりうるので fallback 側に
    置く(capabilities.usage 等と同じく UI は持たず model API から設定する)。
    """
    ctx = (model.get("openai") or {}).get("max_model_len")
    if ctx is None:
        base_id = (model.get("info") or {}).get("base_model_id")
        if base_id:
            base = request.app.state.MODELS.get(base_id) or {}
            ctx = (base.get("openai") or {}).get("max_model_len")
    if ctx is None:
        meta = (model.get("info") or {}).get("meta") or {}
        ctx = meta.get("context_size")
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


def is_continuous_usage_requested(model) -> bool:
    """ストリーミング中の累積 usage(stream_options.continuous_usage_stats)を要求するか
    (issue #102)。

    既定 ON(キー不在は有効)。continuous_usage_stats は SGLang 拡張で、未知の
    stream_options キーを弾く OpenAI 互換実装に繋ぐ配備向けの opt-out として、
    モデルの capabilities.continuous_usage=False のときだけ要求しない。
    なお手動 opt-out が無くても、上流が 4xx で弾いた場合は egress
    (routers/openai.py)が剥がして 1 回リトライし、以後そのモデルには
    送らないことを学習する(混在ゲートウェイ配備の自動対応 — 2026-08-16)。
    usage 自体を要求しないモデル(is_usage_requested が False)では常に False —
    include_usage 無しに continuous だけ頼む形は作らない。
    """
    if not is_usage_requested(model):
        return False
    meta = (model or {}).get("info", {}).get("meta", {}) or {}
    capabilities = meta.get("capabilities") or {}
    return capabilities.get("continuous_usage", True) is not False


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
        # llens(#89): モデルの目に画素を流す汎用 tool-push ヘルパ。宣言した tool
        # だけに注入され(tools.py:49 の sig 一致)、実行中 tool-call の sink へ
        # file_id を積む。実体化は送信直前 inline に一任。view_image に閉じない。
        "__feed_image__": tool_output.feed_image,
        # llens(#88): __feed_image__ と対称の、人間の目へ向けた汎用 tool-push。
        # __display__(content, kind) を実行中 tool-call の sink へ積み、下で harvest
        # して results[i].display へ載せる(collapse 外に描画・モデル非注入)。
        "__display__": tool_output.display,
        # llens(#100): tool が実行中に進捗を伝える唯一の口。行き先はその tool 自身の
        # ウィジェット(ToolCallBlock ヘッダ)に閉じる。揮発 — 完了後は所要時間が残る。
        "__progress__": tool_output.progress,
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
    # llens(#107 仕様変更 2026-08-14): 無効 preset の agent session 子ターンでは
    # model が app.state.MODELS に無い(subagent 側が Model 行から合成して渡す)。
    # 後段の id 引き(get_task_model_id / pipeline inlet / __model__)が KeyError
    # しないよう、この処理の中だけ合成 model を重ねた view を使う(state は汚さない)。
    models = request.app.state.MODELS
    if model.get("id") and model["id"] not in models:
        models = {**models, model["id"]: model}

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
        # llens(#100): tool の実行中通知は __progress__ 一本。status は落とす
        # (filter 側の emitter は無変更 — status は filter の常設表示に使われている)。
        "__event_emitter__": make_tool_event_emitter(event_emitter),
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
    # llens(#101): モデル自律 load_skill(入口 2)の分は履歴から再導出する。
    # 認可は履歴に委ねず、その場で `/command` と同じ AND 条件を再確認
    # (詳細は replay_skill_tool_ids の docstring)。DB read を含むので
    # ワーカースレッドで(#28 と同じ規律)。
    for tool_id in await asyncio.to_thread(
        replay_skill_tool_ids, form_data, user, model
    ):
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
                # llens(#110): 第 2 経路(遅延ロード)でも適格 tool には background
                # 引数を注入する(下の初回合成と同じヘルパ — spec はコピー)。
                form_data["tools"].append(
                    {
                        "type": "function",
                        "function": spec_for_model(
                            name, tool, exec_spawn_context_ok(metadata)
                        ),
                    }
                )
            loaded.append(name)
        # llens(#110 A-3): 適格(backgroundable)tool がこの遅延ロードで初めて
        # 載ったターンでも、回収 builtin(check_task/await_task/stop_task)を追加
        # mount する — 初回合成時の判定は「その時点の tools_dict」しか見ておらず、
        # ここで昇格面だけ現れて回収面が無い片肺になるため。spec append は上と
        # 同じ配達機序(tools_dict / form_data["tools"] は native ループが毎
        # イテレーション参照する同一オブジェクト)。
        if exec_recall_context_ok(metadata) and any(
            entry_is_backgroundable(name, tools_dict[name]) for name in loaded
        ):
            mount_recall_tools(tools_dict, form_data, user, metadata)
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

    # llens(#107/#109): サブエージェントの擬似 tool run_subagent(load_skill と
    # 同型)。allowlist / 機能フラグ / depth 上限の判定は build_subagent_tools 側に
    # 閉じ、対象が無ければ None(tool 自体を載せない)。同期 DB read(親 chat の
    # kind/depth + Models×allowlist 件数)を含むためワーカースレッドへ(#28 の規律)。
    # 非同期形は #110 の execution 基盤に一本化(旧 await_subagent は撤去)。
    subagent_tools = await asyncio.to_thread(
        build_subagent_tools, request, model, user, metadata
    )
    for name, entry in (subagent_tools or {}).items():
        if name in tools_dict:
            log.warning(f"tool '{name}' is shadowed by the subagent pseudo-tool")
        tools_dict[name] = entry

    # llens(#110): 回収 builtin check_task / await_task / stop_task。適格
    # (backgroundable)tool が載るターン、または chat に exec 行が残っている場合に
    # mount する(§7 — 過剰 mount は無害: terminal への再 await は冪等)。mount
    # 条件は spawn 系(async_exec_available)と**独立**(契約 M1 — 回収は DB 読みで
    # 完結し、multi-worker でも boot スイープが running を倒すので liveness 誤爆は
    # ない。過去に spawn された行が構成変更後も回収不能にならないため)。builtin
    # なので #101 の履歴再導出は不要。agent session(D9)と chat 文脈なしでは
    # 載せない(spawn も同条件で拒否される)。
    if exec_recall_context_ok(metadata):
        should_mount_recall = any(
            entry_is_backgroundable(name, entry) for name, entry in tools_dict.items()
        )
        if not should_mount_recall:
            should_mount_recall = await asyncio.to_thread(
                Executions.has_awaitable_executions_by_chat_id, metadata["chat_id"]
            )
        if should_mount_recall:
            mount_recall_tools(tools_dict, form_data, user, metadata)

    allow_background = exec_spawn_context_ok(metadata)

    if tools_dict:
        # llens: native function calling のみサポート(issue #18)。
        # ツールがあれば常にモデルへ tool spec を渡す(function_calling パラメータは無視)。
        # #110: 適格 tool の spec には共有 background 引数を注入する(spec の
        # コピーに対して — tools_dict 側は汚さず、unknown-drop の基準は元 spec)。
        metadata["tools"] = tools_dict
        form_data["tools"] = [
            {
                "type": "function",
                "function": spec_for_model(name, tool, allow_background),
            }
            for name, tool in tools_dict.items()
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
    # ここで base64 data URL に変換する。
    # 注意(#89): これは初回 payload(form_data["messages"])の 1 箇所。tool ループの
    # 追撃再送は convert_content_blocks_to_messages で新規に image_url(view_image の
    # file_id 参照)を append するため、その経路にも別途 inline を掛けている
    # (process_chat_response の追撃ブロック参照)。ここを「1 箇所で足りる」と誤解して
    # 追撃側 inline を消すと、file_id 参照が未実体化のままモデルに渡り画素が届かない。
    form_data["messages"] = await inline_internal_image_urls(
        form_data["messages"], user
    )

    # llens(#57): streaming でも実測 usage を受け取る。OpenAI 互換サーバーは
    # stream_options.include_usage が無いと usage チャンクを送らない(SGLang 実測済み)。
    # capabilities.usage=False のモデルは要求しない(弾く互換 API 向けの opt-out — 2026-07-15)。
    if form_data.get("stream") and is_usage_requested(model):
        stream_options = form_data.get("stream_options") or {}
        stream_options["include_usage"] = True
        # llens(#102): 累積 usage をチャンクにも載せてもらい(SGLang 実測済み)、
        # ストリーミング中のライブ表示(chat:usage)の源にする。
        # capabilities.continuous_usage=False のモデルは要求しない(opt-out)。
        if is_continuous_usage_requested(model):
            stream_options["continuous_usage_stats"] = True
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
    # llens(#107): agent session(サブエージェントの子ターン)はタイトルを触らない。
    # spawn 時の決定的タイトルが正(tasks=None でも来ないはずの二重ガード)。
    if metadata.get("agent_session"):
        return

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
    # llens(#107): agent session の子ターンはタイトル生成を走らせない
    # (spawn 時の決定的タイトルが正。tasks=None でも来ないはずの二重ガード)。
    if metadata.get("agent_session"):
        return

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
    """実測 usage(llens #57)の永続化 + イベント。tool ループの各呼び出しで加算。

    #102: emit するのは**マージ後の message.usage**(record の戻り — completion /
    duration はターン累積、prompt は最新、context_size 同梱)。FE の message.usage と
    DB が常に一致し、saveChatHandler の history 保存が DB のマージ結果を上書きしても
    値が変わらない。記録できない経路(metadata 不足・message 不在)だけ生 usage +
    context_size で emit する。
    """
    ctx = get_model_context_size(request, model)
    merged = None
    if metadata.get("chat_id") and metadata.get("message_id"):
        merged = await asyncio.to_thread(
            Chats.record_usage_by_id_and_message_id,
            metadata["chat_id"],
            metadata["message_id"],
            usage,
            context_size=ctx,
        )
    if merged is None:
        merged = {**usage, "context_size": ctx} if ctx is not None else usage
    await event_emitter(
        {
            "type": "chat:completion",
            "data": {"usage": merged},
        }
    )


def _live_usage_payload(usage: dict, context_size) -> dict:
    """チャンク usage → chat:usage ペイロード(#102)の正規化。

    契約キーのみ載せる(prompt_tokens_details 等のバックエンド固有キーは落とす)。
    reasoning_tokens はチャンク usage にあるときのみ、context_size は解決できて
    いるときのみ。
    """
    payload = {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
    reasoning_tokens = usage.get("reasoning_tokens")
    if reasoning_tokens is not None:
        payload["reasoning_tokens"] = int(reasoning_tokens)
    if context_size is not None:
        payload["context_size"] = context_size
    return payload


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
    - チャンク(choices 非空)に累積 usage が載っていれば(#102、
      continuous_usage_stats)、USAGE_EMIT_INTERVAL の leading-edge throttle で
      揮発イベント `chat:usage` として流す。
    - usage の**永続化は 1 ストリームにつき 1 回**。原則は最終 usage フレーム
      (choices: [])だが、そこに来なかった配備のために最後に観測した usage を
      ストリーム終端で拾う(下の pending_usage を参照)。

    ストリーム終端で reasoning の閉じ規律③と finish_reason 整合ガード
    (llens #18-2)を適用する。
    """
    assembler.start_stream()

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    last_snapshot_at = started_at
    # #102: ライブ usage(chat:usage)の throttle 時計と context_size。
    # context_size はこのモデル呼び出しの間は不変なので 1 回だけ解決して使い回す。
    last_usage_emit_at = None
    context_size = get_model_context_size(request, model)
    # #102: usage の永続化状態。recorded_usage が False のままストリームが終わった
    # ときだけ、最後に観測した pending_usage を終端で記録する(下の後始末を参照)。
    recorded_usage = False
    pending_usage = None

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
                # #102: usage の主たる永続化点はこの最終フレーム(choices: [])。
                # チャンクに載る累積 usage(continuous)は下の chat:usage(揮発)で
                # 流すのみで、ここを通らない限り DB には書かない。
                usage = data.get("usage", {})
                if usage:
                    # 生成所要時間(#102): この呼び出しの stream 消費の壁時計を実測して
                    # usage に添える(DB では completion 同様に累積 → ターンの生成時間。
                    # tool 実行時間は含まれない)。クライアント積算はリロードで消えるため
                    # 計測は backend の責務。
                    usage = {**usage, "duration": round(loop.time() - started_at, 3)}
                    await _record_usage(request, model, metadata, usage, event_emitter)
                    recorded_usage = True
                continue

            # llens(#102): continuous_usage_stats でチャンクに載る累積 usage を
            # leading-edge throttle で揮発イベントとして流す。tail flush は不要 —
            # 最終真値は上の choices: [] フレーム(chat:completion {usage})、
            # それが来ない配備ではストリーム終端の後始末が受け持つ。
            live_usage = data.get("usage")
            if live_usage:
                pending_usage = live_usage
                if (
                    last_usage_emit_at is None
                    or loop.time() - last_usage_emit_at >= USAGE_EMIT_INTERVAL
                ):
                    last_usage_emit_at = loop.time()
                    await event_emitter(
                        {
                            "type": "chat:usage",
                            "data": _live_usage_payload(live_usage, context_size),
                        }
                    )

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

    if not recorded_usage and pending_usage is not None:
        # llens(#102): usage を `choices: []` の専用フレームで送るのは SGLang の形で、
        # OpenAI の streaming 仕様が定めた形ではない。間に proxy が挟まると同じ usage が
        # choices 非空(delta: {})のチャンクに載って届く(LiteLLM v1.82.3 実測 —
        # finish_reason チャンクの次、[DONE] の直前)。上のループはそれをライブ usage と
        # してしか見ないため、この後始末が無いと usage が一度も永続化されず
        # message.usage が空のままになる = 所要時間・tok/s・IN/OUT が丸ごと出ない。
        # フレームの形を当てにいかず「最後に観測した累積 usage を終端で 1 回記録する」で
        # 両方の形を拾う(SGLang では上で記録済みなのでここは通らない)。
        usage = {**pending_usage, "duration": round(loop.time() - started_at, 3)}
        await _record_usage(request, model, metadata, usage, event_emitter)

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


def _make_progress_emitter(events_stream, metadata):
    """tool 進捗(#100)の同期 emit を作る。sink 生成時に注入される。

    `stream.emit` は put_nowait だけの同期関数なので、`call_soon_threadsafe` 一本で
    イベントループ側(async tool)からもワーカースレッド側(sync tool)からも正しく、
    待ちが要らない。**`run_coroutine_threadsafe` は使えない** — async tool は
    `utils/tools.py` の iscoroutinefunction 分岐で to_thread を通らずループ上で直接
    await されるため、そこから future を待つとループが自分自身を待って固まる。

    `__event_emitter__` も経由しない: あちらは async で status/message に DB 書き
    (to_thread)を含み、await 境界で順序が入れ替わるうえ共有 executor を圧迫する。

    stream を**ここでオブジェクトごと捕まえる**のも要点。message_id からの遅延
    逆引きにすると、regenerate 時に次のターンの stream へ幽霊進捗を注入しうる。
    closed になった stream への emit は CompletionEventStream 側が黙って捨てる。
    """
    if events_stream is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # ループ外(同期テスト等)。進捗は黙って無効化する
        return None

    chat_id = (metadata or {}).get("chat_id")
    message_id = (metadata or {}).get("message_id")
    # この関数はループ上で呼ばれる = ここの ident がループスレッド
    loop_thread_id = threading.get_ident()

    def emitter_for(tool_call_id):
        def _emit(payload):
            envelope = {
                "chat_id": chat_id,
                "message_id": message_id,
                "data": {"type": "tool:progress", "data": payload},
            }
            if threading.get_ident() == loop_thread_id:
                # async tool(大多数)はループ上で走るので直呼び。スケジュールすると
                # ループ停止時に未実行のコールバックが落ちる余地が残る
                events_stream.emit(envelope)
            else:
                # sync tool の to_thread ワーカーから。emit は購読者集合を触るので
                # ワーカーから直接呼んではならない
                loop.call_soon_threadsafe(events_stream.emit, envelope)

        return _emit

    return emitter_for


async def _execute_tool_call_sync(
    tool, tool_name, tool_call_id, tool_function_params, progress_emitter_for
):
    """1 件の tool call を同期(このターン内)で実行する(execute_tool_calls の本体)。

    戻り値は (tool_result, tool_succeeded, sink)。sink は close 済みで、呼び出し側が
    成功時のみ harvest する(context_images / display / result_render)。
    background 昇格時(#110)はここを通らず spawn_execution が代替する。
    """
    # llens(#89): tool-call ごとの出力 sink を張る。tool が
    # __feed_image__(file_id) を呼ぶと sink に file_id が積まれ、呼び出し側で
    # harvest して results[i].context_images へ載せる(VLM 再注入トリガ)。
    # 戻り値をハイジャックしない汎用経路 — view_image に閉じない。
    sink = ToolOutputSink(
        tool_call_id=tool_call_id,
        emit_progress=(
            progress_emitter_for(tool_call_id) if progress_emitter_for else None
        ),
    )
    sink_token = tool_output.current_sink.set(sink)
    # llens(#100): この call の開始をフロントへ通知する。ブロックは実行前の
    # 1 回の emit で一斉に描かれるので、これがフロントの経過時間タイマの
    # 起点になる(逐次実行なので 2 本目以降は「まだ始まっていない」)。
    sink.mark_started()
    tool_succeeded = False
    tool_result = None
    try:
        tool_function = tool["callable"]
        # llens: システム側の権威的タイムアウト(sandbox 等の実行側は
        # 自前上限を持たなくてよい)。sync tool はワーカースレッド実行
        # (#28)のため wait_for の cancel で待ちだけが外れ、スレッド自体は
        # 走り切ってから回収される(有界リーク — turn の進行を優先)。
        # #107: tool エントリの per-tool timeout キーが優先(run_subagent は
        # 子ターン全体を包むため SUBAGENT_TURN_TIMEOUT を持つ。await_task は
        # 0 = 無効化 — 自前の clamp 済み有界待ちが running 形を返す)。
        tool_timeout = tool.get("timeout", CHAT_TOOL_EXECUTION_TIMEOUT)
        if tool_timeout > 0:
            tool_result = await asyncio.wait_for(
                tool_function(**tool_function_params),
                timeout=tool_timeout,
            )
        else:
            tool_result = await tool_function(**tool_function_params)
        tool_succeeded = True

    except asyncio.TimeoutError:
        log.warning(f"tool execution timed out: {tool_name} " f"(> {tool_timeout}s)")
        # モデル向け文字列は日本語(_memos/model-frames.md の言語規約)
        tool_result = (
            f"ツール {tool_name} の実行が {tool_timeout} 秒で"
            "タイムアウトしました(システム側上限)。"
        )
    except Exception as e:
        tool_result = str(e)
    finally:
        tool_output.current_sink.reset(sink_token)
        # sink を閉じる(#100): 抑制中の進捗を 1 通流し、以後の push を
        # no-op にする。ContextVar の reset はメインループ側 context の話で、
        # copy_context を持ったまま走り続ける timeout orphan スレッド
        # (上の有界リーク)には効かない — closed フラグがそれを止める。
        sink.close()
    return tool_result, tool_succeeded, sink


async def execute_tool_calls(
    response_tool_calls, tools, *, events_stream=None, metadata=None
):
    """tool call 群を順に実行し、tool result のリストを返す。

    events_stream / metadata は tool 進捗(#100)の宛先。省略時(headless・テスト)は
    `__progress__` が黙って no-op になる。metadata は background 昇格(#110)の
    exec 行束縛(user_id / chat_id / message_id)にも使われる。
    """
    results = []
    progress_emitter_for = _make_progress_emitter(events_stream, metadata)
    for tool_call in response_tool_calls:
        tool_call_id = tool_call.get("id", "")
        tool_name = tool_call.get("function", {}).get("name", "")
        # 所要時間(#100)は成否に関わらず測る — 落ちた tool が何秒走ったかこそ
        # 知りたい(context_images/display の tool_succeeded ゲートとは規律が違う)。
        started_at = time.monotonic()

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
        context_image_file_ids = []
        display_items = []
        result_render_override = None

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

            # llens(#110): 共有 background 引数は allowed_params フィルタより前に
            # 必ず pop する(適格 tool の spec **コピー**にのみ注入される引数で、
            # 元 spec には無い)。tool 自身が background を宣言している場合は tool の
            # 実引数であり恒久不適格(D4)— 触らずそのまま渡す。
            allowed_params = spec.get("parameters", {}).get("properties", {}).keys()
            background_requested = False
            if "background" not in allowed_params:
                background_requested = bool(
                    tool_function_params.pop("background", False)
                )
            tool_function_params = {
                k: v for k, v in tool_function_params.items() if k in allowed_params
            }

            if background_requested and not (
                entry_is_backgroundable(tool_name, tool)
                and exec_spawn_context_ok(metadata)
            ):
                # 不適格 tool / 文脈(agent session・chat 文脈なし)への指定は
                # 無視して同期実行(§7)
                log.warning(
                    f"background=true ignored for tool '{tool_name}' "
                    "(not backgroundable in this context)"
                )
                background_requested = False

            if background_requested:
                # llens(#110): execution チャンネルへ昇格(K2)。spawn は exec 行を
                # INSERT して runner task を起動し、即座に running 形(K3)を返す。
                # この running 形が親メッセージの tool result として以後不変
                # (append-only)— 完了の真実は exec 行、回収は await_task /
                # check_task とサーバ駆動配達(execution_resume)。params は
                # unknown-drop 済み(監査
                # 保存と callable 引数の両方に使われる)。
                tool_result = await spawn_execution(
                    user_id=metadata["user_id"],
                    chat_id=metadata["chat_id"],
                    message_id=metadata["message_id"],
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    entry=tool,
                    params=tool_function_params,
                )
            else:
                tool_result, tool_succeeded, sink = await _execute_tool_call_sync(
                    tool,
                    tool_name,
                    tool_call_id,
                    tool_function_params,
                    progress_emitter_for,
                )
                # 失敗/タイムアウトした tool の push は再注入しない(失敗 call は
                # 副産物を出さない — content=エラー文言と画像が混在する不整合を
                # 避ける)。成功時のみ harvest(lock 下 snapshot で timeout orphan
                # スレッドの遅延 append を除外)。
                context_image_file_ids = sink.context_images() if tool_succeeded else []
                # display(人間の目、#88)も同じ規律で成功時のみ harvest。
                display_items = sink.displays() if tool_succeeded else []
                # #110(§8): await_task が宣言した表示ディスクリプタ
                # (exec 行の render_descriptor)は型由来の render より優先する
                result_render_override = (
                    sink.result_render() if tool_succeeded else None
                )

        # wire 契約: content は常に文字列。表示種別は実型から決めて同梱する
        # (フロントに「JSON に見えるか」の動的解釈をさせない — 型に忠実の原則)。
        # dict/list だけ kind=json を明示し、それ以外は省略(フロント既定 = text)。
        # 戻り型宣言(calls[].render.result)があればフロントはそちらを優先する。
        # 正規化は executions.normalize_tool_result と共有(#110 契約 M0 —
        # ensure_ascii=False / indent なしの根拠はそちらの docstring)。
        tool_result, result_render = normalize_tool_result(tool_result)

        # llens(#110 §8): await_task の render_descriptor パススルー。exec 行に
        # 保存された実 tool の戻り値宣言(sink.set_result_render 経由)が、
        # 型由来の render(kind=json)より優先する(subagent 等の描画復元)。
        if result_render_override:
            result_render = result_render_override

        # llens(#110 契約 M0): 切詰めは結果の生産点で一度だけ — 同期 tool は
        # ここが生産点(background は runner の保存時)。回収 builtin
        # (check_task/await_task/stop_task)は適用除外: 中身は保存時に切詰め
        # 済みで、envelope の json.dumps 膨張で再切詰めすると壊れ JSON になる。
        # 切詰め時は render を text へ降格(JSON / descriptor 描画はパース前提)。
        result_truncated = False
        original_chars = 0
        if tool_name not in EXEC_RECALL_TOOL_NAMES:
            tool_result, result_truncated, original_chars = truncate_tool_result(
                tool_result
            )
            if result_truncated:
                result_render = None

        results.append(
            {
                "tool_call_id": tool_call_id,
                "content": tool_result,
                # 所要時間(#100)。モデル向け履歴は tool_call_id/content しか拾わない
                # ので、これがモデルへ漏れることはない(content_blocks.py)。
                "duration": round(time.monotonic() - started_at, 3),
                **({"render": result_render} if result_render else {}),
                **(
                    {"truncated": True, "original_chars": original_chars}
                    if result_truncated
                    else {}
                ),
                **(
                    {"context_images": context_image_file_ids}
                    if context_image_file_ids
                    else {}
                ),
                **({"display": display_items} if display_items else {}),
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
            for event in events:
                event, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=event,
                    extra_params=extra_params,
                )

                if event:
                    yield sse_line(event)

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

                results = await execute_tool_calls(
                    response_tool_calls,
                    tools,
                    events_stream=events_stream,
                    metadata=metadata,
                )

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
                    # llens(#89 要石): convert が生成する内部 file URL の image_url
                    # (view_image の file_id 参照)を追撃でも画素化する。process_chat_payload
                    # の inline は form_data["messages"] にしか掛からない(:「変換はこの
                    # 1 箇所で足りる」は追撃を見落としていた)ため、合流後にもう一度
                    # 掛ける。followup_msgs は毎周新規 list なので content_blocks の原本を
                    # 汚さず、内部 URL 非一致は無変更=二重掛けでも冪等。
                    followup_msgs = [
                        *form_data["messages"],
                        *convert_content_blocks_to_messages(assembler.content_blocks),
                    ]
                    followup_msgs = await inline_internal_image_urls(
                        followup_msgs, user
                    )
                    res = await generate_chat_completion(
                        request,
                        {
                            "model": model_id,
                            # 追撃呼び出しは元リクエストの stream 指定を尊重する
                            # (stream 非対応モデル向けの opt-out を殺さない)
                            "stream": form_data.get("stream", True),
                            # llens(#57): tool ループの各呼び出しでも usage を受け取る
                            # (capabilities.usage=False のモデルは opt-out)。
                            # #102: continuous_usage_stats もここで再度要求する —
                            # 初回 payload だけに付けると tool 実行後の追撃ストリームで
                            # ライブ usage が止まる。
                            **(
                                {
                                    "stream_options": {
                                        "include_usage": True,
                                        **(
                                            {"continuous_usage_stats": True}
                                            if is_continuous_usage_requested(model)
                                            else {}
                                        ),
                                    }
                                }
                                if form_data.get("stream", True)
                                and is_usage_requested(model)
                                else {}
                            ),
                            "tools": form_data["tools"],
                            "messages": followup_msgs,
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
            # llens(#107): agent session の子ターンは対象外 — 結果は親の tool
            # result が運ぶ。購読ゼロで走るのが正常形(タイトル抑止と同じマーカ)。
            # llens(#110 契約 M5): サーバ駆動配達の続きターン(task_resume)は
            # **連鎖 1 通目のみ**発火 — 無人連鎖の 2 通目以降まで webhook を
            # 連打しない(task_resume_chain は配達 driver が載せる連鎖カウント)。
            if (
                not events_stream.has_subscribers
                and not metadata.get("agent_session")
                and (
                    not metadata.get("task_resume")
                    or metadata.get("task_resume_chain", 1) <= 1
                )
            ):
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
