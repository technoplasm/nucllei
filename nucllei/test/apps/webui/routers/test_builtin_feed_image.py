"""builtin_feed_image の url / path / file_id ソースのユニットテスト。

配備層 (prism-hu/llens の tests/test_feed_image_path.py) から tool と一緒に移設 (#141、2026-10-03)。
pydantic stub / importlib のロードは落とし (nucllei の中なので直接 import する)、resolver の無い
旧 nucllei を見ていたテストも落とした。振る舞いの番兵は全部残す。

引数の意味 (2026-10-03 ken 決定): url = URL (スキーム付き絶対 URL か同一サーバの相対 `/ext/…`)、
path = sandbox の絶対パス、file_id = 添付・生成物。**解釈は引数名だけで決める** — 値の形を見て
別の引数の意味に読み替えない (url="/workspace/…" は URL として解決できない、path="https://…" は
sandbox のパスの形でない、の固定文 ERROR)。

共有 resolver (utils/image_refs: resolve_url / resolve_path / detect_kind / pdf_page_count) は
module 属性の差し替えで偽装する — 解決規則 (許可 root / 上流 / traversal / サイズ上限) の正しさは
test_image_refs_resolve.py が見る。ここで見るのは tool の振る舞いだけ:
引数名どおりの resolver に渡すこと (url → resolve_url、path → resolve_path、url の
/api/v1/files/{id}/content は file として所有者チェックで読む・file_id との排他)、
RefError.code ごとに「何が起きたか + 次の一手」の ERROR 文 (unsupported_form は引数ごとの
固定文) になること、画像は参照そのまま / PDF はページ付き参照で sink に積むこと、
max_pages の打ち切り、**File を 1 件も作らない** (Storage / Files に触れない) こと。
"""

import asyncio
import types

from nucllei.utils.image_refs import (
    PATH_ARG_FORM,
    REF_ARGS_RULE,
    URL_ARG_FORM,
    RefError,
)


def _mod():
    from nucllei.builtin_tools import feed_image

    return feed_image


# --- resolver の偽物 (image_refs の使用面だけ) --------------------------------


class _Loaded:
    def __init__(self, data=b"", content_type="", name="x"):
        self.data = data
        self.content_type = content_type
        self.name = name


def _detect_kind(loaded):
    if loaded.data[:5] == b"%PDF-" or loaded.content_type == "application/pdf":
        return "pdf"
    if loaded.content_type.startswith("image/"):
        return "image"
    return None


class _Resolver:
    """resolve_url (url 引数) / resolve_path (path 引数) の偽物。受ける形の判定は nucllei の
    `_is_url` / `_is_sandbox_path` と同じ (外れれば unsupported_form)。table の値は _Loaded か
    RefError。table に無い ref は not_found。refs は渡された参照、calls は (入口, 参照)。"""

    def __init__(self, table):
        self.table = table
        self.refs: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.users: list[str] = []
        self.kwargs: list[dict] = (
            []
        )  # resolver に渡されたキーワード (feed_image は何も渡さない)

    def _lookup(self, ref):
        hit = self.table.get(ref)
        if hit is None:
            return None, RefError("not_found", "missing")
        if isinstance(hit, RefError):
            return None, hit
        return hit, None

    async def url(self, ref, **kwargs):
        self.refs.append(ref)
        self.calls.append(("url", ref))
        self.kwargs.append(kwargs)
        if not (
            ref.lower().startswith(("http://", "https://")) or ref.startswith("/ext/")
        ):
            return None, RefError("unsupported_form", "not a URL this server resolves")
        return self._lookup(ref)

    async def path(self, ref, user_id, **kwargs):
        self.refs.append(ref)
        self.calls.append(("path", ref))
        self.users.append(user_id)
        self.kwargs.append(kwargs)
        if not ref.startswith(("/workspace/", "/ext/")):
            return None, RefError("unsupported_form", "not a sandbox path")
        return self._lookup(ref)


class _NoStorage:
    """Storage / Files に触れたら落ちる番人 (File を作らないことの検証)。"""

    def __getattr__(self, name):
        raise AssertionError(f"must not touch Storage/Files: .{name}")


def _install(monkeypatch, table, n_pages=5):
    """resolver を偽物に差し替えて (module, 偽 resolver) を返す。"""
    mod = _mod()
    resolver = _Resolver(table)
    monkeypatch.setattr(mod, "Files", _NoStorage())
    monkeypatch.setattr(mod, "Storage", _NoStorage())
    monkeypatch.setattr(mod, "resolve_url", resolver.url)
    monkeypatch.setattr(mod, "resolve_path", resolver.path)
    monkeypatch.setattr(mod, "detect_kind", _detect_kind)
    monkeypatch.setattr(mod, "pdf_page_count", lambda data: n_pages)

    # 配信されている `/ext/…` だけ URL (同じ文字列) を返す偽物。既定は「配信されていない」
    async def _served(path):
        return path if path in getattr(resolver, "served", ()) else None

    monkeypatch.setattr(mod, "url_for_mounted_path", _served)
    return mod, resolver


PNG = _Loaded(b"\x89PNG\r\n\x1a\n" + b"\0" * 16, "image/png", "p0145-f1.jpg")
PDF = _Loaded(b"%PDF-1.7 fake", "application/pdf", "10-14.pdf")
# 参照の形: URL (read_doc の相対 /ext/ 形 / 既存チャットの絶対 /ext/ 形 / 原本 URL) と sandbox の fs。
ORIGIN = "https://llens.example"
HIS = "https://med-info.med.hokudai.ac.jp/his-info"
FIG_REL = "/ext/cage/static/risk_manual/figs/p0145-f1.jpg"  # read_doc の戻り値の形
FIG_URL = f"{ORIGIN}{FIG_REL}"
HIS_PDF_URL = f"{HIS}/log_write.aspx?LI_UID=G_UID&LI_URL=x.pdf"
HIS_PNG_URL = f"{HIS}/log_write.aspx?LI_URL=fig.png"
TABLE = {
    FIG_URL: PNG,
    FIG_REL: PNG,
    "/ext/cage/static/kansen_manual/docs/10-14.pdf": PDF,  # url でも path でも同じ物
    "/workspace/out/plot.png": PNG,
    HIS_PDF_URL: PDF,
    HIS_PNG_URL: PNG,
    f"{ORIGIN}/ext/cage/static/kansen_manual/docs/10-14.pdf": PDF,
    "/ext/cage/doc/a.md": _Loaded(b"# a", "text/markdown", "a.md"),
}

URL_FORM = f"{URL_ARG_FORM} {REF_ARGS_RULE}"
PATH_FORM = f"{PATH_ARG_FORM} {REF_ARGS_RULE}"
SHOW_PATH = (
    "ユーザーに見せるなら、fetch_from_workspace が使えるならそれに path を渡し、"
    "返ったリンクを本文に書く。"
)
LINK_HINT = "ユーザーに示すなら [ラベル]("


def _ref_kw(ref, arg=None):
    """参照を正の引数名で渡す: URL (スキーム付き / 相対 `/ext/`) は url=、sandbox のパスは path=。
    `/ext/…` は両方の形 (同じファイル) なので、path として渡したいテストは `arg="path"` で指定。"""
    if arg:
        return {arg: ref}
    r = ref.strip().lower()
    return (
        {"url": ref}
        if r.startswith(("http://", "https://", "/ext/"))
        else {"path": ref}
    )


def _run(
    monkeypatch, table=TABLE, *, n_pages=5, max_pages=None, max_image_mb=None, **kwargs
):
    """feed_image を呼び、(戻り文字列, sink, 偽 resolver) を返す。"""
    mod, resolver = _install(monkeypatch, table, n_pages)
    tools = mod.Tools()
    if max_pages is not None:
        tools.valves.max_pages = max_pages
    if max_image_mb is not None:
        tools.valves.max_image_mb = max_image_mb
    fed: list[str] = []
    result = asyncio.run(
        tools.feed_image(__user__={"id": "u1"}, __feed_image__=fed.append, **kwargs)
    )
    return result, fed, resolver


# --- 画像 -----------------------------------------------------------------


def test_image_path_pushes_the_ref_as_is(monkeypatch):
    """絶対 URL (自オリジンの /ext/ 形・原本 URL) も sandbox のパスも、resolver にそのまま
    渡し、そのまま sink に積む (tool は URL を書き換えない)。"""
    for ref in (
        FIG_URL,
        FIG_REL,
        "/workspace/out/plot.png",
        HIS_PNG_URL,  # 拡張子を持たない配信口 (2026-09-04)
    ):
        result, fed, resolver = _run(monkeypatch, **_ref_kw(ref))
        assert fed == [ref], ref
        assert resolver.refs == [ref]
        assert not result.startswith("ERROR") and "自分の目に入れた" in result
        assert "ユーザーには表示されない" in result


def test_path_is_stripped(monkeypatch):
    result, fed, _r = _run(monkeypatch, path="  /workspace/out/plot.png \n")
    assert fed == ["/workspace/out/plot.png"] and not result.startswith("ERROR")


# --- url / path の意味 (2026-10-03 ken 決定): 引数名だけで解釈する ------------------


def test_url_takes_an_absolute_url(monkeypatch):
    for ref in (FIG_URL, HIS_PDF_URL):
        result, fed, resolver = _run(monkeypatch, url=ref)
        assert resolver.refs == [ref], ref
        assert fed and fed[0].startswith(ref) and not result.startswith("ERROR"), (
            ref,
            result,
        )


def test_url_takes_a_same_server_relative_ext_url(monkeypatch):
    """read_doc の戻り値 (相対 `/ext/…`) をそのまま resolver へ渡し、そのまま積む。ホストを補わない。"""
    for ref, kw, want in (
        (FIG_REL, {}, [FIG_REL]),
        (
            "/ext/cage/static/kansen_manual/docs/10-14.pdf",
            {"pages": "2"},
            ["/ext/cage/static/kansen_manual/docs/10-14.pdf#page=2"],
        ),
    ):
        result, fed, resolver = _run(monkeypatch, url=ref, **kw)
        assert resolver.refs == [ref] and fed == want, (ref, result)
        assert not result.startswith("ERROR") and "配備の不備" not in result, (
            ref,
            result,
        )


def test_path_takes_a_sandbox_absolute_path(monkeypatch):
    for ref in (
        "/workspace/out/plot.png",
        "/ext/cage/static/kansen_manual/docs/10-14.pdf",
    ):
        result, fed, resolver = _run(monkeypatch, path=ref)
        assert resolver.refs == [ref] and resolver.users == ["u1"], ref
        assert fed and fed[0].startswith(ref) and not result.startswith("ERROR"), (
            ref,
            result,
        )


def test_url_given_to_path_is_not_reinterpreted(monkeypatch):
    """path に URL (絶対でも相対 /ext/ でも) → resolve_path に渡り (URL としては取得しない)、
    sandbox のパスの形でない、の固定文。"""
    for ref in (FIG_URL, HIS_PNG_URL, "/api/v1/files/abc/content"):
        result, fed, resolver = _run(monkeypatch, path=ref)
        assert fed == [] and resolver.calls == [("path", ref)], ref
        assert result == f"ERROR: {ref} は sandbox のパスの形でない。{PATH_FORM}", (
            ref,
            result,
        )
    # 相対 `/ext/…` は sandbox のパスでもある (同じ文字列・同じファイル) — path として読める
    result, fed, resolver = _run(monkeypatch, path=FIG_REL)
    assert resolver.calls == [("path", FIG_REL)] and fed == [FIG_REL]
    assert not result.startswith("ERROR")


def test_sandbox_path_given_to_url_is_not_reinterpreted(monkeypatch):
    """url に /workspace/… → resolve_url に渡り (sandbox の fs としては読まない)、
    URL として解決できない、の固定文。(`/ext/…` は URL でもあるので対象外 — 同じファイル)"""
    for ref in (
        "/workspace/out/plot.png",
        "/mnt/cage/static/kansen_manual/docs/10-14.pdf",  # 旧 RO 置き場。もう何でもない
    ):
        result, fed, resolver = _run(monkeypatch, url=ref)
        assert fed == [] and resolver.calls == [("url", ref)], ref
        assert result == f"ERROR: {ref} は URL として解決できない。{URL_FORM}", (
            ref,
            result,
        )


def test_error_text_is_fixed_per_argument_not_per_value(monkeypatch):
    """値を調べて行き先を出し分けない: url の失敗文は値によらず同じ形、path も同じ。
    他の tool 名・別の引数への誘導を含まない。"""
    for bad in (
        "/workspace/x.png",
        "/etc/x.pdf",
        "relative/x.png",
        "ftp://h/x.png",
        "//evil.example.com/x.png",
    ):
        result, fed, resolver = _run(monkeypatch, url=bad)
        assert fed == [] and resolver.calls == [("url", bad)], bad
        assert result == f"ERROR: {bad} は URL として解決できない。{URL_FORM}", (
            bad,
            result,
        )
        assert "path=" not in result and "path に" not in result, bad
    for bad in (
        "https://h/x.png",
        "/mnt/cage/x.png",  # 2026-10-03 までの RO 置き場。もう何でもない
        "relative/x.png",
        "/opt/x.png",
        "/etc/passwd",
    ):
        result, fed, resolver = _run(monkeypatch, path=bad)
        assert fed == [] and resolver.calls == [("path", bad)], bad
        assert result == f"ERROR: {bad} は sandbox のパスの形でない。{PATH_FORM}", (
            bad,
            result,
        )
        assert "url=" not in result and "url に" not in result, bad


def test_url_and_path_together(monkeypatch):
    """両方に値があれば (同じ値でも) どちらか一方にさせる。片方が空なら値のある方だけを使う。"""
    for kw in (
        dict(url=FIG_URL, path="/workspace/out/plot.png"),
        dict(url=FIG_URL, path=FIG_URL),
    ):
        result, fed, resolver = _run(monkeypatch, **kw)
        assert (
            fed == []
            and resolver.refs == []
            and result.startswith("ERROR")
            and "同時" in result
        ), kw
    for kw, want in (
        (dict(url=FIG_URL, path=""), [FIG_URL]),
        (dict(url="", path="/workspace/out/plot.png"), ["/workspace/out/plot.png"]),
    ):
        result, fed, _r = _run(monkeypatch, **kw)
        assert fed == want and not result.startswith("ERROR"), (kw, result)
    result, fed, resolver = _run(monkeypatch, url="", path="")
    assert fed == [] and resolver.refs == [] and result.startswith("ERROR")


def test_empty_value_is_the_fixed_text_of_the_argument(monkeypatch):
    result, fed, resolver = _run(monkeypatch, url="  ")
    assert (
        result == f"ERROR: (空) は URL として解決できない。{URL_FORM}"
        and resolver.refs == []
    )
    result, fed, resolver = _run(monkeypatch, path="")
    assert (
        result == f"ERROR: (空) は sandbox のパスの形でない。{PATH_FORM}"
        and resolver.refs == []
    )


def test_url_is_stripped(monkeypatch):
    result, fed, _r = _run(monkeypatch, url=f"  {FIG_URL} \n")
    assert fed == [FIG_URL] and not result.startswith("ERROR")


def test_docstring_names_url_and_path_with_their_fixed_meanings():
    """モデルに見える docstring は正の形だけ: url = 絶対 URL か同一サーバの相対 `/ext/…`、
    path = sandbox の絶対パス。"""
    doc = _mod().Tools.feed_image.__doc__
    assert ":param url:" in doc and ":param path:" in doc and ":param file_id:" in doc
    url_line = next(
        ln for ln in doc.splitlines() if ln.strip().startswith(":param url:")
    )
    path_line = next(
        ln for ln in doc.splitlines() if ln.strip().startswith(":param path:")
    )
    assert "URL" in url_line and "/ext/" in url_line and "/workspace/" not in url_line
    assert "/workspace/" in path_line and "URL" not in path_line
    assert "ホストを補わない" in doc
    assert "取り違え" not in doc and "どちらでも" not in doc
    assert "BE 上" not in doc and "添付・生成物" in doc
    # 2026-10-04: `#page=N` の扱い等の細目は README_API §5.7.1 へ移した (毎ターン送る
    # token の節約)。docstring に残すのは引数の形・戻り値・ERROR の形だけ
    assert "受け取った形のまま" in doc and '"ERROR: ..."' in doc
    assert "先頭 3 ページと総ページ数が返る" in doc and '"4-6"' in doc
    assert "cage" not in doc.lower() and "棚" not in doc


def test_image_path_rejects_pages(monkeypatch):
    result, fed, _r = _run(monkeypatch, path="/workspace/out/plot.png", pages="1-3")
    assert fed == [] and result.startswith("ERROR") and "PDF" in result


def test_image_size_cap(monkeypatch):
    result, fed, _r = _run(
        monkeypatch, path="/workspace/out/plot.png", max_image_mb=0.000001
    )
    assert fed == [] and result.startswith("ERROR") and "大きすぎる" in result


# --- PDF ------------------------------------------------------------------


def test_pdf_path_pushes_page_refs_without_creating_files(monkeypatch):
    ref = "/ext/cage/static/kansen_manual/docs/10-14.pdf"
    result, fed, _r = _run(monkeypatch, path=ref, pages="2-3", n_pages=10)
    assert fed == [f"{ref}#page=2", f"{ref}#page=3"]
    assert not result.startswith("ERROR")
    assert "p.2, p.3" in result and "全 10 ページ" in result


def test_pdf_path_without_pages_takes_from_the_top_and_truncates(monkeypatch):
    ref = HIS_PDF_URL
    result, fed, _r = _run(monkeypatch, **_ref_kw(ref), n_pages=10, max_pages=3)
    assert fed == [f"{ref}#page=1", f"{ref}#page=2", f"{ref}#page=3"]
    assert "上限 3 ページで打ち切った" in result


def test_pdf_path_pages_beyond_the_end_are_clipped(monkeypatch):
    ref = "/ext/cage/static/kansen_manual/docs/10-14.pdf"
    result, fed, _r = _run(monkeypatch, path=ref, pages="4-9", n_pages=5)
    assert fed == [f"{ref}#page=4", f"{ref}#page=5"]
    assert not result.startswith("ERROR")


def test_page_fragment_in_path_is_honoured(monkeypatch):
    """`…/x.pdf#page=3` の literal (自分が前に積んだ参照) を渡されても壊れない。URL でも同じ。"""
    for ref in (
        "/ext/cage/static/kansen_manual/docs/10-14.pdf",
        f"{ORIGIN}/ext/cage/static/kansen_manual/docs/10-14.pdf",
    ):
        result, fed, resolver = _run(monkeypatch, **_ref_kw(f"{ref}#page=3"), n_pages=5)
        assert resolver.refs == [ref], ref  # resolver には #page 無しで渡る
        assert fed == [f"{ref}#page=3"] and not result.startswith("ERROR"), ref


# --- 拒否 -----------------------------------------------------------------


def test_rejects_relative_paths(monkeypatch):
    for bad in ("ext/cage/x.png", "cage/static/x.png", "ftp://h/x.png", ""):
        for kw in ({"path": bad}, {"url": bad}):
            result, fed, resolver = _run(monkeypatch, **kw)
            assert fed == [], f"must not feed {kw!r}"
            assert result.startswith("ERROR"), f"must reject {kw!r}"


def test_unknown_upstream_is_one_fixed_text_without_a_reason(monkeypatch):
    """unknown_upstream は detail の文言 (変わりうる) で分岐しない。reason の無いものは 1 文面。"""
    table = dict(TABLE)
    cases = {
        "http://172.17.0.1:13303/static/x.jpg": "host resolves to this server itself",
        "https://example.com/x.png": "some other detail",
        f"{ORIGIN}/ext/unknown/x.png": "name=unknown",
        "/ext/his": "",
    }
    for bad, detail in cases.items():
        table[bad] = RefError("unknown_upstream", detail)
    for bad in cases:
        result, fed, resolver = _run(monkeypatch, table, url=bad)
        assert resolver.refs == [bad], bad
        assert fed == [] and result == (
            f"ERROR: {bad} は取得を許可されていない URL。" + URL_ARG_FORM
        ), (bad, result)


def test_unknown_upstream_is_told_apart_by_reason(monkeypatch):
    """`RefError.reason` で言い分ける (detail は見ない)。"""
    want = {
        "server_self": "このサーバ自身",
        # unknown_name / not_ext_form は 2026-10-03 に無くした — 知らない reason は 1 文面
        "unknown_name": "取得を許可されていない URL",
        "not_ext_form": "取得を許可されていない URL",
    }
    for reason, phrase in want.items():
        err = RefError("unknown_upstream", "detail that must not matter", reason=reason)
        result, fed, _r = _run(monkeypatch, {"/ext/x/y.png": err}, url="/ext/x/y.png")
        assert fed == [] and phrase in result and result.endswith(URL_ARG_FORM), (
            reason,
            result,
        )


class _Rec:
    def __init__(self, fid, user_id, filename, content_type):
        self.id = fid
        self.user_id = user_id
        self.filename = filename
        self.meta = {"content_type": content_type}
        self.path = "stored/" + filename


def _run_with_files(
    monkeypatch, recs, raw=b"%PDF-1.7 fake", n_pages=4, max_pages=None, **kwargs
):
    """Files を偽物にして feed_image を呼ぶ (file として読む経路用)。"""
    mod, resolver = _install(monkeypatch, TABLE, n_pages=n_pages)
    monkeypatch.setattr(
        mod, "Files", types.SimpleNamespace(get_file_by_id=lambda fid: recs.get(fid))
    )
    monkeypatch.setattr(mod, "_read_raw", lambda r: raw)
    tools = mod.Tools()
    if max_pages is not None:
        tools.valves.max_pages = max_pages
    fed: list[str] = []
    result = asyncio.run(
        tools.feed_image(__user__={"id": "u1"}, __feed_image__=fed.append, **kwargs)
    )
    return result, fed, resolver


def test_url_of_the_internal_file_endpoint_is_read_as_the_file(monkeypatch):
    """url の `/api/v1/files/{id}/content` (相対でも絶対でも) は URL のルーティングとして file に
    当たる — resolver では取りに行かず、file_id と同じ所有者チェックで読む
    (send_to_workspace(url=) と同じ扱い)。`#page=N` は pages の既定になる。"""
    recs = {"f1": _Rec("f1", "u1", "scan.pdf", "application/pdf")}
    for u, want in (
        (
            "/api/v1/files/f1/content",
            ["/api/v1/files/f1/content#page=1", "/api/v1/files/f1/content#page=2"],
        ),
        (
            f"{ORIGIN}/api/v1/files/f1/content#page=3",
            ["/api/v1/files/f1/content#page=3"],
        ),
    ):
        result, fed, resolver = _run_with_files(monkeypatch, recs, url=u)
        assert resolver.calls == [], u
        assert fed[: len(want)] == want and "scan.pdf" in result, (u, result)


def test_url_of_the_internal_file_endpoint_keeps_the_owner_check(monkeypatch):
    recs = {"f1": _Rec("f1", "someone-else", "scan.pdf", "application/pdf")}
    fixed = "ERROR: file_id {} のレコードが無い、またはアクセス権が無い。file_id は添付・生成物の id。"
    result, fed, _r = _run_with_files(monkeypatch, recs, url="/api/v1/files/f1/content")
    assert fed == [] and result == fixed.format("f1"), result
    result, fed, _r = _run_with_files(monkeypatch, {}, url="/api/v1/files/nope/content")
    assert fed == [] and result == fixed.format("nope"), result
    # `/content` の無い形は file に当てない — URL として resolve_url に渡り、解決できない
    result, fed, resolver = _run_with_files(monkeypatch, recs, url="/api/v1/files/f1")
    assert fed == [] and resolver.calls == [("url", "/api/v1/files/f1")]
    assert result.startswith("ERROR: /api/v1/files/f1 は URL として解決できない")


def test_success_on_a_sandbox_path_does_not_suggest_pasting_it(monkeypatch):
    """fs (`/workspace/…`、配信されていない `/ext/…`) はブラウザに意味が無い —
    `![…](/workspace/…)` を勧めない。URL のときだけ本文への再掲を勧める。"""
    result, fed, _r = _run(monkeypatch, path="/workspace/out/plot.png")
    assert fed == ["/workspace/out/plot.png"] and "](" not in result
    assert "本文に貼れない" in result and SHOW_PATH in result
    # nucllei 側に無い /ext (doc 等) も同じ (偽物の既定は「配信されていない」)
    result, fed, _r = _run(monkeypatch, path=FIG_REL)
    assert "本文に貼れない" in result and "](" not in result
    for ref in (FIG_URL, FIG_REL):
        result, fed, _r = _run(monkeypatch, url=ref)
        assert f"![…]({ref})" in result and "本文に貼れない" not in result, ref


def test_pages_overrides_the_page_fragment_in_the_url(monkeypatch):
    """docstring の約束: `#page=N` 付き literal はそのまま渡せる。pages を付ければそちらが優先。"""
    for ref in (
        f"{ORIGIN}/ext/cage/static/kansen_manual/docs/10-14.pdf",
        "/ext/cage/static/kansen_manual/docs/10-14.pdf",
    ):
        result, fed, _r = _run(monkeypatch, url=f"{ref}#page=3", pages="1-2", n_pages=5)
        assert fed == [f"{ref}#page=1", f"{ref}#page=2"] and not result.startswith(
            "ERROR"
        ), ref


def test_unsupported_form_from_the_resolver_uses_the_fixed_text_of_the_argument(
    monkeypatch,
):
    """resolver が unsupported_form を返したときも、どの引数で来たかで固定文を選ぶ。"""
    result = _explained(monkeypatch, "unsupported_form", ref="/ext/x.png", arg="path")
    assert result == f"ERROR: /ext/x.png は sandbox のパスの形でない。{PATH_FORM}"
    result = _explained(monkeypatch, "unsupported_form", ref=f"{ORIGIN}/x.png")
    assert result == f"ERROR: {ORIGIN}/x.png は URL として解決できない。{URL_FORM}"


def test_feed_image_does_not_pass_max_bytes(monkeypatch):
    """画像 inline 用の既定上限のまま読む — fetch_url と違い max_bytes を渡さない。"""
    for ref in (FIG_URL, "/workspace/out/plot.png", HIS_PDF_URL):
        _result, _fed, resolver = _run(monkeypatch, **_ref_kw(ref))
        assert resolver.kwargs == [{}], ref


# --- RefError.code ごとの ERROR 文 (番兵)。文面はモデルがユーザーへ転記する前提 ----------
# 実走 (2026-09-06): 32.6 MB の PDF が上限 25 MB に当たったのを、束ねた ERROR 文のせいで
# モデルが「リンク切れ」と誤報した。code ごとに「何が起きたか + 次の一手」を言う。


def _explained(monkeypatch, code, ref=HIS_PDF_URL, arg=None, **kw):
    kw.setdefault("detail", "")
    result, fed, resolver = _run(
        monkeypatch, {ref: RefError(code, **kw)}, **_ref_kw(ref, arg)
    )
    assert resolver.refs == [ref], code
    assert fed == [] and result.startswith("ERROR"), (code, result)
    assert "cage" not in result.lower() and "EXT_" not in result, (code, result)
    return result


def test_too_large_names_size_and_limit_in_mb_and_hands_the_url_to_the_user(
    monkeypatch,
):
    """32,646,426 B → 31.1 MB / limit 26,214,400 B → 25.0 MB (小数 1 桁、MiB 換算)。
    size_exact=False (Content-Length 無し、size は打ち切り時点の受信量 = 下限) なので「少なくとも」。
    URL なら: 画像は取り込んで縮小 (send_to_workspace → run_python)、要らなければ URL を案内。"""
    result = _explained(
        monkeypatch,
        "too_large",
        size=32_646_426,
        limit=25 * 1024 * 1024,
        size_exact=False,
    )
    assert (
        f"{HIS_PDF_URL} は少なくとも 31.1 MB あり、取得の上限 25.0 MB を超えている"
        in result
    )
    assert "自分の目に入れられない" in result
    assert (
        "send_to_workspace" in result
        and "run_python" in result
        and "/workspace/" in result
    )
    assert f"{LINK_HINT}{HIS_PDF_URL}) のリンクにする" in result
    assert "ブラウザでは開ける" in result
    assert "リンク切れ" not in result and "存在しない" not in result


def test_too_large_on_a_sandbox_path_does_not_talk_about_urls(monkeypatch):
    """`/ext/…` `/workspace/…` はブラウザに意味が無い — 「リンクで示す」と言わず、
    run_python で直接開いて縮小 / fetch_from_workspace で渡す、を言う。"""
    for ref in ("/ext/doc/static/big.png", "/workspace/out/huge.pdf"):
        result = _explained(
            monkeypatch,
            "too_large",
            ref=ref,
            arg="path",
            size=32_646_426,
            limit=25 * 1024 * 1024,
            size_exact=True,
        )
        assert f"{ref} は 31.1 MB あり" in result, ref  # fs の stat 由来 = 実サイズ
        assert LINK_HINT not in result and "ブラウザ" not in result, (
            ref,
            result,
        )
        assert "send_to_workspace" not in result, (ref, result)
        assert SHOW_PATH in result, ref
        assert "run_python" in result and "fetch_from_workspace" in result, ref


def test_too_large_says_at_least_only_when_the_size_is_not_exact(monkeypatch):
    """size_exact=True (Content-Length / stat / Files) なら「N MB あり」、False なら
    「少なくとも N MB あり」。limit からの距離では判定しない。"""
    limit = 25 * 1024 * 1024
    result = _explained(
        monkeypatch, "too_large", size=limit + 300 * 1024, limit=limit, size_exact=True
    )
    assert f"{HIS_PDF_URL} は 25.3 MB あり、取得の上限 25.0 MB を超えている" in result
    assert "少なくとも" not in result
    for kw in (dict(size_exact=False), {}):
        result = _explained(
            monkeypatch, "too_large", size=limit + 64 * 1024, limit=limit, **kw
        )
        assert (
            f"{HIS_PDF_URL} は少なくとも 25.1 MB あり、取得の上限 25.0 MB を超えている"
            in result
        ), kw


def test_too_large_without_numbers_still_says_it_is_the_size_cap(monkeypatch):
    for kw in ({}, dict(size_exact=True), dict(size_exact=False)):
        result = _explained(monkeypatch, "too_large", **kw)
        assert (
            "取得の上限" in result and "超えている" in result and LINK_HINT in result
        ), kw
        assert "少なくとも" not in result and "MB あり" not in result, (kw, result)


def test_unreachable_on_a_sandbox_path_does_not_talk_about_urls(monkeypatch):
    result = _explained(
        monkeypatch, "upstream_unreachable", ref="/workspace/out/plot.png"
    )
    assert LINK_HINT not in result and "run_shell が使えるなら ls" in result


def test_image_over_the_image_cap_tells_how_to_shrink_by_ref_shape(monkeypatch):
    """8 MB 超・resolver 上限以下の画像: fs は run_python が直接開ける、URL は先に取り込む。"""
    result, fed, _r = _run(
        monkeypatch, path="/workspace/out/plot.png", max_image_mb=0.000001
    )
    assert fed == [] and "run_python" in result and "send_to_workspace" not in result
    result, fed, _r = _run(monkeypatch, **_ref_kw(HIS_PNG_URL), max_image_mb=0.000001)
    assert fed == [] and "send_to_workspace" in result and "run_python" in result


def test_not_found_is_the_only_code_that_says_dead_link(monkeypatch):
    """404 も、table に無い ref (偽 resolver が not_found にする) も同じ文。
    path で来たもの (sandbox のファイル) はリンク切れと言わず run_shell の ls を挙げる。"""
    result = _explained(monkeypatch, "not_found")
    assert (
        f"{HIS_PDF_URL} は存在しない (404 / ファイル無し)" in result
        and "次の手: ユーザーにはリンク切れと伝える。" in result
    )
    result, fed, _r = _run(monkeypatch, path="/ext/cage/static/missing.png")
    # `/ext/…` は fetch_from_workspace と同文 (ken 2026-10-04)
    assert fed == [] and result == (
        "ERROR: /ext/cage/static/missing.png はこのサーバの /ext に無い (存在しないか、"
        "sandbox にだけ mount されている)。次の手: 中身が要るなら sandbox で読んで使う。"
    )
    assert "リンク切れ" not in result


def test_unfollowable_redirect_asks_for_the_target_page(monkeypatch):
    """reason 無しの 3xx = 転送先が URL として組めなかった (redirect 自体は 3 回まで辿る)。"""
    for status in (301, 302):
        result = _explained(
            monkeypatch, "upstream_status", ref=f"{HIS}/dir", status=status
        )
        assert (
            f"redirect ({status})" in result
            and "転送先が開ける URL ではなかった" in result
        )
        assert "次の手: 転送先のページを直接指す URL を渡す。" in result
        assert "辿らない" not in result and "リンク切れ" not in result


def test_server_error_status_suggests_retry_or_handing_over_the_url(monkeypatch):
    for status in (500, 503, 403):
        result = _explained(monkeypatch, "upstream_status", status=status)
        assert f"上流が {status} を返した" in result, status
        assert "時間を置いて再試行" in result and LINK_HINT in result, status
        assert "redirect" not in result, status


def test_unreachable_upstream_says_the_web_is_not_reachable_from_here(monkeypatch):
    result = _explained(monkeypatch, "upstream_unreachable")
    assert "のホストに届かない" in result and "この配備からは到達できない" in result
    assert LINK_HINT in result and "リンク切れ" not in result


def test_traversal_code_says_out_of_bounds(monkeypatch):
    """url なら url の形の文、path なら path の形の文を添える (行き先の規則は付けない)。"""
    result = _explained(monkeypatch, "traversal", ref=f"{ORIGIN}/ext/his/../x")
    assert "許可された範囲の外" in result and result.endswith(URL_ARG_FORM)
    for ref in ("/workspace/../etc/passwd", "/ext/../secret"):
        result = _explained(monkeypatch, "traversal", ref=ref, arg="path")
        assert "許可された範囲の外" in result and result.endswith(PATH_ARG_FORM), ref


def test_workspace_unavailable_points_to_list_workspace(monkeypatch):
    result = _explained(
        monkeypatch,
        "workspace_unavailable",
        ref="/workspace/out/plot.png",
        detail="sandbox down",
    )
    assert (
        "sandbox" in result
        and "list_workspace" in result
        and "sandbox down" not in result
    )


def test_unknown_code_falls_back_to_the_bundled_text(monkeypatch):
    result = _explained(
        monkeypatch, "something_new", detail="internal detail must not leak"
    )
    assert "読めない" in result and "internal detail" not in result


def test_general_url_reasons_have_their_own_text(monkeypatch):
    """reason: server_self / redirect_self / too_many_redirects / unresolvable。"""
    for code, reason, kw, phrase in (
        ("unknown_upstream", "server_self", {}, "このサーバ自身"),
        (
            "upstream_status",
            "redirect_self",
            {"status": 302},
            "転送先が このサーバ自身",
        ),
        ("upstream_status", "too_many_redirects", {"status": 302}, "4 回以上"),
        ("upstream_unreachable", "unresolvable", {}, "ホスト名を解決できない"),
    ):
        e = RefError(code, "detail that must not matter", reason=reason, **kw)
        result, fed, _r = _run(
            monkeypatch, {"http://172.16.9.9/x.png": e}, url="http://172.16.9.9/x.png"
        )
        assert fed == [] and result.startswith("ERROR") and phrase in result, (
            reason,
            result,
        )


def test_non_image_non_pdf_is_an_error_pointing_to_text_tools(monkeypatch):
    """読めた結果がテキストなら、来た引数に応じた読み方 (path → run_shell、url → fetch_url)。"""
    result, fed, _r = _run(monkeypatch, path="/ext/cage/doc/a.md")
    assert fed == [] and result.startswith("ERROR")
    assert "run_shell" in result and "fetch_url" not in result
    table = dict(TABLE)
    table["/ext/cage/doc/a.md"] = _Loaded(b"# a", "text/markdown", "a.md")
    result, fed, _r = _run(monkeypatch, table, url="/ext/cage/doc/a.md")
    assert fed == [] and "fetch_url(url=…)" in result and "run_shell" not in result


def test_path_exclusive_with_file_id_and_filename(monkeypatch):
    for extra in ({"file_id": "some-uuid"}, {"filename": "x"}):
        for src in ({"path": "/workspace/out/plot.png"}, {"url": FIG_URL}):
            result, fed, resolver = _run(monkeypatch, **src, **extra)
            assert fed == [] and resolver.calls == [], (src, extra)
            assert result.startswith("ERROR") and "同時に指定できない" in result, (
                src,
                extra,
            )


def test_no_source_at_all_is_an_error(monkeypatch):
    result, fed, _r = _run(monkeypatch)
    assert fed == [] and result.startswith("ERROR") and "path" in result


def test_requires_user_and_feed_channel(monkeypatch):
    mod, resolver = _install(monkeypatch, TABLE)
    assert asyncio.run(
        mod.Tools().feed_image(url=FIG_URL, __feed_image__=lambda r: None)
    ).startswith("ERROR: ユーザー情報")
    assert "画像を目に入れる経路が使えない" in asyncio.run(
        mod.Tools().feed_image(url=FIG_URL, __user__={"id": "u1"})
    )
    assert resolver.refs == []


# --- file_id の PDF も File を作らない (#139) ---------------------------------


def test_file_id_pdf_pushes_page_refs_on_the_internal_file_url(monkeypatch):
    rec = _Rec("f1", "u1", "scan.pdf", "application/pdf")
    result, fed, _r = _run_with_files(
        monkeypatch, {"f1": rec}, n_pages=4, max_pages=2, file_id="f1"
    )
    assert fed == ["/api/v1/files/f1/content#page=1", "/api/v1/files/f1/content#page=2"]
    assert "scan.pdf" in result and "全 4 ページ" in result and "打ち切った" in result


def test_no_args_targets_attached_pdfs_only_not_images(monkeypatch):
    """両方省略は添付 PDF だけ (添付画像は既にモデルの入力に入っているので二重に食わせない)。"""
    recs = {
        "pdf1": _Rec("pdf1", "u1", "scan.pdf", "application/pdf"),
        "img1": _Rec("img1", "u1", "photo.jpg", "image/jpeg"),
    }
    files = [
        {
            "id": "img1",
            "name": "photo.jpg",
            "file": {"id": "img1", "meta": {"content_type": "image/jpeg"}},
        },
        {
            "id": "pdf1",
            "name": "scan.pdf",
            "file": {"id": "pdf1", "meta": {"content_type": "application/pdf"}},
        },
    ]
    result, fed, _r = _run_with_files(monkeypatch, recs, n_pages=1, __files__=files)
    assert fed == ["/api/v1/files/pdf1/content#page=1"] and "photo.jpg" not in result
    # 画像だけの添付なら「対象なし」— file_id を明示すれば見られる旨を言う
    result, fed, _r = _run_with_files(monkeypatch, recs, n_pages=1, __files__=files[:1])
    assert (
        fed == []
        and result.startswith("ERROR")
        and "file_id" in result
        and "PDF" in result
    )


def test_file_id_image_is_pushed_by_id_with_the_size_cap(monkeypatch):
    rec = _Rec("f1", "u1", "photo.jpg", "image/jpeg")
    result, fed, _r = _run_with_files(
        monkeypatch, {"f1": rec}, raw=b"\xff\xd8\xff" + b"\0" * 8, file_id="f1"
    )
    assert fed == ["f1"] and "file_id=f1" in result
    # 見せるなら本文に書く形をそのまま渡す (2026-10-04)
    assert "見せるなら本文に ![photo.jpg](/api/v1/files/f1/content) を書く" in result
    mod, _r = _install(monkeypatch, TABLE)
    monkeypatch.setattr(
        mod, "Files", types.SimpleNamespace(get_file_by_id=lambda fid: rec)
    )
    monkeypatch.setattr(
        mod, "_read_raw", lambda r: b"\xff\xd8\xff" + b"\0" * (2 * 1024 * 1024)
    )
    tools = mod.Tools()
    tools.valves.max_image_mb = 1
    fed = []
    result = asyncio.run(
        tools.feed_image(file_id="f1", __user__={"id": "u1"}, __feed_image__=fed.append)
    )
    assert fed == [] and "大きすぎる" in result and "縮小" in result
    assert "次の手: send_to_workspace と run_python が使えるなら" in result


def test_file_id_owner_check_is_fail_closed(monkeypatch):
    for owner in ("someone-else", None, ""):
        rec = _Rec("f1", owner, "scan.pdf", "application/pdf")
        result, fed, _r = _run_with_files(monkeypatch, {"f1": rec}, file_id="f1")
        assert fed == [] and "アクセス権が無い" in result, owner


def test_success_on_a_served_ext_path_suggests_the_same_string_as_url(monkeypatch):
    """`/ext/…` で nucllei 側にも実体がある path は同じ文字列が URL (配信されている) —
    url= と同じく本文に `![…](/ext/…)` を書けば見せられる、と案内する (llens 指摘 2026-10-03)。"""
    mod, resolver = _install(monkeypatch, TABLE)
    resolver.served = {FIG_REL}
    fed: list[str] = []
    result = asyncio.run(
        mod.Tools().feed_image(
            path=FIG_REL,
            __user__={"id": "u1"},
            __feed_image__=fed.append,
        )
    )
    assert fed == [FIG_REL]
    assert f"![…]({FIG_REL})" in result and "本文に貼れない" not in result
    assert "配信されている" in result
