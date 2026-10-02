"""認証付き汎用参照ゲート "/ext/{name}/**"(#97 / #139)。

cage(HTTP ツールゲートウェイ)等が配信する文書内の図・PDF は Docker bridge
内部 URL やコンテナ内パスでしか参照できず、ブラウザから到達できない。上流側が
PUBLIC_BASE を "/ext/{name}"(相対)にすると文書内の参照が同一オリジンの URL になり、
チャット表示・引用・display すべてでブラウザが session cookie 付きでそのまま解決できる
(相対なので origin や http/https が変わっても壊れない。絶対の
"https://<自ホスト>/ext/{name}" でも同じに動く。モデルが tool の `url` にそのまま渡す
ときの解決規則は utils/image_refs.py — 2026-10-03 に「相対 /ext/ は受けない」を覆した)。

backend は env EXT_UPSTREAMS の値の形で 2 種(`utils/ext_upstreams.ext_backend`):

- http(s) base URL: httpx で中継。upstream 2xx はストリーミング透過、404 は 404、
  3xx を含むその他は 502(Location 等で内部 URL を漏らさない)。
- `file:///abs/path`: nucllei 自身に read-only で見えている root からの静的配信
  (#139 — cage のコーパスは nucllei にも bind されるので、上流 HTTP を叩かずに
  済む)。`FileResponse`(Range / etag / last-modified — PDF はブラウザのビューアが
  Range を投げる)。root は resolve 済み、要求パスも resolve して root の中に閉じる
  (symlink 越えも閉じる)。env EXT_FILE_SCOPES(`cage=static`)があれば、配信するのは
  root のそのディレクトリの中だけ(範囲外は 404)— 判定は resolver と共有の
  `ext_upstreams.ext_file_served`。ディレクトリ・不在は 404(一覧は出さない)。
  content-type は拡張子から、不明は application/octet-stream。

backend によらないゲート:

- allowlist は EXT_UPSTREAMS 一本(SSRF の真実源)。map に無い name / map 空は 404。
- セッション認証必須(get_verified_user)。per-user のアクセス検査はしない —
  公開範囲は admin 管理の EXT_UPSTREAMS が真実源。
- GET(read-only 参照資料の中継)。HEAD は Starlette 既定で GET と同経路、
  それ以外のメソッドは 405。traversal パスは 400。
- name の直下 `/ext/{name}/`・`/ext/{name}`(path が空)も同じ経路: http backend は上流の
  `<base>/` を中継し、file backend は root = ディレクトリなので 404。resolver
  (`image_refs.resolve_url`)が同じ入力で取りに行く先と揃える(あちらは http の
  `<base>/` を GET / file は `not_found`)。
- content-type はそのまま出すが、アクティブ型(HTML/SVG/JS)は添付扱い +
  nosniff で実行を止める(#97 review — 同一オリジン配信の stored XSS 対策。
  /cache と同じ content_hardening を共有)。
- 資料は Files 意味論(#94 soft delete/purge)の外 — 記録責任は上流側。
"""

import logging
import mimetypes
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, StreamingResponse

from nucllei.env import SRC_LOG_LEVELS
from nucllei.utils.auth import get_verified_user
from nucllei.utils.content_hardening import active_content_headers
from nucllei.utils.ext_upstreams import (
    ext_backend,
    ext_file_served,
    get_ext_client,
    has_path_traversal,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

# 上流レスポンスから透過するヘッダ(allowlist 方式)。content-encoding は
# aiter_raw(デコードなし)で流すため必須。set-cookie / www-authenticate 等の
# 認証系・hop-by-hop は allowlist 外として自然に落ちる。
PASSTHROUGH_RESPONSE_HEADERS = ("content-type", "content-length", "content-encoding")


# "/{name}" は末尾 "/" 無しの name 直下。これが無いと SPA の catch-all("/" の mount)に
# 落ちて、resolver が解決する URL をブラウザが開けない
@router.get("/{name}")
@router.get("/{name}/{path:path}")
async def proxy_ext_upstream(
    request: Request, name: str, path: str = "", user=Depends(get_verified_user)
):
    backend = ext_backend(name)
    if backend is None:
        raise HTTPException(status_code=404, detail="Not found")

    if has_path_traversal(path):
        raise HTTPException(status_code=400, detail="Invalid path")

    kind, target = backend
    if kind == "file":
        return _serve_local(name, target, path)
    return await _proxy_http(target, name, path, request.url.query)


def _serve_local(name: str, root: Path, path: str):
    # "{path:path}" は "//" 由来の先頭 "/" を残しうる。Path の結合は右辺が絶対だと
    # 左辺を捨てる(root / "/etc/passwd" = /etc/passwd)ので剥がしてから結合する。
    # 閉じ込めの権威は下の ext_file_served(root の中 + EXT_FILE_SCOPES。resolver と共有)—
    # ここは事故の芽を摘むだけ。
    try:
        resolved = (root / path.lstrip("/")).resolve()
    except (OSError, RuntimeError):
        # 深すぎる symlink ループ等。root の外か中かを判定できないので不在扱い
        raise HTTPException(status_code=404, detail="Not found")
    if not ext_file_served(name, resolved) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return FileResponse(
        resolved,
        media_type=content_type,
        headers=active_content_headers(content_type),
    )


async def _proxy_http(base_url: str, name: str, path: str, query: str):
    upstream_url = f"{base_url}/{path}"
    if query:
        upstream_url += f"?{query}"

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
