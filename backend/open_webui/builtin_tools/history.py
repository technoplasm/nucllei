"""組み込みツール: 履歴管理(issue #42)。

自分のチャット履歴の一覧・読み出し・検索。**認可は owner-only 固定**
(`__user__` の chat のみ — gfm2/#37 で塞いだ面をツールで開け直さない)。

現在は機構検証用のスタブ(本実装は後続タスク)。実装時は Chats モデルの
get_chat_list_by_user_id / get_chat_by_id_and_user_id /
get_chats_by_user_id_and_search_text を使う。
"""

from typing import Optional


class Tools:
    def list_history(
        self,
        limit: int = 20,
        __user__: Optional[dict] = None,
    ) -> str:
        """自分の最近のチャット一覧を返す(id・タイトル・更新日時)。

        :param limit: 返す件数の上限(省略時 20)
        """
        # TODO(#42): Chats.get_chat_list_by_user_id(__user__["id"]) で実装
        return "TODO: list_history は未実装です(組み込みツール機構のスタブ)。"

    def read_history(
        self,
        chat_id: str,
        __user__: Optional[dict] = None,
    ) -> str:
        """自分のチャット 1 件の内容(メッセージ列)を読み出す。

        :param chat_id: 対象チャットの id(list_history / grep_history で得たもの)
        """
        # TODO(#42): Chats.get_chat_by_id_and_user_id(chat_id, __user__["id"]) で実装
        # (owner-only: 他ユーザーの chat_id は「見つからない」扱いにする)
        return "TODO: read_history は未実装です(組み込みツール機構のスタブ)。"

    def grep_history(
        self,
        query: str,
        limit: int = 20,
        __user__: Optional[dict] = None,
    ) -> str:
        """自分のチャット履歴をテキスト検索し、ヒットした一覧を返す。

        :param query: 検索文字列
        :param limit: 返す件数の上限(省略時 20)
        """
        # TODO(#42): Chats.get_chats_by_user_id_and_search_text で実装
        return "TODO: grep_history は未実装です(組み込みツール機構のスタブ)。"
