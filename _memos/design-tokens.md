# デザイン(色トークン / テーマ)— #34

UI 全体の色は **CSS 変数トークンに一元化**し、`src/tailwind.css` の1箇所(`:root` = light /
`html.dark` = dark)で調整する。個別コンポーネントに hex を散らさない。
Tailwind への登録は `tailwind.config.js` の `theme.extend.colors`。

## 色トークン一覧(真実源 = `src/tailwind.css`)

値は調整中につきドリフトする — 正確な現行値は常に `src/tailwind.css` を見る(下表は 2026-07-14 時点)。

| トークン | 用途 | Tailwind クラス | light / dark |
|---|---|---|---|
| `--surface` | メイン背景 | `bg-surface` | `#f7f7f7` / `#1a1a1a` |
| `--surface-sidebar` | サイドバー背景 | `bg-surface-sidebar` | `#eeeeee` / `#212121` |
| `--surface-raised` | 入力欄・カードなど持ち上げ面 | `bg-surface-raised` | `#ffffff` / `#262626` |
| `--surface-muted` | 控えめな帯(table thead 等) | `bg-surface-muted` | `#f0f0f0` / `#2d2d2d` |
| `--surface-hover` | raised 面の hover | `hover:bg-surface-hover` | `#f0f0f0` / `#161616` |
| `--primary(-hover/-content)` | 主ボタン(Save/Create) | `bg-primary hover:bg-primary-hover text-primary-content` | 黒地白字 / 白地黒字(#58 で差し替え予定) |
| `--secondary(-hover/-content)` | 副ボタン(戻る/キャンセル) | `bg-secondary hover:bg-secondary-hover text-secondary-content` | `#e9e9e9` / `#333333` 地 |
| `--sidebar-hover` | サイドバー行 hover | `bg-sidebar-hover` | `#e7e7e7` / `#292929` |
| `--sidebar-selected` | サイドバー行 選択中/active | `bg-sidebar-selected` | `#dddddd` / `#313131` |
| `--line` | 罫線・区切り線(table/hr) | `border-line` | `#d3d3d3` / `#404040` |
| `--content` | 本文(既定テキスト色) | `text-content` | `#333333` / `#ececec` |
| `--content-muted` | 2次情報・ラベル・キャプション | `text-content-muted` | `#909090` / `#b4b4b4` |
| `--content-subtle` | placeholder・disabled・3次 | `text-content-subtle` | `#a0a0a0` / `#9b9b9b` |

### ボタン/コントロールの使い分け(2026-07-14 確立、users/skills/tools/functions に適用済み)

- **コントロール(input/select/textarea)= `bg-surface-raised` + `rounded-xs` + `text-sm`**
  (丸みの使い分け・2026-07-15 改訂: **入力するもの(input/select/textarea)= `rounded-xs`** /
  **そのまま押せるもの(ボタン・タブ)= `rounded-lg`** / pill(Create 等)= `rounded-full`)
  (チャット入力欄と同じ「surface 上の持ち上げ面」)。ただし **raised は surface の直上でだけ** —
  raised 相当の面(モーダル = bg-white dark:bg-gray-900 等)の中のコントロールは
  `bg-surface-muted` を使う 2 段ルール。
- **主ボタン = primary(Create/Save)/ 副ボタン(戻る/キャンセル/Access)= secondary**。
  ボタンに raised を使わない(入力面と顔が被る)。
- **Checkbox** はトークン化済み: 箱 = `bg-surface-raised` + `outline-line`(透明だと面と同化)、
  checked = primary。名前クリックで toggle させる場合は素の `<label>` で包む(button は labelable)。
- **CodeEditor** の light テーマは自前 `EditorView.theme`(本体 = raised / gutter = muted。
  公式テーマは one-dark のみで、light 未指定は透明背景になる)。
- エディタ系ページの全高: workspace layout の `.max-w-5xl` が `h-full` を持つので、
  ページ側は「form 一段の flex-col h-full + エディタ枠 `flex-1 min-h-0`」で貼る(col 連打・`h-0` hack は廃止)。
- フォームのラベルは固定幅(`w-24 shrink-0`)の `<label for>` でコントロール(`id` 付き)と同一行
  (縦を稼ぐ + クリックでフォーカス。真実源 = FunctionEditor)。
- **一覧ページの様式**(真実源 = models/+page.svelte、2026-07-15 改訂): ヘッダ `mt-1.5`(+ ボタン行 `mt-2`)+
  primary Create pill + raised 検索ボックス(`rounded-xs`)/ grid は `mt-6 gap-3` / カード =
  `relative bg-surface-raised rounded-xl` + **カード全体がクリック=編集**(stretched-link:
  `a` に `after:absolute after:inset-0`、右のボタン/Switch は `relative` でオーバーレイの上に載せる)。
  hover はカード全体だが、**内側 button の hover 中は `[&:hover:not(:has(button:hover))]:bg-surface-hover`
  で親の hover を抑制**(hover 面とクリック先が常に一致)/ セクション見出し `text-md text-content-muted mt-3 mb-1`(先頭は mt-6 相当)。
- **テーブル**(users/chats): thead = `bg-surface-muted`、tbody 行 = `bg-surface-raised`。
  共通コンポーネント化は 3 例目が出るまで見送り。

- 変数がテーマで切り替わるので利用側に `dark:` 変種は不要。
- テキストトークンの実値は **raw hex**(`--surface`/`--line` と同流儀で `:root`/`html.dark` に直書き)。
  ※ `var(--color-gray-*)` は `@config` 構成では `:root` に出力されず**空**になる罠があるので使わない
  (使うと dark で `text-content` が fallback `#333` に落ち、暗背景に暗文字で不可視になる)。

### テキスト色トークンの移行ルール(2026-07-14 追加・gray ハードコード抽象化)

`text-gray-*` は 11 shade がバラバラに散在(実測: 300×244 / 500×225 / 700×115 / 600×111 /
400×100 / 100×82 …、text 系だけで ~1000 箇所 + `dark:text-gray-*` ~455)。**shade は役割と
1:1 対応していない**(同じキャプションが 400 だったり 500 だったり)ので、**shade 単位の一括置換は
不整合をトークンに固めるだけ**。役割(role)を文脈から判断して置換する:

| 役割 | 判定の目安 | → トークン |
|---|---|---|
| 本文・見出し・ボタン文言・active な選択肢 | 既定の可読テキスト。light 700–900 / dark 100–300 が多い | `text-content` |
| ラベル・キャプション・補助説明・2次メタ | `text-xs`/`text-sm` の説明文、フォームのラベル、控えめな注記。light 500–600 / dark 400 | `text-content-muted` |
| placeholder・disabled・空状態・最も控えめ | 未選択 select、プレースホルダ、`opacity` 相当の淡いテキスト。light 400 / dark 500–600 | `text-content-subtle` |

- **明暗ペアは1トークンに畳む**: `text-gray-700 dark:text-gray-300` → `text-content`(dark: 変種を消す)。
- **`dark:` 欠落は潜在バグ**: `text-gray-700` 単独(dark 指定なし)は dark モードで暗背景に暗文字。
  トークン化で自動的に解消する(移行の副次効果)。
- **対象は text 前景のみ**。`bg-gray-*`/`border-gray-*` は surface/line/sidebar 軸(別トークン)なので触らない。
- 迷ったら **muted に寄せる**(subtle は明確に placeholder/disabled/空状態のときだけ)。

## レイアウトトークン(2026-07-12 追加)

| トークン | 用途 | 現行値 |
|---|---|---|
| `--sidebar-width` | サイドバー実幅。Sidebar 本体と、その幅を差し引く各レイアウト(workspace/home/playground・Chat.svelte・FilesOverlay)の両方が参照 | `300px` |

- 経緯: upstream v0.6.5 は Sidebar 実幅 300px に対し各レイアウトが `calc(100%-260px)` しか
  確保しておらず、root layout の `justify-end` で溢れた 40px 分**サイドバー左端が画面外に
  切れていた**(幅 ~1300px 以下で発現)。#19 セッションで変数に一元化して解消(8492ac032)。
- 値の食い違いはこのバグの再発になるので、px 直書きを増やさず必ずこの変数を参照する。
- サイドバー行の active と selected は同一トークン(`sidebar-selected`)に統一。
- ヘッダー(chat/layout/channel Navbar)のグラデ背景は `from-surface`(dark が背景より
  暗すぎる問題を解消)。チャット入力欄は `bg-surface-raised` + 柔らかい影(`shadow-lg` 廃止)。

### 設定系画面の共通レイアウト(2026-07-13 / 改訂 2026-07-14)

管理設定(`admin/Settings.svelte`)・モデル編集(`ModelEditor.svelte`)・ワークスペース各ページを
**同じ様式に統一**(ワイド画面で全幅に間延びさせない):

- **横幅は workspace layout(`routes/(app)/workspace/+layout.svelte`)で一元管理**。
  改訂 2026-07-14(cc76b1d91): 各画面が個別に `max-w-3xl` を持つと画面ごとに幅がばらつくため、
  `#workspace-container` 内の `max-w-5xl mx-auto w-full` ラッパに集約。個々のページ/エディタは
  自前 max-w を持たず継承する(4a71ddc94/dbb2556d3 で Settings/ModelEditor の自前 max-w 撤去)。
  上部メニューも同じ 5xl に揃える。値は 3xl(768)→ **5xl(1024)**(submenu 分を足した)。
- **左サブメニュー**(`lg` 以上で `lg:flex-col lg:w-40` の縦タブ、未満は横スクロール行)+ 右コンテンツ `flex-1`。
  ボタンは アクティブ=既定文字色(pill なし)/ 非アクティブ=`text-content-subtle`(hover で `text-content`)、
  先頭に class アイコン(`i-lucide-*`)。真実源は `admin/Settings.svelte` のタブ。
  - 少数タブ(モデル編集は 3 個)は `flex-1` だと狭幅で全幅に間延びするため **`flex-none`**(左詰め)に
    (dbb2556d3)。settings は `flex-1 md:flex-none`。
- **上部メニューは宣言配列 `WORKSPACE_TABS`(label/path/show)で定義**し、マークアップは {#each} 一箇所(cc76b1d91)。
- **高度なパラメータ(AdvancedParams)**: 改訂 2026-07-14(7e16e5cdd)。旧「カラム化却下・justify-between 保留」を
  **撤回し採用**。全項目を宣言配列化しマークアップ一箇所、grid 自動カラム(`minmax(260px,1fr)` —
  max を固定 px にすると auto-fill が本数を数え違えて 1col に潰れる)、キー-左/値-右 `justify-between`、
  ラベルは `whitespace-nowrap` で折り返さず slider が残り幅を吸収。ラベルは i18n せず実キー名を表示。
  値変更デグレ(Svelte legacy の untrack)の罠は `dev-environment.md` 参照。
- **設定タブの整理**(4a71ddc94): ツールサーバー設定は独立タブをやめ General に完全統合(保存共通)。
  Code Interpreter タブは撤去(nsjail+IPython 固定で無意味 — #11)。
- **モデル編集の General レイアウト**(改訂 2026-07-15、#70 Models バッチ): 旧「名前/ID = 全タブ共通の
  素のヘッダ input(3xl)+ 画像を左に size-40〜60」を撤回。名前/ID/Base Model はラベル付き raised input の
  左カラム、**モデル画像は右端に行ブロックの高さへ合わせた正方形**(`max-h-40` キャップ)。画像列は
  `absolute inset-y-0 right-0`(行高に寄与させない)+ 左カラム `mr-44` で幅を予約 — flex だけだと
  aspect-square の幅算出が循環して 0 幅列に画像が被る。Description/ACL はその下に全幅。

## テーマ

- **`color-scheme` を :root(light)/ html.dark(dark)で宣言している**(2026-07-15、`src/tailwind.css`)。
  未宣言だとネイティブウィジェット(select のポップアップ・スクロールバー・date picker)が dark でも
  light 描画になり、透明背景の select が「light ポップアップ + 白文字」で破綻する
  (不透明 dark 背景の select だけ Chromium のヒューリスティックで偶然暗転する罠)。
- 選択肢は **System / Dark / Light の3つのみ**(`oled-dark` と `her` = 映画 Her イースターエッグ
  は撤去)。適用ロジックは `src/lib/utils/theme.ts`、起動時スクリプトは `src/app.html`。
- gray パレット(`--color-gray-*`)は dark 時に `theme.ts` が **800/850/900/950 のみ**を
  インライン設定(中立: #333/#262626/#171717/#0d0d0d)。**700 以下は未設定**。

## ⚠️ Tailwind v4 の gray が青寄り(ハマりどころ)

> **改訂(2026-07-14):** 罫線問題の**根因は「v4 gray が青い」ことではなく、md(prose)マークアップの
> セレクタ強さ(specificity)制御**だった(下の prose 注入の項)。青みは実在するが**別件の純粋な見た目**の話で、
> **仮に gray を完全中立化しても `border-gray-*` は prose に勝てない**(= 後述 B案は罫線問題を解かない)。
> `border-line!` の `!` は「青対策」ではなく **specificity で prose に勝つため**。→ 生産的な方向は
> **ボーダー色のトークン化**(`--line` 系の拡張・prose 領域は specificity 制御込み)であって、パレット中立化ではない。

v4 の既定 `gray` パレットは **クール(青寄り)の oklch**(例 `gray-700` =
`oklch(0.373 0.034 259.733)`)。本プロジェクトは config で `gray.N = var(--color-gray-N, #hex)`
と中立 hex をフォールバック指定しているが、**v4 が同名 `--color-gray-N` を既定で先に定義**するため
フォールバックが効かず v4 のクール値が出る。

- **`gray-850` だけ無事**だったのは v4 のパレットに 850 が無く、フォールバック #262626 が効くから。
- `theme.ts` が dark で 800/900/950 を上書きするので、それらは dark では中立。**light は全滅・
  50〜700 は全モードでクール**。
- さらに **Tailwind Typography(`prose`)が `tbody tr` に `--tw-prose-td-borders`(= 青い gray-700)
  を注入**するため、`border-gray-*` を当てても勝てない(`prose` の方が勝つ)。

対処(現行・本対処): 罫線は **`--line` トークン + `border-line!`**(`!` = important、`px-3!` と同流儀)で
prose の注入色を **specificity で打ち消す**。これが罫線問題の本質的対処。
**B案(`:root` で `--color-gray-50〜950` の中立フルパレット定義)は罫線問題には無関係** — prose の勝ちは
specificity なので中立化では解けない。B案は `bg-gray-100` 等の**微妙な青み=純粋な見た目**が気になった
場合の任意対応にすぎず、優先度は低い。生産的な次の一手は**ボーダー色のトークン化**(`border-gray-*` を `--line` 系トークンへ、text 移行と同型・次セッション候補)。

## ブランド / アイコン

- スプラッシュ & ログインロゴは dark/light とも**カラーの `splash.png`**(gray 版
  `splash-dark.png`/`favicon-dark.png` への差し替えは撤去)。
- モデルの既定アイコンは **`profile_image_url = null`** とし、表示側で gray へ fallback
  (`DEFAULT_MODEL_IMAGE_URL = /static/favicon-dark.png`、`src/lib/constants.ts`)。
  backend `ModelMeta.profile_image_url` の既定も `None`(焼き込まない)。
