import logging
import math
import re
from datetime import datetime
from typing import Optional


from nucllei.utils.misc import get_last_user_message, get_messages_content

from nucllei.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


def get_task_model_id(
    default_model_id: str, task_model: str, task_model_external: str, models
) -> str:
    # Set the task model
    task_model_id = default_model_id
    # Check if the user has a custom task model and use that model
    if models[task_model_id].get("owned_by") == "ollama":
        if task_model and task_model in models:
            task_model_id = task_model
    else:
        if task_model_external and task_model_external in models:
            task_model_id = task_model_external

    return task_model_id


def prompt_variables_template(template: str, variables: dict[str, str]) -> str:
    for variable, value in variables.items():
        template = template.replace(variable, value)
    return template


def resolve_base_model_id(model: Optional[dict]) -> Optional[str]:
    """model dict(app.state.MODELS の形)から {{BASE_MODEL_ID}} の値を引く。

    実際に推論する base model の id(preset なら info.base_model_id、base を持たない
    素のモデルならそのモデル自身の id)。取れなければ None(prompt_template が
    "Unknown" に落とす)。
    """
    model = model or {}
    info = model.get("info") or {}
    return info.get("base_model_id") or model.get("id")


def _group_name_sort_key(name: str) -> tuple[str, str]:
    """{{USER_GROUPS}} の並び: casefold した名前 → 元の名前。

    大文字小文字の揺れ(`Lab` / `lab` / `LAB`)で順序が DB の返し順に左右されないよう、
    まず casefold で比べ、同じなら元の文字列(コードポイント順)で決める — 全順序なので安定。
    """
    return (name.casefold(), name)


def user_group_names(user_id: Optional[str]) -> list[str]:
    """{{USER_GROUPS}} の元: user が**所属している**グループの名前(保存値そのまま)。

    admin でも所属している物だけ(全グループではない)。並びは _group_name_sort_key。
    get_groups_by_member_id は user_ids の文字列 LIKE なので、実際の所属を user_ids で
    確かめ直す(utils/ext_acl.member_group_names と同じ方式)。user_id が無ければ []。
    """
    if not user_id:
        return []
    from nucllei.models.groups import Groups

    names = [
        g.name
        for g in Groups.get_groups_by_member_id(user_id)
        if user_id in (g.user_ids or []) and g.name
    ]
    return sorted(names, key=_group_name_sort_key)


def user_prompt_params(user) -> dict:
    """prompt_template に渡すユーザー由来の引数(UserModel / {"name", …} の dict / None)。

    {{USER_NAME}} / {{USER_LOCATION}} / {{USER_ROLE}} / {{USER_GROUPS}} の出どころを
    展開箇所(preset の system prompt・skill・続きターンの wire・タイトル生成)で揃える。
    location は UserModel なら info.location、dict なら "location" キー。
    """
    if user is None:
        return {}
    if isinstance(user, dict):
        get = user.get
        location = get("location")
    else:

        def get(key):
            return getattr(user, key, None)

        info = get("info")
        location = info.get("location") if isinstance(info, dict) else None
    return {
        "user_name": get("name"),
        "user_location": location,
        "user_role": get("role"),
        "user_id": get("id"),
    }


# datetime.weekday() の順(月曜 = 0)
_WEEKDAYS_JA = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")


def prompt_template(
    template: str,
    user_name: Optional[str] = None,
    user_location: Optional[str] = None,
    base_model_id: Optional[str] = None,
    user_role: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """サーバー既知のプロンプト変数を展開する(変数の一覧はここが単一情報源)。

    {{USER_ROLE}} は role の値そのまま(不明なら "Unknown")。{{USER_GROUPS}} は
    user_group_names(user_id) を ", " でつないだもの(所属なし・user 不明なら空文字列)。
    グループの DB 照会は {{USER_GROUPS}} が本文にあるときだけ、1 呼び出しにつき 1 回。
    """
    # Get the current date
    current_date = datetime.now()

    # Format the date to YYYY-MM-DD
    formatted_date = current_date.strftime("%Y-%m-%d")
    formatted_time = current_date.strftime("%I:%M:%S %p")
    # 曜日は日本語で固定(2026-10-19 llens 監査 — strftime("%A") はプロセスのロケール次第で
    # 英語になっていた。モデル向けの文は日本語 — _memos/model-frames.md)
    formatted_weekday = _WEEKDAYS_JA[current_date.weekday()]

    template = template.replace("{{CURRENT_DATE}}", formatted_date)
    template = template.replace("{{CURRENT_TIME}}", formatted_time)
    template = template.replace(
        "{{CURRENT_DATETIME}}", f"{formatted_date} {formatted_time}"
    )
    template = template.replace("{{CURRENT_WEEKDAY}}", formatted_weekday)

    if user_name:
        # Replace {{USER_NAME}} in the template with the user's name
        template = template.replace("{{USER_NAME}}", user_name)
    else:
        # Replace {{USER_NAME}} in the template with "Unknown"
        template = template.replace("{{USER_NAME}}", "Unknown")

    if user_location:
        # Replace {{USER_LOCATION}} in the template with the current location
        template = template.replace("{{USER_LOCATION}}", user_location)
    else:
        # Replace {{USER_LOCATION}} in the template with "Unknown"
        template = template.replace("{{USER_LOCATION}}", "Unknown")

    # 実際に推論する base model の id(値の引き方は resolve_base_model_id)。
    # 取れなければ "Unknown"
    template = template.replace("{{BASE_MODEL_ID}}", base_model_id or "Unknown")

    template = template.replace("{{USER_ROLE}}", user_role or "Unknown")
    if "{{USER_GROUPS}}" in template:
        template = template.replace(
            "{{USER_GROUPS}}", ", ".join(user_group_names(user_id))
        )

    return template


def replace_prompt_variable(template: str, prompt: str) -> str:
    def replacement_function(match):
        full_match = match.group(
            0
        ).lower()  # Normalize to lowercase for consistent handling
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)

        if full_match == "{{prompt}}":
            return prompt
        elif start_length is not None:
            return prompt[: int(start_length)]
        elif end_length is not None:
            return prompt[-int(end_length) :]
        elif middle_length is not None:
            middle_length = int(middle_length)
            if len(prompt) <= middle_length:
                return prompt
            start = prompt[: math.ceil(middle_length / 2)]
            end = prompt[-math.floor(middle_length / 2) :]
            return f"{start}...{end}"
        return ""

    # Updated regex pattern to make it case-insensitive with the `(?i)` flag
    pattern = r"(?i){{prompt}}|{{prompt:start:(\d+)}}|{{prompt:end:(\d+)}}|{{prompt:middletruncate:(\d+)}}"
    template = re.sub(pattern, replacement_function, template)
    return template


def replace_messages_variable(
    template: str, messages: Optional[list[dict]] = None
) -> str:
    def replacement_function(match):
        full_match = match.group(0)
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)
        # If messages is None, handle it as an empty list
        if messages is None:
            return ""

        # Process messages based on the number of messages required
        if full_match == "{{MESSAGES}}":
            return get_messages_content(messages)
        elif start_length is not None:
            return get_messages_content(messages[: int(start_length)])
        elif end_length is not None:
            return get_messages_content(messages[-int(end_length) :])
        elif middle_length is not None:
            mid = int(middle_length)

            if len(messages) <= mid:
                return get_messages_content(messages)
            # Handle middle truncation: split to get start and end portions of the messages list
            half = mid // 2
            start_msgs = messages[:half]
            end_msgs = messages[-half:] if mid % 2 == 0 else messages[-(half + 1) :]
            formatted_start = get_messages_content(start_msgs)
            formatted_end = get_messages_content(end_msgs)
            return f"{formatted_start}\n{formatted_end}"
        return ""

    template = re.sub(
        r"{{MESSAGES}}|{{MESSAGES:START:(\d+)}}|{{MESSAGES:END:(\d+)}}|{{MESSAGES:MIDDLETRUNCATE:(\d+)}}",
        replacement_function,
        template,
    )

    return template


# {{prompt:middletruncate:8000}}


def title_generation_template(template: str, messages: list[dict], user=None) -> str:
    """user は UserModel / {"name", "location", …} の dict / None(user_prompt_params)。"""
    prompt = get_last_user_message(messages)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages)

    template = prompt_template(template, **user_prompt_params(user))

    return template
