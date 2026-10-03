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

    def test_promotion_reasoning_only_stop_becomes_text(self):
        # 昇格規律: SGLang parser が最終回答を丸ごと reasoning_content に載せ
        # content 空のまま stop する Kimi 実挙動(tool 追撃で観測)。
        # 回答が思考折りたたみに綴じ込まれないよう本文へ昇格する。
        a = make_assembler()
        a.handle_delta({"reasoning_content": "秘密のパスワードは「じゃがいも」です。"})
        a.note_finish_reason("stop")
        a.finish_stream()

        assert [b["type"] for b in a.content_blocks] == ["text"]
        assert (
            a.content_blocks[0]["content"] == "秘密のパスワードは「じゃがいも」です。"
        )
        assert a.content == "秘密のパスワードは「じゃがいも」です。"

    def test_promotion_after_tool_results(self):
        # 追撃ストリームの実形: tool_calls(+results)→ 空 text → reasoning のみ
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        calls, _ = a.finish_stream()
        a.ensure_tool_calls_block(calls)
        a.set_tool_results([{"tool_call_id": "c1", "content": "ok"}])

        a.start_stream()
        a.handle_delta({"reasoning_content": "答えは 42 です。"})
        a.note_finish_reason("stop")
        a.finish_stream()

        assert [b["type"] for b in a.content_blocks] == ["text", "tool_calls", "text"]
        assert a.content_blocks[-1]["content"] == "答えは 42 です。"

    def test_no_promotion_when_text_present(self):
        # 本文があるなら末尾 reasoning は真正の思考 — 触らない
        a = make_assembler()
        a.handle_delta({"reasoning_content": "考え中"})
        a.handle_delta({"content": "本文"})
        a.handle_delta({"reasoning_content": "後思考"})
        a.note_finish_reason("stop")
        a.finish_stream()

        assert [b["type"] for b in a.content_blocks] == [
            "text",
            "reasoning",
            "text",
            "reasoning",
        ]

    def test_no_promotion_without_stop(self):
        # 切断(finish_reason 未達)・length 打ち切りの中途 reasoning は
        # 回答に見せない(閉じ規律③で thinking のまま確定)
        for finish_reason in (None, "length"):
            a = make_assembler()
            a.handle_delta({"reasoning_content": "考え中"})
            if finish_reason:
                a.note_finish_reason(finish_reason)
            a.finish_stream()

            reasoning = next(b for b in a.content_blocks if b["type"] == "reasoning")
            assert "ended_at" in reasoning

    def test_no_promotion_with_tool_calls(self):
        # reasoning → tool_calls 直行(通常形)は昇格対象外
        a = make_assembler()
        a.handle_delta({"reasoning_content": "考え中"})
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        a.note_finish_reason("tool_calls")
        a.finish_stream()

        assert any(b["type"] == "reasoning" for b in a.content_blocks)

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

    def test_first_delta_without_arguments_key_accumulates(self):
        # vLLM(qwen3.6-35b / vLLM 0.26、2026-08-18 実測)は初回デルタが
        # id + name のみで arguments キーを載せない
        # (SGLang は必ず arguments: "" を載せる — fixture の形)。
        # 正規化なしだと後続の += が KeyError → 引数なし tool call で
        # 実行され、follow-up 再送も上流 validation 400 になる。
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="get_weather"))
        a.handle_delta(tool_call_delta(arguments='{"city": "'))
        a.handle_delta(tool_call_delta(arguments='札幌"}'))

        a.note_finish_reason("tool_calls")
        calls, mismatch = a.finish_stream()
        assert mismatch is None
        assert calls[0]["function"]["name"] == "get_weather"
        assert calls[0]["function"]["arguments"] == '{"city": "札幌"}'

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

    def test_content_between_parallel_calls_keeps_single_block(self):
        # 並列 call の区切りに content デルタ(改行等)を挟むモデルでは、間に text
        # ブロックが開いて calls のブロックが末尾でなくなる。call ごとに同じ calls の
        # ブロックを積み増さない(実測: 3 call で 3 ブロック、先頭 2 つは results 空のまま
        # 永続化され、次ターンで [interrupted] 付きで再送されていた)。
        a = make_assembler()
        for index in range(3):
            a.handle_delta({"content": "\n\n"})
            a.handle_delta(tool_call_delta(index=index, id=f"c{index}", name="t"))
            a.handle_delta(tool_call_delta(index=index, arguments="{}"))

        calls, _ = a.finish_stream()
        a.ensure_tool_calls_block(calls)
        results = [{"tool_call_id": f"c{i}", "content": "ok"} for i in range(3)]
        a.set_tool_results(results)

        blocks = [b for b in a.content_blocks if b["type"] == "tool_calls"]
        assert len(blocks) == 1
        assert [c["id"] for c in blocks[0]["content"]] == ["c0", "c1", "c2"]
        assert blocks[0]["results"] is results
        # 区切りの空白 text は末尾 strip で落ち、calls の前の 1 個だけが残る
        assert [b["type"] for b in a.content_blocks] == ["text", "tool_calls", "text"]

    def test_ensure_moves_block_after_trailing_text(self):
        # calls の後に本文を出して終わるストリーム: 実行直前の ensure がブロックを
        # 末尾へ移す(重複を積まない)。結果は calls のブロックに綴じられ、本文は
        # その前に来る = 「content + tool_calls の 1 assistant メッセージ」の形。
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        a.handle_delta({"content": "後段"})

        calls, _ = a.finish_stream()
        a.ensure_tool_calls_block(calls)
        assert [b["type"] for b in a.content_blocks if b["type"] == "tool_calls"] == [
            "tool_calls"
        ]
        assert a.content_blocks[-1]["content"] is calls
        assert a.content_blocks[-2] == {"type": "text", "content": "後段"}

        results = [{"tool_call_id": "c1", "content": "ok"}]
        a.set_tool_results(results)
        assert a.content_blocks[-2]["results"] is results
        assert a.content_blocks[-1] == {"type": "text", "content": ""}

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


class TestLastFrame:
    """wire v2(SSE 一本化)のフレーム分類: 純追記はデルタ、開閉・tool_calls はスナップショット。"""

    def test_pure_text_append_is_not_structural(self):
        a = make_assembler()
        a.handle_delta({"content": "こん"})
        # 初期 text ブロックへの追記(ブロックは __init__ で存在済み)
        assert a.last_frame == {
            "structural": False,
            "appends": [{"block": "text", "content": "こん"}],
        }
        a.handle_delta({"content": "にちは"})
        assert a.last_frame["appends"] == [{"block": "text", "content": "にちは"}]

    def test_reasoning_open_is_structural(self):
        a = make_assembler()
        a.handle_delta({"reasoning_content": "うーん"})
        assert a.last_frame["structural"] is True
        assert a.last_frame["appends"] == []

    def test_reasoning_append_is_delta(self):
        a = make_assembler()
        a.handle_delta({"reasoning_content": "うーん"})
        a.handle_delta({"reasoning_content": "そうか"})
        assert a.last_frame == {
            "structural": False,
            "appends": [{"block": "reasoning", "content": "そうか"}],
        }

    def test_reasoning_close_by_content_is_structural(self):
        a = make_assembler()
        a.handle_delta({"reasoning_content": "考え中"})
        a.handle_delta({"content": "答え"})
        assert a.last_frame["structural"] is True

    def test_tool_calls_delta_is_structural(self):
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{"))
        assert a.last_frame["structural"] is True
        a.handle_delta(tool_call_delta(arguments='"x": 1}'))
        # 引数の続きも部分 JSON 突き合わせが要るためスナップショット一択
        assert a.last_frame["structural"] is True

    def test_text_after_tool_results_opens_block_structurally(self):
        a = make_assembler()
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        a.set_tool_results([{"tool_call_id": "c1", "content": "ok"}])
        a.start_stream()
        # set_tool_results が空 text ブロックを開いた直後の追記は既存末尾への追記
        a.handle_delta({"content": "続き"})
        assert a.last_frame == {
            "structural": False,
            "appends": [{"block": "text", "content": "続き"}],
        }

    def test_no_change_frame_resets_last_frame(self):
        a = make_assembler()
        a.handle_delta({"content": "x"})
        a.handle_delta({})
        assert a.last_frame == {"structural": False, "appends": []}


class TestStreamBlocks:
    """stream_blocks: 直近の start_stream 以降に積んだ出力だけを返す(carried_tokens
    の推定に使う — tool ループで以前の呼び出しの出力を混ぜない)。"""

    def test_first_stream_returns_all_output(self):
        a = make_assembler()
        a.handle_delta({"reasoning_content": "考える"})
        a.handle_delta({"content": "答え"})
        # 先頭は __init__ が置く空 text(追記先)— 文字数 0 なので推定に影響しない
        assert [(b["type"], b["content"]) for b in a.stream_blocks()] == [
            ("text", ""),
            ("reasoning", "考える"),
            ("text", "答え"),
        ]

    def test_second_stream_excludes_previous_calls_output(self):
        a = make_assembler()
        a.handle_delta({"reasoning_content": "前の思考"})
        a.handle_delta(tool_call_delta(id="c1", name="t", arguments="{}"))
        calls, _ = a.finish_stream()
        a.ensure_tool_calls_block(calls)
        a.set_tool_results([{"tool_call_id": "c1", "content": "ok"}])

        a.start_stream()
        a.handle_delta({"reasoning_content": "次の思考"})
        a.handle_delta({"content": "本文"})
        blocks = a.stream_blocks()
        # set_tool_results が開いた空 text(今回のストリームで追記される先)から始まる。
        # reasoning が開く前なので空 text が先頭に残る
        assert [(b["type"], b["content"]) for b in blocks] == [
            ("text", ""),
            ("reasoning", "次の思考"),
            ("text", "本文"),
        ]

    def test_resumed_text_block_prefix_is_excluded(self):
        # initial_content(resume)は前回の出力 — 今回の追記分だけを数える
        a = make_assembler(initial_content="既存")
        a.handle_delta({"content": "追記"})
        assert [(b["type"], b["content"]) for b in a.stream_blocks()] == [
            ("text", "追記")
        ]
        # 元のブロックは書き換えない(コピーを返す)
        assert a.content_blocks[-1]["content"] == "既存追記"
