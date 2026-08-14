"""非同期 tool 実行(#110)の呼び出し面 — 適格性判定・spec 注入・回収 builtin。

K2(呼び出し面)の実装: tool 単位の静的適格性 `backgroundable`(tool エントリの
per-tool キー — `timeout` と同型)+ 呼び出し時の共有 `background` 引数(適格 tool の
spec の**コピー**にのみ注入 — tools_dict 側の spec は汚さない。unknown-drop の基準は
元 spec のまま)。昇格された呼び出しは execution チャンネル(exec 行 —
utils/executions.py)に乗り、回収は本モジュールの builtin `check_async`(K4:
非ブロッキング poll)/ `await_async`(K5: 有界待ち)が担う。

適格性(設計 D4 — opt-in):
- builtin では run_subagent のみ既定適格(subagent.py がエントリに付与)。
- DB tool は tool 作者(CRUD は admin 専用 = admin の明示指定)が `Tools` クラス
  属性 `backgroundable` で宣言する: True(全関数)または関数名の list/tuple/set
  (utils/tools.py の get_tools が per-entry キーへ落とす。`file_handler` と同型)。
- `load_skill` / `check_async` / `await_async` と、自前で `background` パラメータを
  宣言する tool は恒久不適格(衝突ガード — warning ログ)。tool server は v1 では
  opt-in 面を持たない(常に不適格)。
- D9: agent session(子ターン)には注入しない — 子 chat の exec は FE 回収者不在で
  宙吊りになる。判定は metadata の agent_session マーカ(exec_spawn_context_ok)。
"""

import asyncio
import json
import logging
import time

from nucllei.env import SRC_LOG_LEVELS
from nucllei.models.executions import Executions
from nucllei.utils import tool_output
from nucllei.utils.executions import (
    async_exec_available,
    ensure_execution_liveness,
    get_exec_task,
    get_latest_progress,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# await_async の有界待ち(K5 / 承認済み Q8): 既定 90 秒、10..300 に clamp
AWAIT_ASYNC_DEFAULT_TIMEOUT = 90
AWAIT_ASYNC_MIN_TIMEOUT = 10
AWAIT_ASYNC_MAX_TIMEOUT = 300

# 恒久不適格(D4): 回収 builtin 自身と load_skill は昇格対象にしない
EXEC_INELIGIBLE_TOOL_NAMES = {"load_skill", "check_async", "await_async"}

# 適格 tool の spec コピーへ注入する共有引数(§7)
BACKGROUND_PARAM_SPEC = {
    "type": "boolean",
    "description": (
        "true で非同期実行。即座に exec_id が返り、結果は await_async / "
        "check_async で扱う。通常は指定しない。"
    ),
}


def exec_spawn_context_ok(metadata) -> bool:
    """このターンで background 昇格を許すか(spec 注入と execute 分岐の共通述語)。

    条件は 2 つ:
    - サーバー構成が非同期実行を持てること(D7 — `UVICORN_WORKERS > 1` では
      registry がプロセス内で完結せず spawn は拒否される。**述語をここで AND して
      おかないと** spec 注入・回収 builtin の mount・実行分岐がバラバラに振る舞い、
      モデルには background 引数が見えるのに呼ぶと必ずエラーになる)
    - exec 行の束縛に必要な user_id / chat_id / message_id が揃い、かつ
      agent session(子ターン)でないこと(D9 — 子 chat の exec は FE 回収者不在)
    """
    md = metadata or {}
    return bool(
        async_exec_available()
        and md.get("user_id")
        and md.get("chat_id")
        and md.get("message_id")
        and not md.get("agent_session")
    )


def entry_is_backgroundable(name: str, entry: dict) -> bool:
    """tool エントリが background 昇格に適格か(K2 静的適格性 + D4 衝突ガード)。"""
    if not entry.get("backgroundable"):
        return False
    if name in EXEC_INELIGIBLE_TOOL_NAMES:
        log.warning(
            f"tool '{name}' is permanently ineligible for background execution; "
            "ignoring its backgroundable flag"
        )
        return False
    properties = (entry.get("spec") or {}).get("parameters", {}).get("properties") or {}
    if "background" in properties:
        log.warning(
            f"tool '{name}' declares its own 'background' parameter; "
            "ignoring its backgroundable flag (collision guard)"
        )
        return False
    return True


def spec_for_model(name: str, entry: dict, allow_background: bool) -> dict:
    """モデルへ渡す spec を返す(適格なら background 引数を注入した**コピー**)。

    tools_dict 側の spec オブジェクトは変異しない — execute_tool_calls の
    unknown-drop(allowed_params フィルタ)は元 spec を基準にし続ける。
    """
    spec = entry.get("spec", {})
    if not (allow_background and entry_is_backgroundable(name, entry)):
        return spec
    parameters = spec.get("parameters") or {}
    properties = dict(parameters.get("properties") or {})
    properties["background"] = dict(BACKGROUND_PARAM_SPEC)
    return {**spec, "parameters": {**parameters, "properties": properties}}


def _error(message: str) -> str:
    """エラーは例外にせず tool result 文字列(JSON)で返す(subagent.py と同流儀)。"""
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _running_form(row) -> dict:
    return {
        "exec_id": row.id,
        "tool_name": row.tool_name,
        "status": "running",
        "elapsed_s": max(0, int(time.time()) - row.created_at),
    }


def build_exec_tools(user, metadata) -> dict:
    """回収 builtin `check_async` / `await_async` を合成する(§8)。

    mount 判定(適格 tool が載る、または chat に exec 行がある)は呼び出し側
    (middleware.process_chat_payload)が行う。認可述語は「行が存在 ∧ 行の
    user_id == user.id ∧ 行の chat_id == 親 chat_id」(subagent の session 検証と
    同型)— exec_id は履歴に載るが単なるキーで、権威は server-authored な exec 行の
    束縛(#101 整合)。builtin なので履歴再導出(#101)は不要。
    """
    parent_chat_id = (metadata or {}).get("chat_id")

    async def _load_authorized(exec_id: str):
        """(row, error) — 認可述語を満たす行の読み出し。error は _error 文字列。"""
        if not (exec_id or "").strip():
            return None, _error("exec_id は必須です。")
        row = await asyncio.to_thread(Executions.get_execution_by_id, exec_id)
        if row is None or row.user_id != user.id or row.chat_id != parent_chat_id:
            return None, _error(
                f"exec_id が不正です: {exec_id}"
                "(このチャットで起動した実行のみ扱えます)"
            )
        return row, None

    check_spec = {
        "name": "check_async",
        "description": (
            "background 実行(exec_id)の現在状態を即座に返す(待たない)。"
            "status と最新の進捗(progress)がわかる。結果本文は返さない — "
            "結果の回収は await_async で行う。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "exec_id": {
                    "type": "string",
                    "description": (
                        "照会する実行の id(background: true の返りに含まれる)。"
                    ),
                },
            },
            "required": ["exec_id"],
        },
    }

    async def check_async(exec_id: str = ""):
        row, error = await _load_authorized(exec_id)
        if error is not None:
            return error

        now = int(time.time())
        out = {"exec_id": row.id, "tool_name": row.tool_name, "status": row.status}
        if row.status == "running":
            out["elapsed_s"] = max(0, now - row.created_at)
            # 最新 progress 1 件(best-effort・プロセス内 — 承認済み Q9)
            progress = get_latest_progress(row.id)
            if progress:
                snippet = {
                    key: progress[key]
                    for key in ("description", "current", "total")
                    if progress.get(key) is not None
                }
                if snippet:
                    out["progress"] = snippet
        else:
            out["elapsed_s"] = max(0, (row.finished_at or now) - row.created_at)
            # terminal でも結果は返さない — 回収は await_async に一本化(§8)
            out["hint"] = "結果は await_async(exec_id) で回収してください。"
        return out

    await_spec = {
        "name": "await_async",
        "description": (
            "background 実行(exec_id)の完了を有界で待ち、結果を回収する。"
            f"timeout 秒(既定 {AWAIT_ASYNC_DEFAULT_TIMEOUT})以内に完了しなければ "
            'status: "running" が返る — その場合は実行中である旨を報告して'
            "ターンを終えること(完了後にあらためて回収できる)。"
            "完了済みの実行には即返す(再回収は冪等)。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "exec_id": {
                    "type": "string",
                    "description": (
                        "回収する実行の id(background: true の返りに含まれる)。"
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        f"待つ秒数({AWAIT_ASYNC_MIN_TIMEOUT}〜"
                        f"{AWAIT_ASYNC_MAX_TIMEOUT}、既定 "
                        f"{AWAIT_ASYNC_DEFAULT_TIMEOUT})。"
                    ),
                },
            },
            "required": ["exec_id"],
        },
    }

    async def await_async(
        exec_id: str = "", timeout: int = AWAIT_ASYNC_DEFAULT_TIMEOUT
    ):
        row, error = await _load_authorized(exec_id)
        if error is not None:
            return error

        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = AWAIT_ASYNC_DEFAULT_TIMEOUT
        timeout = max(AWAIT_ASYNC_MIN_TIMEOUT, min(AWAIT_ASYNC_MAX_TIMEOUT, timeout))

        if row.status == "running":
            task = get_exec_task(row.id)
            if task is not None:
                tool_output.progress(f"execution {row.id}({row.tool_name})を待機中")
                try:
                    # shield 必須: 素の wait_for(task) だと時間切れの cancel が
                    # exec task 本体へ届いて実行を殺す(D6: 待ちを外しても exec は
                    # 走り続ける)。
                    await asyncio.wait_for(asyncio.shield(task), timeout)
                except (asyncio.TimeoutError, TimeoutError):
                    # K5: 時間切れは running 形(exec には触らない)。モデルは
                    # 「実行中」と報告してターンを終える → FE 自動再開が回収する。
                    return _running_form(row)
                except asyncio.CancelledError:
                    # 親ターンの stop(自タスクへの cancel 要求)は re-raise のみ —
                    # exec は止めない(D6: fire の意思の尊重)。さもなくば exec task
                    # 側発の cancel(stop API)— 行を再読して status どおり返す
                    # (subagent._await_child_task と同パターン)。
                    if asyncio.current_task().cancelling():
                        raise
                # 完走(runner は return 前に永続化済み)/ stop 済み — 再読
                row = (
                    await asyncio.to_thread(Executions.get_execution_by_id, row.id)
                    or row
                )
            else:
                # running なのに task 不在 — liveness ガード(§4。spawn 窓
                # グレース内はそのまま running が返る)
                row = await ensure_execution_liveness(row)
            if row.status == "running":
                # グレース内 / 永続化失敗の残骸 — running 形で返す(後の照会で
                # liveness ガード / boot スイープが倒す)
                return _running_form(row)

        if row.status != "done":
            # failed / stopped / orphaned。再 await は冪等(同じ形が返るだけ)
            return {
                "ok": False,
                "exec_id": row.id,
                "tool_name": row.tool_name,
                "status": row.status,
                "error": row.error or "",
            }

        meta = row.meta or {}

        # 戻り値宣言(render_descriptor)は sink 経由で results[].render へ上書き
        # (§8 — subagent 描画に必須)。replay と違い毎回適用する(再 await でも
        # この call の結果ブロックは同じ描画になるべき)。
        render_descriptor = meta.get("render_descriptor")
        if render_descriptor:
            sink = tool_output.current_sink.get()
            if sink is not None:
                sink.set_result_render(render_descriptor)

        # context_images / display は初回のみ自 sink へ replay(mark_replayed の
        # 勝者だけ — VLM 再注入・表示の重複防止)
        context_images = meta.get("context_images") or []
        displays = meta.get("display") or []
        if context_images or displays:
            if await asyncio.to_thread(Executions.mark_replayed, row.id):
                for file_id in context_images:
                    tool_output.feed_image(file_id)
                for item in displays:
                    if isinstance(item, dict):
                        tool_output.display(
                            item.get("content", ""), item.get("kind", "markdown")
                        )

        # 保存 render が json なら生 object に戻す(二重エスケープ回避 — §8)
        result_value = row.result
        if (meta.get("render") or {}).get("kind") == "json" and result_value:
            try:
                result_value = json.loads(result_value)
            except Exception:
                pass  # 壊れていれば文字列のまま返す(切詰め時は render 降格済み)

        out = {
            "ok": True,
            "exec_id": row.id,
            "tool_name": row.tool_name,
            "status": "done",
            "result": result_value,
        }
        if row.duration is not None:
            out["duration"] = row.duration
        if row.usage:
            out["usage"] = row.usage
        if meta.get("truncated"):
            out["truncated"] = True
        if row.purged_at:
            out["hint"] = "結果本文は保持期限を過ぎて削除されています。"
        return out

    return {
        "check_async": {
            "spec": check_spec,
            "callable": check_async,
        },
        "await_async": {
            "spec": await_spec,
            "callable": await_async,
            # 自前の有界待ち(clamp 済み ≤ 300 秒)が running 形を返す設計なので、
            # 外側(execute_tool_calls)の wait_for は無効化する(§8)
            "timeout": 0,
        },
    }
