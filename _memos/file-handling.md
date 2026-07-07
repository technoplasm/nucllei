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

**構図**: プロバイダ(実行機構)= core 登録、ルーティング(振り分け)= ユーザー定義 Function。B(ユーザー定義)と C(登録機構)の合体。
- **プロバイダ** = docling エンジン / STT モデル / 単一 file→text モデル を「ドキュメント」「STT」等として**登録**し、`extract(file_id, provider) → text` の**内部APIで一様に露出**。個々は engine URL だったり model 接続だったりを裏で吸収。
- **upload フック(Function・新 type)= ルータ**。content_type や独自ルールでプロバイダを選び内部APIを叩く。filter/action/pipe と同じ Function infra(ロード/実行/valves)を流用。
- **③gate は inlet filter のまま**(per-model、prism `large_file_gate` 昇格)。data.content を読むだけ。

**B 固有の設計要素**:
1. **非同期 + フック内で処理制御**: 抽出は遅い(秒〜分)ので upload 同期ブロックはしない。file に `extracting→done` を持たせ非同期実行、プレビューは「処理中…」→完了で表示。加えて **タイムアウト/長すぎる処理の打ち切りもフック内でコントロール**(ユーザー定義なので案件ごとに閾値・中断ポリシーを持てる=B の利点)。チャット途中のファイル追加もこの発火で自然に走る。
2. **admin scope(filter と同レベル)**: Function は元来 admin 権限で authored。upload フックも admin-global で filter と同じレベル。upload 時点でモデル未確定なので **per-model 割り当て(#46)はしない**(全 upload で発火)。抽出はモデル非依存なので問題なし。
3. **発火はファイル1個につき1回**: その1回の中で複数プロバイダを使うのは自由(docling + STT、rasterize+VLM+docling など、フックのコードが組み合わせる)。「1プロバイダに限る」制約ではない。
4. **失敗は content にメッセージを書くだけ**: プロバイダ失敗時は `data.content` にエラーメッセージを入れる。以降は content を一様に扱い、**システムプロンプトで「このファイルは抽出失敗:<msg>」と突き合わせる**。専用 failed 状態や分岐は不要。content は常に「テキスト or 失敗メッセージ」で埋まる。

**新規なのは**: upload router のフック呼び出し口 / プロバイダ内部API(`extract`)/ 非同期実行+プレビューの処理中表示 / Function の新 type(#45 の type 系に1つ追加)。Function 実行基盤・タイムアウト機構は既存流用。

## #52 core 作業リスト

1. **自動を剥がす**: upload 時 auto `process_file` / middleware auto inject / RAG 分岐(§④・vector・KB・needle・ef/rf)を撤去。
2. **プロバイダ登録 + 内部API露出**: `extract(file_id, provider)` 冪等・キャッシュ(data.content)。docling/STT/file→text モデルを登録機構で吸収。
3. **upload フック(新 Function type)**: upload router に呼び出し口。非同期実行 + フック内タイムアウト制御。失敗は content にメッセージ。
4. **① 薄い受け入れゲート**だけ core(size / ext、upload は filter 前なので拒否はここだけ)。
5. **③gate は inlet filter**: prism `large_file_gate`(合算予算・大きい順除去・file_id 告知)を Function 移植。PDF→VLM は `pdf_vision_router` 移植(Kimi VLM 有)。
6. **フロント**: 処理中/failed プレビュー、送信時の未抽出ハンドリング。

## 参照
- prism 実装: `_links/llens` の `owui/filters/large_file_gate.py`・`pdf_vision_router.py`・
  `owui/tools/sandbox.py`・`sandbox/README.md`。
- 現状 fork のフロー: upload=`routers/files.py` / 抽出=`routers/retrieval.py::process_file` /
  注入=`utils/middleware.py::chat_completion_files_handler` + `retrieval/utils.py::get_sources_from_files`。
- 関連: #18(builtin tool)/ #46(per-model filter)/ #13(Filter ミドルウェア化)/ #40(file IDOR)/ #41(clone・別軸)。

## 未確定
- **プロバイダの実体**: engine 設定(docling URL)と model 接続(STT / file→text)を `extract()` の裏でどう一様に扱うか。登録 UI は既存のどれに寄せるか。
- **未抽出時の送信挙動**: extracting 中に送信されたら待たせるか、即 raw 扱いで ④ に倒すか。
- ① 受け入れ上限サイズの確定値(`SANDBOX_FILE_LIMIT_BYTES` 準拠案)。
- ③ で画像/音声のトークンを注入予算に数えるか(v1 はテキスト産出分のみ gate 対象で可)。
- 手動「全文強制」トグル(prism `file.context=="full"`)を残すか gate 一本にするか。
