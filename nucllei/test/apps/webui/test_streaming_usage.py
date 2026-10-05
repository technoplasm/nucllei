"""ストリーミング中のライブ usage(chat:usage — issue #102)の回帰テスト。

SGLang の stream_options.continuous_usage_stats は choices を持つ全チャンクに
累積 usage を載せる(実機確認済み)。consume_model_stream はこれを
USAGE_EMIT_INTERVAL の leading-edge throttle で揮発イベント chat:usage として
流す。永続化(_record_usage → DB)は 1 ストリームにつき 1 回で、その emit には
context_size が同梱される。

フレームは実キャプチャ形式(SGLang × Kimi K2.6 — _memos/model-frames.md)に
合わせて合成する: role フレーム → content* → finish → usage フレーム
(choices: [])→ [DONE]。continuous では choices を持つチャンクにも累積
usage が載る。

usage フレームの形は配備依存である点も併せて固定する — proxy(LiteLLM)越しでは
usage が `choices: []` ではなく choices 非空(delta: {})のチャンクに載って届く。
永続化はフレームの形ではなくストリーム終端で保証する(_litellm_frames)。
"""

import asyncio
import json
import types

from starlette.responses import StreamingResponse

from test.util import model_frames as mf

from nucllei.utils import middleware
from nucllei.utils.response_assembler import ContentBlocksAssembler


def _usage(prompt, completion, reasoning=None):
    """実キャプチャ形の累積 usage(prompt_tokens_details: null を含む)。"""
    usage = {
        "prompt_tokens": prompt,
        "total_tokens": prompt + completion,
        "completion_tokens": completion,
        "prompt_tokens_details": None,
    }
    if reasoning is not None:
        usage["reasoning_tokens"] = reasoning
    return usage


def _chunk(delta=None, finish_reason=None, usage=None):
    """実キャプチャ形式のチャンク。delta も finish_reason も無ければ choices: []。"""
    frame = {
        "id": "245d5cac931d43c0b0f53300679d17e0",
        "object": "chat.completion.chunk",
        "created": 1783217380,
        "model": "kimi-k2.6",
        "choices": (
            [
                {
                    "index": 0,
                    "delta": delta or {},
                    "logprobs": None,
                    "finish_reason": finish_reason,
                    "matched_stop": None,
                }
            ]
            if delta is not None or finish_reason is not None
            else []
        ),
    }
    if usage is not None:
        frame["usage"] = usage
    return frame


def _continuous_frames():
    """continuous_usage_stats 有効時の応答(累積 usage が全チャンクに載る)。"""
    return [
        _chunk(
            delta={"reasoning_content": None, "role": "assistant", "content": ""},
            usage=_usage(28, 1, reasoning=1),
        ),
        _chunk(delta={"content": "こんにちは"}, usage=_usage(28, 2, reasoning=1)),
        _chunk(delta={"content": "!"}, usage=_usage(28, 3, reasoning=1)),
        _chunk(
            delta={"reasoning_content": None},
            finish_reason="stop",
            usage=_usage(28, 4, reasoning=1),
        ),
        _chunk(usage=_usage(28, 4, reasoning=1)),  # 最終 usage フレーム(choices: [])
    ]


def _litellm_frames():
    """LiteLLM proxy 越しの応答(実キャプチャ形式 — v1.82.3 / 2026-08-14)。

    SGLang 直との差は 3 点:
    - continuous_usage_stats が通らない(チャンクに累積 usage が載らない)
    - **usage が `choices: []` ではなく choices 非空(delta: {})のチャンクに載る**
      — finish_reason チャンクの次、[DONE] の直前
    - reasoning は completion_tokens_details.reasoning_tokens に入れ子で入る
      (SGLang 拡張の top-level reasoning_tokens は来ない)
    """
    usage = {
        "completion_tokens": 101,
        "prompt_tokens": 26,
        "total_tokens": 127,
        "completion_tokens_details": {"reasoning_tokens": 94, "text_tokens": 7},
    }
    return [
        {
            "id": "e99915b96b7a4b68a8a42b4a5f0113b1",
            "object": "chat.completion.chunk",
            "created": 1786683185,
            "model": "kimi-k2.6",
            "choices": [{"index": 0, "delta": delta, **extra}],
            **({"usage": usage} if with_usage else {}),
        }
        for delta, extra, with_usage in [
            ({"reasoning_content": "ユーザー", "role": "assistant"}, {}, False),
            ({"content": "こん"}, {}, False),
            ({"content": "にちは!"}, {}, False),
            ({}, {"finish_reason": "stop"}, False),
            ({}, {}, True),  # usage チャンク(choices 非空・finish_reason 無し)
        ]
    ]


def _sse_response_from_frames(frames):
    async def gen():
        for frame in frames:
            yield "data: " + json.dumps(frame, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse_response_from_fixture(name):
    """実キャプチャ SSE(include_usage のみ — continuous なし)を行単位で流す。"""
    raw = mf.load_raw(name).decode("utf-8")

    async def gen():
        for line in raw.split("\n"):
            yield line + "\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _consume(monkeypatch, response, model=None, assembler=None):
    """consume_model_stream を単体で駆動し、(イベント列, DB record 呼び出し列) を返す。

    assembler を渡すと同じ assembler で続けて駆動できる(tool ループ相当)。
    """
    events = []
    record_calls = []

    monkeypatch.setattr(
        middleware.Chats,
        "upsert_message_to_chat_by_id_and_message_id",
        lambda chat_id, message_id, data: None,
    )
    monkeypatch.setattr(
        middleware.Chats,
        "record_usage_by_id_and_message_id",
        lambda chat_id, message_id, usage, context_size=None: record_calls.append(
            {"usage": usage, "context_size": context_size}
        ),
    )

    async def event_emitter(event):
        events.append(event)

    request = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(MODELS={}))
    )
    await middleware.consume_model_stream(
        request,
        response,
        assembler if assembler is not None else ContentBlocksAssembler(),
        model=(
            model
            if model is not None
            else {"id": "kimi-k2.6", "openai": {"max_model_len": 262144}}
        ),
        metadata={"chat_id": "chat-1", "message_id": "message-1"},
        filter_functions=[],
        extra_params={},
        event_emitter=event_emitter,
    )
    return events, record_calls


def _usage_events(events):
    return [e["data"] for e in events if e.get("type") == "chat:usage"]


def _completion_usage_events(events):
    return [
        e["data"]["usage"]
        for e in events
        if e.get("type") == "chat:completion" and "usage" in e.get("data", {})
    ]


def test_live_usage_is_throttled_leading_edge(monkeypatch):
    """瞬時に届くチャンク列では最初の 1 発だけが emit される(leading edge)。

    tail flush は無い — 最終真値は chat:completion {usage} が無条件で届く。
    """
    events, _ = asyncio.run(
        _consume(monkeypatch, _sse_response_from_frames(_continuous_frames()))
    )

    assert _usage_events(events) == [
        {
            "prompt_tokens": 28,
            "completion_tokens": 1,
            "total_tokens": 29,
            "reasoning_tokens": 1,
            "context_size": 262144,
        }
    ]


def test_live_usage_emits_again_after_interval(monkeypatch):
    """throttle は間隔経過で再度通す(間隔 0 なら全チャンクが emit される)。"""
    monkeypatch.setattr(middleware, "USAGE_EMIT_INTERVAL", 0.0)
    events, _ = asyncio.run(
        _consume(monkeypatch, _sse_response_from_frames(_continuous_frames()))
    )

    assert [u["completion_tokens"] for u in _usage_events(events)] == [1, 2, 3, 4]


def test_only_final_usage_frame_is_recorded(monkeypatch):
    """DB 永続化(_record_usage)は最終 usage フレーム(choices: [])の 1 回のみ。

    ライブ usage(チャンクに載る累積値)は揮発イベントで流すだけで DB に書かない。
    emit される chat:completion {usage} には DB と同じ context_size が同梱される。
    """
    events, record_calls = asyncio.run(
        _consume(monkeypatch, _sse_response_from_frames(_continuous_frames()))
    )

    # carried_tokens(次ターンへ持ち越す量)= prompt + (completion − reasoning)
    # 呼び出しごとの記録は推定(carried_source=estimated)— 実測への差し替えは
    # ターン末の計測リクエスト(TestMeasuredCarriedTokens)
    final_usage = {
        **_usage(28, 4, reasoning=1),
        "carried_tokens": 28 + (4 - 1),
        "carried_source": "estimated",
    }
    assert len(record_calls) == 1
    # 生成所要時間(#102)は backend 実測 — 値は環境依存なのでキーの存在と型だけ固定し、
    # DB へ渡る usage と emit される usage で同じ値であることを見る
    recorded_duration = record_calls[0]["usage"].pop("duration")
    assert isinstance(recorded_duration, float) and recorded_duration >= 0
    assert record_calls == [{"usage": final_usage, "context_size": 262144}]

    emitted = _completion_usage_events(events)
    assert len(emitted) == 1
    assert emitted[0].pop("duration") == recorded_duration
    assert emitted == [{**final_usage, "context_size": 262144}]


def test_usage_on_non_empty_choices_frame_is_recorded(monkeypatch):
    """proxy 越し(usage が choices 非空のチャンクに載る)でも永続化される。

    ここが落ちると message.usage が空のままになり、FE の所要時間・平均 tok/s・
    IN/OUT が丸ごと出なくなる(LiteLLM 経由で実際に起きた)。
    """
    events, record_calls = asyncio.run(
        _consume(monkeypatch, _sse_response_from_frames(_litellm_frames()))
    )

    assert len(record_calls) == 1
    duration = record_calls[0]["usage"].pop("duration")
    assert isinstance(duration, float) and duration >= 0
    assert record_calls[0]["usage"] == {
        "completion_tokens": 101,
        "prompt_tokens": 26,
        "total_tokens": 127,
        "completion_tokens_details": {"reasoning_tokens": 94, "text_tokens": 7},
        # reasoning は入れ子(completion_tokens_details)からも読む: 26 + (101 − 94)
        "carried_tokens": 33,
        "carried_source": "estimated",
    }
    assert record_calls[0]["context_size"] == 262144

    emitted = _completion_usage_events(events)
    assert len(emitted) == 1
    assert emitted[0]["completion_tokens"] == 101


def test_usage_is_recorded_once_when_final_frame_follows(monkeypatch):
    """終端の後始末は二重記録しない — 最終フレーム(choices: [])が来た形が正。

    continuous(全チャンクに累積 usage)+ 最終フレーム、という SGLang の形で
    記録が 1 回に留まることを固定する。throttle は無効化して全チャンクを
    ライブ経路に通す(pending_usage が毎回更新される最悪条件)。
    """
    monkeypatch.setattr(middleware, "USAGE_EMIT_INTERVAL", 0.0)
    _, record_calls = asyncio.run(
        _consume(monkeypatch, _sse_response_from_frames(_continuous_frames()))
    )

    assert len(record_calls) == 1
    assert record_calls[0]["usage"]["completion_tokens"] == 4


def test_context_size_from_litellm_max_input_tokens(monkeypatch):
    """LiteLLM(1.96+)の /v1/models が載せる max_input_tokens も実測値として読む。

    config.yaml の model_info.max_input_tokens がそのまま出るキー。SGLang の
    max_model_len と同格に扱うので手動設定は要らない。
    """
    _, record_calls = asyncio.run(
        _consume(
            monkeypatch,
            _sse_response_from_frames(_litellm_frames()),
            model={"id": "kimi-k2.6", "openai": {"max_input_tokens": 262144}},
        )
    )

    assert record_calls[0]["context_size"] == 262144


def test_context_size_falls_back_to_model_meta(monkeypatch):
    """max_model_len を返さない endpoint 向けに meta.context_size を手で設定できる。"""
    _, record_calls = asyncio.run(
        _consume(
            monkeypatch,
            _sse_response_from_frames(_litellm_frames()),
            model={"id": "kimi-k2.6", "info": {"meta": {"context_size": 262144}}},
        )
    )

    assert record_calls[0]["context_size"] == 262144


def test_max_model_len_wins_over_model_meta(monkeypatch):
    """実測値(max_model_len)が取れるならそちらが真 — 手動値は stale になりうる。"""
    _, record_calls = asyncio.run(
        _consume(
            monkeypatch,
            _sse_response_from_frames(_litellm_frames()),
            model={
                "id": "kimi-k2.6",
                "openai": {"max_model_len": 262144},
                "info": {"meta": {"context_size": 8192}},
            },
        )
    )

    assert record_calls[0]["context_size"] == 262144


def test_no_live_usage_without_continuous_chunks(monkeypatch):
    """continuous なし(実キャプチャ text_basic)では chat:usage は流れない。

    最終 usage フレームの永続化 + chat:completion {usage} は従来どおり効く。
    """
    events, record_calls = asyncio.run(
        _consume(monkeypatch, _sse_response_from_fixture("text_basic"))
    )

    assert _usage_events(events) == []
    assert len(record_calls) == 1
    assert record_calls[0]["usage"]["completion_tokens"] == 66
    assert record_calls[0]["context_size"] == 262144


def test_live_usage_payload_omits_unresolved_optional_keys(monkeypatch):
    """reasoning_tokens はチャンク usage にあるときのみ、context_size は解決できた
    ときのみ載せる(契約外キー prompt_tokens_details は落とす)。"""
    frames = [
        _chunk(
            delta={"role": "assistant", "content": "2"},
            usage=_usage(22, 1),
        ),
        _chunk(delta={}, finish_reason="stop", usage=_usage(22, 2)),
        _chunk(usage=_usage(22, 2)),
    ]
    # max_model_len を持たないモデル(base も無し)→ context_size は解決できない
    events, record_calls = asyncio.run(
        _consume(monkeypatch, _sse_response_from_frames(frames), model={"id": "m"})
    )

    assert _usage_events(events) == [
        {"prompt_tokens": 22, "completion_tokens": 1, "total_tokens": 23}
    ]
    # 最終 usage も context_size キーを載せない(None 同梱はしない)。
    # duration(#102、backend 実測)は最終 usage にだけ載る — ライブ側には無い。
    # ctx 不明時の emit は record と同一 dict を共有するので pop は record 側で 1 回
    duration = record_calls[0]["usage"].pop("duration")
    assert isinstance(duration, float) and duration >= 0
    # reasoning 未報告・出力は text のみ → 出力を丸ごと再送扱い: 22 + 2
    expected = {
        **_usage(22, 2),
        "carried_tokens": 24,
        "carried_source": "estimated",
    }
    assert record_calls == [{"usage": expected, "context_size": None}]
    emitted = _completion_usage_events(events)
    assert len(emitted) == 1
    emitted[0].pop("duration", None)
    assert emitted == [expected]


class TestCarriedTokens:
    """次ターンへ持ち越すコンテキスト量 carried_tokens(ken 指摘 2026-10-03)。

    ゲージが最後の呼び出しの prompt_tokens を出すと、ターンの出力分だけ次ターンの
    先頭で跳ねる。確定時に「次の呼び出しの prompt」を見積もって message.usage に載せる:
        carried = prompt + (completion − reasoning)   … reasoning は再送されない
    reasoning_tokens はバックエンド報告(top-level / completion_tokens_details)を使い、
    無ければこの呼び出しの出力ブロックの文字数比で推定する。
    """

    def test_reported_reasoning_tokens(self):
        usage = _usage(100, 40, reasoning=30)
        assert middleware.carried_tokens_from_usage(usage, None) == 110

    def test_nested_reasoning_details(self):
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 35},
        }
        # ブロックは無視される(報告があれば推定しない)
        blocks = [{"type": "reasoning", "content": "x" * 1000}]
        assert middleware.carried_tokens_from_usage(usage, blocks) == 105

    def test_estimated_from_output_chars(self):
        usage = _usage(100, 40)
        blocks = [
            {"type": "reasoning", "content": "r" * 60},
            {"type": "text", "content": "t" * 15},
            {
                "type": "tool_calls",
                "content": [
                    {
                        "id": "functions.f:0",
                        "function": {"name": "f", "arguments": "{}"},
                        "render": {"ignored": True},
                    }
                ],
            },
        ]
        # 再送 15 + (1 + 2) = 18 文字、全体 78 文字 → round(40 × 18 / 78) = 9
        assert middleware.carried_tokens_from_usage(usage, blocks) == 109

    def test_estimate_without_blocks_resends_whole_completion(self):
        assert middleware.carried_tokens_from_usage(_usage(100, 40), []) == 140
        assert middleware.carried_tokens_from_usage(_usage(100, 40), None) == 140

    def test_clamped_to_completion_range(self):
        # reasoning > completion(壊れた報告)でも prompt を下回らない
        assert (
            middleware.carried_tokens_from_usage(_usage(100, 40, reasoning=50)) == 100
        )
        # 負の reasoning も completion 以上には膨らまない
        assert (
            middleware.carried_tokens_from_usage(_usage(100, 40, reasoning=-5)) == 140
        )

    def test_absent_prompt_or_completion_yields_none(self):
        assert middleware.carried_tokens_from_usage({"prompt_tokens": 5}, []) is None
        assert (
            middleware.carried_tokens_from_usage({"completion_tokens": 5}, []) is None
        )
        assert middleware.carried_tokens_from_usage({}, []) is None

    def test_absent_completion_records_no_field(self, monkeypatch):
        """completion の無い usage フレームでは carried_tokens を載せない。"""
        frames = [
            _chunk(delta={"role": "assistant", "content": "2"}),
            _chunk(delta={}, finish_reason="stop"),
            _chunk(usage={"prompt_tokens": 22}),
        ]
        _, record_calls = asyncio.run(
            _consume(monkeypatch, _sse_response_from_frames(frames))
        )
        assert len(record_calls) == 1
        assert "carried_tokens" not in record_calls[0]["usage"]

    def test_estimate_uses_only_this_streams_blocks_in_tool_loop(self, monkeypatch):
        """tool ループでは最後の呼び出しだけで見積もる。

        1 回目(tool_call fixture — reasoning 報告あり)の出力は 2 回目の prompt に
        含まれているので、2 回目(reasoning 未報告)の推定に 1 回目の reasoning 文字が
        混ざってはいけない(assembler.stream_blocks のスコープ)。
        """
        assembler = ContentBlocksAssembler()
        _, first = asyncio.run(
            _consume(
                monkeypatch,
                _sse_response_from_fixture("tool_call"),
                assembler=assembler,
            )
        )
        # tool_call fixture の最終 usage: prompt 141 / completion 886 / reasoning 719
        assert first[0]["usage"]["carried_tokens"] == 141 + (886 - 719)

        # tool ループ側の操作(実行結果を綴じて次の text ブロックを開く)
        calls, _ = assembler.finish_stream()
        assembler.ensure_tool_calls_block(calls)
        assembler.set_tool_results(
            [{"tool_call_id": call["id"], "content": "晴れ"} for call in calls]
        )

        frames = [
            _chunk(delta={"role": "assistant", "content": ""}),
            _chunk(delta={"reasoning_content": "r" * 30}),
            _chunk(delta={"content": "t" * 10}),
            _chunk(delta={}, finish_reason="stop"),
            _chunk(usage=_usage(1200, 40)),  # reasoning 未報告
        ]
        _, second = asyncio.run(
            _consume(
                monkeypatch, _sse_response_from_frames(frames), assembler=assembler
            )
        )
        # この呼び出しの出力だけで推定: round(40 × 10 / 40) = 10 → 1200 + 10。
        # ターン累積の completion(886 + 40)は使わない。
        assert second[0]["usage"]["carried_tokens"] == 1210

    def test_carried_rides_non_streaming_response(self, monkeypatch):
        """非ストリーム JSON 応答(ingest_model_response_json)でも載る。"""
        record_calls = []
        monkeypatch.setattr(
            middleware.Chats,
            "upsert_message_to_chat_by_id_and_message_id",
            lambda chat_id, message_id, data: None,
        )
        monkeypatch.setattr(
            middleware.Chats,
            "record_usage_by_id_and_message_id",
            lambda chat_id, message_id, usage, context_size=None: record_calls.append(
                usage
            ),
        )

        async def event_emitter(event):
            pass

        request = types.SimpleNamespace(
            app=types.SimpleNamespace(state=types.SimpleNamespace(MODELS={}))
        )
        asyncio.run(
            middleware.ingest_model_response_json(
                request,
                {
                    "choices": [
                        {
                            "message": {
                                "content": "t" * 10,
                                "reasoning_content": "r" * 30,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": _usage(50, 40),
                },
                ContentBlocksAssembler(),
                model={"id": "m"},
                metadata={"chat_id": "chat-1", "message_id": "message-1"},
                event_emitter=event_emitter,
            )
        )
        assert record_calls[0]["carried_tokens"] == 60


class TestContinuousUsageAutoOptOut:
    """continuous_usage_stats を弾く上流への自動対応(routers/openai.py — 2026-08-16)。

    LiteLLM 経由の OpenAI/Anthropic 系は未知の stream_options キーを 400 で拒否する。
    egress は初回の 4xx を観測したら剥がして 1 回リトライし、(url, model) を
    プロセス内で学習して以後送らない。SGLang 系(受理する上流)は従来どおり。
    """

    def setup_method(self):
        from nucllei.routers import openai as openai_router

        openai_router._continuous_usage_rejected.clear()

    def test_strip_removes_only_the_sglang_extension(self):
        from nucllei.routers.openai import strip_continuous_usage_stats

        payload = {
            "model": "gpt-5.6-luna",
            "stream_options": {"include_usage": True, "continuous_usage_stats": True},
        }
        assert strip_continuous_usage_stats(payload) is True
        # 標準の include_usage は残す(usage 最終チャンクは OpenAI 標準)
        assert payload["stream_options"] == {"include_usage": True}
        # 冪等: 既に無ければ False
        assert strip_continuous_usage_stats(payload) is False

    def test_strip_drops_empty_stream_options(self):
        from nucllei.routers.openai import strip_continuous_usage_stats

        payload = {"stream_options": {"continuous_usage_stats": True}}
        assert strip_continuous_usage_stats(payload) is True
        assert "stream_options" not in payload

    def test_strip_is_a_noop_without_stream_options(self):
        from nucllei.routers.openai import strip_continuous_usage_stats

        payload = {"model": "kimi-k2.6"}
        assert strip_continuous_usage_stats(payload) is False

    def test_rejection_predicate_requires_4xx_and_key_mention(self):
        from nucllei.routers.openai import is_continuous_usage_rejection

        litellm_body = (
            "litellm.BadRequestError: OpenAIException - Unknown parameter: "
            "'stream_options.continuous_usage_stats'. Received Model Group=gpt-5.6-luna"
        )
        assert is_continuous_usage_rejection(400, litellm_body) is True
        assert is_continuous_usage_rejection(422, litellm_body) is True
        # 5xx(上流障害)や無関係の 4xx で機能を落とさない
        assert is_continuous_usage_rejection(500, litellm_body) is False
        assert is_continuous_usage_rejection(400, "invalid api key") is False
        assert is_continuous_usage_rejection(400, "") is False
        assert is_continuous_usage_rejection(400, None) is False
