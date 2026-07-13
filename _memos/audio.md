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
- **TTS**: `AUDIO_TTS_ENGINE="openai"` = OpenAI 互換(llens = tts-bridge → AivisSpeech)。
  fork プロセスは合成しない(プロキシ + mp3 キャッシュのみ)— fork 側の CPU/GPU 設定と無関係。
- **内部関数(#53)**: `utils/audio.py::transcribe_audio / synthesize_speech`。
  sync 提供(主 caller = sync tool。async からは `asyncio.to_thread`)。
  TTS の voice/model は config 既定 + 引数上書き、en/ja→voice の対応表は caller(prism 層 tool)責務。

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
- #56(疑似ストリーミング STT)は `transcribe_audio(path)` のチャンク呼びで乗る。
  `NUM_WORKERS > 1` はチャンク並列にもそのまま効く。
- tool が合成した音声のチャット内再生 UI は未定義(#53 スコープ外 — 必要になったら別 issue)。
