"""/ext の共有ヘルパ(`utils/ext_upstreams.py`)の回帰テスト(#97 / #139 / 2026-10-03)。

- `/ext/**` の配信に設定は無い(2026-10-03 ken 決定): nucllei の `/mnt` をそのまま配信する。
  旧 EXT_UPSTREAMS / EXT_FILE_SCOPES / EXT_PUBLIC_BASES(と同日に一度置いた EXT_DIRS)は
  env にもパーサにも helper にも残さない。
- has_path_traversal: /ext ゲートと resolver が共有する同一判定。percent デコード
  (多重エンコード対策で安定するまで)後の ".." セグメントとバックスラッシュを拒否する。
- _NoCookies: 共有クライアントが上流 Set-Cookie を保持しない。
- 改名(同日): EXT_SELF_ADDRESSES → SELF_ADDRESSES、EXT_INLINE_MAX_BYTES → FETCH_MAX_BYTES。
"""

import httpx

from nucllei.utils.ext_upstreams import _NoCookies, has_path_traversal


def test_env_has_no_ext_serving_config():
    from nucllei import env
    from nucllei.utils import ext_upstreams, image_refs

    for gone in (
        "EXT_UPSTREAMS",
        "EXT_FILE_SCOPES",
        "EXT_PUBLIC_BASES",
        "EXT_DIRS",
        "EXT_SELF_ADDRESSES",
        "EXT_INLINE_MAX_BYTES",
        "parse_ext_upstreams",
        "parse_ext_file_scopes",
        "parse_ext_public_bases",
        "parse_ext_dirs",
        "parse_ext_self_addresses",
    ):
        assert not hasattr(env, gone), gone
    assert env.SELF_ADDRESSES == frozenset()
    assert env.FETCH_MAX_BYTES == 25 * 1024 * 1024
    for gone in (
        "ext_backend",
        "ext_dir",
        "ext_dir_of",
        "ext_file_served",
        "ext_file_withheld",
        "match_public_base",
        "_served_roots",
        "EXT_DIRS",
    ):
        assert not hasattr(ext_upstreams, gone), gone
    for gone in ("_fetch_http", "mounted_path_withheld", "_withheld"):
        assert not hasattr(image_refs, gone), gone
    fields = image_refs.UrlRoute.__dataclass_fields__
    assert set(fields) == {"rel", "query", "origin", "kind"}


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
