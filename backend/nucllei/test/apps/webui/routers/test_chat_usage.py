"""実測 usage の永続化回帰テスト(issue #57)。

record_usage_by_id_and_message_id はモデルサーバー(SGLang)の本物の usage を
ターン(message.usage)とチャット(meta["usage"])の両単位で記録する。

- prompt_tokens: 最新値で上書き(最後の呼び出しの入力 = 真のコンテキスト量)
- completion_tokens: 加算(tool ループ・再生成を跨いだ生成総量)
- context_size: モデルの max_model_len を添付(#19 閲覧側が使用率を出せる)
"""

from test.util.abstract_integration_test import AbstractPostgresTest


class TestChatUsageRecording(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def setup_method(self):
        super().setup_method()
        from nucllei.models.chats import ChatForm, Chats

        self.Chats = Chats
        chat = Chats.insert_new_chat(
            "u1",
            ChatForm(
                **{
                    "chat": {
                        "title": "usage test",
                        "history": {
                            "currentId": "m1",
                            "messages": {
                                "m1": {"id": "m1", "role": "assistant", "content": ""}
                            },
                        },
                    }
                }
            ),
        )
        self.chat_id = chat.id

    def test_records_message_and_meta(self):
        result = self.Chats.record_usage_by_id_and_message_id(
            self.chat_id,
            "m1",
            {"prompt_tokens": 100, "completion_tokens": 10},
            context_size=262144,
        )
        assert result is not None

        chat = self.Chats.get_chat_by_id(self.chat_id)
        message_usage = chat.chat["history"]["messages"]["m1"]["usage"]
        assert message_usage["prompt_tokens"] == 100
        assert message_usage["completion_tokens"] == 10
        assert message_usage["total_tokens"] == 110
        assert message_usage["context_size"] == 262144

        meta_usage = chat.meta["usage"]
        assert meta_usage["prompt_tokens"] == 100
        assert meta_usage["completion_tokens"] == 10
        assert meta_usage["total_tokens"] == 110
        assert meta_usage["context_size"] == 262144

    def test_accumulates_completion_and_keeps_latest_prompt(self):
        # tool ループ相当: 同一ターン内で 2 回モデル呼び出し
        self.Chats.record_usage_by_id_and_message_id(
            self.chat_id, "m1", {"prompt_tokens": 100, "completion_tokens": 10}
        )
        self.Chats.record_usage_by_id_and_message_id(
            self.chat_id, "m1", {"prompt_tokens": 150, "completion_tokens": 20}
        )

        chat = self.Chats.get_chat_by_id(self.chat_id)
        message_usage = chat.chat["history"]["messages"]["m1"]["usage"]
        assert message_usage["prompt_tokens"] == 150  # 最新値
        assert message_usage["completion_tokens"] == 30  # 加算
        assert message_usage["total_tokens"] == 180

        meta_usage = chat.meta["usage"]
        assert meta_usage["prompt_tokens"] == 150
        assert meta_usage["completion_tokens"] == 30

    def test_meta_accumulates_across_turns(self):
        # 別ターン(別 message)でも meta は会話累積
        self.Chats.upsert_message_to_chat_by_id_and_message_id(
            self.chat_id, "m2", {"id": "m2", "role": "assistant", "content": ""}
        )
        self.Chats.record_usage_by_id_and_message_id(
            self.chat_id, "m1", {"prompt_tokens": 100, "completion_tokens": 10}
        )
        self.Chats.record_usage_by_id_and_message_id(
            self.chat_id, "m2", {"prompt_tokens": 200, "completion_tokens": 5}
        )

        chat = self.Chats.get_chat_by_id(self.chat_id)
        # message 単位はターン独立
        assert (
            chat.chat["history"]["messages"]["m1"]["usage"]["completion_tokens"] == 10
        )
        assert chat.chat["history"]["messages"]["m2"]["usage"]["completion_tokens"] == 5
        # meta は累積 + 最新 prompt
        assert chat.meta["usage"]["prompt_tokens"] == 200
        assert chat.meta["usage"]["completion_tokens"] == 15

    def test_unknown_message_does_not_create_skeleton(self):
        result = self.Chats.record_usage_by_id_and_message_id(
            self.chat_id,
            "no-such-message",
            {"prompt_tokens": 50, "completion_tokens": 1},
        )
        assert result is not None

        chat = self.Chats.get_chat_by_id(self.chat_id)
        # 骸骨 message は作られない
        assert "no-such-message" not in chat.chat["history"]["messages"]
        # meta には記録される
        assert chat.meta["usage"]["prompt_tokens"] == 50

    def test_empty_usage_is_noop(self):
        result = self.Chats.record_usage_by_id_and_message_id(self.chat_id, "m1", {})
        assert result is None

        chat = self.Chats.get_chat_by_id(self.chat_id)
        assert "usage" not in (chat.meta or {})

    def test_context_size_absent_is_omitted(self):
        self.Chats.record_usage_by_id_and_message_id(
            self.chat_id, "m1", {"prompt_tokens": 10, "completion_tokens": 1}
        )
        chat = self.Chats.get_chat_by_id(self.chat_id)
        assert "context_size" not in chat.meta["usage"]
