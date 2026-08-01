"""EXT_UPSTREAMS のパースと path traversal 判定の回帰テスト(#97)。

- parse_ext_upstreams(env.py): "name=baseURL" のカンマ区切り。name は [a-z0-9_-]+、
  baseURL は http/https のみ。不正エントリは warning して捨てる(全体は殺さない)。
  既定空 = {} = 機能無効。
- has_path_traversal(utils/ext_upstreams.py): プロキシ router と inline 解決が
  共有する同一判定。percent デコード(多重エンコード対策で安定するまで)後の
  ".." セグメントとバックスラッシュを拒否する。
"""

import httpx

from nucllei.env import parse_ext_upstreams
from nucllei.utils.ext_upstreams import _NoCookies, has_path_traversal


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
                "ftp=ftp://x",  # スキームが http/https でない
                "file=file:///etc/passwd",  # 同上
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
