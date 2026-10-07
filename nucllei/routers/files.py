import json
import logging
import os
import uuid
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
    Query,
)
from fastapi.responses import FileResponse, StreamingResponse
from nucllei.constants import ERROR_MESSAGES
from nucllei.env import SRC_LOG_LEVELS
from nucllei.models.chats import Chats
from nucllei.models.files import (
    FileForm,
    FileModel,
    FileModelResponse,
    Files,
)

from nucllei.storage.provider import Storage
from nucllei.utils.access_control import (
    has_permission,
    has_read_access_to_file,
)
from nucllei.utils.attachment import process_attachment_functions
from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.utils.file_limit import (
    ADMIN_SETTING,
    attachment_limit_bytes,
    format_limit,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
# Dictation meta(音声入力の通し録音)
############################

# 音声入力(マイク)の通し録音に FE が付ける印(_memos/audio.md「通し録音の添付」)。
# Files.meta に **クライアントが書き込める唯一のキー**で、下の形だけを受ける(extra は拒否)。
# 配備側の tool が原本から読み直すための区間一覧で、モデルの prompt には入れない
# (`<source … dictation="true">` の印だけが載る)。
# 長い録音は約 60 分ごとに複数のファイルへ分かれて届く(session_id が共通、part / parts が
# 何本目か、offset_sec が録音全体の何秒目から始まるか)。segments の秒は**そのファイルの
# 先頭から**、i は録音全体の通し番号。
# 上限: 1 ファイル 60 分で区間は多くて数百・本文は数万字なので、JSON 全体 512KB・区間 2000 個で足りる
DICTATION_MAX_BYTES = 512 * 1024
DICTATION_MAX_SEGMENTS = 2000


class DictationSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    i: int = Field(ge=1)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    cut: Literal["breath", "forced", "silence", "end"]
    text: str


class DictationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    session_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    part: int = Field(ge=1)
    parts: int = Field(ge=1)
    offset_sec: float = Field(ge=0)
    duration_sec: float = Field(ge=0)
    container: str = Field(pattern=r"^[a-z0-9]+/[a-z0-9.]+$", max_length=32)
    segments: list[DictationSegment] = Field(
        min_length=1, max_length=DICTATION_MAX_SEGMENTS
    )
    joined_text: str

    @model_validator(mode="after")
    def _segments_are_ordered(self):
        if self.part > self.parts:
            raise ValueError("part must be <= parts")
        previous_end = 0.0
        for index, segment in enumerate(self.segments):
            if segment.i != self.segments[0].i + index:
                raise ValueError("segments[].i must be consecutive")
            if segment.end_sec < segment.start_sec:
                raise ValueError("segments[].end_sec must be >= start_sec")
            if segment.start_sec < previous_end:
                raise ValueError("segments must not overlap and must be in order")
            previous_end = segment.end_sec
        return self


def parse_dictation_meta(raw: str, content_type: Optional[str]) -> dict:
    """upload の Form `dictation`(JSON 文字列)を検証して meta に載せる dict を返す。

    形が違う・大きすぎる・音声でないファイルに付いている場合は 400。
    """
    if not (content_type or "").startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="dictation is only accepted for audio files",
        )
    if len(raw.encode("utf-8")) > DICTATION_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"dictation must not exceed {DICTATION_MAX_BYTES} bytes",
        )
    try:
        return DictationMeta.model_validate(json.loads(raw)).model_dump()
    except (ValueError, ValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid dictation: {e}",
        )


############################
# Upload File
############################


async def store_uploaded_file(
    request: Request,
    file: UploadFile,
    user,
    file_metadata: dict = {},
    process: bool = True,
    dictation: Optional[dict] = None,
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
        # 上限は sandbox のファイルの出入りと同じ 1 つ(utils/file_limit。0 = 無制限)
        limit = attachment_limit_bytes(request, user)
        if limit is not None and file.size and file.size > limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"ファイルサイズが上限({format_limit(limit)})を超えています。"
                f"上限は管理者が{ADMIN_SETTING}で変えられます。",
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
                        # 検証済み(parse_dictation_meta)のものだけがここへ来る
                        **({"dictation": dictation} if dictation else {}),
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
    dictation: Optional[str] = Form(None),
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

    return await store_uploaded_file(
        request,
        file,
        user,
        process=process,
        dictation=(
            parse_dictation_meta(dictation, file.content_type) if dictation else None
        ),
    )


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
    deleted: str = Query("exclude"),
    user=Depends(get_verified_user),
):
    """ファイル管理ページ用のページング一覧(#69)。

    非 admin は常に自分の file のみ。`user_ids`(カンマ区切り)は admin 専用の
    フィルタで、非 admin が指定したら 403。`deleted`(exclude/only/include — #94)も
    admin 専用で、非 admin が exclude 以外を指定したら 403。items は data.content を
    除いた軽量形(data は status のみ残す。meta は残す)。返却は {"items": [...], "total": n}。
    """
    if order_by not in ("created_at", "accessed_at", "filename"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="order_by must be one of: created_at, accessed_at, filename",
        )
    if direction not in ("desc", "asc"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="direction must be one of: desc, asc",
        )
    if deleted not in ("exclude", "only", "include"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="deleted must be one of: exclude, only, include",
        )
    if user.role != "admin" and deleted != "exclude":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
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
        deleted=deleted,
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
# List Chats Referencing File
############################


@router.get("/{id}/chats")
async def get_file_chats_by_id(id: str, user=Depends(get_verified_user)):
    """この file を参照しているチャット一覧(#69、ファイル管理ページのチャット導線)。

    非 admin は本人のチャットのみ。admin は全ユーザー(他人のチャットは
    /s/{id} から開ける — 振り分けは user_id を見て FE で)。
    file 自体の閲覧権限(owner / admin / ACL read)は単品取得と同じ。
    deleted は admin のみ可(#94、監査導線: file 行 → チャット)。
    """
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.deleted_at is not None and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        return Chats.get_chat_previews(
            user_id=None if user.role == "admin" else user.id, file_id=id
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


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
# Get File By Id
############################


@router.get("/{id}")
async def get_file_by_id(id: str, user=Depends(get_verified_user)):
    """file 単品取得。deleted/purged の扱いは #94:

    - active: 従来通り(owner / admin / ACL read)
    - deleted/purged: admin は全メタ(deleted_at/deleted_by/purged_at/purged_by 含む)。
      非 admin は owner にのみ縮小 marker {id, deleted_at} を返す(FE のチャット添付
      「削除済み」バッジの根拠。表示名はチャット JSON snapshot が持つので filename は
      返さない — 凍結 ACL の評価は削除後のグループ加入者に filename を開示し得るし、
      purge で filename が落ちる変化から purge 時点を推定できてしまう)。
      owner 以外は 404(purged 情報・実体残存は漏らさない)。
    """
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.deleted_at is None:
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

    if user.role == "admin":
        return file

    if file.user_id == user.id:
        return {
            "id": file.id,
            "deleted_at": file.deleted_at,
        }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ERROR_MESSAGES.NOT_FOUND,
    )


############################
# Get File Data Content By Id
############################


@router.get("/{id}/data/content")
async def get_file_data_content_by_id(id: str, user=Depends(get_verified_user)):
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # llens(#94): deleted は admin のみ取得可(実体がある限り)。purged は全員 404
    if file.deleted_at is not None and (
        user.role != "admin" or file.purged_at is not None
    ):
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
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # llens(#94): deleted は admin のみ取得可(実体がある限り)。purged は全員 404
    if file.deleted_at is not None and (
        user.role != "admin" or file.purged_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        # llens(#94): purge 済みは path が落ちている — Storage に触る前に弾く
        if not file.path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
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
                    elif content_type and content_type.startswith(("audio/", "video/")):
                        # #87: チャット埋め込み <audio>/<video> の再生用に inline 配信。
                        # attachment だと Firefox がメディア要素での再生を拒否する。
                        # 保存導線は ?attachment=true か {download} リンクで担う
                        headers["Content-Disposition"] = (
                            f"inline; filename*=UTF-8''{encoded_filename}"
                        )
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
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # llens(#94): deleted は admin のみ取得可(実体がある限り)。purged は全員 404
    if file.deleted_at is not None and (
        user.role != "admin" or file.purged_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        file.user_id == user.id
        or user.role == "admin"
        or has_access_to_file(id, "read", user)
    ):
        # llens(#94): purge 済みは path が落ちている — Storage に触る前に弾く
        if not file.path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
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
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # llens(#94): deleted は admin のみ取得可(実体がある限り)。purged は全員 404
    if file.deleted_at is not None and (
        user.role != "admin" or file.purged_at is not None
    ):
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
    """soft delete(#94)。行は消さず deleted_at/deleted_by を立てる。Storage の実体は
    触らない(実体の破棄は admin 明示操作の purge のみ)。既に deleted/purged の行への
    再実行は no-op 成功(冪等 — 元の deleted_at/deleted_by を上書きしない)。
    """
    file = Files.get_file_by_id(id, include_deleted=True)

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
        if file.deleted_at is not None:
            return {"message": "File deleted successfully"}

        result = Files.soft_delete_file_by_id(id, deleted_by=user.id)
        if result:
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


############################
# Restore File By Id (admin)
############################


@router.post("/{id}/restore")
async def restore_file_by_id(id: str, user=Depends(get_admin_user)):
    """soft delete の取り消し(#94、admin 専有)。deleted → active に戻す。

    purged は実体が無いので 400、active(未削除)も 400。
    """
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.purged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("File is purged and cannot be restored"),
        )
    if file.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("File is not deleted"),
        )

    result = Files.restore_file_by_id(id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error restoring file"),
        )
    return Files.get_file_by_id(id)


############################
# Purge File By Id (admin)
############################


@router.post("/{id}/purge")
async def purge_file_by_id(id: str, user=Depends(get_admin_user)):
    """実体の破棄(#94、admin 専有)。deleted 状態が前提 — active は先に削除が必要。

    Storage の実体を消してから、行の実体由来フィールド
    (filename/path/data/access_control)を落とし purged_at/purged_by を立てる。
    実体削除に失敗したら DB は変更せず 500(Local provider は不存在でも warning のみ
    なので再実行できる)。行と id/hash/meta.size は監査用に残す。
    """
    file = Files.get_file_by_id(id, include_deleted=True)

    if not file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if file.purged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("File is already purged"),
        )
    if file.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("File must be deleted before purge"),
        )

    try:
        Storage.delete_file(file.path)
    except Exception as e:
        log.exception(e)
        log.error("Error deleting file from storage")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT("Error deleting file from storage"),
        )

    purged = Files.purge_file_by_id(id, purged_by=user.id)
    if not purged:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error purging file"),
        )
    return purged
