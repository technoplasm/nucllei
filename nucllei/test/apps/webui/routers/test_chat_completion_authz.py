"""GHSA-gfm2-xm6c-37qc(/api/chat/completions が chat_id の所有者を検証しない)の回帰。

他人が所有する chat_id を渡すと、そのチャットへメッセージが読み書きされる
(クロスユーザーのチャット継続)。所有者でなければ 403 で拒否する。
"""

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


class TestChatCompletionAuthz(AbstractPostgresTest):
    BASE_PATH = "/api/chat"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats

        cls.Chats = Chats

    def test_cannot_use_another_users_chat_id(self):
        """他人所有の chat_id を completion に渡すと 403(所有者検証は model 解決より前)。"""
        from nucllei.models.chats import ChatForm

        victim_chat = self.Chats.insert_new_chat(
            "1", ChatForm(chat={"title": "victim"})
        )

        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        app.state.MODELS = {"m": {"id": "m", "name": "m"}}
        try:
            with mock_webui_user(id="2", role="user"):
                r = self.fast_api_client.post(
                    "/api/chat/completions",
                    json={
                        "model": "m",
                        "messages": [{"role": "user", "content": "hi"}],
                        "chat_id": victim_chat.id,
                    },
                )
        finally:
            app.state.MODELS = original

        assert r.status_code == 403

    def _insert_agent_chat(self, user_id="2"):
        from nucllei.models.chats import ChatForm

        return self.Chats.insert_new_chat(
            user_id,
            ChatForm(chat={"title": "agent"}),
            kind="agent",
            parent_chat_id="parent-1",
            parent_message_id="pm-1",
            depth=1,
        )

    def test_agent_session_completion_rejected_via_http(self):
        """llens(#107): agent session は HTTP 経由では read-only — 所有者でも 403。

        書き手は親 tool 実行の内部経路(utils.chat.generate_chat_completion 直呼び)
        のみで、router を通らないので自然に許可される。
        """
        agent_chat = self._insert_agent_chat(user_id="2")

        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        app.state.MODELS = {"m": {"id": "m", "name": "m"}}
        try:
            with mock_webui_user(id="2", role="user"):
                r = self.fast_api_client.post(
                    "/api/chat/completions",
                    json={
                        "model": "m",
                        "messages": [{"role": "user", "content": "hi"}],
                        "chat_id": agent_chat.id,
                    },
                )
        finally:
            app.state.MODELS = original

        assert r.status_code == 403
        assert "read-only" in r.text

    def test_agent_session_chat_action_rejected(self):
        """llens(#107): /api/chat/actions/{action_id} も agent session へは 400。"""
        agent_chat = self._insert_agent_chat(user_id="2")

        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.post(
                "/api/chat/actions/some-action",
                json={"chat_id": agent_chat.id, "model": "m"},
            )
        assert r.status_code == 400, r.text
        assert "read-only" in r.text


class TestUnreadablePresetChat(AbstractPostgresTest):
    """利用者が読めない preset(サブエージェント用など)で書かれた自分のチャット。

    fork(clone)した agent session などは kind="user" のまま chat.models に読めない
    preset を持つ。FE は選択欄にその id を出すが送信を止める。BE も送らせない:
    completion は 400(Model not found)で、上流へ出ず、チャットも書き換えない。
    preset の名前・system prompt はその利用者には返さない。
    """

    BASE_PATH = "/api/chat"

    PRESET_NAME = "秘密のサブエージェント"
    PRESET_SYSTEM = "【SECRET-SYSTEM】この文が利用者に見えたら失敗"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats

        cls.Chats = Chats

    def _setup(self):
        from nucllei.models.chats import ChatForm
        from nucllei.models.models import ModelForm, ModelParams, Models

        Models.insert_new_model(
            ModelForm(
                id="hidden-sub",
                name=self.PRESET_NAME,
                base_model_id="base-llm",
                meta={},
                params=ModelParams(system=self.PRESET_SYSTEM),
                # read ACL なし = 作成者と admin 以外は読めない
                access_control={},
            ),
            "1",
        )
        messages = {
            "u1": {
                "id": "u1",
                "parentId": None,
                "childrenIds": ["a1"],
                "role": "user",
                "content": "続きをお願いします",
            },
            "a1": {
                "id": "a1",
                "parentId": "u1",
                "childrenIds": [],
                "role": "assistant",
                "content": "",
                "model": "hidden-sub",
            },
        }
        return self.Chats.insert_new_chat(
            "2",
            ChatForm(
                chat={
                    "title": "Clone of agent",
                    "models": ["hidden-sub"],
                    "messages": list(messages.values()),
                    "history": {"currentId": "a1", "messages": messages},
                }
            ),
        )

    def test_send_is_rejected_without_touching_the_chat(self):
        from unittest.mock import patch

        chat = self._setup()
        before = self.Chats.get_chat_by_id(chat.id).chat

        app = self.fast_api_client.app
        original = getattr(app.state, "MODELS", {})
        # 実配備と同じく MODELS には載っている(ACL は check_model_access が見る)
        app.state.MODELS = {"hidden-sub": {"id": "hidden-sub", "name": "x"}}
        try:
            with patch(
                "nucllei.main.process_chat_payload",
                side_effect=AssertionError("payload processing must not run"),
            ):
                with mock_webui_user(id="2", role="user"):
                    r = self.fast_api_client.post(
                        "/api/chat/completions",
                        json={
                            "model": "hidden-sub",
                            "messages": [
                                {"role": "user", "content": "続きをお願いします"}
                            ],
                            "chat_id": chat.id,
                            "id": "a1",
                            "stream": True,
                        },
                    )
        finally:
            app.state.MODELS = original

        assert r.status_code == 400, r.text
        assert r.json()["detail"] == "Model not found"
        # エラーをメッセージへ書かない・models を書き換えない
        assert self.Chats.get_chat_by_id(chat.id).chat == before

    def test_preset_contents_are_not_returned(self):
        self._setup()
        with mock_webui_user(id="2", role="user"):
            r = self.fast_api_client.get("/api/v1/models/model?id=hidden-sub")
        assert r.status_code == 200
        assert r.json() is None
        assert self.PRESET_NAME not in r.text
        assert self.PRESET_SYSTEM not in r.text
