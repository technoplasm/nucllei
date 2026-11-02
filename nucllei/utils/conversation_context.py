"""会話の側に入れるもの — 会話の前置き・更新の枠・添付の `<source>` — を user 発言の先頭に置く。

system には入れない: system は「preset の system + チャットの system」だけで、記憶・レシピ・添付が変わっても
変わらない (prefix cache が会話全体に効く)。process_chat_payload が Filter の inlet・tool の組み立て・添付の
解決の後に `apply_conversation_side` を 1 回呼ぶ。tool ループの追撃・carried の計測は同じ messages を使う。

**前置き** (preset の params.context を展開したもの): その枝の最初の user 発言の先頭に

    <conversation-context>
    {展開 (前後の空白を strip。変数の値は utils/frames で無害化済み)}
    </conversation-context>

最初に組んだバイト列を chat_context (models/chat_context) に凍結し、毎ターン同じ位置に送り直す。新しい user 発言の
とき、前置きのテンプレートにある変数 ({{USER_RECIPES}} / {{USER_MEMORY_INDEX}}) の今の展開が、この枝でこれまでに
送った版と違えば、その発言の先頭に `<conversation-context-update>` で変わった節を丸ごと入れる (前のターンは
書き換えない)。再生成 (同じ user 発言) は保存したものを使い、sandbox に聞かない。

- 枝: chat-wire (`chat_id` + assistant の `id`) では DB の履歴でその assistant から親を遡った鎖の user 発言が単位。
  wire の user メッセージの目印 `id` は、鎖の上の user メッセージの id のときだけ位置決めに使う (中身は常に保存値)。
  目印が無い呼び手は、最初の wire user ↔ 鎖の最初の user、最後の wire user ↔ assistant の親、だけを位置で対応させる
- headless (chat_id 無し・chat が DB に無い): 毎リクエストその場で展開し、最初の user メッセージに置く。保存しない
- サブエージェントの子ターン (metadata の agent_session): 前置きも更新も入れない

**添付** (`<source>`): そのファイルを付けた user 発言の先頭 (前置き・更新の枠の後、本文の前) に、毎ターン同じ位置で
入れ直す。どの発言のものかは wire の目印 `files: [{"type": "file", "id": …}]`。入れるのは inlet 後の
`metadata["files"]` (読取権限・Filter を通った一覧) から組んだ sources だけ。同じファイルは会話の順で最初の発言だけ。
目印の無いファイルは最後の user 発言に。保存しない (ファイルの中身は抽出後に変わらないので毎回同じバイト)。

**添付の音声** (音声を聞けるモデルだけ — utils/audio_input): `<source>` と同じ発言の content の末尾に input_audio の
参照パートを足す (同じ置き場所の規則)。実体 (mp3 の base64) にするのは送信直前の inline_audio_refs。

**利用者の本文**も無害化する (枠を書けるのはサーバだけ)。目印 (`id` / `files`) は組み終えたら全メッセージから
取り除く (未知のキーで 400 にする OpenAI 互換実装がある)。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

from nucllei.env import SRC_LOG_LEVELS
from nucllei.utils.frames import CONTEXT_TAG, CONTEXT_UPDATE_TAG, defuse
from nucllei.utils.task import (
    CONTEXT_VARIABLES,
    expand_context_template,
    fetch_context_sections,
    preset_context_template,
    section_is_known,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

WIRE_MARKER_KEYS = ("id", "files")
UPDATE_LEAD = "会話の途中で次のものが変わった。前の版に代わってこちらが今の版 (ここに無いものは前のまま)。"
UNKNOWN = "unknown"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def frame_initial(expanded: str) -> str:
    """前置きの枠 (展開が空白だけなら "")。プレビューの `framed` も同じこれ。テンプレートの地の文 (配備が書く) は
    そのまま — 変数の値 (記憶の目次・レシピの一覧) は取ったときに無害化してある (task.fetch_context_sections)。"""
    body = (expanded or "").strip()
    if not body:
        return ""
    return f"<{CONTEXT_TAG}>\n{body}\n</{CONTEXT_TAG}>"


def frame_update(changed: list[str]) -> str:
    """更新の枠 (変わった節が無ければ "")。節は無害化済みの変数の値。"""
    body = "\n\n".join(s.strip() for s in changed if s and s.strip())
    if not body:
        return ""
    return f"<{CONTEXT_UPDATE_TAG}>\n{UPDATE_LEAD}\n\n{body}\n</{CONTEXT_UPDATE_TAG}>"


def template_variables(template: str) -> list[str]:
    """前置きのテンプレートにある前置きの変数 (書かれた順、重複なし)。"""
    found = []
    for token in CONTEXT_VARIABLES:
        pos = template.find(token)
        if pos >= 0:
            found.append((pos, token))
    return [token for _, token in sorted(found)]


def initial_state(template: str, sections: dict) -> dict:
    return {
        "template": _sha(template),
        "vars": {
            token: (_sha(text) if section_is_known(token, text) else UNKNOWN)
            for token, text in sections.items()
        },
    }


async def build_initial(user, model, template: str) -> tuple[str, dict]:
    """前置きのテンプレートを今の値で展開して枠に入れる。(枠込みのバイト列, state)。"""
    sections = await fetch_context_sections(user, model, template)
    text = frame_initial(expand_context_template(template, sections, model))
    return text, initial_state(template, sections)


async def build_update(
    user, model, template: str, prev_state: dict
) -> tuple[str, dict]:
    """前の state と今の展開を比べ、変わった節を丸ごと入れた更新の枠と、新しい state を返す。

    比べるのは今のテンプレートにある変数だけ。取れなかった (不通・古い sandbox) 節と空の節 (記憶が無い・能力
    「レシピ」OFF・sandbox 無し) は「変わった」としない (前の版のまま)。"""
    prev_vars = dict((prev_state or {}).get("vars") or {})
    tokens = template_variables(template)
    new_state = {"template": (prev_state or {}).get("template"), "vars": prev_vars}
    if not tokens:
        return "", new_state
    sections = await fetch_context_sections(user, model, template)
    changed = []
    for token in tokens:
        text = sections.get(token) or ""
        if not text.strip() or not section_is_known(token, text):
            continue
        sha = _sha(text)
        if prev_vars.get(token) == sha:
            continue
        changed.append(text)
        prev_vars[token] = sha
    return frame_update(changed), new_state


# ---------------------------------------------------------------------------
# 目印 (wire の `id` / `files`)
# ---------------------------------------------------------------------------


@dataclass
class _Markers:
    ids: dict[int, str] = field(default_factory=dict)  # wire の位置 -> message id
    files: dict[int, list[str]] = field(default_factory=dict)  # wire の位置 -> file id


def strip_wire_markers(messages: list) -> tuple[list, _Markers]:
    """user メッセージの目印を読み、全メッセージから目印のキーを取り除いた新しい list を返す (非破壊)。"""
    markers = _Markers()
    out = []
    for idx, message in enumerate(messages or []):
        if not isinstance(message, dict):
            out.append(message)
            continue
        if message.get("role") == "user":
            mid = message.get("id")
            if isinstance(mid, str) and mid:
                markers.ids[idx] = mid
            fids = []
            for entry in message.get("files") or []:
                if (
                    isinstance(entry, dict)
                    and entry.get("type") == "file"
                    and isinstance(entry.get("id"), str)
                    and entry["id"]
                    and entry["id"] not in fids
                ):
                    fids.append(entry["id"])
            if fids:
                markers.files[idx] = fids
        if any(key in message for key in WIRE_MARKER_KEYS):
            message = {k: v for k, v in message.items() if k not in WIRE_MARKER_KEYS}
        out.append(message)
    return out, markers


# ---------------------------------------------------------------------------
# 鎖 (chat-wire)
# ---------------------------------------------------------------------------


def resolve_chain(chat_id: str, assistant_id: str) -> Optional[tuple[list[str], str]]:
    """DB の履歴で assistant から親を遡った鎖の user メッセージの id (会話の順) と、assistant の親 (今の
    user 発言)。chat・assistant が無い、親が user でなければ None。"""
    from nucllei.models.chats import Chats

    chat = Chats.get_chat_by_id(chat_id)
    if chat is None:
        return None
    messages = ((chat.chat or {}).get("history") or {}).get("messages") or {}
    assistant = messages.get(assistant_id)
    if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
        return None
    current = assistant.get("parentId")
    parent = messages.get(current) if current else None
    if not isinstance(parent, dict) or parent.get("role") != "user":
        return None
    users = []
    seen = set()
    node_id = current
    while node_id and node_id not in seen:
        seen.add(node_id)
        node = messages.get(node_id)
        if not isinstance(node, dict):
            break
        if node.get("role") == "user":
            users.append(node_id)
        node_id = node.get("parentId")
    users.reverse()
    return users, current


async def _chain_frames(
    chat_id: str,
    chain: list[str],
    current: str,
    user,
    model,
    template: str,
) -> dict[str, str]:
    """鎖の user 発言ごとの枠 ({message id: 枠込みのバイト列})。今の発言に行が無ければ組んで書く。"""
    from nucllei.models.chat_context import ChatContexts

    rows = await asyncio.to_thread(ChatContexts.get_by_chat_id, chat_id)
    if current not in rows:
        ancestors = [rows[mid] for mid in chain if mid != current and mid in rows]
        has_initial = any(row.kind == "initial" for row in ancestors)
        prev_state = ancestors[-1].state if ancestors else None
        if not has_initial and template.strip():
            text, state = await build_initial(user, model, template)
            kind = "initial" if text else "none"
        elif has_initial and template.strip():
            text, state = await build_update(user, model, template, prev_state)
            kind = "update" if text else "none"
        else:
            text, state = "", (prev_state or {"template": None, "vars": {}})
            kind = "none"
        rows[current] = await asyncio.to_thread(
            ChatContexts.insert_once,
            chat_id,
            current,
            kind,
            text,
            state,
            (model or {}).get("id"),
        )
    return {mid: rows[mid].text for mid in chain if mid in rows and rows[mid].text}


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------


def _prepend(content, prefix: str):
    """利用者の本文 (無害化) の前に prefix を置く。parts なら最初の text パートの前に、text パートが無ければ
    先頭に text パートを 1 つ足す。"""

    def join(body: str) -> str:
        body = defuse(body or "")
        if not prefix:
            return body
        return f"{prefix}\n\n{body}" if body.strip() else prefix

    if isinstance(content, list):
        parts = []
        placed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                if not placed:
                    parts.append({**part, "text": join(part.get("text") or "")})
                    placed = True
                else:
                    parts.append({**part, "text": defuse(part.get("text") or "")})
            else:
                parts.append(part)
        if not placed and prefix:
            parts.insert(0, {"type": "text", "text": prefix})
        return parts
    return join(content if isinstance(content, str) else "")


async def apply_conversation_side(
    form_data: dict,
    *,
    user,
    model: dict,
    metadata: dict,
    sources: list[tuple[Optional[str], str]],
    audio_parts: Optional[list[tuple[str, dict]]] = None,
) -> None:
    """form_data["messages"] の user 発言の先頭に前置き・更新の枠・`<source>` を置き、目印を取り除く (in place で
    form_data["messages"] を新しい list に差し替える)。`sources` は (file id or None, `<source …>…</source>`) の列
    (inlet 後の metadata["files"] から組んだもの・その順)。`audio_parts` は (file id, input_audio の参照パート) の列で、
    そのファイルの `<source>` と同じ発言の content の末尾に足す。"""
    messages, markers = strip_wire_markers(form_data.get("messages") or [])
    user_idxs = [
        i
        for i, m in enumerate(messages)
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    if not user_idxs:
        if sources or audio_parts:
            raise Exception("No user message found")
        form_data["messages"] = messages
        return

    frames: dict[int, str] = {}
    if not (metadata or {}).get("agent_session"):
        template = await asyncio.to_thread(preset_context_template, model)
        chat_id = (metadata or {}).get("chat_id")
        assistant_id = (metadata or {}).get("message_id")
        resolved = None
        if chat_id and assistant_id:
            resolved = await asyncio.to_thread(resolve_chain, chat_id, assistant_id)
        if resolved is not None:
            chain, current = resolved
            by_id = await _chain_frames(
                chat_id, chain, current, user, model, template or ""
            )
            frames = _place_chain_frames(
                by_id, chain, current, user_idxs, markers, chat_id
            )
        elif (template or "").strip():
            text, _ = await build_initial(user, model, template)
            if text:
                frames[user_idxs[0]] = text

    # 添付: 目印の発言 (同じファイルは最初の発言だけ)、目印が無ければ最後の user
    first_marked: dict[str, int] = {}
    for idx in user_idxs:
        for fid in markers.files.get(idx, []):
            first_marked.setdefault(fid, idx)
    tags: dict[int, list[str]] = {}
    for fid, tag in sources:
        idx = first_marked.get(fid) if fid else None
        tags.setdefault(user_idxs[-1] if idx is None else idx, []).append(tag)
    appended: dict[int, list[dict]] = {}
    for fid, part in audio_parts or []:
        idx = first_marked.get(fid)
        appended.setdefault(user_idxs[-1] if idx is None else idx, []).append(part)

    out = list(messages)
    for idx in user_idxs:
        prefix = "\n\n".join(
            p for p in (frames.get(idx, ""), "\n".join(tags.get(idx, []))) if p
        )
        content = _prepend(messages[idx].get("content"), prefix)
        if appended.get(idx):
            content = _append_parts(content, appended[idx])
        out[idx] = {**messages[idx], "content": content}
    form_data["messages"] = out


def _append_parts(content, parts: list[dict]) -> list:
    """content の末尾にパートを足す。文字列なら text パートにしてから (空なら text パートは付けない —
    空 text を弾く推論エンジンがある)。"""
    if isinstance(content, list):
        return [*content, *parts]
    text = content if isinstance(content, str) else ""
    return [*([{"type": "text", "text": text}] if text.strip() else []), *parts]


def _place_chain_frames(
    by_id: dict[str, str],
    chain: list[str],
    current: str,
    user_idxs: list[int],
    markers: _Markers,
    chat_id: str,
) -> dict[int, str]:
    """鎖の枠を wire の user メッセージの位置へ。目印 `id` が鎖の上にあればそれ、無ければ最初・最後だけ位置で。"""
    chain_set = set(chain)
    placed: dict[int, str] = {}  # wire の位置 -> message id
    used = set()
    for idx in user_idxs:
        mid = markers.ids.get(idx)
        if mid in chain_set and mid not in used:
            placed[idx] = mid
            used.add(mid)
    if chain and chain[0] not in used and user_idxs[0] not in placed:
        placed[user_idxs[0]] = chain[0]
        used.add(chain[0])
    if current not in used and user_idxs[-1] not in placed:
        placed[user_idxs[-1]] = current
        used.add(current)
    missing = [mid for mid in by_id if mid not in used]
    if missing:
        log.warning(
            f"[context] wire に目印 `id` が無く、更新の枠を置けない発言がある (chat_id={chat_id}, "
            f"{len(missing)} 件)"
        )
    return {idx: by_id[mid] for idx, mid in placed.items() if mid in by_id}
