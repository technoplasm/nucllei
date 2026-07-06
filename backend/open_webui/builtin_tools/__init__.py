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

from open_webui.builtin_tools import history

log = logging.getLogger(__name__)

# tool_id → 定義。id は `builtin_` prefix でユーザー作成 tool と衝突させない
BUILTIN_TOOLS = {
    "builtin_history": {
        "module": history,
        "name": "履歴管理",
        "description": (
            "自分のチャット履歴の一覧・読み出し・検索"
            "(list_history / read_history / grep_history)。"
            "※現在はスタブ(#42、本実装は後続)"
        ),
    },
}


def is_builtin_tool_id(tool_id: str) -> bool:
    return tool_id in BUILTIN_TOOLS


def load_builtin_tool(tool_id: str):
    """plugin.load_tool_module_by_id と同じ外形(Tools インスタンス, frontmatter)。"""
    return BUILTIN_TOOLS[tool_id]["module"].Tools(), {}


def sync_builtin_tools() -> None:
    """起動時に builtin 行を Tools テーブルへ upsert する。

    specs / name / description は毎回 repo から再生成(コード更新の反映)。
    access_control は admin の運用設定なので既存値を保持する。
    """
    # 循環 import 回避(utils.tools → utils.plugin → builtin_tools)のため遅延 import
    from open_webui.models.tools import ToolForm, ToolMeta, Tools
    from open_webui.utils.tools import get_tool_specs

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
                        content="",
                        meta=ToolMeta(description=entry["description"], builtin=True),
                        access_control=None,  # 既定 public read(露出は紐づけで制御)
                    ),
                    specs,
                )
                log.info(f"builtin tool registered: {tool_id}")
            else:
                meta = {
                    **(existing.meta.model_dump() if existing.meta else {}),
                    "description": entry["description"],
                    "builtin": True,
                }
                Tools.update_tool_by_id(
                    tool_id,
                    {
                        "name": entry["name"],
                        "content": "",
                        "specs": specs,
                        "meta": meta,
                    },
                )
        except Exception as e:
            log.error(f"builtin tool sync failed: {tool_id}: {e!r}")
