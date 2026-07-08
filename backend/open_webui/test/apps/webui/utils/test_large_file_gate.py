"""注入 gate(builtin_large_file_gate、#52 プランB ③)の回帰。

合算 context 予算による「大きい順 eviction」と抽出失敗告知を固定する。DB 非依存
(Files.get_file_by_id を mock し、gate の inlet を直接叩く)。gate は fail-open が命綱
(inlet が例外を投げるとチャット全体が壊れる)なのでそこも固定する。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import open_webui.builtin_functions.large_file_gate as G


def _rec(content="", size=0, content_type="text/plain"):
    return SimpleNamespace(
        data={"content": content},
        meta={"size": size, "content_type": content_type},
    )


def _body(*ids):
    return {
        "files": [{"id": i, "name": i} for i in ids],
        "messages": [{"role": "user", "content": "hi"}],
    }


def _gate(max_total_tokens=100000):
    # tiktoken を無効化して tokens≈len(content)(chars_per_token_fallback=1.0)で決定的に。
    inst = G.Filter()
    inst.valves.max_total_tokens = max_total_tokens
    inst.valves.chars_per_token_fallback = 1.0
    return inst


def _run(inst, body, recs):
    # tiktoken を無効化 → tokens≈len(content)(fallback 係数 1.0)で決定的に。
    with patch.object(G, "tiktoken", None):
        with patch.object(G.Files, "get_file_by_id", side_effect=lambda fid: recs[fid]):
            return asyncio.run(inst.inlet(body))


def _system_notes(body):
    return [m for m in body["messages"] if m["role"] == "system"]


def test_under_budget_is_noop():
    """合算が予算内なら間引かない・system 告知も出さない。"""
    inst = _gate(max_total_tokens=100)
    recs = {"a": _rec("x" * 10, 999), "b": _rec("y" * 20, 999)}
    out = _run(inst, _body("a", "b"), recs)

    assert sorted(f["id"] for f in out["files"]) == ["a", "b"]
    assert _system_notes(out) == []


def test_oversize_evicts_largest_first_until_under_budget():
    """予算超過分を大きい順に除去し、収まったら止める。残りは body に残る。"""
    inst = _gate(max_total_tokens=10)
    recs = {
        "big": _rec("x" * 60, 999),
        "mid": _rec("y" * 30, 999),
        "small": _rec("z" * 5, 999),
    }
    out = _run(inst, _body("big", "mid", "small"), recs)

    # big(60)→mid(30) を除去して残 small(5)≤10 で停止。
    assert [f["id"] for f in out["files"]] == ["small"]
    notes = _system_notes(out)
    assert len(notes) == 1
    assert "file_id=big" in notes[0]["content"]
    assert "file_id=mid" in notes[0]["content"]
    assert "file_id=small" not in notes[0]["content"]


def test_failed_extraction_is_announced_but_kept_in_body():
    """raw は大きいのに抽出が空 = failed。告知するが body からは外さない
    (import_chat_file が拾えるように)。"""
    inst = _gate(max_total_tokens=100000)
    inst.valves.min_failed_raw_bytes = 100
    recs = {"empty": _rec("", size=1000)}  # 空 content・raw 1000B
    out = _run(inst, _body("empty"), recs)

    assert [f["id"] for f in out["files"]] == ["empty"]  # 残る
    notes = _system_notes(out)
    assert len(notes) == 1
    assert "抽出が空" in notes[0]["content"]
    assert "file_id=empty" in notes[0]["content"]


def test_small_empty_file_not_flagged_failed():
    """min_failed_raw_bytes 未満の空ファイルは failed 誤検知しない。"""
    inst = _gate(max_total_tokens=100000)
    inst.valves.min_failed_raw_bytes = 100 * 1024
    recs = {"tiny": _rec("", size=10)}
    out = _run(inst, _body("tiny"), recs)

    assert _system_notes(out) == []


def test_no_attachments_passes_through():
    inst = _gate()
    body = {"messages": [{"role": "user", "content": "hi"}]}
    with patch.object(G.Files, "get_file_by_id", side_effect=AssertionError("呼ぶな")):
        out = asyncio.run(inst.inlet(body))
    assert out is body


def test_inlet_is_fail_open_on_error():
    """inlet 内で例外が出ても body をそのまま通す(チャットを止めない)。"""
    inst = _gate(max_total_tokens=1)
    body = _body("x")
    with patch.object(G.Files, "get_file_by_id", side_effect=RuntimeError("boom")):
        out = asyncio.run(inst.inlet(body))
    # 間引かれず・告知も無し・元の body が返る
    assert [f["id"] for f in out["files"]] == ["x"]
    assert _system_notes(out) == []
