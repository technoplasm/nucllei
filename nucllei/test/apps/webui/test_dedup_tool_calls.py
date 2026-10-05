"""一度きりの掃除 nucllei dedup-tool-calls の回帰テスト(refs #142)。"""

import copy
import io
import json
import sqlite3

from nucllei.maintenance.dedup_tool_calls import dedup_blocks, dedup_chat, run

CALLS = [
    {"id": "call_a", "type": "function", "function": {"name": "f", "arguments": "{}"}},
    {"id": "call_b", "type": "function", "function": {"name": "f", "arguments": "{}"}},
]
RESULTS = [
    {"tool_call_id": "call_a", "content": "A"},
    {"tool_call_id": "call_b", "content": "B"},
]


def _dup_blocks(n=3):
    blocks = [{"type": "text", "content": "\n\n"}]
    for i in range(n):
        last = i == n - 1
        blocks.append(
            {
                "type": "tool_calls",
                "calls": copy.deepcopy(CALLS),
                "results": copy.deepcopy(RESULTS) if last else [],
            }
        )
        blocks.append({"type": "text", "content": "\n"})
    blocks.append({"type": "text", "content": "answer"})
    return blocks


def _clean_blocks():
    return [
        {"type": "text", "content": "x"},
        {
            "type": "tool_calls",
            "calls": copy.deepcopy(CALLS[:1]),
            "results": RESULTS[:1],
        },
        {
            "type": "tool_calls",
            "calls": copy.deepcopy(CALLS[1:]),
            "results": RESULTS[1:],
        },
        # synthetic は対象外(同じ id でも残す)
        {"type": "tool_calls", "synthetic": True, "calls": copy.deepcopy(CALLS[:1])},
        {"type": "text", "content": "y"},
    ]


def _chat(blocks_dup, blocks_clean):
    dup = {
        "id": "m2",
        "role": "assistant",
        "content": "answer",
        "content_blocks": blocks_dup,
    }
    clean = {
        "id": "m4",
        "role": "assistant",
        "content": "xy",
        "content_blocks": blocks_clean,
    }
    user = {"id": "m1", "role": "user", "content": "q"}
    return {
        "history": {
            "messages": {"m1": user, "m2": dup, "m4": clean},
            "currentId": "m4",
        },
        "messages": [copy.deepcopy(user), copy.deepcopy(dup), copy.deepcopy(clean)],
    }


def test_dedup_blocks_keeps_only_block_with_results():
    blocks, dropped = dedup_blocks(_dup_blocks(3))
    assert dropped == 2
    tool_blocks = [b for b in blocks if b["type"] == "tool_calls"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0]["results"] == RESULTS
    # text ブロックは触らない(message.content との整合を保つ)
    assert [b for b in blocks if b["type"] == "text"] == [
        b for b in _dup_blocks(3) if b["type"] == "text"
    ]


def test_dedup_blocks_leaves_clean_unchanged():
    blocks = _clean_blocks()
    out, dropped = dedup_blocks(blocks)
    assert dropped == 0
    assert out == _clean_blocks()


def test_dedup_chat_fixes_history_and_linear_copy():
    chat = _chat(_dup_blocks(3), _clean_blocks())
    original = copy.deepcopy(chat)
    new, changes = dedup_chat(chat)
    assert chat == original  # 入力は非破壊
    assert sorted(changes) == [("history", "m2", 2), ("messages", "m2", 2)]
    for m in (new["history"]["messages"]["m2"], new["messages"][1]):
        tool_blocks = [b for b in m["content_blocks"] if b["type"] == "tool_calls"]
        assert len(tool_blocks) == 1 and tool_blocks[0]["results"] == RESULTS
    assert new["history"]["messages"]["m4"] == original["history"]["messages"]["m4"]
    assert new["messages"][2] == original["messages"][2]


def test_run_sqlite_dry_run_then_apply(tmp_path):
    db = tmp_path / "webui.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE chat (id TEXT PRIMARY KEY, chat JSON, updated_at BIGINT)"
    )
    dirty = _chat(_dup_blocks(3), _clean_blocks())
    clean = _chat(_clean_blocks(), _clean_blocks())
    conn.execute("INSERT INTO chat VALUES ('c1', ?, 100)", (json.dumps(dirty),))
    conn.execute("INSERT INTO chat VALUES ('c2', ?, 200)", (json.dumps(clean),))
    conn.commit()
    conn.close()
    url = f"sqlite:///{db}"
    backups = tmp_path / "backups"

    summary = run(url, apply=False, backup_dir=backups, out=io.StringIO())
    assert (summary["chats"], summary["messages"], summary["blocks"]) == (1, 1, 2)
    assert not backups.exists()  # dry-run は書かない

    summary = run(url, apply=True, backup_dir=backups, out=io.StringIO())
    assert summary["updated"] == 1 and summary["skipped"] == []
    assert len(list(backups.iterdir())) == 1

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT id, chat FROM chat").fetchall())
    stamps = dict(conn.execute("SELECT id, updated_at FROM chat").fetchall())
    conn.close()
    assert stamps == {"c1": 100, "c2": 200}  # 一覧の並びを変えない
    assert json.loads(rows["c2"]) == clean
    fixed = json.loads(rows["c1"])
    assert dedup_chat(fixed)[1] == []
    assert fixed["history"]["messages"]["m2"]["content"] == "answer"

    assert run(url, apply=False, backup_dir=backups, out=io.StringIO())["chats"] == 0
