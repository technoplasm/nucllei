"""builtin_sandbox の read_file(#153)の回帰。

sandbox の HTTP(`_post`)だけをモックし、パスの解決・分類・文字コード・ページング・表示種別は
本物のコードを通す。グループの置き場の可否(実 DB の所属)は `test_ext_acl.py` 側。
"""

import asyncio
import base64

import pytest

from test.util.tool_errors import call_text

USER = {"id": "u1", "role": "user"}


def _tool(data=None, stat=None, error=None):
    """`/files/stat` → stat(既定は data の大きさのファイル)、`/files/read` → data。"""
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.calls = []

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload))
        if error is not None:
            raise error
        if path == "/files/stat":
            if stat is not None:
                return stat
            return {"exists": True, "type": "file", "size": len(data or b"")}
        if path == "/files/read":
            return {
                "name": "x",
                "size": len(data),
                "b64": base64.b64encode(data).decode(),
            }
        raise AssertionError(path)

    tool._post = _post
    return tool


def _run(coro):
    return asyncio.run(call_text(coro))


def _read(tool, path, **kw):
    return _run(tool.read_file(path, __user__=USER, **kw))


# ---------------------------------------------------------------------------
# 文字コード
# ---------------------------------------------------------------------------
def test_utf8_markdown_is_rendered_as_markdown():
    from nucllei.utils.tool_render import Rendered

    tool = _tool("# レシピ\n\n手順".encode("utf-8"))
    out = _read(tool, "/workspace/work/recipe.md")
    assert isinstance(out, Rendered)
    assert out.kind == "markdown" and out.language is None
    assert (
        out.text
        == "`/workspace/work/recipe.md` — 文字コード: UTF-8、全 9 字\n\n# レシピ\n\n手順"
    )
    # workspace は相対で、stat → read(nofollow)の順。mounts は付けない
    assert [c[0] for c in tool.calls] == ["/files/stat", "/files/read"]
    assert tool.calls[0][1] == {"user_id": "u1", "path": "work/recipe.md"}
    assert tool.calls[1][1] == {
        "user_id": "u1",
        "path": "work/recipe.md",
        "nofollow": True,
    }


def test_bom_utf8_is_stripped_and_reported():
    out = _read(_tool(b"\xef\xbb\xbfa,b\n1,2\n"), "/workspace/x.csv")
    assert out.text.startswith("`/workspace/x.csv` — 文字コード: UTF-8 (BOM 付き)、")
    assert out.text.endswith("\n\na,b\n1,2\n")
    assert (out.kind, out.language) == ("code", "csv")


def test_cp932_is_decoded_and_says_so():
    out = _read(_tool("品名,数量\nりんご,3\n".encode("cp932")), "/workspace/a.txt")
    assert (
        "文字コード: cp932 (Shift_JIS 系。write_file で書き戻すと UTF-8 になる)"
        in out.text
    )
    assert out.text.endswith("品名,数量\nりんご,3\n")
    assert out.kind == "text"


@pytest.mark.parametrize(
    "data",
    [b"abc\x00def", b"\xff\xfe\xfd\x80", b"\xef\xbb\xbf\xff\xfe"],
    ids=["nul", "undecodable", "bom-then-invalid"],
)
def test_non_text_content_is_the_binary_error(data):
    from nucllei.builtin_tools.sandbox import READ_BINARY_HINT

    out = _read(_tool(data), "/workspace/data.txt")
    assert out == (
        "ERROR: /workspace/data.txt はテキストとして読めない (Office 文書などのバイナリか、"
        f"UTF-8 / cp932 のテキストでない)。次の手: {READ_BINARY_HINT}"
    )


def test_empty_file():
    out = _read(_tool(b""), "/workspace/empty.txt")
    assert out.text == "`/workspace/empty.txt` — 文字コード: UTF-8、全 0 字\n\n"


# ---------------------------------------------------------------------------
# 表示種別(拡張子)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, kind, language",
    [
        ("a.md", "markdown", None),
        ("a.MARKDOWN", "markdown", None),
        ("a.py", "code", "python"),
        ("a.sh", "code", "bash"),
        ("a.ts", "code", "typescript"),
        ("a.yml", "code", "yaml"),
        ("a.json", "code", "json"),
        ("a.sql", "code", "sql"),
        ("a.tsv", "code", "tsv"),
        ("a.r", "code", "r"),
        ("a.html", "code", "html"),
        ("a.log", "text", None),
        ("Makefile", "text", None),
        (".bashrc", "text", None),
    ],
)
def test_kind_follows_the_extension(name, kind, language):
    out = _read(_tool(b"x"), f"/workspace/{name}")
    assert (out.kind, out.language) == (kind, language)
    descriptor = {"kind": kind, **({"language": language} if language else {})}
    assert out.descriptor() == descriptor


# ---------------------------------------------------------------------------
# 読まない物(拡張子で先に断る — sandbox に問い合わせない)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["fig.png", "scan.PDF", "photo.jpeg"])
def test_image_and_pdf_point_to_feed_image(name):
    from nucllei.builtin_tools.sandbox import READ_IMAGE_HINT

    tool = _tool(b"x")
    out = _read(tool, f"/workspace/{name}")
    assert out == (
        f"ERROR: /workspace/{name} は画像・PDF で、read_file はテキストしか読まない。"
        f"次の手: {READ_IMAGE_HINT}"
    )
    assert "feed_image" in READ_IMAGE_HINT
    assert tool.calls == []


@pytest.mark.parametrize("name", ["report.docx", "table.xlsx", "a.zip", "a.sqlite"])
def test_office_and_binary_extensions_are_refused(name):
    from nucllei.builtin_tools.sandbox import READ_BINARY_HINT

    tool = _tool(b"x")
    out = _read(tool, f"/workspace/{name}")
    assert out.startswith(f"ERROR: /workspace/{name} はテキストとして読めない")
    assert out.endswith(f"次の手: {READ_BINARY_HINT}")
    assert tool.calls == []


def test_default_hints_do_not_name_deployment_tools():
    from nucllei.builtin_tools import sandbox

    for hint in (
        sandbox.READ_IMAGE_HINT,
        sandbox.READ_BINARY_HINT,
        sandbox.READ_PROXY_MOUNT_HINT,
    ):
        assert "read_doc" not in hint and "file-conversion" not in hint, hint


def test_valves_override_the_hints(monkeypatch):
    from nucllei import env

    monkeypatch.setattr(env, "EXT_MOUNTS", {"doc": "http://cage:13303/doc"})
    tool = _tool(b"x")
    tool.valves.image_hint = "画像は X で見る。"
    tool.valves.binary_hint = "file-conversion skill で変換する。"
    tool.valves.proxy_mount_hint = "read_doc で読む。"
    assert _read(tool, "/workspace/a.png").endswith("次の手: 画像は X で見る。")
    assert _read(tool, "/workspace/a.docx").endswith(
        "次の手: file-conversion skill で変換する。"
    )
    assert _read(tool, "/ext/doc/x/_index.md").endswith("次の手: read_doc で読む。")
    # decode できなかった物も同じ分類 = 同じ valve
    tool = _tool(b"\x00")
    tool.valves.binary_hint = "file-conversion skill で変換する。"
    assert _read(tool, "/workspace/a.txt").endswith(
        "次の手: file-conversion skill で変換する。"
    )


def test_proxy_mount_is_not_read(monkeypatch):
    from nucllei import env
    from nucllei.builtin_tools.sandbox import READ_PROXY_MOUNT_HINT

    monkeypatch.setattr(env, "EXT_MOUNTS", {"doc": "http://cage:13303/doc"})
    tool = _tool(b"x")
    out = _read(tool, "/ext/doc/ガイドライン/a.md")
    assert out == (
        "ERROR: /ext/doc/ガイドライン/a.md はサーバが中継する資料の置き場で、read_file では"
        f"読まない。次の手: {READ_PROXY_MOUNT_HINT}"
    )
    assert tool.calls == []


def test_other_ext_places_are_refused(monkeypatch, tmp_path):
    from nucllei import env

    monkeypatch.setattr(env, "EXT_MOUNTS", {"shared": str(tmp_path)})
    tool = _tool(b"x")
    for path in ("/ext/shared/a.md", "/ext/unknown/a.md", "/ext"):
        out = _read(tool, path)
        assert out.startswith(f"ERROR: {path} は read_file では読めない"), out
        assert "run_shell" in out
    assert tool.calls == []


# ---------------------------------------------------------------------------
# パス・stat
# ---------------------------------------------------------------------------
def test_path_form_errors_are_the_common_ones():
    from nucllei.utils.image_refs import WORKSPACE_PATH_ARG_FORM

    tool = _tool(b"x")
    for bad in ("work/a.md", "", "https://x/y.md", "/etc/passwd"):
        out = _read(tool, bad)
        assert out.startswith("ERROR: ") and WORKSPACE_PATH_ARG_FORM in out, out
    assert "ディレクトリ" in _read(tool, "/workspace")
    assert "ディレクトリ" in _read(tool, "/workspace/work/")
    assert tool.calls == []


def test_stat_outcomes():
    out = _read(_tool(stat={"exists": False}), "/workspace/imports/a.md")
    assert out.startswith(
        "ERROR: /workspace/imports/a.md というファイルは無い (ディレクトリは不可)。"
    )
    assert "期限で消えた可能性" in out
    out = _read(
        _tool(stat={"exists": True, "type": "dir", "size": 0}), "/workspace/work"
    )
    assert out == (
        "ERROR: /workspace/work はディレクトリ。次の手: list_workspace で中を一覧する。"
    )
    # 末端の symlink は sandbox の 400(経路の symlink)と同じ文
    tool = _tool(stat={"exists": True, "type": "symlink", "size": 0})
    out = _read(tool, "/workspace/link.md")
    assert out == (
        "ERROR: /workspace/link.md は使えない path (sandbox が受け付けなかった)。"
        "次の手: /workspace/… の絶対パスで渡す。"
    )
    assert [c[0] for c in tool.calls] == ["/files/stat"]


def test_sandbox_errors_use_the_shared_wording():
    from nucllei.builtin_tools.sandbox import SandboxHTTPError

    out = _read(_tool(error=SandboxHTTPError(404, "nf")), "/workspace/a.md")
    assert "というファイルは無い" in out
    out = _read(_tool(error=SandboxHTTPError(401, "x")), "/workspace/a.md")
    assert "sandbox との認証に失敗した" in out
    out = _read(_tool(error=OSError("down")), "/workspace/a.md")
    assert "sandbox に接続できない" in out


def test_byte_limit_is_checked_before_the_transfer(monkeypatch):
    from nucllei.builtin_tools import sandbox

    monkeypatch.setattr(sandbox, "READ_FILE_LIMIT", 1024)
    tool = _tool(stat={"exists": True, "type": "file", "size": 4096})
    out = _read(tool, "/workspace/big.log")
    assert out == (
        "ERROR: /workspace/big.log は 4.0KB で、read_file で読める上限 1.0KB を超えている。"
        "次の手: run_python / run_shell で必要な部分だけを /workspace/ に書き出してから読む。"
    )
    assert [c[0] for c in tool.calls] == ["/files/stat"]
    # stat の後に伸びた物も読んだ後で断る
    tool = _tool(b"x" * 2048, stat={"exists": True, "type": "file", "size": 10})
    assert "を超えている" in _read(tool, "/workspace/big.log")


def test_default_byte_limit_pairs_with_write_file():
    from nucllei.builtin_tools import sandbox

    assert sandbox.READ_FILE_LIMIT == sandbox.WRITE_FILE_LIMIT


# ---------------------------------------------------------------------------
# offset ページング
# ---------------------------------------------------------------------------
def test_offset_paging_and_next_offset():
    text = "あ" * 100_000
    tool = _tool(text.encode("utf-8"))
    out = _read(tool, "/workspace/long.txt")
    head, body = out.text.split("\n\n", 1)
    assert (
        head
        == "`/workspace/long.txt` — 文字コード: UTF-8、全 100000 字 のうち 0〜40000 字目"
    )
    assert (
        body
        == "あ" * 40000
        + "\n\n(続きあり: next_offset=40000。続きは offset=40000 で読む)"
    )

    out = _read(tool, "/workspace/long.txt", offset=40000, max_chars=999_999)
    head, body = out.text.split("\n\n", 1)
    assert head.endswith("のうち 40000〜100000 字目")
    assert body == "あ" * 60000  # 上限 60000 字で、続きは無い
    assert "next_offset" not in out.text

    from nucllei.env import TOOL_RESULT_MAX_CHARS

    assert len(out.text) <= TOOL_RESULT_MAX_CHARS

    out = _read(tool, "/workspace/long.txt", offset=100_000)
    assert out == (
        "ERROR: offset 100000 は末尾を越えている (/workspace/long.txt は全 100000 字)。"
        "次の手: offset を 0〜99999 で渡す。"
    )
    out = _read(tool, "/workspace/long.txt", offset="x")
    assert out.startswith("ERROR: offset / max_chars が整数でない")


def test_no_user():
    out = _run(_tool(b"x").read_file("/workspace/a.md"))
    assert out == "ERROR: ユーザーを特定できない。"


# ---------------------------------------------------------------------------
# spec(モデルに見える面)
# ---------------------------------------------------------------------------
def test_spec_and_no_static_result_declaration():
    from nucllei.builtin_tools.sandbox import Tools
    from nucllei.utils.tool_render import extract_render_from_function
    from nucllei.utils.tools import get_tool_specs

    spec = next(s for s in get_tool_specs(Tools()) if s["name"] == "read_file")
    assert list(spec["parameters"]["properties"]) == ["path", "offset", "max_chars"]
    assert spec["parameters"]["required"] == ["path"]
    assert "sandbox の絶対パス (`/workspace/…`)。" in (
        spec["parameters"]["properties"]["path"]["description"]
    )
    # 戻り型 Rendered は静的な結果宣言を作らない(FE は宣言を値の render より優先する)
    assert "result" not in extract_render_from_function(Tools().read_file)
