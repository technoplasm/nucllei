"""native function calling 一本化(issue #18 / #35)の回帰テスト。

- 非 native(プロンプトベース)tool calling 経路は削除済み。ツールが指定されたら
  function_calling パラメータに関係なく常に native 経路(payload の "tools")で
  モデルへ渡ることを固定する。
- ツール呼び出しの反復上限は CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS
  (既定 30 / -1 で無制限)。上限到達時はチャット内エラーイベントを発行する。

反復ループのテストは issue #29 の実キャプチャ fixture(SGLang × Kimi K2.6)を
リプレイして行う。
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from starlette.responses import StreamingResponse

from test.util import model_frames as mf
from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user


####################################
# 反復上限の env パース(既定 30 / -1 無制限 / 不正値は既定)
####################################


# open_webui パッケージを含む backend ディレクトリ(サブプロセスの import パス用)。
# CI では cwd や sys.path が異なり `from open_webui...` が解決できないため明示する。
import open_webui

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(open_webui.__file__)))


def _parse_iterations_env(value):
    """サブプロセスで open_webui.env を import し直してパース結果を得る。

    env.py は import 時に値が確定するため、プロセス内での再 import では
    テストできない(他モジュールが古い値を保持する)。
    """
    code = (
        "from open_webui.env import CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS as v;"
        "print('ITERATIONS=%d' % v)"
    )
    env = {**os.environ}
    env["PYTHONPATH"] = os.pathsep.join(
        [_BACKEND_DIR, *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    if value is None:
        env.pop("CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS", None)
    else:
        env["CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS"] = value
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=_BACKEND_DIR,
        check=True,
    )
    lines = [l for l in result.stdout.splitlines() if l.startswith("ITERATIONS=")]
    assert lines, result.stdout
    return int(lines[-1][len("ITERATIONS=") :])


def test_max_tool_call_iterations_default_is_30():
    assert _parse_iterations_env(None) == 30


def test_max_tool_call_iterations_minus_one_means_unlimited():
    assert _parse_iterations_env("-1") == -1


def test_max_tool_call_iterations_invalid_falls_back_to_default():
    assert _parse_iterations_env("not-a-number") == 30


####################################
# native ツール呼び出しループ(fixture リプレイ)
####################################


def _sse_response(name: str) -> StreamingResponse:
    """実キャプチャ SSE を行単位で流す StreamingResponse。"""
    raw = mf.load_raw(name).decode("utf-8")

    async def gen():
        for line in raw.split("\n"):
            yield line + "\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _drive_tool_call_loop(
    monkeypatch,
    cap,
    followup_responses,
    initial_fixture="tool_call",
    extra_tools=None,
):
    """process_chat_response をツール呼び出し SSE で駆動する。

    followup_responses: ツール実行後の継続リクエストへの応答(fixture 名)の列。
    尽きた後は常に tool_call を返す(上限テスト用)。
    initial_fixture: 初回応答に使う fixture 名(引数なし/特殊文字引数の検証で差し替える)。
    extra_tools: metadata["tools"] に追加登録するツール(get_current_time など)。
    戻り値: (emit されたイベント列, 継続リクエスト数, ツール実行時の引数列)
    """
    from open_webui.utils import middleware

    events = []
    completion_calls = []
    tool_invocations = []
    followups = list(followup_responses)

    def fake_get_event_emitter(_metadata):
        async def emit(event):
            events.append(event)

        return emit

    async def fake_generate_chat_completion(request, form_data, user):
        completion_calls.append(form_data)
        name = followups.pop(0) if followups else "tool_call"
        return _sse_response(name)

    if cap is not None:
        monkeypatch.setattr(middleware, "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS", cap)
    monkeypatch.setattr(middleware, "get_event_emitter", fake_get_event_emitter)
    monkeypatch.setattr(middleware, "get_event_call", fake_get_event_emitter)
    monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda model: [])
    monkeypatch.setattr(
        middleware, "get_active_status_by_user_id", lambda user_id: "active"
    )
    monkeypatch.setattr(
        middleware, "generate_chat_completion", fake_generate_chat_completion
    )

    # DB 非依存にする(チャット保存はこのテストの対象外)
    monkeypatch.setattr(
        middleware.Chats,
        "get_message_by_id_and_message_id",
        lambda chat_id, message_id: None,
    )
    monkeypatch.setattr(
        middleware.Chats,
        "upsert_message_to_chat_by_id_and_message_id",
        lambda chat_id, message_id, data: None,
    )
    monkeypatch.setattr(
        middleware.Chats, "get_chat_title_by_id", lambda chat_id: "chat"
    )
    monkeypatch.setattr(
        middleware.Chats, "get_messages_by_chat_id", lambda chat_id: None
    )

    background = {}

    def fake_create_task(coro, id=None):
        task = asyncio.get_event_loop().create_task(coro)
        background["task"] = task
        return id, task

    monkeypatch.setattr(middleware, "create_task", fake_create_task)

    async def get_weather(**kwargs):
        tool_invocations.append(kwargs)
        return "晴れ"

    weather_spec = {
        "name": "get_weather",
        "description": "天気を返す",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "detail": {"type": "string"},
            },
            "required": ["city"],
        },
    }

    tools = {"get_weather": {"spec": weather_spec, "callable": get_weather}}
    if extra_tools:
        tools.update(extra_tools)
    metadata = {
        "chat_id": "chat-1",
        "message_id": "message-1",
        "session_id": "session-1",
        "features": {},
        "tools": tools,
    }
    form_data = {
        "model": "kimi-k2.6",
        "stream": True,
        "messages": [{"role": "user", "content": "札幌の天気は?"}],
        "tools": [{"type": "function", "function": weather_spec}],
    }
    user = types.SimpleNamespace(
        id="1", email="john.doe@openwebui.com", name="John Doe", role="admin"
    )
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                WEBUI_NAME="llens",
                config=types.SimpleNamespace(WEBUI_URL="http://localhost"),
            )
        )
    )
    model = {"id": "kimi-k2.6", "name": "kimi-k2.6"}

    result = await middleware.process_chat_response(
        request,
        _sse_response(initial_fixture),
        form_data,
        user,
        metadata,
        model,
        [],
        {},
    )
    assert result.get("status") is True
    await background["task"]
    return events, completion_calls, tool_invocations


def _iteration_limit_errors(events):
    return [
        e["data"]["error"]["content"]
        for e in events
        if e.get("type") == "chat:completion"
        and isinstance(e.get("data", {}).get("error"), dict)
        and "iteration limit" in e["data"]["error"].get("content", "")
    ]


def test_native_tool_loop_executes_tool_and_completes(monkeypatch):
    """tool_call → ツール実行 → 継続 → 通常応答で正常終了する(native 経路)。"""
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=None, followup_responses=["text_basic"])
    )

    # 実キャプチャの引数がパースされてツールに渡っている
    assert len(tool_invocations) == 1
    assert tool_invocations[0]["city"] == "札幌"

    # 継続リクエストは 1 回(その応答が通常応答なのでループ終了)
    assert len(completion_calls) == 1
    assert completion_calls[0]["tools"], "継続リクエストにも tools が付く"

    # エラーは出ず、done イベントで完了する
    assert _iteration_limit_errors(events) == []
    assert any(
        e.get("type") == "chat:completion" and e.get("data", {}).get("done")
        for e in events
    )


def test_native_tool_loop_parses_no_args_call(monkeypatch):
    """引数なし tool call(tool_call_no_args): arguments が空 {} でツールが実行される。

    実キャプチャでは Kimi は arguments を "{}" で流す(引数なしツールでも空文字では
    ない)。middleware は空 {} をそのままツールへ渡す(properties が空なので許可引数も空)。
    """
    invocations = []

    async def get_current_time(**kwargs):
        invocations.append(kwargs)
        return "12:00"

    clock_spec = {
        "name": "get_current_time",
        "description": "現在時刻を返す。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }
    extra = {"get_current_time": {"spec": clock_spec, "callable": get_current_time}}

    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_no_args",
            extra_tools=extra,
        )
    )

    # 引数なしでツールが 1 回実行され、渡る引数は空 dict
    assert len(invocations) == 1
    assert invocations[0] == {}

    # 継続リクエストは 1 回、エラーなく完了
    assert len(completion_calls) == 1
    assert _iteration_limit_errors(events) == []
    assert any(
        e.get("type") == "chat:completion" and e.get("data", {}).get("done")
        for e in events
    )


def test_native_tool_loop_parses_unicode_args_call(monkeypatch):
    """特殊文字入り引数(tool_call_unicode_args)が dict に正しくパースされる。

    実キャプチャでは Kimi は arguments を厳密 JSON(引用符 \\"、改行 \\n、
    バックスラッシュ \\\\ を正しくエスケープ、絵文字/日本語は生 UTF-8)で流す。
    middleware は ast.literal_eval を先に試すが、これらのエスケープは Python の
    文字列リテラル構文でも有効なため literal_eval で成功し、json.loads
    フォールバックには落ちない。ここではパース結果の中身を固定する。
    """
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_unicode_args",
        )
    )

    assert len(tool_invocations) == 1
    args = tool_invocations[0]
    assert args["city"] == "札幌"
    # detail には引用符・改行・バックスラッシュ・絵文字が復元されている
    detail = args["detail"]
    assert '"寒い"' in detail  # 二重引用符
    assert "\n" in detail  # 実際の改行文字
    assert "\\" in detail  # バックスラッシュ
    assert "☃" in detail  # 絵文字

    assert len(completion_calls) == 1
    assert _iteration_limit_errors(events) == []


####################################
# multi-turn 履歴の綴じ込み(フォローアップ request の messages を検査)
####################################


def _assistant_tool_calls_message(followup_form_data):
    """継続リクエストの messages から tool_calls を持つ assistant メッセージを返す。"""
    for m in followup_form_data["messages"]:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return m
    return None


def _tool_messages(followup_form_data):
    return [m for m in followup_form_data["messages"] if m.get("role") == "tool"]


def test_followup_preserves_tool_call_history(monkeypatch):
    """継続リクエストに assistant(tool_calls)+ 対応する role:tool 結果が綴じ込まれる。

    tool 結果を text へ平坦化せず、tool_call_id で正しくリンクすることを固定する
    (これが崩れるとモデルが tool 出力を幻覚する)。
    """
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=None, followup_responses=["text_basic"])
    )
    assert len(completion_calls) == 1
    msgs = completion_calls[0]["messages"]

    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None, "assistant(tool_calls) が履歴に無い"
    tc = assistant["tool_calls"][0]
    assert tc["id"] == "functions.get_weather:0"
    assert tc["function"]["name"] == "get_weather"
    assert tc["function"].get("arguments")

    tool_msgs = _tool_messages(completion_calls[0])
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "functions.get_weather:0"
    # assistant の直後に対応する tool メッセージが来る(順序)
    assert msgs.index(tool_msgs[0]) == msgs.index(assistant) + 1


def test_followup_preserves_parallel_tool_calls(monkeypatch):
    """並列 tool call(tool_call_parallel): 1 assistant に 2 要素の tool_calls 配列 +
    id 対応の tool メッセージ 2 本が綴じ込まれる。"""
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_parallel",
        )
    )
    assert len(completion_calls) == 1

    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None
    ids = [tc["id"] for tc in assistant["tool_calls"]]
    assert ids == ["functions.get_weather:0", "functions.get_weather:1"]

    tool_msgs = _tool_messages(completion_calls[0])
    assert [m["tool_call_id"] for m in tool_msgs] == [
        "functions.get_weather:0",
        "functions.get_weather:1",
    ]
    # 2 都市それぞれツールが実行されている
    assert len(tool_invocations) == 2


def test_reasoning_not_leaked_into_followup_content(monkeypatch):
    """reasoning はモデルへの継続リクエストの assistant content に漏らさない(#18-2)。

    reasoning_then_tool は reasoning + 実応答 text + tool call を含む。継続リクエストの
    assistant メッセージ content には実応答 text だけが載り、UI 用の
    <details type="reasoning"> マークアップは含まれないこと。ただし UI への emit
    (chat:completion content イベント)には reasoning が残っていること。
    """
    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="reasoning_then_tool",
        )
    )
    assert len(completion_calls) == 1

    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None
    # モデル向け履歴に reasoning の UI マークアップが漏れていない
    assert '<details type="reasoning"' not in (assistant.get("content") or "")

    # UI への emit には reasoning が残っている(表示は維持)
    emitted = "".join(
        e.get("data", {}).get("content", "")
        for e in events
        if e.get("type") == "chat:completion"
        and isinstance(e.get("data", {}).get("content"), str)
    )
    assert '<details type="reasoning"' in emitted


def test_open_reasoning_not_leaked_into_followup_content(monkeypatch):
    """reasoning が閉じないケース(tool_call_no_args: content デルタが来ず reasoning
    が done=false のまま)でも、継続リクエストの assistant content に漏れない。"""

    async def get_current_time(**kwargs):
        return "12:00"

    clock_spec = {
        "name": "get_current_time",
        "description": "現在時刻を返す。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }
    extra = {"get_current_time": {"spec": clock_spec, "callable": get_current_time}}

    events, completion_calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_fixture="tool_call_no_args",
            extra_tools=extra,
        )
    )
    assert len(completion_calls) == 1
    assistant = _assistant_tool_calls_message(completion_calls[0])
    assert assistant is not None
    content = assistant.get("content") or ""
    assert '<details type="reasoning"' not in content


def test_tool_call_iteration_cap_emits_chat_error(monkeypatch):
    """上限到達時に黙って止まらず chat:completion エラーを emit する。"""
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=2, followup_responses=[])
    )

    # 上限(2)まで反復して停止
    assert len(completion_calls) == 2
    assert len(tool_invocations) == 2

    errors = _iteration_limit_errors(events)
    assert len(errors) == 1
    assert "(2)" in errors[0]
    assert "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS" in errors[0]


def test_tool_call_iteration_cap_minus_one_is_unlimited(monkeypatch):
    """-1 は無制限: 既定の旧上限(10)を超えても継続し、正常終了する。"""
    followups = ["tool_call"] * 12 + ["text_basic"]
    events, completion_calls, tool_invocations = asyncio.run(
        _drive_tool_call_loop(monkeypatch, cap=-1, followup_responses=followups)
    )

    # 初回 + 12 回の tool_call をすべて処理し切っている
    assert len(tool_invocations) == 13
    assert len(completion_calls) == 13
    assert _iteration_limit_errors(events) == []
    assert any(
        e.get("type") == "chat:completion" and e.get("data", {}).get("done")
        for e in events
    )


####################################
# ツールは常に native 経路でモデルへ届く(HTTP 統合)
####################################


class MockOpenAIHandler(BaseHTTPRequestHandler):
    """最小の OpenAI 互換サーバ(/v1/models, /v1/chat/completions)。"""

    def log_message(self, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "mock-model",
                            "object": "model",
                            "created": 0,
                            "owned_by": "mock",
                        }
                    ],
                }
            )
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.rstrip("/") == "/v1/chat/completions":
            self.server.received.append(payload)
            self._json(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )
        else:
            self._json({"error": "not found"}, 404)


CLOCK_TOOL_CONTENT = '''
class Tools:
    def get_current_time(self) -> str:
        """現在時刻を返す。"""
        return "12:00"
'''

CLOCK_TOOL_SPEC = {
    "name": "get_current_time",
    "description": "現在時刻を返す。",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


class TestToolsAlwaysNative(AbstractPostgresTest):
    BASE_PATH = "/api"

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.mock_server = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAIHandler)
        cls.mock_server.received = []
        cls.server_thread = threading.Thread(
            target=cls.mock_server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.mock_server.server_address[1]}/v1"

        from open_webui.main import app

        app.state.config.ENABLE_OPENAI_API = True
        app.state.config.ENABLE_OLLAMA_API = False
        app.state.config.OPENAI_API_BASE_URLS = [cls.base_url]
        app.state.config.OPENAI_API_KEYS = ["sk-dummy"]
        app.state.config.OPENAI_API_CONFIGS = {}

    @classmethod
    def teardown_class(cls):
        cls.mock_server.shutdown()
        cls.mock_server.server_close()
        super().teardown_class()

    def setup_method(self):
        super().setup_method()
        from open_webui.main import app

        app.state.MODELS = {}
        app.state.OPENAI_MODELS = {}
        app.state.TOOLS = {}
        self.mock_server.received.clear()

    def _register_clock_tool(self):
        from open_webui.models.tools import ToolForm, ToolMeta, Tools

        Tools.insert_new_tool(
            user_id="1",
            form_data=ToolForm(
                id="clock",
                name="clock",
                content=CLOCK_TOOL_CONTENT,
                meta=ToolMeta(description=None, manifest={}),
            ),
            specs=[dict(CLOCK_TOOL_SPEC)],
        )

    def test_tool_ids_flow_to_model_as_native_tools(self):
        """function_calling 指定なしでも tools がモデルへの payload に載る。

        旧実装の既定(非 native)ではタスクモデルへの function_calling
        プロンプト呼び出しが挟まり、payload に tools は載らなかった。
        """
        self._register_clock_tool()

        with mock_webui_user(id="1", role="admin"):
            response = self.fast_api_client.post(
                "/api/chat/completions",
                json={
                    "model": "mock-model",
                    "messages": [{"role": "user", "content": "何時?"}],
                    "stream": False,
                    "tool_ids": ["clock"],
                },
            )
        assert response.status_code == 200, response.text

        # 余計なプロンプトベース function_calling 呼び出しが無い(1 リクエストのみ)
        assert len(self.mock_server.received) == 1
        sent = self.mock_server.received[0]

        # tool spec が native 形式でそのまま届く
        assert "tools" in sent, sent.keys()
        assert [t["type"] for t in sent["tools"]] == ["function"]
        assert sent["tools"][0]["function"]["name"] == "get_current_time"

        # 旧経路のプロンプト注入("Available Tools:")が消えている
        for message in sent["messages"]:
            assert "Available Tools:" not in (message.get("content") or "")
