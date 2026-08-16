# SGLang × Kimi K2.6 出力フレーム仕様(実キャプチャ観察、issue #29)

キャプチャ日: 2026-07-05。サーバ: prism-llens:8000(SGLang、`kimi-k2.6`、
max_model_len=262144、**reasoning parser 有効**)。
fixture: `nucllei/test/fixtures/model_frames/`(生 SSE + 非ストリーム JSON +
チャンク境界 + meta.json)。ローダ: `nucllei/test/util/model_frames.py`。

## 再キャプチャ手順

```bash
uv run python scripts/capture_model_frames.py   # 既定 http://prism-llens:13300/v1
uv run pytest nucllei/test/apps/webui/test_model_frame_fixtures.py
git diff nucllei/test/fixtures/model_frames/   # 差分 = フレーム仕様の変化
```

SGLang / モデル更新時は必ず再実行し、差分をこのメモに追記する。

既存 fixture を保全したまま新シナリオだけ追加キャプチャするには `--only` を使う
(meta.json は既存エントリにマージ、他の `.sse`/`.json` は不変):

```bash
uv run python scripts/capture_model_frames.py \
  --only tool_call_no_args --only tool_call_unicode_args
```

## フレーム列の全体構造(全シナリオ共通)

```
role フレーム → reasoning_content* → content* → (tool_calls*) → finish フレーム → usage フレーム → [DONE]
```

1. **role フレーム**: `delta: {"reasoning_content": null, "role": "assistant", "content": ""}`
2. **reasoning デルタ**: `delta: {"reasoning_content": "<piece>"}`。
   **`<think>` タグは流れない**(parser がサーバ側で分離済み)。
   Kimi K2.6 は「1+1は?」でも必ず reasoning を出す(text_basic で 24 フレーム)。
3. **content デルタ**: `delta: {"reasoning_content": null, "content": "<piece>"}`
   (reasoning_content: null が明示的に載り続ける点に注意)
4. **finish フレーム**: 単独フレーム。`delta: {"reasoning_content": null}` +
   `finish_reason: "stop" | "tool_calls"` + **`matched_stop`(SGLang 拡張、stop token id)**
5. **usage フレーム**: `choices: []` + `usage`。
   **`usage.reasoning_tokens`(SGLang 拡張)** が载る。
   `stream_options.include_usage` を付けてキャプチャ。
   **注意(#57、2026-07-10 実測)**: usage フレームは `stream_options.include_usage`
   を付けた時**だけ**送られる(素の streaming リクエストでは一切来ない)。
   fixture に usage があるのはキャプチャスクリプトが明示注入していたため。
   OWUI 本体は `ec15b4552` から全チャット経路(payload 組み立て + tool ループ
   継続呼び出し)で注入し、実測 usage を Chat.meta["usage"] / message.usage に
   永続化する(context_size = base モデルの max_model_len 込み)。
6. `data: [DONE]`

## tool_calls の形状(重要)

- **id が OpenAI 形式(`call_xxx`)ではなく `functions.<name>:<index>` 形式**
  (例: `functions.get_weather:0`)。middleware/フロントの id 前提に注意。
- 呼び出しごとの最初のフレームに `id` + `function.name` + `arguments: ""`。
  以降のフレームは `id: null, function.name: null` で `arguments` の断片のみ。
- **引数 JSON はトークン境界で任意に割れる**(0〜33 文字/チャンク、空文字チャンクあり、
  `{"city": "札幌` のように文字列リテラル途中でも割れる)→ 逐次 JSON パーサ必須(#20)。
- 並列呼び出しは **index 順に直列に流れる**(index=0 完了後に index=1 開始)。
  interleave はしない。
- tool call 前に content が少量出ることがある(「調べます」等)。

## 引数エンコーディング(ast.literal_eval / json.loads の要否、issue #15 #28)

middleware は tool_call の `arguments` を **`ast.literal_eval` を先に試し、失敗時のみ
`json.loads`** にフォールバックする(`utils/middleware.py`)。追加キャプチャ(2026-07-05)で
実際にどちらが効くかを確認した:

- **`tool_call_no_args`**(引数なしツール `get_current_time`、properties 空):
  Kimi は `arguments` を **`"{}"`**(空文字ではなく空オブジェクト)で流す。
  ストリームでは最初のフレームが `arguments: ""`、続く 1 デルタで `"{}"`。
  → 空引数は `{}` にパースされ、許可引数が無いためツールへは空 dict が渡る。
- **`tool_call_unicode_args`**(引用符・改行・バックスラッシュ・絵文字を含む detail):
  Kimi は **厳密な JSON** を出す。引用符は `\"`、改行は `\n`、バックスラッシュは `\\` と
  正しくエスケープし、日本語・絵文字(☃️)は **`\u` エスケープせず生 UTF-8** で載せる。
  実測した非ストリーム arguments:
  `{"city":"札幌","detail":"札幌は\"寒い\"ことが多いです。\n☃️\nバックスラッシュ: \\"}`

**結論(最重要)**: Kimi の tool call 引数は clean な JSON であり、
その中で使うエスケープ(`\"` `\n` `\\`)は Python 文字列リテラル構文の部分集合なので、
**`ast.literal_eval`(一次経路)がそのまま成功し、`json.loads` フォールバックには落ちない**。
両パーサとも同一 dict を返すことをキャプチャ実データで確認済み。
`json.loads` フォールバックが実際に必要になるのは JSON の `true`/`false`/`null`
(Python は `True`/`False`/`None`)を含む引数のケースだが、現行ツール schema では発生しない。
リプレイ回帰は `test_native_tool_calling.py::test_native_tool_loop_parses_{no_args,unicode_args}_call`。

## 非ストリーミング応答

- `message.reasoning_content` に reasoning 全文、`message.content` に本文。
- tool_calls は複数要素の配列(並列 2 件を確認)。id 形式は同上。

## 異常系

- クライアント切断(aborted_client_cut.sse): 途中までの正常フレームのみ。
  finish/usage/[DONE] は無い。リプレイテストでの「切断」入力に使う。
- keep-alive コメント行(`: ping` 等)は観察されなかった。
- **回答の think 綴じ込み(2026-07-23 実害)**: Kimi は(特に tool 結果後の追撃で)
  最終回答を丸ごと think 内に書いて閉じタグ直後に EOT することがある。この場合
  reasoning parser は回答全文を `reasoning_content` で流し、**content デルタゼロのまま
  finish_reason=stop** で終わる。非決定的で fixture 未キャプチャ(決定的な再現は
  `dev/e2e/mock_reasoning_model.py` — このパターンを吐くモックモデルサーバ)。
  対策は assembler の昇格規律(`_memos/rendering-design.md` §3)。なお「think 内で回答を書いてから
  content で言い直す」パターンの方が多く、そちらは無害(昇格は発火しない)。

## proxy 越しの差分(LiteLLM、2026-08-14 実測)

上の仕様は **SGLang に直結したとき**の形。OpenAI 互換 proxy を挟む配備
(llens staging は LiteLLM v1.82.3 経由)では次が崩れる。**fixture は SGLang 直の
一次資料として保つ** — proxy の形は `test_streaming_usage.py` の
`_litellm_frames()` に実キャプチャ形式で合成してある。

1. **usage フレームの `choices: []` が保たれない。** 同じ usage が
   `choices: [{"index": 0, "delta": {}}]`(finish_reason 無し)のチャンクに載って
   届く — finish_reason チャンクの次、`[DONE]` の直前。`choices: []` は OpenAI の
   streaming 仕様が定めた形ではないので、**フレームの形を永続化の条件にしない**
   (consume_model_stream はストリーム終端で最後に観測した usage を拾う)。
2. **チャンクに載る累積 usage が消える。** `stream_options` 自体は proxy を素通りして
   SGLang まで届く(`litellm/utils.py` の drop_params ループが `stream_options` を
   明示的に除外している)ので、SGLang は continuous で usage を返している。**落として
   いるのは LiteLLM の受信側** — `litellm_core_utils/streaming_handler.py` が全チャンク
   から usage を削除し(`# remove usage from chunk, only send on final chunk`)、
   最後に自前で 1 個合成する。上の 1. の「choices 非空 + usage」はこの合成チャンク。
   ハードコードで config のノブは無く、1.96 でも同じ(退避先が `_hidden_params` に
   増えただけ)。生成中のライブ tok/s(#102)は proxy 経由では出せない — 確定値表示のみ。
   raw が要るなら LiteLLM の pass-through endpoint(`general_settings.pass_through_endpoints`、
   SSE をバイト単位で無改変に流す)を使う。
3. **`usage.reasoning_tokens`(SGLang 拡張)が来ない。** 代わりに OpenAI 形の
   `usage.completion_tokens_details.reasoning_tokens` が入る。
4. **`/v1/models` の `max_model_len` が消える。** LiteLLM 1.82.3 は
   id/object/created/owned_by の 4 キーしか返さない(context 長が既知の商用モデルでも
   同じ)。**1.96 以降は `max_input_tokens` / `max_output_tokens` / `mode` を返す**
   (`proxy/utils.py` の `create_model_info_response`。config.yaml の
   `model_info.max_input_tokens` が `router.get_configured_token_limits()` 経由で
   優先される)。`get_model_context_size` は `max_model_len` と `max_input_tokens` を
   同格に読む。どちらも来ない配備は `info.meta.context_size` を手で設定して補う
   (最終 fallback)。
5. delta の `reasoning_content: null` の明示、`matched_stop`、`logprobs` も落ちる。
6. **先が SGLang ですらない場合、`continuous_usage_stats` は 400 で弾かれる**
   (2026-08-16 実発症: prism-spark:4000 ゲートウェイの gpt-5.6-luna —
   LiteLLM 経由の OpenAI/Anthropic 系は未知の stream_options キーを
   `Unknown parameter` で拒否する)。egress(`routers/openai.py`)が自動対応する:
   4xx かつ本文がキー名に言及する拒否を観測したら拡張だけ剥がして 1 回再送し、
   `(url, model)` をプロセス内で学習して以後送らない(`include_usage` は維持 —
   ライブ累積 usage だけの劣化で完結。5xx・無関係の 4xx では学習しない)。
   手動 opt-out `capabilities.continuous_usage=False` は併存(`ed05d518e`)。

## 未キャプチャ(条件が変わったら追加)

- **reasoning parser 無効**時の生 `<think>` タグ混り content
  (サーバ起動オプション変更が必要。現行本番は parser 有効運用のためスコープ外)
- サーバ側エラーフレーム(`{"error": ...}`)、途中での server 切断

## モデル向け文字列の言語規約(2026-07-15 決定)

**システム本体がモデルに見せる注釈・tool 結果メッセージは日本語のまま書く**
(builtin tool の結果文言、middleware のファイル未抽出注釈、tool エラー等)。

- 根拠: Kimi は日本語プロンプトだと CoT も日本語になる。ここに英語を混ぜると
  思考・応答が英語に引っ張られうる。海外展開・OSS 再公開は非想定なので
  英語化の動機がない。
- 対して **UI 文字列は英語キー + ja-JP 翻訳**(i18n)、**識別子・表示名
  (tool の name 等)は英語** — 「モデルが読む文字列」だけがこの規約の対象。
- backend の HTTPException detail(例: files.py のアップロード拒否)は FE の
  i18n を通らず素通しでトーストに出るため、これも日本語のまま(ユーザー向け)。
