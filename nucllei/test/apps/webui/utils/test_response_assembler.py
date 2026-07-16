"""ContentBlocksAssembler の単体テスト(issue #23)。

デルタ → content_blocks の状態遷移と reasoning の閉じ規律
(_memos/rendering-design.md §3)を、middleware から切り離した粒度で固定する。
ストリーム全体の外形(イベント・永続化)は test_native_tool_calling.py の
リプレイテストが担う。
"""

from nucllei.utils.response_assembler import (
    CHANGED_REASONING,
    CHANGED_TEXT,
    CHANGED_TOOL_CALLS,
    MISMATCH_MISSING_TOOL_CALLS,
    MISMATCH_UNEXPECTED_FINISH,
    ContentBlocksAssembler,
)


def make_assembler(**kwargs):
    a = ContentBlocksAssembler(**kwargs)
    a.start_stream()
    return a


def tool_call_delta(index=0, id=None, name=None, arguments=None):
    call = {"index": index, "function": {}}
    if id is not None:
        call["id"] = id
    if name is not None:
        call["function"]["name"] = name
    if arguments is not None:
        call["function"]["arguments"] = arguments
    return {"tool_calls": [call]}


class TestTextDelta:
    def test_accumulates_into_text_block_and_content(self):
        a = make_assembler()
        assert a.handle_delta({"content": "こん"}) == {CHANGED_TEXT}
        assert a.handle_delta({"content": "にちは"}) == {CHANGED_TEXT}

        assert a.content == "こんにちは"
        assert a.content_blocks[-1] == {"type": "text", "content": "こんにちは"}

    def test_empty_delta_reports_no_change(self):
        a = make_assembler()
        assert a.handle_delta({}) == set()
        assert a.handle_delta({"content": ""}) == set()

    def test_initial_content_seeds_first_text_block(self):
        a = make_assembler(initial_content="続きから")
        a.handle_delta({"content": "書く"})
        assert a.content == "続きから書く"
        assert a.content_blocks[0]["content"] == "続きから書く"


class TestReasoningDiscipline:
    def test_reasoning_delta_opens_block(self):
        a = make_assembler()
        assert a.handle_delta({"reasoning_content": "うーん"}) == {CHANGED_REASONING}
        block = a.content_blocks[-1]
        assert block["type"] == "reasoning"
        assert block["content"] == "うーん"
        assert "ended_at" not in block

    def test_rule1_content_arrival_closes_reasoning(self):
        a = make_assembler()
        a.handle_delta({"reasoning_content": "考え中"})
        changed = a.handle_delta({"content": "答え"})

        assert changed == {CHANGED_TEXT}
        reasoning = a.content_blocks[-2]
        assert reasoning["type"] == "reasoning"
        assert "ended_at" in reasoning
        assert "duration" in reasoning
        assert a.content_blocks[-1] == {"type": "text", "content": "答え"}

    def test_rule2_tool_calls_delta_closes_reasoning(self):
        # Kimi は content を経ず tool_calls に直行する(fixture: tool_call_no_args)
        a = make_assembler()
        a.handle_delta({"reasoning_content": "考え中"})
        changed = a.handle_delta(tool_call_delta(id="c1", name="t", arguments=""))

        assert changed == {CHANGED_TOOL_CALLS}
        reasoning = next(b for b in a.content_blocks if b["type"] == "reasoning")
        assert "ended_at" in reasoning

    def test_rule3_stream_end_closes_reasoning(self):
        # 切断・エラー終端でも done=false の「Thinking…」を残さない
        # (fixture: aborted_client_cut)
        a = make_assembler()
        a.handle_delta({"reasoning_content": "考え中"})
        a.finish_stream()

        reasoning = next(b for b in a.content_blocks if b["type"] == "reasoning")
        assert "ended_at" in reasoning

    def test_reasoning_alias_field(self):
        # reasoning_content / reasoning の両デルタ形を受ける
        a = make_assembler()
        assert a.handle_delta({"reasoning": "別名"}) == {CHANGED_REASONING}
        assert a.content_blocks[-1]["content"] == "別名"


class TestToolCallAccumulation:
    def test_arguments_accumulate_across_chunks(self):
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="get_", arguments='{"a"'))
        a.handle_delta(tool_call_delta(name="time", arguments=": 1}"))

        calls, mismatch = a.finish_stream()
        a.note_finish_reason(None)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "get_time"
        assert calls[0]["function"]["arguments"] == '{"a": 1}'

    def test_parallel_calls_tracked_by_index(self):
        a = make_assembler()
        a.handle_delta(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "c0",
                        "function": {"name": "f0", "arguments": ""},
                    },
                    {
                        "index": 1,
                        "id": "c1",
                        "function": {"name": "f1", "arguments": ""},
                    },
                ]
            }
        )
        a.handle_delta({"tool_calls": [{"index": 1, "function": {"arguments": "{}"}}]})

        calls, _ = a.finish_stream()
        assert [c["id"] for c in calls] == ["c0", "c1"]
        assert calls[1]["function"]["arguments"] == "{}"

    def test_open_block_is_shared_object_and_not_duplicated(self):
        # llens(#20 ステップ5): ストリーム中の open ブロックは累積 calls と
        # 同一オブジェクト。tool ループ側の ensure は再追加しない。
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments=""))
        a.handle_delta(tool_call_delta(arguments="{}"))

        blocks = [b for b in a.content_blocks if b["type"] == "tool_calls"]
        assert len(blocks) == 1

        calls, _ = a.finish_stream()
        a.ensure_tool_calls_block(calls)
        blocks = [b for b in a.content_blocks if b["type"] == "tool_calls"]
        assert len(blocks) == 1
        assert blocks[0]["content"] is calls

    def test_text_after_tool_calls_opens_new_text_block(self):
        a = make_assembler()
        a.handle_delta({"content": "前置き"})
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        a.handle_delta({"content": "後段"})

        types = [b["type"] for b in a.content_blocks]
        assert types == ["text", "tool_calls", "text"]
        assert a.content_blocks[0]["content"] == "前置き"
        assert a.content_blocks[-1]["content"] == "後段"

    def test_set_tool_results_closes_block_and_opens_text(self):
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        calls, _ = a.finish_stream()

        a.ensure_tool_calls_block(calls)
        results = [{"tool_call_id": "c1", "content": "ok"}]
        a.set_tool_results(results)

        tool_block = next(b for b in a.content_blocks if b["type"] == "tool_calls")
        assert tool_block["results"] is results
        assert a.content_blocks[-1] == {"type": "text", "content": ""}


class TestRenderDescriptors:
    def test_render_attached_from_tools(self):
        tools = {"t": {"render": {"args": {"kind": "code"}}}}
        a = make_assembler(tools=tools)
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))

        calls, _ = a.finish_stream()
        assert calls[0]["render"] == {"args": {"kind": "code"}}

    def test_late_added_tool_resolves(self):
        # load_skill の tool 遅延取り込み: tools dict は参照共有で後から増える
        tools = {}
        a = make_assembler(tools=tools)
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments=""))
        tools["t"] = {"render": {"result": {"kind": "skill"}}}
        a.handle_delta(tool_call_delta(arguments="{}"))

        calls, _ = a.finish_stream()
        assert calls[0]["render"] == {"result": {"kind": "skill"}}

    def test_existing_render_not_overwritten(self):
        tools = {"t": {"render": {"kind": "b"}}}
        a = make_assembler(tools=tools)
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        calls, _ = a.finish_stream()
        calls[0]["render"] = {"kind": "manual"}

        a.ensure_tool_calls_block(calls)
        assert calls[0]["render"] == {"kind": "manual"}


class TestFinishStream:
    def test_missing_tool_calls_mismatch(self):
        a = make_assembler()
        a.note_finish_reason("tool_calls")
        calls, mismatch = a.finish_stream()
        assert calls == []
        assert mismatch == MISMATCH_MISSING_TOOL_CALLS

    def test_unexpected_finish_mismatch(self):
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        a.note_finish_reason("stop")
        _, mismatch = a.finish_stream()
        assert mismatch == MISMATCH_UNEXPECTED_FINISH

    def test_normal_tool_calls_no_mismatch(self):
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        a.note_finish_reason("tool_calls")
        calls, mismatch = a.finish_stream()
        assert len(calls) == 1
        assert mismatch is None

    def test_finish_reason_none_no_mismatch(self):
        # 切断(finish_reason 未達)で tool_calls が組めていても mismatch にしない
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        _, mismatch = a.finish_stream()
        assert mismatch is None

    def test_trailing_text_stripped_and_dropped(self):
        a = make_assembler()
        a.handle_delta({"content": "本文  \n"})
        a.finish_stream()
        assert a.content_blocks[-1] == {"type": "text", "content": "本文"}

        b = make_assembler()
        b.handle_delta({"content": "   \n"})
        b.finish_stream()
        # 空になった text は落ち、全滅時は空 text を 1 個残す
        assert b.content_blocks == [{"type": "text", "content": ""}]

    def test_start_stream_resets_per_stream_state(self):
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        a.note_finish_reason("tool_calls")
        calls1, _ = a.finish_stream()
        assert len(calls1) == 1

        a.start_stream()
        assert a.finish_reason is None
        calls2, mismatch = a.finish_stream()
        assert calls2 == []
        assert mismatch is None


class TestSeedBlocks:
    def test_seed_blocks_precede_initial_text(self):
        seed = [{"type": "tool_calls", "synthetic": True, "content": [], "results": []}]
        a = make_assembler(seed_blocks=seed)
        assert a.content_blocks[0]["synthetic"] is True
        assert a.content_blocks[1] == {"type": "text", "content": ""}
