# llens 既定値プロファイル(upstream v0.6.5 からの逸脱一覧)

llens として upstream v0.6.5 の既定値から変更した設定の一覧。
**環境変数を何も設定しない fresh 環境でこの表の値になる**ことを回帰テストで固定する
(`test_documents_docling.py::test_llens_rag_defaults`、`test_llens_defaults.py`)。

既存 DB には PersistentConfig の永続値が優先されるため、この変更は
新規構築環境にのみ効く。既存環境は管理画面または env で合わせること。

| 設定 | upstream 既定 | llens 既定 | 根拠 issue |
|---|---|---|---|
| `BYPASS_EMBEDDING_AND_RETRIEVAL` | False | **True**(添付は全文注入) | #9 |
| `ENABLE_RETRIEVAL_QUERY_GENERATION` | True | **False** | #9 |
| `ENABLE_CODE_EXECUTION` | True | **False** | #11 |
| `CODE_EXECUTION_ENGINE` | pyodide | **jupyter**(有効化時も Pyodide に戻さない) | #11 |
| `ENABLE_CODE_INTERPRETER` | True | **False** | #11 |
| `CODE_INTERPRETER_ENGINE` | pyodide | **jupyter** | #11 |
| `ENABLE_EVALUATION_ARENA_MODELS` | True | **False** | #10 |
| `ENABLE_COMMUNITY_SHARING` | True | **False**(openwebui.com 導線を断つ) | #10 |

変更していない(upstream 既定のまま llens 要件に合致):

- `ENABLE_WEB_SEARCH=False`、`ENABLE_LDAP=False`、`ENABLE_OAUTH_SIGNUP=False`
- 認証まわりは `_memos/auth-profile.md` 参照(signup 初回後自動 OFF 等)
- TTS/STT は `_memos/dev-env.md`・issue #12 参照

方針: DROP 領域は「まず既定無効化」。コード削除は運用安定後に個別判断
(例外: arena は削除まで可 — issue #10)。
