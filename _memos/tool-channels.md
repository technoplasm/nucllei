# tool 出力チャネル一覧(真実源)

tool が「戻り値以外」で何かを届ける口の全量。issue #88 / #89 / #100 で足したものが
`builtin-tools.md` / `tool-arg-render.md` / `rendering-design.md` に散っていて、
**「実行中に押せる口はどれか」「sync から押せるか」が読み取れなかった**ので 1 枚に集約する。

実装: `nucllei/utils/tool_output.py`(sink 基盤)/ `nucllei/utils/middleware.py`
(注入と harvest)/ `nucllei/utils/completion_events.py`(イベントバス)。

## 一覧

| 口 | 誰の目 | いつ届く | 永続 | sync から | モデルに入る |
|---|---|---|---|---|---|
| 戻り値(`return`) | モデル | tool 終了時 | ○(results[i].content) | ○ | ○ |
| 失敗(`raise ToolError` — #145) | モデル + 人間(種別) | tool 終了時 | ○(results[i].content + results[i].error_kind) | ○ | ○(文面) |
| `__progress__(description, *, current, total, done)` | 人間 | **実行中に即時** | **✗(揮発)** | ○ | ✗ |
| `__display__(content, kind)` | 人間 | 実行後 harvest | ○(results[i].display) | ○ | ✗ |
| `__feed_image__(参照文字列: file_id / URL(`http(s)://…`・同一サーバの `/ext/…`)/ sandbox のパス(`/workspace/…` `/mnt/…`)/ `/api/v1/files/{id}/content#page=N`。振り分けは `image_refs.stored_ref_kind`)` | モデル(VLM) | 実行後 harvest | ○(results[i].context_images) | ○ | ○(画素) |
| `__event_emitter__({type,data})` | 人間 | 即時 | 型による | ✗(async) | 型による |
| `__event_call__({type,data})` | 人間(モーダル) | 即時・双方向 | ✗ | ✗(async) | ✗ |

`__event_emitter__` は tool・filter とも**素通し**(allowlist ではない)。tool が実際に使うのは
`chat:message:files`(sandbox の成果物添付)/ `message` / `replace` / `notification` /
`chat:title`。`source` / `citation`(メッセージ下の出典一覧)は 2026-10-05 に引用表示ごと
撤去した — BE は素通しのまま、FE が未知の型として捨てる(`_memos/feature-triage.md`)。素通しなので `chat:completion` を撃って本文を差し替えることも
できるが、tool は元々任意の Python なのでここは**権限境界ではない**(認可は tool の
access_control 側)。

**`status` は 2026-08-18 に機構ごと全廃した(#92)。** 受け口(DB 書き込み・FE 表示・
`make_tool_event_emitter` の選別)をすべて撤去済みなので、撃っても誰も拾わない。
経緯は下の「status は廃止した」節。

**`__progress__` は tool 実行文脈でのみ効く。** pipe(`functions.py`)や filter 経路にも
注入されるが、そちらは sink を張らないので恒久 no-op(呼んでも例外にならないだけ)。

## 選び方

- **実行中の「今どこか」** → `__progress__`。他に手段は無い
- **後から読み返してほしい実行ログ・プレイヤー・図** → `__display__`(collapse 外・永続・リロード再現)
- **モデルに画像を見せたい** → `__feed_image__`。引数は file_id か `/` 始まりの参照
  (`/ext/…` `/mnt/…` `/workspace/…`、PDF は `#page=N`)。参照は送信直前に
  `utils/image_refs.py` が実体化する(File は作らない、#139)
- **モデルに伝えたい作業データ** → 戻り値。表示種別は型注釈で決める(`tool-arg-render.md`)
- **成果物をチャット添付にしたい** → `__event_emitter__({"type":"chat:message:files"})`
  (sandbox の outputs/ 回収がこれ。DB へ upsert してから emit する)
- **ユーザーに聞き返したい** → `__event_call__`(`confirmation` / `input` の 2 種のみ)

## `__progress__`(#100)

行き先は **その tool 自身のウィジェット(ToolCallBlock のヘッダ)**。メッセージ単位の
共用スロットではないので、同時に走る他の tool や filter の表示を踏み潰さない。
これが status からの移行理由そのもの — 旧実装は共用の 1 行(`.at(-1)`)を last-wins で
奪い合い、トークン計(filter)の常設表示を tool の進捗が潰していた(status 自体はその後
全廃 — 下記)。

### 規律

- **揮発**: DB に書かない。タスク生存中は `CompletionEventStream.latest_progress` が
  tool_call_id ごとの最新 1 件を保持し、再アタッチ(`GET /api/tasks/{id}/events`)の
  初回配信にスナップショットの**後**で混ぜる。結果が出た tool の分はスナップショット
  到着時に捨てる
- **完了の権威は results の到着**であって `done=True` ではない。done を撃たずに return
  しても、timeout / 例外で tool のコードを通らなくても、フロントは結果の到着で進捗を
  捨てる(`ToolCallBlock` の `isDone` が権威)
- **正規化は backend**: `total<=0` / `current` 不在は不定に落とし、`current` は
  `[0,total]` にクランプ。フロントは「total があるか」しか見ない
- **スロットル** `PROGRESS_THROTTLE_S`(既定 0.2 秒)。抑制した最後の 1 通は
  `sink.close()` で必ず流す(leading-edge だけだと古い値が画面に残る)
- **description 省略で直前の文言を維持**(tqdm の `set_description` 相当)
- **開始マーカ**: `execute_tool_calls` は各 call の実行直前に `sink.mark_started()` で
  空 description の進捗を 1 発打つ。ブロックは実行前の 1 回の emit で**一斉に**描かれる
  ので、これがフロントの経過時間タイマの起点になる(逐次実行なので、これが無いと
  2 本目以降が前の tool の待ち時間まで数えて確定時に巻き戻る)。マーカは
  スロットルの窓を動かさない — 直後に tool が押す最初の description を巻き添えで
  落とさないため
- **描画は素のテキスト**。ヘッダ本体は Markdown 経由だが、進捗を md として通すと
  tool が任意の markdown をヘッダへ注入できる

### 実装上の要点(ここを踏むと壊れる)

- **`run_coroutine_threadsafe` は使えない。** async tool は `utils/tools.py` の
  `iscoroutinefunction` 分岐で to_thread を通らず**イベントループ上で直接 await される**
  ため、そこから future を待つとループが自分自身を待って固まる。ループスレッド上なら
  `stream.emit` を直呼び、ワーカースレッドからは `loop.call_soon_threadsafe` に渡す
  (`_make_progress_emitter`)
- **`__event_emitter__` を経由しない。** あちらは async で status/message に DB 書き
  (to_thread)を含み、await 境界で順序が入れ替わるうえ共有 executor を圧迫する
- **stream はレイジー逆引きしない。** `get_stream_for_message` で引くと regenerate 時に
  次のターンの stream へ幽霊進捗を注入しうる。sink 生成時にオブジェクトごと捕まえる
- **ループ参照・stream・スロットル状態・closed は sink の属性**に持つ。ContextVar で
  運ぶのは sink 1 本だけ(将来 `execute_tool_calls` を並列化しても壊れない)
- **timeout orphan**: sync tool は `wait_for` の cancel 後もスレッドが走り切る
  (`middleware.py` の有界リーク)。`current_sink.reset()` はメインループ側 context の
  話で orphan には効かないので、`finally` の `sink.close()` が closed フラグで止める

## 経過時間と所要時間(#100)

**実行中の経過秒は tool が押さなくても出る。** フロント側の共有クロック
(`src/lib/utils/tick.svelte.ts` — 購読者がいる間だけ 1 秒タイマを 1 本共有)が、
思考中(ReasoningBlock)・tool 実行中(ToolCallBlock)・追撃待ち(PendingIndicator)の
3 箇所で刻む。tool が `__progress__` で押すのは**中身のある進捗**(何件目・何を処理中)
だけでよい。

進捗が消えた後に残るのが所要時間。`results[i].duration`(秒・小数)と reasoning ブロックの
`duration`。

- **成否に関わらず測る** — 落ちた tool が何秒走ったかこそ知りたい。
  `context_images` / `display` の `tool_succeeded` ゲートとは規律が異なる
- 計測は `time.monotonic()`
- **モデルには漏れない**: `convert_content_blocks_to_messages` は `tool_call_id` と
  `content` しか拾わない
- 丸めは表示側(`src/lib/utils/duration.ts`)。backend は小数のまま持つ
  (`int()` 切り捨てだと 1 秒未満の思考が全部「0秒間の思考」になっていた)

## status は廃止した(2026-08-18、#92)

**メッセージ本文の上の共用 1 行(status / `statusHistory`)は機構ごと撤去した。**
filter・tool のどちらからも触れない。

- 撤去範囲: `completion_events.py` の status 分岐と `make_tool_event_emitter` /
  `Chats.add_message_status_to_chat_by_id_and_message_id` / FE の
  `applyMessageMutation('status')`・`MessageStatus` 型・`ResponseMessage.svelte` の
  表示ブロック(`knowledge_search` 特例と i18n 3 キー含む)/ `.status-description` CSS /
  dev fixture `dev/functions/token_meter_lite.py`
- **なぜ**: 本 fork は OWUI の拡張点として status を使う立場をやめた。必要な表示は
  本体にネイティブで入れられる — tool の進捗は `__progress__`(tool 自身のウィジェット)、
  トークン計は `live-usage` store + `MessageActions.svelte`、非同期タスクは
  入力欄下部の進行中チップ。**共用 1 行を last-wins で奪い合う設計そのものが不要**だった
- 実態としても撤去前から死んでいた: llens 側の filter は 2026-08-09 に status を全廃済み
  (`large_file_gate.py` / `token_meter.py` のコメント)、tool は #100 で遮断済み、
  本体 core は元々 emit していなかった
- これで #92 の原因(`statusHistory` を FE / BE が独立に append し、FE のチャット保存が
  DB 行を上書きして BE 分が蒸発する)も消滅した
- **旧データ互換は持たない**(CLAUDE.md)。既存 chat JSON に残る `statusHistory` は
  読まれず、表示もされない

## 関連

- `_memos/tool-arg-render.md` — 引数・戻り値の型駆動レンダリング(#20)と #88/#89 の経緯
- `_memos/builtin-tools.md` — 組み込み tool の機構と sandbox 移植の受け入れ基準
- `_memos/rendering-design.md` §3 — content_blocks の確定スキーマ
- `README_API.md` §5.3 / §5.5 — 外部 tool 作者向けの一次記述
