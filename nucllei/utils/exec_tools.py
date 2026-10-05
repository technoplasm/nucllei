"""非同期 tool 実行(#110)の呼び出し面 — 適格性判定・spec 注入・回収 builtin。

K2(呼び出し面)の実装: tool 単位の静的適格性 `backgroundable`(tool エントリの
per-tool キー — `timeout` と同型)+ 呼び出し時の共有 `background` 引数(適格 tool の
spec の**コピー**にのみ注入 — tools_dict 側の spec は汚さない。unknown-drop の基準は
元 spec のまま)。昇格された呼び出しは execution チャンネル(exec 行 —
utils/executions.py)に乗る。

用語(契約 T1): モデル・UI 向けは「タスク」(task_id / check_task / await_task /
stop_task)。内部(DB テーブル execution・API パス /executions・モジュール名)は
execution のまま — 内部の nucllei/tasks.py(asyncio task registry)との衝突を
避けるため、本モジュールは nucllei/tasks.py を import しない(停止は
executions.stop_execution 経由)。

回収 builtin は役割 1 関数 1 つ(契約 T2):
- `check_task`: 状態確認のみ(待たない・結果本文は決して返さない・read-only)
- `await_task`: 結果取り出しの一本化(有界窓 1 回で複数 id をカバー。終了済みは
  即返 = 冪等)。replay / render_descriptor / delivered_at はここだけ
- `stop_task`: 強制終了(冪等)。報告した terminal 行にも delivered_at を立てる
  (サーバ駆動配達の幽霊ターン防止 — 契約 M1)

完了結果の自動配達はサーバ駆動(契約 T3 — execution_resume の続きターン)。
terminal 形を返した行に delivered_at を立てるのは「この返りでモデルの文脈に結果が
載った = 配達不要」を配達 driver に伝えるため。

適格性(設計 D4 — opt-in):
- tool 行を持つ tool(builtin / DB tool とも)は `Tools` クラス属性 `backgroundable`
  で宣言する: True(全関数)または関数名の list/tuple/set(utils/tools.py の
  get_tools が per-entry キーへ落とす)。DB tool の CRUD は
  admin 専用 = admin の明示指定。builtin では run_subagent(builtin_subagent)と
  sandbox 系が宣言している — #140 で run_subagent も同じ宣言経路に揃え、
  本モジュールに tool 名の特例は無い。
- `load_skill` / 回収 builtin 3 つと、自前で `background` パラメータを宣言する
  tool は恒久不適格(衝突ガード — warning ログ)。tool server は v1 では opt-in 面を
  持たない(常に不適格)。
- D9: agent session(子ターン)には注入しない — 子 chat の exec は回収文脈不在で
  宙吊りになる。判定は metadata の agent_session マーカ(exec_spawn_context_ok)。

mount 条件の分離(契約 M1): 回収 3 tool の mount は「適格 tool が載る ∨ chat に
exec 行」で `async_exec_available()` と独立(読み出しは DB で完結。multi-worker
では boot スイープが running を倒すので liveness 誤爆なし)。spawn 系(spec 注入・
execute 分岐)は従来どおり async_exec_available に依存する。
"""

import asyncio
import json
import logging
import time

from nucllei.env import (
    ASYNC_EXEC_RETENTION_DAYS,
    ASYNC_EXEC_TIMEOUT_S,
    SRC_LOG_LEVELS,
)
from nucllei.models.executions import Executions
from nucllei.utils import tool_output
from nucllei.utils.tool_errors import ToolCallError
from nucllei.utils.executions import (
    ERROR_KIND_META_KEY,
    async_exec_available,
    ensure_execution_liveness,
    get_exec_task,
    get_latest_progress,
    stop_execution,
)

# await_task の説明に載せる保持期間(env に従う — 0 / 未設定は無期限。
# exec 行の purge は ASYNC_EXEC_RETENTION_DAYS 日で走る)
_RETENTION_PHRASE = (
    f"結果は {ASYNC_EXEC_RETENTION_DAYS} 日保持"
    if ASYNC_EXEC_RETENTION_DAYS > 0
    else "結果は無期限に保持"
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# await_task の有界待ち(K5 / 承認済み Q8): 既定 90 秒、10..300 に clamp
AWAIT_TASK_DEFAULT_TIMEOUT = 90
AWAIT_TASK_MIN_TIMEOUT = 10
AWAIT_TASK_MAX_TIMEOUT = 300

# 1 回の呼び出しで扱える task_ids の上限(契約 M0 — wire 膨張の上限)。
# 超過分は per-entry error(全体は失敗させない)。
TASK_IDS_MAX = 20

# check_task(include_done)の列挙上限(新しい順に切る。total は切る前の総数)
CHECK_TASK_LIST_LIMIT = 50

# 回収 builtin 3 tool(mount と切詰め除外の判定に使う — 契約 M0:
# 中身は保存時に切詰め済みで、envelope の json.dumps 膨張で再切詰めすると
# 壊れ JSON になるため、これらの返りは共通切詰めの適用外)
EXEC_RECALL_TOOL_NAMES = ("check_task", "await_task", "stop_task")

# 恒久不適格(D4): 回収 builtin 自身と load_skill は昇格対象にしない
EXEC_INELIGIBLE_TOOL_NAMES = {"load_skill", *EXEC_RECALL_TOOL_NAMES}

# 適格 tool の spec コピーへ注入する共有引数(§7)。適格 tool の数だけ複製されて
# 毎ターン context に載るため最小限に保つ — 機構の詳細(自動配達・使い分け)は
# 1 回しか載らない回収 3 tool(check_task / await_task / stop_task)の spec が持つ。
BACKGROUND_PARAM_SPEC = {
    "type": "boolean",
    "description": (
        "true でバックグラウンド実行(task_id 即返し・結果は自動配達)。通常は指定しない。"
    ),
}


def exec_spawn_context_ok(metadata) -> bool:
    """このターンで background 昇格を許すか(spec 注入と execute 分岐の共通述語)。

    条件は 2 つ:
    - サーバー構成が非同期実行を持てること(D7 — `UVICORN_WORKERS > 1` では
      registry がプロセス内で完結せず spawn は拒否される。**述語をここで AND して
      おかないと** spec 注入・実行分岐がバラバラに振る舞い、モデルには background
      引数が見えるのに呼ぶと必ずエラーになる)
    - exec 行の束縛に必要な user_id / chat_id / message_id が揃い、かつ
      agent session(子ターン)でないこと(D9 — 子 chat の exec は回収文脈不在)
    """
    md = metadata or {}
    return bool(
        async_exec_available()
        and md.get("user_id")
        and md.get("chat_id")
        and md.get("message_id")
        and not md.get("agent_session")
    )


def exec_recall_context_ok(metadata) -> bool:
    """回収 builtin(check_task / await_task / stop_task)を mount できる文脈か。

    spawn 側と違い `async_exec_available()` を含めない(契約 M1 — mount 条件の
    分離): 回収は DB の exec 行を読むだけで registry に依存せず、multi-worker でも
    boot スイープが running を倒すので liveness 誤爆はない。message_id も不要
    (回収は行を作らない)。agent session は D9 のとおり除外。
    """
    md = metadata or {}
    return bool(md.get("user_id") and md.get("chat_id") and not md.get("agent_session"))


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


def background_runs_inline(call_count: int) -> bool:
    """background: true の適格 call を同期実行へ降格するか(2026-10-04 ken 承認)。

    **単独の background 呼び出しは同期で走らせる**: 1 応答に call が 1 本だけ
    (他の tool も並ばない)なら、background にしても親ターンは次に await_task を
    撃つだけで、その 1 往復(実測 3〜5 秒)がまるごと損になる。モデルは子 1 本でも
    3 回に 1 回は background を選ぶ(subagent-timing 実測)。

    2 本以上並ぶ応答では従来どおり昇格する(同期 call 同士は execute_tool_calls が
    並行実行するので、並列のためだけに background を使う必要はもう無いが、
    「投げて親ターンを先に進める」意図は残す)。

    降格した call は通常の同期結果を返し(task_id は出さない・exec 行も作らない)、
    タイムアウトは background と同じ値(`inline_background_timeout`)を使う —
    長い仕事を background にした call が同期既定 300 秒で切られないように。
    代わりに D6(親ターン stop は exec に伝播しない)は効かず、ユーザー停止で
    止まる(同期 call と同じ)。判断はこの関数 1 箇所(呼び出し側は
    middleware.execute_tool_calls のみ)。
    """
    return call_count == 1


def inline_background_timeout(entry: dict):
    """同期へ降格した background call のタイムアウト秒(exec runner と同じ式)。

    per-entry `timeout` が最優先、無ければ ASYNC_EXEC_TIMEOUT_S。非正値は
    「システム側上限なし」(execute_tool_calls は `> 0` のときだけ wait_for)。
    """
    return entry.get("timeout") or ASYNC_EXEC_TIMEOUT_S


def mount_recall_tools(tools_dict: dict, form_data: dict, user, metadata) -> list[str]:
    """回収 builtin 3 tool を tools_dict へ mount する(初回合成と A-3 の共通経路)。

    form_data["tools"] が既に合成済み(= native ループ走行中の遅延 mount)なら
    spec も append する — tools_dict と form_data["tools"] は native ループが
    毎イテレーション参照する同一オブジェクトなので、次のイテレーションから
    モデルに見える(load_skill の第 2 経路と同じ配達機序)。冪等(既 mount は
    スキップ)。戻り値は今回 mount した名前。
    """
    mounted = []
    for name, entry in build_exec_tools(user, metadata).items():
        existing = tools_dict.get(name)
        if existing is not None:
            if existing.get("exec_recall"):
                continue  # 既 mount(初回合成と A-3 の二重呼びは no-op)
            # 同名の外部 tool は builtin が影に置く(load_skill と同じ規律)
            log.warning(f"tool '{name}' is shadowed by the execution builtin")
        tools_dict[name] = entry
        if isinstance(form_data.get("tools"), list):
            form_data["tools"].append({"type": "function", "function": entry["spec"]})
        mounted.append(name)
    return mounted


def build_exec_tools(user, metadata) -> dict:
    """回収 builtin `check_task` / `await_task` / `stop_task` を合成する。

    mount 判定(適格 tool が載る、または chat に exec 行がある)は呼び出し側
    (middleware.process_chat_payload / mount_recall_tools)が行う。認可述語は
    「行が存在 ∧ 行の user_id == user.id ∧ 行の chat_id == 親 chat_id」
    (subagent の session 検証と同型)— task_id は履歴に載るが単なるキーで、
    権威は server-authored な exec 行の束縛(#101 整合)。builtin なので
    履歴再導出(#101)は不要。不正 id は per-entry error で全体は失敗させない
    (契約 T2)。
    """
    parent_chat_id = (metadata or {}).get("chat_id")

    def _invalid_entry(task_id: str) -> dict:
        # 他人・他 chat の行は「存在しない」と同じ応答(存在を漏らさない)
        return {
            "task_id": task_id,
            "error": (
                "task_id が不正 (扱えるのはこのチャットで起動したタスクだけ)。"
                "次の手: background: true の返りの task_id をそのまま渡す。"
            ),
        }

    def _over_limit_entry(task_id: str) -> dict:
        return {
            "task_id": task_id,
            "error": (
                f"1 回の呼び出しで扱える task_id は {TASK_IDS_MAX} 件まで"
                " (この id は処理していない)。次の手: 次の呼び出しで指定する。"
            ),
        }

    def _coerce_ids(task_ids):
        """ids — 単一 string は [str] に coerce。順序を保って dedupe。空・形の誤りは
        call の ToolError(#145 — 呼び出し全体の誤り。per-entry の誤りは返りの形に載る)。"""
        if task_ids is None:
            raise ToolCallError("task_ids が空", "task_id を配列で渡す。")
        if isinstance(task_ids, str):
            task_ids = [task_ids]
        if not isinstance(task_ids, (list, tuple)):
            raise ToolCallError("task_ids の形が違う", "task_id を文字列の配列で渡す。")
        ids = []
        seen = set()
        for tid in task_ids:
            if not isinstance(tid, str):
                tid = "" if tid is None else str(tid)
            tid = tid.strip()
            if not tid or tid in seen:
                continue
            seen.add(tid)
            ids.append(tid)
        if not ids:
            raise ToolCallError("task_ids が空", "task_id を配列で渡す。")
        return ids

    async def _load_rows(ids: list[str]) -> dict:
        """id → 認可済み行(不正 / 他人 / 他 chat は None)。"""
        rows = {}
        for tid in ids:
            row = await asyncio.to_thread(Executions.get_execution_by_id, tid)
            if row is None or row.user_id != user.id or row.chat_id != parent_chat_id:
                rows[tid] = None
            else:
                rows[tid] = row
        return rows

    def _running_entry(row) -> dict:
        return {
            "task_id": row.id,
            "tool_name": row.tool_name,
            "status": "running",
            "started_at": row.created_at,
            "elapsed_s": round(max(0.0, time.time() - row.created_at), 1),
        }

    def _status_entry(row) -> dict:
        """check_task の per-entry 形(常に同じ形。結果本文は決して含めない)。"""
        now = time.time()
        end = now if row.status == "running" else float(row.finished_at or now)
        entry = {
            "task_id": row.id,
            "tool_name": row.tool_name,
            "status": row.status,
            "started_at": row.created_at,
            "elapsed_s": round(max(0.0, end - row.created_at), 1),
        }
        if row.status == "running":
            # 最新 progress 1 件(best-effort・プロセス内 — 承認済み Q9)
            progress = get_latest_progress(row.id)
            if progress:
                snippet = {
                    key: progress[key]
                    for key in ("description", "current", "total")
                    if progress.get(key) is not None
                }
                if snippet:
                    entry["progress"] = snippet
        else:
            if row.duration is not None:
                entry["duration"] = row.duration
            if row.error:
                entry["error"] = row.error
            # #145: 失敗の種別(exec 行 meta)。同期の results[i].error_kind と同じ値
            error_kind = (row.meta or {}).get(ERROR_KIND_META_KEY)
            if error_kind:
                entry["error_kind"] = error_kind
        return entry

    check_spec = {
        "name": "check_task",
        "description": (
            "バックグラウンドタスクの状態を確認する(待たない・結果本文は返さない)。"
            "引数なしで実行中のタスクを列挙、include_done: true で終了済みも含めて"
            "列挙、task_ids 指定でその id の現在状態を返す。完了時は結果が自動で"
            "届く(通知ターン)ため、回収のために呼ぶ必要はない — 今すぐ結果が"
            "必要なら await_task。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "状態を確認するタスク id(background: true の返りに含まれる)。"
                        "省略時はこのチャットの実行中タスクを列挙する。"
                    ),
                },
                "include_done": {
                    "type": "boolean",
                    "description": (
                        "true で終了済み(done/failed/stopped/orphaned)も新しい順に"
                        f"列挙する(最大 {CHECK_TASK_LIST_LIMIT} 件)。"
                    ),
                },
            },
            "required": [],
        },
    }

    async def check_task(task_ids=None, include_done: bool = False):
        # read-only(契約 T2): liveness も書かない。stale な running は boot
        # スイープ / await_task 側のガードが倒す。
        if task_ids:
            ids = _coerce_ids(task_ids)
            over = ids[TASK_IDS_MAX:]
            requested = ids[:TASK_IDS_MAX]
            rows = await _load_rows(requested)
            entries = []
            for tid in requested:
                row = rows[tid]
                entries.append(
                    _invalid_entry(tid) if row is None else _status_entry(row)
                )
            entries.extend(_over_limit_entry(tid) for tid in over)
            return {"tasks": entries, "total": len(entries)}

        rows = await asyncio.to_thread(
            Executions.get_executions_by_chat_id, parent_chat_id
        )
        rows = [row for row in rows if row.user_id == user.id]
        if include_done:
            # 終了済みを含む列挙は新しい順・上限あり(total は切る前の総数)
            rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
            total = len(rows)
            rows = rows[:CHECK_TASK_LIST_LIMIT]
        else:
            # 既定は「残りタスク」= running のみ・古い順
            rows = [row for row in rows if row.status == "running"]
            total = len(rows)
        out = {"tasks": [_status_entry(row) for row in rows], "total": total}
        if not out["tasks"]:
            out["note"] = "タスクは無い" if include_done else "実行中のタスクは無い"
        return out

    await_spec = {
        "name": "await_task",
        "description": (
            "バックグラウンドタスク(task_ids)の完了を有界で待ち、結果を回収する。"
            "終了済みのタスクは待たず即返す(冪等 — "
            f"{_RETENTION_PHRASE}、その間は何度でも再回収できる)。timeout 秒(既定 "
            f"{AWAIT_TASK_DEFAULT_TIMEOUT})の 1 回の待ちで全 id をカバーし、"
            '時間切れのタスクは status: "running" で返る(終わった分だけの部分回収)。'
            "完了時は結果が自動で届く(通知ターン)ため、今すぐ結果が必要なとき"
            "だけ使う — running が返ったら実行中である旨を報告してターンを終えること。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "回収するタスク id(background: true の返りに含まれる)。"
                        f"最大 {TASK_IDS_MAX} 件。"
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        f"待つ秒数({AWAIT_TASK_MIN_TIMEOUT}〜"
                        f"{AWAIT_TASK_MAX_TIMEOUT}、既定 "
                        f"{AWAIT_TASK_DEFAULT_TIMEOUT})。"
                    ),
                },
            },
            "required": ["task_ids"],
        },
    }

    async def await_task(task_ids=None, timeout: int = AWAIT_TASK_DEFAULT_TIMEOUT):
        ids = _coerce_ids(task_ids)
        over = ids[TASK_IDS_MAX:]
        requested = ids[:TASK_IDS_MAX]
        rows = await _load_rows(requested)
        if not over and all(rows[tid] is None for tid in requested):
            # 全不正は全体エラー(部分回収の形を返す意味がない)。ただし上限超過分
            # (over)がある場合は除く(#110 敵対検証 5): 21 件目以降に有効 id が
            # 残っているかもしれず、全体エラーにするとモデルが per-entry の
            # 「次の呼び出しで指定」案内を受け取れない — per-entry 形で返す。
            raise ToolCallError(
                "有効な task_id が無い (扱えるのはこのチャットで起動したタスクだけ)",
                "background: true の返りの task_id をそのまま渡す。",
            )

        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = AWAIT_TASK_DEFAULT_TIMEOUT
        timeout = max(AWAIT_TASK_MIN_TIMEOUT, min(AWAIT_TASK_MAX_TIMEOUT, timeout))

        # 有界窓 1 回で全 id をカバー(追補 2 — 単発待ちを N 回並べると親ターンが
        # 最悪 N×timeout 占有される)。asyncio.wait は要素の例外を消費しない —
        # stop 発の cancel も done 側に畳まれ、確定は下の全行再読が行う。
        # 待つのは走行中で registry に task がある分だけ(集合が空なら wait せず
        # 全行再読へ — 終了済みは待たず即返 = 冪等)。
        wait_set = set()
        for tid in requested:
            row = rows[tid]
            if row is not None and row.status == "running":
                task = get_exec_task(row.id)
                if task is not None:
                    wait_set.add(task)
        if wait_set:
            tool_output.progress(f"{len(wait_set)} 件のバックグラウンドタスクを待機中")
            try:
                await asyncio.wait(wait_set, timeout=timeout)
            except asyncio.CancelledError:
                # 親ターンの stop(自タスクへの cancel 要求)は re-raise のみ —
                # exec は止めない(D6: fire の意思の尊重)。さもなくば下の
                # 全行再読で status どおり確定する。
                if asyncio.current_task().cancelling():
                    raise

        # wait 後は必ず全 id の行を再読して確定(契約 M1 — runner の
        # 「永続化 → registry pop」直後を誤って running と報告しないため)。
        entries = []
        delivered_ids = []
        done_rows = []
        for tid in requested:
            row = rows[tid]
            if row is None:
                entries.append(_invalid_entry(tid))
                continue
            row = await asyncio.to_thread(Executions.get_execution_by_id, tid) or row
            if row.status == "running" and get_exec_task(row.id) is None:
                # running なのに task 不在 — liveness ガード(§4。spawn 窓
                # グレース内はそのまま running が返る)
                row = await ensure_execution_liveness(row)
            if row.status == "running":
                # 時間切れ(部分回収)— 残りはサーバ駆動配達が後で届ける
                entries.append(_running_entry(row))
                continue
            delivered_ids.append(row.id)
            if row.status != "done":
                # failed / stopped / orphaned。再 await は冪等(同じ形が返るだけ)
                failed_entry = {
                    "ok": False,
                    "task_id": row.id,
                    "tool_name": row.tool_name,
                    "status": row.status,
                    "error": row.error or "",
                }
                # #145: 失敗の種別(exec 行 meta)。stopped / orphaned と種別の無い行は載せない
                error_kind = (row.meta or {}).get(ERROR_KIND_META_KEY)
                if error_kind:
                    failed_entry["error_kind"] = error_kind
                entries.append(failed_entry)
                continue
            done_rows.append(row)
            meta = row.meta or {}
            # 保存 render が json なら生 object に戻す(二重エスケープ回避 — §8)
            result_value = row.result
            if (meta.get("render") or {}).get("kind") == "json" and result_value:
                try:
                    result_value = json.loads(result_value)
                except Exception:
                    pass  # 壊れていれば文字列のまま返す(切詰め時は render 降格済み)
            entry = {
                "ok": True,
                "task_id": row.id,
                "tool_name": row.tool_name,
                "status": "done",
                "result": result_value,
            }
            if row.duration is not None:
                entry["duration"] = row.duration
            if row.usage:
                entry["usage"] = row.usage
            if meta.get("truncated"):
                entry["truncated"] = True
            # #145: outcome(答えが「無い」)は done で届く — 種別だけ添える
            if meta.get(ERROR_KIND_META_KEY):
                entry["error_kind"] = meta[ERROR_KIND_META_KEY]
            entries.append(entry)
        entries.extend(_over_limit_entry(tid) for tid in over)

        # context_images / display は per-task・初回のみ自 sink へ replay
        # (mark_replayed の勝者だけ — VLM 再注入・表示の重複防止。配達 notice 側の
        # 消費とも同じ CAS を取り合う)
        for row in done_rows:
            meta = row.meta or {}
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

        # 戻り値宣言(render_descriptor)の sink 上書きは「要求 id がちょうど
        # 1 件 ∧ done」のみ(契約 M1 — 複数回収の envelope に単一 tool の
        # descriptor を当てると他 entry の描画まで乗っ取るため)。
        if len(requested) == 1 and not over and len(done_rows) == 1:
            render_descriptor = (done_rows[0].meta or {}).get("render_descriptor")
            if render_descriptor:
                sink = tool_output.current_sink.get()
                if sink is not None:
                    sink.set_result_render(render_descriptor)

        # terminal 形で返した全行に delivered_at(契約 M1 — この返りでモデルの
        # 文脈に結果が載った = サーバ駆動配達は不要。running 形は立てない)。
        if delivered_ids:
            await asyncio.to_thread(Executions.mark_delivered, delivered_ids)

        return {"tasks": entries}

    stop_spec = {
        "name": "stop_task",
        "description": (
            "バックグラウンドタスク(task_ids)を強制終了する。停止後の実状態を"
            "返す。終了済みのタスクに撃っても現状を返すだけ(冪等)。停止しても"
            "副作用は完了している可能性がある。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "停止するタスク id(background: true の返りに含まれる)。"
                        f"最大 {TASK_IDS_MAX} 件。"
                    ),
                },
            },
            "required": ["task_ids"],
        },
    }

    async def stop_task(task_ids=None):
        ids = _coerce_ids(task_ids)
        over = ids[TASK_IDS_MAX:]
        requested = ids[:TASK_IDS_MAX]
        rows = await _load_rows(requested)
        entries = []
        delivered_ids = []
        for tid in requested:
            if rows[tid] is None:
                entries.append(_invalid_entry(tid))
                continue
            # 停止の権威は endpoint 側の CAS(D2)— stop_execution が
            # stopped へ CAS し、勝ったときだけ task を cancel する。冪等。
            row = await stop_execution(tid) or rows[tid]
            entries.append(
                {"task_id": row.id, "tool_name": row.tool_name, "status": row.status}
            )
            if row.status != "running":
                delivered_ids.append(row.id)
        entries.extend(_over_limit_entry(tid) for tid in over)

        # 報告した terminal 行に delivered_at(契約 M1 批評 W 裁定 — 停止を
        # モデルに報告済みなのに配達 driver が通知ターンを起こす幽霊を防ぐ)
        if delivered_ids:
            await asyncio.to_thread(Executions.mark_delivered, delivered_ids)

        return {"tasks": entries}

    # exec_recall マーカ: mount_recall_tools の冪等判定(既 mount の自分自身と
    # 同名の外部 tool を区別する)に使う
    return {
        "check_task": {
            "spec": check_spec,
            "callable": check_task,
            "exec_recall": True,
        },
        "await_task": {
            "spec": await_spec,
            "callable": await_task,
            "exec_recall": True,
            # 自前の有界待ち(clamp 済み ≤ 300 秒)が running 形を返す設計なので、
            # 外側(execute_tool_calls)の wait_for は無効化する(§8)
            "timeout": 0,
        },
        "stop_task": {
            "spec": stop_spec,
            "callable": stop_task,
            "exec_recall": True,
        },
    }
