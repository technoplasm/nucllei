"""`/` でレシピを呼ぶ (入力欄の `/name`)。

利用者が今の発言の先頭行の行頭に `/name` と書くと、そのレシピの load_recipe を「呼んだことにする」—
skill の `/command` (utils/skills.apply_skill_command) と同じく、発言の直後に assistant の
load_recipe 呼び出し + その結果を入れ、同じ往復を応答の先頭に tool_calls ブロックとして残す
(次のリクエストからは履歴の load_recipe 往復として届く)。結果は本物の load_recipe (このターンの
tools_dict の物を、モデルの call と同じ execute_tool_calls で) が作る — 読める範囲・壊れている・警告・
読み込み済みの判定は load_recipe のまま。

名前の決め方 (入力欄の補完 `src/lib/utils/slash-commands.ts` と同じ規則):

- skill が先: name 全体が skill の id と一致し、その skill が発火する (モデルに紐づく ∧ 見える ∧ 機能
  「スキル」ON) なら skill (utils/skills.command_skill)。同じ名前のレシピはどの置き場でも使われない。
  前方一致では当てない (`/emr抽出` はレシピ「emr抽出」、`/emr` と `/emr 抽出して` は skill emr)
- レシピ同士は狭い置き場が勝つ: 個人 → グループ (グループ名の順) → 全体
- 勝った物が壊れていれば何も呼ばない (広い置き場の同じ名前のレシピに回さない)
- 候補は見えるレシピの一覧 (recipe_index — 個人・所属グループ・全体。管理者でも所属分だけ)。
  名前に空白のあるレシピは `/` では呼べない (名前は空白までの 1 語)

能力「レシピ」が OFF・sandbox の無い配備では何もしない (sandbox にも聞かない)。一覧を取るのは
今の発言が `/name` で始まり、skill に当たらないときだけ (このリクエストの一覧は get_user_recipe_listing
が持ち回る — load_recipe の説明・会話の前置きと 1 回で済む)。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from nucllei.utils.skills import (
    command_name,
    command_skill,
    command_skill_ids,
    message_text,
)

log = logging.getLogger(__name__)

# 置き場の狭い順
PLACE_ORDER = {"personal": 0, "group": 1, "public": 2}
# 合成の呼び出しの id (モデル由来の call と衝突しない自前の形)
RECIPE_CALL_ID = "recipe:{idx}"


def command_token(text) -> Optional[str]:
    """`text` の先頭行の行頭の `/name` の name (`/` から空白・行末まで。NFC — skills.command_name)。"""
    return command_name(text)


def _field(recipe, key: str):
    return recipe.get(key) if isinstance(recipe, dict) else getattr(recipe, key, None)


def precedence(recipe) -> tuple:
    """同じ名前のレシピのどれが使われるかの順 (小さい方が勝つ)。グループ同士はグループ名の順
    (所属グループの並び — recipe_index.member_groups と同じ鍵)。"""
    group = _field(recipe, "group") or ""
    return (
        PLACE_ORDER.get(_field(recipe, "place"), len(PLACE_ORDER)),
        group.casefold(),
        group,
    )


def resolve_recipe_command(recipes, name: str):
    """`/name` で呼ぶレシピ (無ければ None)。skill に当たるかは呼び手が先に見る。

    名前の一致は完全一致 (NFC)。同じ名前が複数の置き場にあれば狭い置き場の物、それが壊れて
    いれば None。"""
    candidates = sorted(
        (r for r in recipes or [] if _field(r, "name") == name), key=precedence
    )
    if not candidates:
        return None
    winner = candidates[0]
    if _field(winner, "broken"):
        return None
    return winner


def _load_recipe_paths(message) -> list[str]:
    """assistant メッセージの load_recipe 呼び出しの path (tool_calls と content_blocks の両方)。"""
    calls = list(message.get("tool_calls") or [])
    for block in message.get("content_blocks") or []:
        if block.get("type") == "tool_calls":
            calls.extend(block.get("content") or [])
    paths = []
    for call in calls:
        function = (call or {}).get("function") or {}
        if function.get("name") != "load_recipe":
            continue
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if isinstance(path, str):
            path = path.rstrip("/")
            if path.endswith("/RECIPE.md"):
                path = path[: -len("/RECIPE.md")]
            paths.append(path)
    return paths


def _fired_after(messages, idx: int, folder: str) -> bool:
    """発言 `idx` の後 (次の user の発言まで) に、そのレシピの load_recipe が既にあるか
    (この `/name` が前のリクエストで発火して応答に残った・モデルが自分で呼んだ)。"""
    for message in messages[idx + 1 :]:
        if message.get("role") == "user":
            break
        if message.get("role") == "assistant" and folder in _load_recipe_paths(message):
            return True
    return False


async def apply_recipe_command(form_data, user, model):
    """今の発言 (最後の user メッセージ) の `/name` でレシピを呼ぶ合成の往復を入れる。

    見るのは今の発言だけ — 前の発言の `/name` は、その発言のターンで発火して応答に残っている
    (requires の shell / python を後のターンで走らせ直さない)。再生成・発言の編集では、その発言が
    また「今の発言」になるので発火し直す。結果の中身は後で run_recipe_activations が入れる
    (load_recipe はこのターンの tools_dict が揃ってから呼ぶ)。

    戻り値は (form_data, 発火リスト)。要素は {call_id, folder, call, result}。
    """
    from nucllei.utils.task import get_user_recipe_listing, recipes_available

    activations: list = []
    if not recipes_available(model):
        return form_data, activations
    messages = form_data.get("messages") or []
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_idxs:
        return form_data, activations
    idx = user_idxs[-1]
    text = message_text(messages[idx])
    name = command_token(text)
    if name is None:
        return form_data, activations
    # skill が先 (同じ `/name` で発火する skill があればレシピは呼ばない)
    if command_skill(text, user, command_skill_ids(model)) is not None:
        return form_data, activations

    listing = await get_user_recipe_listing(user)
    recipe = resolve_recipe_command(listing.entries, name)
    if recipe is None:
        return form_data, activations
    folder = recipe.folder
    if _fired_after(messages, idx, folder):
        return form_data, activations

    call_id = RECIPE_CALL_ID.format(idx=idx)
    call = {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "load_recipe",
            "arguments": json.dumps({"path": folder}, ensure_ascii=False),
        },
    }
    exchange = [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call_id, "content": ""},
    ]
    form_data["messages"] = messages[: idx + 1] + exchange + messages[idx + 1 :]
    activations.append(
        {
            "call_id": call_id,
            "folder": folder,
            "call": call,
            "result": None,
        }
    )
    return form_data, activations


def _result_message(messages, call_id: str):
    for message in messages or []:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            return message
    return None


async def run_recipe_activations(activations, messages, execute) -> None:
    """発火した load_recipe を実行して結果を入れる (このリクエストの messages の結果と、応答に残す結果)。

    `messages` は今の form_data["messages"] — 結果の置き場は call id で引き直す (inlet が messages を
    作り直していても、その中の往復に入れる)。往復が消えていれば (inlet が落とした) 実行も応答への保存も
    しない (モデルが見ていない往復を残さない)。`execute(calls)` は middleware.execute_tool_calls
    (モデルの call と同じ関数 — load_recipe が無い・失敗したときもその ERROR が結果になる)。
    process_chat_payload が能力の tool を載せた後に呼ぶ。"""
    for activation in activations:
        target = _result_message(messages, activation["call_id"])
        if target is None:
            log.warning(
                f"[recipes] /name の load_recipe の往復が messages に無い (inlet が作り直した): "
                f"{activation['folder']}"
            )
            continue
        result = (await execute([activation["call"]]))[0] or {}
        result = {**result, "tool_call_id": activation["call_id"]}
        activation["result"] = result
        target["content"] = str(result.get("content") or "")


def recipe_activation_seeds(activations) -> list:
    """応答に残す形 (metadata["recipe_activations"])。"""
    return [
        {"call": a["call"], "result": a["result"]}
        for a in activations
        if a.get("result") is not None
    ]


def recipe_activation_seed_calls(seeds) -> tuple[list, list]:
    """応答の先頭の tool_calls ブロックに足す (呼び出し, 結果)。"""
    return [s["call"] for s in seeds], [s["result"] for s in seeds]
