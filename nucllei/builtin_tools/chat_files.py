"""組み込みツール: チャット添付ファイル(file_id)の一覧・検索。

モデルが file_id を知る手段は発行時の tool 戻り値テキストだけで、長い会話で
コンテキストから落ちると取り戻せない。その再発見手段として、現在のチャットで
file_id 発行済みの添付(ユーザーのアップロード・tool の exports/ 自動回収・
fetch_from_workspace 等)を一覧・名前検索する(#42 history と同じ owner-only 型)。

スコープは**現在チャット限定**(`__chat_id__` + owner-only)。ユーザーの Files
全件を列挙する経路は意図的に作らない — 1 チャットから他チャットのファイルを
発見できると prompt injection 時の露出が質的に変わる(file_id 直指定 tool の
「id を知っていれば読める」capability と「全部ブラウズできる」は別物)。
返すのはメタデータのみで中身は読まない。削除機能も提供しない(#94 の
soft delete / purge は管理系の明示操作 — モデル起点の破壊操作にする理由がない)。
"""

import time
import types
from typing import Optional

from pydantic import BaseModel, Field

from nucllei.models.chats import Chats
from nucllei.models.files import Files
from nucllei.utils.access_control import has_read_access_to_file

DEFAULT_LIMIT = 20


def _fmt_time(epoch: Optional[int]) -> str:
    if not epoch:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(epoch))


def _human_size(n: Optional[int]) -> Optional[str]:
    if not isinstance(n, (int, float)):
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024


def _require_user_id(__user__: Optional[dict]) -> str:
    user_id = (__user__ or {}).get("id")
    if not user_id:
        raise ValueError("ユーザーを特定できないので添付ファイルは一覧できない。")
    return user_id


def _iter_chat_file_ids(chat_json: dict):
    """chat JSON の添付 file_id を出現順(≒発行順)に列挙する。

    走査形は routers/chats.get_file_ids_from_chat と同一(トップレベル files +
    各 message.files、type file/image のみ、id を持たない旧 base64 直入れ
    エントリは自然にスキップ)。router を import すると FastAPI 一式を
    巻き込むため、ここに同形を持つ。
    """
    entries = list(chat_json.get("files") or [])
    for message in ((chat_json.get("history") or {}).get("messages") or {}).values():
        entries.extend(message.get("files") or [])
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") in ("file", "image"):
            file_id = entry.get("id") or (entry.get("file") or {}).get("id")
            if file_id:
                yield file_id


class Tools:
    class Valves(BaseModel):
        max_limit: int = Field(
            default=50, description="limit 引数に許す上限件数(超過指定はここへ clamp)"
        )

    def __init__(self):
        self.valves = self.Valves()

    def _collect(
        self, __user__, __chat_id__, __metadata__, query: Optional[str]
    ) -> tuple[Optional[str], list[dict]]:
        """(エラーメッセージ | None, 添付アイテム列) を返す共通部。"""
        user_id = _require_user_id(__user__)
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id")
        if not chat_id:
            return (
                "ERROR: 現在のチャットを特定できない (チャットの中でだけ使える)。",
                [],
            )

        # owner-only: 他ユーザーの chat は「見つからない」扱い(history と同じ)
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user_id)
        if chat is None:
            return (
                "ERROR: チャットが見つからない (一覧できるのは自分のチャットだけ)。",
                [],
            )

        ids = list(dict.fromkeys(_iter_chat_file_ids(chat.chat or {})))
        if not ids:
            return None, []
        records = {f.id: f for f in Files.get_files_by_ids(ids)}
        # has_read_access_to_file は UserModel 外形(.id / .role)を要求する
        user_ns = types.SimpleNamespace(id=user_id, role=(__user__ or {}).get("role"))

        items = []
        for fid in ids:
            rec = records.get(fid)
            if rec is None:
                continue  # 行そのものが無い id は到達不能 — 載せない
            # chat JSON はクライアント制御(#40 IDOR と同じ前提)。自分のチャットに
            # 他人の file_id が紛れても、read 権の無い物のメタデータは見せない。
            # 削除済みの他人 file もここで落ちる(get_file_by_id が既定除外)。
            if rec.user_id != user_id and not has_read_access_to_file(fid, user_ns):
                continue
            name = rec.filename
            if query and query.lower() not in (name or "").lower():
                continue
            item = {
                "file_id": fid,
                "name": name,
                "content_type": (rec.meta or {}).get("content_type"),
                "size": _human_size((rec.meta or {}).get("size")),
                "created_at": _fmt_time(rec.created_at),
            }
            if rec.deleted_at is not None:
                # 削除(soft delete/purge)済み — この file_id はもう読み出せない
                item["deleted"] = True
            items.append(item)
        return None, items

    def _render(self, items: list[dict], limit) -> dict:
        # tool 引数は実行時の型検証を通らない(モデルは 5.0 や "5" を出し得る)。
        # float のままだと末尾 slice が TypeError、str は min() で落ちる — int へ矯正。
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, self.valves.max_limit))
        total = len(items)
        if total > limit:
            items = items[-limit:]  # 出現順の末尾 = 新しく発行された側を優先
        # dict を素直に返す(自前で直列化しない)。#99 で本体が ensure_ascii=False で
        # 直列化するようになり、実型から render {kind:"json"} も付く。str を返すと
        # text 描画に落ちて JSON ハイライトが消える。
        # 切り詰めは散文の前置きではなく total として構造に載せる — モデルが
        # 「まだある」を機械的に読めるし、表示も JSON 一本で済む。
        return {"total": total, "files": items}

    def list_chat_files(
        self,
        limit: int = DEFAULT_LIMIT,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict | str:
        """このチャットで file_id 発行済みの添付ファイルを一覧する(発行順)。

        ユーザーのアップロードと tool が添付した成果物の両方が対象。過去に発行された
        file_id を思い出す用途に使い、得た file_id は file_id を受け取る他の tool に
        そのまま渡せる。返るのはメタデータのみ(中身は読まない)。

        :param limit: 返す件数の上限(省略時 20、新しいものを優先)
        """
        err, items = self._collect(__user__, __chat_id__, __metadata__, None)
        if err:
            return err
        if not items:
            return "このチャットに添付ファイルは無い。"
        return self._render(items, limit)

    def grep_chat_files(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict | str:
        """このチャットの添付ファイルをファイル名で検索する(部分一致・大小文字無視)。

        :param query: 検索文字列(ファイル名に対する部分一致)
        :param limit: 返す件数の上限(省略時 20、新しいものを優先)
        """
        if not (query or "").strip():
            return "ERROR: 検索文字列が空。次の手: query に検索文字列を入れて呼び直す。"
        err, items = self._collect(__user__, __chat_id__, __metadata__, query)
        if err:
            return err
        if not items:
            return f"「{query}」に一致する添付ファイルは見つからなかった。"
        return self._render(items, limit)
