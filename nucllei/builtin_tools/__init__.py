"""組み込みツール(issue #42)。

リポジトリのコードで定義され、Tools テーブルに builtin 行として起動時 sync される。
カスタム tool と同列(toolIds picker / skill の tool_ids / access_control / 4pcg ガードが
そのまま効く)で、違いは content が DB でなく repo モジュールから来ること
(_memos/builtin-tools.md 1c: 境界基準は「出荷単位」)。

- 一覧 UI は組み込み/カスタムをセクション分けして見せる(meta.builtin で判別)
- content 更新・削除は拒否(access_control の編集のみ可)。定義の真実源は常に repo
- 同名関数の衝突は先勝ち discard(#18-3 の教訓)— builtin の関数名は他とぶつけない
"""

import logging

from nucllei.builtin_tools import attachment_text, chat_files, history, sandbox

log = logging.getLogger(__name__)

# builtin tool の id 名前空間。この prefix はユーザー作成 tool から予約する
# (create で拒否 / sync で未定義の builtin 行を prune する基準)。
BUILTIN_ID_PREFIX = "builtin_"

# tool_id → 定義。id は BUILTIN_ID_PREFIX 始まりでユーザー作成 tool と衝突させない
BUILTIN_TOOLS = {
    "builtin_history": {
        "module": history,
        "name": "History Management",
        "description": (
            "List, read and search your own chat history "
            "(list_history / read_history / grep_history). Owner-only"
        ),
    },
    "builtin_chat_files": {
        "module": chat_files,
        "name": "Chat Files",
        "description": (
            "List and search file attachments issued in the current chat "
            "(list_chat_files / grep_chat_files). Metadata only, owner-only"
        ),
    },
    "builtin_attachment_text": {
        "module": attachment_text,
        "name": "Attachment Text",
        "description": (
            "Read the extracted text of an attachment by file_id "
            "(read_attachment). Owner-only"
        ),
    },
}

# sandbox だけは env ゲート付き。他の 3 本は nucllei 自身の DB を読むだけで
# 外部依存ゼロ = どの配備でも必ず動くのに対し、sandbox は外部サービス
# (sandbox コンテナ) が要る。居ない配備で一覧に生やすと「見えているのに呼ぶと
# 必ずエラー」になり、builtin の性質(常に動く)に反する。
# 向き先 env(SANDBOX_URL)が設定されている配備でだけ BUILTIN_TOOLS に載せ、
# 未設定なら定義そのものが無い扱いにする — 撤去時の後片付けは下の prune
# (`builtin_` prefix かつ BUILTIN_TOOLS 未定義の行を削除)がそのまま担う。
# 帰結として **env を外すと access_control 設定は失われる**(行ごと消えるため)。
# 再設定は運用の手間だが、専用の無効化状態を DB に持つより素直と判断した
# (2026-09-03)。
#
# **共有トークン(SANDBOX_TOKEN)も条件に含める**(#132、2026-09-04): sandbox API は
# 全リクエストに `Authorization: Bearer` を要求し、未設定側からの呼び出しは必ず 401 に
# なる。URL だけ設定された配備で登録すると、まさにこのゲートが避けたい「一覧に生えて
# いるのに呼ぶと必ずエラー」になる。**トークンだけ設定して URL が無い場合は従来通り
# 非登録**(sandbox の居ない配備)。原因が読めるよう、URL はあるのにトークンが無い
# ケースだけ sync 時に log.error を出す(下の sync_builtin_tools)。
if sandbox.SANDBOX_URL and sandbox.SANDBOX_TOKEN:
    BUILTIN_TOOLS["builtin_sandbox"] = {
        "module": sandbox,
        "name": "Sandbox",
        "description": (
            "Run Python / shell in a server-side isolated environment (nsjail) "
            "with a persistent /workspace "
            "(run_python / run_python_once / run_shell / send_to_workspace / "
            "write_file / list_workspace / fetch_from_workspace)"
        ),
    }

# 「URL はあるのにトークンが無い」= 配備の設定漏れ。import 時点ではまだ logging の設定が
# 済んでいない可能性があるので、フラグだけ立てて sync_builtin_tools() で吐く(起動ログに
# 必ず載る)。tool が黙って消えると原因が読めないため、これが唯一の手掛かりになる。
_SANDBOX_TOKEN_MISSING = bool(sandbox.SANDBOX_URL) and not sandbox.SANDBOX_TOKEN


def is_builtin_tool_id(tool_id: str) -> bool:
    """現に登録されている builtin か(update/delete 保護・ロード分岐用)。"""
    return tool_id in BUILTIN_TOOLS


def is_reserved_builtin_id(tool_id: str) -> bool:
    """builtin 用に予約された id 名前空間か(create 拒否用)。
    未定義でも `builtin_` prefix は一律予約 — ユーザーに shadow させない。"""
    return tool_id.startswith(BUILTIN_ID_PREFIX)


def load_builtin_tool(tool_id: str):
    """plugin.load_tool_module_by_id と同じ外形(Tools インスタンス, frontmatter)。"""
    return BUILTIN_TOOLS[tool_id]["module"].Tools(), {}


def sync_builtin_tools() -> None:
    """起動時に builtin 行を Tools テーブルへ同期する(upsert + prune)。

    - 定義済み: specs / name / description を毎回 repo から再生成(コード更新の反映)。
      access_control は admin の運用設定なので既存値を保持する。
    - 未定義: `builtin_` prefix かつ BUILTIN_TOOLS に居ない行は DB から削除する。
      定義の真実源は常に repo なので orphan を残さない(builtin を削除したら次回起動で
      DB からも消える)。**env ゲートが閉じた builtin もここに落ちる** —
      builtin_sandbox は SANDBOX_URL / SANDBOX_TOKEN のどちらかを外して再起動すると
      同じ経路で消える。
    """
    if _SANDBOX_TOKEN_MISSING:
        log.error(
            "builtin_sandbox を登録しません: SANDBOX_URL は設定されていますが "
            "SANDBOX_TOKEN が未設定です。sandbox API は共有トークン必須で "
            "(sandbox 側もトークン無しでは起動しません)、このままでは全呼び出しが "
            "401 になります。sandbox コンテナと同じ値を nucllei にも SANDBOX_TOKEN "
            "として渡してください"
        )

    # 循環 import 回避(utils.tools → utils.plugin → builtin_tools)のため遅延 import
    from nucllei.models.tools import ToolForm, ToolMeta, Tools
    from nucllei.utils.tools import get_tool_specs

    # 不変条件: 全 builtin id は予約 prefix を持つ。破れると prune(prefix 基準)が
    # 撤去時に orphan を掃除できなくなる(#89 review nit)。開発時に早期検知する。
    _bad = [k for k in BUILTIN_TOOLS if not k.startswith(BUILTIN_ID_PREFIX)]
    if _bad:
        raise ValueError(
            f"builtin id は '{BUILTIN_ID_PREFIX}' 始まりが必須(prune 前提): {_bad}"
        )

    for tool_id, entry in BUILTIN_TOOLS.items():
        try:
            specs = get_tool_specs(entry["module"].Tools())
            existing = Tools.get_tool_by_id(tool_id)
            if existing is None:
                Tools.insert_new_tool(
                    # 所有者は実ユーザーでない固定値(操作権は admin ロールで担保)
                    "system",
                    ToolForm(
                        id=tool_id,
                        name=entry["name"],
                        description=entry["description"],
                        content="",
                        meta=ToolMeta(builtin=True),
                        access_control=None,  # 既定 public read(露出は紐づけで制御)
                    ),
                    specs,
                )
                log.info(f"builtin tool registered: {tool_id}")
            else:
                Tools.update_tool_by_id(
                    tool_id,
                    {
                        "name": entry["name"],
                        "description": entry["description"],
                        "content": "",
                        "specs": specs,
                        "meta": {"builtin": True},
                    },
                )
        except Exception as e:
            log.error(f"builtin tool sync failed: {tool_id}: {e!r}")

    # 現定義に無い builtin 行を prune。予約 prefix を持つのに BUILTIN_TOOLS に居ない
    # ものは DB に残しておく理由が無い(旧 builtin_view_image 等の orphan 掃除)。
    # 理由は 2 通りあり、**ログでは区別できない**: コード側で撤去された(定義ごと消えた)か、
    # env ゲートが閉じた(builtin_sandbox の SANDBOX_URL 未設定)か。後者は配備の設定変更で
    # 日常的に起こりうるので、「repo から消えた」と断定する文言にはしない。
    try:
        for tool in Tools.get_tools():
            if tool.id.startswith(BUILTIN_ID_PREFIX) and tool.id not in BUILTIN_TOOLS:
                Tools.delete_tool_by_id(tool.id)
                log.info(f"builtin tool pruned (not defined in this build): {tool.id}")
    except Exception as e:
        log.error(f"builtin tool prune failed: {e!r}")
