import copy
import logging
import uuid
from typing import Optional


from nucllei.models.chats import (
    ChatForm,
    ChatImportForm,
    ChatResponse,
    Chats,
    ChatTitleIdResponse,
)
from nucllei.models.files import FileForm, Files
from nucllei.models.folders import Folders
from nucllei.models.users import Users

from nucllei.config import ENABLE_ADMIN_CHAT_ACCESS, ENABLE_ADMIN_EXPORT
from nucllei.constants import ERROR_MESSAGES
from nucllei.env import SRC_LOG_LEVELS
from nucllei.storage.provider import Storage
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel


from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.utils.access_control import has_permission, has_read_access_to_file

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


def get_file_ids_from_chat(chat_json: dict) -> list[str]:
    """llens(#41): chat JSON(トップレベル files + 各 message.files)から添付 file_id を集める。

    #69 で画像も files テーブル経由に統一したため type=="image" も対象。
    base64 直入れ時代の旧 image エントリは id を持たないので自然にスキップされる。
    """
    entries = list(chat_json.get("files") or [])
    for message in ((chat_json.get("history") or {}).get("messages") or {}).values():
        entries.extend(message.get("files") or [])

    ids = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("type") in ("file", "image"):
            file_id = entry.get("id") or (entry.get("file") or {}).get("id")
            if file_id:
                ids.append(file_id)
    return list(dict.fromkeys(ids))


def publish_chat_files(chat_json: dict, user) -> None:
    """llens(#41): 共有 = 参照 file の read を認証ユーザー全員へ拡大(public マーカー)。

    chat JSON はクライアント制御のため、共有者自身が所有する file のみ public 化する
    (他人の file_id を紛れ込ませた共有による権限拡大を防ぐ)。共有解除では戻さない —
    狭める操作はファイル管理機構(#69)の明示操作のみ(_memos/chat-share-files.md)。
    """
    for file_id in get_file_ids_from_chat(chat_json):
        file = Files.get_file_by_id(file_id)
        if not file or file.user_id != user.id:
            continue
        access_control = file.access_control or {}
        read = access_control.get("read") or {}
        if read.get("public") is True:
            continue
        read["public"] = True
        access_control["read"] = read
        Files.update_file_access_control_by_id(file_id, access_control)


def copy_chat_files_for_user(chat_json: dict, user) -> dict:
    """llens(#41): clone = 参照 file の実体コピー(cloner 所有・private)。

    コピーした瞬間から元 file の生死・共有状態と無縁になる。読めない・消えた file は
    差し替えずに残す(送信時に middleware の filter_files_by_user_access が drop する
    従来挙動に落ちる)。chat_json を破壊的に書き換えるため deepcopy を渡すこと。
    """
    id_map = {}
    for file_id in get_file_ids_from_chat(chat_json):
        if not has_read_access_to_file(file_id, user):
            continue
        src = Files.get_file_by_id(file_id)
        if not src:
            continue

        new_id = str(uuid.uuid4())
        new_path = src.path
        if src.path:
            try:
                with open(Storage.get_file(src.path), "rb") as f:
                    _, new_path = Storage.upload_file(f, f"{new_id}_{src.filename}")
            except Exception:
                log.exception(f"clone: storage copy failed (file_id={file_id})")
                continue

        copied = Files.insert_new_file(
            user.id,
            FileForm(
                id=new_id,
                hash=src.hash,
                filename=src.filename,
                path=new_path or "",
                data=src.data or {},
                meta=src.meta or {},
                access_control=None,
            ),
        )
        if copied:
            id_map[file_id] = new_id

    if not id_map:
        return chat_json

    def rewrite(entries):
        for entry in entries or []:
            if not (isinstance(entry, dict) and entry.get("type") in ("file", "image")):
                continue
            old_id = entry.get("id") or (entry.get("file") or {}).get("id")
            new_id = id_map.get(old_id)
            if not new_id:
                continue
            if entry.get("id"):
                entry["id"] = new_id
            if entry.get("url"):
                # image エントリの url は表示用の content URL 形式(#69)
                entry["url"] = (
                    f"/api/v1/files/{new_id}/content"
                    if entry.get("type") == "image"
                    else f"/api/v1/files/{new_id}"
                )
            if isinstance(entry.get("file"), dict) and entry["file"].get("id"):
                entry["file"]["id"] = new_id

    rewrite(chat_json.get("files"))
    for message in ((chat_json.get("history") or {}).get("messages") or {}).values():
        rewrite(message.get("files"))
    return chat_json


############################
# GetChatList
############################


@router.get("/", response_model=list[ChatTitleIdResponse])
@router.get("/list", response_model=list[ChatTitleIdResponse])
async def get_session_user_chat_list(
    user=Depends(get_verified_user), page: Optional[int] = None
):
    if page is not None:
        limit = 60
        skip = (page - 1) * limit

        return Chats.get_chat_title_id_list_by_user_id(user.id, skip=skip, limit=limit)
    else:
        return Chats.get_chat_title_id_list_by_user_id(user.id)


############################
# DeleteAllChats
############################


@router.delete("/", response_model=bool)
async def delete_all_user_chats(request: Request, user=Depends(get_verified_user)):

    if user.role == "user" and not has_permission(
        user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Chats.delete_chats_by_user_id(user.id)
    return result


############################
# GetUserChatList
############################


@router.get("/list/user/{user_id}")
async def get_user_chat_list_by_user_id(
    user_id: str,
    user=Depends(get_admin_user),
    skip: int = 0,
    limit: int = 50,
):
    # llens(#69): admin のユーザーチャット一覧(UserChatsModal)。ファイル管理の
    # 「関連チャット」と同じプレビュー形(snippet 込み)に統一 — 消費側は ChatsListModal。
    if not ENABLE_ADMIN_CHAT_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return Chats.get_chat_previews(user_id=user_id, skip=skip, limit=limit)


############################
# CreateNewChat
############################


@router.post("/new", response_model=Optional[ChatResponse])
async def create_new_chat(form_data: ChatForm, user=Depends(get_verified_user)):
    try:
        chat = Chats.insert_new_chat(user.id, form_data)
        return ChatResponse(**chat.model_dump())
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ImportChat
############################


@router.post("/import", response_model=Optional[ChatResponse])
async def import_chat(form_data: ChatImportForm, user=Depends(get_verified_user)):
    try:
        chat = Chats.import_chat(user.id, form_data)
        return ChatResponse(**chat.model_dump())
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetChats
############################


@router.get("/search", response_model=list[ChatTitleIdResponse])
async def search_user_chats(
    text: str, page: Optional[int] = None, user=Depends(get_verified_user)
):
    if page is None:
        page = 1

    limit = 60
    skip = (page - 1) * limit

    chat_list = [
        ChatTitleIdResponse(**chat.model_dump())
        for chat in Chats.get_chats_by_user_id_and_search_text(
            user.id, text, skip=skip, limit=limit
        )
    ]

    return chat_list


############################
# GetChatsByFolderId
############################


@router.get("/folder/{folder_id}", response_model=list[ChatResponse])
async def get_chats_by_folder_id(folder_id: str, user=Depends(get_verified_user)):
    folder_ids = [folder_id]
    children_folders = Folders.get_children_folders_by_id_and_user_id(
        folder_id, user.id
    )
    if children_folders:
        folder_ids.extend([folder.id for folder in children_folders])

    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_chats_by_folder_ids_and_user_id(folder_ids, user.id)
    ]


############################
# GetPinnedChats
############################


@router.get("/pinned", response_model=list[ChatResponse])
async def get_user_pinned_chats(user=Depends(get_verified_user)):
    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_pinned_chats_by_user_id(user.id)
    ]


############################
# GetChats
############################


@router.get("/all", response_model=list[ChatResponse])
async def get_user_chats(user=Depends(get_verified_user)):
    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_chats_by_user_id(user.id)
    ]


############################
# GetArchivedChats
############################


@router.get("/all/archived", response_model=list[ChatResponse])
async def get_user_archived_chats(user=Depends(get_verified_user)):
    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_archived_chats_by_user_id(user.id)
    ]


############################
# GetAllChatsInDB
############################


@router.get("/all/db", response_model=list[ChatResponse])
async def get_all_user_chats_in_db(user=Depends(get_admin_user)):
    if not ENABLE_ADMIN_EXPORT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return [ChatResponse(**chat.model_dump()) for chat in Chats.get_chats()]


############################
# Admin Chat List (#19)
############################


ADMIN_CHAT_SNIPPET_LENGTH = 160


def _get_linear_messages(chat_json: dict) -> list[dict]:
    """現行ブランチの線形 messages。無ければ history 全ノードを timestamp 順で代用。"""
    messages = chat_json.get("messages") or []
    if messages:
        return messages
    history_messages = ((chat_json.get("history") or {}).get("messages") or {}).values()
    return sorted(history_messages, key=lambda m: m.get("timestamp") or 0)


def _to_admin_chat_list_item(chat, user_name: Optional[str]) -> dict:
    """llens(#19): 一覧用の軽量行。chat JSON はここで集約し、行には含めない。

    - snippet: 最初の user メッセージ冒頭(タイトルだけでは内容が判別しづらいため)
    - turn_count: 現行ブランチの user メッセージ数 / message_count: 同ブランチ全メッセージ数
    - file_count: 分岐込みの添付 file_id ユニーク数(get_file_ids_from_chat と同じ集合)
    - usage: #57 が meta["usage"] に記録した保存値をそのまま返す(無い旧チャットは None)
    """
    chat_json = chat.chat or {}
    messages = _get_linear_messages(chat_json)

    snippet = ""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            # content blocks 形式(text パート連結)
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        snippet = " ".join(str(content or "").split())
        break

    return {
        "id": chat.id,
        "user_id": chat.user_id,
        "user_name": user_name,
        "title": chat.title,
        "snippet": snippet[:ADMIN_CHAT_SNIPPET_LENGTH],
        "models": chat_json.get("models") or [],
        "message_count": len(messages),
        "turn_count": sum(1 for m in messages if m.get("role") == "user"),
        "file_count": len(get_file_ids_from_chat(chat_json)),
        "usage": (chat.meta or {}).get("usage"),
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "archived": chat.archived,
        "pinned": chat.pinned,
        "share_id": chat.share_id,
        "folder_id": chat.folder_id,
    }


def _ensure_admin_chat_access():
    if not ENABLE_ADMIN_CHAT_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


@router.get("/admin/list")
async def get_admin_chat_list(
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order_by: str = Query("updated_at"),
    direction: str = Query("desc"),
    user_id: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    updated_from: Optional[int] = Query(None, ge=0),
    updated_to: Optional[int] = Query(None, ge=0),
    user=Depends(get_admin_user),
):
    """admin チャット閲覧ビュー(#19)用のページング一覧。

    行は chat JSON を含まない軽量形(導出列はページ分だけサーバーでパース)。
    返却は {"items": [...], "total": n}。
    """
    _ensure_admin_chat_access()

    if order_by not in ("created_at", "updated_at"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="order_by must be one of: created_at, updated_at",
        )
    if direction not in ("desc", "asc"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="direction must be one of: desc, asc",
        )

    chats, total = Chats.get_chats_page_admin(
        user_id=user_id,
        title=title,
        model=model,
        updated_from=updated_from,
        updated_to=updated_to,
        order_by=order_by,
        direction=direction,
        limit=limit,
        offset=offset,
    )

    users = Users.get_users_by_user_ids(list({chat.user_id for chat in chats}))
    user_names = {u.id: u.name for u in users}

    return {
        "items": [
            _to_admin_chat_list_item(chat, user_names.get(chat.user_id))
            for chat in chats
        ],
        "total": total,
    }


@router.get("/admin/users")
async def get_admin_chat_users(user=Depends(get_admin_user)):
    """チャットを 1 件以上持つユーザーの一覧(#19、admin のフィルタ UI 用)。"""
    _ensure_admin_chat_access()
    return Chats.get_chat_owners()


@router.get("/admin/models")
async def get_admin_chat_models(user=Depends(get_admin_user)):
    """チャットで使われている model id の一覧(#19、admin のフィルタ UI 用)。"""
    _ensure_admin_chat_access()
    return Chats.get_chat_models()


############################
# GetArchivedChats
############################


@router.get("/archived", response_model=list[ChatTitleIdResponse])
async def get_archived_session_user_chat_list(
    user=Depends(get_verified_user), skip: int = 0, limit: int = 50
):
    return Chats.get_archived_chat_list_by_user_id(user.id, skip, limit)


############################
# ArchiveAllChats
############################


@router.post("/archive/all", response_model=bool)
async def archive_all_chats(user=Depends(get_verified_user)):
    return Chats.archive_all_chats_by_user_id(user.id)


############################
# GetSharedChatById
############################


@router.get("/share/{share_id}", response_model=Optional[ChatResponse])
async def get_shared_chat_by_id(share_id: str, user=Depends(get_verified_user)):
    if user.role == "pending":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if user.role == "user" or (user.role == "admin" and not ENABLE_ADMIN_CHAT_ACCESS):
        chat = Chats.get_chat_by_share_id(share_id)
    elif user.role == "admin" and ENABLE_ADMIN_CHAT_ACCESS:
        chat = Chats.get_chat_by_id(share_id)

    if chat:
        return ChatResponse(**chat.model_dump())

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# GetChatById
############################


@router.get("/{id}", response_model=Optional[ChatResponse])
async def get_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)

    if chat:
        return ChatResponse(**chat.model_dump())

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# UpdateChatById
############################


@router.post("/{id}", response_model=Optional[ChatResponse])
async def update_chat_by_id(
    id: str, form_data: ChatForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        updated_chat = {**chat.chat, **form_data.chat}
        chat = Chats.update_chat_by_id(id, updated_chat)
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# UpdateChatMessageById
############################
class MessageForm(BaseModel):
    content: str


@router.post("/{id}/messages/{message_id}", response_model=Optional[ChatResponse])
async def update_chat_message_by_id(
    id: str, message_id: str, form_data: MessageForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id(id)

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if chat.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    chat = Chats.upsert_message_to_chat_by_id_and_message_id(
        id,
        message_id,
        {
            "content": form_data.content,
        },
    )

    # llens(wire v2): socket 前提のマルチタブ同期 emit(chat:message)は廃止。
    # 編集の永続化は上の upsert で完結する。
    return ChatResponse(**chat.model_dump())


# llens(wire v2): POST /{id}/messages/{message_id}/event(任意イベント注入 API)は
# エンドポイントごと撤去(FE・現行 llens とも消費ゼロ、socket 基盤と共に廃止)。


############################
# DeleteChatById
############################


@router.delete("/{id}", response_model=bool)
async def delete_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        result = Chats.delete_chat_by_id(id)

        return result
    else:
        if not has_permission(
            user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

        result = Chats.delete_chat_by_id_and_user_id(id, user.id)
        return result


############################
# GetPinnedStatusById
############################


@router.get("/{id}/pinned", response_model=Optional[bool])
async def get_pinned_status_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        return chat.pinned
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# PinChatById
############################


@router.post("/{id}/pin", response_model=Optional[ChatResponse])
async def pin_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.toggle_chat_pinned_by_id(id)
        return chat
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# CloneChat
############################


class CloneForm(BaseModel):
    title: Optional[str] = None


@router.post("/{id}/clone", response_model=Optional[ChatResponse])
async def clone_chat_by_id(
    form_data: CloneForm, id: str, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        updated_chat = {
            **chat.chat,
            "originalChatId": chat.id,
            "branchPointMessageId": chat.chat["history"]["currentId"],
            "title": form_data.title if form_data.title else f"Clone of {chat.title}",
        }

        chat = Chats.insert_new_chat(user.id, ChatForm(**{"chat": updated_chat}))
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# CloneSharedChatById
############################


@router.post("/{id}/clone/shared", response_model=Optional[ChatResponse])
async def clone_shared_chat_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    # llens(#41): 共有チャットの clone は chat.clone フラグでゲート(既定 off)
    if user.role == "user" and not has_permission(
        user.id, "chat.clone", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if user.role == "admin":
        chat = Chats.get_chat_by_id(id)
    else:
        chat = Chats.get_chat_by_share_id(id)

    if chat:
        updated_chat = copy.deepcopy(
            {
                **chat.chat,
                "originalChatId": chat.id,
                "branchPointMessageId": chat.chat["history"]["currentId"],
                "title": f"Clone of {chat.title}",
            }
        )
        # llens(#41): 参照 file を実体コピー(cloner 所有・private)し file_id を差し替える
        updated_chat = copy_chat_files_for_user(updated_chat, user)

        chat = Chats.insert_new_chat(user.id, ChatForm(**{"chat": updated_chat}))
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ArchiveChat
############################


@router.post("/{id}/archive", response_model=Optional[ChatResponse])
async def archive_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.toggle_chat_archive_by_id(id)
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ShareChatById
############################


@router.post("/{id}/share", response_model=Optional[ChatResponse])
async def share_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    # llens(#41): 共有は chat.share フラグでゲート(解除 DELETE は狭める操作なので常時可)
    if user.role == "user" and not has_permission(
        user.id, "chat.share", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        if chat.share_id:
            shared_chat = Chats.update_shared_chat_by_chat_id(chat.id)
        else:
            shared_chat = Chats.insert_shared_chat_by_chat_id(chat.id)

        if not shared_chat:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_MESSAGES.DEFAULT(),
            )

        # llens(#41): スナップショットが参照する file を public 化(再共有時は新規添付分も)
        publish_chat_files(shared_chat.chat, user)

        return ChatResponse(**shared_chat.model_dump())

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# DeletedSharedChatById
############################


@router.delete("/{id}/share", response_model=Optional[bool])
async def delete_shared_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        if not chat.share_id:
            return False

        result = Chats.delete_shared_chat_by_chat_id(id)
        update_result = Chats.update_chat_share_id_by_id(id, None)

        return result and update_result != None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# UpdateChatFolderIdById
############################


class ChatFolderIdForm(BaseModel):
    folder_id: Optional[str] = None


@router.post("/{id}/folder", response_model=Optional[ChatResponse])
async def update_chat_folder_id_by_id(
    id: str, form_data: ChatFolderIdForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.update_chat_folder_id_by_id_and_user_id(
            id, user.id, form_data.folder_id
        )
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )
