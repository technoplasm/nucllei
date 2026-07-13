# デザイン(色トークン / テーマ)— #34

UI 全体の色は **CSS 変数トークンに一元化**し、`src/tailwind.css` の1箇所(`:root` = light /
`html.dark` = dark)で調整する。個別コンポーネントに hex を散らさない。
Tailwind への登録は `tailwind.config.js` の `theme.extend.colors`。

## 色トークン一覧(真実源 = `src/tailwind.css`)

| トークン | 用途 | Tailwind クラス | light / dark(現行値) |
|---|---|---|---|
| `--surface` | メイン背景 | `bg-surface` | `#f6f6f6` / `#202020` |
| `--surface-sidebar` | サイドバー背景 | `bg-surface-sidebar` | `#f0f0f0` / `#1b1b1b` |
| `--surface-raised` | 入力欄など持ち上げ面 | `bg-surface-raised` | `#ffffff` / `#262626` |
| `--sidebar-hover` | サイドバー行 hover | `bg-sidebar-hover` | `#e9e9e9` / `#212121` |
| `--sidebar-selected` | サイドバー行 選択中/active | `bg-sidebar-selected` | `#e3e3e3` / `#262626` |
| `--line` | 罫線・区切り線(table/hr) | `border-line` | `#d3d3d3` / `#404040` |

- 変数がテーマで切り替わるので利用側に `dark:` 変種は不要。

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
  ボタンは アクティブ=既定文字色(pill なし)/ 非アクティブ=`text-gray-300 dark:text-gray-600`、
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
- モデル画像は狭いほど小さく(`size-40 sm:size-52 md:size-60`。旧 `size-72 md:size-60` は狭い方が大きい逆転だった)。
- モデル編集の名前/ID は全タブ共通ヘッダとしてコンテンツ内に置く。

## テーマ

- 選択肢は **System / Dark / Light の3つのみ**(`oled-dark` と `her` = 映画 Her イースターエッグ
  は撤去)。適用ロジックは `src/lib/utils/theme.ts`、起動時スクリプトは `src/app.html`。
- gray パレット(`--color-gray-*`)は dark 時に `theme.ts` が **800/850/900/950 のみ**を
  インライン設定(中立: #333/#262626/#171717/#0d0d0d)。**700 以下は未設定**。

## ⚠️ Tailwind v4 の gray が青寄り(ハマりどころ)

v4 の既定 `gray` パレットは **クール(青寄り)の oklch**(例 `gray-700` =
`oklch(0.373 0.034 259.733)`)。本プロジェクトは config で `gray.N = var(--color-gray-N, #hex)`
と中立 hex をフォールバック指定しているが、**v4 が同名 `--color-gray-N` を既定で先に定義**するため
フォールバックが効かず v4 のクール値が出る。

- **`gray-850` だけ無事**だったのは v4 のパレットに 850 が無く、フォールバック #262626 が効くから。
- `theme.ts` が dark で 800/900/950 を上書きするので、それらは dark では中立。**light は全滅・
  50〜700 は全モードでクール**。
- さらに **Tailwind Typography(`prose`)が `tbody tr` に `--tw-prose-td-borders`(= 青い gray-700)
  を注入**するため、`border-gray-*` を当てても勝てない(`prose` の方が勝つ)。

対処(現行): 罫線は **`--line` トークン + `border-line!`**(`!` = important、`px-3!` と同流儀)で
prose の注入色を打ち消す。根本対処(未実施・B案)は `:root` で `--color-gray-50〜950` の中立
フルパレットを定義して v4 既定を潰すこと。`bg-gray-100` 等の微妙な青みが問題化したら実施する。

## ブランド / アイコン

- スプラッシュ & ログインロゴは dark/light とも**カラーの `splash.png`**(gray 版
  `splash-dark.png`/`favicon-dark.png` への差し替えは撤去)。
- モデルの既定アイコンは **`profile_image_url = null`** とし、表示側で gray へ fallback
  (`DEFAULT_MODEL_IMAGE_URL = /static/favicon-dark.png`、`src/lib/constants.ts`)。
  backend `ModelMeta.profile_image_url` の既定も `None`(焼き込まない)。
