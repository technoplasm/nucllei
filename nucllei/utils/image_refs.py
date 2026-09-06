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
  - どれにも当たらなければ None(fail closed。任意の URL を取りに行く口ではない)。
  file backend なら root 内をローカル読み、http backend なら httpx で取得
  (content-type は上流ヘッダ)。
- **"/" 始まり = sandbox の fs パスだけ**:
  - `/mnt/...`(`RO_MOUNT_ROOT` 配下)— nucllei 自身に read-only で bind された参照用
    データ(sandbox と同じ置き場 #138)。resolve() して root 内でなければ読まない。
  - `/workspace/...` — sandbox の `/files/read`(呼び出しユーザーの workspace に閉じる)。
    jail 内で作ったものをモデルが見るだけなら、Files に登録する必要は無い。
  - **`/ext/...` は受けない**(None + warning)。`/ext/` は URL であって fs パスでは
    ない — 同じ "/" 始まりで同居させると、モデルが `/ext/` を fs と誤認したり fs パスを
    URL に書き換えたりする事故の温床になる。URL は絶対 URL で渡す。
- それ以外(`/api/v1/files/...`、相対)— ここでは扱わない(None)。

**File を発行しない**: PDF は送信直前にページを画像化して data URL にする
(`rasterize_pdf_page`)。「見るために置く」ものが Storage / Files に永久に残る
経路(旧 feed_image の `_register_png`)を無くすのが目的の 1 つ。ページ指定は参照の
`#page=N`(1-origin)に載せる(`split_page` / `with_page`)。URL でも同じ — 末尾の
`#page=N` だけを見るので、URL 自身の fragment とは衝突しない(`#page=N` は常に
一番後ろに付く。`https://h/x.pdf#page=3` → base `https://h/x.pdf`)。

**fail closed**: 境界(root の外・サイズ上限・不達・不明な種別)はすべて None +
log.warning。tool 側に on/off の valve は置かない — 境界を持つのは解決層だけで、
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


def _read_confined(root: Path, target: Path, label: str) -> LoadedRef | None:
    """root(resolve 済み)の中にある通常ファイルだけを読む。blocking — thread で呼ぶ。"""
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError, ValueError):
        log.warning(f"image_refs: cannot resolve path ({label})")
        return None
    if not resolved.is_relative_to(root):
        log.warning(f"image_refs: path escapes root ({label}, root={root})")
        return None
    if not resolved.is_file():
        log.warning(f"image_refs: not a file ({label})")
        return None
    try:
        size = resolved.stat().st_size
        if size > EXT_INLINE_MAX_BYTES:
            log.warning(
                f"image_refs: file exceeds size limit ({label}, size={size}, "
                f"limit={EXT_INLINE_MAX_BYTES})"
            )
            return None
        data = resolved.read_bytes()
    except OSError:
        log.exception(f"image_refs: read failed ({label})")
        return None
    return LoadedRef(
        data=data,
        content_type=_content_type_for(data, None, resolved.name),
        name=resolved.name,
    )


async def _load_ext(name: str, path: str, query: str) -> LoadedRef | None:
    backend = ext_backend(name)
    if backend is None:
        log.warning(f"image_refs: unknown ext backend (name={name})")
        return None
    if has_path_traversal(path):
        log.warning(f"image_refs: rejecting traversal ext path (name={name})")
        return None
    kind, target = backend
    if kind == "file":
        # URL の path は percent-encoded のまま来る(文書に焼かれた URL は日本語
        # ディレクトリを %E7%A5%9E… で持つ)。ブラウザ側 routers/ext.py は Starlette が
        # decode 済みの path を受けるので、同じ URL が同じファイルに当たるようここでも
        # decode してから結合する。traversal 判定は decode 後で見る(念のため再判定)。
        decoded = unquote(path)
        if has_path_traversal(decoded):
            log.warning(f"image_refs: rejecting traversal ext path (name={name})")
            return None
        # 先頭 "/" は Path 結合で root を捨てさせる(routers/ext.py と同じ扱い)
        return await asyncio.to_thread(
            _read_confined, target, target / decoded.lstrip("/"), f"ext:{name}"
        )
    # http backend へは encode されたまま渡す(上流が decode する)
    return await _fetch_http(f"{target}/{path}{query or ''}", name, path)


async def _fetch_http(url: str, name: str, path: str) -> LoadedRef | None:
    """http backend。redirect は辿らない(共有クライアント)、2xx 以外・上限超過は None。"""
    try:
        async with get_ext_client().stream("GET", url) as response:
            if not response.is_success:
                log.warning(
                    f"image_refs: ext fetch failed (name={name}, "
                    f"status={response.status_code})"
                )
                return None
            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > EXT_INLINE_MAX_BYTES:
                    # 全量バッファせず、超過した時点でストリームを打ち切る
                    log.warning(
                        f"image_refs: ext content exceeds size limit "
                        f"(name={name}, limit={EXT_INLINE_MAX_BYTES})"
                    )
                    return None
                chunks.append(chunk)
            declared = response.headers.get("content-type")
    except (httpx.HTTPError, httpx.InvalidURL):
        # InvalidURL は httpx.HTTPError の subclass でない(#97 review)。制御文字は
        # has_path_traversal で既に弾いているが、fail closed の約束を破らないよう
        # 明示的に合流させる。
        log.exception(f"image_refs: ext fetch error (name={name})")
        return None
    data = b"".join(chunks)
    filename = os.path.basename(path)
    return LoadedRef(
        data=data,
        content_type=_content_type_for(data, declared, filename),
        name=filename,
    )


async def _load_workspace(rel_path: str, user_id: str) -> LoadedRef | None:
    if not sandbox.SANDBOX_URL:
        log.warning("image_refs: /workspace reference without SANDBOX_URL")
        return None
    if not rel_path or has_path_traversal(rel_path):
        log.warning("image_refs: rejecting workspace path")
        return None
    try:
        res = await sandbox.sandbox_post(
            "/files/read", {"user_id": user_id, "path": rel_path}, 60
        )
        data = base64.b64decode(res["b64"])
    except Exception:
        # 不在(404)・上限(413)・不達は全部ここ — sandbox 側のメッセージは log に残す
        log.exception(f"image_refs: workspace read failed (path={rel_path})")
        return None
    if len(data) > EXT_INLINE_MAX_BYTES:
        log.warning(
            f"image_refs: workspace file exceeds size limit (path={rel_path}, "
            f"size={len(data)}, limit={EXT_INLINE_MAX_BYTES})"
        )
        return None
    filename = res.get("name") or os.path.basename(rel_path)
    return LoadedRef(
        data=data, content_type=_content_type_for(data, None, filename), name=filename
    )


async def _load_url(url: str) -> LoadedRef | None:
    """スキーム付き URL(traversal / 制御文字は入口で拒否済み)を backend で解決する。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        log.warning("image_refs: malformed URL")
        return None
    query = f"?{parts.query}" if parts.query else ""

    match = EXT_URL_PATH_PATTERN.match(parts.path)
    if match:
        # 自オリジンの表示 URL。host は見ない(module docstring)
        name, rest = match.groups()
        return await _load_ext(name, rest, query)

    hit = match_public_base(url)
    if hit is None:
        log.warning(
            "image_refs: absolute URL is neither an /ext/ URL nor under "
            f"EXT_PUBLIC_BASES ({url[:80]!r})"
        )
        return None
    name, rel = hit
    rel_path, sep, rel_query = rel.split("#", 1)[0].partition("?")
    if not rel_path:
        log.warning(f"image_refs: empty path under EXT_PUBLIC_BASES (name={name})")
        return None
    return await _load_ext(name, rel_path, f"?{rel_query}" if sep else "")


async def load_ref(ref: str, user_id: str) -> LoadedRef | None:
    """参照(`#page` を含まない)をバイト列にする。解決できなければ None。

    受ける形は 2 つ — スキーム付き URL(`http(s)://`)と sandbox の fs パス("/" 始まりの
    `/workspace/...` / `/mnt/...`)。境界は module docstring のとおり。サイズ上限は
    EXT_INLINE_MAX_BYTES(どの経路も同じ)。
    """
    if not isinstance(ref, str):
        return None
    is_url = bool(_ABSOLUTE_URL_PATTERN.match(ref))
    if not is_url and not ref.startswith("/"):
        return None
    # 制御文字 / ".." / バックスラッシュはどの形でも読まない(URL は path / query ごと
    # 文字列全体に掛ける — urlsplit は CR/LF/TAB を黙って落とすので、分解前に見る)。
    # 分岐先でも個別に見るが、/mnt/ 直読みは Path.resolve() が NUL で ValueError を投げる
    # (fail closed の約束を破って 500 になる) ので、入口で一律に落とす。
    if has_path_traversal(ref):
        log.warning("image_refs: rejecting reference with traversal/control chars")
        return None

    if is_url:
        return await _load_url(ref)

    if ref.startswith(_EXT_PATH_PREFIX):
        log.warning(
            "image_refs: '/ext/...' is a URL, not a filesystem path — pass it as an "
            f"absolute URL (https://<host>/ext/...) ({ref[:80]!r})"
        )
        return None

    if ref.startswith(WORKSPACE_PREFIX):
        return await _load_workspace(ref[len(WORKSPACE_PREFIX) :], user_id)

    root = Path(RO_MOUNT_ROOT)
    if ref.startswith(f"{root}/"):
        return await asyncio.to_thread(
            _read_confined, root.resolve(), Path(ref), f"mount:{ref}"
        )

    log.warning(f"image_refs: unsupported reference form ({ref[:80]!r})")
    return None


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
