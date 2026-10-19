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
- 平文の判定(`read` の `editable`): テキストの上限(env `SANDBOX_TEXT_MAX_SIZE_MB`)以内・
  UTF-8 で decode 可・NUL を含まない
- 上書き競合: `read` が返す `mtime` を `write` の `expected_mtime` に渡す。違えば 409
  (`detail.message` = `modified` / `deleted`)— 画面は「外で変更された」を出して再読み込みか
  上書きかを選ばせる
- テキストの保存(write — エディタ・新規ファイル)の上限はテキストの上限(write_file tool と同じ)
- アップロード / ダウンロードは sandbox の `/files/raw` を**ストリーミングで中継**する(base64-in-JSON を
  使わない。UploadFile は spool されたものをチャンクで送り、ダウンロードは StreamingResponse で
  そのまま流す — メモリに載るのはチャンク分だけ)。上限は sandbox の上限(sandbox の env
  `SANDBOX_FILE_MAX_SIZE_MB`。sandbox が持つ — nucllei は `/healthz` から読めていれば先に断り、
  読めていなければ sandbox の 413 で知る)と quota(507)
- アップロードはグループの権限 `chat.file_upload`(「ファイルアップロードを許可」)が要る — 無ければ 403
  (画面もアップロードのボタンとドロップを出さない。`places` の `can_upload`)。新規ファイルの作成と
  テキストの保存は対象外
- quota の使用量(`usage`)は `/workspace` だけ(`/ext` は容量上限を持たない)
- `SANDBOX_URL` が無い配備では全部 404(画面も Workspace のタブを出さない — `features.enable_sandbox_files`)
- 会話へ添付する経路は持たない(会話に載せるのはモデルの fetch_from_workspace 一本 — 2026-10-19、
  exports/ の自動添付は廃止)
"""

import base64
import json
import logging
import re
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from nucllei.env import SRC_LOG_LEVELS
from nucllei.utils.access_control import has_permission
from nucllei.utils.auth import get_verified_user
from nucllei.utils.file_limit import sandbox_file_limit_probed
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

_TIMEOUT_S = 60

# sandbox の 413 の文面 `file too large: N bytes (limit L)` から (N, L) を拾う
_TOO_LARGE_RE = re.compile(r"(\d+) bytes \(limit (\d+)\)")


def _sandbox():
    # builtin_tools.sandbox は重い import(models 等)を伴うので遅延 import。
    # sandbox への HTTP 出口は `sandbox_post` 1 本(トークンの付け方を 2 か所に持たない)
    from nucllei.builtin_tools import sandbox

    return sandbox


def _text_limit() -> int:
    """平文として開く・保存する上限(テキストの上限)。超えたら開けない(ダウンロードのみ)。"""
    return _sandbox().TEXT_MAX_BYTES


def _can_upload(request: Request, user) -> bool:
    """アップロードしてよいか — グループの権限 `chat.file_upload`(admin は常に可)。
    チャットへのアップロード(`routers/files.py`)と同じ判定。"""
    return user.role == "admin" or has_permission(
        user.id, "chat.file_upload", request.app.state.config.USER_PERMISSIONS
    )


def _too_large(size: Optional[int], limit: Optional[int]) -> HTTPException:
    """413。detail は `{message, size, limit}` — 画面が上限の値を出す。"""
    return HTTPException(
        413,
        {
            "message": f"too large: {size} bytes (limit {limit})",
            "size": size,
            "limit": limit,
        },
    )


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


def _relay(e: Exception, endpoint: str) -> HTTPException:
    """sandbox の失敗を画面向けの HTTPException に。sandbox の言い分(400/403/404/409/413/507)は
    そのまま中継し(413 は上限の値を拾って `_too_large` の形に)、それ以外・繋がらないは 500
    (502/503/504 は画面が「バックエンドに繋がらない」バナーに使うので返さない)。"""
    sb = _sandbox()
    if isinstance(e, HTTPException):
        return e
    if isinstance(e, sb.SandboxHTTPError):
        detail = e.detail
        try:
            detail = json.loads(e.detail).get("detail", e.detail)
        except (ValueError, AttributeError):
            pass
        if e.status == 413:
            m = _TOO_LARGE_RE.search(str(detail))
            if m:
                return _too_large(int(m.group(1)), int(m.group(2)))
            return _too_large(None, None)
        if e.status in (400, 403, 404, 409, 507):
            return HTTPException(e.status, detail)
        log.warning(f"[sandbox_files] {endpoint} failed: {e!r}")
        return HTTPException(500, "sandbox returned an error")
    log.warning(f"[sandbox_files] {endpoint} unreachable: {e!r}")
    return HTTPException(500, "sandbox is unreachable")


async def _call(user, endpoint: str, payload: dict) -> dict:
    """sandbox の `/files/*` を本人の user_id と ACL の mounts で呼ぶ(失敗は `_relay`)。"""
    sb = _sandbox()
    body = {"user_id": user.id, "mounts": sandbox_ext_mounts(user), **payload}
    try:
        return await sb.sandbox_post(endpoint, body, _TIMEOUT_S)
    except Exception as e:
        raise _relay(e, endpoint)


def _raw_params(user, place: _Place, **extra) -> dict:
    """`/files/raw` の query。mounts はこの場所を覆う bind だけ(query に全グループを載せない)。"""
    mounts = [
        m
        for m in sandbox_ext_mounts(user)
        if place.path == m["path"] or place.path.startswith(m["path"] + "/")
    ]
    return _sandbox().raw_params(user.id, place.sandbox_path, mounts, **extra)


####################
# 置き場
####################


@router.get("/places")
async def get_places(request: Request, user=Depends(get_verified_user)):
    """開ける場所の一覧(先頭が本人の workspace)。`/ext` は ACL で見える物だけ。
    `text_limit_bytes` はテキストの上限、`upload_limit_bytes` は sandbox の上限(`/healthz` から
    読めていなければ null — 画面は事前チェックをせず sandbox の 413 で知る)、`can_upload` は
    アップロードの権限。"""
    _require_sandbox()
    places = [{"path": WORKSPACE_ROOT, "kind": "workspace", "rw": True}]
    for m in sandbox_ext_mounts(user):
        kind = "public" if m["path"] == f"{EXT_ROOT}/public" else "group"
        places.append({"path": m["path"], "kind": kind, "rw": bool(m["rw"])})
    return {
        "places": places,
        "text_limit_bytes": _text_limit(),
        "upload_limit_bytes": sandbox_file_limit_probed(),
        "can_upload": _can_upload(request, user),
    }


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
    if len(data) > _text_limit() or b"\x00" in data:
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
    if st.get("size", 0) > _text_limit():
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
    """sandbox の `/files/raw` をそのまま流す(StreamingResponse、Content-Length 付き)。
    常に添付(attachment)+ nosniff で返す — HTML・SVG をこのオリジンで描画させない。"""
    _require_sandbox()
    place = _resolve(user, path)
    try:
        length, chunks = await _sandbox().sandbox_get_raw(_raw_params(user, place))
    except Exception as e:
        raise _relay(e, "/files/raw")
    name = place.path.rsplit("/", 1)[-1]
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if length is not None:
        headers["Content-Length"] = str(length)
    return StreamingResponse(
        chunks, media_type="application/octet-stream", headers=headers
    )


####################
# 書き換え
####################


def _check_size(n: int):
    limit = _text_limit()
    if n > limit:
        raise _too_large(n, limit)


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
    request: Request,
    dir: str = Form(...),
    overwrite: bool = Form(False),
    file: UploadFile = File(...),
    user=Depends(get_verified_user),
):
    """`dir` の下へ 1 ファイル。既に在れば 409(`overwrite` で上書き)。権限 `chat.file_upload` が
    無ければ 403。本文は spool された UploadFile からチャンクで sandbox の `/files/raw` へ流す。"""
    _require_sandbox()
    if not _can_upload(request, user):
        raise HTTPException(403, "file upload is not permitted")
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not is_safe_path_segment(name) or name.startswith("."):
        raise HTTPException(400, f"invalid file name: {file.filename!r}")
    place = _resolve(user, dir.rstrip("/") + "/" + name)
    _require_rw(place)
    sb = _sandbox()
    size = file.size  # multipart の解析で数えた大きさ(Starlette)
    limit = sandbox_file_limit_probed()
    if limit is not None and size is not None and size > limit:
        # sandbox の上限を読めていれば送る前に断る(読めていなければ sandbox の 413)
        raise _too_large(size, limit)
    await file.seek(0)
    try:
        res = await sb.sandbox_put_raw(
            _raw_params(user, place, exclusive=not overwrite),
            _upload_chunks(file),
            content_length=size,
        )
    except Exception as e:
        raise _relay(e, "/files/raw")
    return {
        "ok": True,
        "path": place.path,
        "size": res.get("size", size),
        "mtime": res.get("mtime"),
    }


async def _upload_chunks(file: UploadFile):
    """spool された UploadFile をチャンクで読む(全体を bytes にしない)。"""
    chunk = _sandbox().RAW_CHUNK
    while True:
        data = await file.read(chunk)
        if not data:
            return
        yield data


class DeleteForm(BaseModel):
    path: str
    permanent: bool = False


@router.post("/delete")
async def delete_path(form: DeleteForm, user=Depends(get_verified_user)):
    """削除。`/workspace` の中(`trash/` の外)は sandbox が既定で `/workspace/trash/` の直下へ移す
    (同名は `-2` `-3`…)— 応答 `{ok, trashed: true, path: "/workspace/trash/<名前>"}`。
    `permanent: true`・`trash/` の中・`/ext` は完全に消す(`{ok, trashed: false}`)。
    `trashed` を返さない古い sandbox は常に完全削除なので `false` として返す。"""
    _require_sandbox()
    place = _resolve(user, form.path)
    _require_rw(place)
    res = await _call(
        user,
        "/files/delete",
        {"path": place.sandbox_path, "permanent": form.permanent},
    )
    if res.get("trashed") and res.get("path"):
        return {"ok": True, "trashed": True, "path": _to_jail(place, res["path"])}
    return {"ok": True, "trashed": False}


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
