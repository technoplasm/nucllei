"""sandbox のファイルブラウザ(#150)"/api/v1/sandbox/files/*"。

利用者が sandbox の置き場をモデル経由でなく直接見て・整理し・平文を編集するための口。
開ける場所は 3 種類で、可否は `utils/ext_acl.py` の判定そのもの(別に持たない):

| 場所(jail のパス) | 一般ユーザー | admin |
|---|---|---|
| `/workspace/…` | 本人の workspace を rw | 本人の workspace を rw(他人のは見ない) |
| `/ext/public/…` | ro | rw |
| `/ext/groups/<g>/…` | メンバーは rw、それ以外は不在(404) | 全グループ rw |

- パスは jail の絶対パス(`/workspace/notes.md`・`/ext/groups/EMR/a.md`)で受け、返す。
  モデルの tool と同じ文字列なので、画面で見たパスをそのままチャットで言える
- `/ext/<それ以外>`(doc 等)はこのブラウザの対象外(404)。`/ext/groups` 直下の一覧は
  `places` の中のグループの列挙で代える(`ext_visible_groups` = 見えるグループだけ)
- ro の場所への書き換え(write / upload / delete / rename / mkdir)は 403。sandbox にも
  ro の bind として渡すので 2 重に止まる
- `.` 始まりのセグメントは扱わない(400)— 一覧でも隠す(list_workspace と同じ)。
  経路上の symlink は sandbox が辿らない(`/files/*` の O_NOFOLLOW。#152 と同じ仕組み)
- 平文の判定(`read` の `editable`): `TEXT_LIMIT` 以内・UTF-8 で decode 可・NUL を含まない
- 上書き競合: `read` が返す `mtime` を `write` の `expected_mtime` に渡す。違えば 409
  (`detail.message` = `modified` / `deleted`)— 画面は「外で変更された」を出して再読み込みか
  上書きかを選ばせる
- 書き込み(write / upload)の上限は env `SANDBOX_WRITE_FILE_LIMIT_BYTES`(write_file tool と同じ)
- quota の使用量(`usage`)は `/workspace` だけ(`/ext` は容量上限を持たない)
- `SANDBOX_URL` が無い配備では全部 404(画面も Workspace のタブを出さない — `features.enable_sandbox_files`)
- 会話へ添付する経路は持たない(会話に載せるのは exports/ 一本 — #150 の 2026-10-07 の整理)
"""

import base64
import json
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.responses import Response

from nucllei.env import SRC_LOG_LEVELS
from nucllei.utils.auth import get_verified_user
from nucllei.utils.ext_acl import (
    ext_path_access,
    is_acl_rel,
    is_safe_path_segment,
    sandbox_ext_mounts,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

WORKSPACE_ROOT = "/workspace"
EXT_ROOT = "/ext"

# 平文として開く(エディタに載せる)上限。超えたらダウンロードのみ
TEXT_LIMIT = 1024 * 1024

_TIMEOUT_S = 60


def _sandbox():
    # builtin_tools.sandbox は重い import(models 等)を伴うので遅延 import。
    # sandbox への HTTP 出口は `sandbox_post` 1 本(トークンの付け方を 2 か所に持たない)
    from nucllei.builtin_tools import sandbox

    return sandbox


def _write_limit() -> int:
    return _sandbox().WRITE_FILE_LIMIT


def enabled() -> bool:
    """この配備でファイルブラウザを出すか(`features.enable_sandbox_files`)。"""
    return bool(_sandbox().SANDBOX_URL)


def _require_sandbox():
    if not enabled():
        raise HTTPException(404, "sandbox is not configured")


class _Place:
    """要求のパスを解決した結果。`sandbox_path` は sandbox の `/files/*` に渡す形
    (workspace は相対、`/ext` は絶対)、`access` は `rw` / `ro`。"""

    def __init__(self, path: str, sandbox_path: str, access: str, kind: str):
        self.path = path
        self.sandbox_path = sandbox_path
        self.access = access
        self.kind = kind


def _resolve(user, path: str) -> _Place:
    """jail の絶対パスを ACL に照らして解決する。見えない物は 404、形が悪い物は 400。"""
    if not isinstance(path, str) or not path.startswith("/"):
        raise HTTPException(400, "path must be absolute (/workspace/… or /ext/…)")
    segs = [s for s in path.split("/") if s != ""]
    if any(not is_safe_path_segment(s) or s.startswith(".") for s in segs):
        raise HTTPException(400, f"invalid path: {path}")
    norm = "/" + "/".join(segs)
    if segs and segs[0] == "workspace":
        rel = "/".join(segs[1:]) or "."
        return _Place(norm, rel, "rw", "workspace")
    if len(segs) >= 2 and segs[0] == "ext" and is_acl_rel("/".join(segs[1:])):
        access = ext_path_access(user, norm)
        if access in ("rw", "ro"):
            return _Place(norm, norm, access, "ext")
    # `/ext/doc` 等・見えないグループ・`/ext/groups` そのものは不在と同じ
    raise HTTPException(404, f"not found: {path}")


def _require_rw(place: _Place):
    if place.access != "rw":
        raise HTTPException(403, f"read-only: {place.path}")


def _to_jail(place: _Place, sandbox_path: str) -> str:
    """sandbox の応答のパスを jail の絶対パスへ(workspace は相対で返ってくる)。"""
    if place.kind == "workspace":
        return (
            WORKSPACE_ROOT
            if sandbox_path in ("", ".")
            else (f"{WORKSPACE_ROOT}/{sandbox_path}")
        )
    return sandbox_path


async def _call(user, endpoint: str, payload: dict) -> dict:
    """sandbox の `/files/*` を本人の user_id と ACL の mounts で呼ぶ。sandbox の言い分
    (400/403/404/409/413/507)はそのまま中継し、それ以外・繋がらないは 500 にする
    (502/503/504 は画面が「バックエンドに繋がらない」バナーに使うので返さない)。"""
    sb = _sandbox()
    body = {"user_id": user.id, "mounts": sandbox_ext_mounts(user), **payload}
    try:
        return await sb.sandbox_post(endpoint, body, _TIMEOUT_S)
    except sb.SandboxHTTPError as e:
        detail = e.detail
        try:
            detail = json.loads(e.detail).get("detail", e.detail)
        except (ValueError, AttributeError):
            pass
        if e.status in (400, 403, 404, 409, 413, 507):
            raise HTTPException(e.status, detail)
        log.warning(f"[sandbox_files] {endpoint} failed: {e!r}")
        raise HTTPException(500, "sandbox returned an error")
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"[sandbox_files] {endpoint} unreachable: {e!r}")
        raise HTTPException(500, "sandbox is unreachable")


####################
# 置き場
####################


@router.get("/places")
async def get_places(user=Depends(get_verified_user)):
    """開ける場所の一覧(先頭が本人の workspace)。`/ext` は ACL で見える物だけ。"""
    _require_sandbox()
    places = [{"path": WORKSPACE_ROOT, "kind": "workspace", "rw": True}]
    for m in sandbox_ext_mounts(user):
        kind = "public" if m["path"] == f"{EXT_ROOT}/public" else "group"
        places.append({"path": m["path"], "kind": kind, "rw": bool(m["rw"])})
    return {"places": places, "write_limit_bytes": _write_limit()}


@router.get("/usage")
async def get_usage(user=Depends(get_verified_user)):
    _require_sandbox()
    res = await _call(user, "/files/usage", {})
    return {
        "used_bytes": res.get("used_bytes"),
        "total_bytes": res.get("total_bytes"),
        "quota_gb": res.get("quota_gb"),
    }


####################
# 読み
####################


class PathForm(BaseModel):
    path: str


@router.post("/list")
async def list_dir(form: PathForm, user=Depends(get_verified_user)):
    _require_sandbox()
    place = _resolve(user, form.path)
    res = await _call(user, "/files/ls", {"path": place.sandbox_path})
    entries = [
        {
            "name": e["name"],
            "path": _to_jail(place, e["path"]),
            "type": e.get("type", "file"),
            "size": e.get("size", 0),
            "mtime": e.get("mtime"),
            # 古い sandbox は返さない (None)。画面は max(mtime, ctime) で期限を出す
            "ctime": e.get("ctime"),
        }
        for e in res.get("entries", [])
        if not str(e.get("name", "")).startswith(".")
    ]
    return {
        "path": place.path,
        "rw": place.access == "rw",
        "exists": bool(res.get("exists")),
        "is_dir": bool(res.get("is_dir")),
        "truncated": bool(res.get("truncated")),
        "entries": entries,
        "cache_ttl_days": res.get("cache_ttl_days"),
    }


@router.post("/stat")
async def stat_path(form: PathForm, user=Depends(get_verified_user)):
    _require_sandbox()
    place = _resolve(user, form.path)
    res = await _call(user, "/files/stat", {"path": place.sandbox_path})
    if not res.get("exists"):
        return {"exists": False, "path": place.path}
    return {
        "exists": True,
        "path": place.path,
        "type": res.get("type"),
        "size": res.get("size", 0),
        "mtime": res.get("mtime"),
        "rw": place.access == "rw",
    }


def _as_text(data: bytes) -> Optional[str]:
    """平文として編集してよいなら文字列、だめなら None。"""
    if len(data) > TEXT_LIMIT or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def _read(user, place: _Place) -> dict:
    return await _call(
        user, "/files/read", {"path": place.sandbox_path, "nofollow": True}
    )


@router.post("/read")
async def read_file(form: PathForm, user=Depends(get_verified_user)):
    """平文なら中身を返す(`text`)。平文でない・大きすぎるものは `text: null`(ダウンロードのみ)。"""
    _require_sandbox()
    place = _resolve(user, form.path)
    st = await _call(user, "/files/stat", {"path": place.sandbox_path})
    if not st.get("exists") or st.get("type") != "file":
        raise HTTPException(404, f"not a file: {place.path}")
    base = {
        "path": place.path,
        "name": place.path.rsplit("/", 1)[-1],
        "size": st.get("size", 0),
        "mtime": st.get("mtime"),
        "rw": place.access == "rw",
    }
    if st.get("size", 0) > TEXT_LIMIT:
        return {**base, "text": None, "reason": "too_large"}
    res = await _read(user, place)
    data = base64.b64decode(res.get("b64", ""))
    text = _as_text(data)
    return {
        **base,
        "size": len(data),
        "mtime": res.get("mtime", base["mtime"]),
        "text": text,
        "reason": None if text is not None else "binary",
    }


@router.get("/download")
async def download_file(path: str, user=Depends(get_verified_user)):
    """常に添付(attachment)+ nosniff で返す — HTML・SVG をこのオリジンで描画させない。"""
    _require_sandbox()
    place = _resolve(user, path)
    res = await _read(user, place)
    data = base64.b64decode(res.get("b64", ""))
    name = place.path.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


####################
# 書き換え
####################


def _check_size(n: int):
    limit = _write_limit()
    if n > limit:
        raise HTTPException(413, f"too large: {n} bytes (limit {limit})")


class WriteForm(BaseModel):
    path: str
    content: str
    expected_mtime: Optional[float] = None
    exclusive: bool = False


@router.post("/write")
async def write_file(form: WriteForm, user=Depends(get_verified_user)):
    """平文の保存(エディタ)・新規ファイル(`exclusive`)。`expected_mtime` が今と違えば 409。"""
    _require_sandbox()
    place = _resolve(user, form.path)
    _require_rw(place)
    data = form.content.encode("utf-8")
    _check_size(len(data))
    res = await _call(
        user,
        "/files/write",
        {
            "path": place.sandbox_path,
            "b64": base64.b64encode(data).decode(),
            "expected_mtime": form.expected_mtime,
            "exclusive": form.exclusive,
            "nofollow": True,
        },
    )
    return {
        "ok": True,
        "path": place.path,
        "size": len(data),
        "mtime": res.get("mtime"),
    }


@router.post("/upload")
async def upload_file(
    dir: str = Form(...),
    overwrite: bool = Form(False),
    file: UploadFile = File(...),
    user=Depends(get_verified_user),
):
    """`dir` の下へ 1 ファイル。既に在れば 409(`overwrite` で上書き)。"""
    _require_sandbox()
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not is_safe_path_segment(name) or name.startswith("."):
        raise HTTPException(400, f"invalid file name: {file.filename!r}")
    place = _resolve(user, dir.rstrip("/") + "/" + name)
    _require_rw(place)
    limit = _write_limit()
    data = await file.read(limit + 1)
    _check_size(len(data))
    res = await _call(
        user,
        "/files/write",
        {
            "path": place.sandbox_path,
            "b64": base64.b64encode(data).decode(),
            "exclusive": not overwrite,
            "nofollow": True,
        },
    )
    return {
        "ok": True,
        "path": place.path,
        "size": len(data),
        "mtime": res.get("mtime"),
    }


@router.post("/delete")
async def delete_path(form: PathForm, user=Depends(get_verified_user)):
    _require_sandbox()
    place = _resolve(user, form.path)
    _require_rw(place)
    await _call(user, "/files/delete", {"path": place.sandbox_path})
    return {"ok": True}


class RenameForm(BaseModel):
    path: str
    dest: str


@router.post("/rename")
async def rename_path(form: RenameForm, user=Depends(get_verified_user)):
    """改名・移動。同じ場所(本人の workspace の中・同じグループの中)だけ。"""
    _require_sandbox()
    src = _resolve(user, form.path)
    dst = _resolve(user, form.dest)
    _require_rw(src)
    _require_rw(dst)
    res = await _call(
        user,
        "/files/rename",
        {"path": src.sandbox_path, "dest": dst.sandbox_path},
    )
    return {"ok": True, "path": _to_jail(dst, res.get("path", dst.sandbox_path))}


@router.post("/mkdir")
async def make_dir(form: PathForm, user=Depends(get_verified_user)):
    _require_sandbox()
    place = _resolve(user, form.path)
    _require_rw(place)
    await _call(user, "/files/mkdir", {"path": place.sandbox_path})
    return {"ok": True, "path": place.path}
