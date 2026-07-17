# Tool 管理の skill 型一本化(#43 B 節の実装)

2026-07-07 起草。Tool の管理面(workspace/tools)を Skill と同じ「構造フィールドが唯一の真実源」
モデルに寄せる。frontmatter・community 連携(share/clone/export/import)・version 表示を全撤去し、
builtin は編集不可にする。**今回は tools のみ** — filters/functions(admin/functions、CORE)は
同じ変更を後続ステップで波及(回帰テスト同伴で分離)。

背景の全体像は #43。エンティティ横断の命名規約(name→title 統一など A 節)は本メモの範囲外。

## 現状の地図(2026-07-07 実測)

| エンティティ | メタの持ち方 | frontmatter(`"""..."""`)| share/clone/export | version 表示 |
|---|---|---|---|---|
| Skill | title/command/description/content/tool_ids に構造分離 | 無し | 全廃済み(#35、openwebui.com 連携撲滅) | 無し |
| **Tool** | フォーム `name`+`meta.description` **と** frontmatter の `meta.manifest` の**二重** | 有り(`extract_frontmatter`) | 有り(share は openwebui.com を開く) | `manifest.version` バッジ + ManifestModal + funding_url |
| Filter/Function | Tool と同じ二重 | 有り | 有り | 同上 |
| Builtin tool | repo 定義が真実源 | 無し | — | 無し |

※ Filter/Function 行は上記が着手前の状態。#45(0f74536a9)/ #46(ce1f12fc1)で Tool と同型に解消済み
(frontmatter/share/version 全撤去・type ドロップダウン新設・is_global/is_active 撤去)。詳細は §5 と「#46」節。

- Skill だけが理想形。Tool は旧 OWUI のまま二重定義が残る。
- frontmatter が運ぶ唯一の実機能は `requirements`(tool ロード時 `pip install`)。title/description/
  version/funding_url は表示専用。runtime pip は配備を問わず筋悪(閉域配備では成立すらしない)。
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
- **改訂(2026-07-07, #47 A1)**: 一覧体裁の整理で builtin の扱いを更新。
  - `TOOL`/`組み込み` バッジ表示 → **「組み込み」セクションで上部にまとめる**(バッジ廃止)。
  - **`access_control` を一覧の globe/lock アイコンから編集可能に**(「管理 UI は設けない」を撤回)。
    個別に開けない builtin でも visibility を設定できるようにするのが狙い。
    → routers/tools.py の builtin access_control 分岐(下記「UI 到達不能」)が**この経路で到達・常用される**。
- バックエンドの「access_control のみ更新」分岐(routers/tools.py:250)は UI から到達不能になるが無害で残置。
- バックエンドの現行ゲート(create/update/delete 拒否)は変更不要。フロントを実態に合わせるだけ。

### 5. 範囲外だった後続ステップ → 出荷済み(#45 / #46)

- **#45 出荷(0f74536a9)**: functions(admin/functions)への同一変更 = #44 の filter 版
  (UI 整理 + frontmatter 廃止)。回帰同伴(CORE = Filter パイプライン)。
  - **改訂(2026-07-07)**: 当初の「type ピッカーも足さない」方針を撤回し、**作成画面に
    filter/action/pipe の type ドロップダウンを新設**した(ユーザー決定)。type の真実源は
    コード中のクラス名(Filter/Action/Pipe)のままで、ドロップダウンは雛形の差し替えのみ
    (二重定義しない)。pipe/action は引き続き温存(DROP せず・実行コード残置)。
  - `meta.manifest` カラム掃除・`extract_frontmatter`/`install_frontmatter_requirements` の削除は
    #45 でまとめて実施(tools・functions 両方から frontmatter が消えたため)。`FunctionMeta`/`ToolMeta`
    の manifest フィールドも撤去。`utils/models.py` の pipe/action icon_url フォールバック(旧 manifest)も撤去。
- **#46 出荷(ce1f12fc1)**: function の適用制御を **per-model 割り当て一本化**。`is_global`
  (全モデル強制 + 解除禁止)と `is_active`(master switch)を DB カラムごと撤去
  (migration a1c3f9e2d7b4)。詳細は下記「#46: 制御面の一本化」。
- builtin access_control 分岐の除去(routers/tools.py:250)は UI 到達不能だが無害で残置のまま(未実施)。
  → **#47 A1(2026-07-07)で到達可能に**: 一覧の visibility アイコン編集が builtin にこの分岐を使う。除去せず**常用**へ。

## #46: 制御面の一本化(is_global / is_active 撤去、2026-07-07)

**決定の根拠**: filter/action の適用は 3 概念(is_global / per-model 割り当て / is_active)が
直交して二重化していた。llens はモデルラインナップが小さく(ユーザーが迷う)、per-model 割り当て
だけで十分と判断し global / active を撤去(ユーザー決定)。

**新モデル**: filter/action はモデルの `meta.filterIds` / `actionIds` 割り当てのみで適用。
pipe は「存在すればモデルとして出る」(active ゲート撤去)。admin/functions は 一覧/検索/作成/編集/削除
だけ(トグル類全廃、skill/tool と同型)。

- `utils/filter.py` `get_sorted_filter_ids` = モデルの filterIds を priority 昇順(global union /
  active 積集合を廃止。実在 filter type でのみ削除済み ID をガード)。
- `utils/models.py`: action は per-model actionIds のみ。`get_global_filter_functions` /
  `get_global_action_functions` / `deactivate_all_functions` 削除、`active_only` 引数撤去。
- `routers/functions.py`: `/toggle`・`/toggle/global` 削除。`plugin.py`: ロード失敗時 auto-disable 撤去
  (壊れた function は割り当て解除 or 修正で対処)。`main.py`: SAFE_MODE の一括無効化撤去。
- frontend: 有効トグル(Functions.svelte)・global メニュー(FunctionMenu.svelte)撤去。
  `FiltersSelector.svelte` は is_global の強制 checked+disabled を撤去し自由トグルに。
- **副作用として許容**: 壊れた filter の auto-disable が無くなる / SAFE_MODE で function を一括停止できない
  (割り当て解除で代替)。
- 回帰: `test_filter.py` の get_sorted_filter_ids と `test_openai_compat_chat.py` の inlet/stream/outlet
  e2e を per-model filterIds 経路に書き換え。backend 全 206 passed。
- 追記(2026-07-17、#71): no-op で残っていた `SAFE_MODE` env 自体も削除(print のみだった)。
  死に env 掃除(DOCKER / PIP_OPTIONS 系も同時)は `95c557f88`。

## #43: description を列へ昇格 / エンティティ命名一貫化(2026-07-07)

§1 の「真実源 = name/id/**meta.description**」は #43 Phase 1(9c3fcfa0c)で改訂:
- **Tool/Function の description を top-level `description` 列に昇格**(旧 meta.description)。
  ToolMeta は `builtin` 判別のみ、FunctionMeta は空になり撤去(function.meta 列も drop)。
  真実源は `id` / `name` / `description`(列)。migration `b2d4e8f1a9c3`。
- 全 workspace エンティティを **`{id, name, description}`** に統一(#43)。Skill は Phase 2a
  (b0a8284f2)で `command`→`id` / `title`→`name`(詳細 `_memos/skill-design.md`)。Model は
  name 済・description は meta.description 据え置き(wire 最小化)。
- 残(Phase 2b): 可読 id の編集(編集画面で id 変更 → 削除→再作成)を 4 エンティティ横断で追加。

## 回帰の当たり所
- 既存 tool(frontmatter 付きコードを貼った場合)を保存 → `manifest` が生えない / description 必須が効く。
- builtin 行に編集導線が出ない。skill の `/command` 発火・tool ロードが退行しない(#42 の E2E)。
- tool ロード経路の pytest を回す。CORE(Filter)には触れないので Filter 回帰は不要。

参照: `_memos/skill-design.md`(構造モデルの手本)、`_memos/builtin-tools.md`(#42 機構)、#43(親)、#16 F トラック。
