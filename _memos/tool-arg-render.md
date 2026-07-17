# tool 引数/結果のレンダリング仕様(型駆動)

2026-07-06 確定、同日ステップ 1〜4 実装済み(下記「実装状況」)。#20(L3)の実装根拠。設計は #14 レンダリング刷新トラック(`_memos/rendering-design.md`)の上に載る。
`content_blocks` の tool_calls ブロック(#17 で確定)が土台。

## 問題

コード実行が tool 化された結果、`run_python(code=...)` の `code` 引数が
`{"code":"def f():\n  ...\n"}` という **エスケープ済み JSON 文字列**として表示され、
`\n` が生・言語ハイライト無し・複数行が一枚岩になる(実キャプチャ `tool_call_long_code`)。
従来「コードブロックとして直接生成」していた体験からの明確な劣化。

## 原則:表示契約は「引数/戻りの型」が持つ

表示都合のメタを **名前** や **スキーマ別建て(x-render)** で後付けしない。
**パラメータ/戻りの型そのもの**が描画の意味を運ぶ。tool 作者はシグネチャを書くだけ。

- **名前ヒューリスティック不採用**(`code`/`sql` というキー名で推測しない)。筋が悪く、複数 code 引数・
  実行時に言語が決まるケースで破綻する。
- **長さヒューリスティック不採用**(長い str を勝手に block 化しない)。
- フォールバックは「**型に忠実**」= 素の str は文字列として、object/array は JSON として出す。推測して code にしない。

## 描画種別(kind)

入力と結果で集合が違う(結果は image が活きる。入力に table は不要 — LLM は表を JSON 配列/CSV で渡す)。

| | 種別 |
|---|---|
| **引数(入力)** | `code(language)` / `scalar`(str/num/bool を `key: value`)/ `json`(object/array ツリー) |
| **結果(出力)** | `text` / `code(language)` / `json`(既定) / `image` / `file` |

- 入力で専用ウィジェットを要求する宣言型は **`code(language)` のみ**。他は JSON 値の構造から自動。
- 結果は戻り型で `text`(素の値)/ `code` / `json`(既定)を選べる。モデルへの見え方(直列化テキスト)は不変。
- **改訂(2026-07-09、`ada5904e8`): `table` 描画を廃止**。tool 結果は「値を見る」ものであり
  デザイン対象ではない、という判断。`table` 宣言でも json 表示に落とす(`classifyToolResult` から
  table 分類・`tryParseTable` を撤去、`ResultDisplay` の `table` 型も削除)。
- 表示の体裁(2026-07-09): 展開時の `Arguments` / `Result` はグレーの pill バッジ、その中身は軽く
  字下げ(ps-2)。ヘッダは done=緑チェック(tool)/ 青チェック(reasoning)、実行中は Spinner。
  応答生成中は `[Spinner] Preparing...`(reasoning の `Thinking...` と同寸で自然に遷移)。
- **改訂(2026-07-17): 結果の既定フォールバックを「型に忠実」に修正**。旧実装は宣言なしの
  非 JSON 文字列を `JSON.stringify(String(...))` でクォート囲み表示し、JSON 風文字列は
  勝手に整形していた — 原則(素の str は文字列として出す・推測しない)と二重に食い違い。
  現在は **backend が実行時の実型から表示種別を同梱**する: dict/list は `json.dumps` +
  `results[i].render = {kind: "json"}`、str/スカラーは render なし。フロントの優先順位は
  「戻り型宣言(`calls[].render.result`)→ 実型ディスクリプタ(`results[i].render`)→
  text」で、**中身から JSON かどうかを動的解釈する経路は無い**(`classifyToolResult`)。
  エラー文字列(str(e) / tool not available 等)も素で読める。あわせて `parseJSONString` の
  スカラー無限再帰(スタックオーバーフロー頼みの停止)を修正、backend は content を常に
  文字列に正規化(int/float/bool は str()、None は空文字)。
  結果 kind の目視確認は dev の `render_probe` tool(`probe_result_*` 群 — `dev/tools/README.md`)。
- **改訂(2026-07-17): コード表示を HljsCode に一本化**。チャット本文のコードフェンス
  (旧 CodeMirror ベースの CodeBlock)も tool 引数/結果も、同じ「ヘッダ(言語名 +
  折りたたみ + コピー)+ hljs 静的ハイライト」で描画する(`common/HljsCode.svelte` が
  ヘッダごと持つ。展開初期値は `expandCodeBlocks` 設定に統一)。チャット内のコード編集
  (Save → message content 書き戻し)は機能ごと撤去 — 表示専用で、ここで直したコードを
  実行する経路はない。CodeMirror(CodeEditor)が残るのは workspace のエディタ画面のみ。
  ※ save/onUpdate の上流配管(Markdown/ContentRenderer/ContentBlocks/ResponseMessage の
  applyContentUpdate)は死んでいる — Messages 樹の正常化トラックで掃除する。

## 宣言メカニズム

**ネイティブ Python tool** — `Annotated` に描画マーカーを載せる。実行時/型的には素の `str`:

```python
def Code(language: str):                 # 生成器(CodeType("python") 相当)
    return Annotated[str, RenderAs(kind="code", language=language)]

PythonCode = Code("python")              # 別名は生成器の薄い糖衣
SqlQuery   = Code("sql")

def run_python(code: PythonCode) -> Stdout: ...   # LLM/実行時は str、描画は python
```

**外部 OpenAPI tool** — JSON Schema 標準の **`contentMediaType`**(独自 `x-` を発明しない):

```jsonc
{ "code": { "type": "string", "contentMediaType": "text/x-python" } }
```

戻り型も同様に型駆動(`-> Table` / `-> Code("python")` / `-> Stdout` 等)。

## wire(正規化ディスクリプタ)

宣言方法(ネイティブ/OpenAPI)の差はバックエンドで吸収し、フロントには**正規化済みディスクリプタ**を渡す:

```jsonc
// content_blocks の tool_calls ブロック / tool_call イベントに同梱
calls[i].render = {
  args: { "<paramName>": { kind: "code", language: "python" }, ... },  // 宣言のある引数のみ
  result: { kind: "text" }                                             // 戻り型があれば(table は廃止)
}
```

- `language` は **hljs 言語 id**。バックエンドが media type ↔ hljs id を **変換テーブル**で正規化。
  テーブルは基本 **素通し**(`python`→`python`)+ **alias**(`shell`→`bash` 等)+ **fallback**(不明は `plaintext`)。
  「得しか無い」ので変換テーブルは保持する。
- 永続化(message.content_blocks)にも同梱 → リロード時にフロントから spec 再取得不要。

## strip はしない

`contentMediaType` は「この文字列は Python」という**正当な型情報**で、モデルに見せて良い影響しかない
(エスケープ/言語の正確性が上がりうる)。表示専用ゴミを作らないので **strip 判断自体が消滅**。
唯一の確認: **Kimi/SGLang が schema に `contentMediaType` があっても tool calling を劣化させないか** →
fixture で検証。劣化するなら LLM 向け spec からのみ落とす(表示ディスクリプタは別チャネルで保持)。

## フロントは dumb

引数レンダラはディスクリプタ通りに各引数をウィジェット描画する。名前も長さも見ない。

- `code(lang)` → `HljsCode`(language 指定)。文字列アンエスケープ(`\n`→改行、`\"`→`"`)を含む。
- `scalar` → `key: value`(値はアンエスケープ)。
- `json` → JSON ツリー/整形。
- これにより **R3(アンエスケープ)/ R4(複数引数の共存・fence 開閉問題)は構造的に自動解決**。

## ストリーミング(後段)

R7: まず「**型どおりに完成品を綺麗に描画**」を固め、逐次表示は後段。設計上は閉ざさない
(#20 の寛容 JSON パーサ = 部分値 + streaming フラグを各ウィジェットに供給)。仕様が固まっているので
テストケースで抑えられる。

## 実装ステップ(spec → 型どおり描画 → streaming)

1. **仕様確定 + 型/抽出**: `RenderAs`/`Code()` 型、media↔hljs 変換テーブル、wire ディスクリプタを定義。
   「型 → ディスクリプタ抽出」の単体テストを先に書く(仕様をテストで固定)。
2. **バックエンド**: spec ビルダが Annotated / `contentMediaType` からディスクリプタ抽出 → tool_call emit と
   `snapshot_content_blocks` の `calls[].render` に同梱。`tool_call_long_code`(run_python)fixture で
   「code param のディスクリプタ = {code, python}」を固定。Kimi 耐性(contentMediaType 同梱)を検証。
3. **フロント(引数)**: ディスクリプタ駆動の引数レンダラ。今の一枚岩 `HljsCode(JSON)` を置換。
   run_python 長コードが Python ハイライトで出ることを実描画で確認。
4. **結果(R5)**: 戻り型/内容で `text`/`code`/`json`/`image`/`file` 出し分け。`table` は同時 or 直後
   (レンダコスト高め)。image/file は既存処理を踏襲。
5. **ストリーミング(R7)**: 寛容 JSON パーサで逐次描画。テストで抑えつつ後段。

## 実装状況(2026-07-06)

**ステップ 1〜5 すべて出荷済み**(5 は同日後半に実装 — 下記)。

- **1. 型/抽出**: `nucllei/utils/tool_render.py`。`RenderAs` / `Code(language)` /
  `Text` / `Table`、media↔hljs 変換(素通し+alias+fallback plaintext)、
  `extract_render_from_function`(Annotated)/ `extract_render_from_spec`(contentMediaType)/
  `apply_render_to_spec`(宣言→spec 反映、既存宣言は上書きしない)。単体テスト
  `nucllei/test/apps/webui/utils/test_tool_render.py`。
- **2. backend 配線**: `get_tool_specs`(保存時)と `get_tools`(実行時。stale な DB spec でも
  生きた関数から反映)が spec に contentMediaType を注入し `tool_dict["render"]` を持つ。
  middleware の tool_call ループが `calls[].render` に同梱 — snapshot 素通しでイベント・永続化
  双方に乗る。**継続リクエストの assistant.tool_calls は {id, type, function} に絞り、render/index を
  モデルへ漏らさない**。`tool_call_long_code` リプレイで {code, python} を固定。
- **3+4. フロント**: 純ロジック `src/lib/utils/tool-render/`(vitest 14 件)+ Collapsible 統合。
  code→HljsCode(言語付き、JSON.parse がアンエスケープ)/ scalar→`key: value` / json→整形、
  結果は text(pre-wrap)/code/json/table(object 配列のみ、違えば json に落とす)。
  壊れた JSON のみ従来の一枚岩表示にフォールバック。空引数 {} は Arguments 節ごと省略。
- **Kimi 耐性 = 検証済み・問題なし**(2026-07-06、実 SGLang)。fixture 同一プロンプト
  (tool_call_long_code)+短コード+unicode の 3 シナリオを contentMediaType 付き schema で
  投げ、全て finish_reason=tool_calls・厳密 JSON・id 形式も fixture と同一。**strip 不要が実機で確定**。
- 実描画確認: dev/tools/render_probe.py に `run_python`(Code+Text)/`probe_result_table`(Table)
  を追加し、実 Kimi 端到端 + リロード(永続化 render からの再描画)を Playwright で確認。
- **5. ストリーミング逐次描画**(beb04dbe5 / b05ffa141 / 91e9f6f4d):
  - 寛容パーサ `src/lib/utils/tool-render/partial.ts`: **累積文字列を毎フレーム再スキャンする
    ステートレス実装**(フレーム間状態なし)。実キャプチャの切断実態 = 文字列値内部が最頻
    (long_code 129/130 デルタ)・**エスケープの分断**(`…\` で切れ次チャンクが `n…`)・
    `\uXXXX` 途中・キー/値未確定。不完全エスケープは「保留」し、部分値が最終値の
    **前方一致で単調に伸びる**ことを fixture 全プレフィックスで検証。
  - backend: delta 蓄積中から tool_calls を open ブロックとして content_blocks に載せ
    (content は response_tool_calls と**参照共有**、実行ループは同一オブジェクトなら再追加しない)、
    部分 arguments 込み snapshot を毎フレーム emit。render は name 確定(初回フレーム)時点で付与。
    **snapshot_content_blocks は calls/results を deepcopy** — 参照共有だと emit 済み
    スナップショットが累積更新で遡って書き換わる(テストで発覚した wire 不変性の穴)。
  - UI: `parseToolArgumentsStreaming`(完全 JSON なら確定表示と同一、未閉鎖なら streaming
    フラグ付き部分表示)。tool_calls の既定展開は **settings.expandDetails に従う**(ユーザー指定)。
  - 実 Kimi ストリーミングで部分コードの単調な伸び(124→307→497→520 文字)とハイライト描画を実測。
- **残**: OpenAPI tool の**戻り値**ディスクリプタは未抽出(consumer が現れる #18-3/nsjail 接続時に、
  responses スキーマの contentMediaType 対応を検討)。issue #20 の要件 1〜4 はすべて充足。

## 関連

[[llens-renderer-track-status]] / `_memos/rendering-design.md`(L0〜L3)/ 旧 `_memos/tools-catchup.md`(#18 — #34 棚卸しで削除済み)
