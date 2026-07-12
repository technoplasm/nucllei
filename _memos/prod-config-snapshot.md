# 現行 llens(prism-hu/llens、北大稼働中)構成スナップショット

取得日: 2026-07-05(**ポート表記を 2026-07-13 の 133xx 帯移行に追従** — 正典は
prism-hu/llens `DEPLOYMENT.md` のサービス表)。出典: `~/src/github.com/prism-hu/llens/docker-compose.yml`
(OWUI v0.9.5 ベースの本番構成)。issue #12「今動いている構成の記録」に対応する
**デグレ検知の基準**。本 fork(v0.6.5 基点)で同等機能を検証する際はこの表に照らす。

注意: 現行は v0.9.5 の env 名。v0.6.5 に存在しないフラグは「0.6.5: なし」と注記。
値そのものではなく「この運用が成り立つこと」が保証対象。

## 構成トポロジ

- `open-webui`(host `127.0.0.1:13301` → コンテナ :8080)→ SGLang(host `:13300`、OpenAI 互換)
- `docling`(docling-serve v1.17.0 CPU、docker 内部 :5001。host debug 公開 `127.0.0.1:13304`)← 文書抽出
- `tts-bridge`(voicevox-openai-tts f169f7a、docker 内部 :8000)→ `aivisspeech-engine`
  (VOICEVOX 互換 API、docker 内部 :10101。host debug 公開 `127.0.0.1:13305`、CPU 版)
- `sandbox`(コード実行 API、docker 内部 :8000。host 公開 `127.0.0.1:13302` = fork OWUI dev 用)
- `cage`(ナレッジツリー API、host `:13303`)
- STT はコンテナ内蔵 faster-whisper(モデルはホストマウント、オフライン)
- 監視: dcgm-exporter → Prometheus/Grafana(issue #32 関連)

## モデル接続(CORE §3.4)

| env | 値 |
|---|---|
| `OPENAI_API_BASE_URL` | `http://host.docker.internal:13300/v1`(SGLang) |
| `OPENAI_API_KEY` | `EMPTY` |
| `ENABLE_OLLAMA_API` | `false` |

## Documents / RAG(issue #9 と一致)

| env | 値 | 備考 |
|---|---|---|
| `CONTENT_EXTRACTION_ENGINE` | `docling` | |
| `DOCLING_SERVER_URL` | `http://docling:5001` | |
| `DOCLING_PARAMS` | `{"do_ocr": false}` | 0.6.5: DOCLING_PARAMS なし(OCR パラメタ不可) |
| `BYPASS_EMBEDDING_AND_RETRIEVAL` | `true` | fork では既定化済み |
| `RAG_ALLOWED_FILE_EXTENSIONS` | pdf,docx,pptx,xlsx,csv,html,htm,md,markdown,txt,asciidoc,adoc,json,xml,vtt,tex | 画像・SVG 除外(OCR しない前提) |

## 認証・登録(issue #7 と一致)

| env | 値 |
|---|---|
| `ENABLE_SIGNUP` | `false` |
| `SHOW_ADMIN_DETAILS` | `false` |
| `PENDING_USER_OVERLAY_TITLE/CONTENT` | 運営チーム窓口の案内文(0.6.5: なし — v0.9.x 機能) |

## TTS(KEEP-MINIMAL §4.1)

| env | 値 |
|---|---|
| `AUDIO_TTS_ENGINE` | `openai` |
| `AUDIO_TTS_OPENAI_API_BASE_URL` | `http://tts-bridge:8000/v1`(末尾 /v1 必須) |
| `AUDIO_TTS_MODEL` | `voicevox-v1` |
| `AUDIO_TTS_VOICE` | AivisSpeech の style ID を直接指定 |
| `AUDIO_TTS_OPENAI_PARAMS` | `{"speed": 1.0}`(0.6.5: なし — 追加パラメタ機構は v0.9.x) |

→ fork 側スモークテスト: `test_audio_tts.py`(ブリッジ互換モックで speech/voices/models を固定)

## STT(KEEP-MINIMAL §4.2)

| env | 値 |
|---|---|
| `STT_ENGINE`(0.6.5 では `AUDIO_STT_ENGINE`) | ``(空 = 内蔵 faster-whisper) |
| `WHISPER_MODEL` | `/models/whisper/faster-whisper-large-v3-turbo-ct2`(ホストマウント・オフライン) |
| `WHISPER_COMPUTE_TYPE` | `int8`(0.6.5: なし) |
| `WHISPER_LANGUAGE` | `ja`(0.6.5: なし — 0.6.5 の内蔵 whisper は言語固定不可) |
| `WHISPER_VAD_FILTER` | `true`(0.6.5 に既存・既定 false) |
| `WHISPER_MODEL_AUTO_UPDATE` | `false` |

**対応済み**(2026-07-05 更新): WHISPER_LANGUAGE / WHISPER_COMPUTE_TYPE は
4244b534c で fork に実装済み(config.py)。WHISPER_VAD_FILTER は v0.6.5 に既存
(初版の「0.6.5: なし」は誤記)。日本語固定(`WHISPER_LANGUAGE=ja`)が実運用上重要。

## 機能 OFF(閉域運用)

`ENABLE_COMMUNITY_SHARING=false`(fork 既定化済み)、`ENABLE_MESSAGE_RATING=false`、
`ENABLE_EVALUATION_ARENA_MODELS=false`(fork 既定化済み)、
`ENABLE_VERSION_UPDATE_CHECK=false`(0.6.5: なし)、`ENABLE_PERSISTENT_CONFIG=false`
(**env を唯一の設定源にする運用**)、
`ENABLE_FOLDERS/MEMORIES/NOTES/CHANNELS/CALENDAR/AUTOMATIONS/USER_WEBHOOKS=false`
(多くは v0.9.x フラグ。0.6.5 に存在しないものは UI ごと存在しないか別名)。

## fork への示唆

1. `ENABLE_PERSISTENT_CONFIG=false` 運用が前提 — 設定レイヤ整理(issue #30)では
   「env 唯一の設定源」プロファイルを一級市民として扱うこと。
2. PDF→画像変換パッケージを焼き込んだ自前 Docker イメージを使っている
   (docker/open-webui/Dockerfile)。fork のイメージビルド(issue #25/#33)で踏襲。
3. TTS ブリッジは 1 ブリッジ = 1 エンジン。AivisSpeech は VOICEVOX 互換のため 1 本。
