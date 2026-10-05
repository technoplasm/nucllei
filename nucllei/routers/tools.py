import logging
from typing import Optional
import time

from nucllei.models.tools import (
    ToolForm,
    ToolModel,
    ToolResponse,
    ToolUserModel,
    ToolUserResponse,
    Tools,
)
from nucllei.builtin_tools import is_builtin_tool_id, is_reserved_builtin_id
from nucllei.utils.plugin import load_tool_module_by_id, replace_imports
from nucllei.config import CACHE_DIR
from nucllei.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, status
from nucllei.utils.tools import get_tool_specs
from nucllei.utils.auth import get_admin_user, get_verified_user
from nucllei.utils.access_control import has_access
from nucllei.env import SRC_LOG_LEVELS

from nucllei.utils.tools import get_tool_servers_data

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


router = APIRouter()


def validate_tool_metadata(form_data: ToolForm) -> None:
    """llens(#44): tool の frontmatter 退役に伴い、title(name)/description は
    DB フィールドが唯一の真実源。name が空なら 400 で保存を拒否する。
    description は任意 — モデルの使用判断は関数 docstring 由来の specs が担い、
    top-level description は一覧 UI 用メタデータにすぎない(skill とは逆の線引き)。"""
    if not (form_data.name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tool name is required",
        )


############################
# GetTools
############################


@router.get("/", response_model=list[ToolUserResponse])
async def get_tools(request: Request, user=Depends(get_verified_user)):

    if not request.app.state.TOOL_SERVERS:
        # If the tool servers are not set, we need to set them
        # This is done only once when the server starts
        # This is done to avoid loading the tool servers every time

        request.app.state.TOOL_SERVERS = await get_tool_servers_data(
            request.app.state.config.TOOL_SERVER_CONNECTIONS
        )

    tools = Tools.get_tools()
    for server in request.app.state.TOOL_SERVERS:
        tools.append(
            ToolUserResponse(
                **{
                    "id": f"server:{server['idx']}",
                    "user_id": f"server:{server['idx']}",
                    "name": server["openapi"]
                    .get("info", {})
                    .get("title", "Tool Server"),
                    "meta": {
                        "description": server["openapi"]
                        .get("info", {})
                        .get("description", ""),
                    },
                    "access_control": request.app.state.config.TOOL_SERVER_CONNECTIONS[
                        server["idx"]
                    ]
                    .get("config", {})
                    .get("access_control", None),
                    "updated_at": int(time.time()),
                    "created_at": int(time.time()),
                }
            )
        )

    # llens(#66): tool の read ACL = 「このユーザーは使える」。picker 表示もこれで絞る
    # (実行時の enforce_access_control と同じ意味論 — utils/tools.py)。
    if user.role != "admin":
        tools = [
            tool for tool in tools if has_access(user.id, "read", tool.access_control)
        ]

    return tools


############################
# GetToolList
############################


def get_tool_module(request: Request, tool_id: str):
    """app.state.TOOLS キャッシュ越しに tool モジュールを得る(未ロードならロード)。"""
    if tool_id in request.app.state.TOOLS:
        return request.app.state.TOOLS[tool_id]
    tools_module, _ = load_tool_module_by_id(tool_id)
    request.app.state.TOOLS[tool_id] = tools_module
    return tools_module


def tool_has_valves(request: Request, tool_id: str) -> bool:
    """一覧 UI の Valves ボタン disable 用。ロード失敗は「設定不可」に倒す。"""
    try:
        return hasattr(get_tool_module(request, tool_id), "Valves")
    except Exception:
        log.exception(f"Failed to load the tool by id {tool_id}")
        return False


# llens(#47 A1): workspace のツール管理一覧は content 込みで返す(ToolUserModel)。
# 一覧内で visibility(access_control)を編集する際、update に必要な content を
# 追加 fetch なしで round-trip するため。
# llens(#66): tool 管理は admin 専有(write ACL は廃止)。
@router.get("/list", response_model=list[ToolUserModel])
async def get_tool_list(request: Request, user=Depends(get_admin_user)):
    tools = Tools.get_tools()
    for tool in tools:
        tool.has_valves = tool_has_valves(request, tool.id)
    return tools


############################
# ExportTools
############################


@router.get("/export", response_model=list[ToolModel])
async def export_tools(user=Depends(get_admin_user)):
    tools = Tools.get_tools()
    return tools


############################
# CreateNewTools
############################


# llens(#66): tool 定義の作成・変更・削除は admin 専有(workspace.tools 権限は廃止)。
# tool は Python コード実行の権力を運ぶため、3 層モデルで最も強い境界に置く
# (GHSA-p4fx のソース書換対策も admin 専有化で単純化)。
@router.post("/create", response_model=Optional[ToolResponse])
async def create_new_tools(
    request: Request,
    form_data: ToolForm,
    user=Depends(get_admin_user),
):
    if not form_data.id.isidentifier():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only alphanumeric characters and underscores are allowed in the id",
        )

    # llens(#44): title/description の真実源は DB フィールドのみ(frontmatter 退役)。
    # 空だと真実源が欠けるので保存を拒否する。
    validate_tool_metadata(form_data)

    form_data.id = form_data.id.lower()

    # llens(#42): builtin 用の `builtin_` prefix は名前空間ごと予約する。
    # 未定義の id でも prefix 一致で拒否(ユーザー作成 tool に builtin_ を使わせない)。
    if is_reserved_builtin_id(form_data.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The 'builtin_' id prefix is reserved for builtin tools",
        )

    tools = Tools.get_tool_by_id(form_data.id)
    if tools is None:
        try:
            form_data.content = replace_imports(form_data.content)
            tool_module, _ = load_tool_module_by_id(
                form_data.id, content=form_data.content
            )

            TOOLS = request.app.state.TOOLS
            TOOLS[form_data.id] = tool_module

            specs = get_tool_specs(TOOLS[form_data.id])
            tools = Tools.insert_new_tool(user.id, form_data, specs)

            tool_cache_dir = CACHE_DIR / "tools" / form_data.id
            tool_cache_dir.mkdir(parents=True, exist_ok=True)

            if tools:
                return tools
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error creating tools"),
                )
        except Exception as e:
            log.exception(f"Failed to load the tool by id {form_data.id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


############################
# GetToolsById
############################


# llens(#66): ToolModel は Python ソース(content)を含む編集用形。編集は admin 専有
# なので取得も admin に限定する(使用側は content を含まない GET / を使う)。
@router.get("/id/{id}", response_model=Optional[ToolModel])
async def get_tools_by_id(id: str, user=Depends(get_admin_user)):
    tools = Tools.get_tool_by_id(id)

    if tools:
        return tools
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateToolsById
############################


@router.post("/id/{id}/update", response_model=Optional[ToolModel])
async def update_tools_by_id(
    request: Request,
    id: str,
    form_data: ToolForm,
    user=Depends(get_admin_user),
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # llens(#42): 組み込みツールの定義(content/name/meta/specs)の真実源は repo。
    # 更新できるのは access_control のみ(admin 専有 — #66)。
    if is_builtin_tool_id(id):
        return Tools.update_tool_by_id(id, {"access_control": form_data.access_control})

    # llens(#44): frontmatter 退役に伴い title/description は DB フィールドが唯一の真実源。
    validate_tool_metadata(form_data)

    try:
        form_data.content = replace_imports(form_data.content)
        tool_module, _ = load_tool_module_by_id(id, content=form_data.content)

        TOOLS = request.app.state.TOOLS
        TOOLS[id] = tool_module

        specs = get_tool_specs(TOOLS[id])

        updated = {
            **form_data.model_dump(exclude={"id"}),
            "specs": specs,
        }

        log.debug(updated)
        tools = Tools.update_tool_by_id(id, updated)

        if tools:
            return tools
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating tools"),
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


############################
# DeleteToolsById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_tools_by_id(request: Request, id: str, user=Depends(get_admin_user)):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    # llens(#42): 組み込みツールは削除不可(repo と一緒に出荷される定義)
    if is_builtin_tool_id(id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Builtin tools cannot be deleted",
        )

    result = Tools.delete_tool_by_id(id)
    if result:
        TOOLS = request.app.state.TOOLS
        if id in TOOLS:
            del TOOLS[id]

    return result


############################
# GetToolValves
############################


# llens(GHSA-7429 / #66): 管理者 valve は秘密(API キー等)を含みうる。
# 読み書きとも admin 専有(3 層化で owner/write 判定は廃止)。
@router.get("/id/{id}/valves", response_model=Optional[dict])
async def get_tools_valves_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    tools = Tools.get_tool_by_id(id)
    if tools:
        try:
            valves = Tools.get_tool_valves_by_id(id)
            return valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# GetToolValvesSpec
############################


@router.get("/id/{id}/valves/spec", response_model=Optional[dict])
async def get_tools_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    tools = Tools.get_tool_by_id(id)
    if tools:
        tools_module = get_tool_module(request, id)

        if hasattr(tools_module, "Valves"):
            Valves = tools_module.Valves
            return Valves.schema()
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateToolValves
############################


@router.post("/id/{id}/valves/update", response_model=Optional[dict])
async def update_tools_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_admin_user)
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    tools_module = get_tool_module(request, id)

    if not hasattr(tools_module, "Valves"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    Valves = tools_module.Valves

    try:
        # null = UI の「Default」。保存は明示したフィールドだけ(exclude_unset)—
        # 未設定フィールドは読み出し側の Valves(**保存値) でコードの既定値に
        # 埋まるので、コード側の既定変更(filter の priority 等)が効き続ける。
        form_data = {k: v for k, v in form_data.items() if v is not None}
        valves = Valves(**form_data)
        Tools.update_tool_valves_by_id(id, valves.model_dump(exclude_unset=True))
        return valves.model_dump()
    except Exception as e:
        log.exception(f"Failed to update tool valves by id {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


# UserValves(per-user 設定)の GET/spec/update は撤去済み(2026-10-05、
# feature-triage「Filter / tool 機構の縮小」— 設定 UI も利用者も無かった)。
