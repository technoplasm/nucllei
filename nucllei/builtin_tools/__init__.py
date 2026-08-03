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

from nucllei.builtin_tools import chat_files, history

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
}


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
    - 撤去済み: コードから消えた builtin 行(`builtin_` prefix かつ BUILTIN_TOOLS 未定義)は
      DB から削除する。定義の真実源は常に repo なので orphan を残さない
      (builtin を削除したら次回起動で DB からも消える)。
    """
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

    # repo から消えた builtin 行を prune。予約 prefix を持つのに現定義に無いものは
    # コード側で撤去された証拠なので DB から削除する(旧 builtin_view_image 等の orphan 掃除)。
    try:
        for tool in Tools.get_tools():
            if tool.id.startswith(BUILTIN_ID_PREFIX) and tool.id not in BUILTIN_TOOLS:
                Tools.delete_tool_by_id(tool.id)
                log.info(f"builtin tool pruned (removed from repo): {tool.id}")
    except Exception as e:
        log.error(f"builtin tool prune failed: {e!r}")
