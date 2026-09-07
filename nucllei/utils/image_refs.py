"""「参照 → バイト」の共有 resolver(#139)。

モデルに見せる画像参照は 2 つの形しか無い: `file_id`(ユーザー添付 = Storage)と、
tool の戻り値に literal で出る **参照文字列**(絶対 URL または sandbox の fs パス)。
後者の解決規則をここ 1 箇所に置く — feed 側の tool(配備層の feed_image / fetch_url、
nucllei プロセス内で動く)と、送信直前に image_url を data URL 化する
`utils/file_images.py` の両方がこれを呼ぶ。規則を 2 箇所に持つと、tool が「見えた」と
判断したものが送信時に落ちる(またはその逆)ずれが生まれる。

名前空間は 2 つで、形で機械的に分かれる(例外なし):

- **スキーム付き URL**(`http://` / `https://`)— ブラウザが開くもの。
  - path が `/ext/{name}/{rest}` なら **host を問わず** EXT_UPSTREAMS の backend
    (`ext_upstreams.ext_backend`)で解決する。これは自オリジンの表示 URL
    (文書本文には `https://<自ホスト>/ext/cage/static/X` が literal で焼き込まれている —
    cage の PUBLIC_BASE)で、モデルは読み替えずそのまま渡せばよい。host を検証しない
    のは、解決が backend 経由で与えられた host には接続しないため(SSRF の真実源は
    EXT_UPSTREAMS 一本)。
  - それ以外は env EXT_PUBLIC_BASES(`name=院内 web の原本 URL のルート`)のいずれかの
    `<base>/` に前方一致すれば、その name の backend に残り(`rel`)を渡して解決する
    (`ext_upstreams.match_public_base`)。
  - どれにも当たらなければ失敗 `unknown_upstream`(fail closed。任意の URL を取りに行く
    口ではない)。
  file backend なら root 内をローカル読み、http backend なら httpx で取得
  (content-type は上流ヘッダ)。
- **"/" 始まり = sandbox の fs パスだけ**:
  - `/mnt/...`(`RO_MOUNT_ROOT` 配下)— nucllei 自身に read-only で bind された参照用
    データ(sandbox と同じ置き場 #138)。resolve() して root 内でなければ読まない。
  - `/workspace/...` — sandbox の `/files/read`(呼び出しユーザーの workspace に閉じる)。
    jail 内で作ったものをモデルが見るだけなら、Files に登録する必要は無い。
  - **`/ext/...` は受けない**(失敗 `relative_ext` + warning)。`/ext/` は URL であって fs パスでは
    ない — 同じ "/" 始まりで同居させると、モデルが `/ext/` を fs と誤認したり fs パスを
    URL に書き換えたりする事故の温床になる。URL は絶対 URL で渡す。
- それ以外(`/api/v1/files/...`、相対)— ここでは扱わない(失敗 `unsupported_form`)。

**File を発行しない**: PDF は送信直前にページを画像化して data URL にする
(`rasterize_pdf_page`)。「見るために置く」ものが Storage / Files に永久に残る
経路(旧 feed_image の `_register_png`)を無くすのが目的の 1 つ。ページ指定は参照の
`#page=N`(1-origin)に載せる(`split_page` / `with_page`)。URL でも同じ — 末尾の
`#page=N` だけを見るので、URL 自身の fragment とは衝突しない(`#page=N` は常に
一番後ろに付く。`https://h/x.pdf#page=3` → base `https://h/x.pdf`)。

**fail closed**: 境界(root の外・サイズ上限・不達・不明な種別)はすべて失敗 +
log.warning。`resolve_ref` は失敗理由を `RefError`(code + 人向け detail)で返し、
`load_ref` はそれを捨てて None にする薄い wrapper — 呼び手が「リンク切れ」と
「サイズ超過」を言い分けられるように(実走で 32.6 MB の PDF が上限 25 MB に当たり、
モデルが「リンク切れ」と誤報した)。tool 側に on/off の valve は置かない — 境界を持つのは解決層だけで、
tool のスイッチはモデルが届く範囲を変えない(意味の無い設定になる)。
"""

import asyncio
import base64
import io
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from nucllei.builtin_tools import sandbox
from nucllei.env import (
    EXT_INLINE_MAX_BYTES,
    EXT_PUBLIC_BASES,
    EXT_UPSTREAMS,
    SRC_LOG_LEVELS,
)
from nucllei.utils.ext_upstreams import (
    ext_backend,
    get_ext_client,
    has_path_traversal,
    match_public_base,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 参照用データの置き場。sandbox(`sandbox/app/main.py`)と同じ定数で、env にしない —
# 配備側は compose の volume を `/mnt/<name>` に足すだけ(#138 と同じ流儀)。
# 呼び出し時に読む(import 時に固定しない)ので、テストは module 属性を差し替える。
RO_MOUNT_ROOT = Path("/mnt")

# sandbox の workspace 参照。prefix を落とした残りが `/files/read` の相対 path。
WORKSPACE_PREFIX = "/workspace/"

# PDF ページ画像化の解像度。valve にしない — モデルが読める解像度は配備で変わらない。
PDF_RASTER_DPI = 150

# 絶対 URL の **path 部** "/ext/{name}/{rest}"(query / fragment は urlsplit で分離済み)。
# host は問わない(module docstring)。name の文字種は EXT_UPSTREAMS のパース(env.py)と
# 同じ [a-z0-9_-]+。"/" 始まりの参照には掛けない — `/ext/...` は fs パスとして受けない。
EXT_URL_PATH_PATTERN = re.compile(r"^/ext/([a-z0-9_-]+)/(.+)$")

# "/" 始まりのうち、URL を fs パスの形で渡してきたもの(受けない)。
_EXT_PATH_PREFIX = "/ext/"

_ABSOLUTE_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

# 末尾の "#page=N" だけを見る(URL 自身の fragment とは衝突しない — module docstring)。
_PAGE_SUFFIX_PATTERN = re.compile(r"^(.*)#page=(\d+)$")

# 種別判定の magic。content-type と拡張子は自己申告(上流ヘッダ / ファイル名)なので
# 実バイトを優先する。
_MAGIC_TYPES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"%PDF", "application/pdf"),
)


@dataclass
class LoadedRef:
    data: bytes
    content_type: str
    name: str


@dataclass(frozen=True)
class RefError:
    """`resolve_ref` の失敗理由。`code` はこの一覧のどれか(増やさない — 呼び手の tool が
    code からモデル向け文面を組むので、ここと README §3.8 と tool 側で同じ集合を持つ):

    - `unsupported_form` — スキーム付き URL でも `/workspace/` `/mnt/` でもない
      (相対パス・空・`ftp:` 等、"/" 始まりでどの名前空間でもないもの)
    - `relative_ext` — `/ext/…` の相対参照(URL は絶対で渡す)
    - `traversal` — 制御文字・`..`・バックスラッシュ・NUL
    - `unknown_upstream` — `/ext/{name}/…` 形か EXT_PUBLIC_BASES 配下だが name が
      EXT_UPSTREAMS に無い / どの名前空間にも当たらない絶対 URL(外部サイト)。
      detail で両者を区別する
    - `not_found` — file backend / mount でファイルが無い・ディレクトリ・root 外、
      http backend の 404、workspace の 404
    - `too_large` — サイズ上限超過(file・http・workspace の 3 経路とも)。
      `size` は実サイズ(http stream 打ち切りなら Content-Length か打ち切り時点の
      受信量)、`limit` は上限。`size_exact` は `size` が確定値か下限かの区別 —
      True: stat(file backend / `/mnt` / sandbox 413)・全量読了(workspace)・
      Content-Length(http)。False: Content-Length の無い http stream を打ち切った
      時点の受信量(実サイズは不明で、`size` は limit を少し超えた値になる)。
      too_large 以外の code では常に False(意味を持たない)
    - `upstream_status` — http backend の 2xx 以外(404 は not_found。3xx = redirect も
      ここ)。`status` に HTTP status
    - `upstream_unreachable` — http backend への接続失敗・タイムアウト・InvalidURL
    - `workspace_unavailable` — SANDBOX_URL 無し / sandbox API 失敗(404 は not_found、
      413 は too_large に寄せる)

    `detail` は人(ログ)向けの 1 行。モデル向け文面は tool 側が code から組む。
    """

    code: str
    detail: str
    size: int | None = None
    limit: int | None = None
    status: int | None = None
    size_exact: bool = False


# 内部の各経路が返す形。成功 (loaded, None) / 失敗 (None, RefError) のどちらか一方。
_Result = tuple[LoadedRef | None, RefError | None]


def _fail(code: str, detail: str, **fields) -> _Result:
    return None, RefError(code, detail, **fields)


def split_page(ref: str) -> tuple[str, int | None]:
    """ "/a.pdf#page=3" → ("/a.pdf", 3)。ページ指定が無ければ (ref, None)。
    URL でも同じ("https://h/a.pdf#page=3" → ("https://h/a.pdf", 3))。"""
    match = _PAGE_SUFFIX_PATTERN.match(ref)
    if not match:
        return ref, None
    return match.group(1), int(match.group(2))


def with_page(ref: str, page: int) -> str:
    """ref にページ指定(1-origin)を付ける。既に付いていれば付け替える。"""
    base, _ = split_page(ref)
    return f"{base}#page={page}"


def _sniff(data: bytes) -> str | None:
    for magic, content_type in _MAGIC_TYPES:
        if data.startswith(magic):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def detect_kind(loaded: LoadedRef) -> str | None:
    """ "image" / "pdf" / None。magic bytes 優先、次に content-type、次に拡張子。"""
    for content_type in (
        _sniff(loaded.data),
        loaded.content_type,
        mimetypes.guess_type(loaded.name)[0],
    ):
        base = (content_type or "").split(";")[0].strip().lower()
        if base.startswith("image/"):
            return "image"
        if base == "application/pdf":
            return "pdf"
    return None


def pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def rasterize_pdf_page(data: bytes, page: int) -> bytes:
    """PDF の page(1-origin)を PNG バイト列にする。範囲外は IndexError。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        if page < 1 or page > len(pdf):
            raise IndexError(f"page {page} out of range (1..{len(pdf)})")
        pdf_page = pdf[page - 1]
        try:
            image = pdf_page.render(scale=PDF_RASTER_DPI / 72).to_pil()
        finally:
            pdf_page.close()
    finally:
        pdf.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _content_type_for(data: bytes, declared: str | None, name: str) -> str:
    """data URL に載せる型。magic → 申告(上流ヘッダ)→ 拡張子 → octet-stream。"""
    declared = (declared or "").split(";")[0].strip().lower()
    return (
        _sniff(data)
        or declared
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )


def _read_confined(root: Path, target: Path, label: str, limit: int) -> _Result:
    """root(resolve 済み)の中にある通常ファイルだけを読む。blocking — thread で呼ぶ。"""
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        log.warning(f"image_refs: cannot resolve path ({label})")
        return _fail("not_found", f"cannot resolve path ({label})")
    if not resolved.is_relative_to(root):
        log.warning(f"image_refs: path escapes root ({label}, root={root})")
        return _fail("not_found", f"path escapes root ({label})")
    if not resolved.is_file():
        log.warning(f"image_refs: not a file ({label})")
        return _fail("not_found", f"not a file ({label})")
    try:
        size = resolved.stat().st_size
        if size > limit:
            log.warning(
                f"image_refs: file exceeds size limit ({label}, size={size}, "
                f"limit={limit})"
            )
            return _fail(
                "too_large",
                f"file exceeds size limit ({label})",
                size=size,
                limit=limit,
                size_exact=True,
            )
        data = resolved.read_bytes()
    except OSError as e:
        log.exception(f"image_refs: read failed ({label})")
        return _fail("not_found", f"read failed ({label}): {e}")
    return (
        LoadedRef(
            data=data,
            content_type=_content_type_for(data, None, resolved.name),
            name=resolved.name,
        ),
        None,
    )


async def _load_ext(name: str, path: str, query: str, limit: int) -> _Result:
    backend = ext_backend(name)
    if backend is None:
        log.warning(f"image_refs: unknown ext backend (name={name})")
        return _fail(
            "unknown_upstream", f"/ext/{name}/ is not in EXT_UPSTREAMS (name={name})"
        )
    if has_path_traversal(path):
        log.warning(f"image_refs: rejecting traversal ext path (name={name})")
        return _fail("traversal", f"traversal in ext path (name={name})")
    kind, target = backend
    if kind == "file":
        # URL の path は percent-encoded のまま来る(文書に焼かれた URL は日本語
        # ディレクトリを %E7%A5%9E… で持つ)。ブラウザ側 routers/ext.py は Starlette が
        # decode 済みの path を受けるので、同じ URL が同じファイルに当たるようここでも
        # decode してから結合する。traversal 判定は decode 後で見る(念のため再判定)。
        decoded = unquote(path)
        if has_path_traversal(decoded):
            log.warning(f"image_refs: rejecting traversal ext path (name={name})")
            return _fail("traversal", f"traversal in ext path (name={name})")
        # 先頭 "/" は Path 結合で root を捨てさせる(routers/ext.py と同じ扱い)
        return await asyncio.to_thread(
            _read_confined,
            target,
            target / decoded.lstrip("/"),
            f"ext:{name}",
            limit,
        )
    # http backend へは encode されたまま渡す(上流が decode する)
    return await _fetch_http(f"{target}/{path}{query or ''}", name, path, limit)


async def _fetch_http(url: str, name: str, path: str, limit: int) -> _Result:
    """http backend。redirect は辿らない(共有クライアント)。404 → not_found、
    それ以外の 2xx 以外(3xx 含む)→ upstream_status、上限超過 → too_large、
    接続失敗 / タイムアウト / InvalidURL → upstream_unreachable。"""
    try:
        async with get_ext_client().stream("GET", url) as response:
            if not response.is_success:
                status = response.status_code
                log.warning(
                    f"image_refs: ext fetch failed (name={name}, status={status})"
                )
                if status == 404:
                    return _fail("not_found", f"upstream 404 (name={name})", status=404)
                return _fail(
                    "upstream_status",
                    f"upstream returned {status} (name={name})",
                    status=status,
                )
            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > limit:
                    # 全量バッファせず、超過した時点でストリームを打ち切る。size は
                    # 打ち切り時点の受信量(実サイズの下限)— Content-Length が
                    # 有ればそちらを採る
                    declared_len = response.headers.get("content-length")
                    exact = (declared_len or "").isdigit()
                    size = int(declared_len) if exact else total
                    log.warning(
                        f"image_refs: ext content exceeds size limit "
                        f"(name={name}, size{'=' if exact else '>='}{size}, "
                        f"limit={limit})"
                    )
                    return _fail(
                        "too_large",
                        f"upstream content exceeds size limit (name={name})",
                        size=size,
                        limit=limit,
                        size_exact=exact,
                    )
                chunks.append(chunk)
            declared = response.headers.get("content-type")
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        # InvalidURL は httpx.HTTPError の subclass でない(#97 review)。制御文字は
        # has_path_traversal で既に弾いているが、fail closed の約束を破らないよう
        # 明示的に合流させる。
        log.exception(f"image_refs: ext fetch error (name={name})")
        return _fail(
            "upstream_unreachable",
            f"upstream fetch error (name={name}): {type(e).__name__}: {e}",
        )
    data = b"".join(chunks)
    filename = os.path.basename(path)
    return (
        LoadedRef(
            data=data,
            content_type=_content_type_for(data, declared, filename),
            name=filename,
        ),
        None,
    )


# sandbox_post が投げる RuntimeError の文面(`sandbox HTTP {status}: {body}`)から
# status と、413 の body(`file too large: N bytes (limit L)`)からサイズを拾う。
_SANDBOX_HTTP_PATTERN = re.compile(r"^sandbox HTTP (\d{3})\b")
_SANDBOX_TOO_LARGE_PATTERN = re.compile(r"(\d+) bytes \(limit (\d+)\)")


def _workspace_error(exc: Exception, rel_path: str) -> RefError:
    """sandbox `/files/read` の失敗を code に振り分ける。404 → not_found、
    413 → too_large、それ以外(不達・401・5xx)→ workspace_unavailable。"""
    message = str(exc)
    match = _SANDBOX_HTTP_PATTERN.match(message)
    status = int(match.group(1)) if match else None
    if status == 404:
        return RefError("not_found", f"workspace file not found (path={rel_path})")
    if status == 413:
        size_match = _SANDBOX_TOO_LARGE_PATTERN.search(message)
        return RefError(
            "too_large",
            f"workspace file exceeds sandbox size limit (path={rel_path})",
            size=int(size_match.group(1)) if size_match else None,
            limit=int(size_match.group(2)) if size_match else None,
            # sandbox は stat で見て 413 を返す — body から拾えた size は確定値
            size_exact=size_match is not None,
        )
    return RefError(
        "workspace_unavailable",
        f"sandbox read failed (path={rel_path}): {type(exc).__name__}: {message[:200]}",
        status=status,
    )


async def _load_workspace(rel_path: str, user_id: str, limit: int) -> _Result:
    if not sandbox.SANDBOX_URL:
        log.warning("image_refs: /workspace reference without SANDBOX_URL")
        return _fail("workspace_unavailable", "SANDBOX_URL is not configured")
    if not rel_path:
        log.warning("image_refs: empty workspace path")
        return _fail("not_found", "empty workspace path")
    if has_path_traversal(rel_path):
        log.warning("image_refs: rejecting workspace path")
        return _fail("traversal", "traversal in workspace path")
    try:
        res = await sandbox.sandbox_post(
            "/files/read", {"user_id": user_id, "path": rel_path}, 60
        )
        data = base64.b64decode(res["b64"])
    except Exception as e:
        # 不在(404)・上限(413)・不達は全部ここ — sandbox 側のメッセージは log に残す
        log.exception(f"image_refs: workspace read failed (path={rel_path})")
        return None, _workspace_error(e, rel_path)
    if len(data) > limit:
        log.warning(
            f"image_refs: workspace file exceeds size limit (path={rel_path}, "
            f"size={len(data)}, limit={limit})"
        )
        return _fail(
            "too_large",
            f"workspace file exceeds size limit (path={rel_path})",
            size=len(data),
            limit=limit,
            size_exact=True,
        )
    filename = res.get("name") or os.path.basename(rel_path)
    return (
        LoadedRef(
            data=data,
            content_type=_content_type_for(data, None, filename),
            name=filename,
        ),
        None,
    )


async def _load_url(url: str, limit: int) -> _Result:
    """スキーム付き URL(traversal / 制御文字は入口で拒否済み)を backend で解決する。"""
    try:
        parts = urlsplit(url)
    except ValueError as e:
        log.warning("image_refs: malformed URL")
        return _fail("unsupported_form", f"malformed URL: {e}")
    query = f"?{parts.query}" if parts.query else ""

    match = EXT_URL_PATH_PATTERN.match(parts.path)
    if match:
        # 自オリジンの表示 URL。host は見ない(module docstring)
        name, rest = match.groups()
        return await _load_ext(name, rest, query, limit)

    hit = match_public_base(url)
    if hit is None:
        log.warning(
            "image_refs: absolute URL is neither an /ext/ URL nor under "
            f"EXT_PUBLIC_BASES ({url[:80]!r})"
        )
        return _fail(
            "unknown_upstream",
            "absolute URL is neither an /ext/{name}/ URL nor under EXT_PUBLIC_BASES "
            f"(external site: {parts.scheme}://{parts.netloc})",
        )
    name, rel = hit
    rel_path, sep, rel_query = rel.split("#", 1)[0].partition("?")
    if not rel_path:
        log.warning(f"image_refs: empty path under EXT_PUBLIC_BASES (name={name})")
        return _fail("not_found", f"empty path under EXT_PUBLIC_BASES (name={name})")
    return await _load_ext(name, rel_path, f"?{rel_query}" if sep else "", limit)


async def resolve_ref(
    ref: str, user_id: str, *, max_bytes: int | None = None
) -> _Result:
    """参照(`#page` を含まない)をバイト列にする。

    成功なら `(loaded, None)`、失敗なら `(None, RefError)`。例外は投げない(fail
    closed の約束 — 境界の外は全部 RefError に閉じる)。受ける形は 2 つ — スキーム付き
    URL(`http(s)://`)と sandbox の fs パス("/" 始まりの `/workspace/...` /
    `/mnt/...`)。境界は module docstring のとおり。

    サイズ上限は `max_bytes`(None なら env の EXT_INLINE_MAX_BYTES)— どの経路
    (file / http stream / workspace)も同じ値で見る。上限は呼び手の都合(画像 inline は
    小さく、文書を読む用途は大きく)なので resolver の env 1 本で縛らない。`too_large`
    の `limit` には実際に使った値が入る。

    失敗理由は `RefError.code`(一覧は RefError の docstring)。呼ぶ側(feed 側の tool、
    送信直前 inline)はこれで「不在」「上限超過」「上流不達」を言い分ける — 本 module は
    warning ログも残すので、ログ側の文面は `detail` と同じ事実を指す。
    """
    limit = EXT_INLINE_MAX_BYTES if max_bytes is None else max_bytes
    if not isinstance(ref, str):
        return _fail(
            "unsupported_form", f"reference is not a string ({type(ref).__name__})"
        )
    is_url = bool(_ABSOLUTE_URL_PATTERN.match(ref))
    if not is_url and not ref.startswith("/"):
        log.warning(f"image_refs: unsupported reference form ({ref[:80]!r})")
        return _fail(
            "unsupported_form",
            "reference is neither an http(s) URL nor a sandbox path "
            f"(/workspace/ or {RO_MOUNT_ROOT}/)",
        )
    # 制御文字 / ".." / バックスラッシュはどの形でも読まない(URL は path / query ごと
    # 文字列全体に掛ける — urlsplit は CR/LF/TAB を黙って落とすので、分解前に見る)。
    # 分岐先でも個別に見るが、/mnt/ 直読みは Path.resolve() が NUL で ValueError を投げる
    # (fail closed の約束を破って 500 になる) ので、入口で一律に落とす。
    if has_path_traversal(ref):
        log.warning("image_refs: rejecting reference with traversal/control chars")
        return _fail("traversal", "reference contains '..', backslash or control chars")

    if is_url:
        return await _load_url(ref, limit)

    if ref.startswith(_EXT_PATH_PREFIX):
        log.warning(
            "image_refs: '/ext/...' is a URL, not a filesystem path — pass it as an "
            f"absolute URL (https://<host>/ext/...) ({ref[:80]!r})"
        )
        return _fail(
            "relative_ext",
            "'/ext/...' is a URL, not a filesystem path — pass it as an absolute URL",
        )

    if ref.startswith(WORKSPACE_PREFIX):
        return await _load_workspace(ref[len(WORKSPACE_PREFIX) :], user_id, limit)

    root = Path(RO_MOUNT_ROOT)
    if ref.startswith(f"{root}/"):
        return await asyncio.to_thread(
            _read_confined, root.resolve(), Path(ref), f"mount:{ref}", limit
        )

    log.warning(f"image_refs: unsupported reference form ({ref[:80]!r})")
    return _fail(
        "unsupported_form",
        f"'/' path is neither /workspace/ nor {root}/ ({ref[:80]!r})",
    )


async def load_ref(ref: str, user_id: str) -> LoadedRef | None:
    """`resolve_ref` の薄い wrapper — 解決できなければ None(理由を要らない呼び手向け)。
    上限は従来どおり EXT_INLINE_MAX_BYTES(引数は増やさない)。"""
    loaded, _ = await resolve_ref(ref, user_id)
    return loaded


def log_mounts() -> None:
    """起動時 1 行: `RO_MOUNT_ROOT` に何が見えているか(sandbox の `_log_ro_mounts` と
    同じ流儀)。配備側が volume を足すだけで増える口で、env を見ても分からないため。
    EXT_UPSTREAMS の file root が無ければ WARNING — その name は全 404 になる。
    EXT_PUBLIC_BASES の name が EXT_UPSTREAMS に無ければ WARNING — その base 配下の
    URL は到達先が無く解決されない。
    """
    root = Path(RO_MOUNT_ROOT)
    try:
        names = sorted(n for n in os.listdir(root) if not n.startswith("."))
    except OSError:
        names = []
    if names:
        log.info(f"[nucllei] read-only mount: {' '.join(str(root / n) for n in names)}")
    else:
        log.info(f"[nucllei] {root} に参照用データは無い")
    for name in EXT_UPSTREAMS:
        backend = ext_backend(name)
        if backend and backend[0] == "file" and not backend[1].is_dir():
            log.warning(
                f"[nucllei] WARNING: EXT_UPSTREAMS {name} の root {backend[1]} が無い "
                f"(/ext/{name}/** は全て 404 になる)"
            )
    for name, base in EXT_PUBLIC_BASES.items():
        if name not in EXT_UPSTREAMS:
            log.warning(
                f"[nucllei] WARNING: EXT_PUBLIC_BASES {name}={base} に対応する "
                f"EXT_UPSTREAMS が無い({base}/** の参照は解決されない)"
            )
