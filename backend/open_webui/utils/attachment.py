"""Attachment Function 種別のディスパッチ(#52 プランB)。

アップロード直後に発火する。`utils/filter.py` の filter ディスパッチと同じ流儀
(型で絞る → priority ソート → valves 注入 → 署名インスペクションで引数注入)。
違いは upload が sync 経路なので同期呼び出しであること。

**fork は仕組みだけ**: Attachment の実体(docling ルータ等のポリシー)は案件層(prism)の
authored Function として登録される。fork 本体は builtin Attachment を持たない。

**適用は完全 opt-in(tool/filter と同一)**: モデルの `meta.attachmentIds` に割り当てられた
attachment function だけが発火する。model 文脈の無い upload(API/KB など)や未割り当ての
モデルでは何も走らない。フック本体はルータで、抽出の実体は内部関数(`retrieval.extract`)へ委譲。
"""

import asyncio
import inspect
import logging

from open_webui.models.functions import Functions
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


def get_attachment_function_ids(assigned_ids) -> list[str]:
    """発火する attachment function の id 列(priority 昇順)。

    `assigned_ids` = モデルの `meta.attachmentIds`(完全 opt-in)。そのうち **実在する
    type=attachment の Function** だけに絞る(削除済み ID を弾く)。`get_sorted_filter_ids`
    と同型。
    """
    if not assigned_ids:
        return []

    def get_priority(function_id):
        valves = Functions.get_function_valves_by_id(function_id)
        return valves.get("priority", 0) if valves else 0

    valid = {f.id for f in Functions.get_functions_by_type("attachment")}
    attachment_ids = [fid for fid in dict.fromkeys(assigned_ids) if fid in valid]
    attachment_ids.sort(key=get_priority)
    return attachment_ids


def _load_module(request, function_id):
    if function_id in request.app.state.FUNCTIONS:
        return request.app.state.FUNCTIONS[function_id]
    module, _, _ = load_function_module_by_id(function_id)
    request.app.state.FUNCTIONS[function_id] = module
    return module


def process_attachment_functions(request, file, user, assigned_ids=None):
    """アップロードされた 1 file を、モデルに割り当てられた attachment function で順に処理する。

    `assigned_ids` = そのモデルの `meta.attachmentIds`(None/空なら何もしない=opt-in)。
    各フックが file.data.content を埋める(実体は内部関数へ委譲)。upload endpoint は
    sync 経路なので同期実行。coroutine ハンドラは threadpool 内で asyncio.run で回す
    (非同期実行+プレビューは後続フェーズ)。戻り値は最終的な file。
    """
    for function_id in get_attachment_function_ids(assigned_ids):
        module = _load_module(request, function_id)

        handler = getattr(module, "process", None)
        if not handler:
            continue

        # valves 注入(filter と同型)
        if hasattr(module, "valves") and hasattr(module, "Valves"):
            valves = Functions.get_function_valves_by_id(function_id)
            module.valves = module.Valves(**(valves if valves else {}))

        sig = inspect.signature(handler)
        params = {"file": file}
        params = params | {
            k: v
            for k, v in {
                "__request__": request,
                "__user__": user,
                "__id__": function_id,
            }.items()
            if k in sig.parameters
        }

        try:
            if inspect.iscoroutinefunction(handler):
                file = asyncio.run(handler(**params)) or file
            else:
                file = handler(**params) or file
        except Exception as e:
            log.exception(f"Error in attachment handler {function_id}: {e}")
            raise e

    return file
