# Tool 管理の skill 型一本化(#43 B 節の実装)

2026-07-07 起草。Tool の管理面(workspace/tools)を Skill と同じ「構造フィールドが唯一の真実源」
モデルに寄せる。frontmatter・community 連携(share/clone/export/import)・version 表示を全撤去し、
builtin は編集不可にする。**今回は tools のみ** — filters/functions(admin/functions、CORE)は
同じ変更を後続ステップで波及(回帰テスト同伴で分離)。

背景の全体像は #43。エンティティ横断の命名規約(name→title 統一など A 節)は本メモの範囲外。

## 現状の地図(2026-07-07 実測)

| エンティティ | メタの持ち方 | frontmatter(`"""..."""`)| share/clone/export | version 表示 |
|---|---|---|---|---|
| Skill | title/command/description/content/tool_ids に構造分離 | 無し | 全廃済み(#35、air-gapped) | 無し |
| **Tool** | フォーム `name`+`meta.description` **と** frontmatter の `meta.manifest` の**二重** | 有り(`extract_frontmatter`) | 有り(share は openwebui.com を開く) | `manifest.version` バッジ + ManifestModal + funding_url |
| Filter/Function | Tool と同じ二重 | 有り | 有り | 同上 |
| Builtin tool | repo 定義が真実源 | 無し | — | 無し |

- Skill だけが理想形。Tool は旧 OWUI のまま二重定義が残る。
- frontmatter が運ぶ唯一の実機能は `requirements`(tool ロード時 `pip install`)。title/description/
  version/funding_url は表示専用。air-gapped では runtime pip 自体が筋悪。
- builtin はバックエンドが content 書換を拒否済み(create=id 予約 / update=access_control のみ /
  delete 拒否)だが、**フロントは編集リンク/メニューをそのまま出している**不整合(Tools.svelte:212)。

## 着地仕様(ロック、2026-07-07)

### 1. frontmatter 完全廃止(tools の真実源から排除 + runtime pip 廃止)
- `utils/plugin.py` `load_tool_module_by_id` の **tool 経路のみ** `extract_frontmatter` /
  `install_frontmatter_requirements` 呼び出しを除去。tool ロード時 `pip install` を廃止(依存は事前導入前提)。
- `routers/tools.py` の `form_data.meta.manifest = frontmatter`(create/update)を削除。
- **共有ヘルパ `extract_frontmatter` / `install_frontmatter_requirements` は残す** —
  `load_function_module_by_id` が使うため(filters 後続まで温存)。tool 経路から呼ばないだけ。
- `meta.manifest` は tools では以後空。DB カラム(models/tools.py)は移行回避で残置(未使用化のみ)。
- **真実源** = `name` / `id` / `meta.description`。**title・description を必須化**(空なら保存失敗)。
  フロントは既に `required`、**バックエンドにも空拒否バリデーションを追加**。

### 2. version 管理・community メタの表示撤去(Tools.svelte)
- `manifest.version` バッジ / `funding_url` / `ManifestModal` を除去。frontmatter 廃止で供給源が消える。

### 3. share / clone / export / import 全廃(skill と同型)
- Tools.svelte: `shareHandler`(openwebui.com 連携)/ `cloneHandler` / `exportHandler` と
  一覧ページの Import/Export ボタンを除去。
- Tools/ToolMenu.svelte: Share / Clone / Export の項目と props を除去。
- ToolkitEditor.svelte: `clone` prop と clone 経路を撤去。
- 残す機能 = **一覧 / 検索 / 作成 / 編集 / 削除**のみ(Skills.svelte:17 の前例コメントと同文言で意図を残す)。

### 4. builtin tool は編集導線を完全除去
- Tools.svelte: `meta.builtin` 行は編集リンク(href)と編集/複製/共有/エクスポート/削除メニューを出さない。
  一覧では**バッジ表示のみ**(クリック不可の素の行)。
- builtin の `access_control` はレジストリ既定で固定(管理 UI は設けない)。
- バックエンドの「access_control のみ更新」分岐(routers/tools.py:250)は UI から到達不能になるが無害で残置。
- バックエンドの現行ゲート(create/update/delete 拒否)は変更不要。フロントを実態に合わせるだけ。

### 5. 範囲外(後続ステップ)
- filters / functions(admin/functions)への同一変更 = **#45 に分離**(CORE = Filter パイプライン、回帰同伴)。
  内容は #44 の filter 版(UI 整理 + frontmatter 廃止)。**pipe/action は放置**(DROP せず・type ピッカーも
  足さず・実行コードも消さない。将来 action を使う可能性があり戻すのが面倒。2026-07-07 決定)。
- `meta.manifest` カラム掃除・`extract_frontmatter`/`install_frontmatter_requirements` の削除
  = frontmatter が filters(#45)からも消えた後にまとめて(tools 側は既に未使用)。builtin access_control
  分岐の除去も同様に後続。

## 回帰の当たり所
- 既存 tool(frontmatter 付きコードを貼った場合)を保存 → `manifest` が生えない / description 必須が効く。
- builtin 行に編集導線が出ない。skill の `/command` 発火・tool ロードが退行しない(#42 の E2E)。
- tool ロード経路の pytest を回す。CORE(Filter)には触れないので Filter 回帰は不要。

参照: `_memos/skill-design.md`(構造モデルの手本)、`_memos/builtin-tools.md`(#42 機構)、#43(親)、#16 F トラック。
