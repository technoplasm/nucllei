"""能力に付く builtin (feed_image / feed_audio) の middleware 経路の回帰。

process_chat_payload が toolIds に依らず、画像を見られる (capabilities.vision が明示的に false でない) モデルに
feed_image を、音声を聞ける (capabilities.audio) モデルに feed_audio を出すか。toolIds に残った
builtin_feed_image / builtin_feed_audio で二重にならないか、サブエージェントの子ターンも子の preset の
能力で決まるか、を見る。tool 本体は `routers/test_builtin_feed_image.py` / `test_builtin_feed_audio.py`。
"""

import asyncio
import types

from test.util.abstract_integration_test import AbstractPostgresTest

FEED_TOOLS = {"feed_image", "feed_audio"}


def _run_payload(model, monkeypatch, agent_session=False):
    from starlette.requests import Request

    import nucllei.env
    from nucllei.builtin_tools import sync_builtin_tools
    from nucllei.main import app
    from nucllei.utils import middleware

    # 行はテストごとに消える (AbstractPostgresTest の TRUNCATE) — 起動時と同じ sync で入れ直す
    sync_builtin_tools()
    # レシピの tool は sandbox の無い配備では入らない (sandbox に聞かせない)
    monkeypatch.setattr(nucllei.env, "SANDBOX_URL", "")
    user = types.SimpleNamespace(
        id="1", name="Taro", email="t@example.invalid", role="user", info=None
    )
    monkeypatch.setattr(app.state, "MODELS", {model["id"]: model}, raising=False)
    request = Request(
        scope={
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/chat/completions",
            "headers": [],
            "query_string": b"",
        }
    )
    form_data = {
        "model": model["id"],
        "stream": True,
        "messages": [{"role": "user", "content": "見て"}],
    }
    metadata = {"user_id": "1", "model": model}
    if agent_session:
        metadata["agent_session"] = True
    form_data, metadata, _ = asyncio.run(
        middleware.process_chat_payload(request, form_data, user, metadata, model, None)
    )
    names = [t["function"]["name"] for t in form_data.get("tools") or []]
    return names, metadata


def _model(capabilities=None, tool_ids=None):
    meta = {}
    if capabilities is not None:
        meta["capabilities"] = capabilities
    if tool_ids is not None:
        meta["toolIds"] = tool_ids
    return {"id": "fc", "name": "fc", "owned_by": "openai", "info": {"meta": meta}}


class TestFeedCapability(AbstractPostgresTest):
    BASE_PATH = "/api/v1/chats"

    def test_vision_offers_feed_image_without_tool_ids(self, monkeypatch):
        names, metadata = _run_payload(_model({"vision": True}), monkeypatch)
        assert names.count("feed_image") == 1
        assert metadata["tools"]["feed_image"]["tool_id"] == "builtin_feed_image"
        assert "feed_audio" not in names

    def test_vision_key_absent_is_on(self, monkeypatch):
        names, _ = _run_payload(_model(), monkeypatch)
        assert names == ["feed_image"]

    def test_vision_false_offers_no_feed_image(self, monkeypatch):
        names, _ = _run_payload(_model({"vision": False}), monkeypatch)
        assert not FEED_TOOLS & set(names)

    def test_audio_offers_feed_audio(self, monkeypatch):
        names, _ = _run_payload(_model({"vision": False, "audio": True}), monkeypatch)
        assert names == ["feed_audio"]
        names, _ = _run_payload(_model({"vision": True, "audio": True}), monkeypatch)
        assert sorted(names) == ["feed_audio", "feed_image"]

    def test_audio_absent_offers_no_feed_audio(self, monkeypatch):
        names, _ = _run_payload(_model({"vision": True}), monkeypatch)
        assert "feed_audio" not in names

    def test_stale_ids_in_tool_ids_add_no_second_copy(self, monkeypatch):
        """toolIds に残った builtin_feed_image / builtin_feed_audio は読まない — 能力のあるモデルで 1 つ、
        能力の無いモデルでは出ない (toolIds では出せない)。"""
        stale = ["builtin_feed_image", "builtin_feed_audio"]
        names, _ = _run_payload(
            _model({"vision": True, "audio": True}, tool_ids=stale), monkeypatch
        )
        assert sorted(names) == ["feed_audio", "feed_image"]
        names, _ = _run_payload(_model({"vision": False}, tool_ids=stale), monkeypatch)
        assert not FEED_TOOLS & set(names)

    def test_rows_are_marked_with_the_capability(self, monkeypatch):
        """行の meta.capability — UI がツールの選択肢から外す印。"""
        from nucllei.models.tools import Tools

        _run_payload(_model(), monkeypatch)
        assert Tools.get_tool_by_id("builtin_feed_image").meta.capability == "vision"
        assert Tools.get_tool_by_id("builtin_feed_audio").meta.capability == "audio"
        assert Tools.get_tool_by_id("builtin_history").meta.capability is None

    def test_subagent_child_turn_follows_the_child_preset(self, monkeypatch):
        """サブエージェントの子ターン (agent_session) も同じ process_chat_payload — 子の preset の能力で決まる。"""
        names, _ = _run_payload(
            _model({"vision": True, "audio": True}), monkeypatch, agent_session=True
        )
        assert sorted(names) == ["feed_audio", "feed_image"]
        names, _ = _run_payload(
            _model({"vision": False}), monkeypatch, agent_session=True
        )
        assert not FEED_TOOLS & set(names)
