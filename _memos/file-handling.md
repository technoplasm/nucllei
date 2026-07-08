# ファイルアップロードの扱い(#52)

添付ファイルの受け入れ・テキスト化・context 注入・サンドボックス取り込みの設計。
一次根拠は fork.md(薄い業務オンプレ基盤)。スコープ/所有/clone(#41)は別軸(本メモ対象外)。

## 結論:プランB を採用(2026-07-08)

抽出の置き場所で2案あり、**プランB(upload 時発火のユーザー定義フック)を採用**。
- **プランA**(inlet filter で抽出): 最大の自由度だが **upload 時プレビューが消える**(送信=確定まで抽出が走らない)= 仕様の劣化。「完成形の一つ」として下に据え置き。
- **プランB**(upload フック + プロバイダ登録): プレビュー維持 + ユーザー定義の柔軟性を両立。カスタム前提の出荷物なので「外から見通しが効く・案件ごとに差を出せる」B が筋。**採用**。
- プランC(core 固定 + admin 設定): プレビューは残るが抽出ロジックが不透明で案件差を吸収できない。B に包含される(下記プロバイダ登録=C 相当)。

**②抽出と③gate は timing で割れる**: ②抽出=モデル非依存=**upload 時**(フック)、③gate/注入=モデル依存(context 予算)=**inlet filter・per-model**。プランA の欠点は「②まで inlet に寄せた」ことに由来していた。

以下「確定した基本方針」「レイヤ分担」は A/B 共通の骨格。B 固有の設計は「## プランB」節。

## 確定した基本方針(2026-07-08 設計)

**OWUI core は薄い土台に徹し、ファイル処理のポリシーは全部 Filter に置く。取り込みは tool。**

- **RAG は撤去**。添付ファイルへの自動チャンク・ベクタ化・近傍自動注入は「古い」ので廃止。
  `file-{id}` collection / `save_docs_to_vector_db` / `query_collection`・hybrid・rerank /
  `generate_queries` / 関連 config(`RAG_TOP_K` / `RELEVANCE_THRESHOLD` / `HYBRID_SEARCH` /
  `RAG_FULL_CONTEXT` / `BYPASS_EMBEDDING_AND_RETRIEVAL` の二重否定)を削除。埋め込みモデル(ef/rf)依存も消滅。
- **RAG/ナレッジベースを OWUI 内に組まない**。「アップロード→KB化→tool探索」も OWUI の仕事にしない
  (やるなら外側 / tool 側)。ドキュメントの一次処理として RAG らない。
- **de-id は #52 から外す**。サブエージェントを挟むだけの独立・低難度タスク。別 filter で後回し。
- **抽出は「text extraction」層**。プロバイダ(docling / STT 等)は「対象形式→テキスト変換」だけを担う。

## レイヤ分担

### core(薄い・不可避な分だけ)
- **① 受け入れゲート**(upload endpoint `POST /files/`): raw を保存し file_id 発番。
  **filter は inlet で動く=upload 後なので、拒否できるのはここだけ**。size / 拡張子の最小限バリデーション。
  上限は原本をサンドボックスへ b64 転送する `SANDBOX_FILE_LIMIT_BYTES`(既定 25MB)を超える分は
  そもそも渡せないので、これを受け入れ上限の目安にする(要確定)。
- **プロバイダ実行機構を filter-callable な内部関数として露出**。core が自動実行はしない。
  - `ensure_extracted(file_id, engine)`: **冪等・キャッシュ付き**。未抽出なら選択プロバイダ(docling/tika 等)で
    抽出し `File.data.content` に保存、既にあればそれを返す。毎ターンの再 docling を防ぐ主キー。
  - STT も同型(audio→テキスト)。
- filter ランタイム(inlet/outlet、per-model = #46)。
- **今ある自動挙動を撤去**: upload 時の自動 `process_file`(docling)/ middleware の
  `get_sources_from_files` 自動注入。これらは filter の仕事に移す。

### Filter(実際のポリシー全部 / prism の Function として出荷)
filter 関数から OWUI 内部関数を叩くと選択プロバイダが動く、という構図。粒度は filter の自由。

- **② 振り分け(text extraction)**(プランB では inlet ではなく **upload フック**へ移動 → 「## プランB」節): 添付 file_id ごとに経路選択。
  - audio → STT / document → docling、と細かく振り分ける、**または**
  - pdf も mp3 も全部食う単一「file→text モデル」1本に raw を丸投げする、
  - どちらでもよい(filter が `ensure_extracted` 等の内部関数を選んで呼ぶだけ)。
- **③ 注入 gate**(prism `large_file_gate` を昇格): 抽出テキストをトークン実測 →
  **合算予算を超える間は大きい順に除去** → 残りを `<source>` で注入 / 除去分は file_id を列挙した
  system 告知1本(「サンドボックスで読め」)。
- **PDF→VLM ルーティング**(prism `pdf_vision_router`): Kimi は VLM 有。テキスト層無し(scan)PDF は
  ラスタライズ→`image_url` 注入。これも filter。
- **②③は 1 filter に合体しても 2 filter に分けてもよい**。どちらも filter なので同じレベルに落ちる。

### tool(#18 builtin / サンドボックス)
- **④ 取り込み**: モデルが `import_chat_file(file_id)` を呼ぶ → 原本 raw を `/workspace/inputs/` に配置 →
  python/shell で分割読み(docx は py 直読、docling 結果ではなく raw を読む)→ 結果だけ context に戻す。
  **OWUI core は無関与**。`owui/tools/sandbox.py`(v0.3.0 で `import_chat_file` に file_id 引数追加済み)を
  fork の builtin tool として移植する。

## 抽出タイミング(反転の実体)

upload は別リクエストでモデル未確定 → **抽出は upload 時(eager)ではなく初回 inlet 時(lazy・filter 駆動)**。
`ensure_extracted` のキャッシュで再抽出を防ぐ。今の OWUI(upload 時同期 docling)からの主な変更点。

## 全分岐フロー

```
[upload] ─► ① core: raw 保存(file_id)
            └► ② upload フック(ユーザー定義 Function・非同期)
                 file→text: content_type 等でプロバイダ選択(複数使用も自由)→ 内部API extract() →
                 data.content に保存(extracting→done、content=テキスト or 失敗メッセージ)。
                 タイムアウト/打ち切りもフック内で制御。プレビューはここ
[chat inlet] ─► ③ Filter(per-model)
                 gate: data.content を実測→大きい順除去(合算予算)→残り注入 /
                       除去分・未抽出は file_id 告知
[モデルが必要なら] ─► ④ tool: import_chat_file(file_id) → /workspace/inputs/ → python/shell
```

注: プランA では ② も inlet filter に同居していた(→プレビュー喪失)。プランB は ② を upload フックに出す。

## プランB(採用)— upload フック + プロバイダ登録

> **Attachment 未登録時の core 挙動 = (c) builtin Attachment を同梱(確定 2026-07-08)**。
> core の upload endpoint(`routers/files.py POST /files/`)から content_type 分岐の自動処理
> (audio→transcribe+process_file / 画像以外→process_file)を剥がし、**docling 抽出する既定の
> builtin Attachment Function を1本出荷**して常に通す。**拡張子/content_type 判定は core の
> ハードコードから builtin Attachment の valves に格下げ**(設定可能化)。出荷/登録の仕組みは
> **builtin tool(#18)の見せ方を参考**にする(自動登録・ユーザー authored と区別)。
>
> **builtin 判別 = id 接頭辞 `builtin_` 一本(確定 2026-07-08)**。Function テーブルは #45/#43 で
> `meta` 列ごと撤去済み → tool の `meta.builtin` フラグは使えない。meta を復元する案も出たが、
> builtin 判別だけなら id 接頭辞で足り、フラグを meta に持たせるのは冗長(tool 側も然り)なので
> **復元しない**。`BUILTIN_FUNCTIONS` dict(ロードの真実源)+ id `builtin_*` で判別、フロントの
> セクション分けも id 接頭辞で行う。

> **Function 新 type 名 = `Attachment`(type トークン `attachment`)確定**。Filter/Action/Pipe が
> 「どこにフックするか=役割」を1語で表すのに倣い、添付ファイルのライフサイクルにフックする種別を
> ドメイン語 Attachment で命名。`upload` はタイミング命名で不整合かつチャット途中の添付追加でも走るため
> 不採用。以下「upload フック」は Attachment Function の口語表記。

**構図**: プロバイダ(実行機構)= core 登録、ルーティング(振り分け)= ユーザー定義 Function。B(ユーザー定義)と C(登録機構)の合体。
- **プロバイダは種別ごとに別々の内部API(入口を無理に揃えない)**。単一 `extract()` に一本化しない ← 種別で入出力も追加オプションも違うので統一する意味がない。hook 内で処理するので入口が同じである必要がない。
  - **文書抽出**: `ファイル → 文字列`(OCR してるか等は呼ぶ側に不透明。設定に付随した共通オプションがあれば渡せる)。既存 `Loader`/`CONTENT_EXTRACTION_ENGINE` を流用。
  - **STT**: `音声 → 文字列(+情報)`。既存 `audio.transcribe`/`AUDIO_STT_ENGINE` を流用。
  - **TTS**: `テキスト → 音声`(必要なら。既存 audio TTS)。
  - 「単一 file→text モデル」も **文書抽出種別の一エンジン**として登録(engine=model 接続)。engine URL か model 接続かは各種別の内部で吸収、種別をまたいでは吸収しない。
  - **入口は「設定で登録したエンジン」に対応してあればいい**(STT の入口に音声を渡せば登録済み STT エンジンが反応する、という素直な対応)。
- **Attachment フック(Function・新 type=`attachment`)= ルータ**。content_type / 拡張子で種別を判定し、対応する内部APIを**呼び分ける**。filter/action/pipe と同じ Function infra(ロード/実行/valves)を流用。core は一様化も自動実行もしない。
- **③gate は inlet filter のまま**(per-model、prism `large_file_gate` 昇格)。data.content を読むだけ。

**B 固有の設計要素**:
1. **非同期 + フック内で処理制御**: 抽出は遅い(秒〜分)ので upload 同期ブロックはしない。file に `extracting→done` を持たせ非同期実行、プレビューは「処理中…」→完了で表示。加えて **タイムアウト/長すぎる処理の打ち切りもフック内でコントロール**(ユーザー定義なので案件ごとに閾値・中断ポリシーを持てる=B の利点)。チャット途中のファイル追加もこの発火で自然に走る。
2. **on/off は per-model(ワークスペースのモデル設定でトグル)**: 使う/使わないは tool と同じく **ワークスペースのモデル設定**で切り替え(builtin Attachment もそこに現れる)。→ 以前の「upload 時点でモデル未確定なので per-model はしない/全 upload 発火」を**上書き**。
   - **含意**: 抽出自体はモデル非依存だが、有効/無効の解決に**モデル文脈が要る** → upload endpoint は「今のチャットの選択モデル」を受け取り、そのモデルで有効な Attachment だけ発火させる(フロントが upload 時に model id を渡す)。authoring は元来 admin 権限。
3. **発火はファイル1個につき1回**: その1回の中で複数プロバイダを使うのは自由(docling + STT、rasterize+VLM+docling など、フックのコードが組み合わせる)。「1プロバイダに限る」制約ではない。
4. **失敗は content にメッセージを書くだけ**: プロバイダ失敗時は `data.content` にエラーメッセージを入れる。以降は content を一様に扱い、**システムプロンプトで「このファイルは抽出失敗:<msg>」と突き合わせる**。専用 failed 状態や分岐は不要。content は常に「テキスト or 失敗メッセージ」で埋まる。

**新規なのは**: upload router のフック呼び出し口 / 種別ごとの内部API(文書抽出=`Loader` を file_id 冪等ラップ、STT/TTS は既存 audio を薄くラップ)/ 非同期実行+プレビューの処理中表示 / Function の新 type(#45 の type 系に1つ追加)。Function 実行基盤・タイムアウト機構・各エンジン実体は既存流用。

## #52 core 作業リスト

1. **自動を剥がす**: upload 時 auto `process_file` / middleware auto inject / RAG 分岐(§④・vector・KB・needle・ef/rf)を撤去。
2. **種別ごとの内部API露出**(入口を揃えない): 文書抽出=`file_id → 文字列`(冪等・キャッシュ=data.content、`Loader` ラップ)/ STT=`音声 → 文字列` / TTS=`テキスト → 音声`。各種別は設定で登録したエンジンに対応。**これらは内部関数で、hook/filter から呼べるほか builtin tool(#18)の実装からも呼べる**。tool として直接モデルに露出するのではなく、**tool のコードが内部関数を叩く**向き(モデルは tool 経由で間接的に文書読取/STT/TTS を使う)。
3. **Attachment フック(新 Function type=`attachment`)**: upload router に呼び出し口。非同期実行 + フック内タイムアウト制御。失敗は content にメッセージ。
4. **① 薄い受け入れゲート**だけ core(size / ext、upload は filter 前なので拒否はここだけ)。
5. **③gate は inlet filter**: prism `large_file_gate`(合算予算・大きい順除去・file_id 告知)を Function 移植。PDF→VLM は `pdf_vision_router` 移植(Kimi VLM 有)。
6. **フロント**: 処理中/failed プレビュー、送信時の未抽出ハンドリング。

## 実装状況(ブランチ `feat/52-file-handling`、2026-07-08)

**済(RAG teardown・バックエンド)**:
- **段1** chat flow の RAG 分岐撤去(543ed2ed3): `get_sources_from_files` を `(request, files)` に縮約し §④(ベクタ検索/hybrid/rerank/full_context)を削除、web_search 以外は data.content を全文注入する唯一経路に。middleware から retrieval 用 `generate_queries`(クエリ生成)撤去(web_search 用途は温存)。
- **段2-lite** `process_file` のベクタ書込撤去(7306370ea): 抽出は温存、`save_docs_to_vector_db` 呼び出しを除去し全文保存のみに。
- **段2** 死んだベクタ検索サブシステム撤去(42cbc39bb): `/query/doc`・`/query/collection` endpoint、`query_doc*`/`query_collection*`/`get_all_items_from_collections`/`merge_*`/`VectorSearchRetriever`/`RerankCompressor`/`get_doc`(相互参照のみ・外部 caller なし)、および専用 import を撤去。
- いずれも挙動不変(BYPASS 既定 True で従来から死んでいた経路)。回帰 docling/filter/IDOR/native tool/skills 緑。

**済(プランB 本体・フェーズ1 = backend 中核・挙動保存)**:
- **文書抽出の内部関数化**(22dd591bd): `retrieval/extract.py::extract_file_text(request, file, *, force=False)`。
  `process_file` の Loader→data.content ロジックを冪等・キャッシュ付きで切り出し、process_file は委譲。
- **Attachment Function 種別 + 既定 builtin**(a421ae4d8): plugin loader に `Attachment` class 判定
  (type `attachment`)。`builtin_functions/`(builtin_tools の Function 版)新設、builtin 判別は
  id 接頭辞 `builtin_` 一本(Function は meta 列なし)。既定 `builtin_attachment` = 従来 files.py の
  content_type 分岐を忠実 relocate・判定は valves 化。起動時 `sync_builtin_functions` で upsert。
- **upload を dispatch に配線**(e79a1c955): `utils/attachment.py::process_attachment_functions`
  (filter dispatch と同型・sync 実行)。files.py のハードコード分岐を撤去し一本化。builtin id は
  `BUILTIN_FUNCTIONS` から直接引く(コードが真実源・sync 前でも発火)。**全 206 passed・挙動不変**。

**プランB 本体 残り**:
- **フェーズ2(per-model トグル)**: `model.meta.attachmentIds` 解決 + upload が model 文脈を受け取る
  + フロント Selector(ワークスペースのモデル設定)。builtin の content 更新/削除拒否(routers/functions.py)も。
- **フェーズ3**: 非同期実行+処理中プレビュー(file に extracting→done)/ STT・TTS 内部関数(後回し可)/
  ③gate filter(`large_file_gate` 移植)/ PDF→VLM(`pdf_vision_router`)/ ④sandbox tool 移植。

**未着手(要ユーザー判断・絡みが深い / teardown 残り)**:
- **config キー撤去**(`RAG_TOP_K`/`RELEVANCE_THRESHOLD`/`ENABLE_RAG_HYBRID_SEARCH`/`RAG_FULL_CONTEXT`/`BYPASS_EMBEDDING_AND_RETRIEVAL`/embedding/reranking/chunk/vector)→ **フロント `Documents.svelte` が `/retrieval/config` で docling・FILE_MAX と RAG 設定を同一 endpoint で読む**ため、config endpoint の分割(docling/upload 設定を RAG から分離)が要る。フロント刷新を伴う。
- **KB(ナレッジベース)撤去** → `models/knowledge.py::Knowledges` が `access_control.has_read_access_to_collection`(#40 CORE セキュリティ)と `test_file_access_idor.py` に結線。router 撤去は可だがモデルは温存 or access_control 改修が要る。
- **vector connector / embedding(ef/rf)撤去** → **`routers/memories.py`(#8 放置方針)が `VECTOR_DB_CLIENT` + `EMBEDDING_FUNCTION` を共有**。connector 本体撤去は memories の去就決定が前提。
- **プランB 本体(未着手)**: プロバイダ登録 + `extract()` 内部API / upload フック(新 Function type)/ ③gate filter(large_file_gate 移植)/ ④sandbox tool 移植(owui/tools/sandbox.py)/ フロント処理中プレビュー。

**次の安全な一手の候補**: (a) KB router 撤去(モデル温存・access_control 改修込み)/ (b) config endpoint 分割 → RAG config キー撤去(フロント Documents 刷新込み)。どちらもフロント or CORE セキュリティに触れるため単独判断は避け、着手前に方針確認。

## 参照
- prism 実装: `_links/llens` の `owui/filters/large_file_gate.py`・`pdf_vision_router.py`・
  `owui/tools/sandbox.py`・`sandbox/README.md`。
- 現状 fork のフロー: upload=`routers/files.py` / 抽出=`routers/retrieval.py::process_file` /
  注入=`utils/middleware.py::chat_completion_files_handler` + `retrieval/utils.py::get_sources_from_files`。
- 関連: #18(builtin tool)/ #46(per-model filter)/ #13(Filter ミドルウェア化)/ #40(file IDOR)/ #41(clone・別軸)。

## 確定(2026-07-08 追記)
- **プロバイダの実体**: 単一 `extract()` に一本化しない。**種別ごとに素直な内部API を1本ずつ**(文書抽出=file→文字列 / STT=音声→文字列 / TTS=テキスト→音声)。入出力も追加オプションも種別で違うので揃えない。engine URL か model 接続かは各種別の内部で吸収(種別をまたいでは吸収しない)。入口は「設定で登録したエンジン」に対応。**内部関数として提示 → hook/filter から呼べる + builtin tool(#18)の実装からも呼べる**(tool として直接露出ではなく tool のコードが叩く=モデルは tool 経由で使う)。呼び分けは hook がルータとして担う。

## 未確定
- **未抽出時の送信挙動**: extracting 中に送信されたら待たせるか、即 raw 扱いで ④ に倒すか。
- ① 受け入れ上限サイズの確定値(`SANDBOX_FILE_LIMIT_BYTES` 準拠案)。
- ③ で画像/音声のトークンを注入予算に数えるか(v1 はテキスト産出分のみ gate 対象で可)。
- 手動「全文強制」トグル(prism `file.context=="full"`)を残すか gate 一本にするか。
