"""サーバ駆動配達 driver(#110 契約 T3 / M3〜M5)。

バックグラウンドタスク(execution)の完了結果を、FE(ブラウザが開いていること)に
依存せず **BE が**親 chat へ配達する:

1. タスク終了(または chat ターン終了・boot・stop)で chat 単位の driver が起きる
2. 親 chat にターンが走行していないこと(静穏)を確認する
3. 未配達 terminal 行から通知メッセージ(notice — user ロール・
   meta.system_notice 付き)+ 空 assistant 対を組み立て、CAS 単一トランザクションで
   履歴へ追記 + delivered_at 確定(models/chats.py append_delivery_turn)
4. 続きターンを内部起動する(subagent.start_internal_turn の共有 3 段 — 契約 M5)。
   モデルは notice を読んで自律継続する

設計原則:
- delivered_at は「notice が履歴に載った(= 結果がモデルの文脈に載る)」事実。
  一度立てたら戻さない。配達先が消えた(chat 不在・agent chat)場合も立てる
  (恒久断念 — 幽霊配達を残さない)
- 単一プロセス(D7)を最大限使う: driver registry・クレームはプロセス内 dict。
  `async_exec_available()` でなければ driver 全体が無効
- wake はイベント駆動のみ(ポーリングしない)。起点は 4 つ:
  ① execution runner の終了(tasks.py cleanup hook の `exec:{chat_id}`)
  ② stop の CAS 勝者(routers/chats.py の stop エンドポイント)
  ③ boot スキャン(list_chat_ids_with_undelivered)
  ④ chat ターンの終了(tasks.py cleanup hook の素の chat_id — 静穏化の通知)
- 連鎖上限(ASYNC_EXEC_RESUME_CHAIN_LIMIT): 人間の発話なしに続く無人ターンを
  chat 単位で抑える。判定は currentId からの parent 鎖遡行で、
  meta.system_notice 付き user の連続数(assistant はスキップ・人間 user で切れる)。
  上限到達中は配達を保留し、次の人間発話のターン終了(wake ④)で自然に解除される
- turn 起動の失敗は 2 分類: モデル id 解決不能 = 恒久(log + 断念 — notice は
  履歴に残っているので次のユーザー発話でモデルが読む)/ それ以外(SGLang 断 等)=
  transient(backoff 上限 5 分で **ターンのみ** 再試行。notice は再 append しない。
  再試行前に履歴が自分の空 assistant より進んでいたら中止 — 誰かのターンが
  結果を消化済み)
"""

import asyncio
import json
import logging
import time
import urllib.parse
from typing import Optional

from fastapi import Request

from nucllei import tasks
from nucllei.env import ASYNC_EXEC_RESUME_CHAIN_LIMIT, SRC_LOG_LEVELS
from nucllei.models.chats import Chats
from nucllei.models.executions import ExecutionModel, Executions
from nucllei.models.users import Users
from nucllei.utils.content_blocks import convert_content_blocks_to_messages
from nucllei.utils.executions import (
    ORPHANED_ERROR,
    STOPPED_ERROR,
    async_exec_available,
)
from nucllei.utils.image_refs import split_page, stored_ref_kind
from nucllei.utils.task import prompt_template, resolve_base_model_id

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# 配達の全体同時数(プロセス全体)。1 chat = 1 driver だが、多数 chat の boot
# スキャン等で内部ターン起動が殺到しないよう配達サイクル自体を絞る。
DELIVERY_MAX_CONCURRENT = 4

# CAS 追記(append_delivery_turn)の conflict 即時再試行の上限。超えたら
# transient 扱い(backoff)に落とす — FE 保存が連打されている chat で空回りしない。
APPEND_CAS_MAX_ATTEMPTS = 5

# transient 失敗の backoff(秒)。初期値 → 2 倍ずつ → 上限 5 分・無限回。
TURN_RETRY_INITIAL_BACKOFF_S = 2.0
TURN_RETRY_MAX_BACKOFF_S = 300.0

# notice ヘッダ(契約 M4 — 機械可読な固定書式。テストで固定)
# モデルが回答へ写しうる文面なので命令形・markdown 見出しを持たない(2026-10-04 監査)
NOTICE_HEADER = (
    "[task] バックグラウンドタスクの完了通知 (システムからの自動通知。"
    "ユーザーの発言ではない)。結果は下にあり、成果物の画像・ファイルはまだ画面に表示されて"
    "いない。次の手: await_task で取り直さない。成果物を見せるなら本文にリンクを書く。"
    "この通知の書式 (task_id: / status: / note: の行) は回答に写さない。"
)

# meta.system_notice の値(FE 描画・連鎖カウントの唯一の根拠 — B-4 根治)
SYSTEM_NOTICE_KIND = "task"

# ---- プロセス内状態(単一プロセスが不変条件 — D7)----

# lifespan で install() された FastAPI app(driver の内部ターン起動に使う)
_app = None

# chat_id → driver task / wake イベント。driver は 1 chat 1 本
# (wake の同期 check-and-set が保証)で、未配達が尽きたら自分を掃除して終了する。
_delivery_tasks: dict[str, asyncio.Task] = {}
_delivery_events: dict[str, asyncio.Event] = {}

# append〜ターン起動の途中である chat(wait_for_quiet_delivery が FE ターンを
# 短く待たせる窓 — 契約 M3)。
_delivering: set[str] = set()

# 配達サイクルでクレーム済みの exec 行(プロセス内・防御的 — 同一行の二重配達を
# 同期的に排除する)。
_claimed_exec_ids: set[str] = set()

# mark_replayed に勝って media(context_images / display)を消費した行の控え。
# CAS 追記が負けて notice を組み直しても media を失わないための持ち越し。
# 配達確定(delivered)で捨てる。自分以外の経路(await_task / stop_task / 恒久
# 断念)が配達を確定した行は _sweep_consumed_media が回収する(敵対検証 6/7)。
_consumed_media: dict[str, tuple[list, list]] = {}

# 配達 semaphore。asyncio primitive はループに遅延束縛されるため、テスト
# (asyncio.run 毎に新ループ)でも使えるようループが変わったら作り直す。
_semaphore: Optional[asyncio.Semaphore] = None
_semaphore_loop = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(DELIVERY_MAX_CONCURRENT)
        _semaphore_loop = loop
    return _semaphore


def _on_task_cleanup(namespace_id):
    """tasks.py の cleanup hook(wake ①④)。

    名前空間 id が `exec:{chat_id}` = execution runner の終了(結果は runner が
    終了前に永続化済み)、素の chat_id = chat ターンの終了(静穏化)。どちらも
    その chat の driver を起こすだけ — 未配達が無ければ driver は即座に終了する。
    """
    if not namespace_id or not isinstance(namespace_id, str):
        return
    if namespace_id.startswith("exec:"):
        wake(namespace_id[len("exec:") :])
    else:
        wake(namespace_id)


async def install(app) -> None:
    """lifespan から呼ぶ(boot — sweep 後)。

    app を控え、tasks cleanup hook を登録し、未配達 terminal 行を持つ chat を
    スキャンして driver を起こす(wake ③)。multi-worker(D7)では全体無効。
    """
    global _app
    if not async_exec_available():
        return
    _app = app
    tasks.register_cleanup_listener(_on_task_cleanup)
    for chat_id in await asyncio.to_thread(Executions.list_chat_ids_with_undelivered):
        wake(chat_id)


def wake(chat_id: str) -> None:
    """chat の配達 driver を起こす(無ければ生成 — 同期 check-and-set)。

    イベントループ上からのみ呼ぶ(done callback / エンドポイント / lifespan)。
    check→create の間に await を挟まないので driver は 2 本立たない。
    別ループの残骸(プロセス内で複数ループが生まれるのはテストのみだが、
    done 済み task が finally を踏めず登録簿に残るケースの防御)は作り直す。
    """
    if not chat_id or _app is None or not async_exec_available():
        return
    loop = asyncio.get_running_loop()
    existing = _delivery_tasks.get(chat_id)
    if existing is not None and (existing.done() or existing.get_loop() is not loop):
        _delivery_tasks.pop(chat_id, None)
        _delivery_events.pop(chat_id, None)
        existing = None
    event = _delivery_events.get(chat_id)
    if event is None:
        event = asyncio.Event()
        _delivery_events[chat_id] = event
    event.set()
    if existing is None:
        _delivery_tasks[chat_id] = loop.create_task(_delivery_loop(chat_id))


async def wait_for_quiet_delivery(chat_id: str, timeout: float = 5.0) -> None:
    """driver が append〜ターン起動の途中なら FE ターンを短く待たせる(契約 M3)。

    chat_completion(main.py)の入口で呼ぶ。超過時は通す(FE 側は save 400
    リカバリで再接ぎできる — 待ちはレース窓を狭める緩和であって排他ではない)。
    """
    if chat_id not in _delivering:
        return
    deadline = time.monotonic() + timeout
    while chat_id in _delivering and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


def count_notice_chain(chat_json: dict) -> int:
    """currentId から parent 鎖を遡り、連続する配達 notice(user ロール ∧
    meta.system_notice)の数を返す(契約 M3 手順 3)。

    user ロールのみを数え、assistant はスキップする。notice でない user
    (人間の発話)で連鎖は切れる。seen set でサイクル(DB 破損)をガード。
    """
    history = (chat_json or {}).get("history") or {}
    messages_map = history.get("messages") or {}
    current_id = history.get("currentId")

    count = 0
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        message = messages_map.get(current_id)
        if not isinstance(message, dict):
            break
        if message.get("role") == "user":
            if (message.get("meta") or {}).get("system_notice"):
                count += 1
            else:
                break
        current_id = message.get("parentId")
    return count


def is_resume_suspended(chat_json: dict) -> bool:
    """連鎖上限で配達保留中か(一覧 API の resume_suspended — チップ表示用)。"""
    return count_notice_chain(chat_json) >= ASYNC_EXEC_RESUME_CHAIN_LIMIT


# ---- notice 組み立て(契約 M4)----


def _fence(text: str) -> str:
    """本文中の最長 `~` 連より 1 長いフェンス(最低 4)— バッククォートと衝突しない。"""
    longest = 0
    run = 0
    for ch in text:
        if ch == "~":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "~" * max(4, longest + 1)


def _fenced(text: str) -> str:
    fence = _fence(text)
    return f"{fence}\n{text}\n{fence}"


def _notice_section(row: ExecutionModel, media: Optional[tuple[list, list]]) -> str:
    lines = [f"task_id: {row.id} ({row.tool_name})"]
    if row.status == "done":
        if row.duration is not None:
            lines.append(f"status: done(duration: {row.duration}s)")
        else:
            lines.append("status: done")
        lines.append("result:")
        lines.append(_fenced(row.result or ""))
    elif row.status == "failed":
        lines.append("status: failed")
        lines.append(f"error: {row.error or ''}")
    elif row.status == "stopped":
        lines.append("status: stopped")
        lines.append(f"note: {STOPPED_ERROR}")
    else:  # orphaned
        lines.append("status: orphaned")
        lines.append(f"note: {ORPHANED_ERROR}")

    displays = media[1] if media else []
    if displays:
        display_text = "\n\n".join(
            item.get("content", "") for item in displays if isinstance(item, dict)
        )
        if display_text:
            lines.append("display:")
            lines.append(_fenced(display_text))
    return "\n".join(lines)


def _notice_files(context_images: list) -> list:
    """context_images(保存済みの参照文字列)→ notice 添付 files(契約 M4)。

    載せるのは **ブラウザが画像として開ける物だけ**(フロントに壊れた img を出さない。
    モデルへの再注入は別経路で行われる)。振り分けは `image_refs.stored_ref_kind` の
    1 箇所の規則に従う — ここに独自の先頭判定を持たない:

    - `url`(`http(s)://…` と同一サーバの `/ext/{name}/…`)→ url としてそのまま載せる
      (相対 `/ext/…` はブラウザが session cookie で開ける。cage の図はこの形)
    - `file_id`(裸の id)→ `/api/v1/files/{id}/content` に組んで載せる
    - `path`(`/workspace/…`)→ sandbox の workspace のパスはブラウザでは開けないので落とす
      (`/ext/…` は URL と同じ文字列なので上の `url` に当たる)
    - `file_url`(`/api/v1/files/{id}/content#page=N` — 添付 PDF のページ参照)と、
      `#page=N` の付いた参照全般 → PDF のページは送信直前に画像化される物で、ブラウザは
      そのまま img として開けないので落とす
    - それ以外(どの形でもない)→ 落とす
    """
    files = []
    for entry in context_images:
        kind = stored_ref_kind(entry)
        if kind is None or split_page(entry)[1] is not None:
            continue
        if kind == "url":
            files.append({"type": "image", "url": entry})
        elif kind == "file_id":
            files.append(
                {
                    "type": "image",
                    "id": entry,
                    "url": f"/api/v1/files/{entry}/content",
                    "name": "task-image",
                    "status": "uploaded",
                }
            )
    return files


def build_delivery_notice(
    rows: list[ExecutionModel], media_by_id: dict[str, tuple[list, list]]
) -> tuple[str, list]:
    """未配達 terminal 行から notice 本文と添付 files を組み立てる(契約 M4)。

    切詰めは行の保存時に済んでいる(生産時一度だけの原則 — M0)ため、ここは
    切詰め済み result の透過的な中継。media_by_id は mark_replayed に勝った行の
    (context_images, display) — 負けた行(await_task が既に注入済み)は結果のみ。
    """
    sections = [_notice_section(row, media_by_id.get(row.id)) for row in rows]
    text = NOTICE_HEADER + "\n\n" + "\n\n".join(sections)
    files = []
    for row in rows:
        media = media_by_id.get(row.id)
        if media:
            files.extend(_notice_files(media[0]))
    return text, files


# ---- 続きターンの wire 組み立て(契約 M5 — wire 忠実性)----


def _parse_stop_sequences(stop) -> Optional[list[str]]:
    """FE parseStopSequences(chat-messages.ts)の対応物。

    chat params はカンマ区切り文字列、settings 由来は配列のことがある — どちらも
    受けて、"\\n" 等のエスケープ表記を実文字へ戻す。
    """
    if stop is None or stop == "":
        return None
    tokens = (
        [token.strip() for token in stop.split(",")]
        if isinstance(stop, str)
        else list(stop)
    )
    decoded = []
    for token in tokens:
        try:
            decoded.append(
                urllib.parse.unquote(json.loads('"' + token.replace('"', '\\"') + '"'))
            )
        except Exception:
            decoded.append(token)
    return decoded


def _merged_params(chat_json: dict, user) -> dict:
    """FE 送信時と同一のマージ順: {...user.settings params, ...chat.params}
    + stop の配列化(契約 M5)。"""
    settings = getattr(user, "settings", None)
    ui = (getattr(settings, "ui", None) or {}) if settings else {}
    settings_params = ui.get("params") or {}
    chat_params = (chat_json or {}).get("params") or {}
    params = {**settings_params, **chat_params}
    stop = _parse_stop_sequences(chat_params.get("stop", settings_params.get("stop")))
    if stop is not None:
        params["stop"] = stop
    else:
        params.pop("stop", None)
    return params


def _user_message_to_wire(message: dict) -> dict:
    """user メッセージを wire 形へ(FE buildWireMessages と同等)。

    files の type:'image' は image_url パートへ展開する。url が "/" 始まりは
    相対 URL としてそのまま、無ければ file id から content URL を組む
    (content_blocks.py と同分岐)。base64 化は送信直前の
    inline_internal_image_urls に一任。
    """
    content = message.get("content") or ""
    image_urls = []
    for file in message.get("files") or []:
        if not isinstance(file, dict) or file.get("type") != "image":
            continue
        url = file.get("url")
        if not url and file.get("id"):
            url = f"/api/v1/files/{file['id']}/content"
        if url:
            image_urls.append(url)
    if not image_urls:
        return {"role": "user", "content": content}
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": content},
            *({"type": "image_url", "image_url": {"url": url}} for url in image_urls),
        ],
    }


def build_wire_messages(chat_json: dict, user, leaf_id: str, model=None) -> list:
    """chat の history 樹から続きターンの wire(OpenAI messages)を組む(契約 M5)。

    FE buildWireMessages(chat-messages.ts)の backend 対応物:
    - system: chat.params.system をテンプレート展開(prompt_template — FE
      send.ts と同等)して先頭へ
    - user: files(type:'image')の image_url 展開込み
    - assistant: content_blocks があれば convert_content_blocks_to_messages
      (壊れ tool_calls の修復込み)、無ければ content 文字列。
      空 assistant(今回の応答先)は自然に落ちる
    サイクル(DB 破損)は seen set でガードした自前遡行で防ぐ。
    """
    history = (chat_json or {}).get("history") or {}
    messages_map = history.get("messages") or {}

    chain = []
    current_id = leaf_id
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        message = messages_map.get(current_id)
        if not isinstance(message, dict):
            break
        chain.insert(0, message)
        current_id = message.get("parentId")

    wire = []
    system = ((chat_json or {}).get("params") or {}).get("system")
    if system:
        wire.append(
            {
                "role": "system",
                "content": prompt_template(
                    system,
                    getattr(user, "name", None),
                    base_model_id=resolve_base_model_id(model),
                ),
            }
        )
    for message in chain:
        role = message.get("role")
        if role == "user":
            wire.append(_user_message_to_wire(message))
        elif role == "assistant":
            blocks = message.get("content_blocks") or []
            if blocks:
                wire.extend(convert_content_blocks_to_messages(blocks))
            elif (message.get("content") or "").strip():
                wire.append({"role": "assistant", "content": message["content"]})
    return wire


def _resolve_turn_model_id(chat_json: dict) -> Optional[str]:
    """続きターンのモデル id: 直近 assistant の model、無ければ chat.models[0]。"""
    history = (chat_json or {}).get("history") or {}
    messages_map = history.get("messages") or {}
    current_id = history.get("currentId")
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        message = messages_map.get(current_id)
        if not isinstance(message, dict):
            break
        if message.get("role") == "assistant" and message.get("model"):
            return message["model"]
        current_id = message.get("parentId")
    return ((chat_json or {}).get("models") or [None])[0]


def _shim_request() -> Request:
    """driver には受信 Request が無い — app だけ持つ内部 Request を作る。

    request.url が例外にならない最小 scope で、自オリジン(Host / X-Forwarded-*)は
    持たない — 続きターンの tool は __request__ から origin を読めない(受信 Request の
    origin を exec 行 meta["origin"] に残して運ぶ仕組みは 2026-10-03 に撤去。既存行に
    残る meta["origin"] は読まない)。
    """
    # 循環 import 回避: subagent → middleware → (executions ↔ 本モジュールの hook)
    from nucllei.utils.subagent import internal_request

    return internal_request(_app)


async def _ensure_models_loaded(user) -> None:
    """models は lazy(app.state.MODELS は初回リクエストまで空)。boot 直後の
    配達でも解決できるよう、ターン起動前に get_all_models 相当を必ず踏む。"""
    if getattr(_app.state, "MODELS", None):
        return
    # 循環 import 回避(utils.models → routers → ... の連鎖を import 時に固定しない)
    from nucllei.utils.models import get_all_models

    await get_all_models(_shim_request(), user=user)


async def _sweep_consumed_media() -> None:
    """delivered 済み行の _consumed_media を回収する(#110 敵対検証 6/7)。

    _consumed_media は CAS 追記の再試行で media を失わないための控えで、正常系は
    配達確定時に pop される。しかし配達確定を**他の経路**が済ませた終端 —
    "missing" 断念・手順 1 断念(chat 消滅)・await_task / stop_task が先に
    配達した行("empty" 終端)・conflict 後 abandoned — では pop する者がおらず
    プロセス寿命いっぱい残留する(リーク)。delivered_at が立った行(= 以後
    どの経路も media を使わない)と行自体が消えた分をここで捨てる。
    サイクルの各終端(手順 0/1 の早期 return と finally)から呼ぶ。
    """
    if not _consumed_media:
        return
    for exec_id in list(_consumed_media):
        row = await asyncio.to_thread(Executions.get_execution_by_id, exec_id)
        if row is None or row.delivered_at is not None:
            _consumed_media.pop(exec_id, None)


# ---- driver 本体 ----


async def _delivery_loop(chat_id: str) -> None:
    """chat 単位の配達 driver。未配達が尽きるまでサイクルを回し、自分を掃除して
    終了する。予期しない例外はループを殺さない(log + backoff)。"""
    event = _delivery_events[chat_id]
    error_backoff = TURN_RETRY_INITIAL_BACKOFF_S
    try:
        while True:
            event.clear()
            try:
                outcome = await _deliver_cycle(chat_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(f"delivery cycle failed (chat_id={chat_id})")
                outcome = "retry"

            if outcome in ("empty", "abandoned"):
                # wake がサイクル中に来ていたら(新しい終端行の可能性)もう 1 周
                if event.is_set():
                    continue
                return
            if outcome == "delivered":
                error_backoff = TURN_RETRY_INITIAL_BACKOFF_S
                continue
            if outcome in ("busy", "chain_limited"):
                # イベント駆動: 次のターン終了(wake ④)/ stop / 完了で再開
                await event.wait()
                continue
            # transient(CAS 連敗・DB 例外 等)— backoff 付きで再試行
            try:
                await asyncio.wait_for(event.wait(), timeout=error_backoff)
            except asyncio.TimeoutError:
                pass
            error_backoff = min(error_backoff * 2, TURN_RETRY_MAX_BACKOFF_S)
    finally:
        _delivery_tasks.pop(chat_id, None)
        _delivery_events.pop(chat_id, None)


async def _deliver_cycle(chat_id: str) -> str:
    """配達 1 サイクル(契約 M3 手順 1〜7)。戻り値は outcome:

    - "empty": 未配達なし(driver は終了してよい)
    - "abandoned": 配達先消滅(恒久断念 — delivered_at は立てた)
    - "busy": ターン走行中(Event 待ちへ)
    - "chain_limited": 連鎖上限で保留(Event 待ちへ — 人間発話で解除)
    - "retry": transient(backoff 再試行)
    - "delivered": notice 追記完了(ターン起動の成否は問わない — notice は届いた)
    """
    # 0. 未配達の有無を先に見る(wake ④ は全 chat ターン終了で来る — 何も無い
    #    chat のために MB 級になりうる chat JSON を読まない)
    if not await asyncio.to_thread(
        Executions.get_undelivered_terminal_by_chat_id, chat_id
    ):
        # 前サイクルで media を控えた行が await_task / stop_task 側で配達済みに
        # なっていることがある(敵対検証 7)— 控えを回収してから終了
        await _sweep_consumed_media()
        return "empty"

    # 1. 配達先の確認: 不在 / 削除済み / agent chat は恒久断念
    chat = await asyncio.to_thread(Chats.get_chat_by_id, chat_id)
    user = (
        await asyncio.to_thread(Users.get_user_by_id, chat.user_id)
        if chat is not None
        else None
    )
    if (
        chat is None
        or chat.deleted_at is not None
        or chat.kind != "user"
        or user is None
    ):
        rows = await asyncio.to_thread(
            Executions.get_undelivered_terminal_by_chat_id, chat_id
        )
        if rows:
            await asyncio.to_thread(Executions.mark_delivered, [row.id for row in rows])
            log.info(
                f"delivery abandoned permanently (chat_id={chat_id}, "
                f"rows={len(rows)}): no deliverable chat"
            )
        # 前サイクルで控えた media は恒久断念(delivered 化)で用済み(敵対検証 6)
        await _sweep_consumed_media()
        return "abandoned"

    semaphore = _get_semaphore()
    await semaphore.acquire()
    semaphore_held = True
    claimed: list[str] = []
    try:
        # 2. 静穏確認(semaphore 取得後に確認 — 取得待ちの間に始まったターンを拾う)
        if tasks.list_task_ids_by_chat_id(chat_id):
            return "busy"

        for attempt in range(APPEND_CAS_MAX_ATTEMPTS):
            chat_json = chat.chat or {}

            # 3. 連鎖上限(無人連続ターンの抑止 — 人間発話で自然解除)
            notice_chain = count_notice_chain(chat_json)
            if notice_chain >= ASYNC_EXEC_RESUME_CHAIN_LIMIT:
                log.info(
                    f"delivery suspended by chain limit (chat_id={chat_id}, "
                    f"chain={notice_chain})"
                )
                return "chain_limited"

            # 4. 未配達 terminal 行 + プロセス内クレーム(同期 — 防御的)
            rows = await asyncio.to_thread(
                Executions.get_undelivered_terminal_by_chat_id, chat_id
            )
            rows = [row for row in rows if row.id not in _claimed_exec_ids]
            if not rows:
                return "empty"
            for row in rows:
                _claimed_exec_ids.add(row.id)
                claimed.append(row.id)

            # media(context_images / display)の消費: mark_replayed の勝者のみ
            # (負け = await_task が注入済み → 結果のみ中継)。CAS 追記の再試行で
            # 失わないよう _consumed_media に控える
            for row in rows:
                meta = row.meta or {}
                context_images = meta.get("context_images") or []
                displays = meta.get("display") or []
                if row.id not in _consumed_media and (context_images or displays):
                    if await asyncio.to_thread(Executions.mark_replayed, row.id):
                        _consumed_media[row.id] = (context_images, displays)

            # 5. notice + 空 assistant を CAS 追記(同一 txn で delivered_at 確定)
            notice_text, notice_files = build_delivery_notice(
                rows,
                {
                    row.id: _consumed_media[row.id]
                    for row in rows
                    if row.id in _consumed_media
                },
            )
            model_id = _resolve_turn_model_id(chat_json)
            model_name = model_id or ""
            if _app is not None and model_id:
                entry = (getattr(_app.state, "MODELS", None) or {}).get(model_id)
                if entry:
                    model_name = entry.get("name", model_id)
            notice_message = {
                "role": "user",
                "content": notice_text,
                "meta": {"system_notice": SYSTEM_NOTICE_KIND},
                **({"files": notice_files} if notice_files else {}),
            }
            assistant_message = {
                "role": "assistant",
                "content": "",
                **({"model": model_id} if model_id else {}),
                **({"modelName": model_name} if model_id else {}),
            }

            # append〜初回ターン起動の窓: chat_completion 入口の
            # wait_for_quiet_delivery(FE ターンの短い待ち)がこの印を見る
            _delivering.add(chat_id)
            try:
                status, updated = await asyncio.to_thread(
                    Chats.append_delivery_turn,
                    chat_id,
                    notice_message,
                    assistant_message,
                    [row.id for row in rows],
                )
                if status == "missing":
                    await asyncio.to_thread(
                        Executions.mark_delivered, [row.id for row in rows]
                    )
                    return "abandoned"
                if status == "conflict":
                    # CAS 負け: 再読して手順 2 から(クレームは解いて読み直す)
                    for exec_id in claimed:
                        _claimed_exec_ids.discard(exec_id)
                    claimed.clear()
                    if tasks.list_task_ids_by_chat_id(chat_id):
                        return "busy"
                    chat = await asyncio.to_thread(Chats.get_chat_by_id, chat_id)
                    if chat is None or chat.deleted_at is not None:
                        return "abandoned"
                    continue

                # 配達確定 — media の控えは用済み
                for row in rows:
                    _consumed_media.pop(row.id, None)

                # 6. 追記後の再静穏確認: FE ターンが出現していたら続きターンの
                #    起動を放棄(notice は履歴に残る = そのターン以降のモデル
                #    呼び出しに自然に載る。配達は完了扱い)
                if tasks.list_task_ids_by_chat_id(chat_id):
                    return "delivered"

                # 7. 続きターンの起動。semaphore は配達サイクルの絞り —
                #    起動(と backoff 再試行)は長時間になりうるので先に手放す
                semaphore.release()
                semaphore_held = False
                assistant_id = (
                    (updated.chat.get("history") or {}).get("currentId")
                    if updated
                    else None
                )
                await _start_resume_turn(chat_id, assistant_id, user, notice_chain + 1)
                return "delivered"
            finally:
                if semaphore_held:
                    # 初回ターン起動まで進まなかった経路(conflict / missing / 例外)
                    _delivering.discard(chat_id)

        # CAS 連敗(FE 保存の連打等)— transient に落とす
        return "retry"
    finally:
        for exec_id in claimed:
            _claimed_exec_ids.discard(exec_id)
        _delivering.discard(chat_id)
        if semaphore_held:
            semaphore.release()
        # delivered 済み行の media 控えを回収(敵対検証 6/7 — "missing" 断念・
        # conflict 後 abandoned・await_task が先に配達した "empty" 等の終端)
        await _sweep_consumed_media()


class _PermanentTurnFailure(Exception):
    """続きターンの恒久失敗(モデル id 解決不能 等)— 再試行しない。"""


async def _start_resume_turn(
    chat_id: str,
    assistant_id: Optional[str],
    user,
    chain_count: int,
) -> None:
    """続きターンを内部起動する(契約 M5 / M3 手順 7)。

    transient 失敗は backoff(上限 5 分・無限回)でターンのみ再試行 —
    notice は再 append しない。ただし再試行前に履歴が自分の空 assistant より
    進んでいたら中止(誰かのターンが結果を消化済み)。恒久失敗(モデル id
    解決不能 等)は log + 断念(notice は履歴に残っているので次のユーザー発話で
    モデルが読む)。_delivering の窓(FE ターンの短い待ち)は初回試行までで
    閉じる — backoff 中まで FE を待たせない。
    """
    if not assistant_id:
        log.error(f"resume turn skipped: no assistant message (chat_id={chat_id})")
        return

    backoff = TURN_RETRY_INITIAL_BACKOFF_S
    first_attempt = True
    while True:
        # 履歴が進んだ / 誰かのターンが走っている → 中止(notice は消化される)
        if tasks.list_task_ids_by_chat_id(chat_id):
            return
        chat = await asyncio.to_thread(Chats.get_chat_by_id, chat_id)
        if (
            chat is None
            or chat.deleted_at is not None
            or ((chat.chat.get("history") or {}).get("currentId")) != assistant_id
        ):
            return

        try:
            await _try_start_turn(chat, assistant_id, user, chain_count)
            return
        except asyncio.CancelledError:
            raise
        except _PermanentTurnFailure as e:
            log.error(
                f"resume turn failed permanently (chat_id={chat_id}): {e} — "
                "notice remains in history; the next user turn will read it"
            )
            return
        except Exception:
            log.exception(
                f"resume turn start failed (chat_id={chat_id}); "
                f"retrying in {backoff:.0f}s"
            )
        finally:
            if first_attempt:
                _delivering.discard(chat_id)
                first_attempt = False

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, TURN_RETRY_MAX_BACKOFF_S)


async def _try_start_turn(chat, assistant_id: str, user, chain_count: int) -> None:
    """1 回のターン起動の試み(分類は呼び出し側 — 恒久は _PermanentTurnFailure)。"""
    # 循環 import 回避: subagent → middleware → (executions ↔ 本モジュールの hook)
    from nucllei.utils import subagent

    await _ensure_models_loaded(user)

    chat_json = chat.chat or {}
    model_id = _resolve_turn_model_id(chat_json)
    if not model_id:
        raise _PermanentTurnFailure("no model id in chat history or chat.models")
    model = (getattr(_app.state, "MODELS", None) or {}).get(model_id)
    if model is None:
        raise _PermanentTurnFailure(f"model '{model_id}' is not resolvable")

    wire_messages = build_wire_messages(chat_json, user, assistant_id, model)
    params = _merged_params(chat_json, user)
    chat_files = chat_json.get("files") or []

    form_extra = {
        **({"params": params} if params else {}),
        **({"files": chat_files} if chat_files else {}),
    }

    # metadata task_resume: True の効果(契約 M5)—
    # background_tasks は None(start_internal_turn が tasks=None で呼ぶ)、
    # webhook は連鎖 1 通目のみ(task_resume_chain を middleware の述語が読む)
    await subagent.start_internal_turn(
        _shim_request(),
        model,
        user,
        chat.id,
        assistant_id,
        wire_messages,
        metadata_extra={"task_resume": True, "task_resume_chain": chain_count},
        form_extra=form_extra,
    )
    # 起動後は待たない(契約 M5)— ターンの終了は wake ④ が拾う
