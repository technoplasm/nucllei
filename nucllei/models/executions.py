"""execution(非同期 tool 実行)エンティティ(#110)。

ターンを跨ぐ非同期 tool 実行の一級エンティティ。親メッセージの tool result は
spawn 時の running 形のみで以後不変(append-only)— 完了の真実はこの exec 行が持つ。

状態機械(_memos/async-exec-design.md §4):

    (INSERT) → running ─┬→ done      runner: callable 正常 return
                        ├→ failed    runner: 例外 / timeout / spawn 後段失敗
                        ├→ stopped   stop API・cascade(CAS 先行 → cancel)
                        └→ orphaned  boot スイープ / 照会時 liveness ガード

終端遷移はすべて guarded UPDATE(WHERE id=? AND status='running')— rowcount 0 は
「負け」で no-op。同着(stop と完走等)は先勝ちで壊れない(設計 D1)。
行は消さない(chat soft delete と対称の監査。減量は purge_stale_results)。
"""

import logging
import time
from typing import Optional

from nucllei.internal.db import Base, get_db
from nucllei.env import SRC_LOG_LEVELS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Index,
    String,
    Text,
    JSON,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

####################
# Execution DB Schema
####################

EXECUTION_STATUSES = ("running", "done", "failed", "stopped", "orphaned")


class Execution(Base):
    __tablename__ = "execution"

    id = Column(String, primary_key=True)  # exec_id(uuid4)
    user_id = Column(String, nullable=False)  # 認可述語・per-user 会計
    chat_id = Column(String, nullable=False)  # チップ一覧・cascade の引き当て
    message_id = Column(String, nullable=False)  # spawn を含む親 assistant メッセージ
    tool_call_id = Column(String, nullable=False)  # spawn call の id(FE ブロック突合)
    tool_name = Column(String, nullable=False)

    args = Column(JSON, nullable=True)  # unknown-drop 後の実引数(監査)
    status = Column(String, nullable=False)  # running|done|failed|stopped|orphaned
    result = Column(Text, nullable=True)  # 正規化・切詰め済み文字列
    error = Column(Text, nullable=True)  # モデル向け日本語文言
    # {render, render_descriptor, truncated, original_chars,
    #  context_images, display, replayed_at}
    meta = Column(JSON, nullable=True)
    # 生戻り値 dict の "usage" キー転記のみ(合算しない — 設計 D8)
    usage = Column(JSON, nullable=True)
    duration = Column(Float, nullable=True)  # round(x,3) 秒(middleware と同粒度)

    created_at = Column(BigInteger, nullable=False)
    # 終端状態になった時刻(実行終了時刻ではない — orphaned はスイープ時刻)
    finished_at = Column(BigInteger, nullable=True)
    purged_at = Column(BigInteger, nullable=True)  # GC(result 減量)時刻

    # チップの主クエリ = chat 内 running 絞り込み
    __table_args__ = (
        Index("ix_execution_chat_id", "chat_id"),
        Index("ix_execution_chat_id_status", "chat_id", "status"),
    )


class ExecutionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    chat_id: str
    message_id: str
    tool_call_id: str
    tool_name: str

    args: Optional[dict] = None
    status: str
    result: Optional[str] = None
    error: Optional[str] = None
    meta: Optional[dict] = None
    usage: Optional[dict] = None
    duration: Optional[float] = None

    created_at: int  # epoch 秒
    finished_at: Optional[int] = None
    purged_at: Optional[int] = None


####################
# Table
####################


class ExecutionsTable:
    def insert_execution(
        self,
        *,
        id: str,
        user_id: str,
        chat_id: str,
        message_id: str,
        tool_call_id: str,
        tool_name: str,
        args: Optional[dict] = None,
    ) -> Optional[ExecutionModel]:
        """running 行を作る(唯一の INSERT 経路 — spawn 専用)。"""
        with get_db() as db:
            try:
                row = Execution(
                    id=id,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=args,
                    status="running",
                    created_at=int(time.time()),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                return ExecutionModel.model_validate(row)
            except Exception as e:
                log.exception(f"Error inserting execution {id}: {e}")
                return None

    def finish_execution(
        self,
        id: str,
        *,
        status: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
        meta: Optional[dict] = None,
        usage: Optional[dict] = None,
        duration: Optional[float] = None,
    ) -> bool:
        """running → 終端の guarded UPDATE(設計 D1)。

        WHERE id=? AND status='running' の CAS。rowcount 0 = 負け(既に他の書き手が
        終端化済み)= False を返し、呼び出し側は no-op(必要なら行を再読)。
        終端は一方向・一度きり — 終端間の遷移は存在しない。
        """
        if status not in ("done", "failed", "stopped", "orphaned"):
            log.error(f"finish_execution: invalid terminal status '{status}'")
            return False
        try:
            with get_db() as db:
                values = {
                    "status": status,
                    "finished_at": int(time.time()),
                }
                if result is not None:
                    values["result"] = result
                if error is not None:
                    values["error"] = error
                if meta is not None:
                    values["meta"] = meta
                if usage is not None:
                    values["usage"] = usage
                if duration is not None:
                    values["duration"] = duration
                rowcount = (
                    db.query(Execution)
                    .filter_by(id=id, status="running")
                    .update(values)
                )
                db.commit()
                return rowcount > 0
        except Exception as e:
            log.exception(f"Error finishing execution {id}: {e}")
            return False

    def mark_running_as_orphaned_on_boot(self, error: str) -> int:
        """boot スイープ(orphaned の権威)。

        単一プロセスが不変条件(設計 D7)なので、起動時に running で残っている行は
        すべて前プロセスの死で失われた実行。まとめて orphaned へ倒す。
        戻り値は倒した行数。
        """
        try:
            with get_db() as db:
                rowcount = (
                    db.query(Execution)
                    .filter_by(status="running")
                    .update(
                        {
                            "status": "orphaned",
                            "error": error,
                            "finished_at": int(time.time()),
                        }
                    )
                )
                db.commit()
                return rowcount
        except Exception as e:
            log.exception(f"Error sweeping orphaned executions: {e}")
            return 0

    def get_execution_by_id(self, id: str) -> Optional[ExecutionModel]:
        with get_db() as db:
            try:
                row = db.get(Execution, id)
                if row is None:
                    return None
                return ExecutionModel.model_validate(row)
            except Exception:
                return None

    def get_executions_by_chat_id(self, chat_id: str) -> list[ExecutionModel]:
        # created_at は秒粒度で同着しうる(1 ターンで複数 spawn)。ポーリングごとに
        # 並びが揺れないよう id で tie-break して全順序にする。
        with get_db() as db:
            return [
                ExecutionModel.model_validate(row)
                for row in db.query(Execution)
                .filter_by(chat_id=chat_id)
                .order_by(Execution.created_at.asc(), Execution.id.asc())
                .all()
            ]

    def get_running_executions_by_chat_id(self, chat_id: str) -> list[ExecutionModel]:
        with get_db() as db:
            return [
                ExecutionModel.model_validate(row)
                for row in db.query(Execution)
                .filter_by(chat_id=chat_id, status="running")
                .order_by(Execution.created_at.asc(), Execution.id.asc())
                .all()
            ]

    def has_awaitable_executions_by_chat_id(self, chat_id: str) -> bool:
        """chat に exec 行があるか(check_async/await_async の mount 判定 — EXISTS)。

        「未回収か」は DB では判定できない(回収済みの権威は親 chat の履歴側)ため、
        purge 済みを除く全行を対象にする。terminal への再 await は冪等なので過剰
        mount は無害。
        """
        with get_db() as db:
            try:
                query = db.query(Execution.id).filter(
                    Execution.chat_id == chat_id,
                    Execution.purged_at == None,  # noqa: E712
                )
                return db.query(query.exists()).scalar() or False
            except Exception:
                return False

    def mark_replayed(self, id: str) -> bool:
        """meta.replayed_at を一度だけ立てる(await 時の context_images/display の
        replay 重複防止)。

        JSON キーへの dialect 非依存な guarded UPDATE は書けないため、単一
        トランザクション内の read-modify-write で代替する。単一プロセスが不変条件
        (設計 D7)で書き手は本プロセスのみ・対象は同一 exec の await 経路に閉じる
        ので、この窓は実害を持たない(万一の重複 replay も VLM 注入がムダに 1 回
        増えるだけで冪等)。既に立っていれば False(負け)。
        """
        try:
            with get_db() as db:
                row = db.get(Execution, id)
                if row is None:
                    return False
                meta = dict(row.meta or {})
                if meta.get("replayed_at"):
                    return False
                meta["replayed_at"] = int(time.time())
                row.meta = meta
                db.commit()
                return True
        except Exception as e:
            log.exception(f"Error marking execution {id} as replayed: {e}")
            return False

    def purge_stale_results(self, before: int) -> int:
        """GC: before(epoch 秒)より前に終端化した行の重量フィールドを落とす。

        行は消さない(監査 — id/status/tool_name/usage/duration/error は残す)。
        result / args / meta を NULL にして purged_at を立てる。running 行と
        purge 済み行は対象外。EXEC_RETENTION_DAYS=0(既定)では呼ばれない。
        """
        try:
            with get_db() as db:
                rowcount = (
                    db.query(Execution)
                    .filter(
                        Execution.status != "running",
                        Execution.purged_at == None,  # noqa: E712
                        Execution.finished_at != None,  # noqa: E712
                        Execution.finished_at < before,
                    )
                    .update(
                        {
                            "result": None,
                            "args": None,
                            "meta": None,
                            "purged_at": int(time.time()),
                        },
                        synchronize_session=False,
                    )
                )
                db.commit()
                return rowcount
        except Exception as e:
            log.exception(f"Error purging stale executions: {e}")
            return 0


Executions = ExecutionsTable()
