# ストリーミング LLM 出力のレンダリング設計

2026-07-05。#14(レンダリング刷新)・#17(reasoning 構造化)・#20(tool 引数レンダリング)の設計根拠。
実害の記録は prism-hu/llens の `cage/docs/07_markdown_setext_trap.md` と `owui/filters/md_setext_guard.py` を参照。

---

## 1. 問題は 2 つあり、独立している

### 問題 A: 未完テキストのパース(時間軸の問題)

markdown には「**後の行が前の行の意味を遡って変える**」文法が複数ある:

| 文法 | 遡及の仕方 |
|---|---|
| setext 見出し | 段落の次の行が `---`/`===` だと、段落全体が遡って `<h2>`/`<h1>` になる |
| 表 | ヘッダ行は 2 行目の `\|---\|` が来て初めて表になる |
| fence 閉じ | ` ``` ` が閉じるまでコードブロックの範囲が確定しない |
| 参照リンク | `[foo]` の解釈は文書末尾の `[foo]: url` 定義に依存する |
| HTML ブロック | 終端条件(空行 or 終了タグ)が来るまで範囲未確定 |
| リスト lazy continuation | 次行のインデントで前行の所属が変わる |

ストリーミングでは常に「プレフィックス」しか見えないため、**出力途中に瞬間的に壊れた表示になるのは実装バグではなく文法の性質**。完全な解決は不可能で、できるのは「壊れうる範囲を狭く閉じ込める」ことと「遡及ルール自体を消す」こと。

### 問題 B: 構造の混線(チャネルの問題)

reasoning・tool call・引用などの機械構造を、markdown 文字列という 1 本のテキスト平面に直列化して再パースすると、本文とメタ構造が**相互に注入し合う**。setext トラップはこの典型:

1. OWUI の `serialize_output()` が text / `<details type="tool_calls">` / `<details type="reasoning">` の各 part を `.strip()` して単独 `'\n'` で join → モデルが置いた空行が破壊される
2. `</details>` 直後に裸の `---` が並ぶ
3. marked の lheading が「`---` から直前の空行まで」を丸ごと 1 個の `<h2>` に飲み込み、`<details>` はインライン扱いで HTML 無効化 → エスケープされた生テキストが露出

現行 prod(prism-hu/llens)はこれを 3 層(middleware join パッチ / outlet filter `md_setext_guard` / プロンプト指示)で抑えているが、いずれも対症療法。**根本原因は「1 本の文字列に混ぜて毎フレーム全文再パースする」アーキテクチャ**にある。

同じ病理クラスの症状 2 方向(いずれも details 擬似 HTML 由来、L0 で構造的に消滅):
- **閉じない**: reasoning が done=false のまま永続化(→ §3 閉じ規律で middleware 側は修正済み)
- **吸い込む**(0.9.5 prod で観察・2026-07-06 報告): tool 実行が多くなると本文テキストが
  思考の折りたたみ内に取り込まれ、**出力が空に見える**。details トークナイザの
  深さカウント(`findMatchingClosingTag`)は details が多段に並ぶほど本文の帰属判定を壊しやすい。

---

## 2. 世の中の方策

**(a) 全文再パース + throttle(OWUI 現行)**
実装が楽。毎チャンク O(n) パース → 合計 O(n²)、長文で重くなる。ちらつき・遡及再解釈をすべて被る。

**(b) ブロック凍結型インクリメンタル(streamdown / llm-ui 等の流儀)**
markdown のブロック境界は概ね行単位で決まる。空行で閉じ、かつ遡及候補(setext 直前・表ヘッダ候補・fence 内・HTML ブロック内)でないブロックは「確定」とみなし、レンダリング済み DOM を凍結。**毎フレーム再パースするのは末尾の未確定ブロック 1 個だけ**。合計 O(n) 化、ちらつきは末尾に局所化。壊れうる範囲の閉じ込めとして現時点の最適解。

**(c) 遡及文法の無効化**
LLM が setext 見出しを意図することは実質ない(ATX `##` を使う)。markdown-it なら `md.disable('lheading')` の 1 行で文法ごと消せる(参照リンク定義も同様に切れる)。marked はルール単位の無効化ができず tokenizer 差し替えのハックになる — **ここが marked の「限界が近い」の正体**。速度差ではなく制御性の差。

**(d) 構造チャネル分離(content blocks)**
reasoning / tool_call / text を構造化ブロックとしてイベント・永続化・表示まで運び、**text part 同士を join しない**(1 part = 1 ブロック)。markdown パーサが見るのは「モデルが書いた散文 1 パート」だけになり、`</details>` 直後の `---` という発火条件が構造的に消滅する。#17 で決定済みの方向。

**(e) サーバーサイドレンダリング**
canonical なパースを 1 回で済ませられるが、chat UI では非推奨: KaTeX・mermaid・コピー・折りたたみ等どうせクライアントで hydrate が要る、sanitize は消えない、ストリーミングでは HTML の差分適用の方がかえって難しい。サーバーの役割は「正しいブロック分割と構造チャネルの維持」まで(これは filter・永続化のためにどうせやる)。ネイティブの高速レンダラ(comrak 等)は export・検索インデックス等「確定後の一括変換」に向く。

**(f) ハイライトのインクリメンタル化**
実測でストリーミング UI が重い主因はパースではなく「DOM 再構築 + 伸び続ける code block の全再ハイライト」。確定行のハイライト結果をキャッシュし追記行だけ処理する(hljs で可能。shiki は正確だが同期コストが高い)。

**パーサ速度の相場観**: markdown-it は marked の 2〜3 倍遅いと言われるが、chat 1 メッセージ(数 KB〜数十 KB)なら ms オーダーの差。(b) を採れば末尾ブロックしか再パースしないので、**パーサ単体の速度差はほぼ効かない**。「markdown-it は速度的に厳しい」は全文再パース戦略を前提にした場合の話。

---

## 3. llens の落とし所(採用方針)

層で分ける。上の層ほど先に効く。

| 層 | 内容 | issue |
|---|---|---|
| **L0 メッセージモデル** | content blocks を端から端まで。text part を join しない。details 擬似 HTML 廃止 | #17 |
| **L1 ブロック分割器** | 自前の追記単調ストリーミング・セグメンタ(行バッファ、保守的なブロック確定、表示用の fence 仮閉じ)。壊れうる範囲を「末尾の未確定ブロック 1 個」に閉じ込める。小さく独立、単体テストで固める | #14 |
| **L2 パーサ** | 確定ブロック単位で **markdown-it**(CommonMark 準拠 + `disable('lheading')` 等のルール制御)。速度は L1 がある限り問題にならない | #14 |
| **L3 ウィジェット** | code / tool 引数 / reasoning は growing string を受け取る専用コンポーネント。ハイライトは行キャッシュ | #17 #20 |
| サーバー | ブロック分割と構造チャネル維持まで。HTML は作らない | #17 |

**この設計で prod の 3 層パッチ(middleware join 修正 / md_setext_guard / プロンプト指示)は fork では構造的に不要になる。**

実装順: #17(L0)→ L1 セグメンタ(#14 の中核)→ #20(L3)。

### reasoning の閉じ規律(2026-07-06 決定 — think タグの閉じ問題)

reasoning ブロックの終了は**タグ対(`</think>`)や content 到着ではなく、
「次の非 reasoning シグナル」で確定する**: ① content デルタ ② tool_calls デルタ
③ ストリーム終端(finish / 切断 / エラー)。

- 根拠: Kimi は content を経ず tool_calls や切断に直行する
  (実キャプチャ: tool_call_no_args / aborted_client_cut)。v0.6.5 の
  「content 到着でのみ閉じる」実装では done=false の「Thinking…」が
  永続化され開きっぱなしになる。
- 現行 middleware に適用済み(`close_open_reasoning_block`、
  test_native_tool_calling.py で fixture リプレイ固定)。#17 L0 の
  content blocks 化でもこの規律を引き継ぐ(閉じはストリーム構造から導出)。
- 生 `<think>` タグ混り content(SGLang reasoning parser 無効)は接続契約外
  (_memos/model-frames.md)。タグ検出経路は #17 で廃止予定であり堅牢化しない。
  ただし上記③の終端 close はタグ経路の未閉じブロックにも効く。

### L0 の確定スキーマ(2026-07-06 決定 — #17 / #27 同時決定)

**永続化(#27)**: chat テーブルの JSON blob 続投。message オブジェクトに
`content_blocks` を追加する(正規化テーブルは #19/監査要件の具体化まで見送り)。
`content` は移行完了後「text ブロックの join」の派生値になる(会話検索・
タイトル生成・API 互換用)。~~旧メッセージ(details 擬似 HTML 埋め込み)は
`content_blocks` 不在 → フロントが旧パスで表示する読み取りフォールバック。~~
→ **改訂(2026-07-14): 読み取りフォールバックは撤去した**。fresh DB で
新規稼働するため旧データ互換を持たない(CLAUDE.md 絶対制約)。details
擬似 HTML の再パース(parseDetails / details トークン / removeDetails 系)は
フロントからも消滅。`content_blocks` 不在の content は素の markdown として
描画される(非 stream 応答・エラー表示用のパスであり互換機構ではない)。

**イベント**: `chat:completion` の data に `content_blocks`(累積スナップショット、
差分ではない)を追加。既存 `content` 文字列は移行期間中並走させ、フロント切替後に
text join へ切替。

**ブロック形(wire = 永続化と同形。消費者のいないフィールドは足さない)**:

```jsonc
{ "type": "text",      "content": "モデルが書いた散文 1 パートそのまま" }
{ "type": "reasoning", "content": "推論全文(markdown として解釈しない)",
  "done": true, "duration": 12 }          // done=false 中は duration 無し
{ "type": "tool_calls", "calls": [/* OpenAI tool_call 形 */],
  "results": [{ "tool_call_id": "...", "content": "...", "files": [] }] }
  // call 単位の done はフロントが results の有無から導出
{ "type": "code_interpreter", ... }        // 現行踏襲・薄く(自前ツール移行で消える面)
```

**middleware の扱い(#23 同梱判断 → 2026-07-14 実施済み)**: L0 時点では
物理分割しない判断だったが、L0 / #18 / #20 安定後に #23 として実施した。
現在の構成:

- `utils/response_assembler.py` — **assembler**(デルタ→content_blocks の
  状態機械 + reasoning の閉じ規律①②③)。I/O レスで単体テスト
  (`test_response_assembler.py`)で固定
- `utils/content_blocks.py` — wire / 永続化変換 + モデル向け履歴変換
  (`convert_content_blocks_to_messages`)。details 擬似 HTML を生成する
  コードは backend から消滅(旧 serialize_content_blocks は text join に
  縮退して吸収)
- `utils/skills.py` — skill 発火(`/command` + load_skill)一式
- `utils/middleware.py` — オーケストレーションのみ(inlet 準備 /
  `consume_model_stream`(SSE + filter + emit)/ tool 実行ループ / 永続化・
  タイトル生成)。code_interpreter・solution 等のタグ検出実行系は全撤去
  (コード実行は tool 一本 — tool 外実行の裏道を残さない)

## 4. 教訓(cage メモより)

- LLM ツールの戻り値として「素の markdown」を返すと、ホスト UI のレンダラの曖昧性解決に巻き込まれる。tool 出力・reasoning を本文平面に同居させる UI では、本文中の HR/見出し境界が UI を壊す地雷になる。
- `<details>` 内に空行を足すと CommonMark の HTML ブロックがそこで途切れて逆に壊れる — テキスト平面での修復はどこを触っても別の地雷がある。だからチャネル分離(L0)が根治で、テキスト修復(現 md_setext_guard)は保険にしかならない。
