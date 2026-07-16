# llens 既定値プロファイル(upstream v0.6.5 からの逸脱一覧)

llens として upstream v0.6.5 の既定値から変更した設定の一覧。
**環境変数を何も設定しない fresh 環境でこの表の値になる**ことを回帰テストで固定する
(`test_llens_defaults.py`、`test_rag_defaults.py`)。

env 未設定の項目は既存 DB の保存値(RuntimeConfig、旧 PersistentConfig — `config-model.md`)が
優先されるため、この変更は新規構築環境にのみ効く。既存環境は管理画面または env で合わせること。

| 設定 | upstream 既定 | llens 既定 | 根拠 issue |
|---|---|---|---|
| `BYPASS_EMBEDDING_AND_RETRIEVAL` | False | **config ごと撤去(#52 — RAG/埋め込み検索は fork に config 自体が無い。文書は prism 層 Attachment が抽出、fork は markitdown 同梱のみ)** | #9/#52 |
| `ENABLE_RETRIEVAL_QUERY_GENERATION` | True | **config ごと撤去(#52)** | #9/#52 |
| `ENABLE_CODE_EXECUTION` / `CODE_EXECUTION_ENGINE` | True / pyodide | **config ごと撤去(#67 — 実行系は nsjail 外部サンドボックスの tool 経路一本)** | #11/#67 |
| `ENABLE_CODE_INTERPRETER` / `CODE_INTERPRETER_ENGINE` | True / pyodide | **config ごと撤去(#23 — タグ検出による tool 外実行の撲滅)** | #11/#23 |
| `ENABLE_EVALUATION_ARENA_MODELS` | True | **config ごと撲滅(#48)** | #10/#48 |
| `ENABLE_MESSAGE_RATING` | True | **config ごと撲滅(#48)** | #48 |
| `ENABLE_COMMUNITY_SHARING` | True | **config ごと撲滅(#10 — openwebui.com 導線を断つ)** | #10 |

フロント側の既定変更(env ではなくユーザー設定 `$settings` の未設定時フォールバック):

- ~~`showChangelog`(What's New 自動ポップアップ): upstream `?? true` → llens `?? false`~~
  → **9755e960a(2026-07-16、#10 #70)で What's New/ChangelogModal ごと全撤去**。設定項目自体が無い。

## 機能削除(既定無効化でなく削除したもの)

- **upstream バージョン更新チェック**(github.com への外部フェッチ + 「新版が出ています」トースト): 外部ネットワーク必須機能を持たない方針(fork.md §6)に反するため機能ごと削除(#10)。
  - 当初(#10)は `/api/version/updates` を互換スタブとして残し手動ボタンも残置していたが、
    **9755e960a(2026-07-16、#10 #70)で根こそぎ撤去**: BE 端点(/api/changelog / /api/version/updates)、
    FE api(getChangelog / getVersionUpdates)、ChangelogModal、Interface の設定 2 行、CHANGELOG.md 本体まで全削除。
    fork の変更履歴は git log が真実源。

変更していない(upstream 既定のまま llens 要件に合致):

- ~~`ENABLE_WEB_SEARCH=False`~~(config ごと撤去 2026-07-13、#51 — 検索は prism tool 直叩き)、`ENABLE_LDAP=False`、`ENABLE_OAUTH_SIGNUP=False`
- 認証まわりは `_memos/auth-profile.md` 参照(signup 初回後自動 OFF 等)
- TTS/STT は `_memos/audio.md`・`_memos/dev-environment.md`・issue #12 参照

方針: DROP 領域は「まず既定無効化」。コード削除は運用安定後に個別判断
(例外: arena は削除まで可 — issue #10)。
※ 現状注記(2026-07-17): 実際には #23/#48/#51/#52/#67 で大半が「既定無効化」を越えて
config・コードごと撤去済み(上表参照)。「まず既定無効化」は新規 DROP 判断時の初手方針として残す。
