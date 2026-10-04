"""skill 発火ロジック(issue #35 / #42 / #43)。

`/command` 発火(apply_skill_command)・load_skill 擬似 tool
(build_load_skill_tool)・添付による自動ロード(apply_skill_auto_load)の
3 入口、およびその共通部(activate_skill / 可視性・機能フラグ判定)。
middleware(オーケストレーション)から issue #23 で分離した。設計の一次情報は _memos/skill-design.md。
"""

import json
import re

from nucllei.models.chats import Chats
from nucllei.models.files import Files
from nucllei.models.skills import Skills, normalize_command
from nucllei.utils.access_control import has_access, has_read_access_to_file
from nucllei.utils.file_images import INTERNAL_FILE_URL_PATTERN
from nucllei.utils.task import prompt_template, resolve_base_model_id
from nucllei.utils.tool_errors import ToolCallError

# 先頭行の行頭にある `/command` トークンを検出する正規表現(issue #35)。
SKILL_COMMAND_PATTERN = re.compile(r"^/([a-zA-Z0-9_-]+)")


def is_skill_visible(skill, user) -> bool:
    """read 可視性(access_control read。作成者は常に可視)。"""
    return skill.user_id == user.id or has_access(user.id, "read", skill.access_control)


def activate_skill(skill, user, model=None) -> str:
    """skill 発火の単一パス(issue #35): content を展開して返す。

    `/command` 起動(apply_skill_command)と load_skill 起動の両方がここを通る。
    変数はサーバー既知のもののみ({{USER_NAME}} / {{CURRENT_DATE}} / {{BASE_MODEL_ID}} 等。
    ブラウザ専用変数は無し。BASE_MODEL_ID は skill を発火させたターンのモデル = model の base)、
    毎リクエスト再展開(発火時点への固定はしない — _memos/skill-design.md「発火の有効範囲」)。

    tool_ids の遅延取り込み(#35 step2b)は呼び出し側が担う:
    `/command` は process_chat_payload の skill_tool_ids 集約、load_skill は
    build_load_skill_tool の load_tools フック(どちらも get_tools を
    enforce_access_control=False で呼ぶ — skill の read 可視性が認可の単位)。
    """
    return prompt_template(
        skill.content,
        user_name=user.name,
        base_model_id=resolve_base_model_id(model),
    )


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

        # id はモデル由来の call(functions.load_skill:N)と衝突しない自前形式。
        exchange, activation = synthesize_load_skill_exchange(
            skill, user, model, f"skill:{skill.id}:{idx}", idx == last_user_idx
        )
        new_messages.extend(exchange)
        activations.append(activation)

    form_data["messages"] = new_messages
    return form_data, activations


def synthesize_load_skill_exchange(skill, user, model, call_id, current):
    """「load_skill を呼んだことにする」合成 exchange と発火情報を作る。

    `/command`(入口 1)と添付による自動ロード(入口 3)の共通部。戻り値は
    ([assistant tool_call, tool result], activation)。
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
        "current": current,
    }
    return exchange, activation


def iter_loaded_skill_ids(messages):
    """履歴中の load_skill 呼び出しが指す skill id を順に返す(正規化済み)。

    assistant の tool_calls に加え、UI 由来の履歴が content_blocks 形で来る経路も読む
    (synthetic = `/command`・自動ロードの合成 exchange は、発火側が毎リクエスト
    再導出して tool_calls 形で注入するので除外)。
    """
    for message in messages:
        if message.get("role") != "assistant":
            continue
        calls = list(message.get("tool_calls") or [])
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
            if not isinstance(arguments, dict):
                continue
            skill_id = arguments.get("skill_id")
            if isinstance(skill_id, str) and normalize_command(skill_id):
                yield normalize_command(skill_id)


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


def _current_turn_file_ids(metadata) -> set[str]:
    """いま応答しようとしている user メッセージに付いた添付の file id(チャット DB から)。

    チャットの添付一覧は「どの発言に付いたか」を運ばないので、自動ロードのカードを
    「添付が付いたターン」にだけ出すための判定に使う(発火・認可・tool の有効化には
    使わない — それらは payload だけで決まる)。chat 文脈が無ければ空。
    """
    chat_id = (metadata or {}).get("chat_id")
    message_id = (metadata or {}).get("message_id")
    if not chat_id or not message_id:
        return set()
    chat = Chats.get_chat_by_id(chat_id)
    if chat is None:
        return set()
    history = ((chat.chat or {}).get("history") or {}).get("messages") or {}
    parent = history.get((history.get(message_id) or {}).get("parentId")) or {}
    if parent.get("role") != "user":
        return set()
    return {
        file.get("id")
        for file in parent.get("files") or []
        if isinstance(file, dict) and file.get("id")
    }


def apply_skill_auto_load(form_data, user, model, metadata=None):
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
    - **持続**: 状態は持たない。添付は毎リクエスト payload に載って来るので、発火も
      毎リクエスト再導出される(後のターンでも tool が載り続ける)。
    - **二重発火しない**: 履歴に同じ skill の load_skill(`/command` の合成 exchange・
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
    turn_file_ids = None  # 要るときだけ DB を読む

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
            if turn_file_ids is None:
                turn_file_ids = _current_turn_file_ids(metadata)
            current = any(fid in turn_file_ids for fid in chat_hits)
        elif message_hits:
            anchor = min(message_hits)
            current = anchor == user_idxs[-1]
        else:
            continue

        exchange, activation = synthesize_load_skill_exchange(
            skill, user, model, f"skill:{skill.id}:auto", current
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
    """今回のターンの `/command` 発火・自動ロードを UI seed 用 metadata に載せる形へ絞る。

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
            "skill を読み込む。作業の手順書 (自分向けの指示) を返すので、それに従って作業する。"
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

    async def load_skill(skill_id: str = ""):
        skill = manifest.get(normalize_command(skill_id))
        if skill is None:
            raise ToolCallError(
                f"skill が見つからない: {skill_id}",
                "load_skill の説明の一覧にある id をそのまま渡す。",
            )
        loaded_tool_names = []
        if load_tools is not None and skill.tool_ids:
            # load_tools は async フック(#28: 中で get_tools をワーカースレッド実行)
            loaded_tool_names = await load_tools(skill) or []
        # 戻り値は構造化 JSON {result, tools}(#35): 指示本文と「何が導入されたか」を
        # モデル/UI の双方に見せる。str のまま返す(ループは str を素通しする)。
        return json.dumps(
            {
                "result": activate_skill(skill, user, model),
                "tools": loaded_tool_names,
            },
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
