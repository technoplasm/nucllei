"""組み込み Function(#52 プランB)。

リポジトリのコードで定義され、Function テーブルに builtin 行として起動時 sync される。
カスタム Function と同列(型ごとの割り当て・valves・ロード機構がそのまま効く)で、
違いは content が DB でなく repo モジュールから来ること。builtin tool(#18,
`builtin_tools/`)の Function 版。

Function テーブルは #45/#43 で `meta` 列ごと撤去済み → tool の `meta.builtin` フラグは
使えない。**builtin 判別は id 接頭辞 `builtin_` 一本**(この BUILTIN_FUNCTIONS dict が
ロードの真実源)。content 更新・削除は将来 routers 側で拒否する(定義の真実源は常に repo)。
"""

import logging

from open_webui.builtin_functions import attachment, large_file_gate

log = logging.getLogger(__name__)

# id → 定義。id は `builtin_` prefix でユーザー作成 Function と衝突させない。
# type は plugin の class 名判定と一致させる(Attachment class → "attachment")。
BUILTIN_FUNCTIONS = {
    "builtin_attachment": {
        "module": attachment,
        "class_name": "Attachment",
        "type": "attachment",
        "name": "添付ファイル処理(既定)",
        "description": (
            "アップロードされたファイルをテキスト化する既定フック。"
            "content_type で STT / docling 抽出 / 素通しを振り分ける"
            "(判定リストは valves で設定可能)。"
        ),
    },
    "builtin_large_file_gate": {
        "module": large_file_gate,
        "class_name": "Filter",
        "type": "filter",
        "name": "注入 gate(大容量ファイル)",
        "description": (
            "添付の抽出テキストを実測し、合算 context 予算を超える分を大きい順に "
            "context から除去して file_id を告知する inlet filter。"
            "モデルへの割り当て(filterIds)で適用。"
        ),
    },
}


def is_builtin_function_id(function_id: str) -> bool:
    return function_id in BUILTIN_FUNCTIONS


def load_builtin_function(function_id: str):
    """plugin.load_function_module_by_id と同じ外形(インスタンス, type, frontmatter)。"""
    entry = BUILTIN_FUNCTIONS[function_id]
    instance = getattr(entry["module"], entry["class_name"])()
    return instance, entry["type"], {}


def sync_builtin_functions() -> None:
    """起動時に builtin 行を Function テーブルへ upsert する。

    name / description / type は毎回 repo から再生成(コード更新の反映)。content は空
    (実体は repo モジュール)。valves は admin の運用設定なので既存値を保持する。
    """
    # 循環 import 回避のため遅延 import
    from open_webui.models.functions import FunctionForm, Functions

    for function_id, entry in BUILTIN_FUNCTIONS.items():
        try:
            existing = Functions.get_function_by_id(function_id)
            if existing is None:
                Functions.insert_new_function(
                    # 所有者は実ユーザーでない固定値(操作権は admin ロールで担保)
                    "system",
                    entry["type"],
                    FunctionForm(
                        id=function_id,
                        name=entry["name"],
                        description=entry["description"],
                        content="",
                    ),
                )
                log.info(f"builtin function registered: {function_id}")
            else:
                Functions.update_function_by_id(
                    function_id,
                    {
                        "name": entry["name"],
                        "description": entry["description"],
                        "type": entry["type"],
                        "content": "",
                    },
                )
        except Exception as e:
            log.error(f"builtin function sync failed: {function_id}: {e!r}")
