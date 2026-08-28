# nucllei — Open WebUI fork(基点 v0.6.5 固定)

北大病院オンプレ向け「薄い LLM チャット基盤」。nucllei 独自の文書はすべて `_memos/` にある:
機能要件の一次根拠は `_memos/fork.md`(CORE / KEEP-MINIMAL / DROP / CONSTRAINT の 4 区分)、
機能別の維持/削除判断は `_memos/feature-triage.md`、upstream の扱いは `_memos/upstream-policy.md`。
実装判断で迷ったらまず fork.md に照らす。

## 絶対制約

- **upstream v0.6.6 以降のソースコードを読まない・写さない**(タグ間 diff の実装内容の閲覧も含む)。
  参照してよいのはリリースノート・公式ドキュメント・API 外形仕様のみ。詳細は `_memos/upstream-policy.md`。
- upstream の無条件マージ禁止。取り込みはファイル/コミット単位、`_memos/upstream-policy.md` に由来を記録。
- コード実行は nsjail + IPython 外部サンドボックス(Jupyter 互換接続)。Pyodide には戻さない
  (Pyodide 実行系はコード・依存・ビルドステップごと削除済み — issue #11)。
  **tool 経路以外のコード実行(タグ検出等の裏道)は作らない**(code_interpreter は #23 で機能ごと撤去済み)。
- 配備先は一様でない(2026-07-17 改訂、旧「air-gapped 前提」): 基本はオンプレだが、air-gapped の閉域もあれば
  外部ネットワークに出られる環境もある。制約は「外部ネットワークを必須にしない(オフライン配備でも完結する)」の
  一点のみ — **閉域・オンプレを理由にオプショナルな外部連携機能を切らない**。
- **旧データ互換を持たない**(2026-07-14 決定): 本 fork はまっさらな DB で新規稼働する。
  旧フォーマット(details 擬似 HTML 埋め込みメッセージ、`content_blocks` 不在メッセージの
  旧パス表示等)の読み取りフォールバック・移行コードは**書かない・残さない**。
  互換のためだけのコードは「わかっているゴミを知らずに残す」ことになる — 見つけたら削除する。

## ワークフロー

- ブランチモデル(2026-07-05 に単一ブランチへ再編): **`master`** が唯一の開発ヘッドかつ出荷ブランチ(日常の作業はここに積む)。
  リリースは将来 **tag** で切る運用(出荷ブランチを分けない — 2 つの head で混乱するのを避ける)。
  **`main`** は fork 基点(upstream v0.6.5 分岐点)の凍結ブランチ — コミットを積まない。v0.6.5 タグはローカルに無いので基点参照は `origin/main` を使う。
  ※ 旧 `develop` は master に統合して廃止済み。
- ロードマップは GitHub issue #16(tracking)。作業は issue 単位で進め、コミットメッセージに `refs #N` を入れる。
- セッション開始時は `/catchup` で現在地(前回の区切り・環境ヘルス・次の一手)を確認し、
  区切りでは `/sync-docs` でドキュメントと issue に同期する。upstream 確認は `/upstream-check`(週次目安)。
- CORE 領域(Filter パイプライン / group 権限 / OpenAI 互換接続 / 認証)に触れる変更は回帰テストを伴うこと。

## フロントエンド実装規約

- **アイコンは class ベース(`i-lucide-*`)で書く。インライン `<svg>` は禁止**
  (#70 A1 で確立、`tailwind.config.js` の `@egoist/tailwindcss-icons` + lucide)。
  例: `<span class="i-lucide-settings size-4 block"></span>`。CSS mask で currentColor を継承する。
  既存のインライン SVG に触れるときは class icon へ置換する(新規は最初から class icon)。
- 既存のインライン SVG が残る画面もある(A1 は段階移行)。**新規・改修分だけでも必ず class icon** にする。
- **コンポーネントイベントは callback prop(`onXxx`)で渡す。`createEventDispatcher` は新規使用禁止**
  (#70 A2-2 で確立、`common/` は 2026-07-16 に全廃)。named slot も新規は snippet prop
  (`{#snippet content()}` + `{@render content?.()}`)で。`$:` 由来の通知はマウント時初期発火に注意
  (`common/Switch.svelte` の lastState ガードパターンを踏襲)。
  残る dispatcher(chat メッセージ樹・サイドバー等 25 ファイル)は C トラック(分割・runes 化)で消化。

## 現行プロジェクトの参照

- `_links/llens` → prism-hu/llens(北大で稼働中の現行 OWUI ベース構成。マシン固有 symlink のため gitignore 済み)。
  現行の Filter 実装・nsjail sandbox(stateful run_python)・TTS 構成・SGLang 設定は適宜ここを参照する。
  リンクが無い環境では `~/src/github.com/prism-hu/llens` を確認。

## tmux 規約(technoplasm 共通様式、2026-08-28 適用)

- 作業は tmux session **`nuc`** 上で行う。windows: **`BE`**(backend サーバー)/ **`FE`**(vite サーバー)/
  `claude` など。ken が手作業するときも同じ。
- 開発サーバーは**必ず BE/FE window で起動**し、**Claude Code 内部の shell では起動しない**
  (foreground/background とも。ken がログを見られないため): BE は `uv run task dev`(:3000)、
  FE は `pnpm dev`(vite :5173)。いずれも repo root で実行。
- **作業開始時にまず tmux を確認**: `tmux list-windows -t nuc -F '#I: #W (#{pane_current_command})'`。
  session/window が無ければ作って名前を付け、サーバーが死んでいれば send-keys で起動する:
  - `tmux send-keys -t nuc:BE 'uv run task dev' Enter`
  - `tmux send-keys -t nuc:FE 'pnpm dev' Enter`
  - 罠: `C-c` 後に履歴 `Up` で再実行しない(別コマンドを拾いうる)。コマンドは必ず明示で送る。
- サーバーログは `tmux capture-pane -p -t nuc:BE`(/FE)で読む。500 の一次調査は BE の traceback から。
- .env 反映・二重起動・reload の固まり等の詳細は `_memos/dev-environment.md`「dev サーバ」節。

## 環境の前提

- 推論エンジンは **SGLang**(`prism-llens:13300`、OpenAI 互換 — 2026-07-13 に `:8000` から変更)。接続モデルは Kimi K2.6 で reasoning 出力あり(`reasoning_content` / `<think>` タグ)。
- バックエンドテストは `nucllei/test/` 配下(pytest)。
- **開発環境のセットアップ(dev サーバ・dev アカウント・dev ツール・テスト/Playwright の罠)は
  `_memos/dev-environment.md`** — 新しい環境で開発を始めるときはまずここを読む。
