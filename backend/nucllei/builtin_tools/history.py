"""組み込みツール: 履歴管理(issue #42)。

自分のチャット履歴の一覧・読み出し・検索。**認可は owner-only 固定**
(`__user__` の chat のみ — gfm2/#37 で塞いだ面をツールで開け直さない)。

OWUI の Chats モデル(DB)に密結合するため fork の組み込みツールとして提供する
(外部システムを叩く web 検索とは線引きが異なる — 2026-07-13 確定、#42)。
"""

import json
import time
from typing import Optional

from nucllei.models.chats import Chats
from nucllei.utils.misc import get_message_list, get_messages_content

MAX_LIMIT = 50


def _fmt_time(epoch: Optional[int]) -> str:
    if not epoch:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(epoch))


def _require_user_id(__user__: Optional[dict]) -> str:
    user_id = (__user__ or {}).get("id")
    if not user_id:
        raise ValueError("履歴ツールはユーザー文脈が必要です(__user__ 未注入)")
    return user_id


class Tools:
    def list_history(
        self,
        limit: int = 20,
        __user__: Optional[dict] = None,
    ) -> str:
        """自分の最近のチャット一覧を返す(id・タイトル・更新日時)。

        :param limit: 返す件数の上限(省略時 20)
        """
        user_id = _require_user_id(__user__)
        limit = max(1, min(limit, MAX_LIMIT))

        chats = Chats.get_chat_list_by_user_id(user_id, limit=limit)
        items = [
            {
                "chat_id": c.id,
                "title": c.title,
                "updated_at": _fmt_time(c.updated_at),
            }
            for c in chats
        ]
        if not items:
            return "チャット履歴はまだありません。"
        return json.dumps(items, ensure_ascii=False, indent=2)

    def read_history(
        self,
        chat_id: str,
        __user__: Optional[dict] = None,
    ) -> str:
        """自分のチャット 1 件の内容(メッセージ列)を読み出す。

        :param chat_id: 対象チャットの id(list_history / grep_history で得たもの)
        """
        user_id = _require_user_id(__user__)

        # owner-only: 他ユーザーの chat_id は「見つからない」扱い(所有者検証を漏らさない)
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user_id)
        if chat is None:
            return "指定のチャットが見つかりません(自分のチャットのみ読み出せます)。"

        history = chat.chat.get("history", {})
        messages_map = history.get("messages", {})
        current_id = history.get("currentId")

        message_list = None
        if current_id and messages_map:
            message_list = get_message_list(messages_map, current_id)

        if not message_list:
            return f"「{chat.title}」には読み出せるメッセージがありません。"

        body = get_messages_content(message_list)
        return f"# {chat.title}\n\n{body}"

    def grep_history(
        self,
        query: str,
        limit: int = 20,
        __user__: Optional[dict] = None,
    ) -> str:
        """自分のチャット履歴をテキスト検索し、ヒットした一覧を返す。

        :param query: 検索文字列(タイトル・本文を対象)
        :param limit: 返す件数の上限(省略時 20)
        """
        user_id = _require_user_id(__user__)
        limit = max(1, min(limit, MAX_LIMIT))

        if not (query or "").strip():
            return "検索文字列を指定してください。"

        chats = Chats.get_chats_by_user_id_and_search_text(user_id, query, limit=limit)
        items = [
            {
                "chat_id": c.id,
                "title": c.title,
                "updated_at": _fmt_time(c.updated_at),
            }
            for c in chats
        ]
        if not items:
            return f"「{query}」に一致するチャットは見つかりませんでした。"
        return json.dumps(items, ensure_ascii=False, indent=2)
