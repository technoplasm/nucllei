"""認証付き汎用上流プロキシ "/ext/{name}/**"(#97)。

cage(HTTP ツールゲートウェイ)等が配信する文書内の図・PDF は Docker bridge
内部 URL でしか参照できず、ブラウザから到達できない。上流側が PUBLIC_BASE を
"/ext/{name}" にすると文書内の参照が同一オリジン相対 URL になり、チャット表示・
引用・display すべてでブラウザが session cookie 付きでそのまま解決できる。

- 上流 allowlist は env EXT_UPSTREAMS("name=baseURL" のカンマ区切り、既定空=無効)。
  SSRF の真実源はこれ一本。map に無い name / map 空は 404。
- セッション認証必須(get_verified_user)。per-user のアクセス検査はしない —
  上流の公開範囲は admin 管理の EXT_UPSTREAMS が真実源。
- GET(read-only 参照資料の中継)。HEAD は Starlette 既定で GET と同経路、
  それ以外のメソッドは 405。
- upstream 2xx はストリーミング透過。404 は 404、3xx を含むその他は 502
  (Location 等で内部 URL を漏らさない)。
- 上流の content-type は透過するが、アクティブ型(HTML/SVG/JS)は添付扱い +
  nosniff で実行を止める(#97 review — 同一オリジン配信の stored XSS 対策。
  /cache と同じ content_hardening を共有)。
- cage 資料は Files 意味論(#94 soft delete/purge)の外 — 記録責任は上流側。
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from nucllei.env import EXT_UPSTREAMS, SRC_LOG_LEVELS
from nucllei.utils.auth import get_verified_user
from nucllei.utils.content_hardening import active_content_headers
from nucllei.utils.ext_upstreams import get_ext_client, has_path_traversal

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

# 上流レスポンスから透過するヘッダ(allowlist 方式)。content-encoding は
# aiter_raw(デコードなし)で流すため必須。set-cookie / www-authenticate 等の
# 認証系・hop-by-hop は allowlist 外として自然に落ちる。
PASSTHROUGH_RESPONSE_HEADERS = ("content-type", "content-length", "content-encoding")


@router.get("/{name}/{path:path}")
async def proxy_ext_upstream(
    request: Request, name: str, path: str, user=Depends(get_verified_user)
):
    base_url = EXT_UPSTREAMS.get(name)
    if base_url is None:
        raise HTTPException(status_code=404, detail="Not found")

    if has_path_traversal(path):
        raise HTTPException(status_code=400, detail="Invalid path")

    upstream_url = f"{base_url}/{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    # クライアントのヘッダは上流へ転送しない(cookie / authorization を漏らさない)。
    client = get_ext_client()
    try:
        request_obj = client.build_request("GET", upstream_url)
    except httpx.InvalidURL:
        # 制御文字等は has_path_traversal で弾いているが、InvalidURL は
        # HTTPError の subclass でない(#97 review)ため取りこぼしを 400 に落とす。
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        upstream = await client.send(request_obj, stream=True)
    except httpx.HTTPError:
        log.exception(f"ext proxy: upstream request failed (name={name})")
        raise HTTPException(status_code=502, detail="Upstream unavailable")

    if upstream.status_code == 404:
        await upstream.aclose()
        raise HTTPException(status_code=404, detail="Not found")

    if not upstream.is_success:
        # 3xx を含む。Location を返すと内部 URL が漏れるため一律 502 に落とす。
        await upstream.aclose()
        log.warning(
            f"ext proxy: unexpected upstream status (name={name}, "
            f"status={upstream.status_code})"
        )
        raise HTTPException(status_code=502, detail="Upstream error")

    headers = {
        key: upstream.headers[key]
        for key in PASSTHROUGH_RESPONSE_HEADERS
        if key in upstream.headers
    }
    # 上流 content-type を透過する分、アクティブ型の実行を止める(nosniff +
    # 添付)。/cache(CacheStaticFiles)と同一定義を共有。
    headers.update(active_content_headers(upstream.headers.get("content-type")))
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(upstream.aclose),
    )
