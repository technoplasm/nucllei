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
| `__progress__(description, *, current, total, done)` | 人間 | **実行中に即時** | **✗(揮発)** | ○ | ✗ |
| `__display__(content, kind)` | 人間 | 実行後 harvest | ○(results[i].display) | ○ | ✗ |
| `__feed_image__(file_id)` | モデル(VLM) | 実行後 harvest | ○(results[i].context_images) | ○ | ○(画素) |
| `__event_emitter__({type,data})` | 人間 | 即時 | 型による | ✗(async) | 型による |
| `__event_call__({type,data})` | 人間(モーダル) | 即時・双方向 | ✗ | ✗(async) | ✗ |

`__event_emitter__` は tool 向けだけ `make_tool_event_emitter` で包み、**`status` を落とす。
それ以外の型は素通し**(allowlist ではない)。tool が実際に使うのは
`chat:message:files`(sandbox の成果物添付)/ `message` / `replace` / `source`(`citation`)/
`notification` / `chat:title`。素通しなので `chat:completion` を撃って本文を差し替えることも
できるが、tool は元々任意の Python なのでここは**権限境界ではない**(認可は tool の
access_control 側)。status だけを塞ぐのは、共用スロットの奪い合いを止めるため。

**`__progress__` は tool 実行文脈でのみ効く。** pipe(`functions.py`)や filter 経路にも
注入されるが、そちらは sink を張らないので恒久 no-op(呼んでも例外にならないだけ)。

## 選び方

- **実行中の「今どこか」** → `__progress__`。他に手段は無い
- **後から読み返してほしい実行ログ・プレイヤー・図** → `__display__`(collapse 外・永続・リロード再現)
- **モデルに画像を見せたい** → `__feed_image__`
- **モデルに伝えたい作業データ** → 戻り値。表示種別は型注釈で決める(`tool-arg-render.md`)
- **成果物をチャット添付にしたい** → `__event_emitter__({"type":"chat:message:files"})`
  (sandbox の outputs/ 回収がこれ。DB へ upsert してから emit する)
- **ユーザーに聞き返したい** → `__event_call__`(`confirmation` / `input` の 2 種のみ)

## `__progress__`(#100)

行き先は **その tool 自身のウィジェット(ToolCallBlock のヘッダ)**。メッセージ単位の
共用スロットではないので、同時に走る他の tool や filter の表示を踏み潰さない。
これが status からの移行理由そのもの — 旧実装は共用の 1 行(`.at(-1)`)を last-wins で
奪い合い、トークン計(filter)の常設表示を tool の進捗が潰していた。

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

## filter は別系統

filter(function)の `__event_emitter__` は無変更で、**`status` を使える**。行き先は
メッセージ本文の上の 1 行(`ResponseMessage.svelte`)で、トークン計のような
メッセージ単位の常設表示はここ。tool が抜けたことで奪い合いは起きない。

残件は #92(`statusHistory` を FE / BE が独立に append しており、FE のチャット保存が
DB 行を上書きして BE 分が蒸発する)。filter だけになれば量は減るので優先度は低い。

## 関連

- `_memos/tool-arg-render.md` — 引数・戻り値の型駆動レンダリング(#20)と #88/#89 の経緯
- `_memos/builtin-tools.md` — 組み込み tool の機構と sandbox 移植の受け入れ基準
- `_memos/rendering-design.md` §3 — content_blocks の確定スキーマ
- `README_API.md` §5.3 / §5.5 — 外部 tool 作者向けの一次記述
