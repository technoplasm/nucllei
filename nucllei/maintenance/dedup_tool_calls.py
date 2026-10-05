"""chat に残った「同じ call id の tool_calls ブロック × N」を 1 つに畳む一度きりの掃除(refs #142)。

f36c8737a(2026-10-03)以前のバグで、assistant メッセージの content_blocks に同じ call id を
持つ tool_calls ブロックが N 個保存されたものがある(results を持つのは最後の 1 つだけ)。
FE の expandAssistantToolHistory はこれを「同じ tool_call × N」+「合成 [interrupted] 結果 × N−1」
に展開して毎ターン送ってしまう。旧データ互換コードは持たない方針なので、データの方を直す。

対象は chat テーブルの chat JSON だけ(content_blocks の保存先はここしか無い):
  - chat.history.messages[*].content_blocks(会話ツリー本体)
  - chat.messages[*].content_blocks(currentId までの線形コピー。同じ内容が複製されている)
各 call id について「その id の result を持つブロック(無ければ最後のブロック)」だけを残し、
それ以外の重複ブロックを落とす。text / reasoning ブロックと message.content は触らない
(content は text ブロックの連結なので、tool_calls を落としても食い違わない)。
synthetic ブロック(`/command` の UI 表示用)は対象外。

本番(閉域)での流し方 — nucllei コンテナ内で(DATABASE_URL / DATA_DIR はコンテナの env を使う):
  1. docker exec <nucllei コンテナ> nucllei dedup-tool-calls            # dry-run: 件数と chat/message id
  2. docker exec <nucllei コンテナ> nucllei dedup-tool-calls --apply    # バックアップ後に書き込み
     バックアップ: sqlite は DB ファイルのコピー、postgres は対象行の JSON を --backup-dir
     (既定 $DATA_DIR/backups)へ。もう一度 dry-run して 0 件になれば完了。
この image より前の版では、このファイルを docker cp して `python dedup_tool_calls.py` でも動く
(nucllei パッケージに依存しない — import しても migration は走らない)。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table, create_engine
from sqlalchemy.engine import make_url


def dedup_blocks(blocks: list) -> tuple[list, int]:
    """重複 tool_calls ブロックを落とした content_blocks と、落としたブロック数を返す。

    各 call id の「残す側」= その id の result を持つ最後のブロック(無ければ最後のブロック)。
    どの call id の残す側にもならないブロックだけを落とす(一部の id だけ重なる想定外の形は
    落とさない — 情報を失わないことを優先)。
    """
    if not isinstance(blocks, list):
        return blocks, 0
    keeper: dict[str, int] = {}
    has_result: dict[str, bool] = {}
    candidates: list[int] = []
    for i, block in enumerate(blocks):
        if (
            not isinstance(block, dict)
            or block.get("type") != "tool_calls"
            or block.get("synthetic")
        ):
            continue
        ids = [c.get("id") for c in block.get("calls") or [] if isinstance(c, dict)]
        ids = [x for x in ids if x]
        if not ids:
            continue
        candidates.append(i)
        result_ids = {
            r.get("tool_call_id")
            for r in block.get("results") or []
            if isinstance(r, dict)
        }
        for call_id in ids:
            got = call_id in result_ids
            # result 持ちを優先し、同格なら後ろのブロックを残す
            if got or not has_result.get(call_id, False):
                keeper[call_id] = i
                has_result[call_id] = has_result.get(call_id, False) or got

    keep_idx = set(keeper.values())
    drop = [i for i in candidates if i not in keep_idx]
    if not drop:
        return blocks, 0
    drop_set = set(drop)
    return [b for i, b in enumerate(blocks) if i not in drop_set], len(drop)


def dedup_chat(chat: Any) -> tuple[Any, list[tuple[str, str, int]]]:
    """chat JSON を複製して掃除する。戻り値: (新 chat, [(場所, message id, 落とした数)])。"""
    if not isinstance(chat, dict):
        return chat, []
    new = copy.deepcopy(chat)
    changes: list[tuple[str, str, int]] = []

    def fix(where: str, message: Any) -> None:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return
        blocks, dropped = dedup_blocks(message.get("content_blocks"))
        if dropped:
            message["content_blocks"] = blocks
            changes.append((where, str(message.get("id")), dropped))

    history_messages = (new.get("history") or {}).get("messages") or {}
    if isinstance(history_messages, dict):
        for message in history_messages.values():
            fix("history", message)
    linear = new.get("messages") or []
    if isinstance(linear, list):
        for message in linear:
            fix("messages", message)
    return new, changes


def _chat_table() -> Table:
    # 必要な列だけの宣言(nucllei.models を import すると migration が走るので使わない)
    return Table(
        "chat",
        MetaData(),
        Column("id", String, primary_key=True),
        Column("chat", JSON),
        Column("updated_at", BigInteger),
    )


def _data_dir() -> Path:
    # nucllei.env と同じ既定(repo ルート直下 data/)。コンテナでは DATA_DIR が入っている
    if os.environ.get("DATA_DIR"):
        return Path(os.environ["DATA_DIR"])
    return Path(__file__).resolve().parent.parent.parent / "data"


def _default_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        url = f"sqlite:///{_data_dir()}/webui.db"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _default_backup_dir() -> Path:
    return _data_dir() / "backups"


def run(
    database_url: Optional[str] = None,
    apply: bool = False,
    backup_dir: Optional[Path] = None,
    out=sys.stdout,
) -> dict:
    """走査(と apply 時は書き込み)。戻り値は件数の要約。"""
    url = database_url or _default_database_url()
    backup_dir = Path(backup_dir) if backup_dir else _default_backup_dir()
    engine = create_engine(url)
    table = _chat_table()

    targets = []  # (id, updated_at, old chat, new chat, changes)
    with engine.connect() as conn:
        for row in conn.execute(table.select()):
            chat = row.chat
            if isinstance(chat, str):
                chat = json.loads(chat)
            new, changes = dedup_chat(chat)
            if changes:
                targets.append((row.id, row.updated_at, chat, new, changes))

    n_messages = len({(t[0], c[1]) for t in targets for c in t[4]})
    n_blocks = sum(c[2] for t in targets for c in t[4] if c[0] == "history")
    print(
        f"{'APPLY' if apply else 'DRY-RUN'}: {len(targets)} chats / {n_messages} messages / "
        f"{n_blocks} duplicate tool_calls blocks (history)",
        file=out,
    )
    for chat_id, _, _, _, changes in targets:
        for where, message_id, dropped in changes:
            print(
                f"  chat={chat_id} {where} message={message_id} drop={dropped}",
                file=out,
            )

    summary = {
        "chats": len(targets),
        "messages": n_messages,
        "blocks": n_blocks,
        "updated": 0,
        "skipped": [],
        "backup": None,
    }
    if not apply or not targets:
        return summary

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backend = make_url(url).get_backend_name()
    if backend == "sqlite":
        db_path = Path(make_url(url).database)
        dest = backup_dir / f"{db_path.name}.dedup-tool-calls-{stamp}"
        shutil.copy2(db_path, dest)
    else:
        dest = backup_dir / f"dedup-tool-calls-{stamp}.json"
        dest.write_text(
            json.dumps(
                [{"id": t[0], "updated_at": t[1], "chat": t[2]} for t in targets],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    summary["backup"] = str(dest)
    print(f"backup: {dest}", file=out)

    # updated_at は進めない(一覧の並びを変えない)。読んだ時点の updated_at を条件にして、
    # 走査後に書かれた chat は上書きせず skip する(再実行で拾える)。
    with engine.begin() as conn:
        for chat_id, updated_at, _, new, _ in targets:
            cond = table.c.id == chat_id
            cond = cond & (
                table.c.updated_at.is_(None)
                if updated_at is None
                else table.c.updated_at == updated_at
            )
            res = conn.execute(table.update().where(cond).values(chat=new))
            if res.rowcount == 1:
                summary["updated"] += 1
            else:
                summary["skipped"].append(chat_id)
    print(
        f"updated {summary['updated']} chats"
        + (
            f", skipped (changed meanwhile): {summary['skipped']}"
            if summary["skipped"]
            else ""
        ),
        file=out,
    )
    return summary


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="書き込む(既定は dry-run)")
    parser.add_argument(
        "--database-url", default=None, help="既定: $DATABASE_URL か $DATA_DIR/webui.db"
    )
    parser.add_argument("--backup-dir", default=None, help="既定: $DATA_DIR/backups")
    args = parser.parse_args(argv)
    run(
        args.database_url,
        args.apply,
        Path(args.backup_dir) if args.backup_dir else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
