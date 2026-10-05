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
