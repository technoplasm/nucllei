"""認証付き参照資料の配信ゲート "/ext/**"(#97 / #139 / #146)。

cage(HTTP ツールゲートウェイ)等が配信する文書・図・PDF は Docker bridge 内部 URL や
コンテナ内パスでしか参照できず、ブラウザから到達できない。上流側が PUBLIC_BASE を
"/ext/<name>"(相対)にすると文書内の参照が同一オリジンの URL になり、チャット表示・
display すべてでブラウザが session cookie 付きでそのまま解決できる(相対なので origin や
http/https が変わっても壊れない)。

**入口は 1 つの対応表 env `EXT_MOUNTS`**(`name=value` のカンマ区切り。2026-10-05):
`/ext/<name>` → 値。値が `http://` / `https://` なら **proxy**、絶対パスなら **静的配信**。
表に無い name は 404(`/ext` 自体・`/ext/` も 404)。例 `cage=http://cage:13303,shared=/ext/shared`。

経緯: ken 2026-10-05「nucはproyxするだけでしょ」「md を静的に生成して html はその場で変換」、続けて
「ext/cage が proxy になればいい / 静的配信はそれで残ってていい / 入口を一つにして、このパスは
proxy、このパスは静的、みたいにしたらいい。url を入れるかディレクトリを入れるかで
/ext/cage : localhost  /ext/shared : /path/to/shared」。2026-10-03 に `EXT_UPSTREAMS` 等を撤去して
`EXT_DIR`(置いた物を暗黙に全部配る)一本にしたのを改め、値の形で静的と proxy を選べる 1 本の
対応表に戻した。目的は doc の配信(md → HTML・パンくず・index・見出し id)を cage に持たせること —
nucllei は中継するだけ。同じ日に一度入れた静的配信の `X` → `X.html` → `X/index.html` の補完は撤去。

**proxy**(`/ext/<name>/<rest>?<query>` → `<base>/<rest>?<query>`):

- GET / HEAD のみ(上流にも同じメソッドで送る)。それ以外は 405
- パス: percent デコード後に `..` セグメント・バックスラッシュ・制御文字があれば 400。上流へは
  デコード済みのパスを quote し直して送る(`?` `#` `@` `:` 等は path の中に閉じ、base の外へ
  出ない)。クエリはそのまま。fragment はブラウザから来ない
- 要求ヘッダは許可リスト(`FORWARD_REQUEST_HEADERS`: Range / If-Range / If-None-Match /
  If-Modified-Since / Accept / Accept-Language)だけ転送。Cookie・Authorization は送らない。
  Accept-Encoding は常に identity(Content-Length / Range をそのまま通すため。それでも符号化して
  返す上流は、復号して流し Content-Length を落とす)
- 応答はステータスを保ってストリーミング(200 / 206 / 304 / 404 …)。応答ヘッダも許可リスト
  (`PASSTHROUGH_RESPONSE_HEADERS`)— Set-Cookie 等は落ちる
- リダイレクトは追わない。Location が上流の base 配下(`ext_upstreams.under_base`)なら
  `/ext/<name>/…` に書き換えて同じステータスで返す(ディレクトリの末尾 `/` を付ける 301 を想定)。
  base の外・Location 無しは 502(内部 URL を漏らさない)
- `text/html` には `HTML_CSP` と nosniff を nucllei が付ける(上流の値は上書き)。それ以外は
  `active_content_headers`(SVG / JS は添付 + nosniff)
- 上流に届かなければ 502、タイムアウト(`EXT_PROXY_TIMEOUT_S`)は 504
- 上流は配備が書いた固定の相手なので、url= の一般 URL の自分自身判定は掛けない

**静的**(`/ext/<name>/<rest>` → `<dir>/<rest>`):

- `FileResponse`(Range / etag / last-modified — PDF はブラウザのビューアが Range を投げる)
- 要求パスは resolve して `<dir>`(resolve 済み)の中に閉じる(中の symlink が外へ出れば 404)—
  resolver と共有の `image_refs.static_file`。ディレクトリ・不在は 404(一覧も補完も無い)
- content-type は拡張子から、不明は application/octet-stream。`.md` / `.txt` は
  `text/plain; charset=utf-8`(#145)。`.html` / `.htm` は `text/html; charset=utf-8` で inline、
  `HTML_CSP` + nosniff。SVG / JS は添付 + nosniff
- **nucllei 自身の `/mnt` も `/ext` も暗黙には配信しない** — 配るのは表に書いた dir だけ

共通のゲート:

- セッション認証必須(get_verified_user)。per-user のアクセス検査は `/ext/groups` `/ext/public`
  だけ(#152。判定は `utils/ext_acl.ext_access` 1 か所): `/ext/groups/<g>/…` はメンバーと admin に
  だけ配信し、それ以外は不在と同じ 404(`/ext/groups` 直下の一覧も 404 — 一覧は出さない)。
  `/ext/public/…` は全員に配信。この 2 つの応答には `Cache-Control: private, no-cache`
  (共有キャッシュに載せず、所属が外れたら次の取得で 404 になる)。グループの中は
  `<root>/<グループ名>` に閉じる(中の symlink で他グループへ出させない)。
  それ以外の name の公開範囲は配備が `EXT_MOUNTS` に書いた物。
- `..` 等の traversal パスは 400(proxy・静的とも)。
- 資料は Files 意味論(#94 soft delete/purge)の外 — 記録責任は配信元側。
- 全体の `CONTENT_SECURITY_POLICY` env を設定した配備では、グローバルの middleware が
  `HTML_CSP` を上書きする。
- モデルの参照解決(`image_refs.resolve_url` / `resolve_path`)も同じ表を引く — 静的はファイル、
  proxy は上流へ GET。
"""

import asyncio
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, Response, StreamingResponse

from nucllei.env import SRC_LOG_LEVELS
from nucllei.utils import image_refs
from nucllei.utils.auth import get_verified_user
from nucllei.utils.content_hardening import active_content_headers
from nucllei.utils.ext_acl import ext_access, is_acl_rel
from nucllei.utils.ext_upstreams import (
    ExtMount,
    ext_mount,
    get_ext_client,
    has_path_traversal,
    proxy_timeout,
    split_ext_rest,
    under_base,
    upstream_url,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

# ブラウザでそのまま読ませるテキスト(#145)。拡張子は小文字で比較する
_PLAIN_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_PLAIN_TEXT_TYPE = "text/plain; charset=utf-8"
# 配備が置いた HTML。inline で返し、必ず HTML_CSP を付ける(中継の text/html も同じ)
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_HTML_TYPE = "text/html; charset=utf-8"
HTML_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)

# 中継: 上流へ転送する要求ヘッダ(許可リスト。小文字)。Cookie / Authorization は入れない
FORWARD_REQUEST_HEADERS = (
    "range",
    "if-range",
    "if-none-match",
    "if-modified-since",
    "accept",
    "accept-language",
)
# 中継: 上流から透過する応答ヘッダ(許可リスト。小文字)。Set-Cookie・認証系・hop-by-hop は落ちる。
# Content-Encoding は入れない — 上流には Accept-Encoding: identity で頼む
PASSTHROUGH_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "etag",
    "last-modified",
    "cache-control",
    "content-disposition",
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _content_type_for(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in _PLAIN_TEXT_SUFFIXES:
        return _PLAIN_TEXT_TYPE
    if suffix in _HTML_SUFFIXES:
        return _HTML_TYPE
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _hardening_headers(content_type: str | None) -> dict[str, str]:
    """HTML は CSP + nosniff(inline)、それ以外は active_content_headers。"""
    base = (content_type or "").split(";")[0].strip().lower()
    if base == "text/html":
        return {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": HTML_CSP,
        }
    return active_content_headers(content_type)


# "" は `/ext` 自体(末尾 "/" 無し)。これが無いと SPA の catch-all("/" の mount)に落ちる
@router.api_route("", methods=["GET", "HEAD"])
@router.api_route("/{path:path}", methods=["GET", "HEAD"])
async def serve_ext(request: Request, path: str = "", user=Depends(get_verified_user)):
    if has_path_traversal(path):
        raise HTTPException(status_code=400, detail="Invalid path")
    name, rest = split_ext_rest(path)
    mount = ext_mount(name)
    if mount is None:
        raise HTTPException(status_code=404, detail="Not found")
    extra = {}
    if is_acl_rel(name):
        # #152: 見えない物は不在と同じ 404(存在を漏らさない)。admin は ext_access が rw
        if ext_access(user, f"{name}/{rest}") == "none":
            raise HTTPException(status_code=404, detail="Not found")
        extra = dict(_ACL_CACHE_HEADERS)
    if mount.proxy:
        return await _proxy(request, mount, rest, extra)
    return await _serve_static(mount, rest, extra)


# per-user の ACL が掛かる置き場(/ext/groups・/ext/public)の応答に付ける。共有キャッシュに
# 載せず、毎回サーバに確かめさせる(所属が外れたら次の取得で 404)
# 小文字: 中継の応答ヘッダ(小文字の dict)の上流の cache-control を上書きするため
_ACL_CACHE_HEADERS = {"cache-control": "private, no-cache"}


# ---- proxy -------------------------------------------------------------------


async def _proxy(request: Request, mount: ExtMount, rest: str, extra=None):
    # scope の query_string をそのまま使う(`request.url.query` は decode 済みの path を繋いで
    # 再解析するので、path に `%3F` があるとそこから先を query と取り違える)
    query = request.scope.get("query_string", b"").decode("latin-1")
    url = upstream_url(mount, rest, f"?{query}" if query else "")
    headers = {
        key: request.headers[key]
        for key in FORWARD_REQUEST_HEADERS
        if key in request.headers
    }
    headers["accept-encoding"] = "identity"
    client = get_ext_client()
    try:
        upstream_request = client.build_request(
            request.method, url, headers=headers, timeout=proxy_timeout()
        )
    except httpx.InvalidURL:
        # 制御文字等は has_path_traversal で弾いている。取りこぼしを 500 にしない
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        log.warning(f"ext proxy: upstream timed out (name={mount.name})")
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.HTTPError as e:
        log.warning(f"ext proxy: upstream unavailable (name={mount.name}): {e!r}")
        raise HTTPException(status_code=502, detail="Upstream unavailable")

    if upstream.status_code in _REDIRECT_STATUSES:
        await upstream.aclose()
        location = upstream.headers.get("location")
        remainder = (
            under_base(urljoin(url, location), mount.target)
            if location and not has_path_traversal(location)
            else None
        )
        if remainder is None:
            # base の外(内部 URL を漏らさない)・Location 無し
            log.warning(
                f"ext proxy: redirect outside the upstream base (name={mount.name}, "
                f"status={upstream.status_code})"
            )
            raise HTTPException(status_code=502, detail="Upstream error")
        return Response(
            status_code=upstream.status_code,
            headers={"Location": f"/ext/{mount.name}/{remainder}", **(extra or {})},
        )

    out = {
        key: upstream.headers[key]
        for key in PASSTHROUGH_RESPONSE_HEADERS
        if key in upstream.headers
    }
    if "content-encoding" in upstream.headers:
        # identity を頼んだのに符号化して返した上流。本文は aiter_bytes が復号して流すので、
        # 符号化後の長さは使えない(Content-Encoding は許可リストの外で元から落ちる)
        out.pop("content-length", None)
    out.update(_hardening_headers(upstream.headers.get("content-type")))
    out.update(extra or {})
    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=out,
        background=BackgroundTask(upstream.aclose),
    )


# ---- 静的 --------------------------------------------------------------------


async def _serve_static(mount: ExtMount, rest: str, extra=None):
    """`<dir>/{rest}` を配信する(rest は Starlette が decode 済み)。閉じ込め・不在・
    ディレクトリの判定は resolver と共有の `image_refs.static_file`。"""
    served = await asyncio.to_thread(image_refs.static_file, mount, rest)
    if served is None:
        raise HTTPException(status_code=404, detail="Not found")
    resolved = served[0]
    content_type = _content_type_for(resolved.name)
    return FileResponse(
        resolved,
        media_type=content_type,
        headers={**_hardening_headers(content_type), **(extra or {})},
    )
