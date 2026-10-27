"""builtin_sandbox の edit_file(llens 依頼・ken 指示 2026-10-27)の回帰。

sandbox の HTTP(`_post`)だけをモックし、パスの解決・一致・文字コード・書き戻しは本物のコードを
通す。偽の sandbox はファイル 1 つを持ち、`/files/read` は範囲だけを返し、`/files/write` は中身を
差し替える(`tool.data`)。グループの置き場の可否(実 DB の所属)は `test_ext_acl.py` 側。
"""

import asyncio
import base64

import pytest

from test.util.tool_errors import call_text

USER = {"id": "u1", "role": "user"}


def _tool(data=None, stat=None, error=None):
    """`/files/stat` → stat(既定は data の大きさのファイル)、`/files/read` → data、
    `/files/write` → data を書き換える。"""
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.calls = []
    tool.data = data

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload))
        if error is not None:
            raise error
        if path == "/files/stat":
            if stat is not None:
                return stat
            return {"exists": True, "type": "file", "size": len(tool.data or b"")}
        if path == "/files/read":
            off, n = payload["offset"], payload["length"]
            chunk = tool.data[off : off + n]
            return {
                "name": "x",
                "size": len(tool.data),
                "offset": off,
                "length": len(chunk),
                "b64": base64.b64encode(chunk).decode(),
            }
        if path == "/files/write":
            tool.data = base64.b64decode(payload["b64"])
            return {"ok": True}
        raise AssertionError(path)

    tool._post = _post
    return tool


def _edit(tool, path, old, new):
    return asyncio.run(call_text(tool.edit_file(path, old, new, __user__=USER)))


def _ops(tool):
    return [c[0] for c in tool.calls]


# ---------------------------------------------------------------------------
# 置き換え
# ---------------------------------------------------------------------------
def test_unique_match_is_replaced_and_written_back():
    body = "# 記憶\n\n- 好きな色: 青\n- 住所: 札幌\n- 職種: 医師\n"
    tool = _tool(body.encode("utf-8"))
    out = _edit(tool, "/workspace/memory.md", "- 住所: 札幌\n", "- 住所: 旭川\n")
    assert tool.data.decode("utf-8") == body.replace("札幌", "旭川")
    assert out == (
        "/workspace/memory.md の 4 行目を置き換えた (文字コード: UTF-8、"
        f"{len(tool.data)}B)。\n\n"
        "2 | \n"
        "3 | - 好きな色: 青\n"
        "4 | - 住所: 旭川\n"
        "5 | - 職種: 医師\n"
        "6 | "
    )
    # workspace は相対で、stat → 全体の範囲読み(nofollow)→ 書き戻し。mounts は付けない
    assert _ops(tool) == ["/files/stat", "/files/read", "/files/write"]
    assert tool.calls[0][1] == {"user_id": "u1", "path": "memory.md"}
    assert tool.calls[1][1] == {
        "user_id": "u1",
        "path": "memory.md",
        "nofollow": True,
        "offset": 0,
        "length": len(body.encode("utf-8")),
    }
    assert set(tool.calls[2][1]) == {"user_id", "path", "b64"}
    assert tool.calls[2][1]["path"] == "memory.md"


def test_multiline_replacement_reports_the_range_and_caps_the_excerpt():
    lines = [f"line{i}" for i in range(1, 31)]
    tool = _tool(("\n".join(lines) + "\n").encode())
    new = "\n".join(f"new{i}" for i in range(1, 13)) + "\n"
    out = _edit(tool, "/workspace/a.py", "line5\n", new)
    head, excerpt = out.split("\n\n", 1)
    assert head.startswith("/workspace/a.py の 5〜16 行目を置き換えた (")
    # 前 2 行から 10 行まで。残りは … で切ったと分かる
    assert excerpt.split("\n") == [
        " 3 | line3",
        " 4 | line4",
        " 5 | new1",
        " 6 | new2",
        " 7 | new3",
        " 8 | new4",
        " 9 | new5",
        "10 | new6",
        "11 | new7",
        "12 | new8",
        "   | …",
    ]


def test_empty_new_text_deletes():
    tool = _tool(b"a\nb\nc\n")
    out = _edit(tool, "/workspace/a.txt", "b\n", "")
    assert tool.data == b"a\nc\n"
    assert out.startswith("/workspace/a.txt の 2 行目を置き換えた")


def test_no_match_is_refused_without_writing():
    tool = _tool(b"alpha\nbeta\n")
    out = _edit(tool, "/workspace/a.txt", "gamma", "x")
    assert out == (
        "ERROR: /workspace/a.txt に old_text と一致する箇所が無い。"
        "次の手: read_file で現物を見て、空白・改行まで含めて old_text を写し直す。"
    )
    assert "/files/write" not in _ops(tool)
    # 空白の違いも寄せない
    out = _edit(tool, "/workspace/a.txt", "alpha \n", "x")
    assert "一致する箇所が無い" in out
    assert tool.data == b"alpha\nbeta\n"


def test_crlf_mismatch_is_named_but_not_fixed():
    tool = _tool(b"a\r\nb\r\n")
    out = _edit(tool, "/workspace/a.txt", "a\nb", "x")
    assert out.startswith(
        "ERROR: /workspace/a.txt に old_text と一致する箇所が無い (このファイルの改行は CRLF)。"
    )
    assert tool.data == b"a\r\nb\r\n"
    # 改行を含まない old_text には添えない
    out = _edit(_tool(b"a\r\nb\r\n"), "/workspace/a.txt", "zz", "x")
    assert "CRLF" not in out
    # CRLF のまま写せば直せる。結果の抜粋に \r は出さない
    tool = _tool(b"a\r\nb\r\n")
    out = _edit(tool, "/workspace/a.txt", "a\r\n", "A\r\n")
    assert tool.data == b"A\r\nb\r\n"
    assert "\r" not in out


def test_multiple_matches_are_refused_with_line_numbers():
    body = "".join(f"x = {i}\nTODO\n" for i in range(8))
    tool = _tool(body.encode())
    out = _edit(tool, "/workspace/a.py", "TODO", "DONE")
    assert out == (
        "ERROR: /workspace/a.py に old_text と一致する箇所が 8 か所ある (2, 4, 6, 8, 10, … 行目)。"
        "次の手: 前後の行を old_text に含めて、一致が 1 か所になるようにする。"
    )
    assert "/files/write" not in _ops(tool)
    # 重なる一致も数える(先頭だけを黙って直さない)
    out = _edit(_tool(b"aaa\n"), "/workspace/a.txt", "aa", "b")
    assert "2 か所ある (1 行目)" in out


def test_empty_old_text_is_refused_before_reading():
    tool = _tool(b"a\n")
    out = _edit(tool, "/workspace/a.txt", "", "b\n")
    assert out == (
        "ERROR: old_text が空。次の手: 末尾に足すなら、最後の行を old_text に含め、"
        "new_text にその行 + 足す行を書く。"
    )
    assert tool.calls == []


# ---------------------------------------------------------------------------
# 文字コード
# ---------------------------------------------------------------------------
def test_bom_is_kept():
    tool = _tool(b"\xef\xbb\xbfa,b\n1,2\n")
    out = _edit(tool, "/workspace/x.csv", "1,2", "3,4")
    assert tool.data == b"\xef\xbb\xbfa,b\n3,4\n"
    assert "文字コード: UTF-8 (BOM 付き)、" in out


def test_cp932_is_kept():
    body = "品名,数量\nりんご,3\n"
    tool = _tool(body.encode("cp932"))
    out = _edit(tool, "/workspace/a.csv", "りんご,3", "みかん,5")
    assert tool.data == "品名,数量\nみかん,5\n".encode("cp932")
    assert "文字コード: cp932、" in out


def test_cp932_unrepresentable_char_is_refused():
    tool = _tool("品名\nりんご\n".encode("cp932"))
    out = _edit(tool, "/workspace/a.txt", "りんご", "りんご🍎")
    assert out == (
        "ERROR: /workspace/a.txt は cp932 のファイルで、new_text に cp932 で表せない文字がある"
        " (🍎)。次の手: cp932 にある文字に置き換えて呼び直す"
        " (ファイルを UTF-8 に変えてよいかはユーザーが決める)。"
    )
    assert "/files/write" not in _ops(tool)


@pytest.mark.parametrize(
    "data",
    [b"abc\x00def", b"\xff\xfe\xfd\x80"],
    ids=["nul", "undecodable"],
)
def test_non_text_content_is_refused(data):
    from nucllei.builtin_tools.sandbox import _EDIT_BINARY_NEXT

    tool = _tool(data)
    out = _edit(tool, "/workspace/data.txt", "abc", "x")
    assert out == (
        "ERROR: /workspace/data.txt はテキストとして読めない (Office 文書などのバイナリか、"
        f"UTF-8 / cp932 のテキストでない)。次の手: {_EDIT_BINARY_NEXT}"
    )
    assert "/files/write" not in _ops(tool)


@pytest.mark.parametrize("name", ["a.xlsx", "a.docx", "a.png", "a.pdf", "a.zip"])
def test_binary_extensions_are_refused_without_asking_the_sandbox(name):
    tool = _tool(b"x")
    out = _edit(tool, f"/workspace/{name}", "x", "y")
    assert out.startswith(f"ERROR: /workspace/{name} はテキストとして読めない")
    assert tool.calls == []


# ---------------------------------------------------------------------------
# パス・大きさ・sandbox の失敗(write_file と同じ解決)
# ---------------------------------------------------------------------------
def test_path_errors_are_the_same_as_write_file():
    tool = _tool(b"x")
    for bad in (
        "work/a.txt",
        "",
        "https://x/y.md",
        "/etc/passwd",
        "/workspace",
        "/workspace/dir/",
        "/ext/server/a.txt",
    ):
        expected = asyncio.run(call_text(tool.write_file(bad, "x", __user__=USER)))
        assert expected.startswith("ERROR: "), bad
        assert _edit(tool, bad, "x", "y") == expected, bad
    assert tool.calls == []


def test_stat_outcomes():
    out = _edit(_tool(stat={"exists": False}), "/workspace/imports/a.md", "x", "y")
    assert out.startswith(
        "ERROR: /workspace/imports/a.md というファイルは無い (ディレクトリは不可)。"
    )
    out = _edit(
        _tool(stat={"exists": True, "type": "dir", "size": 0}),
        "/workspace/work",
        "x",
        "y",
    )
    assert (
        out == "ERROR: /workspace/work はディレクトリ。次の手: ファイル名まで指定する。"
    )
    tool = _tool(stat={"exists": True, "type": "symlink", "size": 0})
    out = _edit(tool, "/workspace/link.md", "x", "y")
    assert out.startswith("ERROR: /workspace/link.md は使えない path")
    assert _ops(tool) == ["/files/stat"]


def test_too_large_before_and_after():
    from nucllei.builtin_tools import sandbox as mod

    limit = mod.TEXT_MAX_BYTES
    tool = _tool(stat={"exists": True, "type": "file", "size": limit + 1})
    out = _edit(tool, "/workspace/big.txt", "x", "y")
    assert out.startswith(
        "ERROR: /workspace/big.txt は edit_file で直せる大きさを超えている ("
    )
    assert out.endswith("次の手: run_python でファイルを開いて置き換える。")
    assert _ops(tool) == ["/files/stat"]

    tool = _tool(b"a" * (limit - 10) + b"\nX\n")
    out = _edit(tool, "/workspace/big.txt", "X", "Y" * 20)
    assert out.startswith("ERROR: 置き換えた結果が大きすぎる (")
    assert "/files/write" not in _ops(tool)


def test_empty_file_has_nothing_to_match():
    tool = _tool(b"")
    out = _edit(tool, "/workspace/empty.txt", "x", "y")
    assert "一致する箇所が無い" in out
    assert _ops(tool) == ["/files/stat"]


def test_sandbox_errors_use_the_shared_wording():
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    out = _edit(_tool(error=SandboxHTTPError(404, "nf")), "/workspace/a.md", "x", "y")
    assert "というファイルは無い" in out
    out = _edit(_tool(error=SandboxHTTPError(401, "x")), "/workspace/a.md", "x", "y")
    assert "sandbox との認証に失敗した" in out
    out = _edit(_tool(error=OSError("down")), "/workspace/a.md", "x", "y")
    assert "sandbox に接続できない" in out


def test_no_user():
    out = asyncio.run(call_text(_tool(b"x").edit_file("/workspace/a.md", "x", "y")))
    assert out == "ERROR: ユーザーを特定できない。"


# ---------------------------------------------------------------------------
# spec(モデルに見える面)
# ---------------------------------------------------------------------------
def test_spec():
    from nucllei.builtin_tools.sandbox import Tools
    from nucllei.utils.tools import get_tool_specs

    specs = {s["name"]: s for s in get_tool_specs(Tools())}
    spec = specs["edit_file"]
    assert list(spec["parameters"]["properties"]) == ["path", "old_text", "new_text"]
    assert spec["parameters"]["required"] == ["path", "old_text", "new_text"]
    assert (
        "sandbox の絶対パス (`/workspace/…`)。"
        in (spec["parameters"]["properties"]["path"]["description"])
    )
    # write_file / read_file から「一部だけ直すなら edit_file」へ向ける
    assert "一部だけを直すなら edit_file" in specs["write_file"]["description"]
    assert "一部だけを直すなら edit_file" in specs["read_file"]["description"]
