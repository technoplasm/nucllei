"""Attachment Function 種別のディスパッチ(#52 プランB / #69 グローバル化)。

アップロード直後(`__event__="upload"`)と、既存ファイルのチャット再添付時
(`__event__="attach"`、`POST /files/{id}/process`)に発火する。`utils/filter.py` の
filter ディスパッチと同じ流儀(型で絞る → valves 注入 → 署名インスペクションで引数注入)。
**非同期**: docling 等の遅い抽出をフック内で待っても event loop をブロックしないよう、
coroutine は await、同期ハンドラは threadpool にオフロードする。

**fork は仕組みだけ**: Attachment の実体(docling ルータ等のポリシー)は案件層(prism)の
authored Function として登録される。fork 本体は builtin Attachment も抽出コードも持たない。
docling でない「素の」md 化が要るときは、fork が同梱する **markitdown** を prism の `process(file)`
内で直接 import して使う(`from markitdown import MarkItDown` → `.convert(path).text_content`)。
xlsx→シート別 CSV 等の型別ポリシーも prism が手前で処理する(fork は関与しない)。

**契約**: `process(file, ...) -> str | None` の純関数。3 帰結:
- **成功=抽出テキスト(md)を返す** → `content=<str>` `status="success"`。
- **失敗=raise** → `content=str(e)` `status="failed"`(システムが catch)。
- **skip=None を返す** → **何も永続化しない**(この関数はこのファイルを扱わない)。
  = **Attachment 未設定と同じ挙動**(未抽出/none)。拡張子 whitelist 外など「自分の対象外」を
  失敗と区別して素通しするための帰結。再添付(`__event__="attach"`)では「抽出済みなので
  再抽出しない」判断にも使える(None を返せば既存の `data.content` はそのまま残る)。
`content`/`status` の**永続化は core が return 値/例外から行う**(attachment は DB を触らない)。
未抽出(none)= Attachment 未設定、または skip(None)。

**適用はグローバル config(#69)**: admin が `ATTACHMENT_FUNCTION_ID` に 1 つ選んだ
attachment function だけが発火する(複数同時有効は content 上書き衝突のため不可)。
per-model の `meta.attachmentIds` 割り当ては廃止した。未設定なら何も走らない。
"""

import inspect
import logging

from fastapi.concurrency import run_in_threadpool

from open_webui.models.functions import Functions
from open_webui.models.files import Files
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


def get_attachment_function_id(request) -> str | None:
    """発火する attachment function の id(グローバル config、#69)。

    `ATTACHMENT_FUNCTION_ID` が空(未設定)、実在しない、または type が attachment
    でない場合は None(何も発火しない)。削除済み id が config に残っても黙って不発に
    なるだけで壊れない(保存時検証は routers/configs.py が担う)。
    """
    function_id = request.app.state.config.ATTACHMENT_FUNCTION_ID
    if not function_id:
        return None
    function = Functions.get_function_by_id(function_id)
    if not function or function.type != "attachment":
        return None
    return function_id


def _load_module(request, function_id):
    if function_id in request.app.state.FUNCTIONS:
        return request.app.state.FUNCTIONS[function_id]
    module, _, _ = load_function_module_by_id(function_id)
    request.app.state.FUNCTIONS[function_id] = module
    return module


async def process_attachment_functions(request, file, user, event="upload"):
    """1 file をグローバル設定の attachment function で処理する(#69)。

    `ATTACHMENT_FUNCTION_ID` 未設定なら何もしない。`event` は署名に `__event__` を宣言した
    フックにだけ注入される発火文脈:
      - `"upload"` = 新規アップロード直後
      - `"attach"` = 既存ファイルのチャット再添付(`POST /files/{id}/process`)。
        再抽出するか否かはフック側が判断できる(不要なら None を返せば data は不変)。
    `__event__` 未宣言の既存フックは従来どおり全 event で動く。

    フックは `process(file, ...) -> str`(抽出テキスト)を返すか raise する。core が結果を
    `file.data` に永続化する:
      - 成功(str 返却) → `{content: <str>, status: "success"}`
      - 失敗(raise)   → `{content: str(e), status: "failed"}`(例外メッセージをそのまま content に。
        カスタム失敗メッセージは `raise Exception("…")` で入れる。失敗の印は status/注入タグが担う)
      - skip(None 返却) → **何も書かない**(この file は未抽出/none のまま、既存 data も不変)。
    coroutine は await、同期ハンドラは threadpool にオフロード。戻り値は最終 file(再取得済み)。
    """
    function_id = get_attachment_function_id(request)
    if not function_id:
        return file

    module = _load_module(request, function_id)

    handler = getattr(module, "process", None)
    if not handler:
        return file

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
            "__event__": event,
        }.items()
        if k in sig.parameters
    }

    try:
        if inspect.iscoroutinefunction(handler):
            text = await handler(**params)
        else:
            # 同期ハンドラ(blocking docling 等)は threadpool で回し loop を空ける
            text = await run_in_threadpool(lambda: handler(**params))
        if text is None:
            # skip: この関数はこの file を扱わない(attach では「再抽出しない」判断)。
            # 何も永続化しない = 既存の data.content も不変。
            return file
        data = {
            "content": text if isinstance(text, str) else "",
            "status": "success",
        }
    except Exception as e:
        log.exception(f"Error in attachment handler {function_id}: {e}")
        # 関数の失敗 = 例外メッセージをそのまま content に(モデル/UI にそのまま伝わる)。
        # カスタム失敗メッセージは attachment 側で raise Exception("…") すればよい。
        data = {"content": str(e), "status": "failed"}

    Files.update_file_data_by_id(file.id, data)
    return Files.get_file_by_id(file.id) or file
