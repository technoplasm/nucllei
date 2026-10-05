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
- **④ 取り込み**: モデルが `send_to_workspace(file_id)`(起草時の名は `import_chat_file`)を呼ぶ →
  原本 raw を `/workspace/imports/`(旧 inputs)に配置
  (`url=` の URL(絶対、または同一サーバの `/ext/…`)も同じ置き場へ — 解決は共有 resolver
  `utils/image_refs`、nucllei の `EXT_DIR` に実体がある物はコピーせず sandbox のパス
  `/ext/…`(URL と同じ文字列)を返す。
  **参照の規則**(url = 絶対 URL または同一サーバの `/ext/…`・`/api/v1/files/{id}/content` / path = sandbox の絶対パス(相対は受けない)/
  file_id = 添付・生成物(本文リンクは `/api/v1/files/{id}/content`、tool には file_id で渡す)/
  引数名で解釈が決まり、値の中身では判断しない / 受け取った形のまま渡す)の定義は
  `utils/image_refs.py` の module docstring と `README_API.md` §3.8、経緯は
  `_memos/builtin-tools.md`「参照の規則の改訂(2026-10-03)」節)→
  python/shell で分割読み(docx は py 直読、docling 結果ではなく raw を読む)→ 結果だけ context に戻す。
  **core のファイル経路は無関与**。**実体は組み込み `nucllei/builtin_tools/sandbox.py`**
  (2026-09-03、#114 で配備層 llens のカスタム tool から移設 — 下の 2026-09-03 節)。

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
[モデルが必要なら] ─► ④ tool: send_to_workspace(file_id) → /workspace/imports/ → python/shell
                        (起草時の名は import_chat_file / inputs。実体は組み込み builtin_sandbox)
```

注: プランA では ② も inlet filter に同居していた(→プレビュー喪失)。プランB は ② を upload フックに出す。

## プランB(採用)— upload フック + プロバイダ登録

> **最終方針: fork は builtin Attachment を持たない(確定 2026-07-08)**。
> core の upload endpoint(`routers/files.py POST /files/`)から content_type 分岐の自動処理
> (audio→transcribe+process_file / 画像以外→process_file)は剥がした。テキスト化ポリシーの実体
> (docling ルータ等)は **すべて案件層(prism)の authored Attachment Function**。fork 本体は
> Attachment 種別の仕組みと内部関数だけを提供し、Attachment の実体は同梱しない。
> ※ 検討経緯: 一時「既定処理を builtin Attachment で同梱((c)案)」→「初期/参照実装として1本だけ
> builtin」と揺れたが、**builtin は邪魔(UI に既定として現れる・重複)**なので撤去に決着。
> よって `builtin_functions/` 機構ごと不要(唯一の中身が Attachment だった)→ 撤去。
> fork 単体では Attachment 未割り当て=upload で抽出は走らない(prism が Attachment を積んで運用)。

> **Function 新 type 名 = `Attachment`(type トークン `attachment`)確定**。Filter/Action/Pipe が
> 「どこにフックするか=役割」を1語で表すのに倣い、添付ファイルのライフサイクルにフックする種別を
> ドメイン語 Attachment で命名。`upload` はタイミング命名で不整合かつチャット途中の添付追加でも走るため
> 不採用。以下「upload フック」は Attachment Function の口語表記。

**構図**: プロバイダ(実行機構)= core 登録、ルーティング(振り分け)= ユーザー定義 Function。B(ユーザー定義)と C(登録機構)の合体。
- **プロバイダは種別ごとに別々の内部API(入口を無理に揃えない)**。単一 `extract()` に一本化しない ← 種別で入出力も追加オプションも違うので統一する意味がない。hook 内で処理するので入口が同じである必要がない。
  - **文書抽出**: `ファイル → 文字列`(OCR してるか等は呼ぶ側に不透明)。
    **fork は抽出コードを持たず、`markitdown` を同梱するだけ**(確定 2026-07-14・#52)。docling でない「素の」
    md 化は、prism の Attachment が `from markitdown import MarkItDown` → `.convert(path).text_content` で
    **直接呼ぶ**。fork にラッパ関数(旧 `utils/extraction.py`)は置かない — 実質 1 行で prism から呼べるため。
    ※ 経緯: 一度 v0.6.5 の langchain ローダ版を `utils/extraction.py` に復元したが、これは markitdown の
    劣化再実装(拡張子→ローダの手振り分け)だったため撤回。markitdown は docx=mammoth・xlsx=pandas・
    pptx=python-pptx・pdf=pdfminer と、まさに同じ「軽い md 化・OCR なし」を担う。`.msg` も markitdown は
    `olefile`(BSD)で読み、#2 で除外した extract-msg(GPL)は引かない(`markitdown[all]` でも安全と lock で確認)。
    - **xlsx→シート別 CSV** 等の型別ポリシーは **prism が手前で処理**(`process` 内で拡張子を見て自前 CSV 化 →
      残りは markitdown に委譲)。fork はカスタムコンバータを持たない。
    - docling が要るのはスキャン PDF の OCR/レイアウト保持など重い用途だけ。型で prism が振り分ける。
  - **STT**: `音声 → 文字列(+情報)`。既存 `audio.transcribe`/`AUDIO_STT_ENGINE` を流用。
  - **TTS**: `テキスト → 音声`(必要なら。既存 audio TTS)。
  - 「単一 file→text モデル」も **文書抽出種別の一エンジン**として登録(engine=model 接続)。engine URL か model 接続かは各種別の内部で吸収、種別をまたいでは吸収しない。
  - **入口は「設定で登録したエンジン」に対応してあればいい**(STT の入口に音声を渡せば登録済み STT エンジンが反応する、という素直な対応)。
- **Attachment フック(Function・新 type=`attachment`)= ルータ**。content_type / 拡張子で種別を判定し、対応する内部APIを**呼び分ける**。filter/action/pipe と同じ Function infra(ロード/実行/valves)を流用。core は一様化も自動実行もしない。
- **③gate は inlet filter のまま**(per-model)。実体は prism `large_file_gate`(案件層)。fork は
  filter 機構を提供するだけで本体には持たない。data.content を読むだけ。

### 既定 md 化 = markitdown(fork 同梱・prism が直接呼ぶ)

> **改訂(2026-07-18)**: llens(prism)側の Attachment は **markitdown_extract(本命・軽量)と
> docling_extract(待機側・遅いがレイアウト/表に強い)の 2 本立て**に再編(llens `f53dac6`。
> 旧 attachment_extract は markitdown_extract にリネーム)。切り替えは compose の
> `ATTACHMENT_FUNCTION_ID` 張り替えのみ。markitdown は **0.0.2 → 0.1.6 に更新**(nucllei
> `3df17d7a3`): 旧版固定の原因は `[all]` extra(0.1.x では azure 系プレリリース依存を引いて
> 解決不能)で、pdf/docx/pptx/xlsx/xls/outlook/epub に絞った。GPL 連鎖なしは lock で再確認。
> **markitdown の PDF は「ほぼ平文」が仕様**(PdfConverter = pdfminer extract_text 一発、
> docstring 自ら essentially plain-text と宣言)— 構造が要る PDF は docling、視覚は pdf_vision。

fork は抽出コードを持たず、`markitdown[all]` を同梱するだけ。prism の Attachment が
`MarkItDown().convert(path).text_content` で md 文字列を得る。型別の中身は markitdown の各コンバータ
(docx=**mammoth** で見出し/リスト/表を md 化・pdf=**pdfminer**(OCR なし)・pptx=**python-pptx**・
xlsx=pandas→md テーブル・html=markdownify)。**OCR は既定で無し**(画像キャプションは任意で LLM を渡した時だけ)。

- **xlsx はトークン節約のため prism が手前で「シート別 CSV」に自前処理**(`pandas.read_excel(sheet_name=None)`
  → 各シート `to_csv()` を `---` 区切り)。markitdown の既定(md テーブル)は使わず、prism が xlsx だけ先に食う。
- **pdf/pptx は文字ベストエフォート**(OCR/レイアウトは prism が VLM 並列で別に取るので markitdown 側は頑張らない)。
- **`.msg`**: markitdown は `olefile`(BSD)で読む。#2 で除外した extract-msg(GPL)は `[all]` でも引かない(lock 確認済み)。
- 旧 `utils/extraction.py`(v0.6.5 langchain ローダ版)は markitdown の劣化再実装だったため撤回・削除。
  併せて langchain-community / unstructured / networkx / nltk / pypdf / docx2txt / ftfy を撤去
  (unstructured の nltk データのオフライン取得問題からも解放)。langchain-core だけは tools.py が使うので直接依存に残す。

**B 固有の設計要素**:
1. **非同期 + フック内で処理制御**: 抽出は遅い(秒〜分)ので upload 同期ブロックはしない。file に `extracting→done` を持たせ非同期実行、プレビューは「処理中…」→完了で表示。加えて **タイムアウト/長すぎる処理の打ち切りもフック内でコントロール**(ユーザー定義なので案件ごとに閾値・中断ポリシーを持てる=B の利点)。チャット途中のファイル追加もこの発火で自然に走る。
2. ~~**on/off は per-model(ワークスペースのモデル設定でトグル)**~~ → **改訂(2026-07-12、#69): グローバル config に一本化**(「## 改訂(#69)」節参照)。モデル単位で Attachment を大きく変える運用は無い、かつファイル管理ページ(チャット外)からのアップロードにはモデル文脈が無いため、per-model 割り当て(`attachmentIds`)は廃止し admin がグローバルに 1 つ選ぶ形へ戻した(「全 upload 発火」への再回帰だが、発火する実体は config で選んだ 1 本)。
3. **発火はファイル1個につき1回・1モデルにつき Attachment は単一選択**: Attachment は
   `data.content` を生成するルータなので、per-model 割り当ては **1つだけ**(UI も単一選択)。
   複数走ると content を上書き衝突する。その1つの中で複数プロバイダを使うのは自由(docling + STT、
   rasterize+VLM+docling など、フックのコードが組み合わせる)。「1プロバイダに限る」制約ではない。
4. **失敗は content にメッセージを書くだけ**: プロバイダ失敗時は `data.content` にエラーメッセージを入れる。以降は content を一様に扱い、**システムプロンプトで「このファイルは抽出失敗:<msg>」と突き合わせる**。専用 failed 状態や分岐は不要。content は常に「テキスト or 失敗メッセージ」で埋まる。

**新規なのは**: upload router のフック呼び出し口 / 種別ごとの内部API(文書抽出=`Loader` を file_id 冪等ラップ、STT/TTS は既存 audio を薄くラップ)/ 非同期実行+プレビューの処理中表示 / Function の新 type(#45 の type 系に1つ追加)。Function 実行基盤・タイムアウト機構・各エンジン実体は既存流用。

## #52 core 作業リスト

1. **自動を剥がす**: upload 時 auto `process_file` / middleware auto inject / RAG 分岐(§④・vector・KB・needle・ef/rf)を撤去。
2. **種別ごとの内部API露出**(入口を揃えない): 文書抽出=`file_id → 文字列`(冪等・キャッシュ=data.content、`Loader` ラップ)/ STT=`音声 → 文字列` / TTS=`テキスト → 音声`。各種別は設定で登録したエンジンに対応。**これらは内部関数で、hook/filter から呼べるほか builtin tool(#18)の実装からも呼べる**。tool として直接モデルに露出するのではなく、**tool のコードが内部関数を叩く**向き(モデルは tool 経由で間接的に文書読取/STT/TTS を使う)。
3. **Attachment フック(新 Function type=`attachment`)**: upload router に呼び出し口。非同期実行 + フック内タイムアウト制御。失敗は content にメッセージ。
4. **① 薄い受け入れゲート**だけ core(size / ext、upload は filter 前なので拒否はここだけ)。
5. **③gate は inlet filter(案件層=prism)**: fork は inlet filter 機構を提供するだけ。実体
   (prism `large_file_gate`=合算予算・大きい順除去・file_id 告知、`pdf_vision_router`)は prism の
   Function をそのまま使う。**fork 本体には移植しない**(重複回避)。~~④ sandbox tool も同様に prism 層。~~
   → **反転(2026-09-03、#114): ④ sandbox tool は fork 本体の組み込み**(下の 2026-09-03 節)。
   ③gate(院内ポリシーの塊)はいまも prism 層のまま。
6. **フロント**: 処理中/failed プレビュー、送信時の未抽出ハンドリング。

## 実装状況(master 統合済み、2026-07-08)

> feat/52-file-handling は master に FF マージ・push 済み(branch は削除)。以下は主要フェーズの記録。

**済(RAG teardown・バックエンド)**:
- **段1** chat flow の RAG 分岐撤去(543ed2ed3): `get_sources_from_files` を `(request, files)` に縮約し §④(ベクタ検索/hybrid/rerank/full_context)を削除、web_search 以外は data.content を全文注入する唯一経路に。middleware から retrieval 用 `generate_queries`(クエリ生成)撤去(web_search 用途は温存)。
- **段2-lite** `process_file` のベクタ書込撤去(7306370ea): 抽出は温存、`save_docs_to_vector_db` 呼び出しを除去し全文保存のみに。
- **段2** 死んだベクタ検索サブシステム撤去(42cbc39bb): `/query/doc`・`/query/collection` endpoint、`query_doc*`/`query_collection*`/`get_all_items_from_collections`/`merge_*`/`VectorSearchRetriever`/`RerankCompressor`/`get_doc`(相互参照のみ・外部 caller なし)、および専用 import を撤去。
- いずれも挙動不変(BYPASS 既定 True で従来から死んでいた経路)。回帰 docling/filter/IDOR/native tool/skills 緑。

**済(プランB 本体・フェーズ1 = backend 中核・挙動保存)**:
- **文書抽出の内部関数化**(22dd591bd): `retrieval/extract.py::extract_file_text(request, file, *, force=False)`。
  `process_file` の Loader→data.content ロジックを冪等・キャッシュ付きで切り出し、process_file は委譲。
- **Attachment Function 種別**(a421ae4d8): plugin loader に `Attachment` class 判定(type
  `attachment`)を追加。※同コミットで入れた builtin_functions/ + 既定 builtin Attachment は
  後に撤去(下記)。plugin loader の type 判定だけが残る。
- **upload を dispatch に配線**(e79a1c955): `utils/attachment.py::process_attachment_functions`
  (filter dispatch と同型・sync 実行)。files.py のハードコード分岐を撤去し一本化。

**方針の確定(2026-07-08)— fork 本体 vs 案件層(prism)の線引き**
(**④ sandbox tool の帰属だけ 2026-09-03 に反転済み** — 下の #114 節):
- prism-llens の filter/tool(gate = `owui/filters/large_file_gate.py`、sandbox tool =
  `owui/tools/sandbox.py` 等)は**そのまま使う**。同じものを fork 本体に重複させない。
- **fork 本体が持つのは「仕組み」だけ**: Attachment という新 Function type(plugin loader の
  type 判定 + dispatch + files.py 配線)、内部関数 `extract_file_text`、既存の filter/tool 機構、
  Files API。**③gate は fork には作らない**(prism 層のポリシー)。
  ~~④sandbox tool も fork には作らない~~ → **反転(2026-09-03、#114)**。
- **builtin Attachment も持たない**: 一時「初期/参照実装として1本だけ builtin」としたが、
  UI に既定として現れて邪魔・重複なので撤去。`builtin_functions/` 機構ごと不要になり削除。
  fork 単体では Attachment 未割り当て=upload で抽出は走らない(prism が積む)。
- ※ 一度 fork 本体に入れた builtin gate(旧 46fd2ccd4)・builtin Attachment(旧 a421ae4d8)は
  上記方針に反するため撤去した。

**済(フェーズ2 = per-model トグル・完全 opt-in)**:
- **backend**(83f849407 + builtin 撤去コミット): `get_attachment_function_ids(assigned_ids)` を
  model.meta.attachmentIds ベースに(get_sorted_filter_ids と同型・DB の type=attachment で実在チェック)。
  None/空=何もしない(完全 opt-in)。`files.py` upload に `model_id` Query 追加 → `Models.get_model_by_id`
  で attachmentIds を引き dispatch へ。docling 回帰は抽出能力を `/retrieval/process/file` で直接固定する形に更新。
- **frontend**(7a2f0a7da + selector 改修): `AttachmentsSelector.svelte`(ModelEditor に挿入、
  model.meta.attachmentIds 読み書き)/ `uploadFile(token,file,modelId)` / MessageInput が
  selectedModelIds[0] を渡す。**単一選択(None + attachment 一覧の radio、横並び・英語ラベル)**:
  content を生成するルータなので複数は上書き衝突。入力欄での per-chat トグルは持たない。builtin が無いので
  候補は prism authored のみ(fork 単体では None だけ)。

**済(フェーズ3 = 非同期 + 抽出状態 + プレビュー)**:
- **非同期化 + 契約確定**(6ce87c02b): Attachment 契約 = `process(file, ...) -> str`(成功=抽出テキスト
  md を返す / 失敗=raise、core が `content=str(e)` `status=failed` を差し込む)。content/status の永続化は
  core が行い attachment は DB を触らない純関数。upload_file/dispatch を async に(docling 等の同期ハンドラは
  run_in_threadpool にオフロード、event loop を塞がない)。
- **抽出状態3種をモデルへ注入**: `file.data.status`(success/failed/未設定=未抽出)を get_sources_from_files
  → middleware の `<source>` タグに file_id + `extraction="failed"/"none"` として載せる(未抽出は file_id 告知)。
- **フロント**: FileItemModal を刷新(add9b32a0)— 選択的検索トグル撤去、コンテンツ=md コード/プレビュー=
  md レンダリング、ヘッダ文言、状態3種表示。Function 作成 UI の type 選択に Attachment 追加(da5877b67)。

**済(RAG config teardown + 受け入れゲート)**:
- **RAG 固有 config 撤去**(e802e3c5a + Documents 779075938): `RAG_TOP_K`/`TOP_K_RERANKER`/
  `RELEVANCE_THRESHOLD`/`ENABLE_RAG_HYBRID_SEARCH`/`RAG_FULL_CONTEXT`/`BYPASS_EMBEDDING_AND_RETRIEVAL` を
  config・main 配線・/retrieval/config・middleware・Documents UI から一掃。BYPASS の起動時 embedding/reranking
  ロードスキップは無条件化(起動時のモデル外部 DL を排除)。~~**温存**: RAG_TEMPLATE / embedding・vector・EMBEDDING_FUNCTION
  (memories #8)/ CHUNK・docling・tika / PDF_EXTRACT_IMAGES~~ → **改訂(2026-07-08 第2セッション)で全撤去**
  (下記「## RAG/KB 完全撤去」参照)。強制全文(context=="full")分岐も撤去。
- **① 受け入れゲート**(c0654415d): upload 入口(Attachment 前)で server-side に拡張子 whitelist +
  サイズ上限を強制。config を **`ATTACHMENT_MAX_SIZE_MB`(既定 25MB)/ `ATTACHMENT_MAX_COUNT` /
  `ATTACHMENT_ALLOWED_EXTENSIONS`(空=全許可)** に統一(旧 RAG_FILE_MAX_* を廃止、`attachment.*` 永続キー)。
  SANDBOX_FILE_LIMIT_BYTES とは別 config だが 25MB で揃える運用。
- **dead 掃除**(f5e4a2583): Documents の reranking scaffolding + 未使用 import を除去。

**残り**:
- **fork 側**: ~~STT・TTS 内部関数(→ **#53**、後回し)~~ → **完了(2026-07-13、`ef116ef6e`)**:
  `utils/audio.py::transcribe_audio / synthesize_speech`。sync 提供(主 caller = sync tool、
  async からは to_thread — #28 パターン)。TTS は config 既定 + 引数 voice/model 上書き
  (en/ja→voice の対応表は caller=prism 層 tool の責務、fork にプリセット config は持たない)、
  キャッシュキーに voice を含む形へ是正し `/audio/speech` openai 分岐も同関数へ載せ替え。
  STT は file_id 入力を `data.content` に冪等キャッシュ(attachment core と同型・
  engine を変えても再文字起こししない割り切り)、path 入力は都度実行(#56 のチャンク呼び対応)。
  tool 合成音声のチャット上の再生 UI はスコープ外(必要になったら別 issue)。
  ~~`/retrieval/reranking`/`RAG_RERANKING_MODEL`~~ → 撤去済み(掃除8a)。
- **FileItemModal の実機確認(次回最優先)**: クリック→モーダルが出ない件で、FileItem の発火条件を
  `data.content` 依存から `data` 有りに修正済み(98652bebe、未抽出/失敗も開くように)。ただし content 有りでも
  出ないなら Markdown 描画等の runtime 問題の可能性 → **実機描画を目視確認**(ブラウザ自動化のファイル添付
  制約で自動取得は保留、dev は 0.0.0.0 + `enda-personal` で実機アクセス可)。
- **prism 層(fork の仕事でない)**: ③gate(`large_file_gate`)/ PDF→VLM(`pdf_vision_router`)。
  ~~④sandbox tool(`import_chat_file`)~~ → **2026-09-03(#114)に fork 本体の組み込みへ移設**。
- ~~**KB 撤去**: 根が深く据え置き~~ → **完了(2026-07-08 第2セッション、下記参照)**。
- **§未確定**: ③ で画像・音声を予算に数えるか(prism gate)。① 上限は 25MB で確定・全文強制トグルは撤去確定。

## RAG/KB 完全撤去(2026-07-08 第2セッション、`aeed2aad4`〜`5b8cf563f`)

前セッションで「温存/据え置き」にした RAG/KB 一式を**外側(フロント)→内側(config)へ層別に完全撤去**。
目標は「Attachment(docling 直叩き)が動く薄いチャット基盤」。設計判断:

- **docling 抽出は fork から出す**: `retrieval/extract.py::extract_file_text` と `loaders/main.py`
  (Loader/DoclingLoader/TikaLoader)を撤去。抽出は**案件層(prism)の Attachment Function が docling を
  直接叩く**方式に一本化(prism 実体: `owui/attachments/docling_extract.py`。`Storage.get_file` +
  `POST /v1/convert/file`、do_ocr=false 等は Valves)。→ **`CONTENT_EXTRACTION_ENGINE`/`DOCLING_SERVER_URL`/
  `TIKA`/`DOCUMENT_INTELLIGENCE`/`MISTRAL_OCR`/`PDF_EXTRACT_IMAGES`/`CHUNK_*`/`TEXT_SPLITTER` を config から撤去**。
- **Attachment 契約に第3の帰結 `None=skip`**(`7f974a54b`): 対象外(拡張子 whitelist 外)は raise でなく
  None を返し「未抽出(none)= Attachment 未割り当てと同じ」扱い。dispatch は None で何も永続化せず次へ continue。
- **KB 完全撤去**(Layer1-4): workspace KB 画面/route・チャット `#` ピッカー・モデル KB 割当・`knowledge`
  store・web/youtube 取り込み・apis/knowledge・**knowledge router(HTTP endpoint 12個)**を撤去。
  **温存境界(#40 CORE)**: `models/knowledge.py`(Knowledges)は `has_read_access_to_collection` + IDOR test が
  使うため残す。`middleware.filter_files_by_user_access` も温存。
- **embedding/vector 基盤撤去**(掃除8): **memory を素の SQL ストアに降格**(`routers/memories.py` から
  VECTOR_DB_CLIENT/EMBEDDING_FUNCTION を除去、`/query` は全 memory を自然言語で返す=Claude 方式。テーブル/
  content/UI は温存)。これで基盤の最後の消費者が消え、`retrieval/vector/` パッケージ丸ごと・
  `EMBEDDING_FUNCTION` 起動時生成・`RAG_EMBEDDING_*`/`VECTOR_DB`/接続キー を撤去(これで
  SentenceTransformers の起動時ロード・外部 DL も消滅)。
- **RAG_TEMPLATE(包む機構)撤去**(掃除11): middleware で `<source>` context を `rag_template()` で包まず
  **そのまま注入**。指示(引用ルール等)が要れば preset system prompt(prism)が持つ。→ **config.py の RAG_ 系ゼロ**。
- **web 検索は tool 用に温存、RAG 結合のみ解除**(`08ecc904c`): middleware の `chat_web_search_handler`
  (自動検索→RAG 注入)と `/api/config` の `enable_web_search` feature を撤去(フロント built-in トグルは
  gate で自動的に隠れる)。検索 API(`/process/web/search`・`search_web`・エンジン・`WEB_SEARCH_*` config)は
  残し、将来 prism の検索 tool が内部検索 API として叩く。query-generation は撤去(掃除9)。
- **`ENABLE_RAG_LOCAL_WEB_FETCH` → `WEB_LOADER_ALLOW_LOCAL_FETCH` 改名**(`c51fb15a7`): 実体は web loader の
  SSRF ガード(localhost/内部 IP fetch を既定拒否)で RAG 無関係。config から "RAG" の名前も一掃。
- **検証**: app 起動 OK / バックエンド全 **214 passed**(#40 IDOR・attachment・gate 含む)/ CI 緑 /
  **添付 chat 注入 E2E**(docling 抽出→生 `<source>` 注入→モデルが marker 正答)+ memory 素ストア 実機確認。

### 残り(第2セッション後)
- ~~**フロントの inert web 検索 UI**~~ → **撤去済み(2026-07-12、`c75711212`)**: `webSearchEnabled` 状態/binding・
  Web Search トグルボタン・`WebSearchResults.svelte`・Interface `toggleWebSearch`・chat payload の
  `features.web_search`・`web_search_results` ファイル収集・Config 型 `enable_web_search` を Chat/MessageInput/
  Placeholder/ResponseMessage/Interface/stores から削除。**温存**: `features.web_search` 権限フラグ(admin Groups UI +
  backend config)。検索 API 本体(`ENABLE_WEB_SEARCH`・エンジン config)は #51 用に温存。
  - **追撃(2026-07-12)**: `features.web_search` **権限フラグも撤去**(web 検索は tool が担い、
    ゲートは tool の read ACL で足りるので専用フラグは冗長)。`config.py` の env/DEFAULT_USER_PERMISSIONS・
    `users.py` の `FeaturesPermissions`・admin Groups UI(Permissions/Groups/EditGroupModal)から削除。
    ※ **#51 は最終的に「fork 組み込みツール」案を撤回し prism tool 直叩きに確定**(2026-07-13、`eae47a4bd`)。
    fork の検索素材(WEB_SEARCH_* config・`retrieval/web/` providers)も全撤去済み。
    併せて `middleware.py::_file_entry_is_readable` の `file_type in ("web_search","text")` 死に分岐も撤去
    (末尾 `return True` の fallthrough と冗長・両型とも生成されない)。IDOR テストの web_search 例は汎用インライン項目へ。
  - 残(軽微・後続): orphan i18n キー("Web Search" 等)は end-game i18n(#34)で一括 prune。
- **prism 層**: web 検索 tool(**改訂 2026-07-13、#51**: fork の内部検索 API 案は撤回 — SearXNG 等の外部検索システムを Valves 設定で直叩き。fork の WEB_SEARCH_* config / retrieval/web providers は全撤去 `eae47a4bd`)/ ③gate / PDF→VLM。~~/ ④sandbox tool~~
  → **④ は 2026-09-03(#114)に fork 本体の組み込み `builtin_sandbox` へ移設**。

## 改訂(2026-07-12、#69 ファイル管理機構)

#69 実装に伴う本メモ設計からの変更点(真実源はコード + `chat-share-files.md` の #69 節):

1. **per-model attachmentIds 廃止 → グローバル config `attachment.function_id`**(`ATTACHMENT_FUNCTION_ID`、
   RuntimeConfig)。admin が `/configs/attachment` で type=attachment の Function を 1 つ選ぶ(""=未設定で
   発火なし)。upload endpoint の `model_id` Query・ModelEditor の AttachmentsSelector は撤去。
   model.meta 内の残留 `attachmentIds` は誰も読まない(migration なし・無害)。
2. **フック契約に `__event__` 注入を追加**: `"upload"`(新規アップロード)| `"attach"`(既存ファイルの
   チャット再添付 = `POST /files/{id}/process`)。attach で None を返せば既存 data.content 温存
   (再抽出しない判断はフック側)。未宣言の既存フックは従来どおり。
3. **画像も files 経由に統一**(base64 直入れ廃止): 送信 payload の内部 URL
   `/api/v1/files/{id}/content` を `utils/file_images.py::inline_internal_image_urls` が
   process_chat_payload 末尾で base64 化(推論エンジンから WebUI 内部 URL への到達・認証は保証されないため)。
4. ファイル管理面(`/files` ページ・共有フル編集・accessed_at)は `chat-share-files.md` #69 節を参照。
5. **運用注意**: 画像がアップロード経路(#52 受け入れゲート)に乗ったため、
   `ATTACHMENT_ALLOWED_EXTENSIONS` を文書系だけの whitelist で運用している環境では
   **画像拡張子(png/jpg/jpeg/gif/webp/avif)を whitelist に足さないと画像添付が全滅する**
   (既定は空 = 全許可なので影響なし)。prism 側の設定確認を #69 出荷時に行うこと。
6. `chat.file_upload` のサーバー側 enforcement は #69 のレビューで upload endpoint に実装
   (それまで UI ミラーのみ)。生成画像の内部保存(`store_uploaded_file`)はこのゲートを通らない。

## 改訂(2026-07-21、#89 tool→VLM 画像注入の整理)

真実源はコード(`#90` = `7b4f9aac7`〜`688d584f9`、master 統合済み)+ issue #89。
`results[].files` が「VLM 再注入(モデルの目)」と「人間向け inline 表示」を型無しで
二役兼ねていた混同を解体した。

1. **無差別 data: magic 廃止**: 旧実装は「tool 戻り値が list なら中の `data:` 文字列を
   全部 `results[].files` に抽出 → `data:image/` だけ再注入」という発火条件が緩い magic
   だった(view_image 専用なのに任意 tool が化ける・非画像 data URL は静かに脱落)。撤去。
2. **型付き汎用 emitter `__feed_image__(file_id)`**(`utils/tool_output.py`): どの tool でも
   実行中にモデルの目へ画素を push できる注入ヘルパ。tool-call ごとの ContextVar sink
   (`ToolOutputSink`、lock 付き)に蓄積し、`execute_tool_calls` が **成功時のみ** harvest して
   `results[i].model_images`(file_id 配列)へ載せる。戻り値をハイジャックしない。
   `#88` の `__display__`(人間の目)と対称の設計([[pr88-display-next]])。
3. **base64-print 廃止・file_id 参照統一**: `content_blocks.convert_content_blocks_to_messages`
   が `model_images` の file_id から `/api/v1/files/{id}/content` の image_url を組む。実体化
   (base64)は **送信直前 `inline_internal_image_urls` の 1 回のみ**(#69 の入力画像と同じ機構)。
   DB スナップショット・戻り境界に base64 を載せない(snapshot 肥大 ~10MB/turn を解消)。
4. **要石**: tool ループの追撃リクエストにも `inline_internal_image_urls` を適用。従来 :541 の
   `form_data["messages"]` にしか掛からず、file_id 参照化した追撃分の画素が届かなかった
   (この 1 行が無いと VLM に画素が渡らない silent 失敗)。
5. **`feed_image` はプリミティブ**: content_type / サイズを検証しない(file_id しか持たず、
   ここで Files 参照すると async tool 実行中にイベントループを塞ぐ)。画像判定・上限は
   **呼び出し側 tool の責務**(view_image が image/* と max_image_mb を担保)。認可は送信直前
   inline の `has_read_access_to_file` が別途担保するので push 自体は認可しない。
6. **view_image を builtin 化**(`nucllei/builtin_tools/view_image.py`、seed の custom 撤去)。
   あわせて owner チェック欠落の **IDOR を封鎖**(`721fb768d`、#40 と同クラス。file_id 直指定で
   他ユーザー画像を覗けた)。詳細は [[builtin-tools]]。
7. **人間 inline は #88 へ**: `ToolCallBlock` の `results[].files` 描画は撤去。当面は
   `message.files` ギャラリー(sandbox 成果物)、恒久は #88 display チャネルが担う。
8. prism 側(Q4 sandbox 命名 `export_to_workspace`/`import_from_workspace`、prism custom
   view_image 撤去)は prism-hu/llens#3(draft)+ #90 デプロイ後の follow-up。
   ※ 命名は最終的に **`send_to_workspace` / `fetch_from_workspace`** に着地し、
   実体は #114 で nucllei の組み込みへ移った(下の 2026-09-03 節)。

## 改訂(2026-09-03、#114)— ④sandbox tool は fork 本体へ(判断の反転)

本メモは複数箇所で「**④ sandbox tool は prism 層。fork には作らない**」と書いていた
(2026-07-08 の線引き)。**この判断は覆った**。sandbox はランタイム(nsjail イメージ)も
tool も nucllei 製品側に移り、tool は env ゲート付きの組み込み **`builtin_sandbox`**
(`nucllei/builtin_tools/sandbox.py`)になった。一次根拠は issue #114、詳細は [[builtin-tools]]。

反転の理由(③gate と何が違ったか):

- **sandbox に院内固有物が一つも無い**。③gate(合算予算・除去ポリシー)や PDF→VLM は
  「その現場でどう運用するか」の塊で案件層のままが正しい。sandbox はコード実行という
  汎用機能で、LLENS の知識をコードに一つも含んでいなかった。
- **依存の向きが逆だった**。llens 側の tool は `nucllei.models.chats` / `models.files` /
  `storage.provider` / `utils.tool_render` / `utils.tool_output`(= 本メモが扱う
  ファイル経路の内部 API そのもの)を掴んでおり、`#88 未対応の旧 nucllei で display が
  無くても…` という try/except で**ホストの内部 API にバージョン交渉していた**。
  「fork は仕組みだけ持つ」の裏返しで、④ は仕組みの利用者ではなく**仕組みと同じ寿命の部品**
  だった。実際 #88(display/feed チャネル)を入れるたび配備層 tool の追従が要っていた。
- **出荷単位が境界**。sandbox サービスとの HTTP 契約(`/exec` `/exec_cell` `/files/*`)が
  repo を跨ぐと、どちらが先に出るか分からない。同じ repo・同じ tag なら交渉が要らない。

**変わらない部分**: ③gate・PDF→VLM は prism 層のまま。sandbox でも
「立てるか / quota / 開放範囲 / system prompt」は配備層(llens)の判断で、nucllei が持つのは
image とその中身の設計 + LLM 向け表面だけ。`SANDBOX_URL` 未設定の配備では tool 自体が生えない。

**追記(2026-10-03)**: 同じ反転が **`feed_image` / `fetch_url`** にも起きた。2026-07-22 に
prism tool へ寄せた `feed_image`(上の #89 節 6 の view_image の後継)と、院内 Web を 1 ページずつ
読む `fetch_url` は、組み込み **`builtin_feed_image`** / **`builtin_fetch_url`**(どちらも常時。
fetch_url は移設時 `EXT_UPSTREAMS` / `EXT_PUBLIC_BASES` / `EXT_INTRANET_CIDRS` のゲート付き
だったが、同日の ken 決定「`url=` は http(s) URL ならどこでも取りに行く — 断るのはこのサーバ
自身だけ、`EXT_INTRANET_CIDRS` は撤去」でゲートも外した。[[builtin-tools]]「一般の URL」)に
なった。理由は sandbox と同型 — 参照の解決・登録した配信元・サイズ上限の規則は全部
`utils/image_refs.py` にあり、llens の tool は殻だけで、殻が repo を跨ぐと ERROR 文が 3 コピー
(feed_image / fetch_url / send_to_workspace)に割れた。文面は `utils/ref_errors.py` 1 本に統合
(文面は不変)。詳細は [[builtin-tools]]「改訂(2026-10-03、feed_image / fetch_url の builtin 化)」。
#89 節 5 の「画像判定・上限は呼び出し側 tool の責務」は builtin_feed_image が担う(valve
`max_image_mb` / `max_pages`)。③gate・PDF→VLM(filter)は引き続き prism 層。

## 改訂(2026-10-03 夕)— `/ext` は nucllei の `/mnt` をそのまま配信(設定無し)

ken 決定: **「nucllei の `/mnt` に volume したものをそのまま `/ext/` で配信する」**。`EXT_UPSTREAMS` /
`EXT_FILE_SCOPES` / `EXT_PUBLIC_BASES`(と同日に一度置いた `EXT_DIRS`)は撤去。`/ext/{rest}` ⇔
`/mnt/{rest}`(`/mnt/cage/static/X` ⇔ `/ext/cage/static/X`。URL の形は不変)。利用者に渡したく
ない物は nucllei 側の `/mnt` に mount しない(sandbox にだけ bind すればモデルは読めるが配信
されない)— 「配信範囲の外は添付もしない」(`mounted_path_withheld`)の仕組みは不要になり撤去。
本メモの ④ の「`/mnt` 配下に実体がある物はコピーせずパスを返す」は変わらない(nucllei 側に
実在すれば、の条件が付く)。改名: `EXT_SELF_ADDRESSES` → `SELF_ADDRESSES`、`EXT_INLINE_MAX_BYTES` →
`FETCH_MAX_BYTES`。詳細は [[builtin-tools]]「改訂(2026-10-03 夕)」、正本は `README_API.md` §3.8。

## 改訂(2026-10-03 夜)— 配信元は `EXT_DIR`、sandbox の置き場は `/ext`(`/mnt` を捨てる)

ken 指摘: 上の「nucllei の `/mnt` をそのまま配信」は **host 直起動を忘れていた**。nucllei は
docker 専用ではなく(dev は host で動く)、固定の `/mnt` だと (a) host 直起動で置くのに root が
要り、(b) host の `/mnt`(NFS 等)がログイン済み全員に黙って配信される。改めて:

- **nucllei の配信元 = `EXT_DIR`**(`image_refs.EXT_DIR`。image では `/ext`、host では
  `./ext`。起動時に mkdir)。Docker は volume を `/ext/<path>` に bind、host 直起動は
  `ext/` にファイルか symlink を置く(dev: `ext/cage/static → <llens>/cage/data/static`、
  [[dev-environment]])。`ext/` の木に置いた**ディレクトリ symlink は mount 扱い**で先が配信
  される(`served_mount`。mount の中身の symlink は外へ出られず、ファイルの symlink は mount に
  ならない)。nucllei 自身の `/mnt` も `/ext` も見ない。
- **sandbox の read-only 置き場も `/mnt` → `/ext`**(同日 ken 決定): sandbox のパス `/ext/cage/static/X`
  = URL `/ext/cage/static/X` = nucllei の `ext/cage/static/X`。モデルは読み替えを要らない。
  `/ext/…` は `url=` でも `path=` でも同じファイル(唯一の意図した重なり。url は percent-decode、
  path はそのまま読む — 引数名は読み方だけを決める)。`/mnt/…` は特別扱いしない(他の `/…` と同じ
  `unsupported_form`)。橋渡し `mounted_path_for_url` / `url_for_mounted_path` は文字列としては恒等で、
  `EXT_DIR/X` の実在確認だけ(日本語名は URL 側が percent-encode)。
- 正本は `README_API.md` §3.8。詳細は [[builtin-tools]]「改訂(2026-10-03 夜)」。

## 改訂(2026-10-04)— `/ext` のファイルは URL で見せるだけ(取り出しの経路は作らない)

ken 決定: **配備は nucllei と sandbox に同じ `/ext` の組を mount する**(llens は
`cage/data/doc:/ext/cage/doc:ro` を nucllei 側にも足す)。だから sandbox に見える `/ext` の
ファイルには必ず URL がある。`/ext` のファイルは URL で見せるだけで、生のファイル(コーパスの
md 等)を添付して要らない file_id を増やさない — sandbox 経由で読み出して添付する経路は
作らない(2026-10-04 に一度検討して取りやめ)。

- `fetch_from_workspace(path="/ext/X")` / `feed_image(path="/ext/X")`: nucllei の `EXT_DIR/X` に
  あれば URL(従来どおり)。無ければ本当に無いか mount のずれで、中立の 1 文
  「{path} はこのサーバの /ext に無い (存在しないか、sandbox にだけ mount されている)。
  次の手: 中身が要るなら sandbox で読んで使う。」— 「渡さない」とも「/workspace にコピーして
  渡す」とも言わない(resolver は「無い」と「sandbox にだけ在る」を分けられない)。
- 同日、tool がモデルに返す文を「事実の文 + `次の手:`」の形に揃えた(README_API §5.1)。
  添付の報告は、画像・音声・動画なら「ユーザーの画面には既に表示されている — 本文に貼り直さない」、
  それ以外(チャットのファイル一覧にだけ載る)は「本文で示すなら [名前](/api/v1/files/{id}/content)」。
- 正本は `README_API.md` §3.8 / §5.7。

## 改訂(2026-10-05)— リンクによる簡易引用(#146 の簡易版):本文の `[/ext/…]` / `[file_id]` をリンクに

ken 決定(2026-10-05): 引用(出典表示)は一旦撤去したが(#142 / #146)、**リンクで原典に飛べる
軽い引用は残す**。

- **FE: 本文の `[/ext/…]` と `[file_id]` をリンクにする**(`utils/markdown-it/index.ts` の inline rule
  `bracket_ref`)。角括弧の中身が `/ext/` で始まるパス(fragment 可。表示は末尾の要素 + ` › 見出し`、
  リンク先はそのまま)か、**そのチャットに実在する添付の** file_id(UUID 形。表示はファイル名、リンク先
  `/api/v1/files/{id}/content`)のときだけ。`[1]` `[580]` `[site-a]`・直後が `(` `[` `:` のもの
  (`[ラベル](url)` / `[a][b]` / 参照リンク定義)・コード内は巻き込まない。実在判定は `lex(src, env)` の
  `env.fileNames`(id → 名前)— `ContentRenderer` が history の全メッセージの `files` から組んで
  `Markdown` の `fileNames` prop で渡す(添付の集合が変わると確定ブロックを lex し直す)。リンクは
  既存の本文リンクと同じく新しいタブ。dev の vite proxy に `^/ext(/|$)` を足した(dev では `/ext` が
  SPA に落ちていた)。
- `/ext` 側: doc の md の HTML 化(見出しアンカー・パンくず・一覧)は **cage が起動時にビルドして出力**し、
  nucllei は静的配信だけ(ken 決定 2026-10-05、proxy は作らない。一度 nucllei 側で md を描画する
  実装を書いたが捨てた)。nucllei の `/ext` に入れたのは汎用の **URL の契約** 2 点
  (`routers/ext.py`、正本 `README_API.md` §3.8):
  - **拡張子の補完**: `/ext/X` が実在のファイルならそのまま、無いかディレクトリなら `X.html` →
    `X/index.html`、どれも無ければ 404。どの候補も mount の判定で閉じる。一覧は出さない
    (一覧は cage が index.html として出力する)。
  - **`.html` / `.htm` は `text/html; charset=utf-8` で inline、必ず厳しい CSP**
    (`default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none';
    form-action 'none'; frame-ancestors 'self'`)+ nosniff。従来の「HTML は添付扱い」をここで替えた
    (stored XSS は CSP で止める。SVG / JS は従来どおり添付)。`CONTENT_SECURITY_POLICY` env を設定した
    配備ではグローバルの middleware がこの CSP を上書きするので注意。
  - `/ext/cage/doc` + doc の住所は cage がビルドした HTML のページになる。中身は配備側が置く物。
  - `.md` / `.txt` は従来どおり text/plain(Accept による分岐はしない)。

## 改訂(2026-10-03)— 音声入力の通し録音と `meta.dictation`

マイクの音声入力は、区切りが 2 つ以上あった録音を 1 本(長ければ約 60 分ごとに複数本)の音声と
して、その発言の**普通の添付**に残す(経路・チップ・削除の意味論は他の添付と同じ)。
詳細は `audio.md`「通し録音の添付」、契約の正本は `README_API.md` §4.4。本メモに関わる点:

- **`Files.meta` にクライアントが書けるキーは `dictation` だけ**。upload の Form `dictation`
  (JSON)を `routers/files.py::DictationMeta` が検証して受ける(未知のキー拒否・512KB まで・
  `audio/*` のみ)。任意の meta を受ける口は作っていない。
- `<source>` タグは `utils/middleware.py::build_source_context` に切り出した。属性は
  `id` / `file_id` / `dictation="true"`(+ 分かれた録音だけ `part` / `parts`)/ `extraction`。
  音声はふつう未抽出なので `extraction="none"` + file_id 告知のまま、印だけが増える。
- Attachment フックは他の添付と同じく発火する(`__event__="upload"`)。音声をどう扱うかは
  配備の Attachment 次第。
- 添付チップのモーダルは `audio/*` をその場で再生できる。

## 参照
- prism 実装: `_links/llens` の `owui/filters/large_file_gate.py`・`pdf_vision_router.py`。
  ※ **sandbox は prism 側に無い**(#114 で移設): tool = `nucllei/builtin_tools/sandbox.py`、
  ランタイムと設計文書 = 本 repo の `sandbox/` と `sandbox/README.md`。
  ※ **`feed_image` / `fetch_url` も prism 側に無い**(2026-10-03 で移設): 組み込み
  `builtin_feed_image` / `builtin_fetch_url`(`nucllei/builtin_tools/`)。
- 現状 fork のフロー(2026-07-08 第2セッション後): upload=`routers/files.py` → `utils/attachment.py`
  (Attachment dispatch) / 抽出=**prism の Attachment Function が docling 直叩き**(fork の
  `process_file`/`extract_file_text` は撤去済み) / 注入=`utils/middleware.py::chat_completion_files_handler`
  + `retrieval/utils.py::get_sources_from_files`(`<source>` タグを RAG_TEMPLATE で包まず直注入)。
- 関連: #18(builtin tool)/ #46(per-model filter)/ #13(Filter ミドルウェア化)/ #40(file IDOR)/ #41(clone・別軸)。

## 確定(2026-07-08 追記)
- **プロバイダの実体**: 単一 `extract()` に一本化しない。**種別ごとに素直な内部API を1本ずつ**(文書抽出=file→文字列 / STT=音声→文字列 / TTS=テキスト→音声)。入出力も追加オプションも種別で違うので揃えない。engine URL か model 接続かは各種別の内部で吸収(種別をまたいでは吸収しない)。入口は「設定で登録したエンジン」に対応。**内部関数として提示 → hook/filter から呼べる + builtin tool(#18)の実装からも呼べる**(tool として直接露出ではなく tool のコードが叩く=モデルは tool 経由で使う)。呼び分けは hook がルータとして担う。

## 未確定
- **未抽出時の送信挙動**: extracting 中に送信されたら待たせるか、即 raw 扱いで ④ に倒すか。
- ① 受け入れ上限サイズの確定値(`SANDBOX_FILE_LIMIT_BYTES` 準拠案)。
- ③ で画像/音声のトークンを注入予算に数えるか(v1 はテキスト産出分のみ gate 対象で可)。
- 手動「全文強制」トグル(prism `file.context=="full"`)を残すか gate 一本にするか。
