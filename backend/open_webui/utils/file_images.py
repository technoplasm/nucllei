"""内部 file URL 画像の base64 インライン化(#69 画像の files 化)。

画像添付は base64 直入れをやめ files テーブル経由に統一した。チャット payload の
image_url には "/api/v1/files/{id}/content" 形式の内部 URL が入るが、air-gapped
前提のためモデル(SGLang)側からこの URL を fetch できない — モデルへ出る直前
(process_chat_payload 末尾)に、要求ユーザーが読める file だけを Storage から
読み出して data URL に変換する。

読めない・存在しない file は警告 log のみで該当パーツをそのまま残す
(payload に 404 等の判定情報を混ぜない — file の存在有無をモデル経由で
漏らさないため、アクセス不可と不存在を区別しない)。
data URL・外部 URL・文字列 content のメッセージは触らない。
"""

import base64
import logging
import mimetypes
import re

from open_webui.models.files import Files
from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_read_access_to_file
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 相対("/api/v1/files/{id}/content")と絶対(スキーム+ホスト付き)の両方に一致。
# {id} は uuid 想定だがフォーマットは強制しない(パス区切り・クエリ開始文字以外)。
INTERNAL_FILE_URL_PATTERN = re.compile(
    r"^(?:https?://[^/]+)?/api/v1/files/([^/?#]+)/content(?:\?[^#]*)?$"
)


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


def inline_internal_image_urls(messages: list, user) -> list:
    """messages(OpenAI 形式)内の内部 file URL の image_url パーツを data URL に置換する。

    messages を破壊的に書き換えて同じ list を返す。変換できないパーツは無変更。
    """
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

            data_url = _load_file_as_data_url(match.group(1), user)
            if data_url is None:
                continue

            if isinstance(image_url, dict):
                image_url["url"] = data_url
            else:
                part["image_url"] = data_url
    return messages
