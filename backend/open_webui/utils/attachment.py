"""Attachment Function 種別のディスパッチ(#52 プランB)。

アップロード直後に発火する。`utils/filter.py` の filter ディスパッチと同じ流儀
(型で絞る → priority ソート → valves 注入 → 署名インスペクションで引数注入)。
違いは (1) upload は sync 経路なので同期呼び出し、(2) builtin(id 接頭辞 `builtin_`)は
DB に content を持たないので `load_builtin_function` でモジュールを得ること。

フック本体はルータで、抽出の実体は内部関数(`retrieval.extract` / audio)に委譲する。
per-model トグル(model.meta)は後続フェーズ。今は builtin を全 upload で発火させる。
"""

import asyncio
import inspect
import logging

from open_webui.models.functions import Functions
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.builtin_functions import (
    BUILTIN_FUNCTIONS,
    is_builtin_function_id,
    load_builtin_function,
)
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


def get_attachment_function_ids() -> list[str]:
    """発火する attachment function の id 列(priority 昇順)。

    フェーズ1: builtin(id 接頭辞 `builtin_`)のみ全 upload で発火。builtin は **コードが
    真実源**なので DB(起動時 sync 行)ではなく `BUILTIN_FUNCTIONS` から直接引く
    (テストや sync 前でも確実に走る)。valves は DB 行があればそこから、無ければ既定値。
    ユーザー authored の per-model 割り当て(model.meta.attachmentIds)は後続フェーズで足す。
    """

    def get_priority(function_id):
        valves = Functions.get_function_valves_by_id(function_id)
        return valves.get("priority", 0) if valves else 0

    attachment_ids = [
        function_id
        for function_id, entry in BUILTIN_FUNCTIONS.items()
        if entry.get("type") == "attachment"
    ]
    attachment_ids.sort(key=get_priority)
    return attachment_ids


def _load_module(request, function_id):
    if function_id in request.app.state.FUNCTIONS:
        return request.app.state.FUNCTIONS[function_id]
    if is_builtin_function_id(function_id):
        module, _, _ = load_builtin_function(function_id)
    else:
        module, _, _ = load_function_module_by_id(function_id)
    request.app.state.FUNCTIONS[function_id] = module
    return module


def process_attachment_functions(request, file, user):
    """アップロードされた 1 file を、登録済み attachment function で順に処理する。

    各フックが file.data.content を埋める(実体は内部関数へ委譲)。upload endpoint は
    sync 経路なので同期実行。coroutine ハンドラは threadpool 内で asyncio.run で回す
    (非同期実行+プレビューは後続フェーズ)。戻り値は最終的な file。
    """
    for function_id in get_attachment_function_ids():
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
