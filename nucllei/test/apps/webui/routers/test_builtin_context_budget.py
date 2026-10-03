"""組み込みツール builtin_context_budget(get_context_budget)の回帰。

llens `tools/context_budget.py` からの移設(2026-10-03)。真実源は record_usage が
chat.meta["usage"] に積む実測 usage。owner-only を固定する。
"""

import asyncio
import types

from test.util.abstract_integration_test import AbstractPostgresTest


def _run(coro):
    return asyncio.run(coro)


class TestBuiltinContextBudgetTool(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tools"

    def setup_class(cls):
        super().setup_class()
        from nucllei.models.chats import Chats

        cls.Chats = Chats

    def _new_chat(self, user_id="owner"):
        from nucllei.models.chats import ChatForm

        return self.Chats.insert_new_chat(
            user_id,
            ChatForm(
                chat={
                    "title": "budget",
                    "history": {
                        "currentId": "m1",
                        "messages": {
                            "m1": {"id": "m1", "role": "assistant", "content": ""}
                        },
                    },
                }
            ),
        )

    def _call(self, chat_id, user_id="owner", request=None, model=None):
        from nucllei.builtin_tools.context_budget import Tools

        return _run(
            Tools().get_context_budget(
                __metadata__={"chat_id": chat_id, "message_id": "m1"},
                __user__={"id": user_id},
                __request__=request,
                __model__=model,
            )
        )

    def test_registered_as_builtin(self):
        from nucllei.builtin_tools import BUILTIN_TOOLS, sync_builtin_tools
        from nucllei.models.tools import Tools

        assert "builtin_context_budget" in BUILTIN_TOOLS
        sync_builtin_tools()
        row = Tools.get_tool_by_id("builtin_context_budget")
        assert row is not None
        assert row.meta.builtin is True
        assert {s["name"] for s in row.specs} == {"get_context_budget"}
        # 予約引数(__metadata__ 等)はモデル向け spec に出ない
        assert row.specs[0]["parameters"]["properties"] == {}

    def test_no_usage_yet_returns_error(self):
        chat = self._new_chat()
        out = self._call(chat.id)
        assert out["ok"] is False
        assert "usage" in out["error"]

    def test_reports_carried_and_context_size(self):
        chat = self._new_chat()
        # tool ループの 1 回目と 2 回目: prompt / carried は最新値、completion は累積
        self.Chats.record_usage_by_id_and_message_id(
            chat.id,
            "m1",
            {"prompt_tokens": 1000, "completion_tokens": 100, "carried_tokens": 1050},
            context_size=200000,
        )
        self.Chats.record_usage_by_id_and_message_id(
            chat.id,
            "m1",
            {"prompt_tokens": 1500, "completion_tokens": 40, "carried_tokens": 1520},
            context_size=200000,
        )
        out = self._call(chat.id)
        assert out["ok"] is True
        assert out["used_tokens"] == 1520  # carried_tokens(最新の呼び出し)
        assert out["context_size"] == 200000
        assert out["remaining_tokens"] == 200000 - 1520
        assert out["used_pct"] == 0.8
        assert "tool 結果はまだ含まれない" in out["note"]

    def test_falls_back_to_prompt_tokens_without_carried(self):
        chat = self._new_chat()
        self.Chats.record_usage_by_id_and_message_id(
            chat.id,
            "m1",
            {"prompt_tokens": 3000, "completion_tokens": 10},
            context_size=4000,
        )
        out = self._call(chat.id)
        assert out["ok"] is True
        assert out["used_tokens"] == 3000
        assert out["remaining_tokens"] == 1000
        assert out["used_pct"] == 75.0

    def test_is_owner_only(self):
        chat = self._new_chat("owner")
        self.Chats.record_usage_by_id_and_message_id(
            chat.id,
            "m1",
            {"prompt_tokens": 10, "completion_tokens": 1, "carried_tokens": 11},
            context_size=100,
        )
        assert self._call(chat.id, user_id="owner")["ok"] is True
        out = self._call(chat.id, user_id="other")
        assert out["ok"] is False
        # 他人の chat_id は「無い」扱い — 存在を漏らさない(usage 無しと同じ文言)
        assert out == self._call("no-such-chat", user_id="other")

    def test_context_size_unknown_returns_nulls(self):
        chat = self._new_chat()
        self.Chats.record_usage_by_id_and_message_id(
            chat.id, "m1", {"prompt_tokens": 500, "completion_tokens": 5}
        )
        # __request__ / __model__ からも解決できない(申告なし・meta.context_size なし)
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(state=types.SimpleNamespace(MODELS={}))
        )
        out = self._call(chat.id, request=request, model={"id": "m", "info": {}})
        assert out["ok"] is True
        assert out["used_tokens"] == 500
        assert out["context_size"] is None
        assert out["remaining_tokens"] is None
        assert out["used_pct"] is None
        assert "context 長が不明" in out["note"]

    def test_context_size_falls_back_to_the_valve(self):
        """記録にも endpoint の申告にも meta にも無いときだけ valve fallback_context_size を使い、
        その旨を note に書く。0 (既定) なら使わない。"""
        from nucllei.builtin_tools.context_budget import Tools

        chat = self._new_chat()
        self.Chats.record_usage_by_id_and_message_id(
            chat.id, "m1", {"prompt_tokens": 1000, "completion_tokens": 5}
        )
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(state=types.SimpleNamespace(MODELS={}))
        )
        tools = Tools()
        tools.valves.fallback_context_size = 4000
        out = _run(
            tools.get_context_budget(
                __metadata__={"chat_id": chat.id, "message_id": "m1"},
                __user__={"id": "owner"},
                __request__=request,
                __model__={"id": "m", "info": {}},
            )
        )
        assert out["context_size"] == 4000 and out["remaining_tokens"] == 3000
        assert out["used_pct"] == 25.0 and "既定値" in out["note"]

        # 記録された context_size があれば valve より優先
        self.Chats.record_usage_by_id_and_message_id(
            chat.id,
            "m1",
            {"prompt_tokens": 1000, "completion_tokens": 5},
            context_size=8000,
        )
        out = _run(
            tools.get_context_budget(
                __metadata__={"chat_id": chat.id, "message_id": "m1"},
                __user__={"id": "owner"},
            )
        )
        assert out["context_size"] == 8000 and "既定値" not in out["note"]

    def test_context_size_resolved_from_model_when_not_recorded(self):
        chat = self._new_chat()
        self.Chats.record_usage_by_id_and_message_id(
            chat.id, "m1", {"prompt_tokens": 500, "completion_tokens": 5}
        )
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(state=types.SimpleNamespace(MODELS={}))
        )
        # endpoint 申告(raw model の max_model_len)→ get_model_context_size
        model = {"id": "m", "openai": {"max_model_len": 8000}, "info": {}}
        out = self._call(chat.id, request=request, model=model)
        assert out["context_size"] == 8000
        assert out["remaining_tokens"] == 7500
        assert out["used_pct"] == 6.2
        # 最後の手段: meta.context_size
        model = {"id": "m", "info": {"meta": {"context_size": 1000}}}
        out = self._call(chat.id, request=request, model=model)
        assert out["context_size"] == 1000
        assert out["remaining_tokens"] == 500
