"""組み込みツール: 履歴管理(issue #42)。

自分のチャット履歴の一覧・読み出し・検索。**認可は owner-only 固定**
(`__user__` の chat のみ — gfm2/#37 で塞いだ面をツールで開け直さない)。

OWUI の Chats モデル(DB)に密結合するため fork の組み込みツールとして提供する
(外部システムを叩く web 検索とは線引きが異なる — 2026-07-13 確定、#42)。
"""

import time
import unicodedata
from typing import Optional

from nucllei.models.chats import Chats
from nucllei.utils.misc import (
    get_content_from_message,
    get_message_list,
    get_messages_content,
)
from nucllei.utils.tool_errors import ToolCallError, ToolFailure
from nucllei.utils.tool_render import Markdown

MAX_LIMIT = 50
# grep_history の抜粋: 1 チャットあたりの最大件数と、一致箇所の前後に付ける文字数
MAX_MATCHES_PER_CHAT = 3
MATCH_CONTEXT_CHARS = 60


def _fold(s: str) -> str:
    """照合用の正規化 (NFKC + casefold)。全角半角・大小文字の違いを無視する (DB 側の検索と揃える)。"""
    return unicodedata.normalize("NFKC", s or "").casefold()


def _excerpts(chat, query: str) -> list[dict]:
    """チャットの現在の枝のメッセージから、query を含む箇所を前後 MATCH_CONTEXT_CHARS 文字つきで
    最大 MAX_MATCHES_PER_CHAT 件抜く (grep_doc と同じく、一致箇所を見せる — ken 2026-10-03:
    タイトルと日時だけでは何がヒットしたか分からない)。NFKC + casefold で照合するので、
    位置は正規化後の文字列で取り、表示も正規化後の文字列から切る (長さが変わる文字があるため)。"""
    history = (chat.chat or {}).get("history", {})
    messages_map = history.get("messages", {})
    current_id = history.get("currentId")
    if not (current_id and messages_map):
        return []
    try:
        message_list = get_message_list(messages_map, current_id) or []
    except Exception:  # noqa: BLE001 — 壊れた履歴は抜粋なし (一覧には残す)
        return []
    needle = _fold(query)
    out: list[dict] = []
    for message in message_list:
        text = get_content_from_message(message) or ""
        folded = _fold(text)
        pos = folded.find(needle)
        while pos >= 0 and len(out) < MAX_MATCHES_PER_CHAT:
            start = max(0, pos - MATCH_CONTEXT_CHARS)
            end = min(len(folded), pos + len(needle) + MATCH_CONTEXT_CHARS)
            # 正規化で長さが変わらなければ原文から切る (大小文字・全角半角を元のまま見せる)
            source = text if len(text) == len(folded) else folded
            excerpt = source[start:end].replace("\n", " ")
            out.append(
                {
                    "role": message.get("role", ""),
                    "excerpt": ("…" if start > 0 else "")
                    + excerpt
                    + ("…" if end < len(folded) else ""),
                }
            )
            pos = folded.find(needle, pos + max(1, len(needle)))
        if len(out) >= MAX_MATCHES_PER_CHAT:
            break
    return out


def _fmt_time(epoch: Optional[int]) -> str:
    if not epoch:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(epoch))


def _require_user_id(__user__: Optional[dict]) -> str:
    user_id = (__user__ or {}).get("id")
    if not user_id:
        # 失敗は ToolError で返す (README_API §5.1。旧: ValueError = 汎用の例外経路で fatal 扱い)
        raise ToolFailure("ユーザーを特定できないので履歴は読めない")
    return user_id


class Tools:
    def list_history(
        self,
        limit: int = 20,
        __user__: Optional[dict] = None,
    ) -> list | str:
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
            return "チャット履歴はまだ無い。"
        # list を素直に返す(自前で直列化しない)。#99 で本体が ensure_ascii=False で
        # 直列化するようになり、実型から render {kind:"json"} も付く。str を返すと
        # text 描画に落ちて JSON ハイライトが消える。
        return items

    def read_history(
        self,
        chat_id: str,
        __user__: Optional[dict] = None,
    ) -> Markdown:
        """自分のチャット 1 件の内容(メッセージ列)を読み出す。

        :param chat_id: 対象チャットの id(list_history / grep_history で得たもの)
        """
        user_id = _require_user_id(__user__)

        # owner-only: 他ユーザーの chat_id は「見つからない」扱い(所有者検証を漏らさない)
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user_id)
        if chat is None:
            raise ToolCallError(
                "指定のチャットは見つからない (読めるのは自分のチャットだけ)",
                "list_history / grep_history で得た chat_id を渡す。",
            )

        history = chat.chat.get("history", {})
        messages_map = history.get("messages", {})
        current_id = history.get("currentId")

        message_list = None
        if current_id and messages_map:
            message_list = get_message_list(messages_map, current_id)

        if not message_list:
            return f"「{chat.title}」には読み出せるメッセージが無い。"

        body = get_messages_content(message_list)
        return f"# {chat.title}\n\n{body}"

    def grep_history(
        self,
        query: str,
        limit: int = 20,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
    ) -> list | str:
        """自分のチャット履歴をテキスト検索し、ヒットした一覧を返す。各ヒットには一致箇所の
        抜粋 (`matches`: 発言者と前後の文脈、最大 3 件) が付くので、どのチャットが目的の物かは
        抜粋で判断し、全文が要るときだけ read_history で読む。いま進行中のチャットは対象外
        (検索を頼んだ発言自身がヒットするのを避ける)。

        :param query: 検索文字列(タイトル・本文を対象)
        :param limit: 返す件数の上限(省略時 20)
        """
        user_id = _require_user_id(__user__)
        limit = max(1, min(limit, MAX_LIMIT))

        if not (query or "").strip():
            raise ToolCallError(
                "検索文字列が空", "query に検索文字列を入れて呼び直す。"
            )

        # 進行中のチャット (__chat_id__) は除く — 「『X』で検索して」という発言自身が X にヒットする
        # (ken 2026-10-03)。除いた分だけ 1 件多く取っておく
        chats = Chats.get_chats_by_user_id_and_search_text(
            user_id, query, limit=limit + (1 if __chat_id__ else 0)
        )
        items = []
        for c in chats:
            if __chat_id__ and c.id == __chat_id__:
                continue
            if len(items) >= limit:
                break
            matches = _excerpts(c, query)
            items.append(
                {
                    "chat_id": c.id,
                    "title": c.title,
                    "updated_at": _fmt_time(c.updated_at),
                    # 本文に一致が無い (タイトルだけの一致) は空 — モデルにはその旨が分かる
                    "matches": matches,
                }
            )
        if not items:
            return f"「{query}」に一致するチャットは見つからなかった。"
        return items
