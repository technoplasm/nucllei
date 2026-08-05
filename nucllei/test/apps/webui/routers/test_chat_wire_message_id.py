"""#98: chat-wire の `id` が検証されず、壊れたチャットが黙って永続化される。

`POST /api/chat/completions` の chat-wire モード(`chat_id` + `id`)で、`id` は
「これから生成されるアシスタントメッセージ」の id でなければならない。検証が無いと
`upsert_message_to_chat_by_id_and_message_id` が

  - 既存の user メッセージへ生成結果をマージ(回答がユーザー発話として残る)
  - 未知 id なら role/parentId を欠いたメッセージ片を新規挿入

を黙って行う。医療系の配備でモデルの回答がユーザー発話として記録されるのは
監査・引用の観点で誤解を招くため、呼び出し側の誤りとして 400 で弾く。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestChatWireMessageId(AbstractPostgresTest):
    BASE_PATH = "/api/chat"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats

        cls.Chats = Chats

    def _chat_with(self, user_id, messages):
        """history.messages を持つ実 OWUI 形の chat を 1 件作る。"""
        from nucllei.models.chats import ChatForm

        msg_map = {m["id"]: m for m in messages}
        return self.Chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={
                    "title": "t",
                    "models": ["m"],
                    "messages": messages,
                    "history": {
                        "currentId": messages[-1]["id"] if messages else None,
                        "messages": msg_map,
                    },
                }
            ),
        )

    def _user_msg(self, mid="u1"):
        return {
            "id": mid,
            "parentId": None,
            "childrenIds": [],
            "role": "user",
            "content": "1+1は?",
        }

    def _assistant_msg(self, mid="a1", parent="u1"):
        return {
            "id": mid,
            "parentId": parent,
            "childrenIds": [],
            "role": "assistant",
            "content": "",
        }

    def _post(self, chat_id, message_id, user_id="1"):
        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        app.state.MODELS = {"m": {"id": "m", "name": "m"}}
        try:
            with mock_webui_user(id=user_id, role="user"):
                return self.fast_api_client.post(
                    "/api/chat/completions",
                    json={
                        "model": "m",
                        "messages": [{"role": "user", "content": "1+1は?"}],
                        "chat_id": chat_id,
                        "id": message_id,
                    },
                )
        finally:
            app.state.MODELS = original

    def test_user_message_id_is_rejected(self):
        """ケース A: user メッセージの id を渡すと 400(回答が追記されない)。"""
        chat = self._chat_with("1", [self._user_msg()])

        res = self._post(chat.id, "u1")

        assert res.status_code == 400
        assert "assistant message" in res.json()["detail"]

        # チャットは一切汚れていない
        after = self.Chats.get_chat_by_id(chat.id)
        messages = after.chat["history"]["messages"]
        assert list(messages) == ["u1"]
        assert messages["u1"]["role"] == "user"
        assert messages["u1"]["content"] == "1+1は?"

    def test_unknown_message_id_is_rejected(self):
        """ケース B: 存在しない id を渡すと 400(不正スキーマの片が残らない)。"""
        chat = self._chat_with("1", [self._user_msg()])

        res = self._post(chat.id, "does-not-exist")

        assert res.status_code == 400
        assert "existing message" in res.json()["detail"]

        after = self.Chats.get_chat_by_id(chat.id)
        messages = after.chat["history"]["messages"]
        assert list(messages) == ["u1"]

    def test_error_path_does_not_create_message_fragment(self):
        """検証を try の外に置く理由の固定。

        payload 処理の except は `message_id` へエラーを upsert する。不正 id の
        ままそこへ落ちると、**エラー経路自体が** role/parentId を欠いた
        メッセージ片を作ってしまう。process_chat_payload を失敗させて、
        その except に確実に入る条件で確かめる
        (Model not found は metadata 代入より前に throw するので入らない)。
        """
        from unittest.mock import patch

        chat = self._chat_with("1", [self._user_msg()])

        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        app.state.MODELS = {"m": {"id": "m", "name": "m"}}
        try:
            with patch(
                "nucllei.main.process_chat_payload",
                side_effect=Exception("boom"),
            ):
                # admin: check_model_access を迂回する(ダミーモデル dict は
                # role="user" の経路だと Model not found で metadata 代入前に落ちる)
                with mock_webui_user(id="1", role="admin"):
                    res = self.fast_api_client.post(
                        "/api/chat/completions",
                        json={
                            "model": "m",
                            "messages": [{"role": "user", "content": "hi"}],
                            "chat_id": chat.id,
                            "id": "ghost",
                        },
                    )
        finally:
            app.state.MODELS = original

        # id 検証で先に弾かれる(payload 処理には到達しない)
        assert res.status_code == 400
        assert "existing message" in res.json()["detail"]

        after = self.Chats.get_chat_by_id(chat.id)
        assert list(after.chat["history"]["messages"]) == ["u1"]

    def test_error_path_writes_to_a_valid_assistant_message(self):
        """正しい id ならエラーは当該アシスタントメッセージへ書かれる
        (検証を足したせいでエラー通知経路が壊れていないことの担保)。"""
        from unittest.mock import patch

        chat = self._chat_with("1", [self._user_msg(), self._assistant_msg()])

        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        app.state.MODELS = {"m": {"id": "m", "name": "m"}}
        try:
            with patch(
                "nucllei.main.process_chat_payload",
                side_effect=Exception("boom"),
            ):
                # admin: check_model_access を迂回する(ダミーモデル dict は
                # role="user" の経路だと Model not found で metadata 代入前に落ちる)
                with mock_webui_user(id="1", role="admin"):
                    res = self.fast_api_client.post(
                        "/api/chat/completions",
                        json={
                            "model": "m",
                            "messages": [{"role": "user", "content": "hi"}],
                            "chat_id": chat.id,
                            "id": "a1",
                        },
                    )
        finally:
            app.state.MODELS = original

        assert res.status_code == 400
        after = self.Chats.get_chat_by_id(chat.id)
        messages = after.chat["history"]["messages"]
        assert set(messages) == {"u1", "a1"}
        # 既存のアシスタントメッセージへマージされる(片を作らない)
        assert messages["a1"]["role"] == "assistant"
        assert messages["a1"]["error"] == {"content": "boom"}

    def test_assistant_message_id_passes_validation(self):
        """正しい使い方(空のアシスタントメッセージの id)は検証を通る。

        通ったあとはモデル解決へ進むので、ここでは「400 の理由が id 検証では
        ないこと」を見る(存在しないモデルで弾かれる)。
        """
        chat = self._chat_with("1", [self._user_msg(), self._assistant_msg()])

        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        app.state.MODELS = {}  # モデル未登録 → Model not found で 400
        try:
            with mock_webui_user(id="1", role="user"):
                res = self.fast_api_client.post(
                    "/api/chat/completions",
                    json={
                        "model": "m",
                        "messages": [{"role": "user", "content": "1+1は?"}],
                        "chat_id": chat.id,
                        "id": "a1",
                    },
                )
        finally:
            app.state.MODELS = original

        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "assistant message" not in detail
        assert "existing message" not in detail

    def test_ownership_check_still_precedes_id_validation(self):
        """他人のチャットは id の形に関わらず 403(所有者検証が先)。

        400 を返すと「その id は存在しない」= チャットの中身が漏れる。
        """
        victim = self._chat_with("1", [self._user_msg()])

        res = self._post(victim.id, "does-not-exist", user_id="2")

        assert res.status_code == 403

    def test_headless_mode_without_chat_id_is_untouched(self):
        """chat-wire でない(chat_id 無し)呼び出しには何も課さない。"""
        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        app.state.MODELS = {}
        try:
            with mock_webui_user(id="1", role="user"):
                res = self.fast_api_client.post(
                    "/api/chat/completions",
                    json={
                        "model": "m",
                        "messages": [{"role": "user", "content": "hi"}],
                        "id": "whatever",
                    },
                )
        finally:
            app.state.MODELS = original

        # id 検証には到達しない(chat_id が無いので chat-wire ではない)
        assert res.status_code == 400
        assert "Model not found" in res.json()["detail"]
