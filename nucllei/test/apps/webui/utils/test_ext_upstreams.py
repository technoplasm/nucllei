"""EXT_UPSTREAMS のパースと path traversal 判定の回帰テスト(#97)。

- parse_ext_upstreams(env.py): "name=値" のカンマ区切り。name は [a-z0-9_-]+、
  値は http/https の base URL か `file:///abs/path`(#139 ローカル root)。不正
  エントリは warning して捨てる(全体は殺さない)。既定空 = {} = 機能無効。
- ext_backend(utils/ext_upstreams.py): 値の形で ("http", base_url) / ("file", root)
  に分ける。file の root は resolve 済み(symlink 越え判定の基準)。
- has_path_traversal(utils/ext_upstreams.py): プロキシ router と inline 解決が
  共有する同一判定。percent デコード(多重エンコード対策で安定するまで)後の
  ".." セグメントとバックスラッシュを拒否する。
"""

import httpx

from nucllei.env import (
    EXT_PUBLIC_BASES,
    EXT_UPSTREAMS,
    parse_ext_public_bases,
    parse_ext_upstreams,
)
from nucllei.utils.ext_upstreams import (
    _NoCookies,
    ext_backend,
    has_path_traversal,
    match_public_base,
)

# ---- parse_ext_upstreams ----


def test_parse_single_entry():
    assert parse_ext_upstreams("cage=http://172.17.0.1:13303") == {
        "cage": "http://172.17.0.1:13303"
    }


def test_parse_multiple_entries_with_spaces_and_trailing_slash():
    assert parse_ext_upstreams(
        " cage=http://172.17.0.1:13303 , docs=https://example.com/base/ "
    ) == {
        "cage": "http://172.17.0.1:13303",
        "docs": "https://example.com/base",
    }


def test_parse_empty_is_disabled():
    assert parse_ext_upstreams("") == {}
    assert parse_ext_upstreams("  ") == {}
    assert parse_ext_upstreams(None) == {}


def test_parse_drops_invalid_entries_keeps_valid():
    parsed = parse_ext_upstreams(
        ",".join(
            [
                "cage=http://172.17.0.1:13303",
                "no-equals-here",  # "=" なし
                "Bad=http://x",  # name に大文字
                "sp ace=http://x",  # name に空白
                "ftp=ftp://x",  # スキームが http/https/file でない
                "rel=file://relative/path",  # file は絶対パスのみ
                "hostful=file://host/etc",  # 同上(host 付き = 絶対でない)
                "slash=file:///",  # root "/" は捨てる
                "=http://x",  # name 空
                "empty=",  # baseURL 空
            ]
        )
    )
    assert parsed == {"cage": "http://172.17.0.1:13303"}


def test_parse_base_url_may_contain_equals():
    # partition は最初の "=" で切る(baseURL 側の "=" は許容)
    assert parse_ext_upstreams("cage=http://x/base?sig=abc=") == {
        "cage": "http://x/base?sig=abc="
    }


def test_parse_file_root(monkeypatch):
    # #139: file:///abs/path はローカル root backend。末尾スラッシュは落とす
    assert parse_ext_upstreams("cage=file:///mnt/cage/") == {"cage": "file:///mnt/cage"}


# ---- ext_backend ----


def test_ext_backend_http_and_file(monkeypatch, tmp_path):
    monkeypatch.setitem(EXT_UPSTREAMS, "his", "http://172.17.0.1:13303")
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{tmp_path}")
    assert ext_backend("his") == ("http", "http://172.17.0.1:13303")
    assert ext_backend("cage") == ("file", tmp_path.resolve())
    assert ext_backend("nope") is None


def test_ext_backend_file_root_is_resolved(monkeypatch, tmp_path):
    # 配備では root 自体が symlink(bind 先)— 閉じ込め判定の基準は実体側
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setitem(EXT_UPSTREAMS, "cage", f"file://{link}")
    assert ext_backend("cage") == ("file", real.resolve())


# ---- has_path_traversal ----


def test_traversal_plain_dotdot_segment():
    assert has_path_traversal("../secret") is True
    assert has_path_traversal("a/../b") is True
    assert has_path_traversal("a/b/..") is True


def test_traversal_percent_encoded():
    assert has_path_traversal("..%2Fsecret") is True
    assert has_path_traversal("%2e%2e/secret") is True


def test_traversal_double_encoded():
    assert has_path_traversal("..%252Fsecret") is True


def test_traversal_backslash():
    assert has_path_traversal("a\\b") is True
    assert has_path_traversal("a%5Cb") is True


def test_safe_paths_pass():
    assert has_path_traversal("figs/a.png") is False
    assert has_path_traversal("a..b/c.png") is False  # ".." を含む名前は別物
    assert has_path_traversal("...dots/file") is False
    assert has_path_traversal("") is False


def test_traversal_control_chars():
    # CRLF / NUL / TAB / DEL は httpx の URL 構築を落とす + ヘッダ注入の臭い
    assert has_path_traversal("a\r\nb") is True
    assert has_path_traversal("a\x00b") is True
    assert has_path_traversal("a\tb") is True
    assert has_path_traversal("a\x7fb") is True
    assert has_path_traversal("a%0d%0ab") is True  # percent 形も安定デコード後に検出


# ---- _NoCookies(共有クライアントが上流 Set-Cookie を保持しない) ----


def test_nocookies_does_not_store_or_send():
    jar = _NoCookies()
    resp = httpx.Response(
        200,
        headers={"set-cookie": "internal=1; Path=/"},
        request=httpx.Request("GET", "http://x/"),
    )
    jar.extract_cookies(resp)
    assert len(jar) == 0  # 上流 Set-Cookie を保持しない
    req = httpx.Request("GET", "http://x/next")
    jar.set_cookie_header(req)
    assert "cookie" not in req.headers  # 後続へ再送しない


# ---- parse_ext_public_bases / match_public_base(#139 名前空間)----


def test_parse_public_bases_shares_format_but_http_only():
    # EXT_UPSTREAMS と同じ "name=値" のカンマ区切り。値は http(s) だけ(file:// は
    # 「ブラウザが開く URL」でないので捨てる)。末尾スラッシュは落とす
    assert parse_ext_public_bases(
        "his=http://his-info.test/docs/, cage=file:///mnt/cage, bad, x=ftp://h"
    ) == {"his": "http://his-info.test/docs"}
    assert parse_ext_public_bases("") == {}


def test_match_public_base(monkeypatch):
    monkeypatch.setitem(EXT_PUBLIC_BASES, "his", "http://his-info.test/docs")
    monkeypatch.setitem(EXT_PUBLIC_BASES, "deep", "http://his-info.test/docs/deep")
    assert match_public_base("http://his-info.test/docs/a/b.png?x=1#page=2") == (
        "his",
        "a/b.png?x=1#page=2",
    )
    # 長い base が勝つ
    assert match_public_base("http://his-info.test/docs/deep/c.png") == (
        "deep",
        "c.png",
    )
    # "<base>/" で切る — 似た prefix・scheme 違いは当たらない
    assert match_public_base("http://his-info.test/docs2/a.png") is None
    assert match_public_base("http://his-info.test/docs2") is None
    # base そのもの(末尾 "/" の有無によらず)は path 部が空の rel = その name のルート
    assert match_public_base("http://his-info.test/docs") == ("his", "")
    assert match_public_base("http://his-info.test/docs/") == ("his", "")
    assert match_public_base("http://his-info.test/docs?x=1#f") == ("his", "?x=1#f")
    assert match_public_base("http://his-info.test/docs/deep") == ("deep", "")
    assert match_public_base("https://his-info.test/docs/a.png") is None
    # scheme / host は小文字化して比較(base 側も)。userinfo は netloc の一部なので外れる
    monkeypatch.setitem(EXT_PUBLIC_BASES, "up", "HTTPS://Host.test:8443/Docs")
    assert match_public_base("https://HOST.test:8443/Docs/a.png") == ("up", "a.png")
    assert match_public_base("https://host.test/Docs/a.png") is None
    assert match_public_base("http://his-info.test@evil/docs/x.png") is None
    assert match_public_base("http://[::1/docs/x.png") is None
