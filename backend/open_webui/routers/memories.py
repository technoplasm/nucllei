from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import logging
from typing import Optional

from open_webui.models.memories import Memories, MemoryModel
from open_webui.utils.auth import get_verified_user
from open_webui.env import SRC_LOG_LEVELS


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

# llens(#52): memory は SQL(Memory テーブル)の素の文字列ストアに降格。ベクタ化・
# 埋め込み検索は撤去し、query は全件を自然言語でそのまま返す(Claude 方式)。fork から
# embedding/vector 基盤を一掃するための最後の切り離し。

router = APIRouter()


############################
# GetMemories
############################


@router.get("/", response_model=list[MemoryModel])
async def get_memories(user=Depends(get_verified_user)):
    return Memories.get_memories_by_user_id(user.id)


############################
# AddMemory
############################


class AddMemoryForm(BaseModel):
    content: str


class MemoryUpdateModel(BaseModel):
    content: Optional[str] = None


@router.post("/add", response_model=Optional[MemoryModel])
async def add_memory(
    request: Request,
    form_data: AddMemoryForm,
    user=Depends(get_verified_user),
):
    return Memories.insert_new_memory(user.id, form_data.content)


############################
# QueryMemory
############################


class QueryMemoryForm(BaseModel):
    content: str
    k: Optional[int] = 1


@router.post("/query")
async def query_memory(
    request: Request, form_data: QueryMemoryForm, user=Depends(get_verified_user)
):
    # llens(#52): ベクタ検索を廃止し、ユーザーの全 memory を自然言語でそのまま返す。
    # 返却 shape はフロント互換(documents[0] / metadatas[0])を維持。
    memories = Memories.get_memories_by_user_id(user.id)
    return {
        "documents": [[memory.content for memory in memories]],
        "metadatas": [[{"created_at": memory.created_at} for memory in memories]],
    }


############################
# ResetMemory
############################
@router.post("/reset", response_model=bool)
async def reset_memory(request: Request, user=Depends(get_verified_user)):
    # llens(#52): ベクタ索引の再構築は撤去。SQL の memory は真実源なので no-op。
    return True


############################
# DeleteMemoriesByUserId
############################


@router.delete("/delete/user", response_model=bool)
async def delete_memory_by_user_id(user=Depends(get_verified_user)):
    return Memories.delete_memories_by_user_id(user.id)


############################
# UpdateMemoryById
############################


@router.post("/{memory_id}/update", response_model=Optional[MemoryModel])
async def update_memory_by_id(
    memory_id: str,
    request: Request,
    form_data: MemoryUpdateModel,
    user=Depends(get_verified_user),
):
    memory = Memories.update_memory_by_id_and_user_id(
        memory_id, user.id, form_data.content
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


############################
# DeleteMemoryById
############################


@router.delete("/{memory_id}", response_model=bool)
async def delete_memory_by_id(memory_id: str, user=Depends(get_verified_user)):
    return Memories.delete_memory_by_id_and_user_id(memory_id, user.id)
