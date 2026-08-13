"""skill 発火ロジック(issue #35 / #42 / #43)。

`/command` 発火(apply_skill_command)と load_skill 擬似 tool
(build_load_skill_tool)の 2 入口、およびその共通部(activate_skill /
可視性・機能フラグ判定)。middleware(オーケストレーション)から
issue #23 で分離した。設計の一次情報は _memos/skill-design.md。
"""

import json
import re

from nucllei.models.skills import Skills, normalize_command
from nucllei.utils.access_control import has_access
from nucllei.utils.task import prompt_template

# 先頭行の行頭にある `/command` トークンを検出する正規表現(issue #35)。
SKILL_COMMAND_PATTERN = re.compile(r"^/([a-zA-Z0-9_-]+)")


def is_skill_visible(skill, user) -> bool:
    """read 可視性(access_control read。作成者は常に可視)。"""
    return skill.user_id == user.id or has_access(user.id, "read", skill.access_control)


def activate_skill(skill, user) -> str:
    """skill 発火の単一パス(issue #35): content を展開して返す。

    `/command` 起動(apply_skill_command)と load_skill 起動の両方がここを通る。
    変数はサーバー既知のもののみ({{USER_NAME}} / {{CURRENT_DATE}} 等。ブラウザ専用変数は無し)、
    毎リクエスト再展開(発火時点への固定はしない — _memos/skill-design.md「発火の有効範囲」)。

    tool_ids の遅延取り込み(#35 step2b)は呼び出し側が担う:
    `/command` は process_chat_payload の skill_tool_ids 集約、load_skill は
    build_load_skill_tool の load_tools フック(どちらも get_tools を
    enforce_access_control=False で呼ぶ — skill の read 可視性が認可の単位)。
    """
    return prompt_template(skill.content, user_name=user.name)


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

    ペイロード内の**全 user メッセージ**を毎リクエスト走査する。発火はその会話
    (現在のメッセージチェーン)で持続し、会話そのものが唯一の状態(2026-07-06 改訂)。

    - 各 user メッセージの先頭行の行頭が `/([a-zA-Z0-9_-]+)` なら、その command で
      read 可視な skill を lookup する。
    - ミス: 何もしない(通常テキストとして素通し。エラーにしない)。
    - 発火条件は AND(issue #42): 機能「スキル」がモデルで有効 ∧ その skill がモデルに
      紐づく(meta.skillIds)∧ ユーザーに read 可視。モデル設定を唯一の制御面にする
      (従来の「可視ならどのモデルでも発火」から変更 — _memos/builtin-tools.md 1b)。

    戻り値は (form_data, 発火リスト)。発火リストの要素は
    {call_id, command, title, content(展開済み), current(最後の user メッセージでの発火か)}。
    current な発火は UI 表示用に応答先頭の tool_calls ブロック(synthetic)にも seed される。
    """
    activations = []

    if not is_skills_feature_enabled(model):
        return form_data, activations
    model_skill_ids = set(get_model_skill_ids(model))

    messages = form_data.get("messages", [])
    last_user_idx = None
    for idx, message in enumerate(messages):
        if message.get("role") == "user":
            last_user_idx = idx

    new_messages = []
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
        ):
            continue

        expanded = activate_skill(skill, user)
        # id はモデル由来の call(functions.load_skill:N)と衝突しない自前形式。
        call_id = f"skill:{skill.id}:{idx}"
        arguments = json.dumps({"skill_id": skill.id}, ensure_ascii=False)
        # 戻り値は構造化 JSON {result, tools}(#35): 導入された tool 名は
        # 後段(process_chat_payload の skill tools ロード)で書き足される。
        result_content = json.dumps(
            {"result": expanded, "tools": []}, ensure_ascii=False
        )

        new_messages.append(
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
            }
        )
        result_message = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": result_content,
        }
        new_messages.append(result_message)

        activations.append(
            {
                "call_id": call_id,
                "id": skill.id,
                "name": skill.name,
                "content": expanded,
                "result": result_content,
                # 導入 tool 名を後段で書き戻すための参照(dict は共有される)
                "result_message": result_message,
                "tool_ids": skill.tool_ids or [],
                "current": idx == last_user_idx,
            }
        )

    form_data["messages"] = new_messages
    return form_data, activations


def replay_skill_tool_ids(form_data, user, model) -> list[str]:
    """履歴中の load_skill 呼び出しから skill の tool_ids を再導出する(issue #101)。

    モデル自律の load_skill(入口 2)は発火ターンの tools_dict に足すだけで、
    次のリクエストで再導出する経路が無かった — `/command`(入口 1)が毎リクエスト
    再導出されるのと非対称で、「1 ターン目に軽い tool → 2 ターン目に本命」の
    自然な流れで tool not available になる。ここで履歴(form_data["messages"])の
    assistant tool_calls / content_blocks から load_skill 発火を拾い直す。

    **認可を履歴に委ねない**: 履歴はクライアント入力なので「過去に呼ばれた事実」は
    根拠にせず、`/command` と同じ AND 条件(機能「スキル」有効 ∧ モデル紐づき
    meta.skillIds ∧ read 可視)をその場で再確認する。preset から skill を外せば
    履歴があっても tool は復活しない。

    skill 本文の再注入はしない(tool result として履歴に残っている)。headless
    (chat_id 無し)でも messages は来るので同じ処理で足りる。

    将来の動的 tool 源(MCP 等)を足すときもこの形を踏襲する: 「導入 tool の
    持続性は会話履歴が唯一の状態」+「復活時はその場で再認可」。tool_calls の
    走査は function.name で分岐しているので、別の導入 tool 名を足す拡張点は
    この関数に閉じる。
    """
    if not is_skills_feature_enabled(model):
        return []
    model_skill_ids = set(get_model_skill_ids(model))
    if not model_skill_ids:
        return []

    tool_ids: list[str] = []
    seen_skill_ids: set[str] = set()

    def collect(skill_id):
        if not isinstance(skill_id, str):
            return
        skill_id = normalize_command(skill_id)
        if not skill_id or skill_id in seen_skill_ids:
            return
        seen_skill_ids.add(skill_id)
        skill = Skills.get_skill_by_id(skill_id)
        if (
            skill is None
            or skill.id not in model_skill_ids
            or not is_skill_visible(skill, user)
        ):
            return
        for tool_id in skill.tool_ids or []:
            if tool_id not in tool_ids:
                tool_ids.append(tool_id)

    for message in form_data.get("messages", []):
        if message.get("role") != "assistant":
            continue
        calls = list(message.get("tool_calls") or [])
        # UI 由来の履歴が content_blocks 形で来る経路にも備える(synthetic =
        # `/command` の合成 exchange は apply_skill_command が毎回再導出するので除外)
        for block in message.get("content_blocks") or []:
            if block.get("type") == "tool_calls" and not block.get("synthetic"):
                calls.extend(block.get("content") or [])
        for call in calls:
            function = (call or {}).get("function") or {}
            if function.get("name") != "load_skill":
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(arguments, dict):
                collect(arguments.get("skill_id"))

    return tool_ids


def current_skill_activation_seeds(skill_activations) -> list:
    """今回のターンの `/command` 発火を UI seed 用 metadata に載せる形へ絞る。

    受け側は process_chat_response(応答先頭の synthetic tool_calls ブロック)で、
    call_id / id / result の 3 キーを読む。#43 で skill の可読キーを command→id に
    改名した際、ここだけ旧キー参照が残り KeyError になっていた(回帰テストあり)。
    """
    return [
        {"call_id": a["call_id"], "id": a["id"], "result": a["result"]}
        for a in skill_activations
        if a["current"]
    ]


def build_load_skill_tool(model, user, load_tools=None):
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
            "Load a skill: returns instructions you must follow to perform "
            "a specific kind of task. Available skills:\n" + listing
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "Id of the skill to load.",
                    "enum": list(manifest.keys()),
                }
            },
            "required": ["skill_id"],
        },
    }

    async def load_skill(skill_id: str = ""):
        skill = manifest.get(normalize_command(skill_id))
        if skill is None:
            return f"skill not found: {skill_id}"
        loaded_tool_names = []
        if load_tools is not None and skill.tool_ids:
            # load_tools は async フック(#28: 中で get_tools をワーカースレッド実行)
            loaded_tool_names = await load_tools(skill) or []
        # 戻り値は構造化 JSON {result, tools}(#35): 指示本文と「何が導入されたか」を
        # モデル/UI の双方に見せる。str のまま返す(ループは str を素通しする)。
        return json.dumps(
            {"result": activate_skill(skill, user), "tools": loaded_tool_names},
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
    """`/command` 発火をモデル自律起動と同形の tool_calls ブロックにする(#35)。

    process_chat_response が応答先頭の seed として使う
    「View Result from load_skill」表示用。synthetic マーカ付き =
    モデル向け履歴には再送しない(実体の tool exchange は
    apply_skill_command が messages に注入済み)。
    """
    return {
        "type": "tool_calls",
        "synthetic": True,
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
