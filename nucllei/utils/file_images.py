"""内部 file URL 画像の base64 インライン化(#69 画像の files 化)。

画像添付は base64 直入れをやめ files テーブル経由に統一した。チャット payload の
image_url には "/api/v1/files/{id}/content" 形式の内部 URL が入るが、モデル
(推論エンジン)側からこの URL へ到達・認証できる保証がない — モデルへ出る直前
(process_chat_payload 末尾)に、要求ユーザーが読める file だけを Storage から
読み出して data URL に変換する。

変換できない内部 file URL(アクセス不可・削除済み・storage 欠落)は、相対 URL の
まま残すとモデル側がリクエストごと落とし該当チャットが恒久的に失敗するため、
text パーツ "(image unavailable)" に置換する。3 経路とも同一の置換にすることで、
payload からアクセス不可と不存在を区別できない(file の存在有無をモデル経由で
漏らさない)。data URL・外部 URL・文字列 content のメッセージは触らない。

読み出し + base64 エンコードは blocking I/O なので thread にオフロードする
(attachment dispatch と同じ方針 — event loop を塞がない)。

#97: file URL に加え、"/ext/{name}/..."(EXT_UPSTREAMS 上流プロキシの相対パス)の
image_url もここでオンデマンド解決する — feed_image が "/ext/..." を sink に積み、
context_images に URL のまま永続し、送信直前のこの層がサーバ側 httpx fetch で
base64 化する(File は発行しない。context_images は same-turn 限定なので fetch は
そのターンの送信数回で自然に止まる)。per-user のアクセス検査はしない — 上流の
公開範囲は admin 管理の EXT_UPSTREAMS が真実源。失敗(未知 name・非 image/*・
サイズ超過・上流不達)はすべて file 経路と同じ "(image unavailable)" 置換に合流する。
"""

import asyncio
import base64
import logging
import mimetypes
import re

import httpx

from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.access_control import has_read_access_to_file
from nucllei.utils.ext_upstreams import get_ext_client, has_path_traversal
from nucllei.env import EXT_INLINE_MAX_BYTES, EXT_UPSTREAMS, SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 相対("/api/v1/files/{id}/content")と絶対(スキーム+ホスト付き)の両方に一致。
# {id} は uuid 想定だがフォーマットは強制しない(パス区切り・クエリ開始文字以外)。
INTERNAL_FILE_URL_PATTERN = re.compile(
    r"^(?:https?://[^/]+)?/api/v1/files/([^/?#]+)/content(?:\?[^#]*)?$"
)

# "/ext/{name}/{path}"(#97)。**相対のみ** — 絶対 URL は外部 URL として触らない。
# name の文字種は EXT_UPSTREAMS のパース(env.py)と同じ [a-z0-9_-]+。
EXT_URL_PATTERN = re.compile(r"^/ext/([a-z0-9_-]+)/([^?#]+)(\?[^#]*)?$")

UNAVAILABLE_IMAGE_TEXT = "(image unavailable)"


def _load_file_as_data_url(file_id: str, user) -> str | None:
    if not has_read_access_to_file(file_id, user):
        log.warning(
            "file_images: skipping unreadable or missing file reference "
            f"(user_id={user.id}, file_id={file_id})"
        )
        return None

    file = Files.get_file_by_id(file_id)
    if not file or not file.path:
        log.warning(f"file_images: file has no stored content (file_id={file_id})")
        return None

    content_type = (file.meta or {}).get("content_type")
    if not content_type:
        content_type = mimetypes.guess_type(file.filename or "")[0]
    if not content_type:
        content_type = "application/octet-stream"

    try:
        with open(Storage.get_file(file.path), "rb") as f:
            raw = f.read()
    except Exception:
        log.exception(f"file_images: storage read failed (file_id={file_id})")
        return None

    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


async def _load_ext_url_as_data_url(name: str, path: str, query: str) -> str | None:
    """ "/ext/{name}/{path}" を EXT_UPSTREAMS 上流からサーバ側 fetch して data URL 化する。

    - name が map に無い(機能無効含む)/ traversal パス → None
    - content-type が image/* でない → None(feed の url 解決は画像限定 — PDF 等は
      表示のプロキシ経由原本参照で足りる)
    - EXT_INLINE_MAX_BYTES 超過 → 読み中断して None
    - redirect は辿らない(follow_redirects=False の共有クライアント)、2xx 以外 → None
    """
    base_url = EXT_UPSTREAMS.get(name)
    if base_url is None:
        log.warning(f"file_images: unknown ext upstream (name={name})")
        return None
    if has_path_traversal(path):
        log.warning(f"file_images: rejecting traversal ext path (name={name})")
        return None

    url = f"{base_url}/{path}{query or ''}"
    try:
        async with get_ext_client().stream("GET", url) as response:
            if not response.is_success:
                log.warning(
                    f"file_images: ext fetch failed (name={name}, "
                    f"status={response.status_code})"
                )
                return None
            content_type = (
                response.headers.get("content-type", "").split(";")[0].strip()
            )
            if not content_type.startswith("image/"):
                log.warning(
                    f"file_images: ext content is not an image (name={name}, "
                    f"content_type={content_type!r})"
                )
                return None
            chunks = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > EXT_INLINE_MAX_BYTES:
                    log.warning(
                        f"file_images: ext content exceeds size limit "
                        f"(name={name}, limit={EXT_INLINE_MAX_BYTES})"
                    )
                    return None
                chunks.append(chunk)
    except (httpx.HTTPError, httpx.InvalidURL):
        # InvalidURL は httpx.HTTPError の subclass でない(#97 review)。制御文字は
        # has_path_traversal で既に弾いているが、funnel の約束(全失敗を
        # "(image unavailable)" へ)を破らないよう明示的に合流させる。
        log.exception(f"file_images: ext fetch error (name={name})")
        return None

    raw = b"".join(chunks)
    return f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"


async def inline_internal_image_urls(messages: list, user) -> list:
    """messages(OpenAI 形式)内の内部 file URL の image_url パーツを data URL に置換する。

    messages を破壊的に書き換えて同じ list を返す。変換できない内部 file URL /
    "/ext/..." パーツは text "(image unavailable)" に置換(冒頭 docstring 参照)。
    それ以外の URL は無変更。
    """
    # 同一ターン内で同じ file / ext URL を複数回参照しても読み出しは 1 回
    # (結果 None もキャッシュ)。
    cache: dict[str, str | None] = {}

    async def load(file_id: str) -> str | None:
        key = f"file:{file_id}"
        if key not in cache:
            cache[key] = await asyncio.to_thread(_load_file_as_data_url, file_id, user)
        return cache[key]

    async def load_ext(url: str, name: str, path: str, query: str) -> str | None:
        key = f"ext:{url}"
        if key not in cache:
            cache[key] = await _load_ext_url_as_data_url(name, path, query)
        return cache[key]

    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not (isinstance(part, dict) and part.get("type") == "image_url"):
                continue

            image_url = part.get("image_url")
            # OpenAI 形式は {"url": ...} だが、素の文字列も来うるので両対応
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if not isinstance(url, str):
                continue

            match = INTERNAL_FILE_URL_PATTERN.match(url)
            if match:
                data_url = await load(match.group(1))
            else:
                ext_match = EXT_URL_PATTERN.match(url)
                if not ext_match:
                    continue
                data_url = await load_ext(url, *ext_match.groups())
            if data_url is None:
                part.clear()
                part["type"] = "text"
                part["text"] = UNAVAILABLE_IMAGE_TEXT
                continue

            if isinstance(image_url, dict):
                image_url["url"] = data_url
            else:
                part["image_url"] = data_url
    return messages
