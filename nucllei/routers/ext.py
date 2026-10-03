"""認証付き参照資料の配信ゲート "/ext/**"(#97 / #139、2026-10-03 に設定を無くした)。

cage(HTTP ツールゲートウェイ)等が配信する文書内の図・PDF は Docker bridge
内部 URL やコンテナ内パスでしか参照できず、ブラウザから到達できない。上流側が
PUBLIC_BASE を "/ext/<name>"(相対)にすると文書内の参照が同一オリジンの URL になり、
チャット表示・引用・display すべてでブラウザが session cookie 付きでそのまま解決できる
(相対なので origin や http/https が変わっても壊れない。絶対の
"https://<自ホスト>/ext/<name>" でも同じに動く。モデルが tool の `url` にそのまま渡す
ときの解決規則は utils/image_refs.py — 2026-10-03 に「相対 /ext/ は受けない」を覆した)。

配信元に設定は無い(ken 決定 2026-10-03): `image_refs.EXT_DIR`(= `EXT_DIR`。Docker では
`/ext`、host 直起動では `./ext`)に置いたものを、そのままの相対パスで配信する —
`/ext/{rel}` ⇔ `EXT_DIR/{rel}`(`ext/cage/static/X` ⇔ `/ext/cage/static/X`)。何を配信
するかは配備が `EXT_DIR` に何を置くか(volume / symlink)で決める。sandbox は同じ物を jail の
`/ext` に bind する規約(#138)なので、モデルが書く sandbox パス `/ext/{rel}` と **同じ文字列で
同じ実体**。**nucllei 自身の `/mnt` も `/ext` も配信しない** — 同日の朝は `/mnt` 固定だったが、nucllei は docker 専用
ではなく、host 直起動では root 無しに置けないうえ host の `/mnt`(NFS 等)をログイン済み全員に
晒してしまうので、`DATA_DIR` の中に改めた(ken 指摘 2026-10-03)。
`FileResponse`(Range / etag / last-modified — PDF はブラウザのビューアが Range を投げる)。
要求パスは resolve して mount(`image_refs.served_mount`: `EXT_DIR` 自身か、配備が `EXT_DIR` の
木に置いたディレクトリ symlink の先)の中に閉じる(mount の中身の symlink 越えは閉じる)—
resolver(`image_refs.resolve_url`)の `_read_confined` と同じ判定。ディレクトリ・不在は 404
(一覧は出さない)。content-type は拡張子から、不明は application/octet-stream。
2026-10-03 までは `EXT_UPSTREAMS` の値の形で http 中継(httpx ストリーミング)と `file://` root の
2 backend があり、`EXT_FILE_SCOPES` で root 配下の配信範囲を絞っていた — `url=` の一般 URL が
どこでも取れるようになり http 中継は要らなくなったので、設定ごと撤去。

ゲート:

- セッション認証必須(get_verified_user)。per-user のアクセス検査はしない —
  公開範囲は配備が `EXT_DIR` に置いた物。
- GET(read-only 参照資料の配信)。HEAD は Starlette 既定で GET と同経路、
  それ以外のメソッドは 405。traversal パスは 400。
- `/ext/` と `/ext/{dir}/`(ディレクトリ)は 404。resolver が同じ入力で返す `not_found` と揃える。
- content-type は拡張子からだが、アクティブ型(HTML/SVG/JS)は添付扱い +
  nosniff で実行を止める(#97 review — 同一オリジン配信の stored XSS 対策。
  /cache と同じ content_hardening を共有)。
- 資料は Files 意味論(#94 soft delete/purge)の外 — 記録責任は配信元側。
"""

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import FileResponse

from nucllei.env import SRC_LOG_LEVELS
from nucllei.utils import image_refs
from nucllei.utils.auth import get_verified_user
from nucllei.utils.content_hardening import active_content_headers
from nucllei.utils.ext_upstreams import has_path_traversal

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()


# "" は `/ext` 自体(末尾 "/" 無し)。これが無いと SPA の catch-all("/" の mount)に落ちる
@router.get("")
@router.get("/{path:path}")
async def serve_ext_file(path: str = "", user=Depends(get_verified_user)):
    if has_path_traversal(path):
        raise HTTPException(status_code=400, detail="Invalid path")
    return _serve_local(path)


def _serve_local(path: str):
    """`EXT_DIR/{path}` を配信する。path は Starlette が decode 済み(`{path:path}` は
    "//" 由来の先頭 "/" を残しうる — Path の結合は右辺が絶対だと左辺を捨てる
    (root / "/etc/passwd" = /etc/passwd)ので剥がしてから結合する)。閉じ込めの権威は
    resolve 済みの実体が、その path の mount(`image_refs.served_mount` — `EXT_DIR` 自身か、
    配備が `EXT_DIR` の木に置いたディレクトリ symlink の先)の中であること(resolver と同じ)。"""
    target = Path(image_refs.EXT_DIR) / path.lstrip("/")
    mount = image_refs.served_mount(target)
    if mount is None:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        # 深すぎる symlink ループ等。root の外か中かを判定できないので不在扱い
        raise HTTPException(status_code=404, detail="Not found")
    if not resolved.is_relative_to(mount[1]) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return FileResponse(
        resolved,
        media_type=content_type,
        headers=active_content_headers(content_type),
    )
