# llens #52 ファイル処理 — 作業引き継ぎ(2026-07-08)

> 別マシン継続用のハンドオフ。恒久ドキュメントは `_memos/file-handling.md`(設計 + 実装状況)。
> 引き継ぎ完了後はこのファイルは削除してよい。

## 0. これは何か
北大病院オンプレ向け Open WebUI フォーク(`technoplasm/open-webui`、基点 v0.6.5)の **issue #52「アップロードファイルの扱い」** の設計確定 + 実装第一波。
**絶対制約**(CLAUDE.md):upstream v0.6.6+ のソース閲覧禁止 / コード実行は nsjail サンドボックス(Pyodide は撤去済) / air-gapped 前提 / CORE(認証・Filter・group 権限・OpenAI 互換)変更は回帰テスト必須 / ユーザーへの応答は日本語。

## 1. 真実源(まずこれを読む)
- **設計**: `_memos/file-handling.md`(プランB 全容 + 実装状況セクション)
- **#52 コメント2本**: 設計サマリ / 進捗(`gh issue view 52 -R technoplasm/open-webui --comments`)
- **prism 参照実装**: `_links/llens`(= `~/src/github.com/prism-hu/llens`)の
  `owui/filters/large_file_gate.py` / `owui/filters/pdf_vision_router.py` / `owui/tools/sandbox.py` / `sandbox/README.md`
- 自動メモ: `~/.claude/.../memory/llens-file-handling-status.md`(あれば)

## 2. 確定した設計(プランB)
**OWUI core は薄い土台、ファイル処理ポリシーは Function/Filter、取り込みは tool。**
- **① 受け入れ**(core・upload endpoint): raw を file_id 保存。薄い size/ext ゲート(upload は filter 前なので拒否はここだけ)。
- **② テキスト化 = upload 時発火のユーザー定義フック(新 Function type)**。登録プロバイダ(docling / STT / 単一 file→text モデル)を `extract(file_id, provider)` 内部APIで一様露出。フックがルーティング(細かく振り分け or 1モデルに丸投げ、複数併用も自由)。**upload 時に走らせる理由=md プレビューを維持するため**(inlet filter だと送信=確定まで走らず劣化する。プランA として据え置き)。非同期+フック内タイムアウト制御、失敗は `data.content` にエラーメッセージを書きシステムプロンプトで突き合わせ(専用 failed 状態なし)。
- **③ 注入 gate = inlet filter(per-model)**: 抽出テキストをトークン実測 → 合算予算を超える間は大きい順に除去 → 残りを `<source>` 全文注入 / 除去分は file_id 告知。prism `large_file_gate` を移植。
- **④ 大容量取り込み = sandbox tool**: モデルが `import_chat_file(file_id)` → `/workspace/inputs/` → python/shell で分割読み。OWUI core 無関与。`owui/tools/sandbox.py`(v0.3.0 で file_id 引数追加済)を builtin tool 移植。
- **撤去**: 添付への RAG(自動チャンク・vector・needle)/ RAG・KB を OWUI 内に組まない / 埋め込みモデル依存。**de-id は #52 から外す**(独立・低難度、後回し)。

## 3. ブランチ・コミット状況
- `master`(push 済): `c78039f55` 設計メモ追加。
- **作業ブランチ `feat/52-file-handling`**(origin に push 済・未マージ)。master 以降のコミット:
  - `543ed2ed3` chat flow の RAG 分岐撤去 → 添付は `data.content` 全文注入の一本道
  - `7306370ea` `process_file` のベクタ書込撤去(抽出=docling/Loader は温存)
  - `42cbc39bb` 死んだベクタ検索サブシステム(query endpoint + helper 群 + import)撤去
  - `d55d50b0b` 設計メモに実装状況追記
  - (このハンドオフ追加コミットが続く)
- **バックエンド全 206 passed**、全て**挙動不変**(`BYPASS_EMBEDDING_AND_RETRIEVAL` 既定 True で従来から死んでいた経路の除去)。

## 4. 新マシンでのセットアップ
```bash
# 両リポジトリを clone(参照用に prism-hu/llens も要る)
#   ~/src/github.com/technoplasm/open-webui
#   ~/src/github.com/prism-hu/llens
cd ~/src/github.com/technoplasm/open-webui
git fetch origin && git checkout feat/52-file-handling

# _links/llens はマシン固有 symlink(gitignore 済・要再作成)
mkdir -p _links && ln -sfn ~/src/github.com/prism-hu/llens _links/llens
# ※ リポジトリ直下に symlink を置かない(Tailwind/vitest が ELOOP)。必ず _links/ 配下。

# backend: uv で .venv(node は .node-version 準拠。node 24.4.0 は禁止)
uv sync
# frontend
pnpm install

# バックエンドテスト(postgres は conftest が docker 自動起動・セッション共有)
cd backend && ../.venv/bin/pytest open_webui/test -q
#   壊れたら: docker rm -f postgres-test-container-will-get-deleted
# 整形: リポジトリ直下で .venv/bin/black <files> / フロントは prettier
```
dev サーバ・dev アカウント・Playwright 検証手順は `_memos/dev-environment.md`(backend は `PYTHONPATH=backend` 必須、SGLang は `prism-llens:8000` の kimi-k2.6)。

**注意**: 前セッションで git の `.git/index.lock` が断続的に出た(バックグラウンド watcher らしい)。commit はリトライ + `rm -f .git/index.lock` で通した。

## 5. 済んだ実装の詳細
- `backend/open_webui/retrieval/utils.py`
  - `get_sources_from_files(request, files)` に縮約。§④(ベクタ検索/hybrid/rerank/full_context/extracted_collections)削除、`BYPASS` 条件も解消。web_search 以外は `data.content` 全文注入。web検索 bypass(`file.docs`)と手動 full(`context=="full"`)は温存。
  - `query_doc*` / `query_collection*` / `get_all_items_from_collections` / `merge_*` / `VectorSearchRetriever` / `RerankCompressor` / `get_doc` を削除(相互参照のみ・外部 caller ゼロを grep 確認済)。関連 import(langchain retrievers/BM25/GetResult/VECTOR_DB_CLIENT/Document 等)整理。
- `backend/open_webui/utils/middleware.py`
  - `chat_completion_files_handler` から retrieval 用 `generate_queries`(クエリ生成)撤去し `get_sources_from_files(request, files)` 直呼びに。**`generate_queries` の import は web_search 用途で残す**(消さない)。
- `backend/open_webui/routers/retrieval.py`
  - `process_file` のベクタ書込ブロック(`save_docs_to_vector_db` 呼び出し)撤去 → 抽出テキストを `data.content` 保存し `collection_name:None` 返却の一本道。**docling/Loader/CONTENT_EXTRACTION_ENGINE は温存**。
  - `/query/doc`・`/query/collection` endpoint と Form 削除(#40 IDOR ガードは endpoint ごと消えて surface 消滅)。
  - **`save_docs_to_vector_db` 本体・`VECTOR_DB_CLIENT` は温存**(process_text/web/batch・KB・reset・memories が使う)。

## 6. 残作業 — 要判断3点(着手前に方針確認)
teardown の続きは全てフロント or CORE セキュリティ or 放置方針(#8)に絡むので勝手に倒さず止めた:

1. **config キー撤去**(`RAG_TOP_K`/`RAG_TOP_K_RERANKER`/`RAG_RELEVANCE_THRESHOLD`/`ENABLE_RAG_HYBRID_SEARCH`/`RAG_FULL_CONTEXT`/`BYPASS_EMBEDDING_AND_RETRIEVAL`/embedding/reranking/chunk/vector 系、`config.py` L1600〜1944 帯)。
   - **絡み**: フロント `src/lib/components/admin/Settings/Documents.svelte` が `/retrieval/config`(`getRAGConfig`/`updateRAGConfig`)で **docling・FILE_MAX・Google/OneDrive 設定と RAG 設定を同一 endpoint**で読む。RAG だけ消すには **config endpoint の分割**(docling/upload を RAG から分離)= フロント刷新が要る。`get_rag_config`/`update_embedding_config`/`update_reranking_config`(`routers/retrieval.py`)も同帯。
   - **温存必須**: `CONTENT_EXTRACTION_ENGINE`/`DOCLING_SERVER_URL`/`TIKA_*`/`RAG_TEMPLATE`(全文注入の system prompt)/`RAG_FILE_MAX_COUNT`・`RAG_FILE_MAX_SIZE`(`/api/config` で汎用ファイル上限に転用)/`PDF_EXTRACT_IMAGES`。
2. **KB(ナレッジベース)撤去**。`routers/knowledge.py`(全稼働・#48 で nav のみ撤去)、`models/knowledge.py::Knowledges`、middleware の `model_knowledge` ブロック、`files.py` の import。
   - **絡み**: `Knowledges` は `utils/access_control.py::has_read_access_to_collection`(#40 CORE セキュリティ)+ `test/apps/webui/utils/test_file_access_idor.py` に結線 → **モデルは温存 or access_control 改修が先**。フロントは app mount 時(`routes/(app)/+layout.svelte`)+ チャットの `#` picker(`MessageInput/Commands.svelte`)で `getKnowledgeBases` を呼ぶので連動。
3. **vector connector / ef・rf 起動ロード撤去**(`retrieval/vector/`、`main.py` L604-641/L691-737)。
   - **絡み**: `routers/memories.py`(#8「放置方針」で close 済)が `VECTOR_DB_CLIENT` + `app.state.EMBEDDING_FUNCTION` を実利用 → **connector 撤去は memories の去就決定が前提**。ef/rf は BYPASS で既に None、`EMBEDDING_FUNCTION` は memories のため生成継続。

## 7. その後 = プランB 本体(TaskList #5)
teardown 方針が固まってから。`_memos/file-handling.md`「#52 core 作業リスト」参照:
1. プロバイダ登録 + `extract(file_id, provider)` 内部API(冪等・キャッシュ=`data.content`)
2. upload フック(新 Function type、#45 の filter/action/pipe に1つ追加)+ upload router 呼び出し口 + 非同期/タイムアウト/失敗は content にメッセージ
3. ① 薄い受け入れゲート(size/ext)
4. ③ gate inlet filter(`large_file_gate` 移植: 合算予算・大きい順除去・file_id 告知)
5. PDF→VLM(`pdf_vision_router` 移植、Kimi は VLM 有)
6. ④ sandbox tool 移植(`owui/tools/sandbox.py` の `import_chat_file`)
7. フロント: 処理中/failed プレビュー、送信時の未抽出ハンドリング

**設計の未確定**(memo「未確定」):プロバイダの実体(engine 設定 vs model 接続を `extract()` 裏で一様化)/ 未抽出時の送信挙動(待つ vs 即 raw で ④)/ ① 上限サイズ(`SANDBOX_FILE_LIMIT_BYTES` 既定25MB 準拠案)/ ③ で画像・音声を予算に数えるか / 手動「全文強制」トグルの要否。

## 8. 検証状況
- バックエンド回帰 **206 passed**(docling/filter/IDOR/native tool/skills 含む)。全変更は挙動不変。
- **未検証**: end-to-end の UI(ファイル添付 → チャットで全文注入)。**マージ前に実機ドライブ推奨**(dev サーバ + Playwright、手順は `_memos/dev-environment.md`。claude-in-chrome は編集前キャッシュに張り付くので Playwright で検証)。

## 9. 作業運用メモ
- 日本語コミット + `refs #N`。CORE 変更は回帰テスト同伴。
- サブエージェント併走時は git working tree と Playwright を共有 → `git add -A` 厳禁(明示パス)・ブラウザ検証は直列化。
- feedback: 意味のない仕様を足さない・一本化を好む / 訂正は削除(否定で残さない) / バックエンド先行(フロントは後で纏めて)。

## 10. 新セッションでの入り方
このファイルと `_memos/file-handling.md` と #52 コメントを読む → 残作業3点(§6)のどれから倒すか方針を決める → プランB 本体(§7)へ。
