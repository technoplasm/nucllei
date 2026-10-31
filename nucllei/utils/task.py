import asyncio
import logging
import math
import re
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


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


# ブラウザでしか分からない変数。
# チャットの metadata.variables (ブラウザの getPromptVariables) から当てるのはこの 2 つだけで、ほかの
# 変数 (日付・時刻・曜日・利用者) はサーバの値が正 — ブラウザが送ってきても使わない。日時はサーバが作るが、TZ はブラウザの
# {{CURRENT_TIMEZONE}} を使う (request_timezone — 利用者の地域の日付・曜日になるように)
BROWSER_PROMPT_VARIABLES = ("{{CURRENT_TIMEZONE}}", "{{USER_LANGUAGE}}")

# このリクエストの TZ (ブラウザが送った {{CURRENT_TIMEZONE}} の IANA 名)。チャットの入口
# (process_chat_payload・preset の system prompt を当てる apply_model_system_prompt_to_body) が置き、
# prompt_template の日時がこれで作られる。無い (API・続きターン等)・不正ならサーバのプロセスの TZ
# (配備のコンテナの TZ 環境変数 — llens は Asia/Tokyo)
_request_timezone: ContextVar[Optional[ZoneInfo]] = ContextVar(
    "nucllei_request_timezone", default=None
)


def set_request_timezone(variables) -> None:
    """ブラウザが送った変数 (metadata.variables) の {{CURRENT_TIMEZONE}} を、このリクエストの TZ にする。"""
    name = (
        (variables or {}).get("{{CURRENT_TIMEZONE}}")
        if isinstance(variables, dict)
        else None
    )
    tz = None
    if isinstance(name, str) and name:
        try:
            tz = ZoneInfo(name)
        except Exception:
            log.warning(f"{{{{CURRENT_TIMEZONE}}}} が IANA の TZ 名でない: {name!r}")
    _request_timezone.set(tz)


def prompt_variables_template(template: str, variables: dict[str, str]) -> str:
    """ブラウザが送った変数を当てる。当てるのはブラウザでしか分からないもの
    (BROWSER_PROMPT_VARIABLES) だけで、ほかはサーバの prompt_template が正。"""
    for variable in BROWSER_PROMPT_VARIABLES:
        value = variables.get(variable)
        if isinstance(value, str):
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
    """{{USER_GROUPS}} の元: user が**所属している**グループの名前 (保存値のまま) のうち、今のグループ名の
    規則を通るもの (`ext_acl.is_group_dir_name` — {{USER_WRITABLE_GROUPS}}・jail の env
    `NUCLLEI_USER_GROUPS`・/ext/groups の bind と同じ絞り方)。規則より前に作られた通らない名前
    (`:`・カンマ・空白・指示の語 + コロン 等) は warning を 1 行出して落とす — モデルに見せるどこにも出さない
    (管理画面で改名すれば入る)。

    admin でも所属している物だけ(全グループではない)。並びは _group_name_sort_key。
    get_groups_by_member_id は user_ids の文字列 LIKE なので、実際の所属を user_ids で
    確かめ直す(utils/ext_acl.member_group_names と同じ方式)。user_id が無ければ []。
    """
    if not user_id:
        return []
    from nucllei.models.groups import Groups
    from nucllei.utils.ext_acl import is_group_dir_name

    names = []
    for g in Groups.get_groups_by_member_id(user_id):
        if user_id not in (g.user_ids or []) or not g.name:
            continue
        if is_group_dir_name(g.name):
            names.append(g.name)
        else:
            log.warning(
                f"group {g.id} の名前 {g.name!r} は今のグループ名の規則に合わないので"
                " {{USER_GROUPS}} / NUCLLEI_USER_GROUPS に入れない (管理画面で改名すれば入る)"
            )
    return sorted(names, key=_group_name_sort_key)


def user_writable_group_names(
    user_id: Optional[str], user_role: Optional[str] = None
) -> list[str]:
    """{{USER_WRITABLE_GROUPS}} の元: user が所属しているグループのうち、置き場
    (`/ext/groups/<名前>/`) に書けるものの名前。並びは _group_name_sort_key、重複なし。

    一般の利用者は `writers` にいるグループ (utils/ext_acl.member_group_access が rw のもの)。
    admin は全グループに書けるので、所属しているグループ全部 (所属していないグループは入れない —
    {{USER_GROUPS}} と同じ)。どちらもディレクトリ名にできる名前 (`ext_acl.is_group_dir_name`) だけ
    — 置き場になれない名前には誰も書けない。user_id が無ければ []。
    """
    if not user_id:
        return []
    from nucllei.utils.ext_acl import is_group_dir_name, member_group_access

    if user_role == "admin":
        names = {n for n in user_group_names(user_id) if is_group_dir_name(n)}
    else:
        names = {n for n, a in member_group_access(user_id).items() if a == "rw"}
    return sorted(names, key=_group_name_sort_key)


def user_prompt_params(user) -> dict:
    """prompt_template に渡すユーザー由来の引数(UserModel / {"name", …} の dict / None)。

    {{USER_NAME}} / {{USER_LOCATION}} / {{USER_ROLE}} / {{USER_GROUPS}} /
    {{USER_WRITABLE_GROUPS}} の出どころを
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


# {{SKILLS}}: 利用者ごとに変わる skill の一覧を、tool の説明 (chat template で system より前に出る) でなく
# system prompt に入れられるようにする。preset の system prompt に変数があれば load_skill の説明には一覧を
# 入れない (middleware — preset_system_prompt)。展開には preset (model) が要る (どの skill が載るか・
# 能力「スキル」)。model を渡さない呼び出し (タイトル生成等) では空。
SKILLS_TOKEN = "{{SKILLS}}"
# 会話の前置き (preset の params.context — utils/conversation_context) でだけ展開する 2 つ。
# {{USER_RECIPES}} = 見えるレシピの一覧 (能力「レシピ」が ON で sandbox のある配備)。前置きにあれば load_recipe の
# 説明には一覧を入れない。{{USER_MEMORY_INDEX}} = 利用者の記憶の目次 (utils/memory_index)。
# どちらもサブエージェントの子ターンでは前置きごと入らない
USER_RECIPES_TOKEN = "{{USER_RECIPES}}"
USER_MEMORY_INDEX_TOKEN = "{{USER_MEMORY_INDEX}}"
CONTEXT_VARIABLES = (USER_RECIPES_TOKEN, USER_MEMORY_INDEX_TOKEN)

# 変数ごとの「何で変わるか」: system prompt・会話の前置きの
# プレビュー (utils/prompt_preview) が、prefix cache の観点でどこが利用者ごと・日時ごとかの印を付ける。
# prompt_template とブラウザの getPromptVariables (src/lib/utils、チャットの metadata.variables) が
# 展開する変数を全部載せる。user = 利用者ごと、time = 日時で変わる、preset = preset ごとに固定
PROMPT_VARIABLE_SCOPES = {
    "{{CURRENT_DATE}}": "time",
    "{{CURRENT_TIME}}": "time",
    "{{CURRENT_DATETIME}}": "time",
    "{{CURRENT_WEEKDAY}}": "time",
    "{{CURRENT_TIMEZONE}}": "user",
    "{{USER_LANGUAGE}}": "user",
    "{{USER_NAME}}": "user",
    "{{USER_LOCATION}}": "user",
    "{{USER_ROLE}}": "user",
    "{{USER_GROUPS}}": "user",
    "{{USER_WRITABLE_GROUPS}}": "user",
    SKILLS_TOKEN: "user",
    USER_RECIPES_TOKEN: "user",
    USER_MEMORY_INDEX_TOKEN: "user",
    "{{BASE_MODEL_ID}}": "preset",
}
# system prompt・チャットの system・skill の本文で展開する変数 (会話の前置きでは空)
SYSTEM_VARIABLES = tuple(
    v for v in PROMPT_VARIABLE_SCOPES if v not in CONTEXT_VARIABLES
)


def misplaced_variables(where: str) -> tuple[str, ...]:
    """`where` ("system" | "skill" | "context") で展開しない (空にする) 変数。"""
    return SYSTEM_VARIABLES if where == "context" else CONTEXT_VARIABLES


# 置き場所違いの変数の WARN は preset・場所・変数ごとにプロセスで 1 回
_warned_misplaced: set = set()


def warn_misplaced(where: str, variable: str, model=None) -> None:
    model_id = as_model_dict(model).get("id") if model is not None else None
    key = (where, model_id, variable)
    if key in _warned_misplaced:
        return
    _warned_misplaced.add(key)
    place = {
        "system": "system prompt",
        "skill": "skill の本文",
        "context": "会話の前置き",
    }
    hint = (
        "system prompt に書く"
        if where == "context"
        else "会話の前置き (params.context) に書く"
    )
    log.warning(
        f"{variable} は {place.get(where, where)} では展開しない (空にした。preset={model_id}): {hint}"
    )


_user_recipes: ContextVar[Optional[dict]] = ContextVar(
    "nucllei_user_recipes", default=None
)
_user_memory: ContextVar[Optional[dict]] = ContextVar(
    "nucllei_user_memory_index", default=None
)
_user_recipe_listing: ContextVar[Optional[dict]] = ContextVar(
    "nucllei_user_recipe_listing", default=None
)
# 一覧・目次はリクエストの間だけ持ち回る (前置きを組むときと load_recipe の説明で 2 回聞かない)。
# 念のため古い値は使わない
_PROMPT_FETCH_TTL_S = 60


def _user_id_of(user) -> Optional[str]:
    if user is None:
        return None
    if isinstance(user, str):
        return user or None
    if isinstance(user, dict):
        return user.get("id") or None
    return getattr(user, "id", None) or None


def as_model_dict(model) -> dict:
    """preset を app.state.MODELS の形 (`{"id", "info": {…, "meta", "params"}}`) にそろえる。
    models テーブルの行 (ModelModel) も受ける。"""
    if model is None:
        return {}
    if isinstance(model, dict):
        return model
    dump = model.model_dump() if hasattr(model, "model_dump") else {}
    return {"id": dump.get("id"), "info": dump}


def _preset_param(model, key: str) -> str:
    """preset の params の文字列 (`system` / `context`)。routers が当てるのと同じく DB の行から読む (MODELS の
    キャッシュは古いことがある)。無ければ MODELS の info、どちらも無ければ空。"""
    model = as_model_dict(model)
    model_id = model.get("id")
    if model_id:
        try:
            from nucllei.models.models import Models

            row = Models.get_model_by_id(model_id)
        except Exception as e:
            log.warning(f"preset {model_id} の params.{key} を読めない: {e!r}")
            row = None
        if row is not None:
            value = ((row.params.model_dump() if row.params else {}) or {}).get(key)
            return value if isinstance(value, str) else ""
    value = ((model.get("info") or {}).get("params") or {}).get(key)
    return value if isinstance(value, str) else ""


def preset_system_prompt(model) -> str:
    """preset の system prompt (params.system)。"""
    return _preset_param(model, "system")


def preset_context_template(model) -> str:
    """preset の会話の前置き (params.context)。"""
    return _preset_param(model, "context")


def recipes_available(model) -> bool:
    """このリクエストでレシピの tool と一覧を出すか (sandbox のある配備 ∧ 能力「レシピ」が ON)。"""
    from nucllei.env import SANDBOX_URL
    from nucllei.utils.recipes import is_recipes_feature_enabled

    return bool(SANDBOX_URL) and is_recipes_feature_enabled(as_model_dict(model))


async def _cached_fetch(var: ContextVar, user, fetch) -> str:
    uid = _user_id_of(user)
    cache = var.get()
    if cache is None:
        cache = {}
        var.set(cache)
    hit = cache.get(uid)
    if hit is not None and time.monotonic() - hit[0] < _PROMPT_FETCH_TTL_S:
        return hit[1]
    text = await fetch(user)
    cache[uid] = (time.monotonic(), text)
    return text


async def get_user_recipes(user) -> str:
    """見えるレシピの一覧 (user_recipes_text の結果)。このリクエストで取ってあればそれを、無ければ
    取って持つ (1 リクエスト 1 回)。呼ぶのは能力「レシピ」が ON のときだけ。"""
    from nucllei.utils.recipe_index import user_recipes_text

    return await _cached_fetch(_user_recipes, user, user_recipes_text)


async def get_user_recipe_listing(user):
    """見えるレシピの一覧の材料 (recipe_index.RecipeListing)。一覧の行 (get_user_recipes) と `/` の発火
    (utils/recipe_commands) が同じこれを通るので、1 リクエストで sandbox に聞くのは 1 回。"""
    from nucllei.utils.recipe_index import user_recipe_listing

    return await _cached_fetch(_user_recipe_listing, user, user_recipe_listing)


async def get_user_memory_index(user) -> str:
    """{{USER_MEMORY_INDEX}} の中身 (utils/memory_index)。このリクエストで取ってあればそれを。"""
    from nucllei.utils.memory_index import user_memory_index_text

    return await _cached_fetch(_user_memory, user, user_memory_index_text)


def reset_prompt_fetches() -> None:
    """このリクエストで取ったレシピの一覧・記憶の目次を捨てる (次に要るとき取り直す)。"""
    _user_recipes.set(None)
    _user_recipe_listing.set(None)
    _user_memory.set(None)


async def fetch_context_sections(user, model, template: str) -> dict[str, str]:
    """会話の前置きの変数のうち `template` にあるものの今の展開 ({変数: 文字列})。sandbox に聞くものは並べて聞く。

    {{USER_RECIPES}} = 見出し + 一覧 (recipe_index_block。能力「レシピ」OFF・sandbox 無し・model 無しは空、0 件・不通・古い
    sandbox はその 1 行)。{{USER_MEMORY_INDEX}} = 記憶の目次 (無い・sandbox 無しは空、不通はその 1 行)。
    user 不明なら両方空。"""
    from nucllei.utils.frames import defuse
    from nucllei.utils.recipe_index import recipe_index_block

    sections: dict[str, str] = {}
    if not isinstance(template, str):
        return sections
    uid = _user_id_of(user)
    want_recipes = USER_RECIPES_TOKEN in template
    want_memory = USER_MEMORY_INDEX_TOKEN in template
    if want_recipes:
        sections[USER_RECIPES_TOKEN] = ""
    if want_memory:
        sections[USER_MEMORY_INDEX_TOKEN] = ""
    if not uid:
        return sections
    jobs = {}
    if want_recipes and model and recipes_available(model):
        jobs[USER_RECIPES_TOKEN] = get_user_recipes(user)
    if want_memory:
        jobs[USER_MEMORY_INDEX_TOKEN] = get_user_memory_index(user)
    if not jobs:
        return sections
    # 持ち回りの dict はここ (呼び手の context) で作っておく — gather は別の task で走らせるので、
    # その中で ContextVar に set した dict は呼び手に戻らない
    for var in (_user_recipes, _user_recipe_listing, _user_memory):
        if var.get() is None:
            var.set({})
    results = await asyncio.gather(*jobs.values())
    for token, text in zip(jobs.keys(), results):
        if token == USER_RECIPES_TOKEN:
            # 書いた人の説明から記憶の目次の枠を閉じたり偽ったりできないように (前置きの枠と `<source>` は
            # 前置き全体に掛ける — conversation_context.frame_initial)
            text = defuse(recipe_index_block(text), memory_index=True)
        sections[token] = text
    return sections


def section_is_known(token: str, text: str) -> bool:
    """前置きの節が「取れた」値か (sandbox 不通・古い sandbox の 1 行の注記は False)。"""
    from nucllei.utils.memory_index import MEMORY_INDEX_UNREACHABLE
    from nucllei.utils.recipe_index import RECIPES_OLD_SANDBOX, RECIPES_UNREACHABLE

    if token == USER_MEMORY_INDEX_TOKEN:
        return text != MEMORY_INDEX_UNREACHABLE
    if token == USER_RECIPES_TOKEN:
        return text not in (RECIPES_OLD_SANDBOX, RECIPES_UNREACHABLE)
    return True


_VARIABLE_TOKEN = re.compile(r"\{\{[A-Z_]+\}\}")


def expand_context_template(template: str, sections: dict, model=None) -> str:
    """会話の前置きのテンプレートを展開する (1 回の走査 — 値の中の `{{…}}` は展開しない)。

    {{USER_RECIPES}} / {{USER_MEMORY_INDEX}} は `sections` (fetch_context_sections) の値、ほかのサーバ変数
    (日時・利用者・{{SKILLS}} 等) は空 (WARN — system prompt に書くもの)。知らない `{{…}}` は字のまま。"""

    def replace(match):
        token = match.group(0)
        if token in CONTEXT_VARIABLES:
            return sections.get(token) or ""
        if token in SYSTEM_VARIABLES:
            warn_misplaced("context", token, model)
            return ""
        return token

    return _VARIABLE_TOKEN.sub(replace, template or "")


# datetime.weekday() の順(月曜 = 0)
_WEEKDAYS_JA = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")


def prompt_template(
    template: str,
    user_name: Optional[str] = None,
    user_location: Optional[str] = None,
    base_model_id: Optional[str] = None,
    user_role: Optional[str] = None,
    user_id: Optional[str] = None,
    model=None,
    where: str = "system",
) -> str:
    """サーバー既知のプロンプト変数を展開する(system prompt・チャットの system・skill の本文・タイトル生成)。

    {{USER_ROLE}} は role の値そのまま(不明なら "Unknown")。{{USER_GROUPS}} は
    user_group_names(user_id) (今のグループ名の規則を通る名前だけ) を ", " でつないだもの
    (所属なし・user 不明なら空文字列)。
    グループの DB 照会は {{USER_GROUPS}} が本文にあるときだけ、1 呼び出しにつき 1 回。
    {{USER_WRITABLE_GROUPS}} は user_writable_group_names(user_id, user_role) を ", " でつないだもの
    (置き場に書ける所属グループ。admin は所属グループ全部。無ければ空文字列)。
    {{SKILLS}} は model (preset) で load_skill できる skill の一覧 (見出し + `- <id>: <説明>` —
    load_skill の説明に入れていたものと同じ。能力「スキル」OFF・0 件・model 無しなら空)。
    {{USER_RECIPES}} / {{USER_MEMORY_INDEX}} は会話の前置きでだけ展開する変数 — ここでは空にし、log に WARN
    (`where` は WARN の文に使う場所: "system" / "skill")。先に空にするので、ほかの変数の値に書かれた字面は残る。
    """
    for token in CONTEXT_VARIABLES:
        if token in template:
            warn_misplaced(where, token, model)
            template = template.replace(token, "")

    # 今の日時。TZ はこのリクエストの利用者の TZ (ブラウザの {{CURRENT_TIMEZONE}})、無ければサーバの TZ
    current_date = datetime.now(_request_timezone.get())

    # Format the date to YYYY-MM-DD
    formatted_date = current_date.strftime("%Y-%m-%d")
    # サーバが作る値は ja の表記に固定 (ブラウザの言語・プロセスのロケールに依らない。strftime の
    # %Y %m %d %H %M %S はロケールに依らない)。時刻は 24 時間制。日付は ISO のまま (2026年10月30日 には
    # しない — レシピの updated・tool の引数が ISO)
    formatted_time = current_date.strftime("%H:%M:%S")
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
    if "{{USER_WRITABLE_GROUPS}}" in template:
        template = template.replace(
            "{{USER_WRITABLE_GROUPS}}",
            ", ".join(user_writable_group_names(user_id, user_role)),
        )
    if SKILLS_TOKEN in template:
        from nucllei.utils.skills import skills_block

        template = template.replace(
            SKILLS_TOKEN,
            skills_block(as_model_dict(model), user_id) if model and user_id else "",
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
