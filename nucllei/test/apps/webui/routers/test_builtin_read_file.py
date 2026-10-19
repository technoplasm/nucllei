"""builtin_sandbox の read_file(#153)の回帰。

sandbox の HTTP(`_post`)だけをモックし、パスの解決・分類・文字コード・ページング・表示種別は
本物のコードを通す。偽の `/files/read` は sandbox と同じく `offset` / `length` の範囲だけを返す
(読んだバイト数を `tool.read_bytes` に記録 — 大きなファイルでも全体を読まないことを見る)。
グループの置き場の可否(実 DB の所属)は `test_ext_acl.py` 側。
"""

import asyncio
import base64
import re

import pytest

from test.util.tool_errors import call_text

USER = {"id": "u1", "role": "user"}


def _tool(data=None, stat=None, error=None):
    """`/files/stat` → stat(既定は data の大きさのファイル)、`/files/read` → data。"""
    from nucllei.builtin_tools.sandbox import Tools

    tool = Tools()
    tool.calls = []
    tool.read_bytes = 0

    async def _post(path, payload, timeout_s):
        tool.calls.append((path, payload))
        if error is not None:
            raise error
        if path == "/files/stat":
            if stat is not None:
                return stat
            return {"exists": True, "type": "file", "size": len(data or b"")}
        if path == "/files/read":
            off, n = payload["offset"], payload["length"]
            chunk = data[off : off + n]
            tool.read_bytes += len(chunk)
            return {
                "name": "x",
                "size": len(data),
                "offset": off,
                "length": len(chunk),
                "b64": base64.b64encode(chunk).decode(),
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
        == "`/workspace/work/recipe.md` — 文字コード: UTF-8、全 19 バイト\n\n# レシピ\n\n手順"
    )
    # workspace は相対で、stat → 範囲読み(nofollow)の順。mounts は付けない
    assert [c[0] for c in tool.calls] == ["/files/stat", "/files/read"]
    assert tool.calls[0][1] == {"user_id": "u1", "path": "work/recipe.md"}
    assert tool.calls[1][1] == {
        "user_id": "u1",
        "path": "work/recipe.md",
        "nofollow": True,
        "offset": 0,
        "length": 19,
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
    tool = _tool(b"")
    out = _read(tool, "/workspace/empty.txt")
    assert out.text == "`/workspace/empty.txt` — 文字コード: UTF-8、全 0 バイト\n\n"
    assert [c[0] for c in tool.calls] == ["/files/stat"]


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
    """feed_image を名指しするのは、このターンに出ているときだけ (2026-10-19 llens 監査 (13))。
    出ていなければ sandbox の中で済む手 (pdftotext / tesseract → read_file) を言う。"""
    from nucllei.builtin_tools.sandbox import READ_IMAGE_HINT, READ_IMAGE_NO_FEED_HINT
    from nucllei.utils.tool_errors import turn_tools

    tool = _tool(b"x")
    with turn_tools(["feed_image", "read_file"]):
        out = _read(tool, f"/workspace/{name}")
    assert out == (
        f"ERROR: /workspace/{name} は画像・PDF で、read_file はテキストしか読まない。"
        f"次の手: {READ_IMAGE_HINT}"
    )
    assert "feed_image" in READ_IMAGE_HINT
    out = _read(tool, f"/workspace/{name}")
    assert out == (
        f"ERROR: /workspace/{name} は画像・PDF で、read_file はテキストしか読まない。"
        "次の手: PDF なら run_shell の pdftotext (スキャンなら tesseract) で /workspace/ に"
        "テキストを書き出し、そのファイルを read_file で読む。"
    )
    assert "feed_image" not in READ_IMAGE_NO_FEED_HINT
    # 配備が valve で書いた文はそのまま (feed_image の有無で差し替えない)
    tool.valves.image_hint = "配備の案内。"
    assert _read(tool, f"/workspace/{name}").endswith("次の手: 配備の案内。")
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
        # テキストは /workspace へコピーして read_file、データは run_python (2026-10-19 llens 監査 (6))
        assert out.endswith(
            "次の手: テキストなら run_shell の cp で /workspace/ にコピーしてから read_file で"
            "読む。CSV・JSON などのデータを集計するなら run_python でそのパスを開く。"
        ), out
        assert "cat" not in out
    # 拡張子の判定が先 (画像・Office 等はその案内)
    out = _read(tool, "/ext/shared/table.xlsx")
    assert out.startswith("ERROR: /ext/shared/table.xlsx はテキストとして読めない"), out
    out = _read(tool, "/ext/shared/fig.png")
    assert out.startswith("ERROR: /ext/shared/fig.png は画像・PDF で"), out
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


# ---------------------------------------------------------------------------
# 範囲読み・ページング(offset / next_offset はバイト位置)
# ---------------------------------------------------------------------------
_NEXT_RE = re.compile(r"\n\n\(続きあり: next_offset=(\d+)。続きは offset=\d+ で読む\)$")
_MIDLINE = re.compile(
    r"\n\n\(1 (行|レコード)が \d+ 字を超えるので.*の途中で切った。.*\)$"
)


def _page(tool, path, **kw):
    """(先頭行, 本文, next_offset or None, 途中で切ったか)。"""
    text = _read(tool, path, **kw).text
    head, rest = text.split("\n\n", 1)
    m = _NEXT_RE.search(rest)
    nxt = None
    if m:
        rest = rest[: m.start()]
        nxt = int(m.group(1))
    mid = _MIDLINE.search(rest)
    if mid:
        rest = rest[: mid.start()]
    return head, rest, nxt, bool(mid)


def _read_all(tool, path, **kw):
    """next_offset を辿って全ページを集める。[(先頭行, 本文, 途中で切ったか)]"""
    pages = []
    offset = 0
    while True:
        head, body, nxt, mid = _page(tool, path, offset=offset, **kw)
        pages.append((head, body, mid))
        if nxt is None:
            return pages
        assert nxt > offset
        offset = nxt


def test_large_file_is_read_by_range_not_whole():
    # 数十 MB でも全体の大きさで断らず、読むのは先頭の判定分 + 1 ページ分だけ
    line = "あいうえお,12345\n".encode("utf-8")
    data = line * 3_000_000
    tool = _tool(data)
    head, body, nxt, _ = _page(tool, "/workspace/huge.log")
    assert head.startswith(
        f"`/workspace/huge.log` — 文字コード: UTF-8、全 {len(data)} バイトのうち 0〜"
    )
    assert len(body) <= 40000 and body.endswith("\n")
    assert nxt == len(body.encode("utf-8")) and nxt % len(line) == 0
    assert tool.read_bytes <= 40000 * 4
    # 途中のページ: 判定用の先頭 64 KiB + そのページ分
    tool.read_bytes = 0
    n = len(tool.calls)
    head, body, _, _ = _page(tool, "/workspace/huge.log", offset=2_000_000 * len(line))
    assert tool.read_bytes <= 64 * 1024 + 40000 * 4
    assert body.startswith("あいうえお,12345\n")
    reads = [c[1] for c in tool.calls[n:] if c[0] == "/files/read"]
    assert [r["offset"] for r in reads] == [0, 2_000_000 * len(line)]
    assert all(r["length"] <= 40000 * 4 for r in reads)


def test_pages_cut_at_line_breaks_and_chain_without_gaps():
    text = "".join(f"{i:05d} " + "字" * (i % 37) + "\n" for i in range(6000))
    tool = _tool(text.encode("utf-8"))
    pages = _read_all(tool, "/workspace/a.txt", max_chars=3000)
    assert len(pages) > 10
    assert "".join(b for _, b, _ in pages) == text
    for _, body, mid in pages:
        assert body.endswith("\n") and len(body) <= 3000 and not mid
    assert pages[-1][0].endswith(f"〜{len(text.encode('utf-8'))} バイト目")


def test_whole_small_file_has_no_range_in_head():
    out = _read(_tool(b"a\nb\n"), "/workspace/a.txt")
    assert out.text == "`/workspace/a.txt` — 文字コード: UTF-8、全 4 バイト\n\na\nb\n"


def test_a_line_longer_than_the_page_is_cut_mid_line_and_says_so():
    text = "x" * 25 + "\n" + "y" * 3
    tool = _tool(text.encode())
    out = _read(tool, "/workspace/a.txt", max_chars=10)
    assert (
        "(1 行が 10 字を超えるので行の途中で切った。続きはその途中から始まる)"
        in out.text
    )
    pages = _read_all(tool, "/workspace/a.txt", max_chars=10)
    assert [m for _, _, m in pages] == [True, True, False]
    assert "".join(b for _, b, _ in pages) == text


@pytest.mark.parametrize("codec", ["utf-8", "cp932"])
def test_multibyte_chars_survive_page_boundaries(codec):
    # 改行の無い長い行 (文字の境界で切る) と、改行のある行の両方
    text = "漢字かな交じりの文" * 50 + "\n" + "表" * 30 + "\n" + "終わり"
    tool = _tool(text.encode(codec))
    pages = _read_all(tool, "/workspace/a.txt", max_chars=7)
    assert "".join(b for _, b, _ in pages) == text
    label = "UTF-8" if codec == "utf-8" else "cp932"
    assert all(f"文字コード: {label}" in h for h, _, _ in pages)


def test_bom_is_skipped_and_offsets_are_bytes():
    data = b"\xef\xbb\xbf" + b"a,b\n" * 10
    tool = _tool(data)
    pages = _read_all(tool, "/workspace/x.txt", max_chars=9)
    assert "".join(b for _, b, _ in pages) == "a,b\n" * 10
    assert pages[0][0].endswith("全 43 バイトのうち 0〜11 バイト目")


def test_offset_errors():
    tool = _tool("あいう\n".encode("utf-8"))
    out = _read(tool, "/workspace/a.txt", offset=10)
    assert out == (
        "ERROR: offset 10 は末尾を越えている (/workspace/a.txt は全 10 バイト)。"
        "次の手: offset を 0〜9 で渡す。"
    )
    # UTF-8 の文字の途中は断る
    out = _read(tool, "/workspace/a.txt", offset=1)
    assert out.startswith("ERROR: offset 1 は文字の途中")
    assert "next_offset" in out
    out = _read(tool, "/workspace/a.txt", offset="x")
    assert out.startswith("ERROR: offset / max_chars が整数でない")


def test_decode_failure_after_the_probe_is_binary():
    from nucllei.builtin_tools.sandbox import READ_BINARY_HINT

    data = b"ok line\n" * 10000 + b"\xff\xfe\x00 broken\n"
    out = _read(_tool(data), "/workspace/a.txt", offset=80000)
    assert out.startswith("ERROR: /workspace/a.txt はテキストとして読めない")
    assert out.endswith(READ_BINARY_HINT)


def test_old_sandbox_without_range_is_sliced():
    # 範囲読みを知らない sandbox は全体を返す — 手元で切る
    from nucllei.builtin_tools.sandbox import Tools

    data = b"line\n" * 1000
    tool = Tools()

    async def _post(path, payload, timeout_s):
        if path == "/files/stat":
            return {"exists": True, "type": "file", "size": len(data)}
        return {"name": "x", "size": len(data), "b64": base64.b64encode(data).decode()}

    tool._post = _post
    head, body, nxt, _ = _page(tool, "/workspace/a.txt", offset=100, max_chars=12)
    assert body == "line\nline\n" and nxt == 110


# ---------------------------------------------------------------------------
# CSV / TSV(レコードの切れ目で切る・見出しを付け直す)
# ---------------------------------------------------------------------------
def test_csv_is_cut_only_between_records_and_header_is_repeated():
    header = "id,メモ,値\n"
    rows = [f'{i},"複数行の\nメモ {i}",{i * 3}\n' for i in range(400)]
    text = header + "".join(rows)
    tool = _tool(text.encode("utf-8"))
    pages = _read_all(tool, "/workspace/a.csv", max_chars=500)
    assert len(pages) > 5
    first_head, first_body, _ = pages[0]
    assert "見出し" not in first_head and first_body.startswith(header)
    rebuilt = first_body
    for head, body, _ in pages[1:]:
        assert "先頭に 1 行目 (見出し) を付け直した" in head
        assert body.startswith(header)
        rebuilt += body[len(header) :]
    assert rebuilt == text
    # どのページも引用符の数が偶数 = レコードの途中で割れていない
    for _, body, mid in pages:
        assert body.count('"') % 2 == 0 and body.endswith("\n") and not mid
    out = _read(tool, "/workspace/a.csv", max_chars=500)
    assert (out.kind, out.language) == ("code", "csv")


def test_tsv_cp932_header_repeated():
    text = "名前\t値\n" + "".join(f"項目{i}\t{i}\n" for i in range(300))
    tool = _tool(text.encode("cp932"))
    pages = _read_all(tool, "/workspace/a.tsv", max_chars=200)
    assert len(pages) > 3
    assert all(b.startswith("名前\t値\n") for _, b, _ in pages)
    assert "cp932" in pages[1][0] and "見出し" in pages[1][0]


def test_csv_record_longer_than_the_page_says_record():
    text = 'a\n"' + "x" * 50 + '"\n'
    tool = _tool(text.encode())
    head, body, nxt, mid = _page(tool, "/workspace/a.csv", max_chars=20)
    assert body == "a\n" and nxt == 2 and not mid  # 最初のレコードの切れ目まで
    out = _read(tool, "/workspace/a.csv", offset=2, max_chars=20)
    assert "1 レコードが" in out.text and "レコードの途中で切った" in out.text


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
