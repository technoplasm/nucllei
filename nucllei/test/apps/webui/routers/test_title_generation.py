"""タイトル生成 (`POST /api/v1/tasks/title/completions`) が上流へ送る body の回帰。

- preset の system prompt を当てない: 送るのは task の template と会話の文字だけ
- preset の base_model_id への差し替え・system 以外の params は当たる
- model の read ACL は今まで通り効く (ACL 外は 404、上流へは出ない)
- pipeline の inlet filter を通る
- 呼び出しごとに `model call ... call=title` の 1 行が出る
"""

import json

from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user

PRESET_SYSTEM = "【PRESET-SYSTEM】この文が上流に出たら失敗"

MESSAGES = [
    {"role": "user", "content": "札幌で冬に気をつけることは?"},
    {"role": "assistant", "content": "路面の凍結と防寒です。"},
]

TITLE_CONTENT = '{"title": "札幌の冬"}'

UPSTREAM_RESPONSE = {
    "id": "x",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": TITLE_CONTENT,
                "reasoning_content": "考えた",
            },
        }
    ],
    "usage": {"prompt_tokens": 300, "completion_tokens": 12, "total_tokens": 312},
}


class _FakeJsonResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    async def json(self):
        return json.loads(json.dumps(UPSTREAM_RESPONSE))

    def raise_for_status(self):
        pass

    def close(self):
        pass

    def release(self):
        pass


class TestTitleGenerationPayload(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tasks"

    def _setup(self, monkeypatch, access_control):
        from nucllei.main import app
        from nucllei.models.models import ModelForm, ModelParams, Models
        from nucllei.routers import openai as openai_router
        from nucllei.routers import tasks as tasks_router

        Models.insert_new_model(
            ModelForm(
                id="preset",
                name="preset",
                base_model_id="base-llm",
                meta={},
                params=ModelParams(system=PRESET_SYSTEM, temperature=0.3),
                access_control=access_control,
            ),
            "1",
        )
        monkeypatch.setattr(
            app.state,
            "MODELS",
            {"preset": {"id": "preset", "owned_by": "openai"}},
            raising=False,
        )
        monkeypatch.setattr(
            app.state,
            "OPENAI_MODELS",
            {"base-llm": {"id": "base-llm", "urlIdx": 0}},
            raising=False,
        )
        config = app.state.config
        for key, value in (
            ("OPENAI_API_BASE_URLS", ["http://upstream.invalid/v1"]),
            ("OPENAI_API_KEYS", ["sk-dummy"]),
            ("OPENAI_API_CONFIGS", {}),
            ("ENABLE_TITLE_GENERATION", True),
            ("TASK_MODEL", ""),
            ("TASK_MODEL_EXTERNAL", ""),
        ):
            monkeypatch.setattr(config, key, value)

        sent = []

        class FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            async def request(self, method, url, data=None, headers=None):
                sent.append(json.loads(data))
                return _FakeJsonResponse()

            async def close(self):
                pass

        monkeypatch.setattr(openai_router.aiohttp, "ClientSession", FakeSession)

        inlet_calls = []

        async def inlet(request, payload, user, models):
            inlet_calls.append(json.loads(json.dumps(payload, default=str)))
            return payload

        monkeypatch.setattr(tasks_router, "process_pipeline_inlet_filter", inlet)

        logged = []
        from nucllei.utils import middleware

        class _Log:
            def info(self, line, *args):
                logged.append(("info", line))

            def warning(self, line, *args):
                logged.append(("warning", line))

            def debug(self, *args, **kwargs):
                pass

        monkeypatch.setattr(middleware, "log", _Log())
        return sent, inlet_calls, logged

    def _post(self, user_id, role):
        with mock_webui_user(id=user_id, role=role):
            return self.fast_api_client.post(
                self.create_url("/title/completions"),
                json={"model": "preset", "messages": MESSAGES, "chat_id": "c1"},
            )

    def test_title_body_has_no_preset_system(self, monkeypatch):
        sent, inlet_calls, logged = self._setup(monkeypatch, access_control=None)
        response = self._post("1", "admin")
        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["message"]["content"] == TITLE_CONTENT

        assert len(sent) == 1
        body = sent[0]
        # 送るのは task の template + 会話の user メッセージ 1 つだけ
        assert [m["role"] for m in body["messages"]] == ["user"]
        assert PRESET_SYSTEM not in json.dumps(body, ensure_ascii=False)
        content = body["messages"][0]["content"]
        assert "札幌で冬に気をつけることは?" in content
        assert "路面の凍結と防寒です。" in content
        # base_model_id への差し替えと system 以外の params は当たる
        assert body["model"] == "base-llm"
        assert body["temperature"] == 0.3
        assert body["stream"] is False
        assert body["max_tokens"] == 4000
        assert "metadata" not in body

        # inlet filter を通る (task の印付き)
        assert len(inlet_calls) == 1
        assert inlet_calls[0]["metadata"]["task"] == "title_generation"

        # model call の 1 行
        lines = [line for level, line in logged if "call=title" in line]
        assert len(lines) == 1
        assert ("info", lines[0]) in logged
        line = lines[0]
        assert line.startswith("model call chat_id=c1 ")
        assert "model=preset " in line
        assert "finish_reason=stop " in line
        assert f"text_chars={len(TITLE_CONTENT)} " in line
        assert "reasoning_chars=3 " in line
        assert "completion_tokens=12 " in line
        assert " elapsed=" in line

    def test_user_with_read_access_gets_title_without_system(self, monkeypatch):
        sent, _, _ = self._setup(
            monkeypatch,
            access_control={
                "read": {"group_ids": [], "user_ids": ["9"]},
                "write": {"group_ids": [], "user_ids": []},
            },
        )
        response = self._post("9", "user")
        assert response.status_code == 200, response.text
        assert len(sent) == 1
        assert [m["role"] for m in sent[0]["messages"]] == ["user"]
        assert PRESET_SYSTEM not in json.dumps(sent[0], ensure_ascii=False)

    def test_user_without_read_access_is_404_and_nothing_is_sent(self, monkeypatch):
        sent, inlet_calls, _ = self._setup(monkeypatch, access_control={})
        response = self._post("9", "user")
        assert response.status_code == 404
        assert sent == []
        assert inlet_calls == []


def test_system_prompt_is_not_applied_to_task_calls():
    from nucllei.utils.payload import apply_model_system_prompt_to_body

    params = {"system": PRESET_SYSTEM}
    task_body = {"messages": [{"role": "user", "content": "t"}]}
    out = apply_model_system_prompt_to_body(
        params, task_body, {"task": "title_generation"}
    )
    assert out["messages"] == [{"role": "user", "content": "t"}]

    chat_body = {"messages": [{"role": "user", "content": "t"}]}
    out = apply_model_system_prompt_to_body(params, chat_body, {"chat_id": "c"})
    assert out["messages"][0] == {"role": "system", "content": PRESET_SYSTEM}


def test_model_response_log_warns_on_length(monkeypatch):
    from nucllei.utils import middleware

    logged = []

    class _Log:
        def info(self, line, *args):
            logged.append(("info", line))

        def warning(self, line, *args):
            logged.append(("warning", line))

        def debug(self, *args, **kwargs):
            pass

    monkeypatch.setattr(middleware, "log", _Log())
    middleware.log_model_response(
        {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": None, "reasoning_content": "x" * 100},
                }
            ],
            "usage": {"completion_tokens": 4000},
        },
        metadata={"chat_id": "c", "message_id": "m"},
        model="preset",
        call_index="title",
        elapsed=1.5,
    )
    assert len(logged) == 1
    level, line = logged[0]
    assert level == "warning"
    assert line.startswith(
        "model call chat_id=c message_id=m call=title model=preset "
        "finish_reason=length text_chars=0 reasoning_chars=100 tool_calls=0[] "
        "completion_tokens=4000 unknown_delta_keys=[] elapsed=1.50s"
    )
    assert "WARN: finish_reason=length" in line
    # 応答の本文は丸めて frames に付く
    assert "x" * 40 + "…" in line
    assert "x" * 41 not in line
