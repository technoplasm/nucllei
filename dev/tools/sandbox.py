"""
title: Sandbox
description: サーバーサイドの隔離環境 (nsjail) で Python / shell を実行する。
author: Ken Enda
version: 0.3.1

changelog:
  0.3.1: llens fork (v0.6.5 基点) への適合 (#18-3)。0.9 ベースとの API 差分を吸収:
         Files.insert_new_file / get_file_by_id は sync (await 除去)、
         Storage.upload_file は 2 引数、メッセージ files の永続化は
         add_message_files_by_id_and_message_id が無いため
         get_chat_by_id + upsert_message_to_chat_by_id_and_message_id でマージ。
  0.3.0: import_chat_file に file_id 引数を追加。large-file-gate 等で context から外れた
         添付も file_id 直指定で取り込めるようにした (jail は無ネットワークで pyfetch 不可の
         ため、添付一覧に無いファイルの回収経路が無かった)。所有者チェック付き。
  0.2.0: run_python を stateful 化 (会話内で変数・import が永続。__chat_id__ ごとの
         常駐 kernel = /exec_cell)。従来の使い捨て版は run_python_once にリネーム。
         最後の式は notebook 風に値表示、セル timeout は中断 (namespace 保持)。
  0.1.1: docstring を schema (目的1行 + :param:) に絞り、利用ガイド (状態モデル・
         入出力の流れ・日本語作図) は system prompt (prompts/llens.md) に移行。
  0.1.0: 初版 (PoC)。run_python / run_shell / import_chat_file / list_workspace /
         export_workspace_file。outputs/ の自動回収・チャット添付、
         実行中の経過秒数表示 (#100 で撤去 — nucllei が自動表示する)。
"""

import base64
import io
import logging
import mimetypes
import os
import uuid
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from pydantic import BaseModel, Field

# 型駆動レンダリング(#20): code 引数はハイライト付き、戻り値は pre-wrap の text 表示
from nucllei.utils.tool_render import Code, Text

logger = logging.getLogger(__name__)

# --- Nucllei 内部 API（ランタイムで解決される） ---------------------------
try:
    from nucllei.models.chats import Chats
    from nucllei.models.files import Files, FileForm
    from nucllei.storage.provider import Storage
except Exception:  # ローカルでの静的解析用フォールバック
    Chats = None
    Files = None
    FileForm = None
    Storage = None


# -----------------------------------------------------------------------------
# 低レベルヘルパ (mount_tool と同パターン)
# -----------------------------------------------------------------------------
def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024


def _attachment_dict(file_id: str, name: str, content_type: str, size: int) -> dict:
    """メッセージ files ストアに入れる添付オブジェクト (mount_tool と同形)。"""
    return {
        "type": "file",
        "id": file_id,
        "name": name,
        "status": "uploaded",
        "url": f"/api/v1/files/{file_id}",
        "error": "",
        "itemId": str(uuid.uuid4()),
        "file": {
            "id": file_id,
            "filename": name,
            "meta": {"name": name, "content_type": content_type, "size": size},
        },
    }


def _register_bytes_file(user_id: str, display_name: str, data: bytes) -> dict:
    """bytes を Storage + Files に登録し、添付 dict を返す (v0.6.5: 全部 sync)。"""
    file_id = str(uuid.uuid4())
    content_type = mimetypes.guess_type(display_name)[0] or "application/octet-stream"
    stored_name = f"{file_id}_{display_name}"
    contents, path = Storage.upload_file(io.BytesIO(data), stored_name)
    Files.insert_new_file(
        user_id,
        FileForm(
            **{
                "id": file_id,
                "filename": display_name,
                "path": path,
                "data": {},
                "meta": {
                    "name": display_name,
                    "content_type": content_type,
                    "size": len(data),
                },
            }
        ),
    )
    return _attachment_dict(file_id, display_name, content_type, len(data))


def _iter_context_files(__files__, __metadata__):
    """チャットコンテキストのファイル参照を正規化して列挙 (mount_tool と同形)。"""
    raw = []
    if __files__:
        raw = __files__
    elif __metadata__ and __metadata__.get("files"):
        raw = __metadata__["files"]
    for f in raw or []:
        inner = f.get("file", {}) if isinstance(f, dict) else {}
        fid = (f.get("id") if isinstance(f, dict) else None) or inner.get("id")
        fname = (
            (f.get("name") if isinstance(f, dict) else None)
            or inner.get("filename")
            or (inner.get("meta") or {}).get("name")
        )
        if fid:
            yield fid, fname


# -----------------------------------------------------------------------------
# Tool 本体
# -----------------------------------------------------------------------------
class Tools:
    class Valves(BaseModel):
        sandbox_url: str = Field(
            default="http://sandbox:8000",
            description="sandbox サービスの URL (docker network 内)",
        )
        default_timeout_s: int = Field(
            default=120, description="実行 wall clock 既定秒数 (サーバー上限で clamp)"
        )
        debug: bool = Field(default=False, description="詳細ログ")

    def __init__(self):
        self.valves = self.Valves()

    # ---- HTTP -----------------------------------------------------------------
    async def _post(self, path: str, payload: dict, timeout_s: float) -> dict:
        url = self.valves.sandbox_url.rstrip("/") + path
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_s)
        ) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    raise RuntimeError(f"sandbox HTTP {resp.status}: {body}")
                return await resp.json()

    # ---- outputs/ の回収 → OWUI Files 登録 + チャット添付 ----------------------
    async def _persist_and_emit_files(
        self,
        attachments,
        chat_id,
        message_id,
        __event_emitter__,
    ):
        """新規添付をメッセージに反映。v0.6.5 には add_message_files_by_id_and_message_id
        が無いため、既存 files を読んでマージし upsert で永続化する。"""
        merged = list(attachments)
        if Chats is not None and chat_id and message_id:
            try:
                chat = Chats.get_chat_by_id(chat_id)
                if chat is not None:
                    messages = chat.chat.get("history", {}).get("messages", {})
                    existing = (messages.get(message_id) or {}).get("files") or []
                    merged = existing + list(attachments)
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        chat_id,
                        message_id,
                        {"files": merged},
                    )
            except Exception as e:
                logger.error(f"[sandbox] persist failed: {e!r} (emit のみで継続)")
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "chat:message:files",
                    "data": {"files": merged},
                }
            )

    async def _register_outputs(
        self,
        user_id,
        outputs,
        chat_id,
        message_id,
        __event_emitter__,
    ) -> list[str]:
        """sandbox の outputs 差分を OWUI Files に登録・添付し、報告行を返す。"""
        lines = []
        attachments = []
        for out in outputs:
            name = out["name"]
            if out.get("truncated") or "b64" not in out:
                lines.append(
                    f"- {name} ({_human_size(out['size'])}): 転送上限超過。"
                    f"workspace には残っている"
                )
                continue
            try:
                att = _register_bytes_file(
                    user_id, os.path.basename(name), base64.b64decode(out["b64"])
                )
                attachments.append(att)
                line = f"- {name} ({_human_size(out['size'])}) file_id={att['id']}"
                if (att["file"]["meta"]["content_type"] or "").startswith("image/"):
                    line += (
                        f" 画像。本文に ![{name}](/api/v1/files/{att['id']}/content)"
                        f" と書くと表示される"
                    )
                lines.append(line)
            except Exception as e:
                lines.append(f"- {name}: 登録失敗 {e!r}")
        if attachments:
            await self._persist_and_emit_files(
                attachments, chat_id, message_id, __event_emitter__
            )
        return lines

    def _format_exec_result(self, res: dict, output_lines: list[str]) -> str:
        parts = [f"exit_code={res['exit_code']} ({res['duration_s']}s)"]
        if res.get("timed_out"):
            parts[0] += " **タイムアウト (time_limit で強制終了)**"
        stdout = res.get("stdout", "")
        stderr = res.get("stderr", "")
        if stdout:
            parts.append("--- stdout ---\n" + stdout.rstrip("\n"))
            if res.get("stdout_truncated"):
                parts.append("(stdout は上限で切り詰め)")
        if stderr:
            parts.append("--- stderr ---\n" + stderr.rstrip("\n"))
            if res.get("stderr_truncated"):
                parts.append("(stderr は上限で切り詰め)")
        if not stdout and not stderr:
            parts.append("(出力なし)")
        if output_lines:
            parts.append(
                "--- 生成ファイル (outputs/) — チャットに添付済み ---\n"
                + "\n".join(output_lines)
            )
        return "\n".join(parts)

    def _format_cell_result(self, res: dict, output_lines: list[str]) -> str:
        parts = []
        if res.get("session_reset"):
            parts.append(
                "※ 実行環境がリセットされ、以前の変数・import は失われています "
                "(必要なら定義し直してから続けてください)。"
            )
        stdout = res.get("stdout", "")
        error = res.get("error")
        if res.get("timed_out"):
            parts.append("**タイムアウト (セルを中断。変数・import は保持)**")
        if stdout:
            parts.append(stdout.rstrip("\n"))
            if res.get("stdout_truncated"):
                parts.append("(出力は上限で切り詰め)")
        if error:
            parts.append("--- エラー ---\n" + str(error).rstrip("\n"))
        if not stdout and not error and not res.get("timed_out"):
            parts.append("(出力なし)")
        if output_lines:
            parts.append(
                "--- 生成ファイル (outputs/) — チャットに添付済み ---\n"
                + "\n".join(output_lines)
            )
        parts.append(f"({res.get('duration_s', 0)}s)")
        return "\n".join(parts)

    async def _exec(
        self,
        lang,
        code,
        timeout_s,
        __user__,
        __event_emitter__,
        __chat_id__,
        __message_id__,
    ) -> str:
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"
        payload = {
            "user_id": __user__["id"],
            "lang": lang,
            "code": code,
            "timeout_s": timeout_s or self.valves.default_timeout_s,
        }
        try:
            res = await self._post("/exec", payload, payload["timeout_s"] + 30)
        except Exception as e:
            return f"ERROR: sandbox 実行に失敗: {e!r}"
        output_lines = await self._register_outputs(
            __user__["id"],
            res.get("outputs", []),
            __chat_id__,
            __message_id__,
            __event_emitter__,
        )
        return self._format_exec_result(res, output_lines)

    async def _exec_cell(
        self,
        code,
        timeout_s,
        __user__,
        __event_emitter__,
        __chat_id__,
        __message_id__,
    ) -> str:
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"
        if not __chat_id__:
            return (
                "ERROR: chat_id が取得できず状態付き実行ができません。"
                "単発実行が必要なら run_python_once を使ってください。"
            )
        payload = {
            "user_id": __user__["id"],
            "chat_id": __chat_id__,
            "code": code,
            "timeout_s": timeout_s or self.valves.default_timeout_s,
        }
        try:
            res = await self._post("/exec_cell", payload, payload["timeout_s"] + 30)
        except Exception as e:
            return f"ERROR: sandbox 実行に失敗: {e!r}"
        output_lines = await self._register_outputs(
            __user__["id"],
            res.get("outputs", []),
            __chat_id__,
            __message_id__,
            __event_emitter__,
        )
        return self._format_cell_result(res, output_lines)

    # =========================================================================
    # 実行系
    # =========================================================================
    async def run_python(
        self,
        code: Code("python"),
        timeout_s: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> Text:
        """
        サーバーの隔離環境で Python コードを実行する。この会話の中では変数・import・関数
        定義が実行を跨いで保持される (前のセルで作った DataFrame をそのまま使える)。
        最後の式は notebook のように値が表示される。/workspace は永続、
        /workspace/outputs/ に保存したファイルは自動でチャットに添付される。ネットワーク不可。

        :param code: 実行する Python コード
        :param timeout_s: このセルの wall clock 秒数 (省略時 120)
        """
        return await self._exec_cell(
            code,
            timeout_s,
            __user__,
            __event_emitter__,
            __chat_id__,
            __message_id__,
        )

    async def run_python_once(
        self,
        code: Code("python"),
        timeout_s: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> Text:
        """
        サーバーの隔離環境で Python コードを 1 回だけ実行する (状態を持たない使い捨て)。
        run_python と違い変数は残らないが、他の会話の状態から完全に独立して走らせたい
        単発処理向け。/workspace は永続、outputs/ は自動添付。ネットワーク不可。

        :param code: 実行する Python コード
        :param timeout_s: wall clock 秒数 (省略時 120)
        """
        return await self._exec(
            "python",
            code,
            timeout_s,
            __user__,
            __event_emitter__,
            __chat_id__,
            __message_id__,
        )

    async def run_shell(
        self,
        command: Code("bash"),
        timeout_s: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> Text:
        """
        サーバーの隔離環境で bash コマンドを実行する。coreutils, jq, ripgrep (rg),
        pandoc, python が利用可能。/workspace は run_python と共有で実行を跨いで永続。
        成果物は /workspace/outputs/ に置くと自動でチャットに添付される。ネットワーク不可。

        :param command: 実行する bash コマンド (スクリプト可)
        :param timeout_s: wall clock 秒数 (省略時 120)
        """
        return await self._exec(
            "shell",
            command,
            timeout_s,
            __user__,
            __event_emitter__,
            __chat_id__,
            __message_id__,
        )

    # =========================================================================
    # ファイル操作 utils
    # =========================================================================
    async def import_chat_file(
        self,
        filename: Optional[str] = None,
        file_id: Optional[str] = None,
        __user__: Optional[dict] = None,
        __files__: Optional[list] = None,
        __metadata__: Optional[dict] = None,
    ) -> str:
        """
        チャットの添付ファイルを sandbox の /workspace/inputs/ に取り込む。
        run_python / run_shell で処理する前に呼ぶこと。

        :param filename: 対象ファイル名 (部分一致)。file_id 未指定時に添付一覧から探す。省略時は全件。
        :param file_id: 取り込むファイルの file_id。large-file-gate 等で本文が context から
            外れた添付も、この file_id 指定なら直接取得できる (添付一覧に無くてもよい)。
        """
        if Files is None:
            return "ERROR: nucllei の内部 API を解決できません。"
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"

        if file_id:
            targets = [(file_id, None)]
        else:
            targets = [
                (fid, fname)
                for fid, fname in _iter_context_files(__files__, __metadata__)
                if not (
                    filename and (not fname or filename.lower() not in fname.lower())
                )
            ]

        report = []
        for fid, fname in targets:
            rec = Files.get_file_by_id(fid)
            if not rec or not rec.path:
                report.append(f"- {fname or fid}: レコード無し (skip)")
                continue
            # 所有者チェック: file_id 直指定で他ユーザーのファイルを取り込ませない
            owner = getattr(rec, "user_id", None)
            if owner and owner != __user__["id"]:
                report.append(f"- {fname or fid}: アクセス権が無い (skip)")
                continue
            try:
                local_path = Storage.get_file(rec.path)
                data = open(local_path, "rb").read()
                name = os.path.basename(fname or rec.filename)
                await self._post(
                    "/files/write",
                    {
                        "user_id": __user__["id"],
                        "path": f"inputs/{name}",
                        "b64": base64.b64encode(data).decode(),
                    },
                    timeout_s=120,
                )
                report.append(
                    f"- {name} ({_human_size(len(data))}) → /workspace/inputs/{name}"
                )
            except Exception as e:
                report.append(f"- {fname or fid}: 取り込み失敗 {e!r}")

        if not report:
            return "取り込み対象の添付ファイルがありませんでした。"
        return "取り込み結果:\n" + "\n".join(report)

    async def list_workspace(
        self,
        path: str = ".",
        __user__: Optional[dict] = None,
    ) -> str:
        """
        sandbox の /workspace 配下のファイル一覧を返す (実行を跨いで永続する領域)。

        :param path: 一覧するディレクトリ (workspace 相対、省略時はルート)
        """
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"
        try:
            res = await self._post(
                "/files/list",
                {
                    "user_id": __user__["id"],
                    "path": path,
                },
                timeout_s=30,
            )
        except Exception as e:
            return f"ERROR: sandbox に接続できません: {e!r}"
        entries = res.get("entries", [])
        if not entries:
            return f"/workspace/{path} は空です。"
        return "\n".join(f"- {e['path']} ({_human_size(e['size'])})" for e in entries)

    async def export_workspace_file(
        self,
        path: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """
        /workspace 配下の任意のファイルをチャットに添付してユーザーに渡す
        (outputs/ 以外に作ったファイルや過去実行の成果物を渡すときに使う)。

        :param path: 対象ファイル (workspace 相対、例: inputs/converted.xlsx)
        """
        if Files is None:
            return "ERROR: nucllei の内部 API を解決できません。"
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"
        try:
            res = await self._post(
                "/files/read",
                {
                    "user_id": __user__["id"],
                    "path": path,
                },
                timeout_s=120,
            )
        except Exception as e:
            return f"ERROR: 読み出しに失敗: {e!r}"
        try:
            att = _register_bytes_file(
                __user__["id"],
                os.path.basename(res["name"]),
                base64.b64decode(res["b64"]),
            )
        except Exception as e:
            return f"ERROR: OWUI Files への登録に失敗: {e!r}"
        await self._persist_and_emit_files(
            [att], __chat_id__, __message_id__, __event_emitter__
        )
        line = f"{path} ({_human_size(res['size'])}) をチャットに添付しました。file_id={att['id']}"
        if (att["file"]["meta"]["content_type"] or "").startswith("image/"):
            line += (
                f"\n画像なので本文に ![{res['name']}](/api/v1/files/{att['id']}/content)"
                f" と書くと表示される。"
            )
        return line
