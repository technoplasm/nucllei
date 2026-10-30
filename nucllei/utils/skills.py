"""skill 発火ロジック(issue #35 / #42 / #43)。

`/command` 発火(apply_skill_command)・load_skill 擬似 tool
(build_load_skill_tool)・添付による自動ロード(apply_skill_auto_load)の
3 入口、およびその共通部(activate_skill / 可視性・機能フラグ判定)。
middleware(オーケストレーション)から issue #23 で分離した。設計の一次情報は _memos/skill-design.md。
"""

import json
import logging
import re

from nucllei.env import SRC_LOG_LEVELS
from nucllei.models.files import Files
from nucllei.models.skills import Skills, normalize_command
from nucllei.utils.access_control import has_access, has_read_access_to_file
from nucllei.utils.file_images import INTERNAL_FILE_URL_PATTERN
from nucllei.utils.task import (
    prompt_template,
    resolve_base_model_id,
    user_prompt_params,
)
from nucllei.utils.tool_errors import ToolCallError, ToolFailure

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 読み込み済みの skill を再ロードしたときの結果(本文は返さない)。tool 結果の文の規約
# (事実の文 + 「次の手:」— README_API §5.1)に揃える。
LOADED_RESULT = (
    "skill {skill_id} は読み込み済み (手順書はこの会話の上にある load_skill の結果)。"
    "次の手: 上の手順書に従って作業を続ける。"
)

# before / after の n 番目の tool call の id(決定的)。結果は load_skill の結果の中に入るので
# 履歴の tool 往復にはならない(表示・ログで何の実行かを引くための名前)
HOOK_CALL_ID = "skill:{skill_id}:{phase}:{n}"

# 先頭行の行頭にある `/command` トークンを検出する正規表現(issue #35)。
SKILL_COMMAND_PATTERN = re.compile(r"^/([a-zA-Z0-9_-]+)")


def is_skill_visible(skill, user) -> bool:
    """read 可視性(access_control read。作成者は常に可視)。"""
    return skill.user_id == user.id or has_access(user.id, "read", skill.access_control)


def activate_skill(skill, user, model=None) -> str:
    """skill 発火の単一パス(issue #35): content を展開して返す。

    `/command` 起動(apply_skill_command)と load_skill 起動の両方がここを通る。
    変数はサーバー既知のもののみ({{USER_NAME}} / {{USER_ROLE}} / {{USER_GROUPS}} /
    {{CURRENT_DATE}} / {{BASE_MODEL_ID}} 等。一覧は utils/task.py prompt_template。
    ブラウザ専用変数は無し。BASE_MODEL_ID は skill を発火させたターンのモデル = model の base)、
    毎リクエスト再展開(発火時点への固定はしない — _memos/skill-design.md「発火の有効範囲」)。

    tool_ids の遅延取り込み(#35 step2b)は呼び出し側が担う:
    `/command` は process_chat_payload の skill_tool_ids 集約、load_skill は
    build_load_skill_tool の load_tools フック(どちらも get_tools を
    enforce_access_control=False で呼ぶ — skill の read 可視性が認可の単位)。
    """
    return prompt_template(
        skill.content,
        **user_prompt_params(user),
        base_model_id=resolve_base_model_id(model),
    )


def reachable_skill_contents(model) -> list[str]:
    """このモデルで読み込まれうる skill の本文(meta.skillIds)。{{USER_RECIPES}} を先に取るかの
    判定に使う(ensure_user_recipes)。"""
    if not is_skills_feature_enabled(model):
        return []
    return [
        skill.content or ""
        for skill in Skills.get_skills_by_ids(get_model_skill_ids(model))
    ]


def is_skills_feature_enabled(model) -> bool:
    """モデル(preset)の機能「スキル」が有効か(issue #42)。

    meta.features.skills に持つ(既定 ON — キー不在は有効)。OFF のモデルでは
    manifest(load_skill)も `/command` 発火も止まり、スキル機構が丸ごと消える。
    グローバルフラグは作らない(制御面はモデル設定に一元化 — _memos/builtin-tools.md 1a/1b)。
    """
    meta = (model or {}).get("info", {}).get("meta", {}) or {}
    features = meta.get("features") or {}
    return features.get("skills", True) is not False


def get_model_skill_ids(model) -> list[str]:
    return (model or {}).get("info", {}).get("meta", {}).get("skillIds", []) or []


def apply_skill_command(form_data, user, model):
    """user メッセージ先頭の `/command` を「load_skill を呼んだことにして」発火する(issue #35)。

    ヒットした user メッセージは**生のまま**残し、直後に合成の
    assistant tool_call(load_skill)+ tool result(展開済み content)を注入する。
    モデル自律起動(入口 2)と wire 上も完全に対称になり、モデルは「ロード済み」を
    履歴で知るので冗長ロードも起きない。UI も既存の tool_calls 描画がそのまま使える。

    ペイロード内の**全 user メッセージ**を毎リクエスト走査する。状態は会話そのもの
    (2026-07-06 改訂) — 発火した `/command` の往復は応答に残り、履歴として届く。

    - 各 user メッセージの先頭行の行頭が `/([a-zA-Z0-9_-]+)` なら、その command で
      read 可視な skill を lookup する。
    - ミス: 何もしない(通常テキストとして素通し。エラーにしない)。
    - 発火条件は AND(issue #42): 機能「スキル」がモデルで有効 ∧ その skill がモデルに
      紐づく(meta.skillIds)∧ ユーザーに read 可視。モデル設定を唯一の制御面にする
      (従来の「可視ならどのモデルでも発火」から変更 — _memos/builtin-tools.md 1b)。

    - **同一 skill の再発火は冪等**: 同じ skill が既にこの会話へ載っていれば
      (この `/command` が前のターンで発火して応答に残った load_skill 往復、モデル自律の
      load_skill、このリクエストで先に注入した分 — 位置は問わない)exchange を足さない —
      本文が context に 2 回入らない。発火リストにも載せない。
    - **発火は 1 回**(2026-10-30): 発火したターンの応答の先頭に、この exchange を通常の
      tool_calls ブロックとして残す(skill_activation_seed_block)。次のリクエストからは履歴の
      load_skill 往復として届くので、ここでは足さない(本文と before / after の結果がその
      1 つの結果に書いた順で入ったまま並ぶ — 本文を毎回組み直していたときは、保存された
      before / after の結果が本文の後ろに回っていた)。

    戻り値は (form_data, 発火リスト)。発火リストの要素は
    {call_id, id, name, content(展開済み), result, result_message, tool_ids, skill}。
    """
    activations = []

    if not is_skills_feature_enabled(model):
        return form_data, activations
    model_skill_ids = set(get_model_skill_ids(model))

    messages = form_data.get("messages", [])
    new_messages = []
    # 本文が載った skill id(冪等判定)。前のターンの発火も応答に残るので、履歴全体から取る
    loaded: set[str] = set(iter_loaded_skill_ids(messages))
    for idx, message in enumerate(messages):
        new_messages.append(message)

        if message.get("role") != "user":
            continue

        # content は文字列 or マルチパート(text パート)。text 部分を取り出す。
        content = message.get("content")
        if isinstance(content, list):
            text = next(
                (
                    item.get("text", "")
                    for item in content
                    if item.get("type") == "text"
                ),
                None,
            )
        else:
            text = content

        if not isinstance(text, str):
            continue

        match = SKILL_COMMAND_PATTERN.match(text)
        if not match:
            continue

        # llens(#43): skill の可読キーは id 一本(旧 command)。/command 一致 = id 一致。
        skill = Skills.get_skill_by_id(match.group(1))
        if (
            skill is None
            or skill.id not in model_skill_ids
            or not is_skill_visible(skill, user)
            or skill.id in loaded
        ):
            continue
        loaded.add(skill.id)

        # id はモデル由来の call(functions.load_skill:N)と衝突しない自前形式
        exchange, activation = synthesize_load_skill_exchange(
            skill, user, model, f"skill:{skill.id}:{idx}"
        )
        new_messages.extend(exchange)
        activations.append(activation)

    form_data["messages"] = new_messages
    return form_data, activations


def synthesize_load_skill_exchange(skill, user, model, call_id):
    """「load_skill を呼んだことにする」合成 exchange と発火情報を作る。

    `/command`(入口 1)と添付による自動ロード(入口 3)の共通部。戻り値は
    ([assistant tool_call, tool result], activation)。result の本文は before / after を
    実行した後に apply_skill_hooks が組み直す(このリクエストの messages と応答の両方)。
    """
    expanded = activate_skill(skill, user, model)
    arguments = json.dumps({"skill_id": skill.id}, ensure_ascii=False)
    # 戻り値は構造化 JSON {result, tools}(#35): 導入された tool 名は
    # 後段(process_chat_payload の skill tools ロード)で書き足される。
    result_content = json.dumps({"result": expanded, "tools": []}, ensure_ascii=False)
    result_message = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result_content,
    }
    exchange = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "load_skill", "arguments": arguments},
                }
            ],
        },
        result_message,
    ]
    activation = {
        "call_id": call_id,
        "id": skill.id,
        "name": skill.name,
        "content": expanded,
        "result": result_content,
        # 導入 tool 名を後段で書き戻すための参照(dict は共有される)
        "result_message": result_message,
        "tool_ids": skill.tool_ids or [],
        # 導入された tool 名(process_chat_payload が skill の tool を載せた後に書く)
        "tool_names": [],
        "skill": skill,
    }
    return exchange, activation


def iter_loaded_skill_ids(messages):
    """履歴中の load_skill 呼び出しが指す skill id を順に返す(正規化済み)。

    assistant の tool_calls に加え、UI 由来の履歴が content_blocks 形で来る経路も読む。
    `/command`・自動ロードの発火も応答に残した load_skill 往復なので同じに数える。
    """
    for message in messages:
        if message.get("role") != "assistant":
            continue
        calls = list(message.get("tool_calls") or [])
        for block in message.get("content_blocks") or []:
            if block.get("type") == "tool_calls":
                calls.extend(block.get("content") or [])
        for call in calls:
            function = (call or {}).get("function") or {}
            if function.get("name") != "load_skill":
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(arguments, dict):
                continue
            skill_id = arguments.get("skill_id")
            if isinstance(skill_id, str) and normalize_command(skill_id):
                yield normalize_command(skill_id)


def hook_call(skill_id: str, phase: str, n: int, entry: dict) -> dict:
    """before / after の n 番目を OpenAI 形の tool call にする。引数は skill に書いた固定値だけ。"""
    return {
        "id": HOOK_CALL_ID.format(skill_id=skill_id, phase=phase, n=n),
        "type": "function",
        "function": {
            "name": entry.get("tool") or "",
            "arguments": json.dumps(entry.get("args") or {}, ensure_ascii=False),
        },
    }


async def run_skill_hooks(skill, tools: dict, execute) -> list[dict]:
    """skill の before / after を書いた順に実行して記録の列を返す(llens#22、2026-10-30)。

    - tool は `tools`(このターンの tools_dict — モデルの tool の read ACL・skill の tool を
      通った物)に在るものだけ。無ければ実行せず ERROR の 1 行
    - 実行は `execute(calls)`(middleware.execute_tool_calls — モデルの call と同じ関数。
      例外・timeout は ERROR の result になり、skill の読み込みは止めない)
    - 1 つずつ順に実行する(before の結果を前提に after を書けるように)

    記録は {phase, n, tool, args, status (ok / error / unavailable), content, duration}。
    呼ぶのは skill の本文が context に入るとき(発火・load_skill の 1 回目)だけで、それは
    chat につき 1 回 — 応答に残った load_skill の結果が「読み込み済み」の印になる。
    """
    records = []
    for phase in ("before", "after"):
        for n, entry in enumerate(getattr(skill, phase, None) or []):
            call = hook_call(skill.id, phase, n, entry)
            name = call["function"]["name"]
            record = {
                "phase": phase,
                "n": n,
                "tool": name,
                "args": entry.get("args") or {},
            }
            if name not in tools:
                log.warning(
                    f"skill {skill.id} の {phase}[{n}] の tool {name!r} はこのターンに無い"
                )
                error = ToolFailure(
                    f"skill {skill.id} の読み込み時に実行する {name} は使えない"
                    " (このターンの tool の一覧に無い)",
                    "その結果無しで手順書に従う。",
                )
                records.append(
                    {
                        **record,
                        "status": "unavailable",
                        "content": error.text,
                        "duration": 0.0,
                    }
                )
                continue
            result = (await execute([call]))[0] or {}
            records.append(
                {
                    **record,
                    "status": "error" if result.get("error_kind") else "ok",
                    "content": str(result.get("content") or ""),
                    "duration": result.get("duration", 0.0),
                }
            )
    return records


def _hook_part(skill_id: str, record: dict) -> str:
    when = "読み込み時" if record["phase"] == "before" else "手順書の後"
    n = record["n"] + 1
    return (
        f'<skill-hook skill="{skill_id}" phase="{record["phase"]}" n="{n}" tool="{record["tool"]}">\n'
        f'### {when}に実行した {record["tool"]} の結果 ({record["phase"]} {n})\n'
        f'{record["content"]}\n'
        "</skill-hook>"
    )


def compose_skill_result(skill_id: str, body: str, records) -> str:
    """load_skill の結果の本文: before の結果(書いた順)→ skill の本文 → after の結果。
    各結果は `<skill-hook>` で囲み、見出しで何の結果かを書く(資料が本文のどこに付くかを
    モデルが取り違えないように)。before / after が無ければ本文そのまま。"""
    records = list(records or [])
    if not records:
        return body
    before = [_hook_part(skill_id, r) for r in records if r["phase"] == "before"]
    after = [_hook_part(skill_id, r) for r in records if r["phase"] == "after"]
    return "\n\n".join([*before, body, *after])


async def apply_skill_hooks(activations, tools: dict, execute) -> None:
    """`/command`・自動ロードの発火の before / after を実行し、その発火の load_skill の結果を
    before → 本文 → after に組み直す(このリクエストの messages に入れた result_message と、
    応答に残す result の両方 — 同じ文字列)。process_chat_payload が skill の tool を載せた
    後に呼ぶ。記録は activation["hooks"] に残す。"""
    for activation in activations:
        skill = activation.get("skill")
        if skill is None or not (skill.before or skill.after):
            continue
        records = await run_skill_hooks(skill, tools, execute)
        activation["hooks"] = records
        activation["result"] = json.dumps(
            {
                "result": compose_skill_result(
                    skill.id, activation["content"], records
                ),
                "tools": activation.get("tool_names") or [],
            },
            ensure_ascii=False,
        )
        activation["result_message"]["content"] = activation["result"]


def _readable_content_type(file_id, user, cache) -> str:
    """要求ユーザーが読める file の content_type(小文字)。読めない・無ければ ""。

    クライアントが申告した meta は信用せず、必ず DB の値で判定する。
    """
    if file_id not in cache:
        content_type = ""
        if has_read_access_to_file(file_id, user):
            file = Files.get_file_by_id(file_id)
            if file is not None:
                content_type = str((file.meta or {}).get("content_type") or "").lower()
        cache[file_id] = content_type
    return cache[file_id]


def _chat_level_attachments(form_data, user, cache) -> list[tuple[str, str]]:
    """リクエスト直下の `files`(チャットの添付一覧)を [(file_id, content_type)] にする。

    チャット wire では画像以外の添付はメッセージに載らず、チャット全体の一覧として
    毎リクエスト届く(どの発言に付いたかは wire からは分からない)。
    """
    attachments = []
    for entry in form_data.get("files") or []:
        if not isinstance(entry, dict) or entry.get("type") == "collection":
            continue
        file_id = entry.get("id")
        if not isinstance(file_id, str) or not file_id:
            continue
        content_type = _readable_content_type(file_id, user, cache)
        if content_type:
            attachments.append((file_id, content_type))
    return attachments


def _message_level_attachments(messages, user, cache) -> list[tuple[int, str]]:
    """user メッセージの image_url パートを [(メッセージ位置, content_type)] にする。

    内部 file URL(/api/v1/files/{id}/content)は DB の content_type、data URL は
    その MIME を読む。tool 結果の画像を運ぶ user メッセージ(tool メッセージの直後に
    置かれる — content_blocks.py / chat-messages.ts)は利用者の添付ではないので除く。
    """
    attachments = []
    for idx, message in enumerate(messages):
        if message.get("role") != "user" or not isinstance(
            message.get("content"), list
        ):
            continue
        if idx > 0 and messages[idx - 1].get("role") == "tool":
            continue
        for part in message["content"]:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            url = (part.get("image_url") or {}).get("url")
            if not isinstance(url, str):
                continue
            content_type = ""
            if url.startswith("data:"):
                content_type = url[5:].split(",", 1)[0].split(";", 1)[0].lower()
            else:
                match = INTERNAL_FILE_URL_PATTERN.match(url)
                if match:
                    content_type = _readable_content_type(match.group(1), user, cache)
            if content_type:
                attachments.append((idx, content_type))
    return attachments


def apply_skill_auto_load(form_data, user, model):
    """添付の content_type を条件に skill を自動ロードする(入口 3)。

    skill が `auto_load_content_types`(content_type の前方一致リスト、例 ["audio/"])を
    宣言していて、会話に条件に合う添付があれば、`/command` と同じく「load_skill を
    呼んだことにする」合成 exchange を注入する。apply_skill_command の後、かつ
    クライアント申告の `files` を読取権限で絞った後に呼ぶこと。

    - **認可は `/command` と同じ AND**: 機能「スキル」有効 ∧ モデルに紐づく
      (meta.skillIds)∧ read 可視。欠ければ何もしない。
    - **添付の出どころは 2 つ**: リクエスト直下の `files`(チャットの添付一覧。画像以外は
      ここに載り、毎リクエスト届く)と、user メッセージの image_url パート(画像)。
      content_type は DB の値で判定する(拡張子・クライアント申告は見ない)。
    - **発火は 1 回**: `/command` と同じく、発火したターンの応答に load_skill 往復として
      残る(次のリクエストからは履歴。tool は replay_skill_tool_ids が載せ直す)。
    - **二重発火しない**: 履歴に同じ skill の load_skill(`/command`・自動ロードの発火・
      モデル自律の呼び出し)が既にあれば足さない。条件に合う添付が複数でも 1 回。
    - **位置**: 画像は「最初に条件に合った user メッセージ」の直後。チャットの添付一覧は
      どの発言に付いたかを運ばないので、最初の user メッセージの直後(チャット全体に
      効く添付として扱う — `<source>` が system メッセージに載るのと同じ扱い)。
      同じ位置に `/command` の exchange があればその後ろ。複数 skill は meta.skillIds の順。

    戻り値は (form_data, 発火リスト)。発火リストの形は apply_skill_command と同じ。
    """
    activations = []

    if not is_skills_feature_enabled(model):
        return form_data, activations

    candidates = []
    for skill_id in get_model_skill_ids(model):
        skill = Skills.get_skill_by_id(skill_id)
        if (
            skill is not None
            and skill.auto_load_content_types
            and is_skill_visible(skill, user)
            and skill.id not in [c.id for c in candidates]
        ):
            candidates.append(skill)
    if not candidates:
        return form_data, activations

    messages = form_data.get("messages", [])
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_idxs:
        return form_data, activations

    cache = {}
    chat_attachments = _chat_level_attachments(form_data, user, cache)
    message_attachments = _message_level_attachments(messages, user, cache)
    if not chat_attachments and not message_attachments:
        return form_data, activations

    loaded = set(iter_loaded_skill_ids(messages))

    # 位置(messages の index)→ その直後に置く exchange 群
    pending = {}
    for skill in candidates:
        if skill.id in loaded:
            continue
        prefixes = tuple(skill.auto_load_content_types)
        chat_hits = [fid for fid, ct in chat_attachments if ct.startswith(prefixes)]
        message_hits = [i for i, ct in message_attachments if ct.startswith(prefixes)]
        if chat_hits:
            anchor = user_idxs[0]
        elif message_hits:
            anchor = min(message_hits)
        else:
            continue

        loaded.add(skill.id)
        exchange, activation = synthesize_load_skill_exchange(
            skill, user, model, f"skill:{skill.id}:auto"
        )
        pending.setdefault(anchor, []).extend(exchange)
        activations.append(activation)

    if not activations:
        return form_data, activations

    def is_command_exchange(message):
        if message.get("role") == "tool":
            return str(message.get("tool_call_id") or "").startswith("skill:")
        return message.get("role") == "assistant" and any(
            str((call or {}).get("id") or "").startswith("skill:")
            for call in message.get("tool_calls") or []
        )

    new_messages = []
    deferred = []
    for idx, message in enumerate(messages):
        # 同じ位置の `/command` exchange を先に通してから自動ロード分を置く
        if deferred and not is_command_exchange(message):
            new_messages.extend(deferred)
            deferred = []
        new_messages.append(message)
        if idx in pending:
            deferred = pending[idx]
    new_messages.extend(deferred)

    form_data["messages"] = new_messages
    return form_data, activations


def replay_skill_tool_ids(form_data, user, model) -> list[str]:
    """履歴中の load_skill 呼び出しから skill の tool_ids を再導出する(issue #101)。

    load_skill(モデル自律・`/command`・自動ロード — どれも応答に load_skill 往復として
    残る)は発火ターンの tools_dict に足すだけで、次のリクエストの tool はここが
    履歴(form_data["messages"])の assistant tool_calls / content_blocks から拾い直す
    (無いと「1 ターン目に軽い tool → 2 ターン目に本命」の流れで tool not available になる)。

    **認可を履歴に委ねない**: 履歴はクライアント入力なので「過去に呼ばれた事実」は
    根拠にせず、`/command` と同じ AND 条件(機能「スキル」有効 ∧ モデル紐づき
    meta.skillIds ∧ read 可視)をその場で再確認する。preset から skill を外せば
    履歴があっても tool は復活しない。

    skill 本文の再注入はしない(tool result として履歴に残っている)。headless
    (chat_id 無し)でも messages は来るので同じ処理で足りる。

    将来の動的 tool 源(MCP 等)を足すときもこの形を踏襲する: 「導入 tool の
    持続性は会話履歴が唯一の状態」+「復活時はその場で再認可」。tool_calls の
    走査(iter_loaded_skill_ids)は function.name で分岐しているので、別の導入
    tool 名を足す拡張点はそこに閉じる。
    """
    if not is_skills_feature_enabled(model):
        return []
    model_skill_ids = set(get_model_skill_ids(model))
    if not model_skill_ids:
        return []

    tool_ids: list[str] = []
    seen_skill_ids: set[str] = set()

    for skill_id in iter_loaded_skill_ids(form_data.get("messages", [])):
        if skill_id in seen_skill_ids:
            continue
        seen_skill_ids.add(skill_id)
        skill = Skills.get_skill_by_id(skill_id)
        if (
            skill is None
            or skill.id not in model_skill_ids
            or not is_skill_visible(skill, user)
        ):
            continue
        for tool_id in skill.tool_ids or []:
            if tool_id not in tool_ids:
                tool_ids.append(tool_id)

    return tool_ids


def current_skill_activation_seeds(skill_activations) -> list:
    """今回のリクエストの `/command` 発火・自動ロードを、応答に残す形(metadata)へ絞る。

    受け側は process_chat_response(応答先頭の tool_calls ブロック)で、
    call_id / id / result(と before / after の記録 hooks)を読む。#43 で skill の可読キーを
    command→id に改名した際、ここだけ旧キー参照が残り KeyError になっていた(回帰テストあり)。
    """
    return [
        {
            "call_id": a["call_id"],
            "id": a["id"],
            "result": a["result"],
            **({"hooks": a["hooks"]} if a.get("hooks") else {}),
        }
        for a in skill_activations
    ]


def build_load_skill_tool(model, user, load_tools=None, messages=None, run_hooks=None):
    """preset `meta.skillIds` ∩ ユーザー可視 から擬似 tool `load_skill` を合成する(#35 step2a)。

    - manifest の識別子は **command 一本**(id と name の二重露出はモデルが name を
      渡して not found になる罠 — _memos/skill-design.md step2a 教訓)。
    - description が空の skill はモデルに露出させない(説明の無い skill は選べない)。
    - `/command` で発火済みの skill を除外する必要は無い — 発火は合成 tool exchange
      として履歴に見えるので、モデルは「ロード済み」を自然に知る。
    - 対象が 1 つも無ければ None(load_skill 自体を載せない)。

    戻り値は native ループ互換の tool エントリ {"spec", "callable"}。
    tool result = activate_skill の展開済み content。
    load_tools(skill) は tool 遅延取り込みの **async** フック(#35 step2b / #28):
    skill.tool_ids が非空なら content を返す前に await され、呼び出し側
    (process_chat_payload)が tools_dict / form_data["tools"] に注入する。
    run_hooks(skill) は before / after を実行して記録の列を返す **async** フック
    (run_skill_hooks — tool の取り込みの後に呼ぶ。読み込み時の実行が skill 自身の tool を
    使えるように)。結果は before → 本文 → after の 1 つの load_skill の結果になる。

    **読み込み済みの skill は本文を返さない**(冗長ロードで本文が context に 2 回入るのを
    止める): このリクエストの messages(`/command` の合成 exchange・過去ターンの
    load_skill 呼び出し — 渡されたときだけ見る)か、同じターン内の先行呼び出しで
    既に読んだ skill は、短い結果(LOADED_RESULT)を返す。tool の遅延取り込みは
    冪等なのでそのまま行う。
    """
    if not is_skills_feature_enabled(model):
        return None
    skill_ids = get_model_skill_ids(model)
    if not skill_ids:
        return None

    manifest = {}
    for skill_id in skill_ids:
        skill = Skills.get_skill_by_id(skill_id)
        if (
            skill is not None
            and (skill.description or "").strip()
            and is_skill_visible(skill, user)
        ):
            manifest[skill.id] = skill

    if not manifest:
        return None

    # llens(#43): manifest 識別子は id 一本(旧 command)。id/name の二重露出はしない。
    listing = "\n".join(
        f"- {sid}: {skill.description.strip()}" for sid, skill in manifest.items()
    )
    spec = {
        "name": "load_skill",
        "description": (
            "skill を読み込む。作業の手順書 (自分向けの指示) を返すので、それに従って作業する。"
            "skill によっては読み込むと tool が増える (戻り値の tools)。"
            "手順書の見出しや文言は回答に写さない。使える skill:\n" + listing
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "読み込む skill の id。",
                    "enum": list(manifest.keys()),
                }
            },
            "required": ["skill_id"],
        },
    }

    # 本文が既に context にある skill id。履歴分は build 時に 1 回だけ読む
    # (ターン内の先行呼び出しは load_skill 自身が足す — 同じ tool エントリが
    # そのターンの native ループで使い回される)。
    loaded_skill_ids = set(iter_loaded_skill_ids(messages or []))

    async def load_skill(skill_id: str = ""):
        skill = manifest.get(normalize_command(skill_id))
        if skill is None:
            raise ToolCallError(
                f"skill が見つからない: {skill_id}",
                "load_skill の説明の一覧にある id をそのまま渡す。",
            )
        # 判定と記録は await より前(同じターンの並列呼び出しでも本文は 1 回)
        first = skill.id not in loaded_skill_ids
        loaded_skill_ids.add(skill.id)
        loaded_tool_names = []
        if load_tools is not None and skill.tool_ids:
            # load_tools は async フック(#28: 中で get_tools をワーカースレッド実行)
            loaded_tool_names = await load_tools(skill) or []
        if not first:
            result = LOADED_RESULT.format(skill_id=skill.id)
        else:
            records = []
            if run_hooks is not None and (skill.before or skill.after):
                records = await run_hooks(skill)
            result = compose_skill_result(
                skill.id, activate_skill(skill, user, model), records
            )
        # 戻り値は構造化 JSON {result, tools}(#35): 指示本文と「何が導入されたか」を
        # モデル/UI の双方に見せる。str のまま返す(ループは str を素通しする)。
        return json.dumps(
            {"result": result, "tools": loaded_tool_names},
            ensure_ascii=False,
        )

    # render.result = skill(#20 のディスクリプタ駆動): フロントは {result, tools} を
    # パースし、result を markdown ハイライト + tools を一覧で描画する。
    return {
        "spec": spec,
        "callable": load_skill,
        "render": {"result": {"kind": "skill"}},
    }


def skill_activation_seed_block(skill_activations) -> dict:
    """`/command` 発火・自動ロードをモデル自律起動と同形の tool_calls ブロックにする(#35)。

    process_chat_response が応答の先頭に置き、保存される — 次のリクエストからは履歴の
    load_skill 往復として届く(apply_skill_command / apply_skill_auto_load はそれを見て
    発火し直さない)。このリクエストの messages には同じ往復を注入済みなので、同じターンの
    追撃では `in_payload` の印で二重にしない(convert_content_blocks_to_messages。印は
    永続化 snapshot_content_blocks で落ちる)。
    """
    return {
        "type": "tool_calls",
        "in_payload": True,
        "content": [
            {
                "id": a["call_id"],
                "type": "function",
                "function": {
                    "name": "load_skill",
                    "arguments": json.dumps(
                        {"skill_id": a["id"]},
                        ensure_ascii=False,
                    ),
                },
                # {result, tools} を skill 表示で描画(#20/#35)
                "render": {"result": {"kind": "skill"}},
            }
            for a in skill_activations
        ],
        "results": [
            {"tool_call_id": a["call_id"], "content": a["result"]}
            for a in skill_activations
        ],
    }
