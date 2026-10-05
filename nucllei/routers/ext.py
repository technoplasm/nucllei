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
(一覧は出さない — 補完は下記)。content-type は拡張子から、不明は application/octet-stream。
`.md` / `.txt` は `text/plain; charset=utf-8`(#145 — mimetypes が `.md` を知らない環境では
octet-stream + nosniff でリンクを押すとダウンロードになっていた。text/markdown も避ける —
ブラウザが表示せずダウンロードに回すことがある。どちらもアクティブ型ではないので nosniff のまま)。
2026-10-03 までは `EXT_UPSTREAMS` の値の形で http 中継(httpx ストリーミング)と `file://` root の
2 backend があり、`EXT_FILE_SCOPES` で root 配下の配信範囲を絞っていた — `url=` の一般 URL が
どこでも取れるようになり http 中継は要らなくなったので、設定ごと撤去。

URL の契約(2026-10-05 ken 決定、#146 の簡易版「リンクによる引用」):

- **拡張子の補完**: `/ext/X` が実在するファイルならそのまま。無いかディレクトリなら `X.html` →
  `X/index.html` の順に探し、どれも無ければ 404。解決後も同じ mount の判定で閉じる。ディレクトリの
  一覧は出さない(一覧が要る配備は index.html として置く)。
- **`.html` / `.htm` は `text/html; charset=utf-8` で inline に返し、必ず厳しい CSP(`HTML_CSP`)を
  付ける** — スクリプト・外部読み込み・フォーム送信・base 差し替え・他オリジンへの埋め込みを止める。
  nosniff は維持。同一オリジンの stored XSS 対策は従来の「添付扱い」から CSP に替わる(SVG / JS は
  従来どおり添付)。
- `/ext/cage/doc` + doc の住所(`/院内マニュアル/医療安全管理マニュアル` 等)は、cage が起動時に
  ビルドした HTML のページ(`<住所>.html` か `<住所>/index.html`)になる。中身は配備側が置く物で、
  nucllei は静的に配信するだけ(md の描画・見出しアンカー・パンくず・一覧は cage の責務)。
- `.md` / `.txt` は従来どおり text/plain(Accept による分岐はしない)。

ゲート:

- セッション認証必須(get_verified_user)。per-user のアクセス検査はしない —
  公開範囲は配備が `EXT_DIR` に置いた物。
- GET(read-only 参照資料の配信)。HEAD は Starlette 既定で GET と同経路、
  それ以外のメソッドは 405。traversal パスは 400。
- `/ext/` と `/ext/{dir}/`(ディレクトリ)は `index.html` が無ければ 404。resolver が同じ入力で
  返す `not_found` と揃える。
- content-type は拡張子から。HTML は上記の CSP 付きで inline、それ以外のアクティブ型(SVG/JS)は
  添付扱い + nosniff で実行を止める(#97 review — 同一オリジン配信の stored XSS 対策。
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

# ブラウザでそのまま読ませるテキスト(#145)。拡張子は小文字で比較する
_PLAIN_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
_PLAIN_TEXT_TYPE = "text/plain; charset=utf-8"
# 配備が置いた HTML(cage がビルドした doc のページ等)。inline で返し、必ず HTML_CSP を付ける
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_HTML_TYPE = "text/html; charset=utf-8"
HTML_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)


def _content_type_for(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in _PLAIN_TEXT_SUFFIXES:
        return _PLAIN_TEXT_TYPE
    if suffix in _HTML_SUFFIXES:
        return _HTML_TYPE
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


# "" は `/ext` 自体(末尾 "/" 無し)。これが無いと SPA の catch-all("/" の mount)に落ちる
@router.get("")
@router.get("/{path:path}")
async def serve_ext_file(path: str = "", user=Depends(get_verified_user)):
    if has_path_traversal(path):
        raise HTTPException(status_code=400, detail="Invalid path")
    return _serve_local(path)


def _confined_file(target: Path) -> Path | None:
    """`target`(`EXT_DIR` 起点、未 resolve)が配信されるファイルなら resolve 済みの実体。
    閉じ込めの権威は resolve 済みの実体が、その path の mount(`image_refs.served_mount` —
    `EXT_DIR` 自身か、配備が `EXT_DIR` の木に置いたディレクトリ symlink の先)の中であること
    (resolver と同じ)。外・不在・ディレクトリ・判定不能は None。"""
    mount = image_refs.served_mount(target)
    if mount is None:
        return None
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        # 深すぎる symlink ループ等。root の外か中かを判定できないので不在扱い
        return None
    if not resolved.is_relative_to(mount[1]) or not resolved.is_file():
        return None
    return resolved


def _serve_local(path: str):
    """`EXT_DIR/{path}` を配信する。path は Starlette が decode 済み(`{path:path}` は
    "//" 由来の先頭 "/" を残しうる — Path の結合は右辺が絶対だと左辺を捨てる
    (root / "/etc/passwd" = /etc/passwd)ので剥がしてから結合する)。

    解決順(URL の契約): `X` → `X.html` → `X/index.html`、どれも mount の中のファイルで
    なければ 404。`X` がディレクトリでも一覧は出さない。"""
    rel = path.lstrip("/")
    target = Path(image_refs.EXT_DIR) / rel
    candidates = [target]
    if rel.strip("/"):
        candidates.append(target.with_name(target.name + ".html"))
    candidates.append(target / "index.html")
    for candidate in candidates:
        resolved = _confined_file(candidate)
        if resolved is not None:
            break
    else:
        raise HTTPException(status_code=404, detail="Not found")

    content_type = _content_type_for(resolved.name)
    if content_type == _HTML_TYPE:
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": HTML_CSP,
        }
    else:
        headers = active_content_headers(content_type)
    return FileResponse(resolved, media_type=content_type, headers=headers)
