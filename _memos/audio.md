# 音声(STT / TTS)— 構成・内部関数・ハードスケーリング

STT/TTS の fork 側実装と、性能・ハード(強い CPU / 将来 GPU)方針の真実源。
llens 現行の実機構成は `_links/llens` の `stt/README.md` / `tts/README.md`。
内部関数の設計経緯は #53、ファイル処理との関係は `file-handling.md`。

## 有効/無効(配備レベルの機能フラグ)

`ENABLE_AUDIO_STT` / `ENABLE_AUDIO_TTS`(persist: `audio.stt.enable` / `audio.tts.enable`、既定 on、
admin Settings > Audio にトグルあり)。off にすると **FE のアイコン(マイク / 読み上げ / Call)非表示 +
`/transcriptions`・`/speech` が 403**(サーバー側 enforcement)。#66 で廃止した per-user 権限フラグ
(誰に使わせるか)とは別物 — こちらは「この配備に機能があるか」。Call は STT+TTS 両方を使うので
どちらか off で非表示。内部関数(`utils/audio.py`)はこのフラグを見ない(caller = admin 作の tool/filter
の責務。エンジン未設定なら既に ValueError)。

## 構成(2026-07-13 現在)

- **STT**: `AUDIO_STT_ENGINE=""` = 内蔵 faster-whisper(**fork プロセス内**で CTranslate2 推論)/
  `"openai"` = 外部 OpenAI 互換 API。llens 現行 = 内蔵 large-v3-turbo・int8・CPU。
  faster-whisper は extras `stt`(torch 無しで軽い — Docker イメージに同梱)。
  dev では `uv sync --extra stt` か `--extra local-ml` で入る(2026-07-16 の extras 分割)。
- **TTS**: `AUDIO_TTS_ENGINE="openai"` = OpenAI 互換(llens = tts-bridge → AivisSpeech)。
  fork プロセスは合成しない(プロキシ + mp3 キャッシュのみ)— fork 側の CPU/GPU 設定と無関係。
- **内部関数(#53)**: `utils/audio.py::transcribe_audio / synthesize_speech`。
  sync 提供(主 caller = sync tool。async からは `asyncio.to_thread`)。
  TTS の voice/model は config 既定 + 引数上書き、en/ja→voice の対応表は caller(prism 層 tool)責務。

## 逐次文字起こし(疑似ストリーミング)と読み取り猶予(#56、2026-09-04)

録音バー(`src/routes/(app)/chat/MessageInput/VoiceRecording.svelte`)は完全バッチ
(stop 時に 1 blob を投げる)をやめ、**発話の区切りごとに転写して入力欄へ追記**する。

- **切り方**: timeslice で細切れにしない。webm/opus は先頭チャンクにしかヘッダが無く、
  2 個目以降が単体でデコードできないため。**無音の切れ目で MediaRecorder を stop →
  すぐ次のセグメントを起こす**(CallOverlay の `stopRecordingCallback` と同じ形)。
- **無音検出**: 波形表示と同じ rAF ループで AnalyserNode(`minDecibels` = -45)+ `lastSoundTime`。
  外部 VAD ライブラリは入れない(オフライン配備で CDN / モデル DL を持ち込まない制約)。
  音を検出したところで `MediaRecorder.start()`(無音だけのセグメントを作らない)。
- **転写**: 既存の `transcribeAudio()`(`POST /api/v1/audio/transcriptions`)のまま。
  新エンドポイント・WebSocket は作らない(socket.io は撤去済み — `streaming-wire-v2.md`)。
- **順序保証**: チャンクは**直列 Promise キュー**(`transcribeQueue`)に積む。
  並列に投げると順不同で追記され文が壊れる。確定ボタンも同じキューの最後尾に乗るので、
  途中チャンクの追記が終わってから `onConfirm` が走る(二重挿入・順序逆転が構造的に起きない)。
- **親への渡し方**: 途中の区間は `onTranscript({text})`(入力欄へ追記するだけ)、
  最後の 1 区間だけ従来どおり `onConfirm({text})`(追記 + 録音 UI を閉じる + `speechAutoSend`)。
- **表示**: 録音中は入力欄が録音バーに置き換わって見えないので、確定済みテキストを
  バー内にも出す(親の `prompt` と同じ内容)。
- **トグルは作らない(常時 ON)**: 逐次はバッチの上位互換 — 一度も間を置かずに喋れば
  結果は従来と同一(1 チャンク)。トグルを付けると使われない 2 本目の経路が残るだけで、
  fork の「互換のためだけのコードを残さない」規律に反する。可変にする価値があるのは
  下の「読み取り猶予」の方なので、そちらを設定に出した。
- **キャンセルの意味**: 既に入力欄へ入ったテキストは残る(送信済みの確定分)。
  飛行中の転写結果は破棄する(`aborted`)。

### 読み取り猶予(`$settings.voiceSilenceTimeout`)

無音がこの長さ続いたら発話の区切りとみなす。**既定 4000ms**
(旧ハードコード 2000ms は「喋りながら考える」人には短すぎる — ken 指摘。
上限側は Call の応答遅延に直結するので 3〜5 秒レンジの中央を採った)。
UI は **Settings > Chats > Voice**(0.5〜30 秒、秒で入力し ms で保存)。
**録音バーと Call オーバーレイの両方**が読む(Call の `2000` ハードコードは撤去済み)。

同じ設定群に **`$settings.voiceMinChunkDuration`(既定 1000ms)** —
これより短い区間は精度が落ちるので転写に送らない。

### secure context ガード

`navigator.mediaDevices` は **secure context(https / localhost)でしか生えない**。
llens 実機は `http://<内部IP>/` 配備なので、無ガードだとマイク/Call ボタンが
**トーストも出さずに無反応**になる(getUserMedia 呼び出しで例外)。
`MessageInput.svelte` / `CallButton.svelte` はボタンを押せない見た目にしてツールチップで理由を出し、
押した場合もトースト(`Microphone requires a secure (HTTPS) connection.`)を出す。
`VoiceRecording.svelte` / `CallOverlay.svelte` の getUserMedia 直前にも同じガードを置く。
**`ENABLE_AUDIO_STT=false` の非表示とは別物** — あちらは「この配備に機能があるか」、
こちらは「このブラウザ経路で使えるか」。

### アップロードの MIME(`audio/webm`)

FE は長らく `recording.wav` / `type: 'audio/wav'` の**偽装ラベル**で送っていた
(MediaRecorder の実体は Chrome/Edge = webm/opus)。`routers/audio.py` の許可リストと
拡張子マップに `audio/webm` を足し、FE は `MediaRecorder.isTypeSupported` で
`audio/webm` → `audio/ogg` の順に選んで**実際の MIME をそのまま送る**。
**残件**: Safari の MediaRecorder は `audio/mp4` しか作れず、許可リストに無いので 400
(現状は偽装で通っていた)。必要になったら `audio/mp4` を許可リスト + 拡張子マップに 1 行ずつ足す
(mp4 音声は非アクティブ形式なので GHSA-m8f9-9whg-f4xr の防御方針とは矛盾しない)。

## 並列モデル — どの層で直列化されるか(STT 内蔵経路)

1. **サービング層**: route は sync def / to_thread → AnyIO threadpool(既定 40 本)。
   並列リクエストの受付上限はここ。
2. **モデル層**: `WHISPER_NUM_WORKERS`(既定 1)= CTranslate2 のワーカー数。
   **1 のままだと同時文字起こしはモデル内で直列化**(スレッドは並んで待つだけ)。
   CTranslate2 は推論中 GIL を解放するので >1 で真のスレッド並列になる。
3. **演算層**: `WHISPER_CPU_THREADS`(既定 0 = ライブラリ既定)= 1 本の文字起こしの
   intra-op スレッド数。既定値は環境依存(OMP_NUM_THREADS 等)で、
   **強い CPU を使い切る保証がない — 本気運用では明示指定**。

総使用スレッド ≈ `NUM_WORKERS × CPU_THREADS`。強い CPU での目安:
ディクテーション主体なら `CPU_THREADS` = 物理コアの 1/2 程度から実測、
`NUM_WORKERS` = 同時発話者数の見込み(2〜4)。実測ベンチは未実施(#12 スモークと同時にやる)。
env 変更の反映は再起動(モデル再ロードは起動時 lazy と STT config 更新時のみ)。

## GPU を含むスケール路線(広く見据えた整理)

- **路線A: 内蔵 + CUDA** — `DEVICE_TYPE=cuda` で配線済み。`WHISPER_COMPUTE_TYPE` は
  float16 / int8_float16 へ(int8 既定は CPU 向け)。cpu_threads は無関係になる。
  欠点: **web プロセスが CUDA コンテキストを抱える**(VRAM 常駐・再起動の重さ・依存)。
- **路線B: 外部 OpenAI 互換 STT サーバ(本命)** — TTS bridge と同型の別コンテナ
  (speaches(旧 faster-whisper-server)等)を立て、`AUDIO_STT_ENGINE=openai` に切替。
  **fork はコード変更ゼロ**、ハード増強(強 CPU でも GPU でも)は STT コンテナの都合に閉じる。
  GPU 本気運用はこちらを既定路線とする。
- どの路線でも**内部 API(sync)の形は不変**: 内蔵推論は CPU/GPU とも呼び出しスレッドを
  ブロックする同期 API、外部エンジンは network I/O — いずれも「スレッドに逃がす」が正解で、
  caller(tool/filter)のコードは書き換え不要。async 化はここでは利得がない
  (CPU/GPU-bound には無力、低〜中並列の I/O では threadpool と差が出ない)。

## 将来の注意点

- threadpool 40 本: 長尺音声の大量並列で埋まる運用が見えたら STT 専用 executor / semaphore を
  検討(現状のディクテーション用途では不要)。
- ~~#56(疑似ストリーミング STT)は `transcribe_audio(path)` のチャンク呼びで乗る~~
  → **実装済み(2026-09-04)**: FE 側で発話区間を切って既存 `/transcriptions` を直列に叩く形
  (上の「逐次文字起こし」節)。BE は無改造。1 発話 = 1 リクエストになるので、
  複数人が同時にディクテーションする配備では `WHISPER_NUM_WORKERS > 1` の効きが上がる。
- ~~tool が合成した音声のチャット内再生 UI は未定義(#53 スコープ外 — 必要になったら別 issue)~~
  → **確定(2026-07-20、#87)**: チャット内提示は 2 形態。
  - **リンク**: markdown-it-attrs による出し分け — `[DL](/api/v1/files/{id}/content){download=voice.mp3}` /
    `{target=_self}` / 無指定 = 新規タブ(allowlist は target / download のみ)。
  - **埋め込み再生**: 生 HTML `<audio controls src="/api/v1/files/{id}/content"></audio>` を
    DOMPurify(`sanitizeChatHtml`、autoplay は FORBID_ATTR)経由で許容。video も同様。
    判断根拠: レンダラの「HTML を作らない」規律は**機械構造**(reasoning/tool_calls)の話で、
    モデル・tool 著の**コンテンツ** HTML は DOMPurify の柵内で従来から許容(画像・iframe 特例と同列)。
    markdown-it-html5-embed / block-embed は不採用 — renderer ルール型は lex()(トークン変換)経路で
    無反応、ruler 型は独自トークンがコンバータで落ちるため、導入しても配線コストは自前実装と同額。
    素の `<audio>` の方がモデルの既知度も高い。カスタム UI(波形等)が要る時は lexer 段の
    自前トークン化で置換可能(書式は不変のまま移行できる)。
  - `/content` は audio/* / video/* を **inline 配信**(attachment だと Firefox が再生拒否 —
    routers/files.py、保存導線は `?attachment=true` と `{download}` リンク)。
  - prism 層(prism-hu/llens)は skill 遅延ロード化済み: `nucllei/skills/audio.md`
    (tool_ids: audio_tools)で preset には skillIds で紐づけ、音声の指示が要るときだけ
    context に載る。tool の戻りはプレイヤー埋め込み + {download} リンクの 2 行
    (モデルが本文に貼る契約)。
  - **将来(#88)**: 「モデルに貼らせる」契約は tool のユーザー向け表示チャネル(display)
    確立後にそちらへ差し替え検討(tool が自分で表示すれば約束が構成的に真になる)。
