"""非同期 tool 実行(execution)の実行系(#110)。

どの tool 実行も `background: true` で非同期化できる汎用基盤。spawn は execution 行
(models/executions.py)を INSERT して runner task を起動し、即座に
{ok, task_id, status: "running"} を返す(用語は契約 T1 — モデル向けは task_id、
内部実体は exec 行のまま)。完了の真実は exec 行が持ち、回収は check_task /
await_task(呼び出し面 — utils/exec_tools.py)が、完了結果の配達はサーバ駆動
(契約 T3 — execution_resume の配達 driver がタスク終了時に親 chat へ通知
メッセージを書き、続きターンを起動する。delivered_at が配達済みの事実)が行う。

設計原則(_memos/async-exec-design.md):
- D1: 状態遷移はすべて guarded UPDATE(finish_execution の CAS)。同着は先勝ち。
- D2: stop の権威は endpoint 側 — stopped へ CAS → 勝ったら task.cancel()。
  runner は CancelledError で一切永続化しない(re-raise のみ)。書けなかった
  running 行は boot スイープが orphaned に倒す。
- D6: 親ターンの stop は exec に伝播しない(fire の意思の尊重)。exec の task は
  `exec:{chat_id}` 名前空間で登録し、FE の SSE 再アタッチ対象
  (GET /api/tasks/chat/{chat_id})から分離する。
- D7: 単一プロセスが不変条件。registry はプロセス内 dict で、UVICORN_WORKERS > 1
  なら起動時 warning + spawn 拒否(他 worker の生きた exec を liveness 判定が
  殺すため)。

runner は自前の sink(_ExecSink)を張る: create_task の context copy が引き継ぐ
親ターンの sink は turn return 直後に close されるため。progress は最新 1 件を
sink に保持し、check_task が registry 経由で読む(best-effort・プロセス内)。
加えて spawn したターンの stream がまだ開いていれば `tool:progress` として
転送する(#140 UI 追従 — background の run_subagent カードが子 session の所在
`subagent` を受け取り live 追従できるように。閉じていれば黙って捨てる)。
終端(done / failed / stopped)は chat の開いている stream 全部へ
`execution:done` を 1 発流す — FE はこれでスピナーを即止める(5 秒ポーリングは
stream の無いときの fallback として残る)。`in_exec` ContextVar は completion_events の DB 分岐遮断
(append-only 破り・RMW 競合の防止 — §6 硬化)に使う。
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Optional

from nucllei import tasks
from nucllei.env import (
    ASYNC_EXEC_MAX_CONCURRENCY,
    ASYNC_EXEC_MAX_TOTAL,
    ASYNC_EXEC_TIMEOUT_S,
    ASYNC_EXEC_RETENTION_DAYS,
    SRC_LOG_LEVELS,
    TOOL_RESULT_MAX_CHARS,
    UVICORN_WORKERS,
)
from nucllei.models.executions import ExecutionModel, Executions
from nucllei.utils import tool_output
from nucllei.utils.tool_output import ToolOutputSink

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# spawn 直後のグレース(秒): created_at からこの間は liveness ガードが orphaned
# 判定をしない(INSERT → registry 反映の窓で他経路が誤検知しないため。実際は
# センチネル登録が INSERT より先なので二重の防御)。
EXEC_SPAWN_GRACE_S = 10

# boot スイープ・liveness ガードの orphaned 文言(モデル向け日本語 — model-frames 規約)
ORPHANED_ON_BOOT_ERROR = (
    "サーバー再起動により実行が失われました。再実行の可否は tool の性質によります。"
)
ORPHANED_ON_PROBE_ERROR = (
    "実行タスクが失われています(サーバー再起動等)。結果は失われました。"
    "再実行の可否は tool の性質によります。"
)
STOPPED_ERROR = "実行は停止されました。副作用は完了している可能性があります。"

# exec runner 内で立つ ContextVar(§6 硬化)。completion_events の DB 書き分岐
# (message/replace の親メッセージ変異)はこれが True のとき全面スキップする —
# append-only(親 tool result は spawn 時の running 形のみ)と、ターン中の
# _persist_message_snapshot との RMW 競合の両方を塞ぐ。
in_exec: ContextVar[bool] = ContextVar("nucllei_in_exec", default=False)

# registry: exec_id → task_id。
#   None            = spawn 中センチネル(INSERT より先に登録 — 誤 orphan 防止)
#   _STOP_REQUESTED = spawn 窓中に stop が勝った(task_id 確定時に cancel)
#   str(task_id)    = 走行中
# イベントループ上からのみ触る(check→update の間に await を挟まない規律 —
# subagent.py の _active_turns と同じ)。
_STOP_REQUESTED = "!stop-requested"
_exec_tasks: dict[str, Optional[str]] = {}

# per-user 同時数会計(0 で pop)
_active_execs_by_user: dict[str, int] = {}

# 走行中 exec の sink(check_task の progress 読み出し用。done callback で pop)
_exec_sinks: dict[str, "_ExecSink"] = {}

# exec 行 meta のキー: run_subagent の子 agent session id(一覧 API が session_id として出す)
SUBAGENT_SESSION_META_KEY = "subagent_session_id"


def _call_on_loop(loop, loop_thread_id, fn, *args) -> None:
    """ループ上なら直呼び、ワーカースレッドからなら call_soon_threadsafe。

    stream の購読者集合・_streams レジストリはループ上からのみ触る
    (middleware._make_progress_emitter と同じ規律)。
    """
    if loop is None:
        return
    if threading.get_ident() == loop_thread_id:
        fn(*args)
    else:
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass  # ループ停止済み(shutdown)— 装飾なので捨てる


def _forward_progress(chat_id, message_id, payload: dict) -> None:
    """spawn したターンの stream へ tool:progress を転送する(ループ上で呼ぶ)。"""
    # 循環 import 回避(completion_events が本モジュールの in_exec を import する)
    from nucllei.utils.completion_events import get_stream_for_message

    stream = get_stream_for_message(chat_id, message_id)
    if stream is None:
        return
    stream.emit(
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "data": {"type": "tool:progress", "data": payload},
        }
    )


def _emit_execution_done(row: Optional[ExecutionModel]) -> None:
    """exec の終端を chat の開いている stream 全部へ流す(ループ上で呼ぶ)。

    `execution:done` {task_id, tool_call_id, tool_name, status, finished_at,
    duration, error}。封筒の message_id は spawn したメッセージ(FE はこれを
    既知の message として受け、executions store の行を即座に更新する)。
    装飾 — 失敗は握る(真実は exec 行。FE は 5 秒ポーリングで追いつく)。
    """
    if row is None:
        return
    try:
        from nucllei.utils.completion_events import get_streams_for_chat

        envelope = {
            "chat_id": row.chat_id,
            "message_id": row.message_id,
            "data": {
                "type": "execution:done",
                "data": {
                    "task_id": row.id,
                    "tool_call_id": row.tool_call_id,
                    "tool_name": row.tool_name,
                    "status": row.status,
                    "finished_at": row.finished_at,
                    "duration": row.duration,
                    "error": row.error,
                },
            },
        }
        for stream in get_streams_for_chat(row.chat_id):
            stream.emit(envelope)
    except Exception:
        log.debug("execution:done emit failed", exc_info=True)


class _ExecSink(ToolOutputSink):
    """exec 専用 sink。

    progress は**最新 1 件を保持する**(揮発・プロセス内)— check_task が
    ポーリングで読む。spawn したターンの stream が開いていればそこへも
    `tool:progress` を転送する(#140 UI 追従。閉じていれば捨てる)。
    context_images / display は親クラスの蓄積をそのまま使い、完了時に exec 行の
    meta へ harvest される。

    ループ上(runner の先頭)で生成すること — 転送先のループを捕まえる。
    """

    def __init__(
        self,
        tool_call_id: str = "",
        *,
        chat_id: Optional[str] = None,
        message_id: Optional[str] = None,
        exec_id: Optional[str] = None,
    ) -> None:
        # _store_progress が参照する属性を親 __init__ より先に用意する
        self.latest_progress: Optional[dict] = None
        self._exec_id = exec_id
        # run_subagent の子 session id(progress の `subagent.session_id`)。exec 行の
        # meta へ永続化する — progress は揮発なので、リロード後のカードはこれで子
        # session へ再リンクする(一覧 API の session_id)。runner は終端化の前に
        # 書き込み(_subagent_persist)の完了を待ち、終端の meta にも同じ値を載せる
        self.subagent_session_id: Optional[str] = None
        self._subagent_persist: Optional[asyncio.Future] = None
        self._chat_id = chat_id
        self._message_id = message_id
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None  # ループ外(同期テスト)— 転送しない
        self._loop_thread_id = threading.get_ident()
        super().__init__(tool_call_id=tool_call_id, emit_progress=self._store_progress)

    def _store_progress(self, payload: dict) -> None:
        self.latest_progress = payload
        subagent = payload.get("subagent") if isinstance(payload, dict) else None
        session_id = subagent.get("session_id") if isinstance(subagent, dict) else None
        if session_id and session_id != self.subagent_session_id:
            self.subagent_session_id = session_id
            _call_on_loop(
                self._loop,
                self._loop_thread_id,
                self._persist_subagent_session,
                session_id,
            )
        if self._chat_id and self._message_id:
            _call_on_loop(
                self._loop,
                self._loop_thread_id,
                _forward_progress,
                self._chat_id,
                self._message_id,
                payload,
            )

    def _persist_subagent_session(self, session_id: str) -> None:
        """ループ上で呼ぶ: exec 行の meta.subagent_session_id を書く(fire-and-forget)。"""
        if not self._exec_id:
            return
        self._subagent_persist = asyncio.ensure_future(
            asyncio.to_thread(
                Executions.set_running_meta,
                self._exec_id,
                SUBAGENT_SESSION_META_KEY,
                session_id,
            )
        )

    async def wait_persisted(self) -> None:
        """meta 書き込みの完了待ち(終端化の meta 上書きと RMW が交差しないように)。"""
        if self._subagent_persist is not None:
            try:
                await self._subagent_persist
            except Exception:
                log.debug("subagent session persist failed", exc_info=True)


def _error(message: str) -> str:
    """エラーは例外にせず tool result 文字列(JSON)で返す(subagent.py と同流儀)。"""
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def normalize_tool_result(raw) -> tuple[str, Optional[dict]]:
    """tool 戻り値を wire 契約(常に文字列)へ正規化する。

    middleware.execute_tool_calls の正規化と同一挙動(共有抽出 — 挙動不変):
    - dict/list → json.dumps(ensure_ascii=False。\\uXXXX 展開のトークン倍化を防ぐ。
      indent なし — FE は kind=json をパースして整形し直すため空白は表示に寄与しない)
      + render {"kind": "json"}
    - str → そのまま
    - None → 空文字("None" という見かけの出力を作らない)、他は str()
    戻り値は (text, render|None)。
    """
    if isinstance(raw, dict) or isinstance(raw, list):
        return json.dumps(raw, ensure_ascii=False), {"kind": "json"}
    if isinstance(raw, str):
        return raw, None
    return ("" if raw is None else str(raw)), None


def truncate_tool_result(text: str) -> tuple[str, bool, int]:
    """tool 結果の共通切詰め(契約 M0 — TOOL_RESULT_MAX_CHARS)。

    適用は**結果の生産点で一度だけ**: 同期 tool は execute_tool_calls の wire
    正規化直後、background は runner の保存時。以後の中継(await_task の envelope・
    配達 notice)は透過 — 特に回収 builtin の返りに再適用すると envelope の
    json.dumps 膨張で壊れ JSON になる(切詰め済みの中身を再び切る)。
    戻り値は (text, truncated, original_chars)。切詰め時の render 降格
    (JSON / descriptor 描画はパース前提なので落とす)は呼び出し側の責務。
    """
    original_chars = len(text)
    if original_chars <= TOOL_RESULT_MAX_CHARS:
        return text, False, original_chars
    return text[:TOOL_RESULT_MAX_CHARS], True, original_chars


def async_exec_available() -> bool:
    """spawn 可能か(D7: multi-worker では無効 — liveness 判定が他 worker の
    生きた exec を殺すため)。"""
    return UVICORN_WORKERS <= 1


def _decrement_user(user_id: str) -> None:
    count = _active_execs_by_user.get(user_id, 0) - 1
    if count <= 0:
        _active_execs_by_user.pop(user_id, None)
    else:
        _active_execs_by_user[user_id] = count


def _release_spawn_slot(exec_id: str, user_id: str) -> None:
    """spawn が task 起動まで到達しなかったときの解放(同期のみ)。

    registry センチネル pop + 同時数 decrement。done callback が付く前の失敗経路
    (例外・**キャンセル**)で呼ぶ — ここを飛ばすと席とセンチネルが恒久リークし、
    以後その exec_id は「走行中」と誤認され続ける。
    """
    _exec_tasks.pop(exec_id, None)
    _decrement_user(user_id)


def _on_exec_task_done(exec_id: str, user_id: str) -> None:
    """runner task の done callback: registry pop + 会計 decrement。

    tasks.py の cleanup_task(task 登録簿の掃除)とは独立した callback
    (add_done_callback は複数登録可・互いに干渉しない)。
    """
    _exec_tasks.pop(exec_id, None)
    _exec_sinks.pop(exec_id, None)
    _decrement_user(user_id)


async def spawn_execution(
    *,
    user_id: str,
    chat_id: str,
    message_id: str,
    tool_call_id: str,
    tool_name: str,
    entry: dict,
    params: dict,
):
    """exec 行を作って runner task を起動し、running 形を即返す(§5 spawn)。

    execute_tool_calls の background 分岐から呼ばれる。params は unknown-drop
    (allowed_params フィルタ)済みの実引数であること(そのまま args に監査保存し、
    callable に渡す)。戻り値は dict(running 形)または _error 文字列 —
    どちらも middleware の wire 正規化にそのまま乗る。
    """
    if not async_exec_available():
        return _error("非同期実行はこのサーバー構成(multi-worker)では利用できません。")

    # 同時数 check → 同期で increment(check→increment 間に await を挟まない —
    # subagent.py の規律)。超過は即エラー(行を作らない)。
    if _active_execs_by_user.get(user_id, 0) >= ASYNC_EXEC_MAX_CONCURRENCY:
        return _error(
            f"バックグラウンドタスクの同時数がユーザー上限"
            f"({ASYNC_EXEC_MAX_CONCURRENCY})に達しています。走行中のタスクの完了を"
            "待つか stop_task で止めてから再試行してください。"
        )
    if sum(_active_execs_by_user.values()) >= ASYNC_EXEC_MAX_TOTAL:
        return _error(
            f"バックグラウンドタスクの同時数がサーバー上限({ASYNC_EXEC_MAX_TOTAL})に"
            "達しています。しばらくしてから再試行してください。"
        )
    _active_execs_by_user[user_id] = _active_execs_by_user.get(user_id, 0) + 1

    exec_id = str(uuid.uuid4())
    # INSERT より先にセンチネル登録(誤 orphan 防止: liveness ガードは
    # 「registry に無い running 行」を倒すため、行が見えた時点で registry に居る)
    _exec_tasks[exec_id] = None
    inserted = False
    try:
        row = await asyncio.to_thread(
            Executions.insert_execution,
            id=exec_id,
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=params,
        )
        if row is None:
            raise RuntimeError(f"execution row insert failed (exec_id={exec_id})")
        inserted = True

        # 名前空間 exec:{chat_id}: GET /api/tasks/chat/{chat_id} は FE の SSE
        # 再アタッチ対象で、stream の無い exec task を混ぜると再アタッチ・
        # 走行判定が壊れる(§5)。親ターンの stop(素の chat_id 宛)も届かない
        # = D6(stop 非伝播)がここで構造的に成立する。
        task_id, task = tasks.create_task(
            _run_execution(
                exec_id,
                tool_call_id,
                tool_name,
                entry,
                params,
                chat_id=chat_id,
                message_id=message_id,
            ),
            id=f"exec:{chat_id}",
        )
        task.add_done_callback(
            lambda t, eid=exec_id, uid=user_id: _on_exec_task_done(eid, uid)
        )
    except Exception as e:
        # スロット恒久リークの防止: decrement + registry pop + 行があれば failed へ CAS
        log.exception(f"spawn_execution failed (tool={tool_name}): {e}")
        _release_spawn_slot(exec_id, user_id)
        if inserted:
            try:
                await asyncio.to_thread(
                    Executions.finish_execution,
                    exec_id,
                    status="failed",
                    error="非同期実行の起動に失敗しました。",
                )
            except Exception:
                log.exception(f"failed to mark spawn-failed execution {exec_id}")
        return _error("非同期実行の起動に失敗しました。")
    except BaseException:
        # CancelledError は BaseException 派生 — 上の except では捕らえられない。
        # INSERT の await がキャンセル点で、ここを素通りすると席とセンチネルが
        # 恒久リークする。解放は**同期分だけ**行って re-raise する(cancel 後の
        # await は再び即 cancel されうるので DB 書きはしない — to_thread の
        # INSERT が後から着地して残る running 行は boot スイープ / liveness
        # ガードが orphaned に倒す)。
        _release_spawn_slot(exec_id, user_id)
        raise

    # INSERT の await 中に stop / cascade が予約を立てた場合、task_id 確定後に cancel
    # (create_task → ここまで await 無し = done callback より先に必ず実行される)
    if _exec_tasks.get(exec_id) == _STOP_REQUESTED:
        _exec_tasks[exec_id] = task_id
        task.cancel()
    else:
        _exec_tasks[exec_id] = task_id

    # 返りのキーは task_id(契約 T1 — モデル・UI 向けの用語。値は exec 行の id)
    return {"ok": True, "task_id": exec_id, "tool_name": tool_name, "status": "running"}


async def _run_execution(
    exec_id: str,
    tool_call_id: str,
    tool_name: str,
    entry: dict,
    params: dict,
    *,
    chat_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> None:
    """runner: callable を走らせ、終端(done/failed)を guarded UPDATE で書く。

    CancelledError では**一切永続化しない**(D2 — stopped は stop 経路が CAS 済み。
    shutdown の一斉 cancel を stopped と誤記録しない)。DB 書き自体の失敗も握って
    log のみ(行は running のまま → boot スイープが orphaned に倒す —
    「終了形は 2 つ」の前提を守る)。
    """
    # 自前 sink: create_task の context copy が引き継ぐ親ターンの sink は
    # turn return 直後に close されるため張り替える(§5 runner)
    sink = _ExecSink(
        tool_call_id=tool_call_id,
        chat_id=chat_id,
        message_id=message_id,
        exec_id=exec_id,
    )
    _exec_sinks[exec_id] = sink
    sink_token = tool_output.current_sink.set(sink)
    exec_token = in_exec.set(True)
    started_at = time.monotonic()
    outcome: Optional[dict] = None
    tool_timeout = entry.get("timeout") or ASYNC_EXEC_TIMEOUT_S
    try:
        try:
            sink.mark_started()
            tool_function = entry["callable"]
            if tool_timeout and tool_timeout > 0:
                raw = await asyncio.wait_for(
                    tool_function(**params), timeout=tool_timeout
                )
            else:
                raw = await tool_function(**params)
        except asyncio.CancelledError:
            # D2: stop / shutdown の cancel。永続化しない(re-raise のみ)
            raise
        except (asyncio.TimeoutError, TimeoutError):
            log.warning(
                f"async execution timed out: {tool_name} (> {tool_timeout}s, "
                f"exec_id={exec_id})"
            )
            outcome = {
                "status": "failed",
                "error": (
                    f"ツール {tool_name} の非同期実行が {tool_timeout} 秒で"
                    "タイムアウトしました(システム側上限)。"
                ),
                "duration": round(time.monotonic() - started_at, 3),
            }
        except Exception as e:
            log.exception(f"async execution failed: {tool_name} (exec_id={exec_id})")
            outcome = {
                "status": "failed",
                "error": str(e) or e.__class__.__name__,
                "duration": round(time.monotonic() - started_at, 3),
            }
        else:
            duration = round(time.monotonic() - started_at, 3)
            text, render = normalize_tool_result(raw)
            meta: dict = {}
            if render is not None:
                meta["render"] = render
            # 戻り値宣言(FE の描画ディスクリプタ)— await 時に results[].render へ
            # パススルーされる(subagent 描画に必須)
            render_descriptor = (entry.get("render") or {}).get("result")
            if render_descriptor:
                meta["render_descriptor"] = render_descriptor
            # 切詰めは生産点で一度だけ(契約 M0 — 同期経路と共通のヘルパ)。
            # 切詰めで JSON が壊れるため render は text に降格(不正 JSON を
            # FE / json.loads に渡さない)。descriptor 描画もパース前提なので同時に落とす
            text, truncated, original_chars = truncate_tool_result(text)
            if truncated:
                meta["truncated"] = True
                meta["original_chars"] = original_chars
                meta.pop("render", None)
                meta.pop("render_descriptor", None)
            # 成功時のみ harvest(snapshot — 失敗 call は副産物を出さない規律は
            # middleware と同じ。failed 経路はこの分岐に来ない)
            context_images = sink.context_images()
            if context_images:
                meta["context_images"] = context_images
            displays = sink.displays()
            if displays:
                meta["display"] = displays
            # 終端の meta は上書き — 走行中に書いた子 session id を載せ直す
            # (failed / stopped は meta を書かないので走行中の値が残る)
            if sink.subagent_session_id:
                meta[SUBAGENT_SESSION_META_KEY] = sink.subagent_session_id
            usage = raw.get("usage") if isinstance(raw, dict) else None
            outcome = {
                "status": "done",
                "result": text,
                "duration": duration,
                **({"meta": meta} if meta else {}),
                **({"usage": usage} if isinstance(usage, dict) and usage else {}),
            }
    finally:
        # cancel 経路もここだけ通る: ContextVar reset / sink close のみ(D2)
        in_exec.reset(exec_token)
        tool_output.current_sink.reset(sink_token)
        sink.close()

    # 永続化(cancel 時は上の raise で到達しない)。guarded UPDATE 負け
    # (stop と同着で stopped が先行済み)は no-op。
    try:
        # 走行中の meta 書き(子 session id)と終端の meta 上書きを交差させない
        await sink.wait_persisted()
        won = await asyncio.to_thread(Executions.finish_execution, exec_id, **outcome)
        if not won:
            log.info(
                f"execution {exec_id} already finalized elsewhere "
                f"(runner outcome '{outcome['status']}' dropped)"
            )
        else:
            # 終端の即時通知(#140 UI 追従)— FE のスピナーをポーリング待ちにしない
            _emit_execution_done(
                await asyncio.to_thread(Executions.get_execution_by_id, exec_id)
            )
    except Exception:
        # 行は running のまま残る → boot スイープ / liveness ガードが orphaned に倒す
        log.exception(f"failed to persist execution outcome (exec_id={exec_id})")


async def stop_execution(
    exec_id: str, *, mark_delivered: bool = False
) -> Optional[ExecutionModel]:
    """個別停止(stop API / cascade 共用)。

    権威は endpoint 側(D2): stopped へ CAS → 勝ったときだけ task を cancel する
    (runner は CancelledError で何も書かない)。registry に無い running 行
    (再起動残骸)もこの CAS で stopped にできる。CAS 負け(既に終端)は no-op。
    戻り値は最新の行(存在しなければ None)。

    mark_delivered=True(cascade 専用 — #110 敵対検証 1)は stopped への CAS と
    同一 UPDATE で delivered_at も立てる。この後の task cancel の await 中に
    cleanup wake → 配達 driver が走るため、CAS 勝ちの時点で配達済みに
    なっていないと消えゆく chat へ notice が書かれる(幽霊配達)。チップの
    個別 stop(stop エンドポイント / stop_task)は配達対象のまま — 指定しない。
    """
    won = await asyncio.to_thread(
        Executions.finish_execution,
        exec_id,
        status="stopped",
        error=STOPPED_ERROR,
        delivered=mark_delivered,
    )
    if won:
        registered = _exec_tasks.get(exec_id)
        if exec_id in _exec_tasks and registered is None:
            # spawn 窓(INSERT 中)— stop 予約を立て、task_id 確定時に cancel させる
            _exec_tasks[exec_id] = _STOP_REQUESTED
        elif isinstance(registered, str) and registered != _STOP_REQUESTED:
            try:
                await tasks.stop_task(registered)
            except ValueError:
                pass  # 既に完走・回収済み(CAS が勝っているので行は stopped のまま)
    row = await asyncio.to_thread(Executions.get_execution_by_id, exec_id)
    if won:
        _emit_execution_done(row)
    return row


async def stop_executions_by_chat_id(chat_id: str) -> int:
    """chat 配下の走行中 exec を全停止する(chat 削除 cascade 用 — §5)。

    親ターンの task cancel 連鎖は detach された exec に届かない(exec task は
    exec:{chat_id} 名前空間)ため、削除経路(単発・全削除・ユーザー削除)は
    これを明示的に呼ぶこと。行は消さない(soft delete と対称の監査)。

    mark_delivered=True で呼ぶ(#110 敵対検証 1): 配達先が消えるため恒久断念 —
    stopped への CAS と同時に delivered_at を立てる。呼び出し元の一括
    _mark_chat_executions_delivered(routers/chats.py)は CAS 負け行(自然完走
    済みの未配達行)向けの保険として残る。
    """
    rows = await asyncio.to_thread(
        Executions.get_running_executions_by_chat_id, chat_id
    )
    for row in rows:
        await stop_execution(row.id, mark_delivered=True)
    return len(rows)


def get_exec_task(exec_id: str) -> Optional[asyncio.Task]:
    """走行中 exec の asyncio.Task(await_task の有界待ち用)。無ければ None。"""
    registered = _exec_tasks.get(exec_id)
    if isinstance(registered, str) and registered != _STOP_REQUESTED:
        return tasks.get_task(registered)
    return None


def has_exec_registry_entry(exec_id: str) -> bool:
    """registry に載っているか(spawn センチネル・stop 予約を含む)。liveness 判定用。"""
    return exec_id in _exec_tasks


def get_latest_progress(exec_id: str) -> Optional[dict]:
    """走行中 exec の最新 progress 1 件(best-effort・プロセス内 — check_task 用)。"""
    sink = _exec_sinks.get(exec_id)
    return sink.latest_progress if sink is not None else None


async def ensure_execution_liveness(
    execution: ExecutionModel,
) -> ExecutionModel:
    """liveness ガード(§4 — 照会時の防御。orphaned の権威は boot スイープ)。

    running なのに registry に居ない行を orphaned へ倒す。ただし:
    - created_at から EXEC_SPAWN_GRACE_S 未満は判定しない(spawn 窓グレース)
    - **行を再読し、なお running ∧ registry 不在のときだけ** CAS する
      (完走直後 = 「DB 書き済み・registry pop 済み」との取り違えレース対策。
      runner は永続化してから done callback で pop するので、再読が running を
      返すなら本当に失われている)
    await_task / 個別 GET / stop の入口で使う。一覧 API は検出のみで書かない。
    """
    if execution.status != "running":
        return execution
    if execution.id in _exec_tasks:
        return execution
    if int(time.time()) - execution.created_at < EXEC_SPAWN_GRACE_S:
        return execution

    fresh = await asyncio.to_thread(Executions.get_execution_by_id, execution.id)
    if fresh is None:
        return execution
    if fresh.status != "running" or fresh.id in _exec_tasks:
        return fresh

    await asyncio.to_thread(
        Executions.finish_execution,
        fresh.id,
        status="orphaned",
        error=ORPHANED_ON_PROBE_ERROR,
    )
    return await asyncio.to_thread(Executions.get_execution_by_id, fresh.id) or fresh


def sweep_executions_on_boot() -> int:
    """boot スイープ(orphaned の権威)+ 保持期限 GC。lifespan から to_thread で呼ぶ。

    単一プロセスが不変条件(D7)なので、起動時に running の行は前プロセスの死で
    失われた実行 — まとめて orphaned に倒す(記録は残るが計算は再開しない。
    冪等性は tool 側の宿題)。ASYNC_EXEC_RETENTION_DAYS > 0 なら古い終端行の重量
    フィールドも落とす。戻り値は orphaned に倒した行数。
    """
    if UVICORN_WORKERS > 1:
        log.warning(
            f"UVICORN_WORKERS={UVICORN_WORKERS}: 非同期 tool 実行(#110)は"
            "単一プロセス前提のため無効化されます(spawn は拒否されます)。"
        )

    orphaned = Executions.mark_running_as_orphaned_on_boot(ORPHANED_ON_BOOT_ERROR)
    if orphaned:
        log.info(f"boot sweep: marked {orphaned} running execution(s) as orphaned")

    if ASYNC_EXEC_RETENTION_DAYS > 0:
        before = int(time.time()) - ASYNC_EXEC_RETENTION_DAYS * 86400
        purged = Executions.purge_stale_results(before)
        if purged:
            log.info(
                f"boot sweep: purged results of {purged} execution(s) older than "
                f"{ASYNC_EXEC_RETENTION_DAYS}d"
            )

    return orphaned
