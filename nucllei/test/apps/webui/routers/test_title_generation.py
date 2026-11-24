"""タイトル生成 (`POST /api/v1/tasks/title/completions`) が上流へ送る body の回帰。

- preset の system prompt を当てない: 送るのは task の template と会話の文字だけ
- preset の base_model_id への差し替え・system 以外の params は当たる
- model の read ACL は今まで通り効く (ACL 外は 404、上流へは出ない)
- pipeline の inlet filter を通る
- 呼び出しごとに `model call ... call=title` の 1 行が出る (失敗した呼び出しも status /
  例外のクラスと elapsed 付きで 1 行)
- 生成に失敗しても (上流の 400・接続断・例外・content が空 / null・title が取れない)
  最初の発話がタイトルになり、失敗の原因を書いた WARNING が 1 行出る
  (middleware.resolve_response_title)
- 最初の発話から作るタイトルは、空白を詰めて先頭 50 文字 (異常終了のときと同じ)
"""

import asyncio
import copy
import json
import types

import aiohttp
import yarl

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
    headers = {"Content-Type": "application/json"}

    def __init__(self, body=None, status=200):
        self.status = status
        self._body = UPSTREAM_RESPONSE if body is None else body

    async def json(self):
        return json.loads(json.dumps(self._body))

    async def text(self):
        return json.dumps(self._body)

    def raise_for_status(self):
        if self.status >= 400:
            url = yarl.URL("http://upstream.invalid/v1/chat/completions")
            raise aiohttp.ClientResponseError(
                aiohttp.RequestInfo(url, "POST", {}, url),
                (),
                status=self.status,
                message="Bad Request",
            )

    def close(self):
        pass

    def release(self):
        pass


class TestTitleGenerationPayload(AbstractPostgresTest):
    BASE_PATH = "/api/v1/tasks"

    def _setup(
        self, monkeypatch, access_control, reply=None, reply_status=200, raises=None
    ):
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
                if raises is not None:
                    raise raises
                return _FakeJsonResponse(reply, reply_status)

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
            def __init__(self, name):
                self.name = name

            def info(self, line, *args, **kwargs):
                logged.append(("info", line))

            def warning(self, line, *args, exc_info=None, **kwargs):
                logged.append(("warning", line))
                if exc_info:
                    logged.append(("traceback", f"{self.name}: {exc_info!r}"))

            def error(self, line, *args, exc_info=None, **kwargs):
                logged.append(("error", f"{self.name}: {line}"))
                if exc_info:
                    logged.append(("traceback", f"{self.name}: {line}"))

            def exception(self, line, *args, **kwargs):
                logged.append(("exception", line))
                logged.append(("traceback", f"{self.name}: {line}"))

            def debug(self, *args, **kwargs):
                pass

        monkeypatch.setattr(middleware, "log", _Log("middleware"))
        monkeypatch.setattr(tasks_router, "log", _Log("tasks"))
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

    # ---- 生成に失敗したときの救済 (resolve_response_title) ----------------------

    def _resolve(
        self,
        monkeypatch,
        user_id="1",
        role="admin",
        tasks=None,
        first=MESSAGES[0]["content"],
    ):
        """middleware.resolve_response_title を実物の tasks.generate_title・
        routers.openai に通して呼ぶ。返り値はタイトル (付けないなら None)。"""
        from nucllei.main import app
        from nucllei.utils import middleware

        message_map = {
            "u1": {
                "id": "u1",
                "parentId": None,
                "role": "user",
                "content": first,
            },
            "a1": {
                "id": "a1",
                "parentId": "u1",
                "role": "assistant",
                "model": "preset",
                "content": MESSAGES[1]["content"],
            },
        }
        monkeypatch.setattr(
            middleware.Chats,
            "get_messages_by_chat_id",
            lambda chat_id: copy.deepcopy(message_map),
        )
        metadata = {"chat_id": "c1", "message_id": "a1"}
        request = types.SimpleNamespace(
            app=app, state=types.SimpleNamespace(metadata=metadata)
        )
        user = types.SimpleNamespace(
            id=user_id, email=f"{user_id}@nucllei.local", name="u", role=role
        )
        return asyncio.run(
            middleware.resolve_response_title(
                request,
                user,
                metadata,
                {"title_generation": True} if tasks is None else tasks,
            )
        )

    def test_title_is_generated(self, monkeypatch):
        sent, _, _ = self._setup(monkeypatch, access_control=None)
        assert self._resolve(monkeypatch) == "札幌の冬"
        assert len(sent) == 1

    @staticmethod
    def _call_lines(logged):
        return [(level, line) for level, line in logged if "call=title" in line]

    @staticmethod
    def _fallback_warnings(logged):
        return [
            line
            for level, line in logged
            if level == "warning" and "title generation failed" in line
        ]

    def test_upstream_400_falls_back_to_first_message(self, monkeypatch):
        sent, _, logged = self._setup(
            monkeypatch,
            access_control=None,
            reply={"object": "error", "message": "bad request"},
            reply_status=400,
        )
        assert self._resolve(monkeypatch) == MESSAGES[0]["content"]
        assert len(sent) == 1
        # 失敗した呼び出しも call=title の 1 行 (上流の status・原因の例外・elapsed)
        lines = self._call_lines(logged)
        assert len(lines) == 1
        level, line = lines[0]
        assert level == "warning"
        assert line.startswith(
            "model call chat_id=c1 message_id=a1 call=title model=preset "
            "status=400 exception=ClientResponseError elapsed="
        )
        assert line.endswith("s WARN: call failed")
        assert self._fallback_warnings(logged) == [
            "title generation failed (chat c1): status=400 "
            "exception=ClientResponseError; using the first message"
        ]
        # traceback は router (差し替えていない実物) の 1 本だけ — タイトル生成の側
        # (tasks・middleware) は出さない
        assert [line for level, line in logged if level == "traceback"] == []

    def test_connection_drop_is_told_apart_from_400(self, monkeypatch):
        sent, _, logged = self._setup(
            monkeypatch,
            access_control=None,
            raises=aiohttp.ServerDisconnectedError(),
        )
        assert self._resolve(monkeypatch) == MESSAGES[0]["content"]
        assert len(sent) == 1
        lines = self._call_lines(logged)
        assert len(lines) == 1
        level, line = lines[0]
        assert level == "warning"
        # 上流の応答が無いので status は書かない (router が付ける 500 は原因ではない)
        assert line.startswith(
            "model call chat_id=c1 message_id=a1 call=title model=preset "
            "exception=ServerDisconnectedError elapsed="
        )
        assert "status=" not in line
        assert self._fallback_warnings(logged) == [
            "title generation failed (chat c1): "
            "exception=ServerDisconnectedError; using the first message"
        ]
        assert [line for level, line in logged if level == "traceback"] == []

    def test_unexpected_exception_logs_its_class_and_traceback(self, monkeypatch):
        from nucllei.routers import tasks as tasks_router

        _, _, logged = self._setup(monkeypatch, access_control=None)

        async def broken(request, form_data, user):
            raise KeyError("choices")

        monkeypatch.setattr(tasks_router, "generate_chat_completion", broken)
        assert self._resolve(monkeypatch) == MESSAGES[0]["content"]
        lines = self._call_lines(logged)
        assert len(lines) == 1
        assert " exception=KeyError elapsed=" in lines[0][1]
        assert self._fallback_warnings(logged) == [
            "title generation failed (chat c1): exception=KeyError; "
            "using the first message"
        ]
        # router を通らない例外は traceback を call の行に 1 本だけ付ける
        tracebacks = [line for level, line in logged if level == "traceback"]
        assert len(tracebacks) == 1
        assert tracebacks[0].startswith("middleware: KeyError")

    def test_endpoint_returns_400_with_one_call_line_and_no_traceback(
        self, monkeypatch
    ):
        sent, _, logged = self._setup(
            monkeypatch,
            access_control=None,
            reply={"object": "error", "message": "bad request"},
            reply_status=400,
        )
        response = self._post("1", "admin")
        assert response.status_code == 400
        assert response.json() == {"detail": "An internal error has occurred."}
        assert len(sent) == 1
        lines = self._call_lines(logged)
        assert len(lines) == 1
        assert " status=400 exception=ClientResponseError " in lines[0][1]
        # generate_title は失敗を重ねて出さない (call の行だけ)
        assert [line for level, line in logged if level in ("error", "traceback")] == []

    def test_exception_falls_back_to_first_message(self, monkeypatch):
        # read ACL 外 = run_title_generation が HTTPException(404) を投げる (上流へは出ない)
        sent, _, logged = self._setup(monkeypatch, access_control={})
        assert self._resolve(monkeypatch, user_id="9", role="user") == (
            MESSAGES[0]["content"]
        )
        assert sent == []
        # モデルを呼んでいないので call の行は無い
        assert self._call_lines(logged) == []
        assert self._fallback_warnings(logged) == [
            "title generation failed (chat c1): status=404 exception=HTTPException; "
            "using the first message"
        ]

    def test_null_content_falls_back_to_first_message(self, monkeypatch):
        reply = copy.deepcopy(UPSTREAM_RESPONSE)
        reply["choices"][0]["finish_reason"] = "length"
        reply["choices"][0]["message"]["content"] = None
        _, _, logged = self._setup(monkeypatch, access_control=None, reply=reply)
        assert self._resolve(monkeypatch) == MESSAGES[0]["content"]
        assert self._fallback_warnings(logged) == [
            "title generation failed (chat c1): content=null finish_reason=length; "
            "using the first message"
        ]

    def test_empty_content_falls_back_to_first_message(self, monkeypatch):
        reply = copy.deepcopy(UPSTREAM_RESPONSE)
        reply["choices"][0]["message"]["content"] = ""
        _, _, logged = self._setup(monkeypatch, access_control=None, reply=reply)
        assert self._resolve(monkeypatch) == MESSAGES[0]["content"]
        assert self._fallback_warnings(logged) == [
            "title generation failed (chat c1): content=empty finish_reason=stop; "
            "using the first message"
        ]
        # 呼び出し自体は成功: call の行は INFO
        assert [level for level, _ in self._call_lines(logged)] == ["info"]

    def test_reply_without_title_falls_back_to_first_message(self, monkeypatch):
        reply = copy.deepcopy(UPSTREAM_RESPONSE)
        reply["choices"][0]["message"]["content"] = "札幌の冬"
        _, _, logged = self._setup(monkeypatch, access_control=None, reply=reply)
        assert self._resolve(monkeypatch) == MESSAGES[0]["content"]
        assert self._fallback_warnings(logged) == [
            "title generation failed (chat c1): content=no_title text_chars=4; "
            "using the first message"
        ]

    def test_fallback_title_is_cut_to_50_chars(self, monkeypatch):
        self._setup(
            monkeypatch,
            access_control=None,
            reply={"object": "error", "message": "bad request"},
            reply_status=400,
        )
        first = "  長い文を\n\n貼り付けた\t発言。" + "あ" * 200
        expected = ("長い文を 貼り付けた 発言。" + "あ" * 200)[:50]
        assert len(expected) == 50
        assert self._resolve(monkeypatch, first=first) == expected

    def test_auto_title_off_cuts_the_first_message_too(self, monkeypatch):
        self._setup(monkeypatch, access_control=None)
        title = self._resolve(
            monkeypatch, tasks={"title_generation": False}, first="い" * 80
        )
        assert title == "い" * 50

    def test_abnormal_end_uses_the_same_rule(self, monkeypatch):
        from nucllei.utils import middleware

        first = "  長い文を\n\n貼り付けた\t発言。" + "あ" * 200
        message_map = {
            "u1": {"id": "u1", "parentId": None, "role": "user", "content": first},
            "a1": {"id": "a1", "parentId": "u1", "role": "assistant", "content": ""},
        }
        monkeypatch.setattr(
            middleware.Chats, "get_messages_by_chat_id", lambda chat_id: message_map
        )
        saved = []
        monkeypatch.setattr(
            middleware.Chats,
            "update_chat_title_by_id",
            lambda chat_id, title: saved.append(title),
        )
        emitted = []

        async def emit(event):
            emitted.append(event)

        asyncio.run(
            middleware.apply_fallback_title_on_abnormal_end(
                {"chat_id": "c1", "message_id": "a1"},
                {"title_generation": True},
                emit,
            )
        )
        expected = middleware.first_message_title([{"role": "user", "content": first}])
        assert len(expected) == 50
        assert saved == [expected]
        assert emitted == [{"type": "chat:title", "data": expected}]

    def test_disabled_title_generation_falls_back_to_first_message(self, monkeypatch):
        from nucllei.main import app

        sent, _, logged = self._setup(monkeypatch, access_control=None)
        monkeypatch.setattr(app.state.config, "ENABLE_TITLE_GENERATION", False)
        assert self._resolve(monkeypatch) == MESSAGES[0]["content"]
        assert sent == []
        # 無効は失敗ではない
        assert self._fallback_warnings(logged) == []

    def test_auto_title_off_uses_first_message_without_calling(self, monkeypatch):
        sent, _, _ = self._setup(monkeypatch, access_control=None)
        assert self._resolve(monkeypatch, tasks={"title_generation": False}) == (
            MESSAGES[0]["content"]
        )
        assert sent == []

    def test_no_title_task_resolves_to_none(self, monkeypatch):
        sent, _, _ = self._setup(monkeypatch, access_control=None)
        assert self._resolve(monkeypatch, tasks={}) is None
        assert sent == []


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
