"""組み込みツール: サーバーサイドの隔離環境 (nsjail) で Python / shell を実行する。

配備層 (prism-hu/llens) のカスタム tool から本体へ移設した (2026-09-03)。判断の根拠は
**依存の向き**: この tool は `nucllei.models.chats` / `nucllei.models.files` /
`nucllei.storage.provider` / `nucllei.utils.tool_render` / `nucllei.utils.tool_output`
という本体の内部 API を掴んでいて、配備側に置く限り「相手の nucllei がその API を
持っているか」を try/except で交渉し続けるしかなかった。builtin なら nucllei と同じ
tag で出るので交渉が要らない (`_memos/builtin-tools.md` 1c: 境界基準は**出荷単位**)。
LLENS 固有の知識はこのファイルに一つも無い (同ディレクトリにあった cage.py /
medical_calculator.py とは質が違う) ことも移設の条件を満たしていた。

**他の builtin と違い外部サービス (sandbox コンテナ) を要する**ので、向き先 env
(`SANDBOX_URL`) が設定されている配備でだけ登録する (ゲートは `__init__.py` 側)。
既定値を持たせない = 未設定なら tool 一覧にすら現れないのが要点 — sandbox の居ない
配備で「一覧に生えているのに呼ぶと必ずエラー」は builtin の性質に反する。
共有トークン (`SANDBOX_TOKEN`、#132) は**ゲートに含めない** — sandbox 側の認証は
opt-in で、トークンが無ければ両側とも認証なしで従来どおり繋がるため。
"""

import asyncio
import base64
import io
import ipaddress
import logging
import mimetypes
import os
import re
import uuid
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import unquote, urljoin, urlsplit

from nucllei.utils.ext_upstreams import match_public_base

import aiohttp
from pydantic import BaseModel, Field

from nucllei.models.chats import Chats
from nucllei.models.files import FileForm, Files
from nucllei.storage.provider import Storage
from nucllei.utils.tool_output import display
from nucllei.utils.tool_render import Code

log = logging.getLogger(__name__)

# 環境差は nucllei コンテナの env で吸収する (compose の環境ごとの上書きがそのまま効く)。
#
# **既定値は持たない**: 空 = sandbox の居ない配備、として `__init__.py` が登録を止める。
# 既定 URL を置くと全配備で常に有効になり、ゲートが意味を失う。
#
# **valve を持たない (#114 の帰結、2026-09-05)**: sandbox は nucllei の出荷物になり
# compose で必ず一緒に立つので、admin が実行時に向き先を差し替える正当な用途が消えた。
# 副次的に、DB (valve) 経由で宛先を外部へ向けて env の `SANDBOX_TOKEN` を
# `Authorization: Bearer` ごと回収する経路も構造的に無くなる — 「env と一致するときだけ
# ヘッダを付ける」条件分岐を足すより素直で、設定も 1 つ減る。
SANDBOX_URL = os.getenv("SANDBOX_URL", "").strip()

# sandbox API の共有トークン (#132)。**opt-in** — 設定すれば `sandbox_post` が
# `Authorization: Bearer` を付け、sandbox 側も認証を強制する。未設定なら両側とも
# 認証なしで従来どおり動く (ヘッダ自体を付けない)。両側 (sandbox コンテナと nucllei) で
# 同名・同値を持ち合うこと。**valve / DB には持たせない** — 秘密の置き場を DB に
# 増やさない、という既存方針 (llens の compose が CAGE_TOKEN を env にしているのと同じ理由)。
SANDBOX_TOKEN = os.getenv("SANDBOX_TOKEN", "").strip()


async def sandbox_post(path: str, payload: dict, timeout_s: float) -> dict:
    """sandbox サービスへの POST。**トークンがあるときだけ**共有トークンを付ける (#132)。

    module 関数なのは、この tool 以外にも sandbox を読む側がいるから (#139:
    `utils/image_refs.py` が `/workspace/...` 参照を `/files/read` で解決する)。
    sandbox への HTTP 出口をここ 1 本にして、トークンの付け方を 2 箇所に持たない。

    宛先は module 定数 `SANDBOX_URL` (env) のみ — DB (valve) からは変えられない。
    header は session に持たせる (この session はこの 1 リクエストにしか使わない)。
    未設定なら header 自体を付けない — 空の `Bearer ` を送らない (sandbox 側の
    認証が有効な配備では、無ヘッダも空トークンも等しく 401 なので実害は無いが、
    「無いものを送る」経路を作らない)。`Tools._http_get` は別 session — 任意 URL を
    取りに行く経路なので、そちらへトークンが漏れないよう混ぜない。
    """
    url = SANDBOX_URL.rstrip("/") + path
    headers = {"Authorization": f"Bearer {SANDBOX_TOKEN}"} if SANDBOX_TOKEN else {}
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout_s),
        headers=headers,
    ) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = (await resp.text())[:300]
                if resp.status == 401:
                    # 値は出さない。sandbox 側の認証が有効なのに nucllei 側の値が
                    # 違う / 無い、のどちらか = 「両側の SANDBOX_TOKEN 不一致」で
                    # まとめて説明がつく (未設定は「不一致」の一種)
                    raise RuntimeError(
                        "sandbox HTTP 401: 認証に失敗しました。nucllei と sandbox の "
                        "SANDBOX_TOKEN が一致していません "
                        "(片側だけ未設定の場合も含む。配備の env を確認)"
                    )
                raise RuntimeError(f"sandbox HTTP {resp.status}: {body}")
            return await resp.json()


# send_to_workspace(url=) の相対パス解決先 = nucllei 自分自身の listen 口 (コンテナ内)。
# Caddy / 公開 origin には依存しない。
SELF_BASE_URL = os.getenv("SELF_BASE_URL", "http://127.0.0.1:8080")

# write_file の本文上限。モデルの出力なのでそもそも巨大にはならないが、
# 事故 (ループで巨大文字列を渡す等) で workspace quota を食い潰さないための蓋。
WRITE_FILE_LIMIT = int(os.getenv("SANDBOX_WRITE_FILE_LIMIT_BYTES", str(1024 * 1024)))

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class _TooLarge(Exception):
    """_http_get のサイズ上限超過。aiohttp の InvalidURL (ValueError 系) と区別する番兵。"""


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


def _safe_import_name(name: str) -> str:
    """imports/ に置くファイル名へ正規化する (パス成分・空・. .. は落とす)。"""
    name = os.path.basename((name or "").strip())
    return "" if name in ("", ".", "..") else name


def _derive_name(headers: dict, url_path: str) -> str:
    """取得結果のファイル名を Content-Disposition → URL の basename → download で導出。"""
    m = re.search(r'filename="?([^";]+)"?', headers.get("content-disposition", ""))
    name = _safe_import_name(unquote(m.group(1))) if m else ""
    if not name:
        name = _safe_import_name(unquote(os.path.basename(url_path)))
    return name or "download"


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
    """ネットワーク不可。/workspace は run_python / run_python_once / run_shell の
    3 ツール共有で実行を跨いで永続し、/workspace/exports/ に保存したファイルは
    自動でチャットに添付される。実行は直列 — run_python は同じ会話内で、
    run_python_once / run_shell は同一ユーザー内で順番待ちになる (background に
    しても並列にはならない。長い background 実行の裏で同系統を呼ぶと完了まで待たされ、
    待ちが長すぎると失敗する)。"""

    # ↑ クラス docstring は nucllei の toolkit 共通注記 — 先頭 spec (run_python) の
    # description に 1 回だけ前置され、モデルに見える。3 実行ツールの docstring に
    # 複製していた共通事項の一本化 (2026-08-16)。開発者メモはこの # コメント側に書く。

    # #110: 非同期実行(background 昇格)への opt-in。時間のかかる Python 実行を
    # background にできるようにする。
    # 既知の v1 制約: ターン終了後に完了した実行では exports の添付 UI
    # (chat:message:files)は出ない — 実体は Files に永続済みで、tool result
    # 文字列にも列挙される(`_memos/async-exec-design.md` §6)。
    backgroundable = ("run_python", "run_python_once", "run_shell")

    # #110 P4: per-entry timeout(`timeouts` クラス属性)。sandbox サービスは
    # timeout_s を最大 600 秒まで受ける(SANDBOX_TIMEOUT_MAX_S)のに、宣言が無いと
    # 同期経路は CHAT_TOOL_EXECUTION_TIMEOUT=300 で切られ、timeout_s=300〜600 の
    # 正当な指定が harness 側で失われる(検査 2026-08-16)。
    # 600 + HTTP 余裕 30 = _post の付け足しと同じ余裕幅。
    timeouts = {"run_python": 630, "run_python_once": 630, "run_shell": 630}

    class Valves(BaseModel):
        # 向き先 (SANDBOX_URL) と共有トークン (SANDBOX_TOKEN) は **env 専用** —
        # valve に置かない。理由はモジュール冒頭の SANDBOX_URL のコメント。
        default_timeout_s: int = Field(
            default=120, description="実行 wall clock 既定秒数 (サーバー上限で clamp)"
        )
        max_output_chars: int = Field(
            default=10_000,
            description="stdout/stderr を tool result に載せる上限 (文字)。超過は head+tail 切り詰め。0 = 無効",
        )
        url_blocked_cidrs: str = Field(
            default="127.0.0.0/8,::1/128,0.0.0.0/8,::/128,169.254.0.0/16,172.16.0.0/12",
            description="send_to_workspace(url=) の絶対 URL 取得で拒否する宛先 CIDR (カンマ区切り)。"
            "既定は loopback / unspecified (0.0.0.0, :: は自ホストに落ちる) / link-local / "
            "docker 帯 (compose 内部サービス)",
        )
        url_max_mb: float = Field(
            default=50.0,
            description="send_to_workspace(url=) で取得する 1 ファイルの上限 MB",
        )
        debug: bool = Field(default=False, description="詳細ログ")

    def __init__(self):
        self.valves = self.Valves()

    # ---- HTTP -----------------------------------------------------------------
    async def _post(self, path: str, payload: dict, timeout_s: float) -> dict:
        """sandbox サービスへの POST(実体は module の `sandbox_post`)。

        メソッドとして残すのは、テストが tool インスタンスの `_post` を差し替えて
        HTTP を切り離す (実コンテナに依存させない) 口だから。
        """
        return await sandbox_post(path, payload, timeout_s)

    # ---- exports/ の回収 → Files 登録 + チャット添付 ---------------------------
    async def _persist_and_emit_files(
        self,
        attachments,
        chat_id,
        message_id,
        __event_emitter__,
    ):
        """新規添付をメッセージに反映する。
        frontend は chat:message:files を message.files への「置換」として扱うため、
        DB 上の現在の files とマージしてから永続化 + emit する (差分だけ送ると
        同一メッセージ内の既存添付が消える)。"""
        merged = list(attachments)
        if chat_id and message_id:
            try:
                cur = Chats.get_message_by_id_and_message_id(chat_id, message_id) or {}
                merged = list(cur.get("files") or []) + list(attachments)
                Chats.upsert_message_to_chat_by_id_and_message_id(
                    chat_id,
                    message_id,
                    {"files": merged},
                )
            except Exception as e:
                log.error(f"[sandbox] persist failed: {e!r} (emit のみで継続)")
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "chat:message:files",
                    "data": {"files": merged},
                }
            )

    async def _register_exports(
        self,
        user_id,
        exports,
        chat_id,
        message_id,
        __event_emitter__,
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
                display(f"⚠ {warn}")
                continue
            try:
                att = _register_bytes_file(
                    user_id, os.path.basename(name), base64.b64decode(out["b64"])
                )
                attachments.append(att)
                line = f"- {name} ({_human_size(out['size'])}) file_id={att['id']}"
                # #88: 画像/音声/動画は display チャネルで tool 枠下に自動表示(モデルの
                # 貼り付けに依存しない)。gallery 添付は _persist_and_emit_files で常時付く。
                md = _display_markdown(
                    name, att["id"], att["file"]["meta"]["content_type"]
                )
                if md:
                    display(md)
                    line += " ユーザーに提示済み — 本文には貼らない ([task] 通知で受け取った場合のみ貼る)"
                lines.append(line)
            except Exception as e:
                lines.append(f"- {name}: 登録失敗 {e!r}")
        if attachments:
            await self._persist_and_emit_files(
                attachments, chat_id, message_id, __event_emitter__
            )
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
            "run_shell の rg / head / tail で部分参照すること]\n" + text[-tail:]
        )

    def _format_exec_result(self, res: dict, output_lines: list[str]) -> str:
        parts = [f"exit_code={res['exit_code']} ({res['duration_s']}s)"]
        if res.get("timed_out"):
            parts[0] += " **タイムアウト (time_limit で強制終了)**"
        stdout = res.get("stdout", "")
        stderr = res.get("stderr", "")
        if stdout:
            parts.append(
                "--- stdout ---\n" + self._truncate_output(stdout.rstrip("\n"))
            )
            if res.get("stdout_truncated"):
                parts.append("(stdout はサーバー転送上限でも切り詰め)")
        if stderr:
            parts.append(
                "--- stderr ---\n" + self._truncate_output(stderr.rstrip("\n"))
            )
            if res.get("stderr_truncated"):
                parts.append("(stderr はサーバー転送上限でも切り詰め)")
        if not stdout and not stderr:
            parts.append("(出力なし)")
        if output_lines:
            parts.append(
                "--- 生成ファイル (exports/) — チャットに添付済み ---\n"
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
            parts.append(self._truncate_output(stdout.rstrip("\n")))
            if res.get("stdout_truncated"):
                parts.append("(出力はサーバー転送上限でも切り詰め)")
        if error:
            parts.append(
                "--- エラー ---\n" + self._truncate_output(str(error).rstrip("\n"))
            )
        if not stdout and not error and not res.get("timed_out"):
            parts.append("(出力なし)")
        if output_lines:
            parts.append(
                "--- 生成ファイル (exports/) — チャットに添付済み ---\n"
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
        output_lines = await self._register_exports(
            __user__["id"],
            res.get("exports", []),
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
        output_lines = await self._register_exports(
            __user__["id"],
            res.get("exports", []),
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
        最後の式は notebook のように値が表示される。

        :param code: 実行する Python コード
        :param timeout_s: このセルの wall clock 秒数 (省略時 120、上限 600)
        """
        return await self._exec_cell(
            code, timeout_s, __user__, __event_emitter__, __chat_id__, __message_id__
        )

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
        単発処理向け。

        :param code: 実行する Python コード
        :param timeout_s: wall clock 秒数 (省略時 120、上限 600)
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
        command: ShellCommand,
        timeout_s: Optional[int] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """
        サーバーの隔離環境で bash コマンドを実行する。coreutils, jq, ripgrep (rg),
        pandoc, weasyprint, unzip / zip, nkf, file, poppler-utils (pdftotext / pdfinfo),
        ffmpeg / ffprobe, python が利用可能。

        :param command: 実行する bash コマンド (スクリプト可)
        :param timeout_s: wall clock 秒数 (省略時 120、上限 600)
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
    async def send_to_workspace(
        self,
        filename: Optional[str] = None,
        file_id: Optional[str] = None,
        url: Optional[str] = None,
        __user__: Optional[dict] = None,
        __files__: Optional[list] = None,
        __metadata__: Optional[dict] = None,
        __request__: Optional[Any] = None,
    ) -> str:
        """
        チャットの添付ファイルや URL で参照できるリソースを sandbox の
        /workspace/imports/ にコピーする。
        **sandbox 内 (run_python / run_shell) で中身を扱う時だけ**呼ぶこと。
        他のツールは file_id から直接読むので、その前段としては不要
        (このコピーはファイルを「読めるようにする」汎用手段ではない)。

        :param filename: 対象ファイル名 (部分一致)。file_id 未指定時に添付一覧から探す。省略時は全件。
        :param file_id: 取り込むファイルの file_id。添付一覧に出ていないファイルでも
            file_id が分かっていれば取り込める。
        :param url: 取り込むリソースの URL。file_id/filename と排他。受けられる形は 2 つ:
            完全な http(s) URL (文書に出る https://…/ext/... の表示 URL や院内 web の
            原本 URL を literal のまま渡す) と、"/" 始まりの同一オリジン相対パス
            (/api/v1/files/{id}/content)。
            jail はネットワーク不可なので、URL を run_shell で curl する代わりにこれで取り込む。
        """
        if not __user__ or not __user__.get("id"):
            return "ERROR: ユーザー情報が取得できませんでした。"

        if url:
            if file_id or filename:
                return (
                    "ERROR: url は file_id/filename と同時指定できない。"
                    "どちらか一方だけを指定すること。"
                )
            return await self._import_url(url.strip(), __user__["id"], __request__)

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
            if owner != __user__["id"]:
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
                        "path": f"imports/{name}",
                        "b64": base64.b64encode(data).decode(),
                    },
                    timeout_s=120,
                )
                report.append(
                    f"- {name} ({_human_size(len(data))}) → /workspace/imports/{name}"
                )
            except Exception as e:
                report.append(f"- {fname or fid}: 取り込み失敗 {e!r}")

        if not report:
            return "取り込み対象の添付ファイルがありませんでした。"
        return "取り込み結果:\n" + "\n".join(report)

    # ---- send_to_workspace の url ソース ---------------------------------------
    async def _import_url(self, u: str, user_id: str, request) -> str:
        """url を取得して imports/ へ書く。

        参照の名前空間は image_refs と同じ (README §3.8): URL はスキーム付き、"/" 始まりは
        sandbox の fs。

        - "/api/v1/files/{id}/content" (添付の同一オリジン相対パス)、および path が
          /ext/{name}/... の完全 URL (host を問わない — 自オリジンの表示 URL) →
          自分自身 (SELF_BASE_URL) に、呼び出し元リクエストの認証ヘッダをそのまま付けて
          GET — ブラウザがチャット画面で解決するのと同じ意味論。/ext/ の session 認証も
          /api/v1/files/... の所有 ACL もサーバ側ルートがそのまま効くので、tool 側は
          経路ごとの検査を持たない。宛先が自分固定なので CIDR ガードは通さない。
          パスはモデル制御下だが、本人の権限で本人が到達できるルートを GET できるだけ
          (= ブラウザ相当) という受容 — 安全の根拠は route 側の認証・ACL であって
          宛先固定ではない
        - EXT_PUBLIC_BASES 配下の完全 URL (院内 web の原本 URL) → 原本 host へは行かず、
          その name の "/ext/{name}/{rel}" として上と同じ self fetch (image_refs が
          backend 経由で解決するのと同じ向き)
        - それ以外の完全な http(s) URL → 院内 LAN fetch (_fetch_lan の宛先ガード付き。
          認証ヘッダは付けない — session を外へ持ち出さない)
        - 相対 "/ext/..." は受けない (URL は絶対 URL で渡す)。"/workspace/..." "/mnt/..."
          は sandbox に既に見えているので取り込む必要が無い (ERROR で案内)
        """
        max_bytes = int(self.valves.url_max_mb * 1024 * 1024)
        if u.startswith("/"):
            if u.startswith("/api/v1/files/"):
                got = await self._fetch_self(u, max_bytes, request)
            elif u.startswith("/ext/"):
                return (
                    "ERROR: /ext/... は URL であって fs パスではない。"
                    "絶対 URL (https://<host>/ext/...) で渡すこと。"
                )
            elif u.startswith(("/workspace/", "/mnt/")):
                return (
                    f"ERROR: {u} は sandbox から既に見えている (取り込み不要)。"
                    "run_python / run_shell からそのまま読むこと。"
                )
            else:
                return (
                    "ERROR: / 始まりで受けるのは /api/v1/files/{id}/content だけ "
                    "(URL は絶対 URL で、sandbox のファイルは /workspace/ か /mnt/ で)。"
                )
        elif u.lower().startswith(("http://", "https://")):
            try:
                parts = urlsplit(u)
            except ValueError as e:
                return f"ERROR: URL を解釈できない ({e})。"
            query = f"?{parts.query}" if parts.query else ""
            hit = match_public_base(u)
            if parts.path.startswith("/ext/"):
                # 自オリジンの表示 URL — 相対パスと同じ self fetch (認証ヘッダ付き)
                got = await self._fetch_self(parts.path + query, max_bytes, request)
            elif hit is not None:
                # 原本 URL → その name の /ext/ に寄せる (原本 host へ直接は行かない)
                name, rel = hit
                rel = rel.split("#", 1)[0]
                got = await self._fetch_self(f"/ext/{name}/{rel}", max_bytes, request)
            else:
                got = await self._fetch_lan(u, max_bytes)
        else:
            return (
                "ERROR: url は完全な http(s) URL か "
                "/api/v1/files/{id}/content の相対パスで渡すこと。"
            )

        if isinstance(got, str):
            return got
        name, data = got
        try:
            await self._post(
                "/files/write",
                {
                    "user_id": user_id,
                    "path": f"imports/{name}",
                    "b64": base64.b64encode(data).decode(),
                },
                timeout_s=120,
            )
        except Exception as e:
            return f"ERROR: sandbox へ書き込めません: {e!r}"
        return (
            "取り込み結果:\n"
            f"- {name} ({_human_size(len(data))}) → /workspace/imports/{name}"
        )

    async def _fetch_self(self, path: str, max_bytes: int, request):
        """相対パスを自分自身から取得する。(filename, bytes) か "ERROR: ..." 文字列。

        redirect は追わない — 認証ヘッダ付きの要求を他所へ持ち出さないための一線
        (自分の GET ルートは今のところ redirect を返さないので実害もない)。
        """
        headers = {}
        for k in ("cookie", "authorization"):
            try:
                v = request.headers.get(k) if request is not None else None
            except Exception:
                v = None
            if v:
                headers[k] = v
        target = SELF_BASE_URL.rstrip("/") + path
        try:
            status, resp_headers, data = await self._http_get(
                target, max_bytes, headers=headers
            )
        except _TooLarge:
            return f"ERROR: 大きすぎる (> {self.valves.url_max_mb:g}MB)。"
        except Exception as e:
            log.error(f"[send_to_workspace] self fetch failed ({path}): {e!r}")
            return f"ERROR: 取得できない ({e})。"
        if status in _REDIRECT_STATUSES:
            return f"ERROR: redirect は追わない (status={status})。"
        if not 200 <= status < 300:
            return f"ERROR: 取得できない (status={status})。"
        return _derive_name(resp_headers, urlsplit(path).path), data

    async def _fetch_lan(self, target: str, max_bytes: int):
        """院内の絶対 URL を GET する。(filename, bytes) か "ERROR: ..." 文字列。

        宛先ガード: イントラは LAN 内全開放の前提で許可し、url_blocked_cidrs
        (loopback / link-local / docker 帯 = compose 内部サービス) だけ拒否する。
        redirect は各 hop で宛先を再検査しつつ 3 回まで追う。解決→接続間の再解決
        (DNS rebinding) は院内閉域の脅威モデルでは受容。
        """
        current = target
        for _hop in range(4):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https"):
                return f"ERROR: http/https のみ取得できる ({parts.scheme or '不明'})。"
            if not parts.hostname:
                return "ERROR: ホスト名が無い。"
            err = await self._blocked_ip_error(parts.hostname)
            if err:
                return err
            try:
                status, headers, data = await self._http_get(current, max_bytes)
            except _TooLarge:
                return f"ERROR: 大きすぎる (> {self.valves.url_max_mb:g}MB)。"
            except Exception as e:
                log.error(f"[send_to_workspace] fetch failed ({current}): {e!r}")
                return f"ERROR: 取得できない ({e})。"
            if status in _REDIRECT_STATUSES:
                loc = headers.get("location")
                if not loc:
                    return f"ERROR: redirect (status={status}) に Location が無い。"
                current = urljoin(current, loc)
                continue
            if not 200 <= status < 300:
                return f"ERROR: 取得できない (status={status})。"
            return _derive_name(headers, parts.path), data
        return "ERROR: redirect が多すぎる。"

    async def _blocked_ip_error(self, host: str) -> Optional[str]:
        """host の解決先が url_blocked_cidrs に入っていればエラー文字列を返す。"""
        blocked = []
        for c in self.valves.url_blocked_cidrs.split(","):
            c = c.strip()
            if not c:
                continue
            try:
                blocked.append(ipaddress.ip_network(c))
            except ValueError:
                log.warning(f"[send_to_workspace] invalid blocked cidr: {c!r}")
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError as e:
            return f"ERROR: ホスト名を解決できない ({host}: {e})。"
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            # IPv4-mapped IPv6 (::ffff:127.0.0.1 等) は v4 に正規化してから照合する —
            # version 一致ゲートがあるため、正規化しないと v4 の拒否レンジを素通りする
            mapped = getattr(ip, "ipv4_mapped", None)
            if mapped is not None:
                ip = mapped
            for net in blocked:
                if ip.version == net.version and ip in net:
                    return f"ERROR: 宛先 {host} ({ip}) は拒否レンジ ({net}) 内。"
        return None

    async def _http_get(self, target: str, max_bytes: int, headers: dict = None):
        """1 hop の GET。(status, 小文字キーの headers, bytes) を返す。

        redirect ステータスは本文を読まずに返す (追うかは呼び出し側)。
        上限超過は _TooLarge。
        """
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            async with session.get(
                target, allow_redirects=False, headers=headers or {}
            ) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                if resp.status in _REDIRECT_STATUSES:
                    return resp.status, headers, b""
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise _TooLarge()
                    chunks.append(chunk)
                return resp.status, headers, b"".join(chunks)

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
            await self._post(
                "/files/write",
                {
                    "user_id": __user__["id"],
                    "path": rel,
                    "b64": base64.b64encode(data).decode(),
                },
                timeout_s=60,
            )
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
            return f"ERROR: Files への登録に失敗: {e!r}"
        await self._persist_and_emit_files(
            [att], __chat_id__, __message_id__, __event_emitter__
        )
        line = (
            f"{path} ({_human_size(res['size'])}) をチャットに添付しました。"
            f"file_id={att['id']}"
        )
        # #88: 画像/音声/動画は display で枠下に自動表示(exports/ 自動回収と同じ経路)。
        md = _display_markdown(
            res["name"], att["id"], att["file"]["meta"]["content_type"]
        )
        if md:
            display(md)
            line += "\nユーザーに提示済み(本文への再掲・貼り付けは不要)。"
        return line
