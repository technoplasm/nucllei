"""builtin_sandbox の remove_file(llens 依頼・ken 決定 2026-10-27)の回帰。

sandbox の HTTP(`_post`)だけをモックし、パスの解決と断り・応答の言い方は本物のコードを通す。
`/files/trash` 自体(各置き場の trash/ へ移す・名前の番号・symlink・完全には消さない)は
`sandbox/tests/test_files_browser.py`、グループの置き場の可否(実 DB の所属)は `test_ext_acl.py` 側。
"""

import asyncio

import pytest

from test.util.tool_errors import call_text

USER = {"id": "u1", "role": "user"}


def _tool(res=None, error=None):
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.calls = []

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload))
        if error is not None:
            raise error
        assert path == "/files/trash", path
        return res if res is not None else {}

    tool._post = _post
    return tool


def _rm(tool, path):
    return asyncio.run(call_text(tool.remove_file(path, __user__=USER)))


def test_workspace_file_moves_to_trash():
    tool = _tool(
        {"ok": True, "trashed": True, "path": "trash/memo.md", "cache_ttl_days": 30}
    )
    out = _rm(tool, "/workspace/notes/memo.md")
    assert out == (
        "/workspace/notes/memo.md を /workspace/trash/memo.md へ移した。"
        "移してから 30 日で自動で消える。\n"
        "戻すなら run_shell で `mv /workspace/trash/memo.md /workspace/notes/memo.md` を実行する。"
    )
    # workspace は相対で、mounts は付けない
    assert tool.calls == [("/files/trash", {"user_id": "u1", "path": "notes/memo.md"})]


def test_directory_and_trailing_slash_are_accepted():
    tool = _tool({"ok": True, "path": "trash/proj-2", "cache_ttl_days": 30})
    out = _rm(tool, "/workspace/proj/")
    assert out.startswith("/workspace/proj を /workspace/trash/proj-2 へ移した。")
    assert tool.calls[0][1]["path"] == "proj"


def test_restore_command_is_quoted():
    tool = _tool({"ok": True, "path": "trash/a b's.md", "cache_ttl_days": 30})
    out = _rm(tool, "/workspace/a b's.md")
    assert (
        "`mv '/workspace/trash/a b'\"'\"'s.md' '/workspace/a b'\"'\"'s.md'`" in out
    ), out


@pytest.mark.parametrize("ttl", [0, None, "30", True])
def test_no_expiry_sentence_without_a_positive_ttl(ttl):
    res = {"ok": True, "path": "trash/a.md"}
    if ttl is not None:
        res["cache_ttl_days"] = ttl
    out = _rm(_tool(res), "/workspace/a.md")
    assert "日で自動で消える" not in out
    assert out.startswith("/workspace/a.md を /workspace/trash/a.md へ移した。\n")


@pytest.mark.parametrize(
    "path, fact",
    [
        ("/workspace/trash", "/workspace/trash はゴミ箱そのもので"),
        ("/workspace/trash/a.md", "/workspace/trash/a.md はゴミ箱の中にある"),
        ("/workspace/trash/d/", "/workspace/trash/d はゴミ箱の中にある"),
        ("/workspace/imports", "/workspace/imports は sandbox が使う置き場で"),
        ("/workspace/.exec", "/workspace/.exec は名前が . で始まり"),
        ("/workspace/w/.env", "/workspace/w/.env は名前が . で始まり"),
        ("/workspace", "/workspace は置き場そのもので"),
        ("/workspace/", "/workspace は置き場そのもので"),
    ],
)
def test_refused_before_asking_the_sandbox(path, fact):
    tool = _tool()
    out = _rm(tool, path)
    assert out.startswith("ERROR: " + fact), out
    assert tool.calls == []


def test_trash_inside_points_to_the_automatic_expiry():
    out = _rm(_tool(), "/workspace/trash/a.md")
    assert out.endswith(
        "次の手: 何もしなくてよい (ゴミ箱のものは移してから一定期間 (既定 30 日) で自動で消える)。"
        "すぐ空けたいならユーザーに頼む。"
    )


def test_imports_contents_can_be_moved():
    tool = _tool({"ok": True, "path": "trash/a.pdf", "cache_ttl_days": 30})
    assert "へ移した" in _rm(tool, "/workspace/imports/a.pdf")


def test_path_errors_match_write_file():
    tool = _tool()
    for bad in ("work/a.txt", "", "https://x/y.md", "/etc/passwd", "/ext/server/a.txt"):
        expected = asyncio.run(call_text(tool.write_file(bad, "x", __user__=USER)))
        assert expected.startswith("ERROR: "), bad
        assert _rm(tool, bad) == expected, bad
    assert tool.calls == []


def test_sandbox_errors():
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    # 古い sandbox (`/files/trash` が無い) — FastAPI 既定の 404
    out = _rm(
        _tool(error=SandboxHTTPError(404, '{"detail":"Not Found"}')), "/workspace/a.md"
    )
    assert out.startswith(
        "ERROR: sandbox が古く、remove_file に対応していない (sandbox の更新が要る)。"
    ), out
    assert "ファイルはそのまま残っている" in out
    # 無い (ディレクトリも可なので「ディレクトリは不可」とは言わない)
    out = _rm(
        _tool(error=SandboxHTTPError(404, '{"detail":"not found: a.md"}')),
        "/workspace/a.md",
    )
    assert out.startswith(
        "ERROR: /workspace/a.md は無い。次の手: list_workspace で確かめる。"
    )
    assert "ディレクトリは不可" not in out
    out = _rm(
        _tool(error=SandboxHTTPError(404, '{"detail":"not found: imports/a.pdf"}')),
        "/workspace/imports/a.pdf",
    )
    assert "期限で消えた可能性がある" in out
    # 末端の symlink
    out = _rm(
        _tool(error=SandboxHTTPError(400, '{"detail":"is a symlink: link"}')),
        "/workspace/link",
    )
    assert out.startswith(
        "ERROR: /workspace/link は symlink で、remove_file では移さない"
    )
    # 経路の symlink 等は共通の文
    out = _rm(
        _tool(error=SandboxHTTPError(400, '{"detail":"path contains a symlink: x"}')),
        "/workspace/l/a.md",
    )
    assert "は使えない path" in out
    # 名前が尽きた
    out = _rm(_tool(error=SandboxHTTPError(409, "no free name")), "/workspace/a.md")
    assert out.startswith(
        "ERROR: ゴミ箱に a.md と同じ名前のものが多すぎて移せない。"
    ), out
    out = _rm(_tool(error=SandboxHTTPError(507, "full")), "/workspace/a.md")
    assert "容量 (quota) が一杯" in out
    out = _rm(_tool(error=SandboxHTTPError(401, "x")), "/workspace/a.md")
    assert "sandbox との認証に失敗した" in out
    out = _rm(_tool(error=OSError("down")), "/workspace/a.md")
    assert "sandbox に接続できない" in out


def test_bad_response():
    out = _rm(_tool({"ok": True}), "/workspace/a.md")
    assert out.startswith("ERROR: sandbox の応答を読めない")


def test_no_user():
    out = asyncio.run(call_text(_tool().remove_file("/workspace/a.md")))
    assert out == "ERROR: ユーザーを特定できない。"


def test_spec():
    from nucllei.builtin_tools.sandbox import Tools
    from nucllei.utils.tools import get_tool_specs

    specs = {s["name"]: s for s in get_tool_specs(Tools())}
    spec = specs["remove_file"]
    assert list(spec["parameters"]["properties"]) == ["path"]
    assert spec["parameters"]["required"] == ["path"]
    assert "ゴミ箱へ移す" in spec["description"]
    assert "rm ではなくこちらを使う" in spec["description"]
    # rm でなく remove_file は remove_file の説明だけが言う (2026-10-30 batch 2 で toolkit の共通注記から
    # 外した)。記憶・EMR の抽出物の rm の例外は system prompt に委ねる
    assert "例外は system prompt の決まりに従う" in spec["description"]
    assert "remove_file" not in Tools.__doc__
