import asyncio
import copy
import logging
import time
import uuid
from typing import Optional


from nucllei.models.chats import (
    ChatForm,
    ChatImportForm,
    ChatModel,
    ChatResponse,
    Chats,
    ChatTitleIdResponse,
)
from nucllei.models.executions import ExecutionModel, Executions
from nucllei.models.files import FileForm, Files
from nucllei.models.users import Users

from nucllei.constants import ERROR_MESSAGES
from nucllei.env import SRC_LOG_LEVELS
from nucllei.storage.provider import Storage
from nucllei.tasks import list_task_ids_by_chat_id, stop_task
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel


from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.utils.access_control import has_permission, has_read_access_to_file
from nucllei.utils import execution_resume
from nucllei.utils.executions import (
    ERROR_KIND_META_KEY,
    EXEC_SPAWN_GRACE_S,
    SUBAGENT_SESSION_META_KEY,
    has_exec_registry_entry,
    stop_execution,
    stop_executions_by_chat_id,
)

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


async def _stop_chat_tasks(chat_id: str) -> None:
    """chat_id に紐づく走行中 completion タスクを stop する。"""
    # stop_task の cleanup callback が chat_tasks を書き換えるためコピーを回す
    for task_id in list(list_task_ids_by_chat_id(chat_id)):
        try:
            await stop_task(task_id)
        except ValueError:
            # 既に完走して registry から消えた task — stop の目的は達成済み
            pass


async def _mark_chat_executions_delivered(chat_id: str) -> None:
    """llens(#110 契約 M2): 削除系 cascade の恒久断念 — 配達先が消えるため、
    当該 chat の未配達 terminal 行(直前の stop で terminal 化した分を含む)へ
    delivered_at を立てる。立てないとサーバ駆動配達 driver が消えた chat へ
    幽霊配達を試み続ける。"""
    rows = await asyncio.to_thread(
        Executions.get_undelivered_terminal_by_chat_id, chat_id
    )
    if rows:
        await asyncio.to_thread(Executions.mark_delivered, [row.id for row in rows])


async def _stop_chat_tree_tasks(chat_id: str) -> None:
    """llens(#107): chat 自身 + 全子孫 agent session の走行中タスクを stop する。

    soft delete の前に呼び、消えた chat へ書き続ける孤児タスクを作らない。
    llens(#110): 非同期 tool 実行(execution)は `exec:{chat_id}` 名前空間で
    親ターンから detach されており、completion タスクの cancel 連鎖では止まらない
    (設計 D6)。chat 削除だけは cascade する(設計 §5)ので明示的に停止し、
    未配達行は配達先消滅として delivered 確定する(契約 M2)。
    """
    for target_id in [chat_id, *Chats.get_descendant_chat_ids(chat_id)]:
        await _stop_chat_tasks(target_id)
        await stop_executions_by_chat_id(target_id)
        await _mark_chat_executions_delivered(target_id)


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

    # llens(#107): soft delete の前に走行中タスクを stop。子 agent session の走行は
    # 親タスクの cancel 連鎖(run_subagent 側)で止まるため、自 chat 分の stop で足りる。
    # llens(#110): execution は detach されており cancel 連鎖では止まらない
    # (設計 §5 — 「cancel 連鎖で止まる」前提は exec に崩壊する)ので明示停止する。
    # 子 agent session には exec を持たせない(D9)ため、走査は自 chat 分で足りる。
    # B-5: chat ごとの DB 往復(N+1)を避ける — exec の停止・恒久断念はユーザー単位の
    # 一括読みで行う(stop → 断念確定の順は chat 単位経路と同じ。CAS 負けの自然完走
    # 分は後段の undelivered 一括 mark が拾う — 契約 M2)。
    for chat_id in await asyncio.to_thread(Chats.get_chat_ids_by_user_id, user.id):
        await _stop_chat_tasks(chat_id)  # in-process registry のみ(DB を打たない)
    for row in await asyncio.to_thread(
        Executions.get_running_executions_by_user_id, user.id
    ):
        await stop_execution(row.id, mark_delivered=True)
    rows = await asyncio.to_thread(
        Executions.get_undelivered_terminal_by_user_id, user.id
    )
    if rows:
        await asyncio.to_thread(Executions.mark_delivered, [row.id for row in rows])

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
        "kind": chat.kind,
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
        "deleted_at": chat.deleted_at,
    }


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
    deleted: str = Query("include"),
    shared: str = Query("include"),
    kind: Optional[str] = Query(None),
    user=Depends(get_admin_user),
):
    """admin チャット閲覧ビュー(#19)用のページング一覧。

    行は chat JSON を含まない軽量形(導出列はページ分だけサーバーでパース)。
    返却は {"items": [...], "total": n}。
    """
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
    if deleted not in ("include", "exclude", "only"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="deleted must be one of: include, exclude, only",
        )
    if shared not in ("include", "exclude", "only"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="shared must be one of: include, exclude, only",
        )
    if kind not in (None, "user", "agent"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="kind must be one of: user, agent",
        )

    chats, total = Chats.get_chats_page_admin(
        user_id=user_id,
        title=title,
        model=model,
        updated_from=updated_from,
        updated_to=updated_to,
        deleted=deleted,
        shared=shared,
        kind=kind,
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
    return Chats.get_chat_owners()


@router.get("/admin/models")
async def get_admin_chat_models(user=Depends(get_admin_user)):
    """チャットで使われている model id の一覧(#19、admin のフィルタ UI 用)。"""
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

    # llens: admin は生 chat_id でも開ける(他人のチャット閲覧 — 管理者は常に可、2026-10-03)
    if user.role == "admin":
        chat = Chats.get_chat_by_id(share_id)
    else:
        chat = Chats.get_chat_by_share_id(share_id)

    if chat:
        return ChatResponse(**chat.model_dump())

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# GetChatById
############################


def _get_chat_as_owner_or_admin(id: str, user) -> Optional[ChatModel]:
    """llens(#19): owner のチャット、または admin なら他人のチャットも返す
    (管理者は常に可 — 2026-10-03)。admin の閲覧経路(/share/{id} の admin 分岐)と同じゲート。"""
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if not chat and user.role == "admin":
        chat = Chats.get_chat_by_id(id)
        # 削除済み(soft delete)は admin でも通常経路では存在しない扱い。
        # 閲覧は /share/{id} の admin 分岐のみ(削除済み表示つき — 2026-07-19 仕様)
        if chat and chat.deleted_at:
            chat = None
    return chat


@router.get("/{id}", response_model=Optional[ChatResponse])
async def get_chat_by_id(id: str, user=Depends(get_verified_user)):
    # llens(#19): admin は他人のチャットも取得可(共有モーダルが share_id を読むため。
    # 内容自体は既に /share/{id} の admin 分岐で閲覧できるので新たな露出はない)
    chat = _get_chat_as_owner_or_admin(id, user)

    if chat:
        return ChatResponse(**chat.model_dump())

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# GetChildChatsById (#107)
############################


@router.get("/{id}/children")
async def get_chat_children_by_id(id: str, user=Depends(get_verified_user)):
    """llens(#107): 親チャット直下の agent session 一覧(サイドバー tree / 親画面用)。
    所有者のみ(admin の他人チャット閲覧経路には載せない)。"""
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )

    return [
        {
            "id": child.id,
            "title": child.title,
            "kind": child.kind,
            "updated_at": child.updated_at,
            "created_at": child.created_at,
            "parent_message_id": child.parent_message_id,
        }
        for child in Chats.get_child_chats_by_parent_id(id, user.id)
    ]


############################
# Executions(#110)
############################


def _execution_item(execution: ExecutionModel) -> dict:
    """一覧・停止応答の行(result 本文は載せない — 回収は await_task の仕事)。

    liveness は**検出のみで書かない**(設計 §4: orphaned の権威は boot スイープと
    個別経路)。registry に居ない running をグレース超過で orphaned として見せる。
    delivered(#110 契約 M2)は「結果がモデルの文脈に載った」事実 — FE の
    未回収バッジ(terminal ∧ !delivered)と poll 述語の根拠。
    """
    status_ = execution.status
    if (
        status_ == "running"
        and not has_exec_registry_entry(execution.id)
        and int(time.time()) - execution.created_at >= EXEC_SPAWN_GRACE_S
    ):
        status_ = "orphaned"

    return {
        "id": execution.id,
        "tool_name": execution.tool_name,
        "status": status_,
        "created_at": execution.created_at,
        "finished_at": execution.finished_at,
        "duration": execution.duration,
        "error": execution.error,
        "delivered": execution.delivered_at is not None,
        # run_subagent の子 agent session(実行中に exec 行 meta へ永続化 — リロード後の
        # カードが子 session へ再リンクする。他の tool / 子の無い行は None)
        "session_id": (execution.meta or {}).get(SUBAGENT_SESSION_META_KEY),
        # #145: 失敗の種別(call / failure / outcome / violation / fatal — utils/tool_errors)。
        # FE は error の文面を解釈せずこれで見た目を決める。成功・旧行・stopped / orphaned は None
        "error_kind": (execution.meta or {}).get(ERROR_KIND_META_KEY),
    }


# B-6: scope=active の「直近終端」窓。FE の poll 述語の追従窓
# (executions.svelte.ts RECENT_DELIVERY_WINDOW_S)と同値に保つ — active 応答は
# 「FE の tick が差し替えたい行」の上位集合でなければならない
EXEC_ACTIVE_RECENT_WINDOW_S = 10 * 60


@router.get("/{id}/executions")
async def get_chat_executions_by_id(
    id: str,
    user=Depends(get_verified_user),
    scope: Optional[str] = Query(default=None, pattern="^active$"),
):
    """llens(#110): chat の execution 行一覧(バックグラウンドチップ用)。所有者のみ。

    応答は {executions, resume_suspended} のオブジェクト形(契約 M2)。
    resume_suspended は連鎖上限(ASYNC_EXEC_RESUME_CHAIN_LIMIT)でサーバ駆動配達が
    保留中であることの表示用 — 人間の発話で自然解除される。

    scope=active(B-6)は poll tick 用の有界応答: running ∨ 未配達 ∨ 直近終端のみ
    返す(terminal ∧ 配達済み ∧ 古い行は不変 — FE はマージで保持)。全量既定は
    seed(loadChat)の履歴描画用(ExecutionBlock は store に無い行を running 扱い
    するため、seed を絞ると古いブロックが壊れる)。
    """
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if scope == "active":
        executions = Executions.get_active_executions_by_chat_id(
            id, int(time.time()) - EXEC_ACTIVE_RECENT_WINDOW_S
        )
    else:
        executions = Executions.get_executions_by_chat_id(id)
    items = [_execution_item(execution) for execution in executions]
    resume_suspended = execution_resume.is_resume_suspended(chat.chat) and any(
        item["status"] != "running" and not item["delivered"] for item in items
    )
    return {"executions": items, "resume_suspended": resume_suspended}


@router.post("/{id}/executions/{exec_id}/stop")
async def stop_chat_execution_by_id(
    id: str, exec_id: str, user=Depends(get_verified_user)
):
    """llens(#110): execution の個別停止(チップの停止導線)。

    所有者検証 + exec が当該 chat のものかを確認してから stop_execution(stopped へ
    CAS → 勝ったときだけ task cancel)。既に終端なら no-op で最新行を返す(冪等)。
    """
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )

    execution = Executions.get_execution_by_id(exec_id)
    if execution is None or execution.chat_id != id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    stopped = await stop_execution(exec_id) or execution
    # llens(#110): stop の CAS 勝者はサーバ駆動配達の wake 起点(契約 M3 ②)。
    # registry に居た exec は task 終了の cleanup hook でも起きるが、再起動残骸
    # (registry 不在の running 行)を stopped にした場合はここが唯一の起点。
    execution_resume.wake(id)
    return _execution_item(stopped)


############################
# UpdateChatById
############################


def _assert_not_agent_session(chat: Optional[ChatModel]):
    """llens(#107): agent session(kind="agent")への HTTP 経由の変更操作を拒否する。
    書き手は親 tool 実行の内部経路のみ。削除も親 chat 削除の cascade でのみ起きる。
    clone はガードしない(= 「介入したいときは fork」— 生成物は kind="user")。"""
    if chat is not None and chat.kind == "agent":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agent session is read-only",
        )


def _assert_message_history_preserved(old_chat: dict, new_chat: dict):
    """llens(#31): メッセージ履歴は append-only — 監査証憑を FE/API から壊せないよう、
    既存メッセージの消失と content の非前方一致な書き換えを拒否する。
    編集は新ブランチとして積む(「編集して送信」の既存挙動)。content の追記
    (ストリーミング終端の保存等)・ブランチ追加・メタ情報(title/tags/params 等)は通す。"""

    def _messages_of(chat: dict) -> dict:
        history = chat.get("history")
        messages = history.get("messages") if isinstance(history, dict) else None
        return messages if isinstance(messages, dict) else {}

    old_messages = _messages_of(old_chat)
    new_messages = _messages_of(new_chat)

    for message_id, old_message in old_messages.items():
        new_message = new_messages.get(message_id)
        if new_message is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Chat message history is append-only; messages cannot be removed.",
            )
        old_content = old_message.get("content")
        new_content = new_message.get("content")
        if old_content == new_content:
            continue
        if (
            isinstance(old_content, str)
            and isinstance(new_content, str)
            and new_content.startswith(old_content)
        ):
            continue
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chat message history is append-only; message content cannot be rewritten.",
        )


@router.post("/{id}", response_model=Optional[ChatResponse])
async def update_chat_by_id(
    id: str, form_data: ChatForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    _assert_not_agent_session(chat)

    # llens(#110 敵対検証 3): 保存を CAS 化 — 読んだ updated_at を期待値に
    # guarded UPDATE(配達 driver の append_delivery_turn と同じスタンプの取り合い)。
    # 無ガードの read-modify-write だと、read と write の間に driver が notice を
    # 追記した場合、FE の古い履歴で丸ごと上書きされ notice が無言で消える。
    # CAS 負けは再読して append-only 検証を新鮮な chat で再評価: 通れば再試行
    # (最大 3 回)、通らなければ既存どおり 400(FE の save-400 リカバリに乗る)。
    for _ in range(3):
        updated_chat = {**chat.chat, **form_data.chat}
        _assert_message_history_preserved(chat.chat, updated_chat)  # 通らねば 400
        cas_status, updated = Chats.update_chat_by_id_guarded(
            id, updated_chat, chat.updated_at
        )
        if cas_status == "ok":
            return ChatResponse(**updated.model_dump())
        if cas_status == "missing":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        chat = Chats.get_chat_by_id_and_user_id(id, user.id)
        if not chat:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

    # CAS 3 連敗(書き込みが殺到)— FE に再取得と再保存を委ねる
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Chat was modified concurrently; refetch and retry.",
    )


# llens(wire v2): POST /{id}/messages/{message_id}/event(任意イベント注入 API)は
# エンドポイントごと撤去(FE・現行 llens とも消費ゼロ、socket 基盤と共に廃止)。
# llens(#31): POST /{id}/messages/{message_id}(メッセージ content の無痕跡上書き API)も
# 撤去 — FE・README_API とも消費ゼロで、append-only の監査制約と両立しない。


############################
# DeleteChatById
############################


@router.delete("/{id}", response_model=bool)
async def delete_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        # llens(#107): agent session の直接削除は拒否(親削除の cascade でのみ消える)。
        # soft delete の前に自身 + 子孫の走行中タスクを stop(孤児タスク防止)
        chat = Chats.get_chat_by_id(id)
        _assert_not_agent_session(chat)
        if chat:
            await _stop_chat_tree_tasks(id)

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

        # llens(#107): 所有チャットに限りタスク stop(所有外の chat id で他人の走行を
        # 止められないよう、fetch が None ならガードも stop も踏まない)
        chat = Chats.get_chat_by_id_and_user_id(id, user.id)
        _assert_not_agent_session(chat)
        if chat:
            await _stop_chat_tree_tasks(id)

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
        _assert_not_agent_session(chat)
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
        _assert_not_agent_session(chat)
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

    # llens(#19): admin は他人のチャットも共有化できる
    chat = _get_chat_as_owner_or_admin(id, user)
    if chat:
        # llens(#107): agent session は共有(/s/)不可。unshare は現状維持(無害)
        _assert_not_agent_session(chat)
        if chat.share_id:
            shared_chat = Chats.update_shared_chat_by_chat_id(chat.id)
        else:
            shared_chat = Chats.insert_shared_chat_by_chat_id(chat.id)

        if not shared_chat:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_MESSAGES.DEFAULT(),
            )

        # llens(#41): スナップショットが参照する file を public 化(再共有時は新規添付分も)。
        # 「共有者所有の file のみ public 化」の不変条件は chat の owner 基準 —
        # admin が他人のチャットを共有する場合も public 化されるのは owner の file だけ
        owner = user if chat.user_id == user.id else Users.get_user_by_id(chat.user_id)
        if owner:
            publish_chat_files(shared_chat.chat, owner)

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
    # llens(#19): admin は他人の共有も解除できる
    # (狭める操作なので owner 側の共有権限ゲートは不要 — 既存方針どおり常時可)
    chat = _get_chat_as_owner_or_admin(id, user)
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
