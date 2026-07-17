# チャットストリーミング wire v2(socket.io 撤去・SSE 一本化)

2026-07-17 出荷(`2894654da`、refs #70 #16)。チャット送受信経路の真実源。
実装: BE = `nucllei/utils/completion_events.py` / `middleware.py`、
FE = `src/lib/utils/chat-events.ts` / `completion-streams.ts`。

## アーキテクチャ

- **POST /api/chat/completions(chat_id/id あり)の応答そのものが SSE**。
  socket.io は存在しない(python-socketio / engineio / socket.io-client とも撤去済み)。
- 生成は**バックグラウンドタスクが完走**する(クライアント切断 = 購読解除であり cancel ではない)。
  POST の SSE はタスクのイベントバス(`CompletionEventStream`)の一購読者にすぎない。
- **再アタッチ**: `GET /api/tasks/{task_id}/events`(最新スナップショット先行 + 以降ライブ)。
  チャットを開くとき `GET /api/tasks/chat/{chat_id}` で走行タスクを照会して繋ぐ。
  ストリーム中のリロード・別タブからの追随はこれで収束する(バックログ再生は無い)。
- **stop**: `POST /api/tasks/stop/{task_id}`(所有者検証あり)。cancel 時のイベント順序は
  task-cancelled → done+blocks(FE の interrupted 判定が依存 — 変えるな)。
- **__event_call__**(tool → UI の confirmation/input)は封筒に call_id が付き、
  応答は `POST /api/tasks/{task_id}/calls/{call_id}`。'execute' イベント(任意 JS 実行)は
  絶対制約(tool 外のコード実行を作らない)により撤去済み — 復活させない。
- **/api/chat/completed・/api/chat/actions**(outlet filter / action)は live バスが無いため
  emitter をバッファ化し、**応答 JSON の events 配列**で返す。この経路の __event_call__ は
  no-op(使えない)。
- headless(chat メタデータ無しの API クライアント)は従来どおり OpenAI 互換フレーム素通し。
- FE の消費者所有権: ストリーム読取は `completion-streams.ts`(モジュールレベル)が持ち、
  Chat コンポーネントは自 chat_id のハンドラを登録/解除するだけ。非表示チャットの
  イベントは +layout のフォールバック(done トースト・chat:title 反映)が受ける。
- **改訂(2026-07-17、#70 C1)**: Chat.svelte は `src/routes/(app)/chat/Chat.svelte` へ
  コロケート移動 + runes 化。純関数層を拡張(いずれも vitest で契約固定):
  - `chat-events.ts applyMessageMutation` — status / task-cancelled / delta / files /
    source(code_execution upsert)など「message の状態だけを変える」イベントの適用
  - `chat-messages.ts buildWireMessages` — system 注入・画像 image_url 展開・tool 往復復元・
    空 content フィルタの wire 組み立て
  - `chat-messages.ts parseStopSequences` — stop 正規化(upstream の settings 側 stop で
    TypeError になる潜在バグも解消)

## イベント規約(SSE data = 封筒 {chat_id, message_id, call_id?, data:{type, data}})

- 最初のイベント: `{type:'task', data:{task_id}}`(stop・再アタッチ用)
- `chat:completion` の data:
  - `{delta:{block:'text'|'reasoning', content}}` — 末尾ブロックへの純追記(高頻度)
  - `{content, content_blocks}` — 全文スナップショット。**emit されるのは
    ①各モデルストリーム開始時(無条件)②構造変化(ブロック開閉・tool_calls)
    ③テキスト連続中の約 2 秒間引き(`SNAPSHOT_INTERVAL`)のみ**
  - `{done:true, content, content_blocks, title}` / `{error:{content}}`(唯一のエラー形)/
    `{usage}` / `{sources}`
- **done ≠ ストリーム終端**。done の後に chat:title 等が続く。終端はタスク終了によるサーバー側
  close = **EOF**(FE は EOF まで読む)。
- 生のモデルフレーム(choices)はクライアントへ流れない(assembler 経由イベントのみ)。
- 旧方式(テキストデルタごとに全文スナップショット)は **O(n²) ワイヤ帯域**だった — 戻さない。

## 永続化(ENABLE_REALTIME_CHAT_SAVE は廃止)

DB upsert は「構造変化時 + 2 秒スロットル + done/cancel/error 時」で固定。
全 DB 書き込みは asyncio.to_thread 経由。リロード復元 = DB 状態 + 再アタッチで収束。

## 非 stream(stream=False)

非 stream JSON 応答も**合成 1 フレーム**として assembler に畳み込み、同一のタスク + SSE 契約で
返す(`ingest_model_response_json`)。tool ループの追撃呼び出しは元リクエストの stream 指定を
尊重する — 非 stream しかできないモデルでも tool が動く。

## テスト

- BE: `test_native_tool_calling.py`(実イベントバス購読 + SSE EOF 完走待ちで駆動)、
  `utils/test_completion_events.py` / `test_execute_tool_calls.py` / `test_response_assembler.py`
- FE: `src/lib/utils/chat-events.test.ts`(適用の純関数)
- 実機 e2e: `dev/e2e/check_streaming_wire_v2.py`(実 SGLang でストリーミング単調成長・
  ストリーム中リロード再アタッチ・完走を検証)

## 既知の制約・将来

- マルチタブの「同一チャットをライブ視聴」は再アタッチで可能だが、編集の他タブ即時同期
  (旧 socket の chat:message 放送)は廃止(リロードで収束)。
- webhook の「見ていなかったら通知」は完了時点のタスク購読者有無で判定(プレゼンス基盤は無い)。
- モデル自身の multimodal 画像出力(assistant メッセージ内画像)は content_blocks に
  `image` ブロック型を足して受ける設計余地を確保済み — 実プロバイダのフレームを
  キャプチャしてからテストファーストで実装する(推測実装しない)。
- リバースプロキシは SSE の応答バッファリング禁止が前提(応答ヘッダ `X-Accel-Buffering: no`
  は付与済み。nginx 側でも尊重されること)。
