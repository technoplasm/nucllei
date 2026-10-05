import asyncio
import inspect
import logging

from nucllei.utils.plugin import load_function_module_by_id
from nucllei.models.functions import Functions
from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


def _function_modules(request=None) -> dict:
    """function モジュールのキャッシュ(app.state.FUNCTIONS)。

    get_sorted_filter_ids は呼び出し側(middleware / chat)の外形を変えないため
    request を任意にしている。無ければ本体 app のキャッシュを使う(実行時の
    process_filter_functions と同じインスタンスを共有する)。
    """
    if request is not None:
        return request.app.state.FUNCTIONS
    from nucllei.main import app

    return app.state.FUNCTIONS


def get_filter_priority(function_id: str, request=None) -> int:
    """filter の priority = `Valves(**保存済み valves).priority`。

    保存値が無いフィールドはコード側の既定値で埋まる(保存は明示した
    フィールドだけ — routers/functions.py)。旧実装は保存 dict を直接読み、
    未保存の filter はコード既定を無視して一律 0 になっていた。
    Valves を持たない / 読めない filter は 0。
    """
    modules = _function_modules(request)
    module = modules.get(function_id)
    if module is None:
        try:
            module, _, _ = load_function_module_by_id(function_id)
        except Exception as e:
            log.exception(f"filter {function_id}: load failed; priority=0: {e}")
            return 0
        modules[function_id] = module

    Valves = getattr(module, "Valves", None)
    if Valves is None:
        return 0
    try:
        valves = Valves(**(Functions.get_function_valves_by_id(function_id) or {}))
    except Exception as e:
        log.exception(f"filter {function_id}: invalid valves; priority=0: {e}")
        return 0
    priority = getattr(valves, "priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, (int, float)):
        return 0
    return priority


def get_sorted_filter_ids(model: dict, request=None):
    # llens(#46): global/active 撤去 → filter はモデルの filterIds 割り当てのみで適用。
    # 重複は先勝ちで除き、並びは filterIds の順を保つ(旧 list(set(...)) は
    # PYTHONHASHSEED 依存で、同 priority の実行順が起動ごとに変わっていた)。
    filter_ids = []
    if "info" in model and "meta" in model["info"]:
        filter_ids = list(dict.fromkeys(model["info"]["meta"].get("filterIds") or []))

    # 実在する filter type の function だけに絞る(削除済み ID を弾く)。
    filter_function_ids = {
        function.id for function in Functions.get_functions_by_type("filter")
    }
    filter_ids = [fid for fid in filter_ids if fid in filter_function_ids]

    # priority 昇順。sort は安定なので同 priority は filterIds の並び順のまま。
    priorities = {fid: get_filter_priority(fid, request) for fid in filter_ids}
    filter_ids.sort(key=lambda fid: priorities[fid])
    return filter_ids


async def process_filter_functions(
    request, filter_functions, filter_type, form_data, extra_params
):
    for function in filter_functions:
        filter = function
        filter_id = function.id
        if not filter:
            continue

        if filter_id in request.app.state.FUNCTIONS:
            function_module = request.app.state.FUNCTIONS[filter_id]
        else:
            # llens(#28): 初回ロードは exec + DB 書き込みを伴うため to_thread でループ外へ
            function_module, _, _ = await asyncio.to_thread(
                load_function_module_by_id, filter_id
            )
            request.app.state.FUNCTIONS[filter_id] = function_module

        # Prepare handler function
        handler = getattr(function_module, filter_type, None)
        if not handler:
            continue

        # Apply valves to the function
        if hasattr(function_module, "valves") and hasattr(function_module, "Valves"):
            valves = Functions.get_function_valves_by_id(filter_id)
            function_module.valves = function_module.Valves(
                **(valves if valves else {})
            )

        try:
            # Prepare parameters
            sig = inspect.signature(handler)

            params = {"body": form_data}
            if filter_type == "stream":
                params = {"event": form_data}

            params = params | {
                k: v
                for k, v in {
                    **extra_params,
                    "__id__": filter_id,
                }.items()
                if k in sig.parameters
            }

            # __user__ は filter ごとの浅いコピー。UserValves は撤去済み(2026-10-05、
            # feature-triage「Filter / tool 機構の縮小」)— 呼び手の dict を filter が
            # 書き換えても後続の filter / tool / pipe に漏れないよう、共有 dict は渡さない。
            # pipe 由来などで "valves" キーが混ざっていても落とす。
            if isinstance(params.get("__user__"), dict):
                params["__user__"] = {
                    k: v for k, v in params["__user__"].items() if k != "valves"
                }

            # Execute handler
            # llens(#28): sync handler はワーカースレッドで実行(sync tool と同じ扱い)。
            # 遅い Filter がイベントループ(全ユーザーのストリーミング)を止めないように。
            if inspect.iscoroutinefunction(handler):
                form_data = await handler(**params)
            else:
                form_data = await asyncio.to_thread(handler, **params)

        except Exception as e:
            log.debug(f"Error in {filter_type} handler {filter_id}: {e}")
            raise e

    # `file_handler = True`(filter が添付を丸ごと引き取り組み込み注入を止める)は
    # 撤去済み(2026-10-05)。添付を間引きたい filter は inlet で body["files"] /
    # metadata["files"] を自分で書き換える(llens large_file_gate の方式)。
    return form_data, {}
