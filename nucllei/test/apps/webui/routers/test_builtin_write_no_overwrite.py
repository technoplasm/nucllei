"""write_file の上書き止め (valve `write_no_overwrite_paths` — 2026-10-31 ken「MEMORY.md の上書きは edit_file に
案内で」)。置き場の下の既にあるファイルへの write_file は断って edit_file へ向け、新しく作るのは通す。置き場の外・
既定 (空) は今までどおり。"""

import asyncio

from test.util.tool_errors import call_text

USER = {"id": "u1", "role": "user"}


def _tool(exists: bool = True, kind: str = "file", valve: str = "/workspace/memory"):
    """`/files/write` は sandbox と同じく、`exclusive` で既にあれば 409、末尾が symlink なら 400。"""
    from nucllei.builtin_tools.sandbox import SandboxHTTPError, Tools

    tool = Tools()
    tool.valves.write_no_overwrite_paths = valve
    tool.calls = []

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload))
        if path == "/files/write":
            if exists and kind == "symlink":
                raise SandboxHTTPError(400, "path contains a symlink: x")
            if exists and payload.get("exclusive"):
                raise SandboxHTTPError(409, "already exists")
            return {"ok": True}
        raise AssertionError(path)

    tool._post = _post
    return tool


def _write(tool, path, content="x"):
    return asyncio.run(call_text(tool.write_file(path, content, __user__=USER)))


def _ops(tool):
    return [p for p, _ in tool.calls]


def _exclusive(tool):
    return [bool(payload.get("exclusive")) for _, payload in tool.calls]


def test_existing_file_under_the_place_is_not_overwritten():
    tool = _tool(exists=True)
    out = _write(tool, "/workspace/memory/MEMORY.md")
    assert out == (
        "ERROR: /workspace/memory/MEMORY.md は既にある (この置き場では write_file で上書きしない)。"
        "次の手: 一部を直す・足すなら edit_file (末尾に足すなら最後の行を old_text に含める。"
        "構成を変えるなら edit_file で該当範囲ごと置き換える — run_shell や run_python で書き直さない)。"
    )
    # 排他の作成 (既にあれば sandbox が 409 — stat してから書く間に作られても上書きしない)
    assert _exclusive(tool) == [True]
    # 入れ子のファイル (用件ごとの記憶) も
    tool = _tool(exists=True)
    assert _write(tool, "/workspace/memory/topics/x.md").startswith("ERROR: ")
    # 末尾が symlink のものも書かない (sandbox が断る)
    tool = _tool(exists=True, kind="symlink")
    assert _write(tool, "/workspace/memory/link.md").startswith("ERROR: ")


def test_new_file_under_the_place_is_created():
    tool = _tool(exists=False)
    out = _write(tool, "/workspace/memory/new-topic.md")
    assert out.startswith("/workspace/memory/new-topic.md に書き込んだ")
    assert _exclusive(tool) == [True]


def test_outside_the_place_and_the_place_itself_are_unchanged():
    tool = _tool(exists=True)
    assert _write(tool, "/workspace/20261031-x/a.md").startswith(
        "/workspace/20261031-x/a.md に書き込んだ"
    )
    assert _exclusive(tool) == [False]  # 今までどおりの上書き
    # `..` で置き場の外へ出たものは置き場の下ではない (正規化してから見る)
    tool = _tool(exists=True)
    assert _write(tool, "/workspace/memory/../notes.md").startswith(
        "/workspace/notes.md に書き込んだ"
    )
    assert _exclusive(tool) == [False]


def test_dotdot_into_the_place_is_still_guarded():
    tool = _tool(exists=True)
    out = _write(tool, "/workspace/20261031-x/../memory/MEMORY.md")
    assert out.startswith("ERROR: /workspace/memory/MEMORY.md は既にある")


def test_default_is_empty_and_overwrites_as_before():
    from nucllei.builtin_tools.sandbox import Tools

    assert Tools().valves.write_no_overwrite_paths == ""
    tool = _tool(exists=True, valve="")
    assert _write(tool, "/workspace/memory/MEMORY.md").startswith(
        "/workspace/memory/MEMORY.md に書き込んだ"
    )
    assert _exclusive(tool) == [False]


def test_globs_and_lists_follow_rm_allowed_paths():
    tool = _tool(exists=True, valve="/tmp/x, /workspace/[0-9]*-keep\n")
    assert _write(tool, "/workspace/20261031-keep/a.md").startswith("ERROR: ")
    tool = _tool(exists=True, valve="/tmp/x, /workspace/[0-9]*-keep\n")
    assert _write(tool, "/workspace/20261031-other/a.md").startswith(
        "/workspace/20261031-other/a.md に書き込んだ"
    )


def test_other_sandbox_errors_are_reported_as_before():
    """409 以外の失敗 (繋がらない等) は今までどおりの ERROR (書けたことにしない)。"""
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.valves.write_no_overwrite_paths = "/workspace/memory"

    async def _post(path, payload, timeout_s):
        raise OSError("down")

    tool._post = _post
    out = _write(tool, "/workspace/memory/MEMORY.md")
    assert out.startswith("ERROR: sandbox に接続できない")
