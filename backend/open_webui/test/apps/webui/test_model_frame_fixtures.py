"""実キャプチャ fixture(SGLang × Kimi K2.6)の構造回帰テスト(issue #29)。

fixture を再キャプチャした際にフレーム仕様が変わっていないかを検知する。
観察された仕様: _memos/model-frames.md。ここが落ちたら仕様が変わったということ
なので、メモを更新した上で下流(#15/#17/#20/#21)への影響を確認する。
"""

import json

import pytest

from test.util import model_frames as mf

COMPLETE = [
    "text_basic",
    "reasoning",
    "tool_call",
    "tool_call_parallel",
    "tool_call_long_code",
    "reasoning_then_tool",
]


@pytest.mark.parametrize("name", COMPLETE)
def test_frame_sequence_structure(name):
    frames = mf.load_frames(name)
    assert mf.has_done(name)

    # 先頭は role フレーム
    first_delta, _ = next(mf.iter_deltas(frames))
    assert first_delta.get("role") == "assistant"
    assert first_delta.get("content") == ""

    # finish フレームがちょうど 1 回、stop か tool_calls
    finishes = [fin for _, fin in mf.iter_deltas(frames) if fin]
    assert len(finishes) == 1
    assert finishes[0] in ("stop", "tool_calls")

    # 末尾に usage フレーム(choices 空 + reasoning_tokens 拡張)
    usage_frames = [f for f in frames if not (f.get("choices") or [])]
    assert len(usage_frames) == 1
    usage = usage_frames[0]["usage"]
    assert usage["completion_tokens"] > 0
    assert "reasoning_tokens" in usage  # SGLang 拡張

    # Kimi K2.6 は常に reasoning を出す(reasoning parser 有効)
    assert any(d.get("reasoning_content") for d, _ in mf.iter_deltas(frames))
    # <think> タグは content に混入しない(parser がサーバ側で分離)
    content = "".join(d.get("content") or "" for d, _ in mf.iter_deltas(frames))
    assert "<think>" not in content


@pytest.mark.parametrize(
    "name", ["tool_call", "tool_call_parallel", "tool_call_long_code"]
)
def test_tool_call_frame_shape(name):
    frames = mf.load_frames(name)

    calls = {}  # index -> {"id": ..., "name": ..., "arguments": str}
    for delta, _ in mf.iter_deltas(frames):
        for tc in delta.get("tool_calls") or []:
            idx = tc["index"]
            entry = calls.setdefault(idx, {"id": None, "name": None, "arguments": ""})
            if tc.get("id"):
                # 呼び出しの最初のフレームだけ id と name を持つ
                entry["id"] = tc["id"]
                entry["name"] = tc["function"]["name"]
            entry["arguments"] += tc["function"].get("arguments") or ""

    assert calls, "tool call フレームが存在する"
    for idx, call in calls.items():
        # SGLang/Kimi の id 形式は functions.<name>:<index>(OpenAI の call_xxx ではない)
        assert call["id"] == f"functions.{call['name']}:{idx}"
        # 断片を連結すると 有効な JSON になる(チャンク境界はトークン任意位置)
        args = json.loads(call["arguments"])
        assert isinstance(args, dict)

    # finish_reason は tool_calls
    finishes = [fin for _, fin in mf.iter_deltas(frames) if fin]
    assert finishes == ["tool_calls"]


def test_parallel_tool_calls_are_sequential_by_index():
    frames = mf.load_frames("tool_call_parallel")
    seen_indices = []
    for delta, _ in mf.iter_deltas(frames):
        for tc in delta.get("tool_calls") or []:
            if not seen_indices or seen_indices[-1] != tc["index"]:
                seen_indices.append(tc["index"])
    # index 0 が完了してから index 1(interleave しない)
    assert seen_indices == [0, 1]

    # 非ストリーム側も 2 件の tool_calls
    response = mf.load_response("tool_call_parallel")
    assert len(response["choices"][0]["message"]["tool_calls"]) == 2


def test_aborted_capture_has_no_terminator():
    """クライアント切断キャプチャ: finish/usage/[DONE] が無い部分ストリーム。"""
    frames = mf.load_frames("aborted_client_cut")
    assert frames, "部分フレームは存在する"
    assert not mf.has_done("aborted_client_cut")
    assert all(fin is None for _, fin in mf.iter_deltas(frames))
    assert all(f.get("choices") for f in frames)  # usage フレームなし
