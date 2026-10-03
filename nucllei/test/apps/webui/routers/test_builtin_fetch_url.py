"""builtin_fetch_url (Web のページと同一サーバの /ext/… を URL で 1 ページずつ辿る) のユニットテスト。

配備層 (prism-hu/llens の tests/test_fetch_url.py) から tool と一緒に移設 (#141、2026-10-03)。
pydantic stub / importlib のロードは落とし (nucllei の中なので直接 import する)、resolver の無い
旧 nucllei を見ていたテストも落とした。振る舞いの番兵は全部残す。

**実 HTTP は一切叩かない** — ネットワークの境界 `resolve_url` だけを module 属性の差し替えで
偽装し (`_FakeResolver`。受ける形と許可先の判定は nucllei の `_is_url` / `_route_url` と同じ規則で
模す。tool はこの判定を resolver に一任する)、ルーティング (`route_of_url` / `ext_backend` /
EXT_PUBLIC_BASES) は**本物**を env の dict を書き換えて通す。HTML は
`test/fixtures/fetch_url/his_*_excerpt.html` (実サイトの保存 HTML を必要な範囲だけ切り出した
もの)、PDF は `_pdf_to_text` の差し替えで代用する。

受理規則 (2026-10-03 ken 決定: url = 絶対 URL か同一サーバの相対 URL。引数名だけで解釈) を番兵する:
  - (a) EXT_PUBLIC_BASES の base 配下 = 原本 URL、(b) path が /ext/{name}/… の URL (相対 `/ext/…`
    と、host を問わない絶対形)、(c) それ以外のスキーム付き http(s) URL = 一般の URL (どこでも
    取りに行く。断るのはこのサーバ自身だけ — 同日の ken 決定)。それ以外の相対 (`/mnt/…`
    `/workspace/…` 等) は固定文の ERROR (url が受ける形を添える)
  - 戻り値のリンク一覧は原本 URL 1 本。原本ホストの無い上流は入力と同じ形の `/ext/…`、
    一般の URL はページ自身の URL 基準
  - RefError.code ごとに「何が起きたか + 次の一手」の ERROR 文 (組み立ては utils/ref_errors)

登録 (`__init__.py`): 常に BUILTIN_TOOLS に載る (env ゲート無し — 2026-10-03 に外した)。
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

PUBLIC_BASE = "https://med-info.med.hokudai.ac.jp/his-info"
UPSTREAM = "https://med-info.med.hokudai.ac.jp/his-info"
# 自オリジンの表示 URL (path が /ext/{name}/…)。host は resolver も tool も見ない。
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


# --- env (EXT_UPSTREAMS / EXT_PUBLIC_BASES) の差し替え ---------------------------
# ext_upstreams / image_refs / fetch_url は env の dict を名前で import している (同じ object) ので、
# 中身を書き換えて戻す。


@pytest.fixture
def ext_env():
    from nucllei import env

    saved = (dict(env.EXT_UPSTREAMS), dict(env.EXT_PUBLIC_BASES))

    def apply(upstreams: dict, bases: dict):
        env.EXT_UPSTREAMS.clear()
        env.EXT_UPSTREAMS.update(upstreams)
        env.EXT_PUBLIC_BASES.clear()
        env.EXT_PUBLIC_BASES.update(bases)

    yield apply
    apply(*saved)


# --- HTML → 平文 + リンク一覧 (変換ロジックだけを直接叩く) --------------------


def _convert(
    raw: bytes, page_path: str, public_base: str = PUBLIC_BASE, link_base=None
):
    return _mod()._html_to_lines(
        raw, page_path, public_base, "his-info", link_base or public_base
    )


def test_html_links_become_numbered_refs_with_one_absolute_url():
    body, links = _convert(_fixture("his_top_excerpt.html"), "top.aspx")
    text = "\n".join(body)
    assert "令和８年度休日" in text
    # 本文中では `ラベル [n]` になる
    assert "の通常診療体制について(再掲載) [1]" in text
    assert len(links) == 3
    # リンク一覧は絶対 URL (原本 URL) 1 本だけ。/ext/ の相対形も「(原本: …)」の併記も無い
    assert links[0].startswith("[1] 令和８年度休日")
    assert (
        f"→ {PUBLIC_BASE}/log_write.aspx?LI_BASE=TOP_NEW&LI_TB=REPO_TB"
        "&LI_URL=report_his_14071338.pdf" in links[0]
    )
    for ln in links:
        assert "(原本" not in ln and "/ext/" not in ln
        assert ln.count("http") == 1


def test_postback_and_fragment_links_are_not_listed():
    """`javascript:__doPostBack` と `#...` はリンク一覧に載せない (辿れないため)。
    ラベル自体は本文に残す。"""
    body, links = _convert(_fixture("his_top_excerpt.html"), "top.aspx")
    text = "\n".join(body)
    assert "院内電話番号表" in text  # postback リンクのラベルは本文に残る
    assert "院内電話番号表 [" not in text  # ただし番号は振らない
    joined = "\n".join(links)
    assert "__doPostBack" not in joined and "javascript:" not in joined
    assert all(not u.startswith("#") for u in _link_urls(links))


def test_script_style_and_hidden_inputs_are_dropped():
    body, links = _convert(_fixture("his_top_excerpt.html"), "top.aspx")
    text = "\n".join(body + links)
    assert "__VIEWSTATE" not in text  # ASP.NET の hidden input (数十 KB)
    assert "M_Click" not in text  # <script>
    assert "webkit-scrollbar" not in text  # <style>


def test_whitespace_is_normalized():
    body, _links = _convert(_fixture("his_unyou_excerpt.html"), "unyou_mn.aspx")
    assert body[0] == "連絡関連"
    assert all(line == line.strip() for line in body)
    assert all("  " not in line for line in body)
    assert all("　" not in line for line in body)


_HREF_FORMS = (
    "<html><body>"
    '<a href="unyou_mn.aspx">相対</a>'
    '<a href="/his-info/x.pdf">ルート相対</a>'
    f'<a href="{PUBLIC_BASE}/w.pdf">同一 origin の絶対</a>'
    '<a href="/other/app.aspx">アプリ外</a>'
    '<a href="https://example.com/z">別ホスト</a>'
    "</body></html>"
).encode("utf-8")


def test_href_forms_are_resolved_to_the_public_base():
    """相対 / ルート相対 / 同一 origin の絶対 / 別ホスト の 4 形を切り分ける。
    上流 base が `.../his-info` なので、ルート相対 `/his-info/x.pdf` は `<base>/x.pdf` に
    畳まれなければならない (`<base>/his-info/x.pdf` は誤り)。"""
    _body, links = _convert(_HREF_FORMS, "annai.aspx")
    assert _link_urls(links) == [
        f"{PUBLIC_BASE}/unyou_mn.aspx",
        f"{PUBLIC_BASE}/x.pdf",
        f"{PUBLIC_BASE}/w.pdf",
    ]


def test_href_forms_fall_back_to_the_ext_url_without_a_public_base():
    """原本 URL のルートが無い name は、入力と同じオリジンの /ext/{name}/… に畳む
    (次の fetch_url / feed_image には渡せる)。原本 origin の絶対 href は畳めないので落ちる。"""
    _body, links = _convert(
        _HREF_FORMS, "annai.aspx", public_base="", link_base=f"{ORIGIN}/ext/his"
    )
    assert _link_urls(links) == [
        f"{ORIGIN}/ext/his/unyou_mn.aspx",
        f"{ORIGIN}/ext/his/x.pdf",
    ]


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
    return _mod()._html_to_lines(
        raw, "x.aspx", PUBLIC_BASE, "his-info", PUBLIC_BASE, content_type
    )


# --- fetch_url 本体 (上流は偽装) ---------------------------------------------


class _Loaded:
    """image_refs.LoadedRef の使用面 (data / content_type / name) だけ模す。"""

    def __init__(self, content_type="text/html", body=b"", name="x"):
        self.data = body
        self.content_type = content_type
        self.name = name


class _FakeResolver:
    """image_refs の resolve_url ((loaded, RefError) を返す) の偽物。呼ばれた ref を記録する。
    受ける形と許可先の判定は nucllei の `_is_url` / `_route_url` と同じ規則で模す (tool は
    この判定を resolver に一任する)。error を与えればそれを返す (code ごとの文面の番兵用)。"""

    def __init__(self, loaded, error=None, ups=None, bases=None):
        self._loaded = loaded
        self._error = error
        self._ups = ups or {}
        self._bases = bases or {}
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
        m = re.match(r"^/ext/([a-z0-9_-]+)(?:/(.*))?$", parts.path)
        if m:
            if m.group(1) not in self._ups:
                return None, RefError("unknown_upstream", f"name={m.group(1)}")
            return self._loaded, None
        if not parts.scheme:
            return None, RefError(
                "unknown_upstream",
                "same-server URL is not of the form /ext/{name}/{path}",
            )
        hit = _match_public_base(self._bases)(ref)
        if hit is None:
            bare = ref.split("#", 1)[0].partition("?")[0].rstrip("/")
            hit = next(((n, "") for n, b in self._bases.items() if b == bare), None)
        if hit is None:
            # 一般の URL: どこでも取りに行く。断るのはこのサーバ自身 (loopback) だけ
            if parts.hostname in ("127.0.0.1", "localhost", "::1"):
                return None, RefError("unknown_upstream", "self", reason="server_self")
            return self._loaded, None
        name, rel = hit
        if name not in self._ups:
            return None, RefError("unknown_upstream", f"name={name}")
        return self._loaded, None


def _match_public_base(env_bases):
    """最長 `<base>/` 前方一致で (name, rel) (偽 resolver の判定用)。"""

    def match(url):
        best, best_len = None, -1
        for name, base in env_bases.items():
            prefix = base.rstrip("/") + "/"
            if url.startswith(prefix) and len(prefix) > best_len:
                best, best_len = (name, url[len(prefix) :]), len(prefix)
        return best

    return match


class _Env:
    """1 テスト分の道具立て: env の dict と偽 resolver を入れ、Tools を返す。"""

    def __init__(
        self,
        monkeypatch,
        ext_env,
        loaded,
        upstreams=None,
        env_bases=None,
        error=None,
        pdf_text=None,
    ):
        mod = _mod()
        ups = {"his": UPSTREAM} if upstreams is None else upstreams
        bases = {"his": PUBLIC_BASE} if env_bases is None else env_bases
        ext_env(ups, bases)
        self.resolver = _FakeResolver(loaded, error, ups, bases)
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
    upstreams=None,
    env_bases=None,
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
        upstreams,
        env_bases,
        error=error,
        pdf_text=pdf_text,
    )
    return env.run(url, **kwargs), env.resolver


def test_fetch_passes_the_public_url_to_the_resolver_and_renders_header(
    monkeypatch, ext_env
):
    """(a) 原本 URL: そのまま resolver に渡し、ヘッダは入力 URL 1 本 (原本行の重複無し)。"""
    result, resolver = _run(monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx")
    assert resolver.refs == [f"{PUBLIC_BASE}/top.aspx"]
    assert result.startswith(f"# {PUBLIC_BASE}/top.aspx\n")
    assert "原本 (職員に案内する URL)" not in result
    assert "注意:" not in result
    assert "種別: html" in result
    # リンク一覧は原本 URL 1 本
    assert f"→ {PUBLIC_BASE}/log_write.aspx?" in result and "/ext/" not in result


def test_fetch_accepts_the_ext_form_and_maps_links_to_the_public_base(
    monkeypatch, ext_env
):
    """(b) 自オリジンの /ext/{name}/… も受ける。resolver にはそのまま渡し、
    ヘッダに原本 URL を添え、リンクは原本 URL にする。"""
    result, resolver = _run(monkeypatch, ext_env, EXT_TOP)
    assert resolver.refs == [EXT_TOP]
    assert result.startswith(
        f"# {EXT_TOP}\n原本 (職員に案内する URL): {PUBLIC_BASE}/top.aspx\n"
    )
    assert f"→ {PUBLIC_BASE}/log_write.aspx?" in result
    assert f"{ORIGIN}/ext/" not in result.split("\n", 1)[1]


def test_query_string_is_passed_through(monkeypatch, ext_env):
    u = f"{PUBLIC_BASE}/log_write.aspx?LI_UID=G_UID&LI_URL=x.pdf"
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
    result, _c = _run(monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx", query="移送")
    assert 'query="移送" に一致した行:' in result
    assert "移送" in result
    assert "令和８年度休日" not in result  # 一致しない行は落ちる


def test_query_is_case_and_width_insensitive(monkeypatch, ext_env):
    result, _c = _run(
        monkeypatch,
        ext_env,
        f"{PUBLIC_BASE}/unyou_mn.aspx",
        query="ｐｈｓ",  # 全角小文字 → NFKC + casefold で "PHS" に当たる
        resp=_Loaded(body=_fixture("his_unyou_excerpt.html")),
    )
    assert "院内PHS電話番号簿" in result


def test_query_matches_link_lines_too(monkeypatch, ext_env):
    """本文行だけでなくリンク行も query の対象 (URL からファイル名で引ける)。"""
    result, _c = _run(
        monkeypatch,
        ext_env,
        f"{PUBLIC_BASE}/top.aspx",
        query="iji_kanzyaisou_01_rev2.pdf",
    )
    assert "iji_kanzyaisou_01_rev2.pdf" in result
    assert 'query="iji_kanzyaisou_01_rev2.pdf" に一致した行: 1 /' in result


def test_query_without_match_explains_instead_of_returning_everything(
    monkeypatch, ext_env
):
    result, _c = _run(
        monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx", query="存在しない語句xyz"
    )
    assert "一致する行が無い" in result
    assert "令和８年度休日" not in result


def test_offset_limit_and_next_offset(monkeypatch, ext_env):
    first, _c = _run(monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx", limit=40)
    assert "(続きあり)" in first and "next_offset=40" in first
    head, _sep, shown = first.partition("\n\n")
    assert len(shown) == 40
    second, _c = _run(
        monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx", offset=40, limit=40
    )
    assert "next_offset=80" in second
    assert second.partition("\n\n")[2] != shown
    whole, _c = _run(monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx", limit=100000)
    assert "(末尾まで)" in whole and "next_offset" not in whole


def test_limit_is_capped_by_valve(monkeypatch, ext_env):
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    env.tools.valves.max_output_chars = 30
    result = env.run(f"{PUBLIC_BASE}/top.aspx", limit=99999)
    assert len(result.partition("\n\n")[2]) == 30


def test_offset_past_the_end_is_an_error(monkeypatch, ext_env):
    result, _c = _run(monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx", offset=10**6)
    assert result.startswith("ERROR") and "末尾" in result


# --- 防壁: 絶対 URL 以外・許可先の外は受けない -----------------------------------
# 受理判定は resolver。tool は値を見ず、そのまま渡す (resolver.refs に残る)。


def test_accepts_a_same_server_relative_ext_url(monkeypatch, ext_env):
    """相対 `/ext/…` (read_doc の図・原本の形) はそのまま resolver に渡す。ホストを補わない。
    原本ルート (public_base) のある name は、相対入力でもリンクのルートが原本ルート (`/ext/` の
    ゲートは上流の 3xx を 502 に落とすので、利用者が開ける原本の URL を返す)。原本ルートの無い
    name だけ相対 `/ext/{name}/…` のまま。"""
    result, resolver = _run(monkeypatch, ext_env, "/ext/his/top.aspx")
    assert resolver.refs == ["/ext/his/top.aspx"]
    assert result.startswith(
        f"# /ext/his/top.aspx\n原本 (職員に案内する URL): {PUBLIC_BASE}/top.aspx\n"
    ), result
    assert f"→ {PUBLIC_BASE}/log_write.aspx?" in result and "→ /ext/his/" not in result
    result, resolver = _run(monkeypatch, ext_env, "/ext/his/top.aspx", env_bases={})
    assert resolver.refs == ["/ext/his/top.aspx"] and not result.startswith("ERROR")
    assert "→ /ext/his/log_write.aspx?" in result and "https://llens" not in result
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


def test_unknown_upstream_is_one_fixed_text_naming_the_bases(monkeypatch, ext_env):
    """未構成の name・形の欠けた `/ext/` は resolver の unknown_upstream。detail で分岐せず
    (reason 無しは) 1 つの固定文にし、登録された原本ホストを名乗る。"""
    for bad in (
        f"{ORIGIN}/ext/other/top.aspx",  # 未構成の name
        "/ext/",  # 配信元の名前が取れない同一サーバの URL
        "/ext/CAGE/x",  # 文字種外の name
    ):
        result, resolver = _run(monkeypatch, ext_env, bad)
        assert resolver.refs == [bad], bad
        assert result.startswith(
            f"ERROR: {bad} は許可された配信元の外、または `/ext/<配信元>/<ファイル>` の形でない URL。"
        ), (bad, result)
        assert f"`{PUBLIC_BASE}` (his)" in result and "EXT_" not in result, bad


def test_general_urls_are_fetched_and_the_server_itself_is_refused(
    monkeypatch, ext_env
):
    """登録に当たらない絶対 URL (外部サイト・同ホストの base の外・任意の LAN) は一般の URL
    として取りに行く (resolver 任せ。tool は行き先で断らない)。断るのはこのサーバ自身だけで、
    その文面は reason `server_self`。"""
    for u in (
        "https://example.com/x",
        "http://172.17.0.1:13303/static/x.png",
        "https://med-info.med.hokudai.ac.jp/other/app.aspx",  # 同ホスト・base の外
        "https://med-info.med.hokudai.ac.jp/his-info2/top.aspx",  # base の prefix 衝突
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
    """直下 (`<base>` `<base>/` `/ext/{name}` `/ext/{name}/`) は name のルートとして resolver が
    取りに行き、302 なら upstream_status (status=302)。redirect の案内文になる。
    tool は末尾 `/` を補わず、受け取った形のまま resolver に渡す。"""
    for u in (PUBLIC_BASE, f"{PUBLIC_BASE}/", "/ext/his", "/ext/his/"):
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
    """直下が 200 で返ったとき (rel == "")、リンクのルートは変わらず、`//` を作らない。"""
    for u in (f"{PUBLIC_BASE}/", PUBLIC_BASE, "/ext/his/", "/ext/his"):
        result, resolver = _run(monkeypatch, ext_env, u)
        assert resolver.refs == [u] and not result.startswith("ERROR"), (u, result)
        links = _listed_links(result)
        assert links and all(
            link.startswith(PUBLIC_BASE + "/") and "//" not in link.split("://", 1)[-1]
            for link in links
        ), (u, links)


def test_traversal_is_an_error_via_the_resolver(monkeypatch, ext_env):
    """traversal の判定は tool ではなく resolver (fail closed)。tool は「範囲の外」と言う。"""
    for bad in (f"{PUBLIC_BASE}/../secret.pdf", f"{ORIGIN}/ext/his/../secret.pdf"):
        result, _r = _run(monkeypatch, ext_env, bad)
        assert result.startswith("ERROR") and "許可された範囲の外" in result, bad
        assert result.endswith(URL_ARG_FORM), bad


def test_no_upstreams_configured(monkeypatch, ext_env):
    result, resolver = _run(
        monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx", upstreams={}
    )
    assert result.startswith("ERROR") and "許可された配信元の外" in result


def test_no_public_bases_configured_treats_original_urls_as_general(
    monkeypatch, ext_env
):
    """env EXT_PUBLIC_BASES が空なら原本 URL はただの一般の URL — そのホストに直接取りに行き、
    リンクはページ自身の URL 基準 (`/ext/` の注意書きは出ない)。/ext/ 形は backend のまま。
    unknown_upstream の ERROR に「原本ホストは未設定」の句は付けない (雑音)。"""
    u = f"{PUBLIC_BASE}/top.aspx"
    result, resolver = _run(monkeypatch, ext_env, u, env_bases={})
    assert not result.startswith("ERROR") and resolver.refs == [u]
    assert result.startswith(f"# {u}\n種別:") and "注意:" not in result
    assert _listed_links(result) and all(
        link.startswith(PUBLIC_BASE + "/") for link in _listed_links(result)
    )
    result, resolver = _run(monkeypatch, ext_env, EXT_TOP, env_bases={})
    assert not result.startswith("ERROR") and resolver.refs == [EXT_TOP]
    result, _r = _run(monkeypatch, ext_env, "/ext/nosuch/x", env_bases={})
    assert result.startswith("ERROR") and "原本ホスト" not in result


def test_trailing_question_mark_does_not_break_links(monkeypatch, ext_env):
    """URL 末尾が `?` だけ (query 空) でも、resolver にはその literal を渡し、
    リンクのルートは 1 字もずれない ((a)(b) 両形)。"""
    for u, root in (
        (f"{PUBLIC_BASE}/top.aspx?", PUBLIC_BASE),
        (f"{ORIGIN}/ext/his/top.aspx?", PUBLIC_BASE),
    ):
        result, resolver = _run(monkeypatch, ext_env, u)
        assert resolver.refs == [u], u
        assert result.startswith(f"# {u}\n"), u
        links = [ln for ln in result.split("\n") if re.match(r"^\[\d+\] ", ln)]
        assert len(links) == 3 and all(
            f"→ {root}/log_write.aspx?" in ln for ln in links
        ), u


def test_root_relative_hrefs_fold_on_the_public_base_path_not_the_backend_path(
    monkeypatch, ext_env
):
    """ページの `/his-info/x.pdf` は**職員側の origin** (EXT_PUBLIC_BASES) に対するルート相対。
    backend (EXT_UPSTREAMS) が別パスで中継する配備 (例 `http://relay/apps/his`) でも、
    prefix は原本ルートのパス (`his-info`) から取る。原本ルートが無い name だけ backend に落ちる。"""
    page = _Loaded(body=_HREF_FORMS)
    result, _r = _run(
        monkeypatch,
        ext_env,
        f"{PUBLIC_BASE}/annai.aspx",
        resp=page,
        upstreams={"his": "http://relay.example/apps/his"},
    )
    assert _listed_links(result) == [
        f"{PUBLIC_BASE}/unyou_mn.aspx",
        f"{PUBLIC_BASE}/x.pdf",
        f"{PUBLIC_BASE}/w.pdf",
    ]
    # 原本ルート無し: backend のパス (`apps/his`) が prefix。`/his-info/x.pdf` はその外なので落ちる
    result, _r = _run(
        monkeypatch,
        ext_env,
        f"{ORIGIN}/ext/his/annai.aspx",
        resp=page,
        env_bases={},
        upstreams={"his": "http://relay.example/apps/his"},
    )
    assert _listed_links(result) == [f"{ORIGIN}/ext/his/unyou_mn.aspx"]


def test_error_text_names_the_same_bases_the_router_uses(monkeypatch, ext_env):
    """ERROR 文の原本ホストはルーティングと同じ dict (EXT_PUBLIC_BASES) から出る。"""
    other = "https://other.example/docs"
    result, _r = _run(
        monkeypatch,
        ext_env,
        f"{ORIGIN}/ext/nosuch/x",
        env_bases={"his": PUBLIC_BASE, "other": other},
        upstreams={"his": UPSTREAM, "other": other},
    )
    assert f"`{PUBLIC_BASE}` (his)" in result and f"`{other}` (other)" in result


def test_requires_user(monkeypatch, ext_env):
    env = _Env(monkeypatch, ext_env, _Loaded(body=b"<html></html>"))
    result = asyncio.run(env.tools.fetch_url(url=f"{PUBLIC_BASE}/top.aspx"))
    assert result.startswith("ERROR") and env.resolver.refs == []


# --- PDF -----------------------------------------------------------------


def test_pdf_without_text_layer_points_to_feed_image(monkeypatch, ext_env):
    u = f"{PUBLIC_BASE}/scan.pdf"
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
    u = f"{PUBLIC_BASE}/log_write.aspx?LI_URL=scan.pdf"
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
        f"{PUBLIC_BASE}/log_write.aspx?LI_URL=x.pdf",
        pdf_text="B" * 300,
        resp=_Loaded(content_type="application/octet-stream", body=b"%PDF-1.7 x"),
    )
    assert "種別: pdf" in result and "BBB" in result


# --- 取得失敗 / サイズ / 原本 URL 未設定 ------------------------------------


def test_resolver_failure_without_a_reason_falls_back_to_the_bundled_text(
    monkeypatch, ext_env
):
    """resolve_url が (None, None) を返した (通常起きない): 理由が無いので束ねた 1 文
    (redirect を辿らないことだけは案内する)。"""
    result, resolver = _run(monkeypatch, ext_env, f"{PUBLIC_BASE}/index", resp=False)
    assert resolver.refs == [f"{PUBLIC_BASE}/index"]
    assert result.startswith(f"ERROR: {PUBLIC_BASE}/index を取得できない")
    assert "redirect" in result and "top.aspx" in result


def test_fetch_passes_the_valve_size_cap_to_the_resolver_as_max_bytes(
    monkeypatch, ext_env
):
    """文書を読む用途の上限 (valve max_fetch_mb、既定 25 = 控えめ) を max_bytes で渡す — 画像
    inline 用の既定上限を借りない。"""
    result, resolver = _run(monkeypatch, ext_env, f"{PUBLIC_BASE}/top.aspx")
    assert not result.startswith("ERROR")
    assert resolver.max_bytes == [25 * 1024 * 1024]
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    env.tools.valves.max_fetch_mb = 2.5
    env.run(f"{PUBLIC_BASE}/top.aspx")
    assert env.resolver.max_bytes == [int(2.5 * 1024 * 1024)]


def test_max_mb_argument_is_capped_by_the_ceiling(monkeypatch, ext_env):
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    env.run(f"{PUBLIC_BASE}/top.aspx", max_mb=30)
    env.run(f"{PUBLIC_BASE}/top.aspx", max_mb=10_000)
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

_PDF_URL = f"{PUBLIC_BASE}/log_write.aspx?LI_UID=G_UID&LI_URL=big.pdf"


def _explained(
    monkeypatch, ext_env, code, url=_PDF_URL, upstreams=None, env_bases=None, **kw
):
    kw.setdefault("detail", "")
    result, resolver = _run(
        monkeypatch,
        ext_env,
        url,
        error=RefError(code, **kw),
        upstreams=upstreams,
        env_bases=env_bases,
    )
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
        monkeypatch, ext_env, "upstream_status", url=f"{PUBLIC_BASE}/dir", status=302
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
    """unknown_upstream の detail 文言は変わりうるので分岐に使わない — どの detail でも同文。"""
    ups = {"his": UPSTREAM, "other": "https://other.example/docs"}
    texts = set()
    for detail in (
        "name=other",
        "absolute URL is neither an /ext/{name}/ URL nor under EXT_PUBLIC_BASES (external site: https://example.com)",
        "same-server URL is not of the form /ext/{name}/{path}",
        "",
    ):
        texts.add(
            _explained(
                monkeypatch,
                ext_env,
                "unknown_upstream",
                url=_PDF_URL,
                upstreams=ups,
                detail=detail,
            )
        )
    assert len(texts) == 1
    (text,) = texts
    assert (
        "許可された配信元の外" in text
        and PUBLIC_BASE in text
        and text.endswith(URL_ARG_FORM)
    )


def test_unknown_upstream_is_told_apart_by_reason(monkeypatch, ext_env):
    """`RefError.reason` で言い分ける (detail は見ない)。"""
    want = {
        "not_ext_form": "配信元の名前が取れない",
        "unknown_name": "配信元は無い",
        "server_self": "このサーバ自身",
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
        assert f"`{PUBLIC_BASE}` (his)" in result, reason


def test_traversal_code_says_out_of_bounds(monkeypatch, ext_env):
    result = _explained(monkeypatch, ext_env, "traversal", url=f"{PUBLIC_BASE}/a.pdf")
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
    result = env.run(f"{PUBLIC_BASE}/top.aspx")
    assert result.startswith("ERROR") and "大きすぎる" in result
    assert (
        "0.0 MB で大きすぎる (この呼び出しの上限 0.0 MB)" in result
    )  # 4 KB / 1 KB を MB 小数 1 桁で
    assert "URL をそのまま案内" in result


def test_unsupported_content_type_hands_back_the_public_url(monkeypatch, ext_env):
    u = f"{PUBLIC_BASE}/log_write.aspx?LI_URL=x.xls"
    result, _c = _run(
        monkeypatch,
        ext_env,
        u,
        resp=_Loaded(content_type="application/vnd.ms-excel", body=b"\xd0\xcf"),
    )
    assert result.startswith("ERROR") and "平文にできない形式" in result
    assert u in result


def test_image_guides_to_feed_image_not_to_a_dead_end(monkeypatch, ext_env):
    """画像は平文にできないが行き止まりではない — feed_image に同じ URL を渡して見る。"""
    u = f"{PUBLIC_BASE}/log_write.aspx?LI_URL=fig.png"
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
            f"{PUBLIC_BASE}/api/x",
            resp=_Loaded(content_type=ct, body=b'{"a": 1}\n\n{"b": 2}\n'),
        )
        assert (
            "種別: text" in result and '{"a": 1}' in result and '{"b": 2}' in result
        ), (ct, result)


def test_ext_form_without_public_base_keeps_links_absolute_and_says_so(
    monkeypatch, ext_env
):
    """EXT_PUBLIC_BASES に無い name の /ext/ 形: リンクは入力と同じ形の /ext/ URL
    (次の tool に渡せ、本文に貼れる) にし、原本ホストが無い旨をヘッダに 1 行出す。
    上流の name (内部名) は出さない。"""
    result, resolver = _run(monkeypatch, ext_env, EXT_TOP, env_bases={})
    assert not result.startswith("ERROR") and resolver.refs == [EXT_TOP]
    assert "注意: この資料の配信元には原本ホストが無い" in result
    assert "(his)" not in result
    assert "原本 (職員に案内する URL)" not in result
    assert "med-info.med.hokudai.ac.jp" not in result
    links = [ln for ln in result.split("\n") if re.match(r"^\[\d+\] ", ln)]
    assert links and all(f"→ {ORIGIN}/ext/his/" in ln for ln in links)


# --- _link_roots は route_of_url の結果だけで組む ------------------------------------


def _route(**fields):
    base = dict(
        name="his",
        rel="",
        query="",
        public_base=None,
        origin=None,
        relative=True,
        general=False,
        page_url=None,
    )
    base.update(fields)
    return lambda u: (types.SimpleNamespace(**base), None)


def test_link_roots_follow_route_of_url(monkeypatch, ext_env):
    """リンクのルートは route_of_url で引く (相対 → `/ext/{name}`、絶対で原本ルートあり → それ、
    無し → `{origin}/ext/{name}`)。"""
    mod = _mod()
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    calls = []

    def route_of_url(u):
        calls.append(u)
        return _route(rel="top.aspx", origin="https://llens.example", relative=False)(u)

    monkeypatch.setattr(mod, "route_of_url", route_of_url)
    result = env.run(EXT_TOP)
    assert calls == [EXT_TOP]
    assert "→ https://llens.example/ext/his/log_write.aspx?" in result, result


def test_route_of_url_with_an_empty_rel_keeps_the_link_root(monkeypatch, ext_env):
    """直下で rel == "" でも、リンクのルートは `/ext/{name}` のまま (`//` を作らない)。"""
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    monkeypatch.setattr(_mod(), "route_of_url", _route(rel=""))
    links = _listed_links(env.run("/ext/his"))
    assert links and all(
        link.startswith("/ext/his/") and "//" not in link for link in links
    ), links


def test_route_of_url_with_a_public_base_links_to_the_original_even_for_relative_input(
    monkeypatch, ext_env
):
    """public_base のある name (http backend) は、相対入力でもリンクのルートを public_base にする。"""
    env = _Env(monkeypatch, ext_env, _Loaded(body=_fixture("his_top_excerpt.html")))
    monkeypatch.setattr(
        _mod(), "route_of_url", _route(rel="top.aspx", public_base=PUBLIC_BASE)
    )
    links = _listed_links(env.run("/ext/his/top.aspx"))
    assert links and all(link.startswith(PUBLIC_BASE + "/") for link in links), links


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
    名前空間 (/ext/{name}・原本ルート) を通さず、ヘッダに /ext/ の注意書きも出さない。"""
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
            ext_form=False,
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


# --- docstring (spec) は env の許可先を名乗る ------------------------------------------


def test_docstring_names_the_public_bases_from_env(ext_env):
    """tool の説明文 (spec) は nucllei.env.EXT_PUBLIC_BASES (受理判定と同じ dict) を名乗る。
    埋め込みは module 読み込み時なので reload で通す。"""
    from nucllei import env

    mod = _mod()
    saved = (dict(env.EXT_UPSTREAMS), dict(env.EXT_PUBLIC_BASES))
    try:
        ext_env({"his": UPSTREAM}, {"his": PUBLIC_BASE})
        doc = importlib.reload(mod).Tools.fetch_url.__doc__
        assert "{PUBLIC_BASES}" not in doc
        assert f"登録された原本ホスト (`{PUBLIC_BASE}` (his))" in doc
        assert (
            "http(s)://" in doc and "`https://…`" not in doc
        )  # http の原本を https に直させない

        ext_env({}, {})
        doc = importlib.reload(mod).Tools.fetch_url.__doc__
        assert "{PUBLIC_BASES}" not in doc
        assert "登録された原本ホスト ((未設定" in doc and "med-info" not in doc
    finally:
        ext_env(*saved)  # env を戻してから reload し、docstring を元の配備の値に戻す
        importlib.reload(mod)


# --- 登録 (`__init__.py`) --------------------------------------------------------------


@contextlib.contextmanager
def ext_gate(upstreams=None, bases=None):
    """EXT_UPSTREAMS / EXT_PUBLIC_BASES を差し替えて builtin_tools を読み直す
    (test_builtin_sandbox の sandbox_env と同じ流儀)。"""
    import nucllei.builtin_tools as pkg
    from nucllei import env

    saved = (dict(env.EXT_UPSTREAMS), dict(env.EXT_PUBLIC_BASES))

    def _apply(ups, bs):
        env.EXT_UPSTREAMS.clear()
        env.EXT_UPSTREAMS.update(ups)
        env.EXT_PUBLIC_BASES.clear()
        env.EXT_PUBLIC_BASES.update(bs)
        importlib.reload(pkg)

    _apply(upstreams or {}, bases or {})
    try:
        yield pkg
    finally:
        _apply(*saved)


def test_fetch_url_is_always_registered():
    """配信元も原本ホストも無い配備でも載る (2026-10-03: url= はどこでも取りに行くので、
    env が空でも「必ず失敗する」builtin ではない)。feed_image も常に居る。"""
    for kw in (
        {},
        dict(upstreams={"cage": "file:///tmp"}),
        dict(bases={"his": PUBLIC_BASE}),
    ):
        with ext_gate(**kw) as pkg:
            entry = pkg.BUILTIN_TOOLS.get("builtin_fetch_url")
            assert entry is not None, kw
            assert entry["module"] is _mod() and entry["name"] == "Fetch URL"
            assert pkg.is_builtin_tool_id("builtin_fetch_url") is True
            assert pkg.is_reserved_builtin_id("builtin_fetch_url") is True
            assert "builtin_feed_image" in pkg.BUILTIN_TOOLS
            assert "EXT_" not in entry["description"]


def test_builtin_specs_have_the_expected_function_names_and_valves():
    """spec は docstring から生成され、Valves は builtin でも載る (sync / 管理画面の編集経路)。"""
    from nucllei.utils.tools import get_tool_specs

    with ext_gate(upstreams={"his": UPSTREAM}, bases={"his": PUBLIC_BASE}) as pkg:
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
