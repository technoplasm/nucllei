# tool 引数/結果のレンダリング仕様(型駆動)

2026-07-06 確定。#20(L3)の実装根拠。設計は #14 レンダリング刷新トラック(`_memos/rendering-design.md`)の上に載る。
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

入力と結果で集合が違う(結果は image/table が活きる。入力に table は不要 — LLM は表を JSON 配列/CSV で渡す)。

| | 種別 |
|---|---|
| **引数(入力)** | `code(language)` / `scalar`(str/num/bool を `key: value`)/ `json`(object/array ツリー) |
| **結果(出力)** | `text` / `code(language)` / `json` / `table` / `image` / `file` |

- 入力で専用ウィジェットを要求する宣言型は **`code(language)` のみ**。他は JSON 値の構造から自動。
- 結果は **tool が「きれいに見せる(table 等)/ モデルが読めればいい(text/json)」を戻り型で選べる**。
  モデルへの見え方(直列化テキスト)は不変、ユーザーへの見え方だけ親切になる非対称が狙い。
- 増やすのは容易(`Markdown` 型で長文段落、等)。v1 は上記で始める。

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
  result: { kind: "table" }                                            // 戻り型があれば
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

## 関連

[[llens-renderer-track-status]] / `_memos/rendering-design.md`(L0〜L3)/ `_memos/tools-catchup.md`(#18)
