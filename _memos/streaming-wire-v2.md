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
  - `chat-events.ts applyMessageMutation` — task-cancelled / delta / files /
    など「message の状態だけを変える」イベントの適用
    (status は 2026-08-18、source / citation は 2026-10-05 に機構ごと撤去 — `tool-channels.md`)
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
    `{usage}`(context_size 同梱 — #102)/ `{sources}`
  - **不変条件: 1 モデル呼び出し(1 ストリーム)の tool calls は tool_calls ブロック 1 個**
    (2026-10-03 修正)。assembler は「calls のブロックが既にあるか」で判定する — 以前は
    「末尾が calls のブロックか」しか見ておらず、tool call デルタの間に content デルタを挟む
    モデル(並列 call の区切りに改行を出す。2026-10-02 に qwen3.8-27b で実測)では間に text
    ブロックが開くたびに同じ calls のブロックを積み増していた。N call で N 個が永続化され、
    results が綴じられるのは末尾だけ → 同ターンの追撃と次ターンの履歴に、同じ call id の
    assistant(tool_calls)が N 回(N-1 回は `[interrupted…]` の合成 result 付き)再送された。
    calls の後に本文を出して終わるストリームも同型だった(実行直前の ensure が 2 個目を積む)。
    今は ensure がブロックを末尾へ移す(本文 → tool_calls → 結果の順 = 「content + tool_calls
    の 1 assistant メッセージ」の形)。回帰は `test_response_assembler.py` と
    `test_native_tool_calling.py::test_content_between_parallel_calls_does_not_duplicate_tool_calls_block`。
- `chat:usage`(#102、揮発): ストリーミング中の累積 usage。SGLang の
  `stream_options.continuous_usage_stats`(capabilities.continuous_usage=False で opt-out、
  usage=False なら連動 OFF)でチャンクに載る累積値を、`USAGE_EMIT_INTERVAL`(0.5 秒)の
  leading-edge throttle で流す。data =
  `{prompt_tokens, completion_tokens, total_tokens, reasoning_tokens?, context_size?}`
  (reasoning_tokens はチャンク usage にあるときのみ、context_size は解決できたときのみ)。
  **永続化は最終 usage フレーム(choices: [])→ chat:completion {usage} のみ** — chat:usage は
  DB に書かず、tail flush も無い(最終真値は chat:completion {usage} が無条件で届く)。
  再アタッチでは最新 1 件をスナップショットの後(task → snapshot → 揮発イベントの順)に配り、
  done=True の chat:completion で破棄する。
- **floating ゲージ(UsageChip)の読み方と既知の落差(2026-10-02 実測)**: ゲージは
  「直近のモデル呼び出しに実際に入った量」= 生成中は `chat:usage` の total、確定後は
  マージ済み usage の最新 prompt。tool ループ中の値に合算・二重計上は無い(SGLang 直結 /
  LiteLLM 経由の両方で、SSE・DB・次ターン先頭の値が一致することを確認)。
  - **確定値は「次ターンへ持ち越す量」`carried_tokens` に変更(2026-10-03、ken「出力終わった
    段階で測り直すようにしたら?」)**。最後の呼び出しの prompt を出すと、そのターンの出力分
    (text + tool_calls は次ターンで再送される)だけ次ターンの先頭でゲージが跳ねていた。
    backend が `_record_usage` で `carried = prompt_tokens + clamp(completion_tokens −
    reasoning_tokens, 0, completion)` を算出して `message.usage.carried_tokens` に載せる
    (`middleware.carried_tokens_from_usage`。DB merge は prompt 同様「最新で上書き」—
    tool ループでは最後の呼び出しだけ。以前の呼び出しの出力は最新 prompt に含まれているので
    ターン累積の completion は使わない)。reasoning_tokens は top-level(SGLang 拡張 —
    dev の prism-gw 越し kimi-k2.6 も同じ)/ `completion_tokens_details.reasoning_tokens`
    (LiteLLM)の報告を使い、無ければ**推定**: その呼び出しの出力ブロック
    (`assembler.stream_blocks()` — start_stream 以降の分だけ)の文字数比
    `completion × len(text + tool_calls) / len(全出力)`。出力ブロックが無ければ completion
    丸ごと再送扱い。FE は `contextUsageFromUsage` が `carried_tokens ?? prompt_tokens ??
    total_tokens` を読み、UsageChip の確定値も同関数に揃えた(MessageActions と同じ数字)。
    生成中(`chat:usage` の total)は従来どおり。回帰: `test_streaming_usage.py::TestCarriedTokens`、
    `test_chat_usage.py::test_carried_tokens_*`、`test_response_assembler.py::TestStreamBlocks`。
    **追補(2026-10-04、UI で再現して確認)**: ken の「2 ターン目で半分に減る」は、1 ターン目の
    done で live(reasoning 込み)→ carried に切り替わる落差だった(数字は正しい: carried 5692 →
    次ターン prompt 5714)。表示の定義は据え置き(生成中は reasoning 込み)。直したのは (1) 生成中の
    IN / OUT が前ターンの確定値だった → live から出す(`chipUsage`)、(2) 新規チャットで最初の usage
    まで空 → 送信時に `markLiveUsagePending`(send.ts)で「…」。dev で「一瞬消える」ように見えたのは
    他セッションのファイル編集による vite の全体リロード(アプリの挙動ではない)。
    **追補 2(2026-10-04、staging で「まだ 2 ターン目の送信直後に大きく落ちる」)**: ゲージの
    計算ではなく**送信内容のバグ**だった。tool ループの追撃のたびに preset system prompt が
    1 枚ずつ前置され積み重なっていた(staging 実測、llens-thinking: system 16600 → 33201 →
    49802 → 66403 → 83004 文字、prompt_tokens qwen 18.7k → 66.6k / luna 22.4k → 72.9k。
    ターン 2 は 1 枚に戻るので carried 66.5k → 次ターン prompt 35.8k、luna 72.8k → 45.0k)。
    原因: `misc.add_or_update_system_message` が共有 dict を in-place で書き換えていた —
    router は `payload = {**form_data}`(浅いコピー)に掛け、初回の `insert` で
    form_data["messages"] に system dict が入り、追撃 `[*form_data["messages"], ...]` は
    その dict を共有したまま router に渡るので、毎回同じ dict に前置し直していた。
    直し: misc の messages 加工 4 関数(system / user / assistant / first-user prepend)と
    `openai_o1_o3_handler` の role 書き換えを**非破壊**(新しい list・書き換える message だけ
    新しい dict)にした。ゲージ側(carried / chipUsage)は変更なし。dev 実測(qwen、system
    6.4k 字の一時 preset + fetch_url 2 回): 修正前 prompt 5243 → 9674 → 14099、carried 14273
    → 次ターン 5779(落差 −8.5k)/ 修正後 5243 → 5418 → 5587、carried 5750 → 次ターン 5768。
    回帰: `test_native_tool_calling.py::test_system_prompt_is_not_duplicated_across_tool_loop_calls`
    (実物の routers.openai を通し、上流へ出た全呼び出しの system が 1 枚・同一)、
    `utils/test_message_helpers.py`。教訓: **request 経路の messages 加工は非破壊で書く**
    (form_data は浅いコピーで回り、追撃・subagent・resume と dict を共有する)。
    **追補 3(2026-10-05、ken「だいぶゆるやかになった」が staging でまだ 2 ターン目の先頭で落ちる。
    「算出のためにリクエスト追加で流してもいい」)**: carried を**推定から実測へ**。staging(LiteLLM
    経由)は reasoning_tokens を報告せず文字数比の推定に落ちる — 英語の reasoning と日本語の本文で
    token あたりの文字数が違うぶん外れる。ほかにも引き算では拾えない差(送信形の違い全般)が残る。
    直し: ターン末(tool ループを抜けた後、done の**前**)に `middleware.measure_carried_tokens` が
    次ターン最初の呼び出しと同じ形 — `form_data["messages"]`(このターンに送った加工済み会話)+
    `convert_content_blocks_to_messages(出力)`(reasoning 抜き、FE expandAssistantToolHistory と同形)+
    最小の user `"."` — を、同じ model・同じ router 経路(preset params / system prompt を router が
    1 枚載せる)・ターン末の tools(load_skill で足した tool も次ターンは履歴から再導出される #101
    ので含める)で、非 stream・`max_tokens: 1` で 1 回流し、返った `prompt_tokens` を
    `carried_tokens` に、`carried_source: "measured"` を付けて DB merge + `chat:completion {usage}`
    で emit する(呼び出しごとの記録は従来どおり推定で `carried_source: "estimated"`)。
    done の前にしたのは、done 後だと FE の完了時保存(saveChatHandler)が推定値の message.usage で
    DB を上書きしうるため。上限 `CARRIED_MEASURE_TIMEOUT` = 10 秒、失敗・usage 無しは推定のまま。
    filter(inlet / stream / outlet)は通さない・persist しない。preset の `params.max_tokens` は
    router で body を上書きするので `max_completion_tokens: 1` も送る(router が後で max_tokens へ
    移す)。`"."` の user ターンぶん(数 token)はゲージに乗る — 次ターンの発言で置き換わる量。
    subagent の子ターンも process_chat_response を通るので同じく実測される。
    dev 実測(prism-gw、preset `llens`): tool 1 回のターン 実測 carried 3872 → 次ターン prompt 3876
    (+4)、reasoning 無しで長文 4.2k token のターン 7932 → 7936(+4)。計測 1 回 0.9 秒(長い出力の
    直後は prefill ぶん 2.5 秒)。回帰: `test_native_tool_calling.py` の
    `test_turn_end_measures_carried_tokens_before_done` / `..._measure_failure_keeps_estimate` /
    `test_measure_carried_tokens_sends_next_turn_shape`(実物の routers.openai — system 1 枚・
    reasoning 無し・末尾 user・ターン末 tools・max_tokens 1)/ `..._returns_none_on_failure` /
    `..._times_out`。残る誤差: このターンの user message に files 由来の source 注入
    (`build_source_context`)があると、次ターンの送信形と違いうる(未確認)。
  **tool 結果の画像(`results[].context_images`、`__feed_image__`)は次ターン以降の履歴にも
  再送する**(#142、ken 決定 2026-10-02 — 画像は忘れさせない。画像の多いチャットが高い占有の
  まま続く代償は受け入れる)。FE `expandAssistantToolHistory`(chat-messages.ts)と BE
  `convert_content_blocks_to_messages`(ターン内の追撃 / execution_resume / subagent)は同じ形 —
  tool メッセージ群の直後に `{role:'user', content:[{text:'(直前の tool 結果の画像)'}, image_url…]}`
  を挟む。url は参照文字列のまま(file id は `/api/v1/files/{id}/content`、スキーム付き URL と
  "/" 始まりはそのまま)で、実体化は毎ターン送信直前の `inline_internal_image_urls`
  (解決できなくなった参照 — 削除済み file 等 — は "(image unavailable)" に落ち、チャットは続く)。
  両側の形は共有 fixture `nucllei/test/fixtures/history_wire/context_images.json` で固定
  (vitest `chat-messages.test.ts` / pytest `test_tool_output.py`)。
  修正前は FE だけが `context_images` を読まず、画像を見た次のターンでモデルが画像を忘れ、
  ゲージも落ちていた(実測: 画像 6 枚で prompt 13,190 → 次ターン 1,556、qwen3.8-27b)。
  修正後の実測(画像 3 枚): ターン末 6,611 → 次ターン 6,645 → リロード後の次ターン 6,675。
- **開始画面にゲージを出さない**: store は '' スコープに書けない(テストで固定)。開始画面に
  出ていたのは `$chatId` の持ち越し — `/c/<id>` からロゴ・ブラウザ「戻る」で `/` に来ると
  Chat が再マウントされるのに `$chatId` が前のチャットのままだった。`Chat.svelte` の onMount が
  chatIdProp 無しのとき '' へ戻して initNewChat に入る(2026-10-02)。
  別件(2026-10-03): 開始画面から最初のメッセージを送った直後は URL だけ `replaceState` で
  `/c/<id>` になり、SvelteKit 上のルートは `/` のまま — ロゴ(`href="/"`)は同一ルートへの遷移で
  Chat が再マウントされず、チャットもゲージも出たままだった。ロゴも「新しいチャット」と同じ
  `newChatHandler`(Sidebar.svelte。goto の後に Navbar の new-chat-button = initNewChat を押す)に揃えた。
- **done ≠ ストリーム終端**。done の後に chat:title 等が続く。終端はタスク終了によるサーバー側
  close = **EOF**(FE は EOF まで読む)。
- 生のモデルフレーム(choices)はクライアントへ流れない(assembler 経由イベントのみ)。
- 旧方式(テキストデルタごとに全文スナップショット)は **O(n²) ワイヤ帯域**だった — 戻さない。

## FE の適用は同期一括(2026-08-13、#103 `41b98d06a`)

- **1 封筒ごとに await を挟まない**。旧実装は chatEventHandler が封筒ごとに
  `await tick()`(= Svelte のフル flush + markdown 再レンダリング)を強制する直列処理で、
  BE の配信が速い(SGLang ~200 tok/s)とレンダリング律速で適用が滞留した。症状:
  文字の流れの遅延 / 生成完了後も「準備中」スピナー常駐 / リロードすると全文出現。
- 現契約: `EnvelopeHandler` は**同期で完結するイベント(delta / chat:usage / tool:progress /
  applyMessageMutation 系)では戻り値なし**、非同期の副作用を持つイベント
  (done の completed 経路・chat:title・__event_call__)だけ Promise を返す。
  `consumeCompletionStream` は**戻り値が falsy なら await しない**(`await undefined` でも
  microtask 境界 = flush が入るため)。1 回の read で届いた分が同期一括適用 = flush 1 回に
  まとまり、BE が FE より速いほどチャンクに多くの封筒が乗る(自己均衡)。
- ハンドラに await を足すときは「高頻度イベントの経路か」を必ず確認 — delta 経路に
  await を戻すと本節の症状が再発する。

## chat-wire の `id` はアシスタントメッセージの id(2026-08-05、#98 `e73945a35`)

- `POST /api/chat/completions` の chat-wire モード(`chat_id` + `id`)で、**`id` は
  「これから生成されるアシスタントメッセージ」の id**。生成結果はそのメッセージへ書かれる。
  呼び出し側は chat 作成時に user メッセージと**空のアシスタントメッセージ**
  (`role:"assistant"`, `content:""`, `parentId`=user の id)の両方を入れ、
  `history.currentId` をアシスタント側にして、その id を渡す。
- 検証が無い間は `upsert_message_to_chat_by_id_and_message_id`(`models/chats.py`)が
  **黙って壊した**: user メッセージの id を渡すと生成結果がそこへマージされ(回答が
  ユーザー発話として残る)、未知 id なら生 dict がそのまま挿入されて `id`/`role`/`parentId`
  を欠いたメッセージ片が永続化される。医療系の配備では監査・引用の観点で誤解を招く。
- **サーバーは正しい形を推測して作り直さない** — 呼び出し側の誤りとして `400`
  (壊れた状態を黙って直さない fork の原則)。検証は既存チャットがあるときだけ効かせる:
  `chat_id` が DB に無ければ upsert が no-op なので汚染は起きない。
- **検証は try の外**(所有者検証と同じ位置)。payload 処理の except は `message_id` へ
  エラーを upsert するので、不正 id のまま落ちると**エラー経路自体がメッセージ片を作る**。
- **所有者検証(403)は id 検証より先**。順序を逆にすると 400 の有無で
  「その id が他人のチャットに存在するか」が漏れる。
- UI は影響を受けない: `send.ts` が `responseMessage`(`role:'assistant'`)を
  `history.messages` へ入れ、`saveChatHandler` で **DB 保存してから** completion を POST する。
  (旧 `continueResponse` は 2026-10-05 に撤去 — _memos/feature-triage.md)
- README_API §4.3 に「assistant message の id」と明記し、chat-wire の最小サンプル
  (`chats/new` → `completions`)を追加した(旧文は「message `id`」としか書いておらず、
  「先に作ったユーザーメッセージの id」と誤読できた — 実際に踏まれた)。
- 回帰は `test_chat_wire_message_id.py`(7 本)。うち 3 本は検証を外すと落ちることを確認済み。
  **罠**: ダミーモデル dict は `role="user"` だと `check_model_access` が
  `Model not found` を投げ、`metadata` 代入**前**に落ちるので payload 処理の except に
  入らない。エラー経路を突くテストは admin で回すこと。

## SSE 行の組み立ては `utils/sse.py` 一本(2026-08-05、#99)

- `data: ` の前置・`\n\n` の終端・JSON 直列化は **`sse_line()` / `sse_raw()` だけが持つ**。
  呼び出し側が f-string を組まないので、`json.dumps` の既定に戻る事故が構造的に起きない。
  - `sse_line(dict|list)` … JSON へ(**必ず `ensure_ascii=False`**)
  - `sse_raw(str)` … 既に JSON 文字列のもの(pydantic の `model_dump_json()` 等)を二重直列化せず流す
- **`ensure_ascii` の既定(True)は禁忌**: 日本語が 1 文字ごとに `\uXXXX`(6 文字)へ展開され、
  素の 6 倍の回線量になる。JSON としては同値なのでブラウザのパース結果は変わらず
  **表示は壊れない** — 壊れないまま黙って太る種類の欠陥で、書いた本人も気づけない。
  #99 では同じ 1 行が 13 箇所へコピーされていた(`completion_events` だけが正しかった)。
- 集約先: `middleware.stream_wrapper` / `response.convert_streaming_response_ollama_to_openai` /
  `functions.process_line`(pipe 出力)/ `routers/ollama.py`(blob upload・DL 進捗)。
- 番人は **`test/apps/webui/utils/test_sse.py`** — ast でリポジトリ全体を走査し、
  ①裸の `json.dumps` が allowlist(DB/ログ/送信ボディ/重複排除キー)以外に無いこと
  ②allowlist に実体の無い項目が残っていないこと ③`data:` 行を `sse.py` の外で
  手組みしていないこと ④`from json import dumps` の抜け道が無いこと、を固定する。
  **除外を増やすときは理由を書く**(allowlist に理由文字列を持たせてある)。
- 推論バックエンドへの送信ボディ(`routers/openai.py` / `ollama.py` の payload)は対象外 —
  受け手がデコードしてからトークン化するので、エスケープはトークンに影響しない。

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
- tool ループの追撃では reasoning_content を再送しない(#18-2 以来の挙動)。upstream v0.9.0 は
  保持する方向に直した(リリースノートのみ確認)が、nucllei は Kimi の挙動を優先して現状維持
  (ken 決定 2026-10-05「Kimi は特殊なので、あまりいじらない」)。
