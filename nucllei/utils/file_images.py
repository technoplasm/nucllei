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
"""

import asyncio
import base64
import logging
import mimetypes
import re

from nucllei.models.files import Files
from nucllei.storage.provider import Storage
from nucllei.utils.access_control import has_read_access_to_file
from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 相対("/api/v1/files/{id}/content")と絶対(スキーム+ホスト付き)の両方に一致。
# {id} は uuid 想定だがフォーマットは強制しない(パス区切り・クエリ開始文字以外)。
INTERNAL_FILE_URL_PATTERN = re.compile(
    r"^(?:https?://[^/]+)?/api/v1/files/([^/?#]+)/content(?:\?[^#]*)?$"
)

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


async def inline_internal_image_urls(messages: list, user) -> list:
    """messages(OpenAI 形式)内の内部 file URL の image_url パーツを data URL に置換する。

    messages を破壊的に書き換えて同じ list を返す。変換できない内部 file URL パーツは
    text "(image unavailable)" に置換(冒頭 docstring 参照)。内部 URL 以外は無変更。
    """
    # 同一ターン内で同じ file を複数回参照しても読み出しは 1 回(結果 None もキャッシュ)。
    cache: dict[str, str | None] = {}

    async def load(file_id: str) -> str | None:
        if file_id not in cache:
            cache[file_id] = await asyncio.to_thread(
                _load_file_as_data_url, file_id, user
            )
        return cache[file_id]

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
            if not match:
                continue

            data_url = await load(match.group(1))
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
