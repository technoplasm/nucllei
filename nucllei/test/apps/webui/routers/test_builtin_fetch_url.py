"""builtin_fetch_url (Web のページと同一サーバの /ext/… を URL で 1 ページずつ辿る) のユニットテスト。

配備層 (prism-hu/llens の tests/test_fetch_url.py) から tool と一緒に移設 (#141、2026-10-03)。
pydantic stub / importlib のロードは落とし (nucllei の中なので直接 import する)、resolver の無い
旧 nucllei を見ていたテストも落とした。振る舞いの番兵は全部残す。

**実 HTTP は一切叩かない** — ネットワークの境界 `resolve_url` だけを module 属性の差し替えで
偽装し (`_FakeResolver`。受ける形と許可先の判定は nucllei の `_is_url` / `_route_url` と同じ規則で
模す。tool はこの判定を resolver に一任する)、ルーティング (`route_of_url`) は**本物**を通す
(設定は無い — `/ext/{rest}` は nucllei の `/mnt/{rest}`)。HTML は
`test/fixtures/fetch_url/his_*_excerpt.html` (実サイトの保存 HTML を必要な範囲だけ切り出した
もの)、PDF は `_pdf_to_text` の差し替えで代用する。

受理規則 (2026-10-03 ken 決定: url = 絶対 URL か同一サーバの相対 URL。引数名だけで解釈) を番兵する:
  - (a) path が /ext/{rest} の URL (相対 `/ext/…` と、host を問わない絶対形。nucllei の
    `/mnt/{rest}` をローカル読み)、(b) それ以外のスキーム付き http(s) URL = 一般の URL (どこでも
    取りに行く。断るのはこのサーバ自身だけ — 同日の ken 決定)。それ以外の相対 (`/mnt/…`
    `/workspace/…` 等) は固定文の ERROR (url が受ける形を添える)
  - 戻り値のリンク一覧は URL 1 本。`/ext/…` のページでも一般の URL でも、そのページ自身の
    URL 基準の urljoin (相対入力なら相対のまま)
  - RefError.code ごとに「何が起きたか + 次の一手」の ERROR 文 (組み立ては utils/ref_errors)

登録 (`__init__.py`): 常に BUILTIN_TOOLS に載る (env ゲート無し — 2026-10-03 に外した)。
EXT_UPSTREAMS / EXT_PUBLIC_BASES (原本ホスト) は同日に設定ごと撤去 — 原本行・docstring の
原本ホストのスロット・ERROR 文の「登録された原本ホストは …」は無い。
"""

import asyncio
import contextlib
import importlib
import pathlib
import re
import types
from urllib.parse import urlsplit

import pytest

from nucllei.utils.image_refs import REF_ARGS_RULE, URL_ARG_FORM, RefError

_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "fetch_url"

# 院内 Web (一般の URL)。偽 resolver がそのまま返す
SITE = "https://med-info.med.hokudai.ac.jp/his-info"
# 自オリジンの表示 URL (path が /ext/{rest})。host は resolver も tool も見ない。
ORIGIN = "https://llens.example"
EXT_TOP = f"{ORIGIN}/ext/his/top.aspx"

URL_FORM = f"{URL_ARG_FORM} {REF_ARGS_RULE}"


def _mod():
    from nucllei.builtin_tools import fetch_url

    return fetch_url


def _fixture(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _link_urls(links):
    return [ln.split("→", 1)[1].strip() for ln in links]


def _listed_links(result: str):
    return [
        ln.split("→", 1)[1].strip()
        for ln in result.split("\n")
        if re.match(r"^\[\d+\] ", ln)
    ]


# --- /ext に env は無い (2026-10-03)。fixture 名は旧来のまま no-op で残す --------------


@pytest.fixture
def ext_env():
    yield


# --- HTML → 平文 + リンク一覧 (変換ロジックだけを直接叩く) --------------------


def _convert(raw: bytes, page_url: str):
    """page_url (ページ自身の URL。絶対なら一般の URL / 絶対の /ext、相対なら `/ext/…`) 基準で
    変換する。"""
    return _mod()._html_to_lines(raw, page_url)


def test_html_links_become_numbered_refs_with_one_absolute_url():
    body, links = _convert(_fixture("his_top_excerpt.html"), f"{SITE}/top.aspx")
    text = "\n".join(body)
    assert "令和８年度休日" in text
    # 本文中では `ラベル [n]` になる
    assert "の通常診療体制について(再掲載) [1]" in text
    assert len(links) == 3
    # リンク一覧は絶対 URL 1 本だけ。/ext/ の相対形も「(原本: …)」の併記も無い
    assert links[0].startswith("[1] 令和８年度休日")
    assert (
        f"→ {SITE}/log_write.aspx?LI_BASE=TOP_NEW&LI_TB=REPO_TB"
        "&LI_URL=report_his_14071338.pdf" in links[0]
    )
    for ln in links:
        assert "(原本" not in ln and "/ext/" not in ln
        assert ln.count("http") == 1


def test_postback_and_fragment_links_are_not_listed():
    """`javascript:__doPostBack` と `#...` はリンク一覧に載せない (辿れないため)。
    ラベル自体は本文に残す。"""
    body, links = _convert(_fixture("his_top_excerpt.html"), f"{SITE}/top.aspx")
    text = "\n".join(body)
    assert "院内電話番号表" in text  # postback リンクのラベルは本文に残る
    assert "院内電話番号表 [" not in text  # ただし番号は振らない
    joined = "\n".join(links)
    assert "__doPostBack" not in joined and "javascript:" not in joined
    assert all(not u.startswith("#") for u in _link_urls(links))


def test_script_style_and_hidden_inputs_are_dropped():
    body, links = _convert(_fixture("his_top_excerpt.html"), f"{SITE}/top.aspx")
    text = "\n".join(body + links)
    assert "__VIEWSTATE" not in text  # ASP.NET の hidden input (数十 KB)
    assert "M_Click" not in text  # <script>
    assert "webkit-scrollbar" not in text  # <style>


def test_whitespace_is_normalized():
    body, _links = _convert(_fixture("his_unyou_excerpt.html"), f"{SITE}/unyou_mn.aspx")
    assert body[0] == "連絡関連"
    assert all(line == line.strip() for line in body)
    assert all("  " not in line for line in body)
    assert all("　" not in line for line in body)


_HREF_FORMS = (
    "<html><body>"
    '<a href="unyou_mn.aspx">相対</a>'
    '<a href="/his-info/x.pdf">ルート相対</a>'
    f'<a href="{SITE}/w.pdf">同一 origin の絶対</a>'
    '<a href="/other/app.aspx">アプリ外</a>'
    '<a href="https://example.com/z">別ホスト</a>'
    "</body></html>"
).encode("utf-8")


def test_href_forms_are_resolved_against_the_page_for_general_urls():
    """一般の URL: 相対 / ルート相対 / 同一 origin の絶対 / 別 origin の絶対 / 別ホスト を
    通常の Web と同じくページの URL 基準で解決する (別ホストも絶対 URL として載る —
    取りに行けるかは次の fetch_url で resolver が判定する)。"""
    _body, links = _convert(_HREF_FORMS, f"{SITE}/annai.aspx")
    assert _link_urls(links) == [
        f"{SITE}/unyou_mn.aspx",
        f"{SITE}/x.pdf",
        f"{SITE}/w.pdf",
        "https://med-info.med.hokudai.ac.jp/other/app.aspx",
        "https://example.com/z",
    ]


def test_href_forms_resolve_against_the_ext_page_url():
    """`/ext/{rest}` のページ: 通常の Web と同じくページ自身の URL 基準の urljoin。相対入力
    (`/ext/his/annai.aspx`) なら `/ext/` に留まる結果だけ残す (ルート相対 `/his-info/x.pdf` や
    別ホストはこのサーバの URL ではない)。絶対入力なら http(s) の結果を全部残す (一般の URL と
    同じ — 取りに行けるかは次の fetch_url で resolver が判定する)。"""
    _body, links = _convert(_HREF_FORMS, "/ext/his/annai.aspx")
    assert _link_urls(links) == ["/ext/his/unyou_mn.aspx"]
    _body, links = _convert(_HREF_FORMS, f"{ORIGIN}/ext/his/annai.aspx")
    assert _link_urls(links) == [
        f"{ORIGIN}/ext/his/unyou_mn.aspx",
        f"{ORIGIN}/his-info/x.pdf",
        f"{SITE}/w.pdf",
        f"{ORIGIN}/other/app.aspx",
        "https://example.com/z",
    ]
    # page_url が無い (file 経路) ならリンクを組まない
    _body, links = _convert(_HREF_FORMS, "")
    assert links == []


def test_html_charset_parameter_is_honoured_and_defaults_to_utf8():
    """Content-Type の charset があればそれで decode、無ければ utf-8。自動判定はしない
    (meta charset も見ない — lxml には decode 済みの str を渡す)。"""
    sjis = "<html><body><p>連絡関連</p></body></html>".encode("shift_jis")
    body, _ = _convert_ct(sjis, "text/html; charset=Shift_JIS")
    assert body == ["連絡関連"]
    body, _ = _convert_ct(
        sjis, "text/html"
    )  # charset 無し → utf-8 (化ける。判定しない)
    assert body != ["連絡関連"]
    utf8 = '<html><head><meta charset="shift_jis"></head><body><p>連絡関連</p></body></html>'.encode()
    body, _ = _convert_ct(utf8, "text/html")  # meta は見ない
    assert body == ["連絡関連"]
    body, _ = _convert_ct(
        utf8, "text/html; charset=no-such-codec"
    )  # 知らない名前 → utf-8
    assert body == ["連絡関連"]


def _convert_ct(raw: bytes, content_type: str):
    return _mod()._html_to_lines(raw, "/ext/his/x.aspx", content_type)


# --- fetch_url 本体 (上流は偽装) ---------------------------------------------


class _Loaded:
    """image_refs.LoadedRef の使用面 (data / content_type / name) だけ模す。"""

    def __init__(self, content_type="text/html", body=b"", name="x", missing=False):
        self.data = body
        self.content_type = content_type
        self.name = name
        self.missing = missing  # /ext/{rest} で /mnt に無い物として振る舞う


class _FakeResolver:
    """image_refs の resolve_url ((loaded, RefError) を返す) の偽物。呼ばれた ref を記録する。
    受ける形と許可先の判定は nucllei の `_is_url` / `_route_url` と同じ規則で模す (tool は
    この判定を resolver に一任する)。error を与えればそれを返す (code ごとの文面の番兵用)。"""

    def __init__(self, loaded, error=None):
        self._loaded = loaded
        self._error = error
        self.refs: list[str] = []
        self.max_bytes: list = []  # resolve ごとに渡された max_bytes

    async def resolve(self, ref, *, max_bytes=None):
        self.refs.append(ref)
        self.max_bytes.append(max_bytes)
        return await self._decide(ref)

    async def _decide(self, ref):
        if ".." in ref.split("/"):
            return None, RefError("traversal", "..")  # resolver が落とす (fail closed)
        if self._error is not None:
            return None, self._error
        if not (
            ref.lower().startswith(("http://", "https://")) or ref.startswith("/ext/")
        ):
            return None, RefError("unsupported_form", "not a URL this server resolves")
        parts = urlsplit(ref)
        if re.match(r"^/ext(?:/.*)?$", parts.path):
            # /mnt/{rest} のローカル読み (実在は _Env の mounted で模す)
            if self._loaded is not None and self._loaded.missing:
                return None, RefError("not_found", "missing")
            return self._loaded, None
        # 一般の URL: どこでも取りに行く。断るのはこのサーバ自身 (loopback) だけ
        if parts.hostname in ("127.0.0.1", "localhost", "::1"):
            return None, RefError("unknown_upstream", "self", reason="server_self")
        return self._loaded, None


class _Env:
    """1 テスト分の道具立て: env の dict と偽 resolver を入れ、Tools を返す。"""

    def __init__(self, monkeypatch, ext_env, loaded, error=None, pdf_text=None):
        mod = _mod()
        self.resolver = _FakeResolver(loaded, error)
        monkeypatch.setattr(mod, "resolve_url", self.resolver.resolve)
        if pdf_text is not None:
            monkeypatch.setattr(mod, "_pdf_to_text", lambda raw: pdf_text)
        self.tools = mod.Tools()

    def run(self, url, **kwargs):
        return asyncio.run(
            self.tools.fetch_url(url=url, __user__={"id": "u1"}, **kwargs)
        )


def _run(
    monkeypatch,
    ext_env,
    url,
    *,
    resp=None,
    pdf_text=None,
    error=None,
    **kwargs,
):
    """fetch_url を偽の resolver で実行し (戻り文字列, 偽 resolver) を返す。
    resp=None は既定の HTML fixture。理由付きの取得失敗は error=RefError(...)、
    理由なし (None, None) は resp=False。"""
    if resp is None:
        resp = _Loaded(body=_fixture("his_top_excerpt.html"))
    env = _Env(
        monkeypatch,
        ext_env,
        None if resp is False else resp,
        error=error,
        pdf_text=pdf_text,
    )
    return env.run(url, **kwargs), env.resolver


def test_fetch_passes_a_general_url_to_the_resolver_and_renders_header(
    monkeypatch, ext_env
):
    """一般の URL: そのまま resolver に渡し、ヘッダは入力 URL 1 本。リンクはページ基準。"""
    result, resolver = _run(monkeypatch, ext_env, f"{SITE}/top.aspx")
    assert resolver.refs == [f"{SITE}/top.aspx"]
    assert result.startswith(f"# {SITE}/top.aspx\n種別: html")
    assert "原本" not in result and "注意:" not in result
    # リンク一覧は絶対 URL 1 本
    assert f"→ {SITE}/log_write.aspx?" in result and "/ext/" not in result


def test_fetch_accepts_the_ext_form_and_keeps_links_in_that_form(monkeypatch, ext_env):
    """自オリジンの /ext/{rest} も受ける。resolver にはそのまま渡し、リンクは入力と同じ
    形 (`{origin}/ext/…`)。原本行も注意書きも無い (2026-10-03 設定撤去)。"""
    result, resolver = _run(monkeypatch, ext_env, EXT_TOP)
    assert resolver.refs == [EXT_TOP]
    assert result.startswith(f"# {EXT_TOP}\n種別: html")
    assert "原本" not in result and "注意:" not in result
    assert f"→ {ORIGIN}/ext/his/log_write.aspx?" in result
    assert SITE not in result


def test_query_string_is_passed_through(monkeypatch, ext_env):
    u = f"{SITE}/log_write.aspx?LI_UID=G_UID&LI_URL=x.pdf"
    result, resolver = _run(
        monkeypatch,
        ext_env,
        u,
        pdf_text="A" * 500,
        resp=_Loaded(content_type="application/pdf", body=b"%PDF-1.7 fake"),
    )
    # query string ごと resolver に渡る (上流 URL の組み立ては resolver の仕事)
    assert resolver.refs == [u]
    assert "種別: pdf" in result
    assert "AAAA" in result


def test_query_filters_lines(monkeypatch, ext_env):
    result, _c = _run(monkeypatch, ext_env, f"{SITE}/top.aspx", query="移送")
    assert 'query="移送" に一致した行:' in result
    assert "移送" in result
    assert "令和８年度休日" not in result  # 一致しない行は落ちる


def test_query_is_case_and_width_insensitive(monkeypatch, ext_env):
    result, _c = _run(
        monkeypatch,
        ext_env,
        f"{SITE}/unyou_mn.aspx",
        query="ｐｈｓ",  # 全角小文字 → NFKC + casefold で "PHS" に当たる
        resp=_Loaded(body=_fixture("his_unyou_excerpt.html")),
    )
    assert "院内PHS電話番号簿" in result


def test_query_matches_link_lines_too(monkeypatch, ext_env):
    """本文行だけでなくリンク行も query の対象 (URL からファイル名で引ける)。"""
    result, _c = _run(
        monkeypatch,
        ext_env,
        f"{SITE}/top.aspx",
        query="iji_kanzyaisou_01_rev2.pdf",
    )
    assert "iji_kanzyaisou_01_rev2.pdf" in result
    assert 'query="iji_kanzyaisou_01_rev2.pdf" に一致した行: 1 /' in result


def test_query_without_match_explains_instead_of_returning_everything(
    monkeypatch, ext_env
):
    result, _c = _run(
        monkeypatch, ext_env, f"{SITE}/top.aspx", query="存在しない語句xyz"
    )
    assert "一致する行が無い" in result
    assert "令和８年度休日" not in result


def test_offset_limit_and_next_offset(monkeypatch, ext_env):
    first, _c = _run(monkeypatch, ext_env, f"{SITE}/top.aspx", limit=40)
    assert "(続きあり)" in first and "next_offset=40" in first
    head, _sep, shown = first.partition("\n\n")
    assert len(shown) == 40
    second, _c = _run(monkeypatch, ext_env, f"{SITE}/top.aspx", offset=40, limit=40)
    assert "next_offset=80" in second
    assert second.partition("\n\n")[2] != shown
    whole, _c = _run(monkeypatch, ext_env, f"{SITE}/top.aspx", limit=100000)
    assert "(末尾まで)" in whole and "next_offset" not in whole


def test_limit_is_capped_by_valve(monkeypatch, ext_env):
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    env.tools.valves.max_output_chars = 30
    result = env.run(f"{SITE}/top.aspx", limit=99999)
    assert len(result.partition("\n\n")[2]) == 30


def test_offset_past_the_end_is_an_error(monkeypatch, ext_env):
    result, _c = _run(monkeypatch, ext_env, f"{SITE}/top.aspx", offset=10**6)
    assert result.startswith("ERROR") and "末尾" in result


# --- 防壁: 絶対 URL 以外・許可先の外は受けない -----------------------------------
# 受理判定は resolver。tool は値を見ず、そのまま渡す (resolver.refs に残る)。


def test_accepts_a_same_server_relative_ext_url(monkeypatch, ext_env):
    """相対 `/ext/…` (read_doc の図・原本の形) はそのまま resolver に渡す。ホストを補わない。
    リンクは相対 `/ext/…` のまま。"""
    result, resolver = _run(monkeypatch, ext_env, "/ext/his/top.aspx")
    assert resolver.refs == ["/ext/his/top.aspx"]
    assert result.startswith("# /ext/his/top.aspx\n種別: html"), result
    assert "→ /ext/his/log_write.aspx?" in result and "https://llens" not in result
    assert "原本" not in result and "注意:" not in result
    result, resolver = _run(
        monkeypatch,
        ext_env,
        " /ext/his/log_write.aspx?LI_URL=x.pdf ",
        pdf_text="本文",
        resp=_Loaded(content_type="application/pdf", body=b"%PDF-1.7"),
    )
    assert resolver.refs == ["/ext/his/log_write.aspx?LI_URL=x.pdf"], result


def test_rejects_other_relative_and_non_url_values_with_the_fixed_text(
    monkeypatch, ext_env
):
    """url に来た値は URL として resolver に渡す (tool 側で振り分けを重ねない)。URL として
    解決できない値は値によらない固定文 (行き先を出し分けない — 案内は prompt / skill 側)。"""
    for bad in (
        "/api/v1/files/x",  # `/content` の無い形は file に当てない
        "/mnt/cage/static/x.pdf",
        "/workspace/x.pdf",
        "/exta/x.aspx",
        "ext/his/top.aspx",
        "//evil.example.com/x",
        "ftp://med-info.med.hokudai.ac.jp/his-info/x",
    ):
        result, resolver = _run(monkeypatch, ext_env, bad)
        assert result == f"ERROR: {bad} は URL として解決できない。{URL_FORM}", (
            bad,
            result,
        )
        assert "run_shell" not in result and "feed_image" not in result, bad
        assert "read_attachment" not in result, bad
        assert resolver.refs == [bad], bad


def test_ext_paths_not_mounted_are_dead_links(monkeypatch, ext_env):
    """/mnt に無い `/ext/…` は resolver の not_found (設定も name の概念も無い —
    2026-10-03)。リンク切れとして案内する。"""
    for bad in (f"{ORIGIN}/ext/other/top.aspx", "/ext/other/x", "/ext/", "/ext/CAGE/x"):
        result, resolver = _run(monkeypatch, ext_env, bad, resp=_Loaded(missing=True))
        assert resolver.refs == [bad], bad
        assert result.startswith(f"ERROR: {bad} は存在しない"), (bad, result)
        assert "EXT_" not in result, bad


def test_general_urls_are_fetched_and_the_server_itself_is_refused(
    monkeypatch, ext_env
):
    """`/ext/` 形でない絶対 URL (外部サイト・任意の LAN・絶対で name が取れない形) は一般の
    URL として取りに行く (resolver 任せ。tool は行き先で断らない)。断るのはこのサーバ自身
    だけで、その文面は reason `server_self`。"""
    for u in (
        "https://example.com/x",
        "http://172.17.0.1:13303/static/x.png",
        "https://med-info.med.hokudai.ac.jp/other/app.aspx",
        f"{ORIGIN}/ext//top.aspx",  # 絶対で name が取れない形も、ただの絶対 URL
    ):
        result, resolver = _run(monkeypatch, ext_env, u)
        assert resolver.refs == [u], u
        assert not result.startswith("ERROR") and result.startswith(f"# {u}\n"), (
            u,
            result,
        )
    result, resolver = _run(monkeypatch, ext_env, "http://127.0.0.1:3000/health")
    assert result.startswith(
        "ERROR: http://127.0.0.1:3000/health は このサーバ自身 (または同居するサービス) を"
        "指しているので取りに行かない。"
    ), result
    assert result.endswith(URL_ARG_FORM) and "EXT_" not in result


def test_root_redirect_is_the_redirect_guidance(monkeypatch, ext_env):
    """直下 (`/ext/{name}` `/ext/{name}/` / サイトのルート) は resolver がそのまま取りに行き、
    302 なら upstream_status (status=302)。redirect の案内文になる。
    tool は末尾 `/` を補わず、受け取った形のまま resolver に渡す。"""
    for u in (SITE, f"{SITE}/", "/ext/his", "/ext/his/"):
        result, resolver = _run(
            monkeypatch,
            ext_env,
            u,
            error=RefError("upstream_status", "302", status=302),
        )
        assert resolver.refs == [u], u
        assert (
            "redirect (302)" in result
            and "ディレクトリではなくページを直接指す" in result
        ), (u, result)
        assert "リンク切れ" not in result, u


def test_root_page_has_link_roots_without_double_slashes(monkeypatch, ext_env):
    """ディレクトリ風の URL (末尾 `/`) が 200 で返ったとき、リンクはその下で `//` を作らない。
    末尾 `/` 無し (`/ext/his`) はファイル名扱いで親の下 — 通常の Web と同じ urljoin。"""
    for u, root in (
        (f"{SITE}/", SITE),
        ("/ext/his/", "/ext/his"),
        ("/ext/his", "/ext"),
        (f"{ORIGIN}/ext/his/", f"{ORIGIN}/ext/his"),
    ):
        result, resolver = _run(monkeypatch, ext_env, u)
        assert resolver.refs == [u] and not result.startswith("ERROR"), (u, result)
        links = _listed_links(result)
        assert links and all(
            link.startswith(root + "/") and "//" not in link.split("://", 1)[-1]
            for link in links
        ), (u, links)


def test_traversal_is_an_error_via_the_resolver(monkeypatch, ext_env):
    """traversal の判定は tool ではなく resolver (fail closed)。tool は「範囲の外」と言う。"""
    for bad in (f"{SITE}/../secret.pdf", f"{ORIGIN}/ext/his/../secret.pdf"):
        result, _r = _run(monkeypatch, ext_env, bad)
        assert result.startswith("ERROR") and "許可された範囲の外" in result, bad
        assert result.endswith(URL_ARG_FORM), bad


def test_trailing_question_mark_does_not_break_links(monkeypatch, ext_env):
    """URL 末尾が `?` だけ (query 空) でも、resolver にはその literal を渡し、
    リンクのルートは 1 字もずれない (一般 / /ext 両形)。"""
    for u, root in (
        (f"{SITE}/top.aspx?", SITE),
        (f"{ORIGIN}/ext/his/top.aspx?", f"{ORIGIN}/ext/his"),
        ("/ext/his/top.aspx?", "/ext/his"),
    ):
        result, resolver = _run(monkeypatch, ext_env, u)
        assert resolver.refs == [u], u
        assert result.startswith(f"# {u}\n"), u
        links = [ln for ln in result.split("\n") if re.match(r"^\[\d+\] ", ln)]
        assert len(links) == 3 and all(
            f"→ {root}/log_write.aspx?" in ln for ln in links
        ), u


def test_ext_page_links_resolve_against_the_page_url(monkeypatch, ext_env):
    """`/ext/{rest}` のページの href はページ自身の URL 基準 (通常の Web と同じ)。相対入力なら
    `/ext/` に留まる物だけ、絶対入力なら http(s) の結果を全部。"""
    page = _Loaded(body=_HREF_FORMS)
    result, _r = _run(monkeypatch, ext_env, f"{ORIGIN}/ext/his/annai.aspx", resp=page)
    assert _listed_links(result) == [
        f"{ORIGIN}/ext/his/unyou_mn.aspx",
        f"{ORIGIN}/his-info/x.pdf",
        f"{SITE}/w.pdf",
        f"{ORIGIN}/other/app.aspx",
        "https://example.com/z",
    ]
    result, _r = _run(monkeypatch, ext_env, "/ext/his/annai.aspx", resp=page)
    assert _listed_links(result) == ["/ext/his/unyou_mn.aspx"]


def test_requires_user(monkeypatch, ext_env):
    env = _Env(monkeypatch, ext_env, _Loaded(body=b"<html></html>"))
    result = asyncio.run(env.tools.fetch_url(url=f"{SITE}/top.aspx"))
    assert result.startswith("ERROR") and env.resolver.refs == []


# --- PDF -----------------------------------------------------------------


def test_pdf_without_text_layer_points_to_feed_image(monkeypatch, ext_env):
    u = f"{SITE}/scan.pdf"
    result, _c = _run(
        monkeypatch,
        ext_env,
        u,
        pdf_text="",
        resp=_Loaded(content_type="application/pdf", body=b"%PDF-1.7 scan"),
    )
    assert "テキスト層が無い" in result
    assert f'feed_image(url="{u}")' in result


def test_pdf_without_text_layer_guides_to_feed_image_even_without_pdf_extension(
    monkeypatch, ext_env
):
    """log_write.aspx 経由 (パスが .pdf で終わらない) でも feed_image へ誘導する
    (feed_image は拡張子ではなく content-type で PDF を判定する)。"""
    u = f"{SITE}/log_write.aspx?LI_URL=scan.pdf"
    result, _c = _run(
        monkeypatch,
        ext_env,
        u,
        pdf_text="",
        resp=_Loaded(content_type="application/pdf", body=b"%PDF-1.7 scan"),
    )
    assert "テキスト層が無い" in result
    assert f'feed_image(url="{u}")' in result
    assert "判定できず" not in result
    assert result.startswith(f"# {u}\n")


def test_pdf_is_detected_by_magic_bytes_when_content_type_lies(monkeypatch, ext_env):
    result, _c = _run(
        monkeypatch,
        ext_env,
        f"{SITE}/log_write.aspx?LI_URL=x.pdf",
        pdf_text="B" * 300,
        resp=_Loaded(content_type="application/octet-stream", body=b"%PDF-1.7 x"),
    )
    assert "種別: pdf" in result and "BBB" in result


# --- 取得失敗 / サイズ ----------------------------------------------------


def test_resolver_failure_without_a_reason_falls_back_to_the_bundled_text(
    monkeypatch, ext_env
):
    """resolve_url が (None, None) を返した (通常起きない): 理由が無いので束ねた 1 文
    (redirect を辿らないことだけは案内する)。"""
    result, resolver = _run(monkeypatch, ext_env, f"{SITE}/index", resp=False)
    assert resolver.refs == [f"{SITE}/index"]
    assert result.startswith(f"ERROR: {SITE}/index を取得できない")
    assert "redirect" in result and "top.aspx" in result


def test_fetch_passes_the_valve_size_cap_to_the_resolver_as_max_bytes(
    monkeypatch, ext_env
):
    """文書を読む用途の上限 (valve max_fetch_mb、既定 25 = 控えめ) を max_bytes で渡す — 画像
    inline 用の既定上限を借りない。"""
    result, resolver = _run(monkeypatch, ext_env, f"{SITE}/top.aspx")
    assert not result.startswith("ERROR")
    assert resolver.max_bytes == [25 * 1024 * 1024]
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    env.tools.valves.max_fetch_mb = 2.5
    env.run(f"{SITE}/top.aspx")
    assert env.resolver.max_bytes == [int(2.5 * 1024 * 1024)]


def test_max_mb_argument_is_capped_by_the_ceiling(monkeypatch, ext_env):
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    env.run(f"{SITE}/top.aspx", max_mb=30)
    env.run(f"{SITE}/top.aspx", max_mb=10_000)
    assert env.resolver.max_bytes == [30 * 1024 * 1024, 200 * 1024 * 1024]


# --- url の /api/v1/files/{id}/content は file として読む (send_to_workspace と同じ) ---


class _Rec:
    def __init__(self, fid, user_id, filename, content_type, path):
        self.id = fid
        self.user_id = user_id
        self.filename = filename
        self.meta = {"content_type": content_type}
        self.path = path


def _run_file(monkeypatch, ext_env, url, recs, tmp_files, **kwargs):
    mod = _mod()
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    monkeypatch.setattr(
        mod, "Files", types.SimpleNamespace(get_file_by_id=lambda fid: recs.get(fid))
    )
    monkeypatch.setattr(
        mod, "Storage", types.SimpleNamespace(get_file=lambda path: tmp_files[path])
    )
    return env.run(url, **kwargs), env.resolver


def test_internal_file_url_is_read_as_the_file_with_the_owner_check(
    monkeypatch, ext_env, tmp_path
):
    note = tmp_path / "note.txt"
    note.write_text("一行目\n二行目\n", encoding="utf-8")
    recs = {"f1": _Rec("f1", "u1", "note.txt", "text/plain", "stored/note.txt")}
    files = {"stored/note.txt": str(note)}
    for u in (
        "/api/v1/files/f1/content",
        "https://llens.example/api/v1/files/f1/content",
    ):
        result, resolver = _run_file(monkeypatch, ext_env, u, recs, files)
        assert resolver.refs == [], u  # HTTP では取りに行かない
        assert (
            not result.startswith("ERROR") and "一行目" in result and "二行目" in result
        ), (u, result)
    recs_other = {
        "f1": _Rec("f1", "someone-else", "note.txt", "text/plain", "stored/note.txt")
    }
    result, _r = _run_file(
        monkeypatch, ext_env, "/api/v1/files/f1/content", recs_other, files
    )
    assert result.startswith("ERROR") and "アクセス権が無い" in result
    result, _r = _run_file(
        monkeypatch, ext_env, "/api/v1/files/nope/content", recs, files
    )
    assert result.startswith("ERROR") and "レコードが無い" in result


def test_internal_file_html_lists_no_links(monkeypatch, ext_env, tmp_path):
    """添付 HTML の相対 href はどの名前空間にも属さない (`/page2.html` になる) ので、file 経路では
    リンク一覧を出さない。本文は読める。"""
    page = tmp_path / "a.html"
    page.write_text(
        '<html><body><p>本文です</p><a href="page2.html">次へ</a><a href="/x.pdf">PDF</a></body></html>',
        encoding="utf-8",
    )
    recs = {"f1": _Rec("f1", "u1", "a.html", "text/html", "stored/a.html")}
    result, resolver = _run_file(
        monkeypatch,
        ext_env,
        "/api/v1/files/f1/content",
        recs,
        {"stored/a.html": str(page)},
    )
    assert not result.startswith("ERROR") and "本文です" in result, result
    assert (
        "→" not in result and "page2.html" not in result and "/x.pdf" not in result
    ), result


def test_internal_file_charset_comes_from_the_files_meta(
    monkeypatch, ext_env, tmp_path
):
    """file 経路では Files の meta の content_type (charset 付きのことがある) で decode する。"""
    page = tmp_path / "s.html"
    page.write_bytes("<html><body><p>本文です</p></body></html>".encode("shift_jis"))
    recs = {
        "f1": _Rec(
            "f1", "u1", "s.html", "text/html; charset=Shift_JIS", "stored/s.html"
        )
    }
    result, _r = _run_file(
        monkeypatch,
        ext_env,
        "/api/v1/files/f1/content",
        recs,
        {"stored/s.html": str(page)},
    )
    assert "本文です" in result and "種別: html" in result, result


# --- RefError.code ごとの ERROR 文 (番兵)。文面はモデルがユーザーへ転記する前提 ----------
# 実走 (2026-09-06): 32.6 MB の PDF が上限 25 MB に当たったのを、束ねた ERROR 文のせいで
# モデルが「リンク切れ」と誤報した。code ごとに「何が起きたか + 次の一手」を言う。

_PDF_URL = f"{SITE}/log_write.aspx?LI_UID=G_UID&LI_URL=big.pdf"


def _explained(monkeypatch, ext_env, code, url=_PDF_URL, **kw):
    kw.setdefault("detail", "")
    result, resolver = _run(monkeypatch, ext_env, url, error=RefError(code, **kw))
    assert resolver.refs == [url], code
    assert result.startswith("ERROR"), (code, result)
    assert "cage" not in result.lower() and "EXT_" not in result, (code, result)
    return result


def test_too_large_names_size_and_limit_in_mb_and_sends_a_subagent_with_max_mb(
    monkeypatch, ext_env
):
    """32,646,426 B → 31.1 MB / limit 26,214,400 B → 25.0 MB (小数 1 桁、MiB 換算)。
    次の一手は「主 context で読まず run_subagent の子が max_mb 付きで読む」。"""
    result = _explained(
        monkeypatch,
        ext_env,
        "too_large",
        size=32_646_426,
        limit=25 * 1024 * 1024,
        size_exact=True,
    )
    assert f"{_PDF_URL} は 31.1 MB で、取得の上限 25.0 MB を超えている" in result
    assert "主 context では読まず" in result and "run_subagent" in result
    assert (
        f'fetch_url(url="{_PDF_URL}", max_mb=32)' in result
    )  # X (31.1) を超える整数 MB
    assert "offset で続き" in result
    assert (
        "中身が要らなければ URL をそのまま案内" in result
        and "ブラウザでは開ける" in result
    )
    assert (
        "リンク切れ" not in result
        and "存在しない" not in result
        and "読めない" not in result
    )


def test_too_large_beyond_the_ceiling_hands_the_url_over_instead(monkeypatch, ext_env):
    """天井 (既定 200 MB) も超える資料は max_mb を上げても読めないので、URL 案内だけ。"""
    result = _explained(
        monkeypatch,
        ext_env,
        "too_large",
        size=250 * 1024 * 1024,
        limit=25 * 1024 * 1024,
        size_exact=True,
    )
    assert "は 250.0 MB で、取得の上限 25.0 MB を超えている" in result
    assert "最大 200.0 MB も超える" in result and "中身は読めない" in result
    assert "URL をそのまま案内" in result
    assert "run_subagent" not in result and "max_mb" not in result


def test_too_large_with_unknown_real_size_proposes_double_the_limit(
    monkeypatch, ext_env
):
    """上流が Content-Length を返さない (aspx の配信口) と、resolver は size に
    **打ち切り時点の受信量** を入れ、size_exact=False を立てる — limit を超えた chunk まで
    読むので size は limit のすぐ上だが実サイズの下限でしかない。これを実サイズ確定と読むと
    「25.0 MB で上限 25.0 MB … max_mb=26」となり 32.6 MB の実体に足りない (実走 2026-09-06)。
    「少なくとも」と書き、上限の 2 倍 (50) を提案し、足りなければ上げると添える。
    size が None (理由だけ来た) も同じ扱い。"""
    limit = 25 * 1024 * 1024
    cases = [
        dict(
            size=limit + 64 * 1024, size_exact=False
        ),  # 打ち切り受信量 (limit + 64 KiB)
        dict(size=limit + 1, size_exact=False),
        dict(size=limit + 64 * 1024),  # size_exact 既定 (False)
        dict(size=None, size_exact=False),
        dict(size=None, size_exact=True),  # size が無ければ exact と言われても不明
        dict(size=None),
    ]
    for kw in cases:
        result = _explained(monkeypatch, ext_env, "too_large", limit=limit, **kw)
        assert f"{_PDF_URL} は少なくとも " in result, kw
        assert "取得の上限 25.0 MB を超えている (実サイズは不明)" in result, kw
        assert re.search(r"少なくとも 25\.[01] MB あり", result), (kw, result)
        assert (
            f'fetch_url(url="{_PDF_URL}", max_mb=50)' in result
            and "run_subagent" in result
        ), kw
        assert "足りなければさらに上げる (天井 200.0 MB)" in result, kw
        assert " で、取得の上限" not in result, kw  # 実サイズ確定の言い方は使わない
    # 2 倍が天井を超えるなら天井で止める
    result = _explained(
        monkeypatch,
        ext_env,
        "too_large",
        size=150 * 1024 * 1024 + 8,
        limit=150 * 1024 * 1024,
        size_exact=False,
    )
    assert "max_mb=200)" in result


def test_too_large_trusts_size_exact_not_the_distance_from_the_limit(
    monkeypatch, ext_env
):
    """size_exact=True なら limit のすぐ上でも実サイズ確定として ceil+1 を提案する。
    判定は resolver の size_exact だけに拠る。"""
    limit = 25 * 1024 * 1024
    result = _explained(
        monkeypatch,
        ext_env,
        "too_large",
        size=limit + 300 * 1024,
        limit=limit,
        size_exact=True,
    )
    assert f"{_PDF_URL} は 25.3 MB で、取得の上限 25.0 MB を超えている" in result
    assert "max_mb=26)" in result and "max_mb=50" not in result
    assert "少なくとも" not in result and "実サイズは不明" not in result
    # 逆に limit より 1 MiB 以上大きくても size_exact=False なら不明のまま
    result = _explained(
        monkeypatch,
        ext_env,
        "too_large",
        size=limit + 2 * 1024 * 1024,
        limit=limit,
        size_exact=False,
    )
    assert "少なくとも 27.0 MB あり" in result and "(実サイズは不明)" in result
    assert "max_mb=50)" in result


def test_too_large_without_numbers_still_says_it_is_the_size_cap(monkeypatch, ext_env):
    result = _explained(monkeypatch, ext_env, "too_large")
    assert (
        "取得の上限" in result and "超えている" in result and "実サイズは不明" in result
    )
    assert "run_subagent" in result and "max_mb=<X を超える値>" in result
    assert "URL をそのまま案内" in result


def test_not_found_is_the_only_code_that_says_dead_link(monkeypatch, ext_env):
    result = _explained(monkeypatch, ext_env, "not_found")
    assert f"{_PDF_URL} は存在しない (404 / ファイル無し)" in result
    assert "リンク切れとして案内" in result


def test_redirect_status_points_to_a_page_not_a_directory(monkeypatch, ext_env):
    result = _explained(
        monkeypatch, ext_env, "upstream_status", url=f"{SITE}/dir", status=302
    )
    assert (
        "redirect (302)" in result and "ディレクトリではなくページを直接指す" in result
    )
    assert "top.aspx" in result
    assert "リンク切れ" not in result


def test_server_error_status_suggests_retry_or_handing_over_the_url(
    monkeypatch, ext_env
):
    for status in (500, 503, 403):
        result = _explained(monkeypatch, ext_env, "upstream_status", status=status)
        assert f"上流が {status} を返した" in result, status
        assert "時間を置いて再試行" in result and "URL をそのまま案内" in result, status
        assert "redirect" not in result, status


def test_unreachable_upstream_says_the_web_is_not_reachable_from_here(
    monkeypatch, ext_env
):
    result = _explained(monkeypatch, ext_env, "upstream_unreachable")
    assert "院内 Web に届かない" in result and "この配備からは到達できない" in result
    assert "URL をそのまま案内" in result and "リンク切れ" not in result


def test_unsupported_form_code_uses_the_fixed_text(monkeypatch, ext_env):
    """resolver の unsupported_form も「URL として解決できない」+ 固定文。"""
    result = _explained(monkeypatch, ext_env, "unsupported_form")
    assert "URL として解決できない" in result and URL_FORM in result


def test_unknown_upstream_does_not_branch_on_the_detail(monkeypatch, ext_env):
    """unknown_upstream の detail 文言は変わりうるので分岐に使わない — どの detail でも同文。
    env の変数名も配信元の一覧も出ない。"""
    texts = set()
    for detail in (
        "name=other",
        "host resolves to this server itself",
        "",
    ):
        texts.add(
            _explained(
                monkeypatch, ext_env, "unknown_upstream", url=_PDF_URL, detail=detail
            )
        )
    assert len(texts) == 1
    (text,) = texts
    assert "取得を許可されていない URL" in text and text.endswith(URL_ARG_FORM)
    assert "登録された原本ホスト" not in text and "/mnt/" not in text


def test_unknown_upstream_is_told_apart_by_reason(monkeypatch, ext_env):
    """`RefError.reason` で言い分ける (detail は見ない)。reason の集合は server_self だけ
    (`not_ext_form` / `unknown_name` は 2026-10-03 に無くした — 知らない reason は 1 文面)。"""
    want = {
        "server_self": "このサーバ自身",
        "not_ext_form": "取得を許可されていない URL",
        "unknown_name": "取得を許可されていない URL",
    }
    for reason, phrase in want.items():
        result = _explained(
            monkeypatch,
            ext_env,
            "unknown_upstream",
            detail="must not matter",
            reason=reason,
        )
        assert phrase in result and result.endswith(URL_ARG_FORM), (reason, result)
        assert "登録された原本ホスト" not in result, reason


def test_traversal_code_says_out_of_bounds(monkeypatch, ext_env):
    result = _explained(monkeypatch, ext_env, "traversal", url=f"{SITE}/a.pdf")
    assert "許可された範囲の外" in result and result.endswith(URL_ARG_FORM)


def test_unknown_code_falls_back_to_the_bundled_text(monkeypatch, ext_env):
    result = _explained(
        monkeypatch, ext_env, "something_new", detail="internal detail must not leak"
    )
    assert "取得できない" in result and "redirect" in result
    assert "internal detail" not in result


def test_general_url_reasons_have_their_own_text(monkeypatch, ext_env):
    """reason: server_self / redirect_self / too_many_redirects / unresolvable。"""
    cases = (
        (
            RefError("unknown_upstream", "x", reason="server_self"),
            "このサーバ自身",
        ),
        (
            RefError("upstream_status", "x", status=302, reason="redirect_self"),
            "転送先が このサーバ自身",
        ),
        (
            RefError("upstream_status", "x", status=302, reason="too_many_redirects"),
            "4 回以上",
        ),
        (
            RefError("upstream_unreachable", "x", reason="unresolvable"),
            "ホスト名を解決できない",
        ),
    )
    for e, phrase in cases:
        result, _r = _run(monkeypatch, ext_env, "http://172.16.9.9/x", error=e)
        assert result.startswith("ERROR") and phrase in result, (e.reason, result)
    result, _r = _run(
        monkeypatch,
        ext_env,
        "http://172.16.9.9/x",
        error=RefError("upstream_unreachable", "x", reason="unresolvable"),
    )
    assert "IP アドレス" in result


def test_size_cap(monkeypatch, ext_env):
    env = _Env(monkeypatch, ext_env, _Loaded(body=b"x" * 4096))
    env.tools.valves.max_fetch_mb = 0.001  # 約 1KB
    result = env.run(f"{SITE}/top.aspx")
    assert result.startswith("ERROR") and "大きすぎる" in result
    assert (
        "0.0 MB で大きすぎる (この呼び出しの上限 0.0 MB)" in result
    )  # 4 KB / 1 KB を MB 小数 1 桁で
    assert "URL をそのまま案内" in result


def test_unsupported_content_type_hands_back_the_page_url(monkeypatch, ext_env):
    u = f"{SITE}/log_write.aspx?LI_URL=x.xls"
    result, _c = _run(
        monkeypatch,
        ext_env,
        u,
        resp=_Loaded(content_type="application/vnd.ms-excel", body=b"\xd0\xcf"),
    )
    assert result.startswith("ERROR") and "平文にできない形式" in result
    assert u in result
    # /ext/ 形 (相対) も入力と同じ形で案内する
    u = "/ext/his/log_write.aspx?LI_URL=x.xls"
    result, _c = _run(
        monkeypatch,
        ext_env,
        u,
        resp=_Loaded(content_type="application/vnd.ms-excel", body=b"\xd0\xcf"),
    )
    assert result.startswith("ERROR") and u in result and "https://" not in result


def test_image_guides_to_feed_image_not_to_a_dead_end(monkeypatch, ext_env):
    """画像は平文にできないが行き止まりではない — feed_image に同じ URL を渡して見る。"""
    u = f"{SITE}/log_write.aspx?LI_URL=fig.png"
    result, _c = _run(
        monkeypatch,
        ext_env,
        u,
        resp=_Loaded(content_type="image/png", body=b"\x89PNG\r\n\x1a\n"),
    )
    assert not result.startswith("ERROR")
    assert f'feed_image(url="{u}")' in result


def test_json_and_text_are_returned_as_lines(monkeypatch, ext_env):
    """JSON / 素のテキスト系は本文をそのまま行にする (院内 API の応答など)。"""
    for ct in ("application/json", "text/plain", "application/ld+json"):
        result, _c = _run(
            monkeypatch,
            ext_env,
            f"{SITE}/api/x",
            resp=_Loaded(content_type=ct, body=b'{"a": 1}\n\n{"b": 2}\n'),
        )
        assert (
            "種別: text" in result and '{"a": 1}' in result and '{"b": 2}' in result
        ), (ct, result)


def test_no_original_host_text_is_left():
    """原本ホスト (EXT_PUBLIC_BASES) の仕組みは無い: docstring のスロットも、ERROR 文の
    「登録された原本ホスト」も、ヘッダの原本行・注意書きも。"""
    mod = _mod()
    for gone in (
        "_describe_bases",
        "_DOC_BASES_SLOT",
        "EXT_PUBLIC_BASES",
        "_FAKE_ORIGIN",
    ):
        assert not hasattr(mod, gone), gone
    for gone in ("_base_prefix", "_head", "_link_roots"):
        assert not hasattr(mod.Tools, gone), gone
    doc = mod.Tools.fetch_url.__doc__
    assert "http(s)://" in doc and "`/ext/…`" in doc
    assert "`https://…`" not in doc  # http の URL を https に直させない
    for gone in ("{PUBLIC_BASES}", "原本ホスト", "EXT_"):
        assert gone not in doc, gone


# --- _link_roots は route_of_url の結果だけで組む ------------------------------------


def _route(**fields):
    base = dict(
        rel="",
        query="",
        origin=None,
        kind="ext",
        relative=True,
        general=False,
        page_url=None,
    )
    base.update(fields)
    return lambda u: (types.SimpleNamespace(**base), None)


def test_page_url_follows_route_of_url(monkeypatch, ext_env):
    """ページ内リンクの基準は route_of_url の `page_url` (相対 → `/ext/{rest}`、絶対 →
    `{origin}/ext/{rest}`)。"""
    mod = _mod()
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    calls = []

    def route_of_url(u):
        calls.append(u)
        return _route(
            rel="his/top.aspx",
            origin="https://llens.example",
            relative=False,
            page_url="https://llens.example/ext/his/top.aspx",
        )(u)

    monkeypatch.setattr(mod, "route_of_url", route_of_url)
    result = env.run(EXT_TOP)
    assert calls == [EXT_TOP]
    assert "→ https://llens.example/ext/his/log_write.aspx?" in result, result
    assert mod.Tools._page_url(EXT_TOP) == "https://llens.example/ext/his/top.aspx"
    monkeypatch.setattr(
        mod,
        "route_of_url",
        _route(rel="his/top.aspx", query="?a=1", page_url="/ext/his/top.aspx?a=1"),
    )
    assert mod.Tools._page_url("/ext/his/top.aspx?a=1") == "/ext/his/top.aspx?a=1"


def test_directory_page_url_keeps_links_under_it(monkeypatch, ext_env):
    """ディレクトリ風の URL (`/ext/his/`) が 200 で返っても、リンクはその下 (`//` を作らない)。"""
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    links = _listed_links(env.run("/ext/his/"))
    assert links and all(
        link.startswith("/ext/his/") and "//" not in link for link in links
    ), links


def test_unroutable_url_after_a_successful_fetch_still_returns_the_text(
    monkeypatch, ext_env
):
    """resolver が通したのに route_of_url が組めない (構成のずれ) ときも本文は返す (ERROR にしない)。"""
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    monkeypatch.setattr(
        _mod(), "route_of_url", lambda u: (None, RefError("unknown_upstream", "x"))
    )
    result = env.run(EXT_TOP)
    assert not result.startswith("ERROR") and "令和８年度休日" in result


def test_general_route_resolves_links_like_ordinary_web(monkeypatch, ext_env):
    """一般の URL (route.general) はリンクをページの URL 基準の urljoin で解決する。
    名前空間 (/ext/{name}) を通さない。"""
    mod = _mod()
    page = "http://172.16.9.9/app/dir/page.aspx?x=1"
    env = _Env(monkeypatch, ext_env, _Loaded(body=_HREF_FORMS))
    monkeypatch.setattr(
        mod,
        "route_of_url",
        _route(
            kind="general",
            name=None,
            rel="app/dir/page.aspx",
            query="?x=1",
            origin="http://172.16.9.9",
            general=True,
            relative=False,
            page_url=page,
        ),
    )

    async def resolve(u, max_bytes=None):
        return _Loaded(body=_HREF_FORMS), None

    monkeypatch.setattr(mod, "resolve_url", resolve)
    result = env.run(page)
    assert result.startswith(f"# {page}\n種別:"), result
    links = _listed_links(result)
    assert "http://172.16.9.9/app/dir/unyou_mn.aspx" in links, links
    assert "http://172.16.9.9/his-info/x.pdf" in links, links
    assert all(link.startswith("http") and "/ext/" not in link for link in links), links


def test_real_routing_is_used_for_ext_and_general(monkeypatch, ext_env):
    """route_of_url の本物で `/ext/his/…` と一般の URL のページ URL が決まる (resolver は
    偽物のまま)。"""
    mod = _mod()
    page = _Loaded(body=_HREF_FORMS)
    result, _r = _run(monkeypatch, ext_env, "/ext/his/annai.aspx", resp=page)
    assert _listed_links(result) == ["/ext/his/unyou_mn.aspx"]
    result, _r = _run(monkeypatch, ext_env, f"{SITE}/annai.aspx", resp=page)
    assert _listed_links(result)[:3] == [
        f"{SITE}/unyou_mn.aspx",
        f"{SITE}/x.pdf",
        f"{SITE}/w.pdf",
    ]
    assert mod.Tools._page_url("/ext/x/y.html?q=1") == "/ext/x/y.html?q=1"
    assert mod.Tools._page_url("/mnt/x") == ""


# --- 登録 (`__init__.py`) --------------------------------------------------------------


@contextlib.contextmanager
def ext_gate():
    """builtin_tools を読み直す (test_builtin_sandbox の sandbox_env と同じ流儀。/ext に
    env は無いので差し替える物は無い)。"""
    import nucllei.builtin_tools as pkg

    importlib.reload(pkg)
    try:
        yield pkg
    finally:
        importlib.reload(pkg)


def test_fetch_url_is_always_registered():
    """env ゲート無しで載る (2026-10-03: url= はどこでも取りに行くので「必ず失敗する」builtin
    ではない)。feed_image も常に居る。"""
    with ext_gate() as pkg:
        entry = pkg.BUILTIN_TOOLS.get("builtin_fetch_url")
        assert entry is not None
        assert entry["module"] is _mod() and entry["name"] == "Fetch URL"
        assert pkg.is_builtin_tool_id("builtin_fetch_url") is True
        assert pkg.is_reserved_builtin_id("builtin_fetch_url") is True
        assert "builtin_feed_image" in pkg.BUILTIN_TOOLS
        assert "EXT_" not in entry["description"]


def test_builtin_specs_have_the_expected_function_names_and_valves():
    """spec は docstring から生成され、Valves は builtin でも載る (sync / 管理画面の編集経路)。"""
    from nucllei.utils.tools import get_tool_specs

    with ext_gate() as pkg:
        fetch = pkg.BUILTIN_TOOLS["builtin_fetch_url"]["module"]
        feed = pkg.BUILTIN_TOOLS["builtin_feed_image"]["module"]
        assert [s["name"] for s in get_tool_specs(fetch.Tools())] == ["fetch_url"]
        assert [s["name"] for s in get_tool_specs(feed.Tools())] == ["feed_image"]
        assert set(fetch.Tools.Valves.model_fields) == {
            "max_fetch_mb",
            "max_fetch_mb_ceiling",
            "max_output_chars",
            "pdf_text_char_threshold",
        }
        assert set(feed.Tools.Valves.model_fields) == {"max_image_mb", "max_pages"}
        # load_builtin_tool は Tools インスタンス + 空 frontmatter (plugin と同じ外形)
        tools, frontmatter = pkg.load_builtin_tool("builtin_fetch_url")
        assert isinstance(tools, fetch.Tools) and frontmatter == {}
        assert tools.valves.max_fetch_mb == 25.0
