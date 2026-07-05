# SGLang × Kimi K2.6 出力フレーム仕様(実キャプチャ観察、issue #29)

キャプチャ日: 2026-07-05。サーバ: prism-llens:8000(SGLang、`kimi-k2.6`、
max_model_len=262144、**reasoning parser 有効**)。
fixture: `backend/open_webui/test/fixtures/model_frames/`(生 SSE + 非ストリーム JSON +
チャンク境界 + meta.json)。ローダ: `test/util/model_frames.py`。

## 再キャプチャ手順

```bash
uv run python scripts/capture_model_frames.py   # 既定 http://prism-llens:8000/v1
uv run pytest backend/open_webui/test/apps/webui/test_model_frame_fixtures.py
git diff backend/open_webui/test/fixtures/model_frames/   # 差分 = フレーム仕様の変化
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

## 未キャプチャ(条件が変わったら追加)

- **reasoning parser 無効**時の生 `<think>` タグ混り content
  (サーバ起動オプション変更が必要。現行本番は parser 有効運用のためスコープ外)
- サーバ側エラーフレーム(`{"error": ...}`)、途中での server 切断
