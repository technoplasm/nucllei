"""preset / skill の編集画面の「プレビュー」— 実際にどう展開されるかを見せる。

**実際のリクエストと同じ関数を通す** — プレビュー専用の展開は書かない:
- system prompt: `payload.apply_model_system_prompt_to_body` (routers/openai・ollama・functions が
  preset の system prompt を当てるのと同じ)
- 会話の前置き: `task.fetch_context_sections` → `task.expand_context_template` → `conversation_context.frame_initial`
  (最初の発言の先頭に入るバイト列 = `framed`)
- tool の宣言: `get_tools` (meta.toolIds) → `middleware.build_capability_tools` (feed_image / feed_audio・
  load_skill・レシピ)
  → `mount_recall_tools_if_needed` → `model_tool_specs` (process_chat_payload と同じ並び)
- skill の本文: `skills.expand_skill_content` (activate_skill と同じ)

展開するのはリクエストした本人としてだけ (他の利用者として見る口は作らない — 個人のレシピ名が
見えてしまう)。前提は「新しいチャットの最初のターン」: 履歴・添付・`/command` 由来の skill の tool は
入らず、Filter の inlet が変える分も入らない。

利用者ごとに変わる部分の印 (prefix cache の観点で、何が preset ごとに固定で何が利用者・日時ごとか):
- system prompt / 会話の前置き / skill の本文: 変数の位置で切り、変数ごとの PROMPT_VARIABLE_SCOPES (task.py) で
  user / time / preset。その場所では展開しない変数 (task.misplaced_variables — 空になる) は `misplaced` で、
  `warnings` にも並べる。切った断片をつないだものが全体の展開と一致しないとき (変数の値に別の変数の
  字面が入っていた等) は、切らずに 1 つで返す (`exact: false`)
- tool: エントリの `varies` (load_skill / load_recipe の説明の一覧 = `description_from` 以降、
  load_skill の skill_id の enum = `params`、spec_overrides で差し替わった tool = `overridden`)
"""

import asyncio
import json
import logging
import re
from typing import Callable, Optional

from nucllei.env import SRC_LOG_LEVELS
from nucllei.utils.task import PROMPT_VARIABLE_SCOPES, misplaced_variables

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

_VARIABLE_PATTERN = re.compile(r"\{\{[A-Z_]+\}\}")

# プレビューは「新しいチャットの最初のターン」として組む。chat / message の id はこの値
# (exec の spawn / 回収の文脈判定が UI のチャットと同じになるように。DB には何も書かない)
PREVIEW_CHAT_ID = "preview"
PREVIEW_MESSAGE_ID = "preview"


def segment_expansion(
    template: str, expand: Callable[[str], str], misplaced: tuple = ()
) -> dict:
    """`template` を `expand` で展開した結果を、変数の位置で断片に切って返す。

    {text, segments: [{text, scope, variable?}], exact}。scope は fixed / user / time / preset /
    misplaced (`misplaced` の変数 — その場所では空に展開される)。未知の `{{…}}` は展開されないので
    fixed の字面のまま。"""
    full = expand(template)
    segments = []
    pos = 0
    for match in _VARIABLE_PATTERN.finditer(template):
        if match.start() > pos:
            segments.append({"text": template[pos : match.start()], "scope": "fixed"})
        token = match.group(0)
        scope = PROMPT_VARIABLE_SCOPES.get(token)
        if scope is None:
            segments.append({"text": token, "scope": "fixed"})
        elif token in misplaced:
            segments.append({"text": "", "scope": "misplaced", "variable": token})
        else:
            segments.append({"text": expand(token), "scope": scope, "variable": token})
        pos = match.end()
    if pos < len(template):
        segments.append({"text": template[pos:], "scope": "fixed"})
    exact = "".join(s["text"] for s in segments) == full
    if not exact:
        segments = [{"text": full, "scope": "fixed"}] if full else []
    return {"text": full, "segments": segments, "exact": exact}


def _preview_model(form, user) -> tuple[dict, object]:
    """編集中の preset (ModelForm) を、チャット経路と同じ 2 つの形にする: app.state.MODELS の形の
    dict (tool の組み立て) と models テーブルの行 (system prompt を当てる関数の model_info)。"""
    from nucllei.models.models import ModelModel

    row = ModelModel(
        id=form.id or "",
        user_id=user.id,
        base_model_id=form.base_model_id,
        name=form.name or "",
        params=form.params,
        meta=form.meta,
        access_control=form.access_control,
        is_active=True,
        updated_at=0,
        created_at=0,
    )
    return {"id": row.id, "name": row.name, "info": row.model_dump()}, row


def _user_param(user) -> dict:
    # process_chat_payload の extra_params["__user__"] と同じ形
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "info": getattr(user, "info", None) or {},
    }


def _tool_view(name: str, entry: dict, spec: dict) -> dict:
    return {
        "name": name,
        "tool_id": entry.get("tool_id"),
        "description": spec.get("description") or "",
        "parameters": spec.get("parameters") or {},
        # 宣言 1 つ分の大きさ (chat template の書き方はモデルで違うので JSON の文字数で見る)
        "chars": len(json.dumps(spec, ensure_ascii=False)),
        "varies": entry.get("varies"),
    }


def misplaced_warnings(template: str, where: str) -> list[dict]:
    """`template` にある、`where` では展開しない変数 (書かれた順・重複なし)。"""
    misplaced = misplaced_variables(where)
    found = []
    for match in _VARIABLE_PATTERN.finditer(template or ""):
        token = match.group(0)
        if token in misplaced and token not in found:
            found.append(token)
    message = (
        "会話の前置きでは展開しない (空になる)。system prompt に書く"
        if where == "context"
        else "ここでは展開しない (空になる)。会話の前置きに書く"
    )
    return [{"where": where, "variable": token, "message": message} for token in found]


async def preview_tools(
    request, user, model: dict, preset_system: str, preset_context: str = ""
) -> list[dict]:
    """このリクエストの利用者で、その preset の新しいチャットの最初のターンにモデルへ渡る tools
    (process_chat_payload と同じ関数・同じ並び。実行はしない)。"""
    from nucllei.utils.exec_tools import exec_spawn_context_ok
    from nucllei.utils.middleware import (
        build_capability_tools,
        get_model_tool_ids,
        model_tool_specs,
        mount_recall_tools_if_needed,
    )
    from nucllei.utils.tools import get_tools

    metadata = {
        "user_id": user.id,
        "chat_id": PREVIEW_CHAT_ID,
        "message_id": PREVIEW_MESSAGE_ID,
        "model": model,
    }
    tools_extra_params = {
        "__event_emitter__": None,
        "__event_call__": None,
        "__user__": _user_param(user),
        "__metadata__": metadata,
        "__request__": request,
        "__model__": model,
        "__chat_id__": PREVIEW_CHAT_ID,
        "__message_id__": PREVIEW_MESSAGE_ID,
        "__messages__": [],
        "__files__": [],
    }
    tools_dict = {}
    tool_ids = get_model_tool_ids(model)
    if tool_ids:
        tools_dict = await asyncio.to_thread(
            get_tools, request, tool_ids, user, tools_extra_params
        )
    for name, entry in (
        await build_capability_tools(
            request, model, user, tools_extra_params, preset_system, preset_context
        )
    ).items():
        tools_dict[name] = entry
    await mount_recall_tools_if_needed(tools_dict, {}, user, metadata)
    specs = model_tool_specs(tools_dict, exec_spawn_context_ok(metadata))
    return [
        _tool_view(
            spec["function"]["name"],
            tools_dict[spec["function"]["name"]],
            spec["function"],
        )
        for spec in specs
    ]


async def preview_preset(request, user, form, variables: Optional[dict] = None) -> dict:
    """preset の編集画面のプレビュー: tools → system prompt → 会話の前置き (編集中の値で、本人として)。"""
    from nucllei.utils.conversation_context import frame_initial
    from nucllei.utils.payload import apply_model_system_prompt_to_body
    from nucllei.utils.task import (
        expand_context_template,
        fetch_context_sections,
        set_request_timezone,
    )

    # 日時はブラウザの TZ でサーバが作る (チャットと同じ — task.BROWSER_PROMPT_VARIABLES)
    set_request_timezone(variables)
    model, model_info = _preview_model(form, user)
    params = form.params.model_dump() if form.params else {}
    system = params.get("system") or ""
    context = params.get("context") if isinstance(params.get("context"), str) else ""
    # チャットの metadata.variables と同じ (ブラウザが getPromptVariables で送る値)
    metadata = {"variables": variables} if variables else None

    def expand(text: str) -> str:
        body = apply_model_system_prompt_to_body(
            {"system": text}, {"messages": []}, metadata, user, model_info
        )
        messages = body.get("messages") or []
        return messages[0]["content"] if messages else ""

    tools = await preview_tools(request, user, model, system, context)
    expanded = segment_expansion(system, expand, misplaced_variables("system"))
    sections = await fetch_context_sections(user, model, context)
    context_expanded = segment_expansion(
        context,
        lambda text: expand_context_template(text, sections, model),
        misplaced_variables("context"),
    )
    framed = frame_initial(context_expanded["text"])
    return {
        "tools": tools,
        "tools_chars": sum(t["chars"] for t in tools),
        "system": {**expanded, "chars": len(expanded["text"])},
        "context": {
            **context_expanded,
            "chars": len(context_expanded["text"]),
            "framed": framed,
        },
        "warnings": misplaced_warnings(system, "system")
        + misplaced_warnings(context, "context"),
    }


async def preview_skill(
    user, content: str, before, after, model=None, variables: Optional[dict] = None
) -> dict:
    """skill の編集画面のプレビュー: before → 本文 (編集中の値を本人として展開) → after。
    before / after は実行しない (何を実行するかの一覧だけ)。model は {{BASE_MODEL_ID}}・{{SKILLS}} の元になる
    preset (無ければそれらは Unknown / 空)。"""
    from nucllei.utils.skills import expand_skill_content
    from nucllei.utils.task import set_request_timezone

    # 日時はブラウザの TZ で (チャットで読み込まれるときと同じ)
    set_request_timezone(variables)
    body = segment_expansion(
        content,
        lambda text: expand_skill_content(text, user, model),
        misplaced_variables("skill"),
    )
    return {
        "before": [{"n": n + 1, **entry} for n, entry in enumerate(before)],
        "content": {**body, "chars": len(body["text"])},
        "after": [{"n": n + 1, **entry} for n, entry in enumerate(after)],
        "warnings": misplaced_warnings(content, "skill"),
    }
