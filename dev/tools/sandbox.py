"""
title: Sandbox
description: サーバーサイドの隔離環境 (nsjail) で Python / shell を実行する。
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

logger = logging.getLogger(__name__)

# 環境差は nucllei コンテナの env で吸収する (compose の環境ごとの上書きがそのまま効く)。
# import 時に解決して Field(default=) に渡すのは、default_factory だと valve の
# JSON schema から default が落ち、管理画面が空欄表示になって「今どこを向いているか」が
# 読めなくなるため (実測: default_factory の項目は spec で default=None)。
SANDBOX_URL = os.getenv("SANDBOX_URL", "http://sandbox:8000")

# write_file の本文上限。モデルの出力なのでそもそも巨大にはならないが、
# 事故 (ループで巨大文字列を渡す等) で workspace quota を食い潰さないための蓋。
WRITE_FILE_LIMIT = int(os.getenv("SANDBOX_WRITE_FILE_LIMIT_BYTES", str(1024 * 1024)))

# --- nucllei 内部 API（ランタイムで解決される） ------------------------------
try:
    from nucllei.models.chats import Chats
    from nucllei.models.files import Files, FileForm
    from nucllei.storage.provider import Storage
    from nucllei.utils.tool_render import Code
except Exception:  # ローカルでの静的解析用フォールバック
    Chats = None
    Files = None
    FileForm = None
    Storage = None

    def Code(language: str, base: type = str):
        return base


# #88: 画像出力を display チャネルで tool 枠下にユーザー表示する(モデルの本文貼り付けに
# 依存しない)。別 try に分けるのは、#88 未対応の旧 nucllei で display が無くても上の
# Files 等の import を巻き添えにしないため — その場合 None にフォールバックし、従来の
# 「本文に ![](...) と貼れ」指示へ退避する(デプロイ順が逆転しても画像に辿り着ける)。
try:
    from nucllei.utils.tool_output import display as _tool_display
except Exception:
    _tool_display = None


def _display_markdown(name: str, file_id: str, content_type: str) -> Optional[str]:
    """content_type から display チャネル用の markdown を返す(image/audio/video)。
    それ以外(DL 提供のみ)は None。<audio>/<video> は sanitizeChatHtml が許可済(#87)。
    exports/ に置いた成果物が「同じ経路」で自動表示に乗るための唯一の分岐点。"""
    url = f"/api/v1/files/{file_id}/content"
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return f"![{name}]({url})"
    if ct.startswith("audio/"):
        return f'<audio controls src="{url}"></audio>'
    if ct.startswith("video/"):
        return f'<video controls src="{url}"></video>'
    return None


# 描画注釈(#20 型駆動レンダリング): 実行時/LLM 的には素の str のまま、
# チャット UI でストリーミング中からハイライト付きコードブロックとして描画される。
PythonCode = Code("python")
ShellCommand = Code("bash")


# -----------------------------------------------------------------------------
# 低レベルヘルパ
# -----------------------------------------------------------------------------
def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024


def _attachment_dict(file_id: str, name: str, content_type: str, size: int) -> dict:
    """メッセージ files ストアに入れる添付オブジェクト。"""
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
    """bytes を Storage + Files に登録し、添付 dict を返す。"""
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
    """チャットコンテキストのファイル参照を正規化して列挙。"""
    raw = []
    if __files__:
        raw = __files__
    elif __metadata__ and __metadata__.get("files"):
        raw = __metadata__["files"]
    for f in raw or []:
        inner = f.get("file", {}) if isinstance(f, dict) else {}
        fid = (f.get("id") if isinstance(f, dict) else None) or inner.get("id")
        fname = (f.get("name") if isinstance(f, dict) else None) \
            or inner.get("filename") or (inner.get("meta") or {}).get("name")
        if fid:
            yield fid, fname


# -----------------------------------------------------------------------------
# Tool 本体
# -----------------------------------------------------------------------------
class Tools:
    class Valves(BaseModel):
        sandbox_url: str = Field(
            default=SANDBOX_URL,
            description="sandbox サービスの URL。既定は nucllei の env (SANDBOX_URL) を流用し、"
            "無ければ docker network 内の既定")
        default_timeout_s: int = Field(
            default=120, description="実行 wall clock 既定秒数 (サーバー上限で clamp)")
        max_output_chars: int = Field(
            default=10_000,
            description="stdout/stderr を tool result に載せる上限 (文字)。超過は head+tail 切り詰め。0 = 無効")
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

    # ---- exports/ の回収 → Files 登録 + チャット添付 ---------------------------
    async def _persist_and_emit_files(
        self, attachments, chat_id, message_id, __event_emitter__,
    ):
        """新規添付をメッセージに反映する。
        frontend は chat:message:files を message.files への「置換」として扱うため、
        DB 上の現在の files とマージしてから永続化 + emit する (差分だけ送ると
        同一メッセージ内の既存添付が消える)。"""
        merged = list(attachments)
        if Chats is not None and chat_id and message_id:
            try:
                cur = Chats.get_message_by_id_and_message_id(chat_id, message_id) or {}
                merged = list(cur.get("files") or []) + list(attachments)
                Chats.upsert_message_to_chat_by_id_and_message_id(
                    chat_id, message_id, {"files": merged},
                )
            except Exception as e:
                logger.error(f"[sandbox] persist failed: {e!r} (emit のみで継続)")
        if __event_emitter__:
            await __event_emitter__({
                "type": "chat:message:files",
                "data": {"files": merged},
            })

    async def _register_exports(
        self, user_id, exports, chat_id, message_id, __event_emitter__,
    ) -> list[str]:
        """sandbox の exports 差分を Files に登録・添付し、報告行を返す。"""
        lines = []
        attachments = []
        for out in exports:
            name = out["name"]
            if out.get("truncated") or "b64" not in out:
                # 転送上限(FILE_LIMIT、既定 25MB / compose 100MB)超過で未添付。
                # モデルにも戻り値で明示 + ユーザーにも display で警告(気付けるように)。
                warn = (
                    f"{name} ({_human_size(out['size'])}) は転送上限を超えて添付できなかった"
                    f"(workspace には残存)。分割・圧縮するか、必要な部分だけ書き出すこと。"
                )
                lines.append(f"- ⚠ {warn}")
                if _tool_display is not None:
                    _tool_display(f"⚠ {warn}")
                continue
            try:
                att = _register_bytes_file(
                    user_id, os.path.basename(name), base64.b64decode(out["b64"]))
                attachments.append(att)
                line = f"- {name} ({_human_size(out['size'])}) file_id={att['id']}"
                # #88: 画像/音声/動画は display チャネルで tool 枠下に自動表示(モデルの貼り付けに
                # 依存しない)。gallery 添付は _persist_and_emit_files で常時付くので display 未対応
                # nucllei でも辿り着ける → 本文への貼り付け指示は出さない(標準ガイダンスと矛盾させない)。
                md = _display_markdown(name, att["id"], att["file"]["meta"]["content_type"])
                if md:
                    if _tool_display is not None:
                        _tool_display(md)
                    line += " ユーザーに提示済み(本文への再掲・貼り付けは不要)"
                lines.append(line)
            except Exception as e:
                lines.append(f"- {name}: 登録失敗 {e!r}")
        if attachments:
            await self._persist_and_emit_files(
                attachments, chat_id, message_id, __event_emitter__)
        return lines

    def _truncate_output(self, text: str) -> str:
        """context 保護の切り詰め。sandbox サービス側の 512KB cap (transport/OOM 保護) とは
        別物で、こちらは tool result が会話 context を食い潰さないための上限。
        エラー/トレースバックは末尾に出るので tail を残す head+tail 方式。"""
        limit = self.valves.max_output_chars
        if limit <= 0 or len(text) <= limit:
            return text
        head = int(limit * 0.8)
        tail = limit - head
        return (
            text[:head]
            + f"\n⋮ [切り詰め: 全 {len(text)} 字中 先頭 {head} 字 + 末尾 {tail} 字のみ表示。"
            "全文が必要なら出力を /workspace のファイルに書き出し、"
            "run_shell の rg / head / tail で部分参照すること]\n"
            + text[-tail:]
        )

    def _format_exec_result(self, res: dict, output_lines: list[str]) -> str:
        parts = [f"exit_code={res['exit_code']} ({res['duration_s']}s)"]
        if res.get("timed_out"):
            parts[0] += " **タイムアウト (time_limit で強制終了)**"
        stdout = res.get("stdout", "")
        stderr = res.get("stderr", "")
        if stdout:
            parts.append("--- stdout ---\n" + self._truncate_output(stdout.rstrip("\n")))
            if res.get("stdout_truncated"):
                parts.append("(stdout はサーバー転送上限でも切り詰め)")
        if stderr:
            parts.append("--- stderr ---\n" + self._truncate_output(stderr.rstrip("\n")))
            if res.get("stderr_truncated"):
                parts.append("(stderr はサーバー転送上限でも切り詰め)")
        if not stdout and not stderr:
            parts.append("(出力なし)")
        if output_lines:
            parts.append("--- 生成ファイル (exports/) — チャットに添付済み ---\n"
                         + "\n".join(output_lines))
        return "\n".join(parts)

    def _format_cell_result(self, res: dict, output_lines: list[str]) -> str:
        parts = []
        if res.get("session_reset"):
            parts.append("※ 実行環境がリセットされ、以前の変数・import は失われています "
                         "(必要なら定義し直してから続けてください)。")
        stdout = res.get("stdout", "")
        error = res.get("error")
        if res.get("timed_out"):
            parts.append("**タイムアウト (セルを中断。変数・import は保持)**")
        if stdout:
            parts.append(self._truncate_output(stdout.rstrip("\n")))
            if res.get("stdout_truncated"):
                parts.append("(出力はサーバー転送上限でも切り詰め)")
        if error:
            parts.append("--- エラー ---\n" + self._truncate_output(str(error).rstrip("\n")))
        if not stdout and not error and not res.get("timed_out"):
            parts.append("(出力なし)")
        if output_lines:
            parts.append("--- 生成ファイル (exports/) — チャットに添付済み ---\n"
                         + "\n".join(output_lines))
        parts.append(f"({res.get('duration_s', 0)}s)")
        return "\n".join(parts)

    async def _exec(
        self, lang, code, timeout_s, __user__, __event_emitter__,
        __chat_id__, __message_id__,
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
            res = await self._post(
                "/exec", payload, payload["timeout_s"] + 30)
        except Exception as e:
            return f"ERROR: sandbox 実行に失敗: {e!r}"
        output_lines = await self._register_exports(
            __user__["id"], res.get("exports", []),
            __chat_id__, __message_id__, __event_emitter__)
        return self._format_exec_result(res, output_lines)

    async def _exec_cell(
        self, code, timeout_s, __user__, __event_emitter__,
        __chat_id__, __message_id__,
    ) -> str:
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"
        if not __chat_id__:
            return ("ERROR: chat_id が取得できず状態付き実行ができません。"
                    "単発実行が必要なら run_python_once を使ってください。")
        payload = {
            "user_id": __user__["id"],
            "chat_id": __chat_id__,
            "code": code,
            "timeout_s": timeout_s or self.valves.default_timeout_s,
        }
        try:
            res = await self._post(
                "/exec_cell", payload, payload["timeout_s"] + 30)
        except Exception as e:
            return f"ERROR: sandbox 実行に失敗: {e!r}"
        output_lines = await self._register_exports(
            __user__["id"], res.get("exports", []),
            __chat_id__, __message_id__, __event_emitter__)
        return self._format_cell_result(res, output_lines)

    # =========================================================================
    # 実行系
    # =========================================================================
    async def run_python(
        self,
        code: PythonCode,
        timeout_s: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """
        サーバーの隔離環境で Python コードを実行する。この会話の中では変数・import・関数
        定義が実行を跨いで保持される (前のセルで作った DataFrame をそのまま使える)。
        最後の式は notebook のように値が表示される。/workspace は永続、
        /workspace/exports/ に保存したファイルは自動でチャットに添付される。ネットワーク不可。

        :param code: 実行する Python コード
        :param timeout_s: このセルの wall clock 秒数 (省略時 120)
        """
        return await self._exec_cell(
            code, timeout_s, __user__, __event_emitter__,
            __chat_id__, __message_id__)

    async def run_python_once(
        self,
        code: PythonCode,
        timeout_s: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """
        サーバーの隔離環境で Python コードを 1 回だけ実行する (状態を持たない使い捨て)。
        run_python と違い変数は残らないが、他の会話の状態から完全に独立して走らせたい
        単発処理向け。/workspace は永続、exports/ は自動添付。ネットワーク不可。

        :param code: 実行する Python コード
        :param timeout_s: wall clock 秒数 (省略時 120)
        """
        return await self._exec(
            "python", code, timeout_s, __user__, __event_emitter__,
            __chat_id__, __message_id__)

    async def run_shell(
        self,
        command: ShellCommand,
        timeout_s: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """
        サーバーの隔離環境で bash コマンドを実行する。coreutils, jq, ripgrep (rg),
        pandoc, ffmpeg / ffprobe, python が利用可能。/workspace は run_python と共有で実行を跨いで永続。
        成果物は /workspace/exports/ に置くと自動でチャットに添付される。ネットワーク不可。

        :param command: 実行する bash コマンド (スクリプト可)
        :param timeout_s: wall clock 秒数 (省略時 120)
        """
        return await self._exec(
            "shell", command, timeout_s, __user__, __event_emitter__,
            __chat_id__, __message_id__)

    # =========================================================================
    # ファイル操作 utils
    # =========================================================================
    async def send_to_workspace(
        self,
        filename: Optional[str] = None,
        file_id: Optional[str] = None,
        __user__: Optional[dict] = None,
        __files__: Optional[list] = None,
        __metadata__: Optional[dict] = None,
    ) -> str:
        """
        チャットの添付ファイルを sandbox の /workspace/imports/ にコピーする。
        **sandbox 内 (run_python / run_shell) で中身を扱う時だけ**呼ぶこと。
        他のツールは file_id から直接読むので、その前段としては不要
        (このコピーはファイルを「読めるようにする」汎用手段ではない)。

        :param filename: 対象ファイル名 (部分一致)。file_id 未指定時に添付一覧から探す。省略時は全件。
        :param file_id: 取り込むファイルの file_id。添付一覧に出ていないファイルでも
            file_id が分かっていれば取り込める。
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
                if not (filename and (not fname or filename.lower() not in fname.lower()))
            ]

        report = []
        for fid, fname in targets:
            rec = Files.get_file_by_id(fid)
            if not rec or not rec.path:
                report.append(f"- {fname or fid}: レコード無し (skip)")
                continue
            # 所有者チェック: file_id 直指定で他ユーザーのファイルを取り込ませない
            owner = getattr(rec, "user_id", None)
            if owner != __user__["id"]:
                report.append(f"- {fname or fid}: アクセス権が無い (skip)")
                continue
            try:
                local_path = Storage.get_file(rec.path)
                data = open(local_path, "rb").read()
                name = os.path.basename(fname or rec.filename)
                await self._post("/files/write", {
                    "user_id": __user__["id"],
                    "path": f"imports/{name}",
                    "b64": base64.b64encode(data).decode(),
                }, timeout_s=120)
                report.append(f"- {name} ({_human_size(len(data))}) → /workspace/imports/{name}")
            except Exception as e:
                report.append(f"- {fname or fid}: 取り込み失敗 {e!r}")

        if not report:
            return "取り込み対象の添付ファイルがありませんでした。"
        return "取り込み結果:\n" + "\n".join(report)

    async def write_file(
        self,
        path: str,
        content: str,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        テキストファイルを sandbox の /workspace に直接書く。HTML・Markdown・CSV・
        テンプレート・設定ファイルなど、本文をそのまま渡したいときに使う。
        run_python の文字列や run_shell のヒアドキュメントに本文を埋め込むと
        クォート・エスケープの事故が起きるので、**ファイルを作るだけならこちらを使う**。
        既存ファイルは上書きする。テキスト専用 (バイナリは扱えない)。

        :param path: 書き込み先 (workspace 相対。例: report.html, work/index.html)
        :param content: ファイルの中身 (テキストそのまま)
        """
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"

        rel = (path or "").strip().lstrip("/")
        if not rel:
            return "ERROR: path が空です。"
        if any(part in ("..", "") for part in rel.split("/")):
            return f"ERROR: 不正な path です: {path!r}"

        data = content.encode("utf-8")
        if len(data) > WRITE_FILE_LIMIT:
            return (
                f"ERROR: 内容が大きすぎます ({_human_size(len(data))} > "
                f"{_human_size(WRITE_FILE_LIMIT)})。分割して複数回に分けるか、"
                "run_python で生成してください。"
            )

        try:
            await self._post("/files/write", {
                "user_id": __user__["id"],
                "path": rel,
                "b64": base64.b64encode(data).decode(),
            }, timeout_s=60)
        except Exception as e:
            return f"ERROR: sandbox に接続できません: {e!r}"

        msg = f"/workspace/{rel} に書き込みました ({_human_size(len(data))})。"
        if rel.startswith("exports/"):
            # exports/ の自動添付は「実行の後始末」で回収する仕組みなので、
            # 実行を伴わない write_file では発火しない (取りこぼしの typical case)
            msg += (
                " なお exports/ への自動添付は run_python / run_shell の実行後に"
                "回収する仕組みのため、この書き込みだけでは添付されません。"
                "ユーザーに渡すなら fetch_from_workspace を呼んでください。"
            )
        return msg

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
            res = await self._post("/files/list", {
                "user_id": __user__["id"], "path": path,
            }, timeout_s=30)
        except Exception as e:
            return f"ERROR: sandbox に接続できません: {e!r}"
        entries = res.get("entries", [])
        if not entries:
            return f"/workspace/{path} は空です。"
        return "\n".join(
            f"- {e['path']} ({_human_size(e['size'])})" for e in entries)

    async def fetch_from_workspace(
        self,
        path: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """
        /workspace 配下の任意のファイルをチャットに添付してユーザーに渡す
        (exports/ 以外に作ったファイルや過去実行の成果物を渡すときに使う)。

        :param path: 対象ファイル (workspace 相対、例: imports/converted.xlsx)
        """
        if Files is None:
            return "ERROR: nucllei の内部 API を解決できません。"
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"
        try:
            res = await self._post("/files/read", {
                "user_id": __user__["id"], "path": path,
            }, timeout_s=120)
        except Exception as e:
            return f"ERROR: 読み出しに失敗: {e!r}"
        try:
            att = _register_bytes_file(
                __user__["id"], os.path.basename(res["name"]),
                base64.b64decode(res["b64"]))
        except Exception as e:
            return f"ERROR: Files への登録に失敗: {e!r}"
        await self._persist_and_emit_files(
            [att], __chat_id__, __message_id__, __event_emitter__)
        line = f"{path} ({_human_size(res['size'])}) をチャットに添付しました。file_id={att['id']}"
        # #88: 画像/音声/動画は display で枠下に自動表示(exports/ 自動回収と同じ経路)。
        # gallery 添付は常時付くので未対応 nucllei でも辿り着ける → 貼り付け指示は出さない。
        md = _display_markdown(res["name"], att["id"], att["file"]["meta"]["content_type"])
        if md:
            if _tool_display is not None:
                _tool_display(md)
            line += "\nユーザーに提示済み(本文への再掲・貼り付けは不要)。"
        return line
