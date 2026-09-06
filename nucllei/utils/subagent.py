"""subagent(agent session)実行ロジック(issue #107 / #109)。

親チャットのターン中に擬似 tool `run_subagent` で子の agent session
(kind="agent" の一級チャット)を spawn し、サーバー内部で 1 ターン
(process_chat_payload → generate_chat_completion → process_chat_response)を
完走させて、最終回答だけを tool result として親へ返す(transcript は返さない)。

非同期形(#110): 汎用 execution 基盤に一本化。run_subagent エントリは
`backgroundable: True` で、共有 `background` 引数(middleware が適格 tool の spec
コピーへ注入)により exec 行へ昇格する — 完了結果はサーバ駆動配達
(utils/execution_resume.py)が親 chat へ通知ターンとして届け、能動的な回収は
builtin `await_task` / `check_task`(utils/exec_tools.py)。#109 の専用
background / await_subagent は撤去済み(同一機能の二重機構になるため — 設計 §12)。
exec stop → runner cancel は下の shield + cancelling() の stop 連鎖で子 chat まで
届く。session_id = 継続可能ハンドル / task_id(exec 行)= 1 実行の記録、の二層
(§10)。内部 3 段ターン起動(start_internal_turn)はサーバ駆動配達の続きターンと
共有する(契約 M5)。

enforce もここに閉じる:
- preset allowlist(親 preset の meta.subagentPresetIds)∩ アクセス可
  (所有者 or read ACL)。enum 構築と callable 実行時の両方で同一述語を評価する。
- 機能フラグ meta.features.subagent(既定 ON — skills と対称)
- depth 上限(SUBAGENT_MAX_DEPTH。user chat = depth 0)
- プロセス内同時実行数(SUBAGENT_MAX_CONCURRENT。超過は待たずにエラー)

構造は skills.py(build_load_skill_tool)と同型。middleware(process_chat_payload)
が mount するため、3 段パイプラインの import は実行時に遅延させる(循環回避)。
"""

import asyncio
import json
import logging

from fastapi import Request

from nucllei.models.chats import ChatForm, Chats, append_turn_messages
from nucllei.models.models import Models
from nucllei.tasks import get_task, list_task_ids_by_chat_id, stop_task
from nucllei.utils import tool_output
from nucllei.utils.access_control import has_access
from nucllei.utils.chat import generate_chat_completion
from nucllei.utils.completion_events import CompletionEventStream
from nucllei.utils.content_blocks import convert_content_blocks_to_messages
from nucllei.utils.executions import in_exec
from nucllei.utils.misc import get_message_list
from nucllei.env import (
    SRC_LOG_LEVELS,
    SUBAGENT_MAX_CONCURRENT,
    SUBAGENT_MAX_DEPTH,
    SUBAGENT_TURN_TIMEOUT,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 親へ返す answer の切り詰め長(超過時は truncated: true を付ける)
ANSWER_MAX_CHARS = 8000

# プロセス内の同時実行カウンタ。イベントループ上の async callable からしか
# 触らない(execute_tool_calls は async tool をループ上で直接 await する)ため、
# lock は不要 — check と increment の間に await を挟まないことだけが規律。
_active_turns = 0

# 実行中の agent session id 集合。同一 session への並行継続は chat JSON の
# read-modify-write を後勝ちで壊す(先行ターンの user/assistant 対が消え、
# _persist_message_snapshot が孤児 assistant を復活させて木が壊れる)ため、
# 継続経路の入口で拒否する。新規 spawn も実行中は載せておく(継続との一貫)。
# check→add の間に await を挟まない(_active_turns と同じ規律)。
_running_agent_sessions: set[str] = set()


def _release_turn_slot(session_id: str | None):
    """同時実行カウンタと実行中集合の解放(#109 で一本化)。

    子 task が起動した後は、この解放を子 task の done callback で行う —
    callable が cancel(親 stop / exec stop)で先に消えても、走り続ける子の
    会計を子自身の終端まで保持できる(callable が shield 越しに task 完走を
    待つ通常経路では、待ちの再開前に callback が走るだけで等価)。
    task 起動前のエラー経路は callable の finally が直接これを呼ぶ。
    tasks.py の cleanup_task(task 登録簿の掃除)とは独立した callback で、
    互いに干渉しない(add_done_callback は複数登録可)。
    """
    global _active_turns
    _active_turns -= 1
    if session_id is not None:
        _running_agent_sessions.discard(session_id)


def get_model_subagent_preset_ids(model) -> list[str]:
    return (model or {}).get("info", {}).get("meta", {}).get(
        "subagentPresetIds", []
    ) or []


def is_subagent_feature_enabled(model) -> bool:
    """モデル(preset)の機能「サブエージェント」が有効か(#107)。

    meta.features.subagent に持つ(既定 ON — キー不在は有効)。skills と対称で、
    グローバルフラグは作らない(制御面はモデル設定に一元化)。
    """
    meta = (model or {}).get("info", {}).get("meta", {}) or {}
    features = meta.get("features") or {}
    return features.get("subagent", True) is not False


def _accessible_subagent_presets(model, user) -> list:
    """allowlist(meta.subagentPresetIds)∩ ユーザーがアクセスできる preset 行。

    述語は「所有者 or has_access(read)」— check_model_access には依存しない
    (admin バイパス・BYPASS_MODEL_ACCESS_CONTROL の穴があるため)。enum 構築と
    callable 実行時の両方でこの同一述語を評価する。ただし子ターン自体は
    generate_chat_completion を通るので既存検証も重ねて効く。

    Models.get_model_by_id を件数分引く(allowlist は少数前提。
    get_models_by_user_id の全件走査は使わない)。is_active は見ない —
    無効 preset は「ユーザーのモデル一覧に出ない」だけで、subagent の
    spawnable としては選択・実行できる(2026-08-14 仕様変更。裏方 preset を
    ピッカーから隠しつつ subagent 専用で使う運用のため。実行時の子モデルは
    _resolve_child_model が合成する)。
    """
    presets = []
    for preset_id in get_model_subagent_preset_ids(model):
        m = Models.get_model_by_id(preset_id)
        if m is None:
            continue
        if m.user_id == user.id or has_access(user.id, "read", m.access_control):
            presets.append(m)
    return presets


def _resolve_child_model(request, preset) -> dict | None:
    """子ターンのモデル dict を解決する(#107 仕様変更 2026-08-14)。

    アクティブ preset は get_all_models が app.state.MODELS に載せているので
    そのまま使う。is_active=False の preset は MODELS に載らない(=ユーザーの
    モデル一覧に出ない)が spawnable ではあるため、Model 行 + base モデルから
    get_all_models のアクティブ preset 合成(utils/models.py)と同じ形の dict を
    その場で合成する。base モデル自体が MODELS に無ければ None(従来どおり
    「preset が利用できません」)。
    """
    model = request.app.state.MODELS.get(preset.id)
    if model is not None:
        return model

    # base の解決は get_all_models と同じ述語(ollama の 'llama3' vs 'llama3:7b'
    # 形式ゆれを含む)
    base = None
    for m in request.app.state.MODELS.values():
        if (
            preset.base_model_id == m["id"]
            or preset.base_model_id == m["id"].split(":")[0]
        ):
            base = m
            break
    if base is None:
        return None

    return {
        "id": preset.id,
        "name": preset.name,
        "object": "model",
        "created": preset.created_at,
        "owned_by": base.get("owned_by", "unknown owner"),
        "info": preset.model_dump(),
        "preset": True,
        **({"pipe": base["pipe"]} if "pipe" in base else {}),
        # actions は表示専用(get_all_models が /api/models 向けに解決する)で、
        # チャット実行経路は読まない — 子ターン用の合成では空で足りる
        "actions": [],
    }


def _error(message: str) -> str:
    """エラーは例外にせず tool result 文字列(JSON)で返し、モデルに軌道修正を委ねる。"""
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _wire_messages_from_history(messages_map: dict, leaf_id: str) -> list:
    """子 chat の history 樹から wire 形(OpenAI messages)を再構成する。

    FE buildWireMessages(src/lib/utils/chat-messages.ts)の backend 対応物:
    user は content をそのまま、assistant は content_blocks があれば
    convert_content_blocks_to_messages(tool 往復込み・reasoning 除外)、
    無ければ content 文字列。空 assistant(今回の応答先)は自然に落ちる。
    """
    chain = get_message_list(messages_map, leaf_id) or []
    wire = []
    for message in chain:
        role = message.get("role")
        if role == "user":
            wire.append({"role": "user", "content": message.get("content") or ""})
        elif role == "assistant":
            blocks = message.get("content_blocks") or []
            if blocks:
                wire.extend(convert_content_blocks_to_messages(blocks))
            elif (message.get("content") or "").strip():
                wire.append({"role": "assistant", "content": message["content"]})
    return wire


def _append_turn_messages(
    chat_json: dict, instruction: str, model_id: str, model_name: str
):
    """子 chat の history に user メッセージ + 空 assistant メッセージを積む。

    組み立ての実体は models 層の append_turn_messages(#110 でサーバ駆動配達の
    notice 追記と共通化 — 単一トランザクション内で同じ形を作れるように寄せた)。
    middleware の _persist_message_snapshot はこの assistant メッセージへ
    スナップショットをマージする。戻り値は (user_message_id, assistant_message_id)。
    """
    return append_turn_messages(
        chat_json,
        {"role": "user", "content": instruction},
        {
            "role": "assistant",
            "content": "",
            "model": model_id,
            "modelName": model_name,
        },
    )


# 内部起動 Request が親 scope から引き継ぐ key。path / query_string は引き継がない
# (内部ターンは特定のルートの要求ではない)が、request.url が KeyError('path') で
# 死なないよう "/" を置く。
_INHERITED_SCOPE_KEYS = (
    "headers",
    "scheme",
    "server",
    "client",
    "http_version",
    "root_path",
)


def request_origin(request) -> dict | None:
    """受信 Request から自オリジン `{"scheme", "host"}` を取り出す。

    読み方は llens doc tool と同じ(Caddy 越しの X-Forwarded-Proto / X-Forwarded-Host、
    無ければ scope の scheme / Host)。取れなければ None。background 実行(#110)の
    続きターンには受信 Request が無いので、spawn 時にこれを exec 行へ残し、resume 時に
    `internal_request(origin=…)` で復元する。
    """
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return None
    headers: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        try:
            headers.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))
        except (AttributeError, UnicodeDecodeError):
            continue
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    host = headers.get("x-forwarded-host") or headers.get("host") or ""
    host = host.split(",")[0].strip()
    if not host:
        return None
    return {"scheme": proto or scope.get("scheme") or "http", "host": host}


def internal_request(
    app, *, scope_from: dict | None = None, origin: dict | None = None
):
    """内部起動ターン用の Request。app だけを共有し state は新しい(親の state に書かない)。

    - `scope_from`(親 Request の scope)があれば `_INHERITED_SCOPE_KEYS` を引き継ぐ
      (headers は複製)。
    - `origin`(`request_origin` の形)があれば scheme と Host / X-Forwarded-* ヘッダを
      それから組む(受信 Request の無い resume ターン用)。
    どちらも無くても `request.url` が例外にならない最小 scope を持つ。
    """
    scope: dict = {
        "type": "http",
        "app": app,
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "http_version": "1.1",
        "server": None,
        "client": None,
        "headers": [],
    }
    if isinstance(scope_from, dict):
        for key in _INHERITED_SCOPE_KEYS:
            if key in scope_from:
                scope[key] = scope_from[key]
        scope["headers"] = list(scope_from.get("headers") or [])
    if origin and origin.get("host"):
        scheme = str(origin.get("scheme") or "http")
        host = str(origin["host"])
        scope["scheme"] = scheme
        scope["headers"] = [
            (b"host", host.encode("latin-1", "ignore")),
            (b"x-forwarded-proto", scheme.encode("latin-1", "ignore")),
            (b"x-forwarded-host", host.encode("latin-1", "ignore")),
        ]
    return Request(scope=scope)


async def start_internal_turn(
    request,
    model,
    user,
    chat_id,
    message_id,
    messages,
    *,
    metadata_extra: dict,
    form_extra: dict | None = None,
):
    """chat の 1 ターンを内部起動する(main.py chat_completion の内部版 3 段)。

    subagent の子ターンと、サーバ駆動配達の続きターン(#110 契約 M5 —
    utils/execution_resume.py)が共有する。新造 Request / 専用 stream(購読なし)/
    process_chat_payload → generate_chat_completion → process_chat_response の
    3 段は同一で、ターンの性格(agent_session / task_resume)は metadata_extra が
    与える。process_chat_response が create_task したタスクを見つけて
    (task_id, task) を返す(完走は待たない)。task は None になりうる
    (既に完走・回収済み)。

    Request の罠: 呼び出し元の Request を使い回さない。generate_chat_completion は
    request.state.metadata を form_data["metadata"] へマージするため、親の
    request.state にこのターンの metadata を書くと親の追撃呼び出し(tool ループ)が
    それを拾う。app だけ共有する新しい Request を作り、その state に載せる
    (元 Request の state には一切書かない)。
    """
    # 循環 import 回避: middleware が本モジュールを import する(mount 側)
    from nucllei.utils.middleware import process_chat_payload, process_chat_response

    # llens(#110): background 実行の runner が立てた in_exec は ContextVar なので
    # このターンにも伝播しうる。内部起動するターンは対象 chat の正規のターンであり、
    # 自分の message へ status / message / replace を書くのは append-only 上も正当
    # (遮断したいのは「終わった親ターンの message への遅延書き込み」だけ)。
    # ターン境界で False に戻す — reset せず False を敷くのは、この関数を抜けた
    # 後も runner 側の in_exec=True を保つため(finally で元に戻す)。
    exec_token = in_exec.set(False)

    # 親の scope(headers / scheme / server / client …)を引き継ぐ — tool は __request__ の
    # X-Forwarded-Proto / X-Forwarded-Host / Host から自オリジンを組んで文書 URL を
    # 絶対化する(llens doc tool)。ヘッダ空の合成 Request だと子ターンで origin が
    # 組めず相対 /ext/ に落ちて feed_image が拒否する(行き止まり)
    child_request = internal_request(
        request.app, scope_from=getattr(request, "scope", None)
    )
    # tool server(auth_type=session)が request.state.token を読む経路のための引き継ぎ
    if hasattr(request.state, "token"):
        child_request.state.token = request.state.token

    metadata = {
        "user_id": user.id,
        "chat_id": chat_id,
        "message_id": message_id,
        "files": None,
        "variables": None,
        "model": model,
        **metadata_extra,
    }
    form_data = {
        "model": model["id"],
        "stream": True,
        "messages": messages,
        **(form_extra or {}),
        "metadata": metadata,
    }
    child_request.state.metadata = metadata

    events_stream = CompletionEventStream(chat_id=chat_id, message_id=message_id)
    # 購読は立てない: 立てても誰も消費せず、2 秒毎の content_blocks 全量
    # スナップショット含む全イベントがターン完了まで queue に溜まるだけ
    # (長ターンで数十 MB)。webhook(「誰も見ていなかった」通知)の誤発火は
    # metadata の性格マーカ(agent_session / task_resume の連鎖 2 通目以降)で
    # process_chat_response 側が抑止する(タイトル抑止と対称)。返却 SSE
    # (sse_event_generator)は返却後にイテレートされない遅延評価のため
    # queue=None(既定)で安全。ストリーム自体は register_stream されるので、
    # FE は task 一覧(GET /api/tasks/chat/{chat_id})から再アタッチできる。

    try:
        form_data, metadata, events = await process_chat_payload(
            child_request, form_data, user, metadata, model, events_stream
        )
        response = await generate_chat_completion(child_request, form_data, user)
        # tasks=None: 内部起動ターンではタイトル生成等のバックグラウンドタスクを
        # 予約しない(subagent は spawn 時の決定的タイトルが正 / 続きターンは
        # 契約 M5 — background_tasks None)。戻り値(SSE 応答)は捨てる。
        # ここで create_task されるタスクは**この時点の context を複製する**ので、
        # in_exec=False がターン全体に効く(reset は下の finally で行う)。
        await process_chat_response(
            child_request,
            response,
            form_data,
            user,
            metadata,
            model,
            events,
            None,
            events_stream=events_stream,
        )

        # process_chat_response が create_task したタスク(id=chat_id)を探す。
        # ターンは逐次実行なので末尾が今回のタスク。
        task_ids = list_task_ids_by_chat_id(chat_id)
        if not task_ids:
            raise RuntimeError(f"internal turn task not found (chat_id={chat_id})")
        turn_task_id = task_ids[-1]
        return turn_task_id, get_task(turn_task_id)
    finally:
        in_exec.reset(exec_token)


async def _await_child_task(child_task_id, child_task):
    """子タスクの完走を待つ(stop 連鎖の分岐点)。

    戻り値は stopped(bool): True = 子タスクが外部から stop された(persisted
    済みの部分回答しか無い)。False = 完走。

    親(自タスク)への cancel(親ターンの stop / turn timeout / background 昇格時の
    exec stop — #110)を受けたときは子を stop してから re-raise する
    (stop 連鎖 — #107 決定事項 10。孤児タスクを作らない)。
    """
    try:
        # shield 必須: 素の `await child_task` だと親への cancel が
        # _fut_waiter 経由で子タスクへ転送され、子は CancelledError を
        # 内部処理して正常終了する(tasks.py stop_task の注記参照)ため、
        # cancel が子に吸収されて親コルーチンに届かない。shield で
        # cancel をこちら側で受け、下の stop 連鎖分岐を成立させる。
        await asyncio.shield(child_task)
    except asyncio.CancelledError:
        # この CancelledError には 2 源がある(shield 越しの await はどちらも
        # 同じ例外を投げる):
        # (a) 親(自タスク)への cancel(stop / turn timeout)の配達
        #     — 自タスクに cancel 要求がある(cancelling() > 0。Python 3.11+)
        # (b) 子タスク自身が cancelled で終了した(所有者が子 task を直接 stop、
        #     大 try 前の await 中の cancel 配達等)— 自タスクへの要求は無い
        # (b) まで re-raise すると execute_tool_calls(Exception しか捕らない)を
        # 素通りして親ターン全体が interrupted になるため、(a) のみ re-raise する。
        if asyncio.current_task().cancelling():
            # stop 連鎖: 親の cancel を子タスクへ伝播してから re-raise する。
            # 孤児タスク(親なき走行中の子)を作らない。
            try:
                await stop_task(child_task_id)
            except ValueError:
                pass  # 既に完走・回収済み
            raise
        # (b) 子側発: 親ターンは継続し、呼び出し元が persisted 済みの
        # 部分回答を tool result に載せる(stopped=True で error を付ける)。
        return True
    return False


async def _format_turn_result(child_chat_id: str, assistant_message_id: str, stopped):
    """完走後の子 chat から最終 assistant メッセージだけを親向け結果に整形する。"""
    message = (
        await asyncio.to_thread(
            Chats.get_message_by_id_and_message_id,
            child_chat_id,
            assistant_message_id,
        )
        or {}
    )
    answer = message.get("content") or ""
    error = message.get("error")
    usage = message.get("usage") or {}

    result = {"ok": error is None and not stopped, "session_id": child_chat_id}
    if error is not None:
        result["error"] = (
            error.get("content") if isinstance(error, dict) else str(error)
        )
    elif stopped:
        # 子タスクが外部から stop された(親ターンは継続)。answer は
        # persisted 済みの部分回答。
        result["error"] = "agent session は停止されました(回答は途中まで)。"
    result["answer"] = answer[:ANSWER_MAX_CHARS]
    if len(answer) > ANSWER_MAX_CHARS:
        result["truncated"] = True
    if usage:
        result["usage"] = {
            key: usage[key]
            for key in ("prompt_tokens", "completion_tokens", "duration")
            if usage.get(key) is not None
        }
    return result


def build_run_subagent_tool(request, model, user, metadata):
    """後方互換の薄い wrapper — run_subagent エントリ単体を返す(テスト・旧呼び出し向け)。"""
    tools = build_subagent_tools(request, model, user, metadata)
    return tools["run_subagent"] if tools else None


def build_subagent_tools(request, model, user, metadata):
    """preset allowlist から擬似 tool `run_subagent` を合成する(#107 / #110)。

    None(tool を載せない)条件:
    - 機能フラグ OFF / allowlist(meta.subagentPresetIds)空 / アクセス可 preset ゼロ
    - chat_id / message_id 無し(素の completions — 子を紐づける親メッセージが無い)
    - 親 depth+1 が SUBAGENT_MAX_DEPTH 超(実行時にも再チェック)

    戻り値は {name: tool エントリ} — エントリは native ループ互換の
    {"spec", "callable", "render", "timeout", "backgroundable"}。timeout は
    per-tool キー(execute_tool_calls が CHAT_TOOL_EXECUTION_TIMEOUT より優先)—
    子ターン全体を包む/待つため長い(background 昇格時は exec runner の
    wait_for 上限としても同キーが効く — 設計 D5)。backgroundable: True は
    #110 の呼び出し面(K2)への opt-in — builtin では run_subagent のみ既定適格
    (D4)。共有 background 引数はここでは宣言しない(middleware が適格 tool の
    spec コピーへ注入する)。
    """
    if not is_subagent_feature_enabled(model):
        return None
    if not get_model_subagent_preset_ids(model):
        return None

    parent_chat_id = (metadata or {}).get("chat_id")
    parent_message_id = (metadata or {}).get("message_id")
    if not (parent_chat_id and parent_message_id):
        return None

    # 軽量 lookup(kind, depth)を使う — get_chat_by_id は chat JSON 全体
    # (MB 級になりうる)を運ぶため、mount のたびに読むには重い。
    parent_row = Chats.get_chat_kind_and_depth_by_id(parent_chat_id)
    if parent_row is None:
        return None
    child_depth = parent_row[1] + 1
    if child_depth > SUBAGENT_MAX_DEPTH:
        return None

    presets = _accessible_subagent_presets(model, user)
    if not presets:
        return None

    listing = "\n".join(
        f"- {m.id}: {m.name}"
        + (
            f" — {m.meta.description.strip()}"
            if (m.meta.description or "").strip()
            else ""
        )
        for m in presets
    )
    spec = {
        "name": "run_subagent",
        "description": (
            "サブエージェント(agent session)を起動し、instruction を 1 ターン実行させる。"
            "返るのは最終回答(answer)のみ — 実行過程(transcript)は親には来ない。"
            "会話を継続するには、結果の session_id を session_id 引数に渡して"
            "追いの instruction を送る。"
            "利用可能な preset:\n" + listing
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "preset_id": {
                    "type": "string",
                    "description": "起動する preset の id。新規セッション時は必須(session_id 継続時は不要)。",
                    "enum": [m.id for m in presets],
                },
                "instruction": {
                    "type": "string",
                    "description": "サブエージェントへの指示。",
                },
                "session_id": {
                    "type": "string",
                    "description": "継続する既存 agent session の id(省略で新規セッション)。",
                },
            },
            "required": ["instruction"],
        },
    }

    async def run_subagent(
        preset_id: str = "",
        instruction: str = "",
        session_id: str = "",
    ):
        global _active_turns

        if not (instruction or "").strip():
            return _error("instruction は必須です。")

        # 同時数上限: 超過は待たずに即エラー(check と increment の間に await を挟まない)
        if _active_turns >= SUBAGENT_MAX_CONCURRENT:
            return _error(
                f"サブエージェントの同時実行数が上限({SUBAGENT_MAX_CONCURRENT})に"
                "達しています。しばらくしてから再試行してください。"
            )
        _active_turns += 1
        # 実行中登録した agent session id。None = 未登録。
        running_session_id = None
        # 会計(_active_turns / _running_agent_sessions)の解放を子 task の
        # done callback へ移譲済みか。移譲前のエラー経路は finally が解放する。
        handed_off = False
        try:
            # 実行時再チェック(mount 後に状態が変わりうる)。認可は enum 構築と
            # 同一述語をその場で再評価する(履歴・enum に委ねない)。
            parent = await asyncio.to_thread(Chats.get_chat_by_id, parent_chat_id)
            if parent is None or parent.deleted_at is not None:
                return _error("親チャットが見つかりません。")
            depth = parent.depth + 1
            if depth > SUBAGENT_MAX_DEPTH:
                return _error(
                    f"サブエージェントの入れ子深さが上限({SUBAGENT_MAX_DEPTH})を"
                    "超えるため起動できません。"
                )
            accessible = {
                m.id: m
                for m in await asyncio.to_thread(
                    _accessible_subagent_presets, model, user
                )
            }

            if session_id:
                # 継続: 既存の子セッションへ追いメッセージ。
                # 並行継続の排他(lost update 防止): 実行中なら待たずに即エラー。
                if session_id in _running_agent_sessions:
                    return _error(
                        f"この agent session は実行中です: {session_id}"
                        "(完了を待ってから継続してください)"
                    )
                _running_agent_sessions.add(session_id)
                running_session_id = session_id
                child = await asyncio.to_thread(Chats.get_chat_by_id, session_id)
                if (
                    child is None
                    or child.deleted_at is not None
                    or child.user_id != user.id
                    or child.kind != "agent"
                    or child.parent_chat_id != parent_chat_id
                ):
                    return _error(
                        f"session_id が不正です: {session_id}"
                        "(このチャットから起動した agent session のみ継続できます)"
                    )
                chat_json = child.chat
                target_preset_id = (chat_json.get("models") or [None])[0]
                preset = accessible.get(target_preset_id)
                if preset is None:
                    return _error(f"preset にアクセスできません: {target_preset_id}")
                _, assistant_message_id = _append_turn_messages(
                    chat_json, instruction, preset.id, preset.name
                )
                await asyncio.to_thread(Chats.update_chat_by_id, session_id, chat_json)
                child_chat_id = session_id
            else:
                # 新規 spawn
                if not preset_id:
                    return _error("新規セッションには preset_id が必須です。")
                preset = accessible.get(preset_id)
                if preset is None:
                    return _error(f"preset にアクセスできません: {preset_id}")
                # 決定的タイトル(生成タスクは走らせない — agent_session マーカで抑止)
                title = f"{preset.name} — {' '.join(instruction.split())[:40]}"
                chat_json = {
                    "title": title,
                    "models": [preset.id],
                    "params": {},
                    "history": {"messages": {}, "currentId": None},
                    "messages": [],
                }
                _, assistant_message_id = _append_turn_messages(
                    chat_json, instruction, preset.id, preset.name
                )
                child = await asyncio.to_thread(
                    Chats.insert_new_chat,
                    user.id,
                    ChatForm(chat=chat_json),
                    kind="agent",
                    parent_chat_id=parent_chat_id,
                    parent_message_id=parent_message_id,
                    depth=depth,
                )
                if child is None:
                    return _error("agent session の作成に失敗しました。")
                child_chat_id = child.id
                # 新規 id は衝突しない(uuid)が、spawn 完了→実行の間も継続経路
                # から「実行中」に見えるよう同じ集合へ載せる
                _running_agent_sessions.add(child_chat_id)
                running_session_id = child_chat_id

            child_model = _resolve_child_model(request, preset)
            if child_model is None:
                return _error(f"preset が利用できません: {preset.id}")

            # 実行中表示(#100)に子 session id を出す(P3 の観測 view の手がかり。
            # 結果 JSON にも session_id が載る)
            tool_output.progress(f"agent session {child_chat_id}")

            wire_messages = _wire_messages_from_history(
                chat_json["history"]["messages"], assistant_message_id
            )
            child_task_id, child_task = await start_internal_turn(
                request,
                child_model,
                user,
                child_chat_id,
                assistant_message_id,
                wire_messages,
                # タイトル生成(通常 + 異常終了フォールバック)・webhook の抑止
                # マーカ。spawn 時の決定的タイトルが正(middleware 側が読む)。
                metadata_extra={"agent_session": True},
            )

            stopped = False
            if child_task is not None:
                # 会計の解放を子 task の done callback へ移譲(#109 決定事項 4)。
                # cancel(親 stop / exec stop)で callable が先に消えても、
                # 走り続ける子の会計を子自身の終端まで保持できる(通常経路は
                # shield 待ちの再開前に callback が走るだけで等価)。
                child_task.add_done_callback(
                    lambda t, sid=running_session_id: _release_turn_slot(sid)
                )
                handed_off = True

                stopped = await _await_child_task(child_task_id, child_task)

            # 完走後の子 chat を読み直し、最終 assistant メッセージだけを親へ返す
            return await _format_turn_result(
                child_chat_id, assistant_message_id, stopped
            )
        finally:
            if not handed_off:
                _release_turn_slot(running_session_id)

    # render.result = subagent(#20 のディスクリプタ駆動): フロントは
    # {ok, session_id, answer, ...} を subagent 表示で描画する(P3)。
    # background 昇格時はこの宣言が exec 行の meta.render_descriptor に保存され、
    # await_task の回収結果へパススルーされる(#110 §8)。
    return {
        "run_subagent": {
            "spec": spec,
            "callable": run_subagent,
            "render": {"result": {"kind": "subagent"}},
            "timeout": SUBAGENT_TURN_TIMEOUT,
            # #110(K2/D4): builtin で唯一の既定適格。共有 background 引数は
            # middleware が spec のコピーへ注入する(ここでは宣言しない —
            # 自前宣言は恒久不適格の衝突ガードに掛かる)。
            "backgroundable": True,
        },
    }
