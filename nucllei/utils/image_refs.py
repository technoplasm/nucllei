"""「参照 → バイト」の共有 resolver(#139)。

モデルに見せる画像参照は 2 つの形しか無い: `file_id`(ユーザー添付 = Storage)と、
tool の戻り値に literal で出る **"/" 始まりの参照**。後者の解決規則をここ 1 箇所に
置く — feed 側の tool(配備層の feed_image / fetch_url、nucllei プロセス内で動く)と、
送信直前に image_url を data URL 化する `utils/file_images.py` の両方がこれを呼ぶ。
規則を 2 箇所に持つと、tool が「見えた」と判断したものが送信時に落ちる(またはその逆)
ずれが生まれる。

受ける参照の形と解決先:

- `/ext/{name}/{path}[?q]` — EXT_UPSTREAMS の backend(`ext_upstreams.ext_backend`)。
  file なら root 内をローカル読み、http なら httpx で取得(content-type は上流ヘッダ)。
  文書本文には `/ext/cage/static/X` が literal で焼き込まれている(cage の
  PUBLIC_BASE)ので、モデルはこれを読み替えずそのまま渡せばよい。
- `/mnt/...`(`RO_MOUNT_ROOT` 配下)— nucllei 自身に read-only で bind された参照用
  データ(sandbox と同じ置き場 #138)。resolve() して root 内でなければ読まない。
- `/workspace/...` — sandbox の `/files/read`(呼び出しユーザーの workspace に閉じる)。
  jail 内で作ったものをモデルが見るだけなら、Files に登録する必要は無い。
- それ以外(絶対 URL、`/api/v1/files/...`、相対)— ここでは扱わない(None)。

**File を発行しない**: PDF は送信直前にページを画像化して data URL にする
(`rasterize_pdf_page`)。「見るために置く」ものが Storage / Files に永久に残る
経路(旧 feed_image の `_register_png`)を無くすのが目的の 1 つ。ページ指定は参照の
`#page=N`(1-origin)に載せる(`split_page` / `with_page`)。

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

import httpx

from nucllei.builtin_tools import sandbox
from nucllei.env import EXT_INLINE_MAX_BYTES, EXT_UPSTREAMS, SRC_LOG_LEVELS
from nucllei.utils.ext_upstreams import (
    ext_backend,
    get_ext_client,
    has_path_traversal,
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

# "/ext/{name}/{path}[?q]"。**相対のみ** — 絶対 URL は外部 URL として扱わない。
# name の文字種は EXT_UPSTREAMS のパース(env.py)と同じ [a-z0-9_-]+。
EXT_REF_PATTERN = re.compile(r"^/ext/([a-z0-9_-]+)/([^?#]+)(\?[^#]*)?$")

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
    """ "/a.pdf#page=3" → ("/a.pdf", 3)。ページ指定が無ければ (ref, None)。"""
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
        # 先頭 "/" は Path 結合で root を捨てさせる(routers/ext.py と同じ扱い)
        return await asyncio.to_thread(
            _read_confined, target, target / path.lstrip("/"), f"ext:{name}"
        )
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


async def load_ref(ref: str, user_id: str) -> LoadedRef | None:
    """ "/" 始まりの参照(`#page` を含まない)をバイト列にする。解決できなければ None。

    受ける形と境界は module docstring のとおり。サイズ上限は EXT_INLINE_MAX_BYTES
    (どの経路も同じ)。
    """
    if not isinstance(ref, str) or not ref.startswith("/"):
        return None
    # 制御文字 / ".." / バックスラッシュはどの形でも読まない。/ext/ と /workspace/ の分岐は
    # 個別にも見るが、/mnt/ 直読みは Path.resolve() が NUL で ValueError を投げる
    # (fail closed の約束を破って 500 になる) ので、入口で一律に落とす。
    if has_path_traversal(ref):
        log.warning("image_refs: rejecting reference with traversal/control chars")
        return None

    match = EXT_REF_PATTERN.match(ref)
    if match:
        return await _load_ext(*match.groups())

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
