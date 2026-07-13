import logging
import os
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
    Query,
)
from fastapi.responses import FileResponse, StreamingResponse
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS
from open_webui.models.files import (
    FileForm,
    FileModel,
    FileModelResponse,
    Files,
)

from open_webui.storage.provider import Storage
from open_webui.utils.access_control import has_permission, has_read_access_to_file
from open_webui.utils.attachment import process_attachment_functions
from open_webui.utils.auth import get_admin_user, get_verified_user
from pydantic import BaseModel

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


router = APIRouter()


############################
# Check if the current user has access to a file.
############################


def has_access_to_file(
    file_id: Optional[str], access_type: str, user=Depends(get_verified_user)
) -> bool:
    file = Files.get_file_by_id(file_id)
    log.debug(f"Checking if user has {access_type} access to file")

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # llens(#41): read は中央ヘルパー(owner / admin / 共有 public マーカー / KB 委譲)に
    # 一本化。write は owner/admin 以外の経路を持たない(呼び出し側が先に判定している)。
    if access_type == "read":
        return has_read_access_to_file(file_id, user)
    return False


############################
# Upload File
############################


async def store_uploaded_file(
    request: Request,
    file: UploadFile,
    user,
    file_metadata: dict = {},
    process: bool = True,
):
    """upload の実体(受け入れゲート → Storage 保存 → Attachment dispatch)。

    llens(#69): endpoint の `chat.file_upload` enforcement を通らない内部保存経路
    (生成画像 = routers/images.py)と共用するため endpoint 本体から分離。
    HTTP パラメータを持たないので、query 経由で権限チェックを迂回される余地がない。
    """
    log.info(f"file.content_type: {file.content_type}")
    try:
        unsanitized_filename = file.filename
        filename = os.path.basename(unsanitized_filename)

        # replace filename with uuid
        id = str(uuid.uuid4())
        name = filename
        filename = f"{id}_{filename}"

        # llens(#52): 受け入れゲート(Attachment 前=保存前に server-side 強制)。
        # 拡張子 whitelist(空=全許可)と上限サイズ(MB)を突き合わせて拒否する。
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        allowed_exts = request.app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS
        if allowed_exts and ext not in allowed_exts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"許可されていないファイル形式です(.{ext or '拡張子なし'})。",
            )
        max_size = request.app.state.config.ATTACHMENT_MAX_SIZE  # MB
        if max_size and file.size and file.size > max_size * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"ファイルサイズが上限({max_size}MB)を超えています。",
            )

        contents, file_path = Storage.upload_file(file.file, filename)

        file_item = Files.insert_new_file(
            user.id,
            FileForm(
                **{
                    "id": id,
                    "filename": name,
                    "path": file_path,
                    "meta": {
                        "name": name,
                        "content_type": file.content_type,
                        "size": len(contents),
                        "data": file_metadata,
                    },
                }
            ),
        )
        if process:
            try:
                # llens(#52/#69): テキスト化は Attachment Function に委譲。admin がグローバル
                # config(ATTACHMENT_FUNCTION_ID)に選んだフックだけが発火し、file.data.content
                # を埋める。未設定なら何もしない(per-model 割り当ては #69 で廃止)。
                await process_attachment_functions(
                    request, file_item, user, event="upload"
                )
                file_item = Files.get_file_by_id(id=id)
            except Exception as e:
                log.exception(e)
                log.error(f"Error processing file: {file_item.id}")
                file_item = FileModelResponse(
                    **{
                        **file_item.model_dump(),
                        "error": str(e.detail) if hasattr(e, "detail") else str(e),
                    }
                )

        if file_item:
            return file_item
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error uploading file"),
            )

    except HTTPException:
        # 受け入れゲート等の明示的な HTTPException は detail を保って伝播させる。
        raise
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/", response_model=FileModelResponse)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_verified_user),
    process: bool = Query(True),
):
    # llens(#69): chat.file_upload はフロントの UI 隠しだけでサーバー側未チェックだった
    # (permissions.md 原則 1 違反)。ここが enforcement point。
    if user.role == "user" and not has_permission(
        user.id, "chat.file_upload", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    return await store_uploaded_file(request, file, user, process=process)


############################
# Process File By Id
############################


@router.post("/{id}/process", response_model=Optional[FileModel])
async def process_file_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    """チャット再添付時の再抽出フック発火(#69)。

    既存ファイルをチャットに再添付したとき、Attachment Function を `__event__="attach"`
    で再度走らせる。再抽出するか否か(抽出済みなら None を返して据え置く等)はフック側の
    判断。dispatch は **owner または admin のときだけ** — 非 owner(read 可)は他人の
    file の data.content を書き換えさせないため、現状の file をそのまま返す。
    read 不可は 404。戻り値は最新の file。
    """
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not has_read_access_to_file(id, user):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id == user.id or user.role == "admin":
        file = await process_attachment_functions(request, file, user, event="attach")

    Files.touch_file_accessed_at(id)
    return file


############################
# Update File Access Control By Id
############################


class FileAccessControlForm(BaseModel):
    access_control: Optional[dict] = None


def _validate_file_access_control(access_control: Optional[dict]) -> None:
    """file ACL の受理形を検証する(#69)。

    file の ACL 意味論は他エンティティと逆(None=非公開 / {"read":{"public":true}}=
    全体公開マーカー / group_ids・user_ids 列挙=その範囲に公開 —
    utils/access_control.py::has_read_access_to_file)。受理するのは
    null / {"read": {...}} のみで、read 内は public(bool)・group_ids(list[str])・
    user_ids(list[str])に限る。想定外のキーを黙って保存すると判定側と
    齟齬が出るため 400 で弾く。
    """
    if access_control is None:
        return
    if set(access_control.keys()) != {"read"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access_control must be null or contain only a 'read' key",
        )
    read = access_control["read"]
    if not isinstance(read, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access_control.read must be an object",
        )
    allowed_keys = {"public", "group_ids", "user_ids"}
    if not set(read.keys()) <= allowed_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access_control.read allows only: public, group_ids, user_ids",
        )
    if "public" in read and not isinstance(read["public"], bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access_control.read.public must be a boolean",
        )
    for key in ("group_ids", "user_ids"):
        if key in read and (
            not isinstance(read[key], list)
            or not all(isinstance(v, str) for v in read[key])
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"access_control.read.{key} must be a list of strings",
            )


@router.post("/{id}/access_control/update", response_model=Optional[FileModel])
async def update_file_access_control_by_id(
    id: str, form_data: FileAccessControlForm, user=Depends(get_verified_user)
):
    """ファイル管理ページからの共有状態フル編集(#69)。

    owner または admin のみ。read 可能な共有 file でも非 owner には ACL を
    触らせない — 存在は読めているので 404 でなく 403 を返す。
    """
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    _validate_file_access_control(form_data.access_control)

    file = Files.update_file_access_control_by_id(id, form_data.access_control)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error updating file access control"),
        )
    return file


############################
# List Files
############################


@router.get("/", response_model=list[FileModelResponse])
async def list_files(user=Depends(get_verified_user), content: bool = Query(True)):
    if user.role == "admin":
        files = Files.get_files()
    else:
        files = Files.get_files_by_user_id(user.id)

    if not content:
        for file in files:
            # data=None / content 無しの file が混ざっても落ちないように
            if file.data:
                file.data.pop("content", None)

    return files


############################
# List Files (paged, for file manager)
############################


@router.get("/list")
async def list_files_page(
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order_by: str = Query("created_at"),
    direction: str = Query("desc"),
    user_ids: Optional[str] = Query(None),
    filename: Optional[str] = Query(None),
    user=Depends(get_verified_user),
):
    """ファイル管理ページ用のページング一覧(#69)。

    非 admin は常に自分の file のみ。`user_ids`(カンマ区切り)は admin 専用の
    フィルタで、非 admin が指定したら 403。items は data.content を除いた軽量形
    (data は status のみ残す。meta は残す)。返却は {"items": [...], "total": n}。
    """
    if order_by not in ("created_at", "accessed_at"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="order_by must be one of: created_at, accessed_at",
        )
    if direction not in ("desc", "asc"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="direction must be one of: desc, asc",
        )

    if user.role == "admin":
        scope = None
        if user_ids is not None:
            ids = [uid for uid in user_ids.split(",") if uid.strip()]
            if ids:
                scope = ids
    else:
        if user_ids is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        scope = [user.id]

    files, total = Files.get_files_page(
        user_ids=scope,
        filename=filename,
        order_by=order_by,
        direction=direction,
        limit=limit,
        offset=offset,
    )

    items = []
    for file in files:
        item = file.model_dump()
        data = item.get("data") or {}
        # 一覧では抽出テキスト(data.content)を返さない — ペイロード肥大を防ぐ
        item["data"] = {"status": data["status"]} if "status" in data else {}
        items.append(item)

    return {"items": items, "total": total}


############################
# List Uploaders (admin)
############################


@router.get("/uploaders")
async def list_file_uploaders(user=Depends(get_admin_user)):
    """file を 1 件以上持つユーザーの一覧(#69、admin のフィルタ UI 用)。"""
    return Files.get_uploaders()


############################
# Search Files
############################


@router.get("/search", response_model=list[FileModelResponse])
async def search_files(
    filename: str = Query(
        ...,
        description="Filename pattern to search for. Supports wildcards such as '*.txt'",
    ),
    content: bool = Query(True),
    user=Depends(get_verified_user),
):
    """
    Search for files by filename with support for wildcard patterns.
    """
    # Get files according to user role
    if user.role == "admin":
        files = Files.get_files()
    else:
        files = Files.get_files_by_user_id(user.id)

    # Get matching files
    matching_files = [
        file for file in files if fnmatch(file.filename.lower(), filename.lower())
    ]

    if not matching_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No files found matching the pattern.",
        )

    if not content:
        for file in matching_files:
            if file.data:
                file.data.pop("content", None)

    return matching_files


############################
# Delete All Files
############################


@router.delete("/all")
async def delete_all_files(user=Depends(get_admin_user)):
    result = Files.delete_all_files()
    if result:
        try:
            Storage.delete_all_files()
        except Exception as e:
            log.exception(e)
            log.error("Error deleting files")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting files"),
            )
        return {"message": "All files deleted successfully"}
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error deleting files"),
        )


############################
# Get File By Id
############################


@router.get("/{id}", response_model=Optional[FileModel])
async def get_file_by_id(id: str, user=Depends(get_verified_user)):
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        return file
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Get File Data Content By Id
############################


@router.get("/{id}/data/content")
async def get_file_data_content_by_id(id: str, user=Depends(get_verified_user)):
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        Files.touch_file_accessed_at(id)
        return {"content": file.data.get("content", "") if file.data else ""}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update File Data Content By Id
############################


class ContentForm(BaseModel):
    content: str


@router.post("/{id}/data/content/update")
async def update_file_data_content_by_id(
    request: Request, id: str, form_data: ContentForm, user=Depends(get_verified_user)
):
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "write", user)
    ):
        try:
            # llens(#52): RAG/vector 撤去に伴い、内容更新は data.content の直接書き込みのみ。
            Files.update_file_data_by_id(id, {"content": form_data.content})
            file = Files.get_file_by_id(id=id)
        except Exception as e:
            log.exception(e)
            log.error(f"Error updating file content: {file.id}")

        return {"content": file.data.get("content", "")}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Get File Content By Id
############################


@router.get("/{id}/content")
async def get_file_content_by_id(
    id: str, user=Depends(get_verified_user), attachment: bool = Query(False)
):
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        Files.touch_file_accessed_at(id)
        try:
            file_path = Storage.get_file(file.path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                # Handle Unicode filenames
                filename = file.meta.get("name", file.filename)
                encoded_filename = quote(filename)  # RFC5987 encoding

                content_type = file.meta.get("content_type")
                filename = file.meta.get("name", file.filename)
                encoded_filename = quote(filename)
                headers = {}

                if attachment:
                    headers["Content-Disposition"] = (
                        f"attachment; filename*=UTF-8''{encoded_filename}"
                    )
                else:
                    if content_type == "application/pdf" or filename.lower().endswith(
                        ".pdf"
                    ):
                        headers["Content-Disposition"] = (
                            f"inline; filename*=UTF-8''{encoded_filename}"
                        )
                        content_type = "application/pdf"
                    elif content_type != "text/plain":
                        headers["Content-Disposition"] = (
                            f"attachment; filename*=UTF-8''{encoded_filename}"
                        )

                return FileResponse(file_path, headers=headers, media_type=content_type)

            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        except Exception as e:
            log.exception(e)
            log.error("Error getting file content")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error getting file content"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/{id}/content/html")
async def get_html_file_content_by_id(id: str, user=Depends(get_verified_user)):
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        Files.touch_file_accessed_at(id)
        try:
            file_path = Storage.get_file(file.path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                log.info(f"file_path: {file_path}")
                # llens(GHSA-8gh5-qqh8-hq3x / CVE-2025-46571): アップロードされた HTML は
                # このエンドポイントで iframe に読み込まれる。フロント側の iframe sandbox に加え、
                # 配信レスポンス自体に CSP sandbox を付けて opaque origin で隔離する(親オリジンの
                # localStorage トークン/Cookie に到達させない=Stored XSS→乗っ取りを封じる)。
                # allow-scripts は正当な HTML プレビューの JS を許可しつつ same-origin は与えない。
                return FileResponse(
                    file_path,
                    headers={
                        "Content-Security-Policy": "sandbox allow-scripts;",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        except Exception as e:
            log.exception(e)
            log.error("Error getting file content")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error getting file content"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/{id}/content/{file_name}")
async def get_file_content_by_id_and_name(id: str, user=Depends(get_verified_user)):
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        Files.touch_file_accessed_at(id)
        file_path = file.path

        # Handle Unicode filenames
        filename = file.meta.get("name", file.filename)
        encoded_filename = quote(filename)  # RFC5987 encoding
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }

        if file_path:
            file_path = Storage.get_file(file_path)
            file_path = Path(file_path)

            # Check if the file already exists in the cache
            if file_path.is_file():
                return FileResponse(file_path, headers=headers)
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=ERROR_MESSAGES.NOT_FOUND,
                )
        else:
            # File path doesn’t exist, return the content as .txt if possible
            file_content = file.content.get("content", "")

            # Create a generator that encodes the file content
            def generator():
                yield file_content.encode("utf-8")

            return StreamingResponse(
                generator(),
                media_type="text/plain",
                headers=headers,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Delete File By Id
############################


@router.delete("/{id}")
async def delete_file_by_id(id: str, user=Depends(get_verified_user)):
    file = Files.get_file_by_id(id)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "write", user)
    ):
        # We should add Chroma cleanup here

        result = Files.delete_file_by_id(id)
        if result:
            try:
                Storage.delete_file(file.path)
            except Exception as e:
                log.exception(e)
                log.error("Error deleting files")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error deleting files"),
                )
            return {"message": "File deleted successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting file"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
