"""EXT_UPSTREAMS(#97)の共有ヘルパ。

"/ext/{name}/**" ゲート(routers/ext.py — 人間の目)と context_images の
"/ext/..." オンデマンド解決(utils/image_refs.py — モデルの目)が同じ基準を共有する:

- backend allowlist は env EXT_UPSTREAMS 一本(SSRF の真実源。別 allowlist は作らない)。
  値の形で backend が決まる(#139): http(s) の base URL = HTTP 中継、
  `file:///abs/path` = ローカル root からの静的配信(`ext_backend`)
- path traversal 拒否は同一判定(percent デコード後の ".." セグメント・バックスラッシュ)
- httpx AsyncClient はモジュールで 1 個を共有(リクエスト毎に生成しない)。
  follow_redirects は常に False — 上流の redirect 先(内部 URL)を辿らない・漏らさない
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import httpx

from nucllei.env import EXT_UPSTREAMS

# 接続 5s / 読み 30s。上流はローカル(Docker bridge)想定 — 不達・遅延で
# ワーカーを長く掴ませない。
EXT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class _NoCookies(httpx.Cookies):
    """上流の Set-Cookie を一切保持・再送しない cookie jar。

    共有 AsyncClient を全ユーザー・全リクエストで使い回すため、既定 jar だと
    上流の Set-Cookie が蓄積して別ユーザーのリクエストに混入する(#97 review)。
    extract(保存)/ set_cookie_header(送出)を no-op 化して stateless に保つ。
    """

    def extract_cookies(self, response: httpx.Response) -> None:  # noqa: D102
        return None

    def set_cookie_header(self, request: httpx.Request) -> None:  # noqa: D102
        return None


_client: httpx.AsyncClient | None = None


def get_ext_client() -> httpx.AsyncClient:
    """共有 AsyncClient(遅延生成)。テストは `_client` を MockTransport 付きの
    インスタンスへ差し替える。cookie は保持しない(_NoCookies)。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=False, timeout=EXT_TIMEOUT, cookies=_NoCookies()
        )
    return _client


def has_path_traversal(path: str) -> bool:
    """上流へ渡してはいけないパスか(traversal / 制御文字)。

    - ".." セグメント(percent デコード後)またはバックスラッシュ → 拒否。
      多重エンコード("..%252F" 等)対策でデコードは安定するまで繰り返す。
    - ASCII 制御文字・非印字(CR/LF/NUL/TAB 等)→ 拒否。これらは httpx の
      URL 構築を InvalidURL で落とし(funnel を逸れて 500 になる)、CRLF は
      ヘッダインジェクションの臭いでもあるため、共有ゲートで fail-closed する
      (#97 review — プロキシは 400、inline は "(image unavailable)" へ合流)。

    判定は拒否のためだけ — 上流へはデコード前のパスをそのまま渡す。
    """
    decoded = path
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in decoded):
        return True
    if "\\" in decoded:
        return True
    return ".." in decoded.split("/")


@lru_cache(maxsize=None)
def _resolved_root(value: str) -> Path:
    """`file:///abs/path` の root を **1 回だけ** resolve() する。

    配備では root 自体が symlink(compose の bind 先)でありうる。要求パスの閉じ込め
    (`is_relative_to`)は resolve 済み同士で比べないと成り立たないので、root 側も
    resolve しておく。値は起動時に env から確定していて変わらないため、要求ごとに
    resolve し直す理由が無い(strict=False — 無い root は "全 404" に倒れるだけ。
    有無は起動時に image_refs.log_mounts が WARNING で知らせる)。
    """
    return Path(value[len("file://") :]).resolve()


def ext_backend(name: str) -> tuple[str, str] | tuple[str, Path] | None:
    """name の backend を `("http", base_url)` / `("file", resolved_root)` で返す。

    map に無い name(機能無効を含む)は None。呼ぶ側は None を「無い」(404 /
    unavailable)として扱い、backend 種別で経路を分ける — 値の形の解釈をここ 1 箇所に
    置き、router と resolver が別々に文字列を見ないようにする。
    """
    value = EXT_UPSTREAMS.get(name)
    if value is None:
        return None
    if value.startswith("file://"):
        return ("file", _resolved_root(value))
    return ("http", value)
