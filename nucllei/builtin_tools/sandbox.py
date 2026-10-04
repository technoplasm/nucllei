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

import base64
import io
import logging
import mimetypes
import os
import posixpath
import uuid
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import unquote

import aiohttp
from pydantic import BaseModel, Field

from nucllei.env import SANDBOX_TOKEN, SANDBOX_URL, SANDBOX_WRITE_FILE_LIMIT_BYTES
from nucllei.models.chats import Chats
from nucllei.models.files import FileForm, Files
from nucllei.storage.provider import Storage
from nucllei.utils.tool_errors import (
    ToolCallError,
    ToolError,
    ToolErrorKind,
    ToolFailure,
    ToolFatal,
    ToolOutcome,
)
from nucllei.utils.tool_output import display, mark_error_kind
from nucllei.utils.tool_render import Code

log = logging.getLogger(__name__)

# 向き先 (`SANDBOX_URL`) と共有トークン (`SANDBOX_TOKEN`) は env 専用で、読み取りは
# `nucllei/env.py` に集約 (2026-10-03)。ここでは module 属性として持ち直す —
# `__init__.py` のゲートと `utils/image_refs.py` が `sandbox.SANDBOX_URL` を見る。
#
# **既定値は持たない**: 空 = sandbox の居ない配備、として `__init__.py` が登録を止める。
# 既定 URL を置くと全配備で常に有効になり、ゲートが意味を失う。
#
# **valve を持たない (#114 の帰結、2026-09-05)**: sandbox は nucllei の出荷物になり
# compose で必ず一緒に立つので、admin が実行時に向き先を差し替える正当な用途が消えた。
# 副次的に、DB (valve) 経由で宛先を外部へ向けて env の `SANDBOX_TOKEN` を
# `Authorization: Bearer` ごと回収する経路も構造的に無くなる — 「env と一致するときだけ
# ヘッダを付ける」条件分岐を足すより素直で、設定も 1 つ減る。
#
# 共有トークン (#132) は **opt-in** — 設定すれば `sandbox_post` が
# `Authorization: Bearer` を付け、sandbox 側も認証を強制する。未設定なら両側とも
# 認証なしで従来どおり動く (ヘッダ自体を付けない)。**valve / DB には持たせない** —
# 秘密の置き場を DB に増やさない、という既存方針 (llens の compose が CAGE_TOKEN を
# env にしているのと同じ理由)。


class SandboxHTTPError(RuntimeError):
    """sandbox サービスが 200 以外を返した (= 繋がってはいる)。

    接続失敗 (aiohttp の例外・タイムアウト) と型で分ける — tool がモデルに返す文面を
    「パスの問題 (400) / ファイルが無い (404) / 繋がらない」で言い分けるため。
    文面は `sandbox HTTP {status}: {detail}` (`utils/image_refs.py` がこの形を読む)。
    """

    def __init__(self, status: int, detail: str):
        super().__init__(f"sandbox HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


async def sandbox_post(path: str, payload: dict, timeout_s: float) -> dict:
    """sandbox サービスへの POST。**トークンがあるときだけ**共有トークンを付ける (#132)。

    module 関数なのは、この tool 以外にも sandbox を読む側がいるから (#139:
    `utils/image_refs.py` が `/workspace/...` 参照を `/files/read` で解決する)。
    sandbox への HTTP 出口をここ 1 本にして、トークンの付け方を 2 箇所に持たない。

    宛先は module 定数 `SANDBOX_URL` (env) のみ — DB (valve) からは変えられない。
    header は session に持たせる (この session はこの 1 リクエストにしか使わない)。
    未設定なら header 自体を付けない — 空の `Bearer ` を送らない (sandbox 側の
    認証が有効な配備では、無ヘッダも空トークンも等しく 401 なので実害は無いが、
    「無いものを送る」経路を作らない)。
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
                    # まとめて説明がつく (未設定は「不一致」の一種)。この detail は
                    # ログ用 — モデルには `_SANDBOX_AUTH` だけを返す (env 名を出さない)
                    raise SandboxHTTPError(
                        401,
                        "認証に失敗した。nucllei と sandbox の "
                        "SANDBOX_TOKEN が一致していない "
                        "(片側だけ未設定の場合も含む。配備の env を確認)",
                    )
                raise SandboxHTTPError(resp.status, body)
            return await resp.json()


# write_file の本文上限 (env `SANDBOX_WRITE_FILE_LIMIT_BYTES`、読み取りは env.py)。
WRITE_FILE_LIMIT = SANDBOX_WRITE_FILE_LIMIT_BYTES

# jail から見た workspace の置き場 (= jail の cwd)。モデルに見せる `path` は全部
# この絶対パスで揃える。sandbox サービスの `/files/*` は workspace 相対で受けるので、
# 変換はここ (tool 側) で持つ — wire を変えないので sandbox image と版がずれても動く。
WORKSPACE_ROOT = "/workspace"
# read-only の参照データの置き場 = **jail 側のパス** (sandbox サービスの RO_MOUNT_ROOT /
# image_refs の SANDBOX_MOUNT_ROOT と同じ `/ext`。2026-10-03 までは `/mnt`)。nucllei の fs では
# `image_refs.EXT_DIR` (= EXT_DIR) に同じ物が在る規約で、URL `/ext/{rest}` とも同じ文字列。
# sandbox の `/files/*` はここを扱えない — write_file / list_workspace は使える手段を
# 案内して断り、fetch_from_workspace は nucllei 側の同じ置き場 (EXT_DIR) から橋渡しする。
_RO_MOUNT_ROOT = "/ext"


def _normalize_sandbox_path(path: str) -> str:
    """モデルが渡した path (**絶対パスのみ**) を正規化する。空・NUL・相対は ""。
    `.` / `..` / 重複した `/` は畳む。相対を「/workspace からの相対」として救済しない —
    `path` の形は 1 つ (`path="https://x/y.html"` が `/workspace/https:/x/y.html` に
    なる、といった帰結を作らない)。"""
    raw = (path or "").strip()
    if not posixpath.isabs(raw) or "\x00" in raw:
        return ""
    # 先頭の `//` は normpath が畳まない (POSIX の実装定義) ので先に 1 本にする
    return posixpath.normpath("/" + raw.lstrip("/"))


def _under_ro_mount(abs_path: str) -> bool:
    return abs_path == _RO_MOUNT_ROOT or abs_path.startswith(_RO_MOUNT_ROOT + "/")


def _resolve_workspace_path(
    path: str, arg_form: str, ro_hint: str = ""
) -> tuple[str, str]:
    """モデルが渡した path を (絶対パス, workspace 相対) に解決する。

    受けるのは絶対パス (`/workspace/...`) だけ。`.` / `..` / 重複した `/` は畳んだうえで
    /workspace の外に出るものを断る — `/workspace/workspace/x` に黙って書くような読み替えは
    しない。解決できないときは call の ToolError を raise する。
    symlink 越しの脱出はここでは見えない (sandbox 側の `_safe_path` が実 fs で弾く)。

    形の誤り (空・相対・URL・/workspace の外) は値を調べて言い分けず、全 tool 共通の形で
    返す: その tool の path が受ける形の固定文 `arg_form` (`image_refs.PATH_ARG_FORM` /
    `WORKSPACE_PATH_ARG_FORM`) + 行き先の規則 `REF_ARGS_RULE`。
    `/ext/…` だけは同じ `path` の中の区分 (read-only の参照データ) なので、`ro_hint` で
    使える手段を案内する。
    """
    abs_path = _normalize_sandbox_path(path)
    if abs_path == WORKSPACE_ROOT:
        return abs_path, "."
    if abs_path.startswith(WORKSPACE_ROOT + "/"):
        return abs_path, abs_path[len(WORKSPACE_ROOT) + 1 :]
    if _under_ro_mount(abs_path):
        # ro_hint は事実の続き (+ 次の手) — 組み立て済みの文として分ける
        raise ToolCallError.from_text(
            f"{abs_path} は read-only の参照データ。{ro_hint}"
        )
    # image_refs は本 module を import する — 循環を避けて呼び出し時に引く
    from nucllei.utils.image_refs import REF_ARGS_RULE

    raise ToolCallError(
        f"{(path or '').strip() or '(空)'} は sandbox のパスの形でない",
        f"{arg_form} {REF_ARGS_RULE}",
    )


# モデルに返す文は「事実の文 + `次の手:` 以降」、常体 (`utils/ref_errors` の module
# docstring)。この toolkit に無い tool は「〜が使えるなら」と条件付きで書く。
_NEXT = "次の手: "

# 画像・音声・動画は display チャネルで tool の枠の下に出る (`_display_markdown`)。
# それを受けたモデル向けの 1 文 (exports の自動添付と fetch_from_workspace で共通)
_DISPLAYED = "ユーザーの画面には既に表示されている。" + _NEXT + "本文に貼り直さない"

# sandbox の実行時間の上限 (sandbox サービスの env `SANDBOX_TIMEOUT_MAX_S` の既定。nucllei
# からは sandbox 側の値を読めないので既定値で言う — `timeouts` の 630 と同じ前提)
_SANDBOX_TIMEOUT_MAX_S = 600

# sandbox に繋がらない / 401 のときの文面。例外の文・HTTP の detail・env 名はモデルに
# 出さず、ログに残す (2026-10-04 llens 監査 A17 / A18)
_SANDBOX_DOWN_NEXT = (
    _NEXT
    + "時間を置いて 1 回だけ呼び直す。続くならユーザーに sandbox が使えないと伝える。"
)
_SANDBOX_AUTH = (
    "ERROR: sandbox との認証に失敗した (サーバの設定の問題)。"
    + _NEXT
    + "呼び直さない。ユーザーには sandbox が今使えないと伝え、管理者への連絡を勧める。"
)


def _file_link_note(name: str, file_id: str) -> str:
    """display に乗らない添付 (このチャットのファイル一覧にだけ載る) を本文で示すリンク。"""
    label = (name or "").replace("[", "(").replace("]", ")")
    return f"{_NEXT}本文で示すなら [{label}](/api/v1/files/{file_id}/content) と書く"


def _err(text: str, kind: ToolErrorKind) -> ToolError:
    """組み立て済みの `ERROR: 事実。次の手: …` を ToolError にする (呼び手が raise)。
    この module の文面は定数・テンプレートで持っているので、分けるのは ToolError に任せる。"""
    return ToolError.from_text(text, kind=kind)


def _sandbox_auth() -> ToolError:
    # 鍵の設定の誤り = サーバが壊れている (呼び直しても直らない) → fatal
    return _err(_SANDBOX_AUTH, "fatal")


def _exec_error(e: Exception) -> ToolError:
    """sandbox の実行 (`/exec` `/exec_cell`) の失敗。例外の中身はログだけ。呼び手が raise する。"""
    log.error(f"[sandbox] exec failed: {e!r}")
    if isinstance(e, SandboxHTTPError) and e.status == 401:
        return _sandbox_auth()
    return _err("ERROR: sandbox で実行できなかった。" + _SANDBOX_DOWN_NEXT, "failure")


def _files_error(e: Exception, abs_path: str) -> ToolError:
    """sandbox `/files/*` の失敗をモデル向けの ToolError にする (原因を言い分ける)。
    例外の文・detail はログだけ (A17)。呼び手が raise する。"""
    log.warning(f"[sandbox] /files failed for {abs_path}: {e!r}")
    if not isinstance(e, SandboxHTTPError):
        return _err(
            "ERROR: sandbox に接続できない (起動していないか、応答が無い)。"
            + _SANDBOX_DOWN_NEXT,
            "failure",
        )
    if e.status == 401:
        return _sandbox_auth()
    if e.status == 400:
        return ToolCallError(
            f"{abs_path} は使えない path (sandbox が受け付けなかった)",
            "/workspace/… の絶対パスで渡す。",
        )
    if e.status == 404:
        return ToolOutcome(
            f"{abs_path} というファイルは無い (ディレクトリは不可)",
            "list_workspace で確かめる。",
        )
    if e.status == 413:
        return ToolOutcome(
            f"{abs_path} は大きすぎて取り出せない",
            "必要な部分だけを /workspace/ に書き出してから渡す。",
        )
    return ToolFailure(
        f"sandbox がエラーを返した (HTTP {e.status})",
        "1 回だけ呼び直す。続くならユーザーに伝える。",
    )


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


# file が引けないときの理由 (レコードが無い / 所有者でない を言い分けない)。file_id の
# エラーは全 tool 共通で `ERROR: file_id <id> のレコードが無い、またはアクセス権が無い。` +
# `image_refs.FILE_ID_ARG_FORM`
_NO_FILE = "レコードが無い、またはアクセス権が無い"


def _too_large_import(u: str, err) -> str:
    """send_to_workspace(url=) の too_large: 上限は valve (管理者の設定) で引数では上げられない。"""
    limit = f" {_human_size(err.limit)}" if err.limit is not None else ""
    if err.size is None:
        what = f"{u} は取り込みの上限{limit} を超えている (実サイズは不明)。"
    elif err.size_exact:
        what = (
            f"{u} は {_human_size(err.size)} で、取り込みの上限{limit} を超えている。"
        )
    else:
        # Content-Length の無い stream を打ち切った時点の受信量 = 実サイズの下限
        what = (
            f"{u} は少なくとも {_human_size(err.size)} あり、取り込みの上限{limit} を"
            "超えている (実サイズは不明)。"
        )
    from nucllei.utils.ref_errors import show_as_link

    return (
        f"ERROR: {what}sandbox には取り込めない (上限は管理者の設定で、引数では"
        f"上げられない)。リンク切れではない。{_NEXT}" + show_as_link(u)
    )


def _explain_ref_error(u: str, err) -> ToolError:
    """resolver の RefError をモデル向けの ToolError にする (send_to_workspace(url=) 用)。

    組み立ては全 tool 共通の `utils/ref_errors.explain_ref_error` (feed_image / fetch_url と
    同じ code を同じ文で言い分ける — 「読めなかった」を全部リンク切れにしない)。この tool
    固有の言い方は too_large (取り込みの上限は valve) と unexplained だけ (2026-10-04 に
    redirect / 上流不達 / 許可先の独自文面は既定へ揃えた)。内部名 (env 名・detail・code) は
    出さない (resolver が warning ログに残す)。
    """
    # ref_errors → image_refs は本 module を参照する — 循環を避けて呼び出し時に引く
    from nucllei.utils.ref_errors import RefErrorHints, explain_ref_error

    hints = RefErrorHints(
        too_large=_too_large_import,
        unexplained="ERROR: {ref} を取得できない。",
    )
    return explain_ref_error(u, err, arg="url", hints=hints)


def _explain_mount_error(abs_path: str, err) -> ToolError:
    """resolver の RefError をモデル向けの ToolError にする (fetch_from_workspace の `/ext/…` 用)。

    文面は解決結果 (RefError.code) で言い分ける。`not_found` は「無い / ディレクトリ /
    read-only の置き場の外を指すリンク」をまとめて言う — resolver がこの 3 つを同じ code に
    閉じている (fail closed。/ext ゲートが全部 404 にするのと同じ) ので、tool も分けない。
    """
    code = err.code
    if code == "too_large":
        limit = f" {_human_size(err.limit)}" if err.limit is not None else ""
        size = f" {_human_size(err.size)} で、" if err.size is not None else ""
        return ToolOutcome(
            f"{abs_path} は{size}添付できる上限{limit} を超えている "
            "(上限は管理者の設定で、引数では上げられない)。ファイルは存在する",
            "run_python / run_shell で必要な部分だけを /workspace/ に書き出してから渡す。",
        )
    if code == "not_found":
        # nucllei 側の EXT_DIR に無い = 本当に無いか、配備の mount のずれ (sandbox にだけ
        # mount されている)。resolver は分けられないので 1 文で両方を言う。ken 2026-10-04:
        # 配備は nucllei と sandbox に同じ /ext の組を mount する — /ext のファイルは URL で
        # 見せるだけで、取り出し (添付) の経路は作らない (要らない file_id を増やさない)
        return ToolOutcome(
            f"{abs_path} はこのサーバの /ext に無い (存在しないか、sandbox にだけ"
            " mount されている)",
            "中身が要るなら sandbox で読んで使う。",
        )
    if code == "traversal":
        return ToolCallError(
            f"{abs_path} は使えない path (制御文字やバックスラッシュを含む)",
            "run_shell で /workspace/ に別名でコピーしてからそのパスを渡す。",
        )
    log.info(f"[sandbox] fetch_from_workspace {abs_path}: unexplained {code}")
    # resolver が返した code をこの tool が言い分けられない = こちらの取りこぼし → fatal
    return ToolFatal(f"{abs_path} を読めない")


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
    # 同期経路は TOOL_EXEC_TIMEOUT_S=300 で切られ、timeout_s=300〜600 の
    # 正当な指定が harness 側で失われる(検査 2026-08-16)。
    # 600 + HTTP 余裕 30 = _post の付け足しと同じ余裕幅。
    timeouts = {"run_python": 630, "run_python_once": 630, "run_shell": 630}

    # 2026-10-04: 1 応答に並んだ同期 call の並行実行から外す(toolkit 単位で応答順に
    # 1 本ずつ — utils/middleware._tool_call_lane_key)。sandbox 側は同じ会話
    # (run_python)/ 同じユーザー(run_python_once / run_shell)で直列化される
    # (_memos/async-exec-design.md 追補 3-5 の実測)ので並行にしても速くならず、
    # HTTP の到着順が応答順と食い違うと stateful なセルの実行順が入れ替わる
    # (後ろのセルが前のセルの変数を使う)うえ、順番待ちが per-call timeout に食い込む。
    serial = True

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
        url_max_mb: float = Field(
            default=50.0,
            description="send_to_workspace(url=) で sandbox に取り込む 1 ファイルの上限 MB "
            "(http(s) URL はどこでも。同一サーバの /ext/… = sandbox の /ext/… と同じ物は"
            "コピーしないので、この上限は掛からない)",
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
                # display (人間の目) には事実だけ、次の手はモデル向けの行にだけ付ける
                warn = (
                    f"{name} ({_human_size(out['size'])}) は転送上限を超えて添付できなかった"
                    " (workspace には残っている)。"
                )
                lines.append(
                    f"- ⚠ {warn}{_NEXT}分割・圧縮するか、必要な部分だけ書き出す。"
                )
                display(f"⚠ {warn}")
                continue
            try:
                att = _register_bytes_file(
                    user_id, os.path.basename(name), base64.b64decode(out["b64"])
                )
                attachments.append(att)
                line = f"- {name} ({_human_size(out['size'])}) file_id={att['id']}。"
                # #88: 画像/音声/動画は display チャネルで tool 枠下に自動表示(モデルの
                # 貼り付けに依存しない)。それ以外はチャットのファイル一覧(右ペイン —
                # message.files の type 'file' は本文の下には描かれない)にだけ載るので、
                # 本文で示すリンクを渡す。background 完了の [task] 通知では display の
                # 中身がモデルに渡り、画面には出ていない(execution_resume)ので貼らせる。
                md = _display_markdown(
                    name, att["id"], att["file"]["meta"]["content_type"]
                )
                if md:
                    display(md)
                    line += f" {_DISPLAYED} ([task] 通知で受け取ったときは表示されていないので貼る)"
                else:
                    line += " " + _file_link_note(name, att["id"])
                lines.append(line)
            except Exception as e:
                log.error(f"[sandbox] register export {name} failed: {e!r}")
                lines.append(f"- {name}: ファイルの登録に失敗した")
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
            + f"\n⋮ [切り詰め: 全 {len(text)} 字中 先頭 {head} 字 + 末尾 {tail} 字だけを表示した。"
            f"{_NEXT}全文が要るなら出力を /workspace のファイルに書き出し、"
            "run_shell の rg / head / tail で部分参照する]\n" + text[-tail:]
        )

    def _format_exec_result(
        self,
        res: dict,
        output_lines: list[str],
        timeout_s: Optional[int] = None,
        flag_failure: bool = False,
    ) -> str:
        # 実行したコードが失敗した (exit_code != 0 / タイムアウト) ら結果に failure の旗を
        # 立てる (#145、ken「sandbox の stderr とかは？実行した python のエラーとか」)。
        # raise はしない — stdout / traceback / 生成ファイルはモデルに要る。stderr だけ
        # (exit_code 0 の warning) は通常の結果。run_shell は旗を立てない (grep の
        # 不一致 = exit 1 のように、非 0 が失敗でないコマンドが多い)
        if flag_failure and (res.get("exit_code") != 0 or res.get("timed_out")):
            mark_error_kind("failure")
        parts = [f"exit_code={res['exit_code']} ({res['duration_s']}s)"]
        if res.get("timed_out"):
            # sandbox は指定値を上限で clamp する — 実際に効いた値で言う
            n = min(
                int(timeout_s or self.valves.default_timeout_s), _SANDBOX_TIMEOUT_MAX_S
            )
            parts[0] += (
                f" **タイムアウト (timeout_s={n} 秒を超えたので強制終了した)**"
                f" {_NEXT}処理を分けるか、timeout_s を上げて呼び直す"
                f" (上限 {_SANDBOX_TIMEOUT_MAX_S} 秒)。"
            )
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
                "--- 生成ファイル (exports/) — このチャットのファイル一覧に添付した ---\n"
                + "\n".join(output_lines)
            )
        return "\n".join(parts)

    def _format_cell_result(self, res: dict, output_lines: list[str]) -> str:
        # セルが例外 (error) かタイムアウトで終わったら failure の旗 (raise しない —
        # `_format_exec_result` と同じ)
        if res.get("error") or res.get("timed_out"):
            mark_error_kind("failure")
        parts = []
        if res.get("session_reset"):
            parts.append(
                "※ 実行環境がリセットされ、以前の変数・import は失われた。"
                f"{_NEXT}必要なら定義し直してから続ける。"
            )
        stdout = res.get("stdout", "")
        error = res.get("error")
        if res.get("timed_out"):
            parts.append(
                "**タイムアウト (セルを中断した。変数・import は残っている)**"
                f" {_NEXT}処理を分けるか、timeout_s を上げて続ける。"
            )
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
                "--- 生成ファイル (exports/) — このチャットのファイル一覧に添付した ---\n"
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
            raise ToolFailure("ユーザーを特定できない")
        payload = {
            "user_id": __user__["id"],
            "lang": lang,
            "code": code,
            "timeout_s": timeout_s or self.valves.default_timeout_s,
        }
        try:
            res = await self._post("/exec", payload, payload["timeout_s"] + 30)
        except Exception as e:
            raise _exec_error(e)
        output_lines = await self._register_exports(
            __user__["id"],
            res.get("exports", []),
            __chat_id__,
            __message_id__,
            __event_emitter__,
        )
        return self._format_exec_result(
            res, output_lines, payload["timeout_s"], flag_failure=lang == "python"
        )

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
            raise ToolFailure("ユーザーを特定できない")
        if not __chat_id__:
            raise ToolCallError(
                "この呼び出しにはチャットの id が無く、状態を持つ実行ができない",
                "単発で足りるなら run_python_once を使う。",
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
            raise _exec_error(e)
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
    ) -> str:
        """
        チャットの添付ファイルや URL で参照できるリソースを sandbox の
        /workspace/imports/ にコピーする。
        **sandbox 内 (run_python / run_shell) で中身を扱う時だけ**呼ぶこと。
        他のツールは file_id から直接読むので、その前段としては不要
        (このコピーはファイルを「読めるようにする」汎用手段ではない)。

        引数は名前のとおりに解釈する (他の tool と共通)。受け取った形のまま渡すこと
        (読み替えない・ホストを補わない)。
        sandbox のパス (`/workspace/…` `/ext/…`) は既に jail から見えているので取り込み不要。
        同一サーバの URL `/ext/…` は sandbox のパス `/ext/…` と同じ文字列で同じファイル
        (url に渡せばコピーせずそのパスを返す)。
        jail はネットワーク不可なので、URL を run_shell で curl する代わりにこれで取り込む。

        :param filename: 対象ファイル名 (部分一致)。file_id 未指定時に添付一覧から探す。省略時は全件。
        :param file_id: 取り込むファイル。添付・生成物の id (添付一覧に出ていなくても、分かっていれば取り込める)。
        :param url: 取り込むリソース。スキーム付きの絶対 URL (`http(s)://…`) か、同一サーバの `/ext/…`・`/api/v1/files/{id}/content`。file_id / filename と排他。
        """
        if not __user__ or not __user__.get("id"):
            raise ToolFailure("ユーザーを特定できない")

        url = (url or "").strip()
        file_id = (file_id or "").strip()
        if url and (file_id or filename):
            raise ToolCallError(
                "url は file_id / filename と同時に指定できない",
                "どちらか一方だけを指定する。",
            )

        # 引数は名前で解釈が決まる — url は URL として、file_id は file の id として解決する。
        # 値の形を見て別の引数に読み替えない (url= に UUID / sandbox のパス、file_id= に URL が
        # 来ても救済しない。それぞれの解決が失敗し、受ける形を固定文で返す)。
        if url:
            return await self._import_url(url, __user__["id"])
        if file_id:
            from nucllei.utils.image_refs import FILE_ID_ARG_FORM

            return await self._import_one(
                file_id, __user__["id"], f"file_id {file_id}", FILE_ID_ARG_FORM
            )
        # 添付一覧から (filename の部分一致、省略時は全件)
        report = []
        for fid, fname in _iter_context_files(__files__, __metadata__):
            if filename and (not fname or filename.lower() not in fname.lower()):
                continue
            line, failure = await self._import_file(fid, fname, __user__["id"])
            report.append(line or f"- {fname or fid}: {failure} (取り込まなかった)")
        if not report:
            return "取り込み対象の添付ファイルが無かった。"
        return "取り込み結果:\n" + "\n".join(report)

    async def _import_file(self, fid: str, fname, user_id: str) -> tuple[str, str]:
        """file (Files のレコード) 1 件を `/workspace/imports/` に書く。
        戻り値は (成功の報告行, "") か ("", 失敗の理由)。所有者でないファイルは取り込まない —
        「レコードが無い」と「所有者でない」は同じ理由 `_NO_FILE` で返す (存在の有無を
        漏らさない。llens の tool と同文)。"""
        rec = Files.get_file_by_id(fid)
        # 所有者チェック: file_id 直指定で他ユーザーのファイルを取り込ませない
        if not rec or not rec.path or getattr(rec, "user_id", None) != user_id:
            return "", _NO_FILE
        try:
            local_path = Storage.get_file(rec.path)
            data = open(local_path, "rb").read()
            name = os.path.basename(fname or rec.filename)
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
            log.error(f"[sandbox] import {fid} failed: {e!r}")
            return "", "取り込みに失敗した"
        return (
            f"- {name} ({_human_size(len(data))}) → {WORKSPACE_ROOT}/imports/{name}",
            "",
        )

    async def _import_one(
        self, fid: str, user_id: str, given: str, arg_form: str = ""
    ) -> str:
        """引数で直に指された file 1 件を取り込む (file_id= / url= の配信口 URL)。

        失敗は ToolError を raise する。`given` はモデルが渡した引数の言い方 (`file_id <値>` / URL)。
        file が引けないときは全 tool 共通の文 + その引数が受ける形の固定文 `arg_form`
        (file_id= なら `image_refs.FILE_ID_ARG_FORM`。url= 経由は形の誤りではないので無し)。
        """
        line, failure = await self._import_file(fid, None, user_id)
        if failure == _NO_FILE:
            raise ToolCallError(f"{given} の{_NO_FILE}", arg_form or None)
        if failure:
            raise ToolFailure(f"{given} の{failure}")
        return "取り込み結果:\n" + line

    # ---- send_to_workspace の url ソース ---------------------------------------
    async def _import_url(self, u: str, user_id: str) -> str:
        """`url` を **URL として** 共有 resolver (`utils/image_refs`) で解決して取り込む。
        受ける形はスキーム付きの絶対 URL と、同一サーバの相対 URL (`/` 始まり = このサーバの
        URL として、サーバが実際に持つ経路に当てる)。値の形から別の意味には読み替えない。

        行き先・redirect の扱い・失敗理由は resolver 一本 = builtin の fetch_url /
        feed_image と同じ (同じ URL が一方で通り他方で落ちることが無い)。取れるのは
        `/ext/{rest}` (相対、または絶対 = host 不問。nucllei の `EXT_DIR/{rest}` をローカル読み —
        与えられた host へは接続しない) と、それ以外のスキーム付き http(s) URL
        **どこでも** (一般の URL。登録無しで直接取得。接続先は解決したアドレスに固定、
        redirect は 3 回まで辿る — 一般の Web と同じ扱い。届くかは配備のネットワーク次第、
        2026-10-03 ken 決定)。
        **取りに行かないのはこのサーバ自身 (と同居するサービス) を指す URL だけ**
        (`unknown_upstream` reason `server_self`)。cookie / 認証ヘッダをどこにも転送しないので、
        リクエスト文脈の無い background 続きターンでも同じに動く。

        - `/ext/{rest}` で nucllei の `EXT_DIR/{rest}` が実在 → コピーせず、sandbox から
          見えるパス `/ext/{rest}` (URL と同じ文字列) を返す (`mounted_path_for_url`)。
          sandbox の `/ext` と nucllei の `EXT_DIR` に同じ物を置くという
          配備の規約 (#138) に依存する — sandbox 側の bind の有無は nucllei からは
          確かめられない (規約どおりでない配備では、返したパスが jail に無い)
        - それ以外 (一般の URL) → `/workspace/imports/<name>` に書く。上限は valve `url_max_mb`
        - `/api/v1/files/{id}/content` (このサーバのファイル配信口。本文リンクとして頻出。
          相対、または絶対 = `/ext/` と同じく host 不問) → URL のルーティングとして file に
          当たる (`file_id_of_url`)。HTTP では取りに行かず、file_id 経路と同じ読み出し・
          同じ所有者チェックで取り込む
        """
        # image_refs は本 module (sandbox_post / SANDBOX_URL) を import する — 循環を
        # 避けて呼び出し時に引く
        from nucllei.utils.image_refs import (
            file_id_of_url,
            mounted_path_for_url,
            resolve_url,
        )

        # fragment は付いていても同じ file に当たる (`file_id_of_url` の規則)
        file_id = file_id_of_url(u)
        if file_id:
            return await self._import_one(file_id, user_id, u)

        mounted = await mounted_path_for_url(u)
        if mounted:
            return (
                f"取り込み不要: {u} は sandbox から {mounted} として既に見えている "
                f"(read-only)。{_NEXT}run_python / run_shell からそのパスで開く。"
            )

        max_bytes = int(self.valves.url_max_mb * 1024 * 1024)
        loaded, err = await resolve_url(u, max_bytes=max_bytes)
        if loaded is None:
            raise _explain_ref_error(u, err)
        # name は URL の basename (percent-encoded のまま)
        name = _safe_import_name(unquote(loaded.name)) or "download"
        try:
            await self._post(
                "/files/write",
                {
                    "user_id": user_id,
                    "path": f"imports/{name}",
                    "b64": base64.b64encode(loaded.data).decode(),
                },
                timeout_s=120,
            )
        except Exception as e:
            log.error(f"[sandbox] import write failed for {u}: {e!r}")
            if isinstance(e, SandboxHTTPError) and e.status == 401:
                raise _sandbox_auth()
            raise ToolFailure("sandbox へ書き込めない")
        return (
            "取り込み結果:\n"
            f"- {name} ({_human_size(len(loaded.data))}) → "
            f"{WORKSPACE_ROOT}/imports/{name}"
        )

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

        :param path: 書き込み先のファイル。sandbox の絶対パス (`/workspace/…`)。例: /workspace/work/index.html
        :param content: ファイルの中身 (テキストそのまま)
        """
        if not __user__ or not __user__.get("id"):
            raise ToolFailure("ユーザーを特定できない")
        from nucllei.utils.image_refs import WORKSPACE_PATH_ARG_FORM

        abs_path, rel = _resolve_workspace_path(
            path, WORKSPACE_PATH_ARG_FORM, "書き込めるのは /workspace/ 配下だけ。"
        )
        if rel == "." or path.rstrip().endswith("/"):
            raise ToolCallError(f"{path!r} はディレクトリ", "ファイル名まで指定する。")

        data = content.encode("utf-8")
        if len(data) > WRITE_FILE_LIMIT:
            raise ToolCallError(
                f"内容が大きすぎる ({_human_size(len(data))} > "
                f"{_human_size(WRITE_FILE_LIMIT)})",
                "分割して複数回に分けるか、run_python で生成する。",
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
            raise _files_error(e, abs_path)

        msg = f"{abs_path} に書き込んだ ({_human_size(len(data))})。"
        if rel.startswith("exports/"):
            # exports/ の自動添付は「実行の後始末」で回収する仕組みなので、
            # 実行を伴わない write_file では発火しない (取りこぼしの typical case)
            msg += (
                " exports/ の自動添付は run_python / run_shell の実行後に回収する仕組みなので、"
                f"この書き込みだけでは添付されない。{_NEXT}ユーザーに渡すなら"
                " fetch_from_workspace を呼ぶ。"
            )
        return msg

    async def list_workspace(
        self,
        path: str = WORKSPACE_ROOT,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        sandbox の /workspace 配下のファイル一覧を絶対パスで返す (実行を跨いで永続する領域)。
        返ったパスは他の tool の path にそのまま渡せる。

        :param path: 一覧するディレクトリ。sandbox の絶対パス (`/workspace/…`)。省略時は /workspace
        """
        if not __user__ or not __user__.get("id"):
            raise ToolFailure("ユーザーを特定できない")
        from nucllei.utils.image_refs import WORKSPACE_PATH_ARG_FORM

        abs_path, rel = _resolve_workspace_path(
            path,
            WORKSPACE_PATH_ARG_FORM,
            "list_workspace が一覧できるのは /workspace/ 配下だけ。"
            f"{_NEXT}`/ext/` は run_shell の ls で見る "
            "(見つけたファイルをユーザーに見せるなら fetch_from_workspace にそのパスを渡す)。",
        )
        try:
            res = await self._post(
                "/files/list",
                {
                    "user_id": __user__["id"],
                    "path": rel,
                },
                timeout_s=30,
            )
        except Exception as e:
            raise _files_error(e, abs_path)
        entries = res.get("entries", [])
        if not entries:
            return f"{abs_path} は空 (ファイルが無いか、ディレクトリが存在しない)。"
        # sandbox は workspace 相対で返す — モデルには絶対パスで見せる
        return "\n".join(
            f"- {WORKSPACE_ROOT}/{e['path']} ({_human_size(e['size'])})"
            for e in entries
        )

    async def fetch_from_workspace(
        self,
        path: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
    ) -> str:
        """
        sandbox のファイルをユーザーに見せられる形にする: チャットに添付して file_id と本文用の
        リンクを返す (exports/ 以外に作ったファイルや過去実行の成果物、`/ext/` で見つけた資料を
        渡すときに使う)。
        画像・音声・動画は添付と同時にユーザーの画面に表示される (本文に貼り直さない)。
        `/ext/…` のファイルは sandbox のパスと URL が同じ文字列で、サーバが配信していれば
        添付せずその URL のリンクを返す (本文にはそれをそのまま書く。日本語名は percent-encode
        された同じファイルの別表記になる)。/workspace のパスはブラウザでは開けないので、
        パスを本文に貼ったり URL に書き換えたりせず、これに渡すこと。

        :param path: 対象ファイル。sandbox の絶対パス (`/workspace/…` `/ext/…`)。例: /workspace/imports/converted.xlsx
        """
        if not __user__ or not __user__.get("id"):
            raise ToolFailure("ユーザーを特定できない")
        from nucllei.utils.image_refs import PATH_ARG_FORM

        # path は sandbox のパスとしてだけ解く (URL / file_id には読み替えない)。置き場が
        # /workspace か /ext かは sandbox の fs の区分で、読み出し口が違うだけ
        normalized = _normalize_sandbox_path(path)
        if _under_ro_mount(normalized):
            return await self._fetch_mounted(
                normalized,
                __user__["id"],
                __chat_id__,
                __message_id__,
                __event_emitter__,
            )
        abs_path, rel = _resolve_workspace_path(path, PATH_ARG_FORM)
        try:
            res = await self._post(
                "/files/read",
                {
                    "user_id": __user__["id"],
                    "path": rel,
                },
                timeout_s=120,
            )
        except Exception as e:
            raise _files_error(e, abs_path)
        try:
            data = base64.b64decode(res["b64"])
        except Exception as e:
            log.error(f"[sandbox] bad /files/read response for {abs_path}: {e!r}")
            raise ToolFatal("sandbox の応答を読めない")
        return await self._attach(
            abs_path,
            os.path.basename(res["name"]),
            data,
            __user__["id"],
            __chat_id__,
            __message_id__,
            __event_emitter__,
        )

    async def _fetch_mounted(
        self, abs_path: str, user_id: str, chat_id, message_id, __event_emitter__
    ) -> str:
        """`/ext/…` (read-only の参照データ) を利用者に見せられる形にする。

        `send_to_workspace(url=)` の「URL の解決先が sandbox から見えるならパスを返す」
        (`mounted_path_for_url`) の逆向き。値の形ではなく解決結果で分かれる:

        - 実体が nucllei 側の `EXT_DIR` にも実在する (= /ext ゲートが `/ext/{rest}` で配信
          している) → コピーも添付もせず、その URL (同じ文字列) を返す (`url_for_mounted_path`)
        - それ以外 → `resolve_path` (境界・上限は resolver) で読めない理由を返す。nucllei 側に
          無い物は「このサーバの /ext に無い」(本当に無いか mount のずれ)。ken 2026-10-04: 配備は
          nucllei と sandbox に同じ /ext の組を mount し、/ext のファイルは URL で見せるだけ —
          sandbox 経由で読み出して添付する経路は作らない (要らない file_id を増やさない)。
          resolver は読めるのに配信口が URL を出さない境界の差 (通常起きない) のときだけ、
          nucllei 側の実体を /workspace のファイルと同じ手順で添付する
        """
        # image_refs は本 module を import する — 循環を避けて呼び出し時に引く
        from nucllei.utils.image_refs import resolve_path, url_for_mounted_path

        url = await url_for_mounted_path(abs_path)
        if url:
            name = os.path.basename(abs_path)
            is_image = (mimetypes.guess_type(name)[0] or "").startswith("image/")
            link = f"![{name}]({url})" if is_image else f"[{name}]({url})"
            return (
                f"添付不要: {abs_path} は既に URL として配信されている "
                f"(ユーザーのブラウザがそのまま開ける)。{_NEXT}ユーザーに見せるときは、"
                f"これを 1 文字も変えずに本文に書く: {link}"
            )
        loaded, err = await resolve_path(abs_path, user_id)
        if loaded is None:
            raise _explain_mount_error(abs_path, err)
        return await self._attach(
            abs_path,
            loaded.name,
            loaded.data,
            user_id,
            chat_id,
            message_id,
            __event_emitter__,
        )

    async def _attach(
        self,
        abs_path: str,
        name: str,
        data: bytes,
        user_id: str,
        chat_id,
        message_id,
        __event_emitter__,
    ) -> str:
        """バイト列を Files に登録してチャットに添付し、モデル向けの報告文を返す。"""
        try:
            att = _register_bytes_file(user_id, name, data)
        except Exception as e:
            log.error(f"[sandbox] register {abs_path} failed: {e!r}")
            raise ToolFatal("ファイルの登録に失敗した")
        await self._persist_and_emit_files(
            [att], chat_id, message_id, __event_emitter__
        )
        line = (
            f"{abs_path} ({_human_size(len(data))}) をこのチャットのファイル一覧に添付した"
            f" (file_id={att['id']})。"
        )
        # #88: 画像/音声/動画は display で枠下に自動表示(exports/ 自動回収と同じ経路)。
        # それ以外はファイル一覧にだけ載る — 本文で示すリンクを渡す(_register_exports と同じ)
        md = _display_markdown(name, att["id"], att["file"]["meta"]["content_type"])
        if md:
            display(md)
            line += f"\n{_DISPLAYED}。"
        else:
            line += f"\n{_file_link_note(name, att['id'])}。"
        return line
