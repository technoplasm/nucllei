"""モデル呼び出しごとの診断ログ(consume_model_stream の終端 1 行 — refs #142)。

挙動は変えない観測のみのログ。正常なら INFO、異常の疑い(a〜d)なら WARNING に
上げて生 SSE の先頭/末尾フレームを添える。本文はログに出さない(長さだけ)、
生フレーム中の本文 delta は先頭 40 文字に丸める。
"""

import asyncio
import json
import logging
import types

from starlette.responses import StreamingResponse

from nucllei.utils import middleware
from nucllei.utils.response_assembler import ContentBlocksAssembler

LOGGER = middleware.log.name

SECRET = "患者山田太郎の電話番号は090-1234-5678で住所は札幌市北区北14条西5丁目です"


def _chunk(delta=None, finish_reason=None, usage=None):
    frame = {
        "id": "x",
        "object": "chat.completion.chunk",
        "model": "kimi-k2.6",
        "choices": (
            [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]
            if delta is not None or finish_reason is not None
            else []
        ),
    }
    if usage is not None:
        frame["usage"] = usage
    return frame


def _response(frames):
    async def gen():
        for frame in frames:
            yield "data: " + json.dumps(frame, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _run(monkeypatch, caplog, frames, *, tools=True, assembler=None):
    monkeypatch.setattr(
        middleware.Chats,
        "upsert_message_to_chat_by_id_and_message_id",
        lambda chat_id, message_id, data: None,
    )
    monkeypatch.setattr(
        middleware.Chats,
        "record_usage_by_id_and_message_id",
        lambda chat_id, message_id, usage, context_size=None: None,
    )

    async def event_emitter(event):
        pass

    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(MODELS={}))
    )
    metadata = {"chat_id": "chat-1", "message_id": "message-1"}
    if tools:
        metadata["tools"] = {"search_phone": {"spec": {"name": "search_phone"}}}
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        asyncio.run(
            middleware.consume_model_stream(
                request,
                _response(frames),
                assembler if assembler is not None else ContentBlocksAssembler(),
                model={"id": "kimi-k2.6"},
                metadata=metadata,
                event_emitter=event_emitter,
            )
        )
    records = [
        r
        for r in caplog.records
        if r.name == LOGGER and r.getMessage().startswith("model call ")
    ]
    assert len(records) == 1, [r.getMessage() for r in records]
    return records[0]


def _tool_call_delta(name, arguments, index=0, call_id="call_1"):
    return {
        "tool_calls": [
            {
                "index": index,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ]
    }


def test_normal_text_reply_logs_info(monkeypatch, caplog):
    record = _run(
        monkeypatch,
        caplog,
        [
            _chunk({"role": "assistant", "reasoning_content": "考える"}),
            _chunk({"content": SECRET}),
            _chunk({"content": "。"}),
            _chunk({}, finish_reason="stop"),
            _chunk(usage={"prompt_tokens": 10, "completion_tokens": 42}),
        ],
    )
    message = record.getMessage()
    assert record.levelno == logging.INFO
    assert "chat_id=chat-1 message_id=message-1 call=1 model=kimi-k2.6" in message
    assert "finish_reason=stop" in message
    assert f"text_chars={len(SECRET) + 1}" in message
    assert "reasoning_chars=3" in message
    assert "tool_calls=0[]" in message
    assert "completion_tokens=42" in message
    assert "unknown_delta_keys=[]" in message
    # 本文も生フレームも出ない
    assert "山田" not in message and "考える" not in message
    assert "head_frames" not in message and "WARN" not in message


def test_normal_tool_call_logs_info_with_names(monkeypatch, caplog):
    record = _run(
        monkeypatch,
        caplog,
        [
            _chunk({"role": "assistant", "reasoning_content": "電話帳を引く"}),
            _chunk(_tool_call_delta("search_phone", '{"q": "')),
            _chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'x"}'}}]}),
            _chunk({}, finish_reason="tool_calls"),
        ],
    )
    assert record.levelno == logging.INFO
    assert "tool_calls=1['search_phone']" in record.getMessage()


def test_call_index_counts_streams_in_the_turn(monkeypatch, caplog):
    assembler = ContentBlocksAssembler()
    frames = [_chunk({"content": "はい。"}), _chunk({}, finish_reason="stop")]
    _run(monkeypatch, caplog, frames, assembler=assembler)
    record = _run(monkeypatch, caplog, frames, assembler=assembler)
    assert " call=2 " in record.getMessage()


def test_unknown_delta_keys_are_named(monkeypatch, caplog):
    record = _run(
        monkeypatch,
        caplog,
        [
            _chunk(
                {
                    "content": "はい。",
                    "function_call": {"name": "search_phone"},
                    "provider_specific_fields": None,  # 値が空のキーは数えない
                    "refusal": None,
                }
            ),
            _chunk({}, finish_reason="stop"),
        ],
    )
    assert "unknown_delta_keys=['function_call']" in record.getMessage()


def _assert_warning(record, reason):
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "head_frames=" in message and "tail_frames=" in message
    assert message.rstrip().endswith(reason), message
    return message


def test_a_tool_calls_finish_without_tool_calls_warns(monkeypatch, caplog):
    record = _run(
        monkeypatch,
        caplog,
        [
            _chunk({"reasoning_content": "x"}),
            _chunk({"content": "了解しました。"}),
            _chunk({}, finish_reason="tool_calls"),
        ],
    )
    _assert_warning(record, "finish_reason=tool_calls but no tool_calls")


def test_b_missing_finish_reason_warns(monkeypatch, caplog):
    record = _run(
        monkeypatch,
        caplog,
        [_chunk({"reasoning_content": "x"}), _chunk({"content": "了解しました。"})],
    )
    message = _assert_warning(record, "no finish_reason")
    assert "finish_reason=None" in message


def test_c_length_finish_warns(monkeypatch, caplog):
    record = _run(
        monkeypatch,
        caplog,
        [
            _chunk({"reasoning_content": "x"}),
            _chunk({"content": "長い"}),
            _chunk({}, finish_reason="length"),
        ],
    )
    _assert_warning(record, "finish_reason=length")


def test_d_short_text_only_reply_cut_mid_sentence_warns(monkeypatch, caplog):
    """今回の症状: tool 付きリクエストで英語の思考だけが本文に来て途中で終わる。"""
    record = _run(
        monkeypatch,
        caplog,
        [
            _chunk({"role": "assistant", "content": ""}),
            _chunk({"content": "The user is asking for the phone number of "}),
            _chunk({"content": "someone. This requires the phone directory"}),
            _chunk({}, finish_reason="stop"),
        ],
    )
    message = _assert_warning(record, "short text-only reply cut mid-sentence")
    # 生フレームは本文 delta が 40 文字に丸められている
    first = "The user is asking for the phone number of "
    assert first[:40] + "…" in message
    assert first not in message
    assert "someone. This requires the phone directory" not in message
    assert "data: [DONE]" in message


def test_d_does_not_fire_without_tools_or_on_complete_sentence(monkeypatch, caplog):
    cut = [_chunk({"content": "The user is asking for"}), _chunk({}, "stop")]
    assert _run(monkeypatch, caplog, cut, tools=False).levelno == logging.INFO
    done = [_chunk({"content": "わかりません。"}), _chunk({}, "stop")]
    assert _run(monkeypatch, caplog, done).levelno == logging.INFO


def test_warning_raw_frames_keep_head_and_tail_three_redacted(monkeypatch, caplog):
    frames = [_chunk({"role": "assistant", "content": ""})]
    frames += [_chunk({"content": f"{i}:{SECRET}"}) for i in range(10)]
    frames += [_chunk(_tool_call_delta("search_phone", '{"q": "' + SECRET + '"}'))]
    frames += [_chunk({}, finish_reason="length")]
    record = _run(monkeypatch, caplog, frames)
    message = _assert_warning(record, "finish_reason=length")

    head = json.loads(message.split("head_frames=", 1)[1].split(" tail_frames=")[0])
    tail = json.loads(message.split("tail_frames=", 1)[1].split(" WARN: ")[0])
    assert len(head) == 3 and len(tail) == 3
    assert '"content": "0:' in head[1] and '"content": "1:' in head[2]
    assert "search_phone" in tail[0] and tail[2] == "data: [DONE]"
    for frame in head + tail:
        assert len(frame) <= 501
        assert SECRET not in frame
    assert f'"content": "{("1:" + SECRET)[:40]}…"' in head[2]
    assert "frames=14 " in message
