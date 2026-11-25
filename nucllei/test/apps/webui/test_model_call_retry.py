"""モデル呼び出しのやり直し(接続系の失敗)と、tool ループがエラーで終わった後の履歴。

- やり直すのは接続系の失敗(接続できない・接続の確立の timeout・接続断・上流の 502/503・
  接続断を伝える上流の 500)で、出力をまだ受け取っていないときだけ。4xx と、応答を待ち
  切れなかった失敗(読み取りの timeout・上流の 504・timeout を伝える上流の 500)は
  やり直さない
- 追撃が落ちて終わった応答の content_blocks から組む次ターンの履歴で、tool_call と結果が
  対になる(結果の無い tool_call を上流へ出さない)
"""

import asyncio
import logging

import aiohttp
import pytest
import yarl
from multidict import CIMultiDict, CIMultiDictProxy
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from nucllei.utils import middleware
from nucllei.utils.content_blocks import convert_content_blocks_to_messages
from nucllei.utils.middleware import (
    ModelStreamInterrupted,
    is_retryable_model_call_error,
    run_model_call_with_retry,
)
from test.apps.webui.test_native_tool_calling import (
    _drive_tool_call_loop,
    _sse_response,
)


def _chained(outer, inner):
    """`except inner: raise outer` と同じ連鎖(router が上流の失敗を包む形)。"""
    try:
        try:
            raise inner
        except BaseException:
            raise outer
    except BaseException as e:
        return e


def _upstream_status(status, detail):
    """router が上流の HTTP エラーを包んだ HTTPException(raise_for_status の連鎖)。"""
    url = yarl.URL("http://upstream/v1/chat/completions")
    inner = aiohttp.ClientResponseError(
        request_info=aiohttp.RequestInfo(
            url, "POST", CIMultiDictProxy(CIMultiDict()), url
        ),
        history=(),
        status=status,
        message="upstream",
    )
    return _chained(HTTPException(status_code=status, detail=detail), inner)


def _connect_timeout():
    """接続の確立の timeout(aiohttp が connect の timeout で投げる形)。"""
    return aiohttp.ConnectionTimeoutError(
        "Connection timeout to host http://upstream/v1/chat/completions"
    )


def _read_timeout():
    """読み取りの timeout(AIOHTTP_CLIENT_TIMEOUT_SOCK_READ の間、何も届かなかった)。"""
    return aiohttp.SocketTimeoutError("Timeout on reading data from socket")


def _no_response(exc):
    """上流の応答が無い失敗(router は 500 + Server Connection Error で包む)。"""
    return _chained(
        HTTPException(status_code=500, detail="Nucllei: Server Connection Error"), exc
    )


####################################
# やり直しの条件
####################################


@pytest.mark.parametrize(
    "exc",
    [
        _no_response(aiohttp.ServerDisconnectedError()),
        _no_response(aiohttp.ClientConnectionError("Connection closed")),
        _no_response(_connect_timeout()),
        _upstream_status(
            500,
            "litellm.APIConnectionError: APIConnectionError: "
            "OpenrouterException - Connection closed.",
        ),
        _upstream_status(502, "Bad Gateway"),
        _upstream_status(503, "Service Unavailable"),
        ModelStreamInterrupted(
            aiohttp.ClientPayloadError("Response payload is not completed"),
            output_started=False,
        ),
    ],
)
def test_connection_errors_are_retried(exc):
    assert is_retryable_model_call_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        _upstream_status(400, "messages: tool_call without response"),
        _upstream_status(401, "Unauthorized"),
        _upstream_status(404, "Model not found"),
        _upstream_status(429, "Rate limit exceeded"),
        _upstream_status(500, "CUDA out of memory"),
        HTTPException(status_code=403, detail="Model not found"),
        ModelStreamInterrupted(
            aiohttp.ClientPayloadError("Response payload is not completed"),
            output_started=True,
        ),
        ValueError("broken"),
        Exception("Model not found"),
    ],
)
def test_request_errors_and_streams_with_output_are_not_retried(exc):
    assert not is_retryable_model_call_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        # 応答ヘッダを待ち切れなかった(router が 500 + Server Connection Error で包む)
        _no_response(_read_timeout()),
        # 全体の timeout(AIOHTTP_CLIENT_TIMEOUT)
        _no_response(asyncio.TimeoutError()),
        # 本文の続きを待ち切れなかった(まだ何も受け取っていなくても)
        ModelStreamInterrupted(_read_timeout(), output_started=False),
        # 上流がその先の応答を待ち切れなかった
        _upstream_status(504, "Gateway Timeout"),
        _upstream_status(500, "litellm.Timeout: Request timed out"),
        _upstream_status(
            500, "litellm.APIConnectionError: APIConnectionError: Connection timed out"
        ),
    ],
)
def test_timeouts_waiting_for_the_response_are_not_retried(exc):
    """応答を待ち切れなかった失敗はやり直さない — 上流は処理していた可能性があり、
    やり直しても同じ長い待ちを繰り返すだけになる。"""
    assert not is_retryable_model_call_error(exc)


class _Recorder(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []
        self.records = []

    def emit(self, record):
        self.lines.append(record.getMessage())
        self.records.append(record)


@pytest.fixture
def model_call_recorder(monkeypatch):
    monkeypatch.setattr(middleware, "MODEL_CALL_RETRIES", 2)
    monkeypatch.setattr(middleware, "MODEL_CALL_RETRY_DELAY_S", 0)
    recorder = _Recorder()
    middleware.log.addHandler(recorder)
    try:
        yield recorder
    finally:
        middleware.log.removeHandler(recorder)


@pytest.fixture
def model_call_lines(model_call_recorder):
    return model_call_recorder.lines


def _model_call_records(recorder):
    return [r for r in recorder.records if r.getMessage().startswith("model call ")]


def _retry(attempt):
    return asyncio.run(
        run_model_call_with_retry(
            attempt,
            metadata={"chat_id": "c", "message_id": "m"},
            model={"id": "glm"},
            call_index=3,
        )
    )


def test_retry_recovers_and_logs_one_line_per_retry(model_call_lines):
    calls = []

    async def attempt():
        calls.append(1)
        if len(calls) < 3:
            raise _no_response(aiohttp.ServerDisconnectedError())
        return "ok"

    assert _retry(attempt) == "ok"
    assert len(calls) == 3
    retries = [line for line in model_call_lines if "WARN: retry" in line]
    assert len(retries) == 2
    assert retries[0].startswith("model call chat_id=c message_id=m call=3 model=glm ")
    assert "exception=ServerDisconnectedError" in retries[0]
    assert retries[0].endswith("WARN: retry 1/2 in 0s")
    assert retries[1].endswith("WARN: retry 2/2 in 0s")


def test_retry_gives_up_after_the_configured_count(model_call_lines):
    calls = []

    async def attempt():
        calls.append(1)
        raise _upstream_status(503, "Service Unavailable")

    with pytest.raises(HTTPException):
        _retry(attempt)
    assert len(calls) == 3
    assert model_call_lines[-1].endswith("WARN: call failed after 2 retries")
    assert "status=503" in model_call_lines[-1]


def test_400_is_raised_without_retry(model_call_lines):
    calls = []

    async def attempt():
        calls.append(1)
        raise _upstream_status(400, "bad request")

    with pytest.raises(HTTPException):
        _retry(attempt)
    assert len(calls) == 1
    assert len(model_call_lines) == 1
    assert model_call_lines[0].startswith(
        "model call chat_id=c message_id=m call=3 model=glm status=400 "
    )
    assert model_call_lines[0].endswith("WARN: call failed")


@pytest.mark.parametrize(
    "make_exc, exception_name",
    [
        (lambda: _no_response(_read_timeout()), "SocketTimeoutError"),
        (
            lambda: ModelStreamInterrupted(_read_timeout(), output_started=False),
            "SocketTimeoutError",
        ),
        (lambda: _upstream_status(504, "Gateway Timeout"), "ClientResponseError"),
    ],
)
def test_read_timeout_is_raised_without_retry_and_logs_call_failed(
    model_call_recorder, make_exc, exception_name
):
    """待ち切れなかった失敗は 1 回で諦め、`… WARN: call failed` を 1 行だけ出す
    (traceback は付けない — 呼び出し側か router が出す)。"""
    calls = []

    async def attempt():
        calls.append(1)
        raise make_exc()

    with pytest.raises(Exception):
        _retry(attempt)
    assert len(calls) == 1
    records = _model_call_records(model_call_recorder)
    assert len(records) == 1
    line = records[0].getMessage()
    assert line.startswith("model call chat_id=c message_id=m call=3 model=glm ")
    assert f"exception={exception_name}" in line
    assert line.endswith("WARN: call failed")
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None


def test_connect_timeout_is_retried(model_call_lines):
    calls = []

    async def attempt():
        calls.append(1)
        if len(calls) < 2:
            raise _no_response(_connect_timeout())
        return "ok"

    assert _retry(attempt) == "ok"
    assert len(calls) == 2
    assert len(model_call_lines) == 1
    assert "exception=ConnectionTimeoutError" in model_call_lines[0]
    assert model_call_lines[0].endswith("WARN: retry 1/2 in 0s")


def test_retries_off_logs_call_failed(monkeypatch, model_call_lines):
    monkeypatch.setattr(middleware, "MODEL_CALL_RETRIES", 0)
    calls = []

    async def attempt():
        calls.append(1)
        raise _no_response(aiohttp.ServerDisconnectedError())

    with pytest.raises(HTTPException):
        _retry(attempt)
    assert len(calls) == 1
    assert len(model_call_lines) == 1
    assert model_call_lines[0].endswith("WARN: call failed")


def test_retry_delay_doubles(monkeypatch):
    monkeypatch.setattr(middleware, "MODEL_CALL_RETRIES", 3)
    monkeypatch.setattr(middleware, "MODEL_CALL_RETRY_DELAY_S", 2)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(middleware.asyncio, "sleep", fake_sleep)

    async def attempt():
        raise _no_response(_connect_timeout())

    with pytest.raises(HTTPException):
        _retry(attempt)
    assert sleeps == [2, 4, 8]


####################################
# tool ループの追撃・最初の応答
####################################


def _broken_stream(frames, exc):
    """frames を流してから接続が切れる SSE(aiohttp が本文の途中で投げる形)。"""

    async def gen():
        for frame in frames:
            yield frame + "\n"
        raise exc

    return StreamingResponse(gen(), media_type="text/event-stream")


_TEXT_FRAME = (
    'data: {"choices":[{"index":0,"delta":{"content":"途中まで"},'
    '"finish_reason":null}]}\n'
)


def _upstream(script):
    """fixture 名ごとに失敗を差し込む upstream(_drive_tool_call_loop 用)。"""

    async def upstream(request, form_data, user, name):
        action = script.get(name)
        if action is None:
            return _sse_response(name)
        return action()

    return upstream


def _errors(events):
    return [
        e["data"]["error"]["content"]
        for e in events
        if e.get("type") == "chat:completion"
        and isinstance(e.get("data", {}).get("error"), dict)
    ]


def _done(events):
    done = [
        e["data"]
        for e in events
        if e.get("type") == "chat:completion" and e.get("data", {}).get("done")
    ]
    assert len(done) == 1
    return done[0]


def _drop():
    raise _no_response(aiohttp.ServerDisconnectedError())


def test_followup_connection_drop_is_retried_with_the_same_body(
    monkeypatch, model_call_lines
):
    events, calls, tools = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["drop", "text_basic"],
            upstream=_upstream({"drop": _drop}),
        )
    )
    # calls は追撃だけ(初回は main.py が取った応答): 落ちた 1 回 + やり直し
    assert len(calls) == 2
    assert calls[0]["messages"] == calls[1]["messages"]
    assert len(tools) == 1
    assert _errors(events) == []
    assert _done(events)["content"]
    retries = [line for line in model_call_lines if "WARN: retry" in line]
    assert len(retries) == 1
    assert " call=2 " in retries[0]


def test_followup_400_is_not_retried(monkeypatch, model_call_lines):
    def bad_request():
        raise _upstream_status(400, "messages are malformed")

    events, calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["bad"],
            upstream=_upstream({"bad": bad_request}),
        )
    )
    assert len(calls) == 1
    assert len(_errors(events)) == 1
    assert "Follow-up model call after tool execution failed" in _errors(events)[0]
    assert not [line for line in model_call_lines if "WARN: retry" in line]


def test_followup_stream_cut_before_output_is_retried(monkeypatch, model_call_lines):
    def cut():
        return _broken_stream(
            [], aiohttp.ClientPayloadError("Response payload is not completed")
        )

    events, calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["cut", "text_basic"],
            upstream=_upstream({"cut": cut}),
        )
    )
    assert len(calls) == 2
    assert _errors(events) == []


def test_followup_read_timeout_is_not_retried(monkeypatch, model_call_lines):
    """追撃の応答を待ち切れなかったら(読み取りの timeout)、何も受け取っていなくても
    やり直さずエラーで終える。`model call … WARN: call failed` を 1 行出す。"""

    def stall():
        return _broken_stream([], _read_timeout())

    events, calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["stall", "text_basic"],
            upstream=_upstream({"stall": stall}),
        )
    )
    assert len(calls) == 1
    errors = _errors(events)
    assert len(errors) == 1
    assert "Timeout on reading data from socket" in errors[0]
    assert not [line for line in model_call_lines if "WARN: retry" in line]
    failed = [line for line in model_call_lines if line.endswith("WARN: call failed")]
    assert len(failed) == 1
    assert " call=2 " in failed[0]
    assert "exception=SocketTimeoutError" in failed[0]


def test_followup_stream_cut_after_output_is_not_retried(monkeypatch, model_call_lines):
    """出力を受け取った後に切れたらやり直さない(やり直すと出力が重なる)。受け取った分は
    残し、エラーで終える。"""

    def cut():
        return _broken_stream(
            [_TEXT_FRAME],
            aiohttp.ClientPayloadError("Response payload is not completed"),
        )

    events, calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["cut"],
            upstream=_upstream({"cut": cut}),
        )
    )
    assert len(calls) == 1
    errors = _errors(events)
    assert len(errors) == 1
    assert "Response payload is not completed" in errors[0]
    assert "途中まで" in _done(events)["content"]
    assert not [line for line in model_call_lines if "WARN: retry" in line]


def test_first_stream_cut_before_output_is_requested_again(
    monkeypatch, model_call_lines
):
    """最初の応答を読み始めて何も来ないうちに切れたら、同じ form_data で取り直す。"""
    events, calls, _ = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["text_basic"],
            initial_response=_broken_stream(
                [], aiohttp.ClientPayloadError("Response payload is not completed")
            ),
        )
    )
    # 取り直し 1 回(text_basic)で終わる — tool は呼ばれない
    assert len(calls) == 1
    assert calls[0]["messages"] == [{"role": "user", "content": "札幌の天気は?"}]
    assert _errors(events) == []
    assert _done(events)["content"]


####################################
# エラーで終わった応答の後の履歴
####################################


def test_history_after_failed_followup_pairs_tool_calls_with_results(
    monkeypatch, model_call_lines
):
    """追撃がやり直しを使い切って落ちても、実行済みの tool の結果は content_blocks に
    残り、次のターンの履歴では tool_call と結果が対になる。"""
    events, calls, tools = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["drop", "drop", "drop"],
            upstream=_upstream({"drop": _drop}),
        )
    )
    assert len(calls) == 3  # 追撃 3 回(1 + やり直し 2)
    assert len(tools) == 1
    assert len(_errors(events)) == 1

    blocks = _done(events)["content_blocks"]
    tool_blocks = [b for b in blocks if b["type"] == "tool_calls"]
    assert len(tool_blocks) == 1
    assert [r["tool_call_id"] for r in tool_blocks[0]["results"]] == [
        c["id"] for c in tool_blocks[0]["calls"]
    ]

    history = convert_content_blocks_to_messages(blocks)
    call_ids = [c["id"] for m in history for c in m.get("tool_calls") or []]
    result_ids = [m["tool_call_id"] for m in history if m["role"] == "tool"]
    assert call_ids and call_ids == result_ids
    assert history[-1]["role"] == "tool"
    assert "晴れ" in history[-1]["content"]


def test_history_after_cut_mid_tool_call_pairs_or_drops_the_call(
    monkeypatch, model_call_lines
):
    """追撃が tool call の引数の途中で切れたら、その call は実行されずに残る。次のターンの
    履歴では壊れた call を落とし、結果の無い tool_call を作らない。"""

    def cut():
        return _broken_stream(
            [
                'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
                '"id":"functions.get_weather:1","type":"function","function":'
                '{"name":"get_weather","arguments":"{\\"city\\": \\"函"}}]},'
                '"finish_reason":null}]}\n'
            ],
            aiohttp.ClientPayloadError("Response payload is not completed"),
        )

    events, calls, tools = asyncio.run(
        _drive_tool_call_loop(
            monkeypatch,
            cap=None,
            followup_responses=["cut"],
            upstream=_upstream({"cut": cut}),
        )
    )
    assert len(calls) == 1
    assert len(tools) == 1

    history = convert_content_blocks_to_messages(_done(events)["content_blocks"])
    call_ids = [c["id"] for m in history for c in m.get("tool_calls") or []]
    result_ids = [m["tool_call_id"] for m in history if m["role"] == "tool"]
    assert call_ids == result_ids
    assert "functions.get_weather:1" not in call_ids
