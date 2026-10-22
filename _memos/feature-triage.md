# 機能別トリアージ(fork.md §5 DROP の詳細化)

2026-07-04 決定。fork.md の 4 区分を機能単位に落とした一次判断。矛盾があれば fork.md が優先。

| 機能 | 判断 | 備考 |
|---|---|---|
| ナレッジベース(組み込み) | **不要・撤去済み(#48 ナビ / #52 で完全撤去)** | 独自実装が別にあるため。#48 で Workspace ナビから撤去。**改訂(2026-07-08 #52 第2セッション): route/backend も完全撤去**(workspace KB 画面・チャット `#` ピッカー・モデル KB 割当・knowledge store・knowledge router 全 endpoint)。~~温存は `models/knowledge.py`(Knowledges)のみ(access_control #40 / IDOR test 結線)~~ → **再改訂(2026-07-10 #41): `models/knowledge.py` も撤去・knowledge テーブル drop(migration a7c9e2f4b8d1)。KB 残骸ゼロ** |
| Documents(文書抽出) | **Docling が使えれば十分(実体は prism)+ docling 非依存の素の md 化は markitdown(fork 同梱)** | ~~v0.6.5 で対応済み(`CONTENT_EXTRACTION_ENGINE=docling`)~~ → **改訂(2026-07-08 #52): fork の抽出 preset(`CONTENT_EXTRACTION_ENGINE`/外部エンジン config)は撤去。docling 抽出は案件層(prism)の Attachment Function が直接叩く(`owui/attachments/docling_extract.py`)。fork は「仕組み」だけ** → **再改訂(2026-07-14): docling は遅いので、docling 非依存の軽い md 化に `markitdown[all]` を fork へ同梱。fork は抽出コードを持たず(一度 v0.6.5 langchain ローダ版を `utils/extraction.py` に復元したが markitdown の劣化再実装のため撤回・削除)、prism の Attachment が `from markitdown import MarkItDown` で直接呼ぶ。xlsx→シート別 CSV 等の型別ポリシーも prism が手前で処理。langchain-community/unstructured/networkx/nltk/pypdf/docx2txt/ftfy は撤去(langchain-core のみ tools.py 用に残置)。詳細 `_memos/file-handling.md`** |
| RAG / retrieval | **一切不使用・撤去済み(#52)** | 将来必要になってもツール側で実装。**2026-07-08: embedding/vector/reranking/chunk/RAG_TEMPLATE を config から完全排除** |
| Web 検索 | **残す(prism tool → 外部検索システム直叩き)** | 改訂(2026-07-13、#51 close): 当初の「fork 組み込みツール」案を撤回 — docling と同じ線引きで **fork は何も持たず**、prism の tool が SearXNG 等を Valves 設定で直接叩く。fork の WEB_SEARCH_* config・providers・管理画面は全撤去(`eae47a4bd`)。閉域本体では成立しない機能(メタ検索も外向き通信必須)= 非閉域の横展開先向け |
| 組み込みツール群(upstream の builtin) | **不要** | 簡単に再現可能。必要なものは自前ツールで。※fork 自前の組み込み tool 機構(`nucllei/builtin_tools/`、制御はモデル設定)は別途導入 — 詳細 `builtin-tools.md`(#18-3)。**追記(2026-09-03、#114)**: 組み込みは 3 本 + env ゲート付きの `builtin_sandbox`(`SANDBOX_URL` がある配備でだけ生える)。**追記(2026-10-03)**: llens の `feed_image` / `fetch_url` も組み込みへ — 無条件 5 本(+ `builtin_feed_image` / `builtin_fetch_url` — fetch_url は同日中に env ゲートを外した: `url=` は http(s) URL ならどこでも取りに行く、`EXT_INTRANET_CIDRS` は撤去)、env ゲート付きは `builtin_sandbox` の 1 本のまま |
| コード実行(組み込み Pyodide/Jupyter) | **撤去済み(#23)。実行系は nucllei が出荷する sandbox tool 一本** | 改訂(2026-07-14、#23): code_interpreter(タグ検出による tool 外コード実行)を機能ごと撤去 — config・permission・UI トグル・実行ループすべて。コード実行は nsjail+IPython の自前 tool 一本で、tool 経路以外の実行裏道は残さない。Pyodide には戻さない(fork.md §6)。**再改訂(2026-09-03、#114)**: その「自前 tool」は配備層 llens のカスタム tool だったが、**ランタイム(`sandbox/` → `ghcr.io/technoplasm/nucllei-sandbox`)も tool(組み込み `builtin_sandbox`)も nucllei 側へ移設**した。fork が「仕組みだけ持ち実体は案件層」という他機能(Web 検索・docling)とは**逆の線引き**になるが、根拠は同じ出荷単位基準 — sandbox には院内固有物が無く、tool が本体の内部 API に密結合していた(`builtin-tools.md` の #114 改訂節)。**配備の判断(立てるか / quota / 開放範囲 / prompt)は引き続き案件層** |
| 会話検索 | **必要・維持 + サイドバーに露出(#121)** | v0.6.5 に存在(タイトル+本文検索、`GET /chats/search`)。デグレさせない。**追記(2026-10-03、#121 ken 決定)**: サイドバーのチャット一覧直上に検索ボックス(`Sidebar/ChatSearch.svelte`、300ms デバウンスで同 API を叩き、一覧を結果で差し替える。ピン留め/日付見出し/無限スクロールは結果一覧に出さない)+「チャットで調べる」ボタン。後者は `/?draft=<文面>` で新規チャットを開き、入力欄に「『<語>』でチャット履歴を検索して」をプリフィルするだけ(送信しない。`?q=` は自動送信なので別パラメータ)— モデルが組み込みの `list_history` / `grep_history` で探す |
| メモリー | **撤去済み(#67)** | ~~放置・無効化も削除も投資もしない~~ → 改訂(2026-07-08 #52): embedding/vector 基盤撤去の一環で「完全自然言語」ストアに降格(これが embedding/vector の最後の消費者。issue #8 は close)→ **再改訂(2026-07-10 #67): UI 撤去(#63)の残骸として router・モデル・テーブルごと撤去(migration c4e8a1f6d2b9)** |
| ノート | 無いままでよい | v0.6.5 に存在しない。追加しない |
| タスク / オートメーション / カレンダー | 無いままでよい | v0.6.5 に存在しない。追加しない |
| 画像生成 | **全撤去(2026-07-17 反転)** | 旧判定 KEEP-MINIMAL を反転。送信前フック + 外部エンジン設定(A1111/ComfyUI/OpenAI/Gemini)は tool 経路の脇の特別扱いパスで、tool 経由生成で足りる(`2894654da`)。モデル自身の multimodal 画像出力(assistant メッセージ内)は将来 content_blocks の image ブロック型で受ける(_memos/streaming-wire-v2.md) |
| プロンプト管理(`/` 呼び出し) | **撤去済み(#35)** | ~~あってもよいがスキルとの棲み分け再設計~~ → **改訂(2026-07-06 #35): 「`/` はスキル能動発火 UI に置き換える」案をそのまま実装 — workspace/picker を Skill に置換し prompts を全退役**(prompt テーブル drop = migration c2d9a7b41e05、74f0e5161) |
| ツール(Tools) | **完全キャッチアップ** | 外形仕様・公式ドキュメントベースで機能パリティを目指す(コード移植は不可) |
| スキル(Skills) | **完全キャッチアップ** | 同上。llens の権限モデル(診療科×職位)と統合(issue #5) |
| ストア(openwebui.com 共有・取得) | **撲滅済み(#10)** | openwebui.com への外部照会が前提の機能。community sharing(config / admin トグル / 共有ボタン / モデル discover / 作者リンク)を機能ごと撤去(773c2dead)。tools/functions/skills の共有導線は #45 で撤去済み |
| 評価 / Arena | **撲滅済み(#48)** | 邪魔なので削除。#48 で feedback(👍/👎)UI・frontend/backend API・DB テーブル(migration e3b1c9f4a7d2 で drop)、arena config/モデル注入/chat 経路の dead code、ENABLE_MESSAGE_RATING まで全撤去。admin Evaluations 画面も削除 |
| 管理画面のチャット分析 | **できれば詳細化** | ユーザー別の利用分析をもう少し詳しく。優先度低 |
| チャットの「コントロール」サイドバー | **削除** | Files/Valves/System Prompt 上書き/高度なパラメータの per-chat 制御。一般ユーザーが制御する必要がなく、変に触らせたくない(2026-07-07) |
| 設定 → 一般 の詳細パラメータ | **撤去** | 既定パラメータをユーザーに編集させない。制御はモデル(管理)側に集約 |
| 高度なパラメータ UI(`AdvancedParams`) | **モデル編集のみに残す・整形済み** | 上記 2 箇所を撤去した結果、実質モデル編集専用に。#47 で整形出荷(d63d08db1/010b19ab2): ラベル隣接化(2カラム固定にはしない)+ Behavior/Sampling/Mirostat/Ollama のセクション分け・(Ollama) 連呼撤去 |
| 本文中の引用チップ(`[n]` → `<source_id>`) | **撤去(2026-10-05 ken 決定、refs #142)** | 添付のあるメッセージで本文の `[1]`〜`[N]` を `<source_id>` に置換し、表示名の重複除去で番号を振ってチップ描画していた(`replaceTokens` の sourceIds・`ContentRenderer` の番号付け・`Markdown/Source.svelte`・`onSourceClick` 配管)。7/6 の markdown-it 移行(`07fb37e59`)以降 `source_id` が HTML タグとして通らず**生文字列のまま表示されて壊れていた**うえ、モデルに `[n]` で引用させる指示は nucllei にも llens にも無い(llens は地の文の出典 + 原本 PDF への markdown リンクで引用)。本文の `[1]` は素の文字列、`[1](url)` は普通のリンクとして描画する。BE の tool `metadata.citation`(誰も読まない)も撤去。残りは続く commit で撤去(次行) |
| 引用(出典表示)全般 | **一旦撤去(2026-10-05 ken 決定「引用のためだけにそこまで頑張れない。いつかちゃんとやるときに棚卸しする」、refs #142)** | 経緯と棚卸しリストは #146。撤去: メッセージ下の出典一覧(`Citations.svelte` + `CitationsModal`)、`message.sources` の表示・保存(BE の `{sources}` emit、FE の applyCompletionData / action 送信)、`source` / `citation` イベントの FE 処理(BE は素通しのまま、FE で未知の型として捨てる)、それにぶら下がっていた `code_execution` 表示(`CodeExecutions` + `CodeExecutionModal` — code_interpreter は #23 で撤去済みで出す者が居ない)、モデル能力 `citations`(トグル・既定値・型・i18n)。**残したもの**: 添付本文をモデルへ渡す BE の `<source id=.. file_id=..>` ブロック(`build_source_context` / `chat_completion_files_handler` — 引用ではなく添付を読ませる仕組みそのもの。llens の tool が id 属性を参照しうるので形は不変 → **2026-10-19 に `id` をやめて `name=`(ファイル名)に改めた**(llens 監査 (3)、llens の依頼「もう使っていない」— `builtin-tools.md`「モデル向け文面の監査」))。**リンクによる簡易引用は残す(#146 の簡易版、2026-10-05)**: 本文の `[/ext/…#見出し]` / `[添付の file_id]` を番号チップ + メッセージ下の出典一覧にする(同じページは同じ番号)— `_memos/file-handling.md`「改訂(2026-10-05)」 |

## 制御面の整理(2026-07-07 決定)

**方針**: 設定画面/モデル編集画面の整理の**前段**として「余計な機能の掃除」を置く。
一般ユーザーがチャット時にモデル挙動(system prompt・valves・サンプリング等)を制御する
必要はなく、変に触らせたくない → **制御はモデル(管理)側に集約**し、チャット側の per-chat
制御面は撤去する。

**掃除の対象(DROP)**:
- チャットの「コントロール」サイドバー(`Controls.svelte` = Files/Valves/System Prompt/Advanced Params)を
  まるごと撤去。右ペイン枠(`ChatControls`)は Overview/Artifacts/通話と共用なので枠は残し Controls 部分のみ外す。
  トグル(Navbar・Navbar/Menu)と `showControls` 経路も撤去。
- 設定 → 一般 の `AdvancedParams` ブロックを撤去。
- 結果 `AdvancedParams` はモデル編集専用に。共有部品のリプレースが不要になったため直接いじれる。
  - **改訂(2026-07-07, #47)**: 「2 カラム化」ではなく **ラベルと値の隣接化**(各行 `justify-between`→`items-center gap-2`)で
    「幅が広いとラベルと値が離れて見づらい」を解消(カラム数は問わない要件)。挙動系(Function Calling / Stream /
    Reasoning Effort)を最上部へ。さらに **Behavior / Sampling / Mirostat / Ollama のセクション分け**と各ラベルの
    `(Ollama)` suffix 撤去・コメントアウト済み Template ブロック除去(010b19ab2)。
  - **値コントロールの刷新(d78adeced/36115522d)**: 「Custom」テキスト → **✕(times)アイコン**(押すと Default に戻る)。
    値ありのとき `ラベル ✕ 数字 [幅固定スライダー]` を1行インライン(**100% 展開しない**・数字は左)。
    スライダー横の数字は **`field-sizing: content`(Chrome 前提)+ padding** で中身幅(固定 width をやめた)。
    箱型入力は `py-1`/`text-xs` にして **Default(24px)と値ありで行高を一定化**(切替でリストが跳ねない)。
  - **タブ切替の暗黙リセット(040ec990c)**: ModelEditor はタブ間で info/params を共有するため、別タブの
    「見えていない編集」も Save で保存される。→ **タブ切替時にロード直後の状態へ暗黙リセット**(名前/ID は常時
    ヘッダなので対象外)。onMount 末 `captureSnapshot()` / `switchTab()` で復元。モーダル確認は未採用(暗黙)。

## Function type と tool 制御面(2026-07-08 決定)

**tool のチャット単位切替を撤去(DROP、#54)**: 入力欄の tool ピッカー(`selectedToolIds`)を撤去し、
tool はモデルの割り当て(`meta.toolIds`)一本化。誠実エージェント方針(tool はモデルが持つ・
ユーザーが選ばない)+ #46 の per-model 一本化の tool 版。制御面の掃除(上節)の続き。
skill 由来 tool_ids(#35)は温存。
**実施済み(2026-07-12)**: フロント(トグル UI / `selectedToolIds` 配管 / `ToolServersModal` /
`?tools=` URL param)撤去 + **backend を `model.meta.toolIds` のサーバー側解決に変更**
(payload `tool_ids` は無視 — クライアント申告経路の廃止はセキュリティ強化でもある。4pcg の
read ACL 検証は #66 ゲートとして維持)。同時に **Capture(画面キャプチャ/カメラ)**
(secure context 必須で HTTP 配備では壊れており、Ctrl+V 画像ペーストで代替可)と
**Google Drive / OneDrive 統合**(config・picker utils・admin 設定とも端到端)も撤去。
撤去理由は「薄い業務基盤に不要」が一次根拠(air-gapped は補助 — 横展開先は非 air-gapped もあり得る)。

**Function type(Pipe / Filter / Action)の扱い**:
- **Filter** = 中核。自作するのはここが圧倒的(de-id / TTS 注入 / ログ #13、#52 の注入 gate・PDF ルーティング)。
- **Pipe** = 残置。固有価値=「セレクタに出る自作モデル」=モデルルーティング/複数モデル束ね/agent-as-model。
  llens では SGLang が OpenAI 互換で当面不要だが、将来の科別ルータ等の余地があり削除しない。
  ※ thinking effort / CoT の自動調整は Pipe ではなく Filter(inlet でパラメータ書換)の仕事。
- **Action** = 残置(既定で作らない)。固有価値は「**人間が主語の後付けトリガ = 応答に貼る永続ボタン**」のみ。
  モーダルや成果物生成は tool でも可能(tool にも `__event_call__` が渡る)なので、Action を選ぶ唯一の理由は
  「あえて人間をトリガにしたい」時。誠実エージェント方針ではほぼ vestigial・用途は未知数。機構は害がないので残す。

## 一覧(item)体裁の整理(A1、2026-07-07 出荷)

model / skill / tool / function の各ワークスペース一覧を整理(#47 の残 A1)。
**共通化(単一 item コンポーネント抽出)はしない**方針 — 各一覧は性格が違うので個別に寄せる。

- **共通**: `By {{name}}`(作成者)表示を全一覧から撤去。
- **Tools**(`Tools.svelte` + backend `get_tool_list`):
  - **組み込み / カスタムの 2 セクション化**。組み込みが有るときだけ両方に小見出し(無ければ無ヘッダ)。
    組み込みは編集/削除不可だが **visibility は一覧のアイコンから編集可**(個別に開かせない)。
  - 2 段目を description ではなく **tool が公開する関数名(specs)の羅列**に(#49「tool spec 露出」を一覧で回収)。
  - **visibility(access_control)を globe/lock アイコンでその場編集**: クリック → 既存 `AccessControlModal` →
    閉じたら list item を round-trip 保存。builtin も同経路(backend が builtin は access_control のみ反映)。
  - `TOOL` バッジ・name tooltip・By・**Valves**・… メニューを撤去。削除は **trash アイコン**のみ。
  - **backend(adb7a11e7)**: `get_tool_list` の response_model を `ToolUserResponse` → **`ToolUserModel`**
    (content/specs 込み)に。visibility の inline 編集で update に必要な content を追加 fetch なしで round-trip
    でき、specs から関数名も引ける。picker の `/`(get_tools)は `ToolUserResponse` のまま(content/specs 非公開)。
    **前提**: 「一覧に出る = 詳細を開ける」権限一致。list は write 権限者/admin にしか返らないのでソース露出は広がらない。
- **Skills**(`Skills.svelte`): 2 段目に **description を表示**。visibility アイコン編集(list は content 込みなので
  `updateSkillById` で直接 round-trip)。By・pencil・… メニュー撤去、削除は trash、行クリックで編集。
- **Models**(`Models.svelte`): 指示どおり**そのまま + By 撤去のみ**。By を消すと下段のメニュー/有効スイッチが
  下にズレたため、item を **flex-col の 2 段組 → 単一行**にして … メニューと有効トグルを名前と縦中央で右寄せに。
  アバター・… メニュー・有効スイッチ自体は従来どおり維持。
- **Functions**(admin `Functions.svelte`): 100% 幅の単一列を skill/tool と同じ **grid(横並び)**に。
  … メニュー(FunctionMenu)を **trash アイコン**に、行クリックで編集。type バッジ(filter/action/pipe)と Valves は維持。

出荷: adb7a11e7(backend)/ 4cdf56616(Tools)/ 0ea09c210(Skills)/ 8dfaedde3(Models)/ f4074705d(Functions)。
clean な Playwright で 4 画面 end-to-end 検証(visibility は builtin 含め Public↔Private 往復・永続化 PASS)。
これで **#47 の残は A1 のみだったため #47 close 可**(子 #49 の backend は別 issue で継続)。i18n locale 追従は後回し。

## Admin を Workspace に統合(#48、2026-07-07 出荷・close)

当初 #48 は「Workspace=スキル+KB / Admin=残り」の**分割**案だったが、二領域を覚えるコストを
避け「薄い管理面」に寄せるため **Admin を Workspace に統合**する方向へ改訂(会話で決定)。
管理面は 1 つ(`/workspace`)、可視性は **per-tab のゲート**で分ける(個々の model/tool の
Public/Private=access_control とは別軸)。

- **単一管理面 `/workspace`**、ナビ順 **Models / Skills / Tools / Functions / Settings / Users**。
  ※改訂(2026-07-12): 現行ナビは **Models / Files / Skills / Tools / Functions / Users / Chats / Settings**
  — #69 で Files(全ログインユーザー)、#19 で Chats(admin 専用のチャット閲覧ビュー)が追加。
  Models/Skills/Tools = `role==admin || permissions.workspace.X`、Functions/Settings/Users = `admin` のみ
  (Functions は CORE=Filter なので admin 固定)。`/admin/{functions,settings,users}` を `/workspace/*` に
  物理移動(コンポーネントは `lib/components/admin/*` を再利用)、`/admin` は `/workspace` へリダイレクト、
  Sidebar UserMenu の「管理者パネル」導線を撤去(入口は Sidebar の Workspace リンク)。
  ※改訂(2026-08-28): UserMenu に「ワークスペース」項目を追加(19d1e0a23)— 右上メニューからも
  到達できるように。入口は Sidebar リンク + UserMenu の 2 箇所(管理者パネル復活ではなく同一 `/workspace` への導線追加)。
- **Evaluations / feedback / arena を完全撲滅**(feature-triage「評価 / Arena」行参照):
  Phase1 で UI(admin Evaluations 画面・チャット 👍/👎)+ frontend API、Phase3a で backend router/model +
  DB テーブル drop(migration e3b1c9f4a7d2)+ ENABLE_MESSAGE_RATING、Phase3b で arena の dead code
  (utils/models.py 注入・utils/chat.py 分岐・DEFAULT_ARENA_MODEL・arena config)+ frontend 残骸を撤去。
- **Knowledge** はナビから撤去(上記テーブル行参照)。
- 途中発見の**別バグを修正**(2adb489e4): reasoning の `<details>` が空行で分断され `</details>` が
  リテラル描画される問題(#22 markdown-it 移行以来の潜在バグ)。segmenter/markdown-it で details を
  単一ブロック化。

出荷: 441fdfa05(P1)/ 27a566cdd(P2)/ 2adb489e4(fix)/ 347a9c38b(P3a)/ 4e07183a7(掃除)/ 94ae78542(P3b)。
検証: CORE 回帰 36 passed(auth/filter/authz/defaults)/ 6タブ統合ナビ・各ページ・`/admin` リダイレクト /
`/api/models` に arena 0 件・feedback テーブル drop 済み / 実チャット(SGLang Kimi)で reasoning + 本文正常・
`</details>` 漏れなし・console エラー 0。**#48 close**。
残: i18n(新タブ含む未訳 → #34 に集約)。~~Knowledge KB backend の掃除(#52)~~ → 完了(2026-07-08)。
#48 で作った `feat/48-admin-workspace-merge` ブランチは ff マージ後に削除済み。

## 全エンティティ/機能の棚卸し(2026-07-10 決定)

権限仕様の整理(#66 / `_memos/permissions.md`)の副産物として全 router・機能を棚卸しし、
KEEP/DROP を確定した。DROP バッチの実装 issue = #67、タイトル生成の修理 = #68。

**#67 は同日中に全 10 項目を出荷して close**(7 コミット a0a135ff2〜776aebf99)。
撤去台帳(何をいつ消したか)= #67 最終コメント。tag / memory / channel / message /
message_reaction の 5 テーブル drop、backend 228 passed、実機スモーク PASS。
道中の発見: retrieval /config は Attachment ゲート設定(#52)の保存先を兼ねていたため
**/configs/attachment へ移設**してから router を撤去(Documents タブの保存先も切替済み)。

### DROP(10 件 → #67)

| 機能 | 理由・スコープ |
|---|---|
| channel(グループチャット) | 誰も使わない・表面積大(backend 1,127 行 + フロント一式 + socket room + テーブル) |
| temporary chat(サーバー側) | フロントは非表示済み。サーバー経路 + `chat.temporary*` フラグごと撤去。監査(#31)とも相性が悪い |
| chat tag + タグ自動生成 | 不要。tag エンティティ・API・UI・generate_chat_tags を一括 |
| 入力中補完(generate_autocompletion) | 不要(トークン消費) |
| 絵文字生成(generate_emoji) | call 画面の飾り。不要 |
| モデル合議(generate_moa_response) | 単一モデル運用で無意味 |
| /utils/gravatar | 外部ネットワーク必須。オンラインに出られる配備でも不要と判断 |
| /utils/code/execute | 組み込みコード実行の残骸(#11 の保留分)。実行系は外部 sandbox 一本 |
| retrieval router | RAG config 残骸ごと全削除。web search は prism 層の tool(外部検索システム直叩き)へ移管、fork の検索素材も撤去済み(#51 close 2026-07-13、`eae47a4bd`) |
| memory API | UI 撤去済み(#63)の残骸。router・モデル・テーブルごと |

### KEEP(明示判断)

| 機能 | 判断 |
|---|---|
| chat import/export | 不要だが邪魔でもない — 残す |
| webhook(ユーザー通知 + signup) | **別システムへの横展開で使う可能性があるため残す**(閉域配備単体では無用でも) |
| 画像生成まわり | ~~そのまま~~ → **全撤去(2026-07-17)**: `features` フラグ・権限 tier ごと撤去(上の行参照) |
| STT / TTS | 全員使用可・設定は admin・tool から呼ぶ内部 API 提供(#53)。`chat.stt/tts/call` の per-user フラグは廃止(#66)。**配備レベル**の `ENABLE_AUDIO_STT/TTS`(audio.stt/tts.enable、既定 on)でアイコン表示 + API(403)を一括制御(2026-07-13) |
| /utils/pdf | 画面印刷の可能性があるため残す |
| /utils/code/format | tool/functions 開発用として残す |
| /utils/db/download | 残す(admin 専有を維持)。持ち出し統制は #31 の監査で扱う |
| pipelines router | 今後サポートの可能性があるため温存 |
| OTEL トレーシング(`utils/telemetry/` + opentelemetry 依存 8 本) | **残す(2026-07-14 判断)**。閉域でも Grafana 等で SGLang 監視をしており、OTLP collector に繋いで遅延内訳(DB / SGLang / 添付)を追う可能性がある。既定 OFF(`ENABLE_OTEL`、env 直読み)のまま投資はしない |
| ollama router | dev fallback として温存 |

### 別タスク

- generate_title がかなり壊れている(全チャット "New Chat"、`title_string=None` の
  AttributeError を 2026-07-10 に実測)→ #68 で修理

## 追加撤去(2026-07-16 前後、#34 ブランディング / #70 正常化の道中)

製品名 Open WebUI → **Nucllei** への改名(#34、repo = technoplasm/nucllei、パッケージ
`nucllei`、`backend/` 廃止でフラット化)と #70 の画面正常化にともなう機能撤去。

| 機能 | 判断 | 備考 |
|---|---|---|
| アップデート確認 / What's New | **撤去済み(#10 #70)** | api.openwebui.com への照会含め機能ごと全撤去(9755e960a) |
| 接続 tags・model tags | **撤去済み(#70)** | 接続モーダルの tags 機能(8f8c5f67e)+ model tags の死にパス(1915000c8)を FE/BE とも撤去 |
| CUSTOM_NAME | **撤去済み(#34)** | api.openwebui.com への起動時照会機能ごと撤去(config.py にコメント跡) |
| K8S_FLAG / kubernetes マニフェスト | **撤去済み(#34)** | k8s サポート全廃 |
| docker-compose | **撤去済み(#34)** | compose 全廃(ec3898e49)。配布は Docker(単一 Dockerfile)のみ |
| PyPI 配布(hatch ビルド層) | **撤去済み(#34)** | Python 配布層を撤去(d4b1404f4)。pyproject は uv 依存管理専用 |
| cypress(upstream E2E) | **撤去済み(#34)** | 一度も動いていない残骸(28ac9a59a)。E2E は Playwright(dev-environment.md) |

## ユーザー設定(設定モーダル「チャット」)の棚卸し(2026-07-17 決定、#70)

方針: **ユーザー個人の好みだけを設定モーダルに残し、配備ポリシーは workspace settings(admin)+ env に寄せる**。

| 項目 | 判断 |
|---|---|
| Auto-Copy Response to Clipboard | **機能ごと撤去**(fb960aaac)。応答完了のたびクリップボードを上書きする挙動は危険で要らない |
| iframe Sandbox Allow Same Origin / Forms | **撤去**(同上)。消費者ゼロの死に設定 — upstream の code artifacts 用で実行系は #11/#23 で撤去済み |
| Haptic Feedback | 残す(モバイルでストリーミング中に振動) |
| Call(音声通話) | **表示トグルを新設・既定 OFF**(`callEnabled`、見た目のみの制御)。ボイスセクションに配置。STT/TTS の配備レベル enable とは別軸 |
| タイトル自動生成 OFF の挙動 | **仕様確定: OFF = LLM を呼ばず最初のユーザー発話をタイトルにする**(New Chat が並ぶのは辛い)。フォールバック emit が応答本文を流していた表示不整合は修正(fb960aaac) |
| Always Collapse Code Blocks / Always Expand Details | **軸を「展開」に統一**(7f7373c3f): `expandCodeBlocks`(既定 ON)/ `expandDetails`(既定 OFF)。既定は互い違いで挙動不変。ラベルは「応答本文のコードブロック」「思考・ツール実行」と中身を明示(tool のコード表示と取り違えた実例より) |
| Image Compression(per-user) | **廃止 → 配備設定へ**(a24ad3241): `ATTACHMENT_IMAGE_MAX_DIMENSION`(長辺 max px、未設定 = 縮小しない)。compressImage は MIME 維持に修理(旧実装は常に PNG 化で JPEG が肥大化) |
| Paste Large Text as File(per-user) | **廃止 → 配備設定へ**(008f34c60): `PASTED_TEXT_CHARACTER_LIMIT`(文字数、未設定 = 変換しない)。閾値の単位は文字数を採用 |
| 送信キー(Enter ⇄ Ctrl+Enter) | per-user のまま。**「+」メニューにその場切り替え + 送信ボタンのホバーに現在の割り当てを表示**(25e41b3cb)。送信ボタンは常時表示化 |

添付系の配備設定は **workspace settings「ファイル」タブ(旧 Documents — 008f34c60 で改称)** に集約
(`GET/POST /api/v1/configs/attachment`、RuntimeConfig = env > UI 保存 > 既定)。

**ライセンス画面(設定 > ライセンス)の記載方針(2026-07-17 決定、2098d81f9)**:
UI に載せるのは「配布物に実体が同梱され、表記義務があるもの」だけ —
① Upstream License = Open WebUI BSD-3-Clause 全文(`licenses/open-webui.txt` を ?raw で取り込み。
文字列リテラル埋め込みはカラム 0 本文がエディタのインデント検出を壊すため禁止)、
② Third-Party Licenses = フォント(OFL-1.1)のみ。licenses.json から自動抽出(license が
OFL で始まる frontend パッケージ)なのでフォント入れ替えに追従する。
MIT/BSD/Apache 等の全列挙はしない(義務は「配布物へのライセンス文同梱」であり UI 掲示ではない。
ソース同梱で充足、オンプレ配備は配布が組織内で完結)。見出しは翻訳しない。
licenses.json の再生成は**素の venv で**(generate_licenses.py の注意書き参照)。

## 監査と削除の整理(#31、2026-07-17 決定・出荷)

方針(#31 のコメント参照): 内容監査の証憑は **DB 丸ごと**(バックアップ dump への照会 — #74)。
nucllei 側の責務は「**一次データが消えない・壊れない**」の保証に限定する。

| 対象 | 判断 |
|---|---|
| chat の削除(個別 / 全消し。フォルダ配下の一括削除はフォルダ機能ごと 2026-07-19 撤去) | **soft delete**(`chat.deleted_at`、migration ecb3c3c42379)。ユーザー向けの一覧・検索・取得からは消えるが行は残る。削除時に share は解除(share_id クリア + 共有コピー行の物理削除 — コピーは派生データ)。admin 一覧(#19)では既定で表示 +「削除済み」バッジ、`deleted=include/exclude/only` で絞り込み。削除済みは admin も /s/ ビューで開く(/c/ は本人向け取得から除外されるため) |
| メッセージの削除 | **不可(サーバで拒否)**。`POST /chats/{id}` の全量更新で「既存メッセージ ID の消失」を 400 で弾く。**FE の削除ボタン(user / assistant)は撤去済み**(2026-10-02、#119 — 押すと必ず拒否される操作を置かない。ken 判断。`deleteMessage` の経路ごと削除)。保存失敗時は `Messages.svelte` の `updateChat` がローカル履歴を巻き戻してトーストを出す |
| メッセージの編集 | **append-only に統一**。user =「編集して送信」で兄弟ブランチ追加、assistant =「コピーとして保存」で兄弟ブランチ追加(どちらも元は残る)。**その場で書き換える「保存」は FE から撤去**(2026-10-02、#119 — 必ず拒否される操作を置かない。ken 判断)。サーバ側は引き続き content の非前方一致な書き換えを 400 で拒否し、content の追記(ストリーミング終端の保存等)は通す |
| POST /chats/{id}/messages/{message_id} | **撤去**。メッセージ content の無痕跡上書き API(FE・README_API とも消費者ゼロ)で、append-only と両立しない |
| user の削除 | **hard delete をエンドポイントごと撤去**(チャット物理削除・file 孤児化を伴っていた。FE 導線は #69 で撤去済み)。代替は **無効化 = role`disabled`**(新設): `get_verified_user` が全 API を遮断し、ログインすると AccountPending の disabled 分岐(案内文言は #59 の本仕様で練り直す)。update/role の受理値は pending/user/admin/disabled の 4 つに限定 |

備考: 全量更新 API がある以上、FE だけで消せなくしても API 経由で書き換えられる —
だから担保はすべて**サーバ側**に置いた。編集履歴の revision テーブル案は「全編集がブランチとして
スタックする」制約で不要になったため見送り(追加ストレージゼロ)。

## チャットフォルダの撤去(2026-07-19 決定・出荷)

**DROP**。upstream 残骸で本トリアージにも未掲載のまま残っていた機能。UI に作成導線が無く
(サイドバー再編の過程で消滅、`createNewFolder` は呼び出しゼロ)、実質「過去データがあれば
操作できる」だけの半死に状態だった。API(`/api/v1/folders`)・FE(サイドバーの
Folders/RecursiveFolder/FolderMenu)・`chat.folder_id` 列・`folder` テーブルごと撤去
(migration `f3d7a91c5b24`)。フォルダ所属だったチャットは未分類に戻るだけで本体は残る。
副産物: フォルダ経由のチャット一括削除だけ共有コピー行の掃除が漏れていた欠陥も、経路ごと消滅。
※ `common/Folder.svelte` は同名の別物(折りたたみセクション部品、Pinned で使用)で残置。

## マルチモデル送信(side-by-side 応答)の撤去(#108、2026-08-13 決定・出荷)

1 プロンプトを複数モデルへ同時送信し応答を横並び表示する機能(ModelSelector の「+」/
`MultiResponseMessages`)を**仕様ごと撤去**。Pipe の「複数モデル束ね」(モデル側で 1 つに
見せる)とは別物 — あちらは残る。

- 判断の背景: サブエージェント(#107)の表示は「親子が分かる形(サイドバー tree)」で
  行くと決定。side-by-side は「並列の枝」の表現で親子の意味論に合わず、**流用もしない**。
  紛らわしい入口(1 プロンプト複数応答)を #107 着手前に塞いだ(以前から気になっていた部分)
- 撤去範囲: ModelSelector 複数選択 UI / send.ts の複数モデルループ /
  `MultiResponseMessages.svelte` / `ChatMessage.modelIdx`・user message の `models[]`(型ごと)/
  権限 `chat.multiple_models`(FE ゲート・admin Groups UI・backend config/permission model)
- **旧データも互換を持たない**(負の遺産を残さない — ken): migration `ff163c4b7986` が
  既存 chat JSON から lane メタデータ(modelIdx / user message models)を剥がし、
  chat.models を先頭 1 要素へ正規化する。複数 lane の応答自体は通常の sibling(1/N ナビ)
  として残る(枝は有効な履歴なので消さない)

## 「続きを生成」(Continue Response)の撤去(refs #142、2026-10-05 決定・FE 出荷)

最後の応答の下にあった「続きの応答」ボタン(丸に三角)と FE の `continueResponse` 経路
(send.ts → Chat → Messages → Message → ResponseMessage → MessageActions の callback prop、
共有ページの空 stub、i18n キー `Continue Response`、マニュアル 03 の記述)を**機能ごと撤去**。

- 判断の背景(監査「同じ内容が重複して context を食う」): 推論エンジン(SGLang / LiteLLM、
  `continue_final_message` 未指定)は末尾 assistant を続けず**最初から書き直す**。BE は旧本文を
  assembler の seed に足して保存するので、「旧本文 + 新全文」が履歴に永久に残る。tool を使った
  メッセージでは tool 履歴が消える(実測)。**正しく続ける手段が無い機能**なので撤去
- 戻すなら `continue_final_message`(エンジン側の prefill 継続)対応で作り直す。現行コードの復活はしない
- BE 側の continue 用コード(`utils/middleware.py` の assembler `initial_content` seed・
  DB の途中回答 / wire 末尾 assistant の読み出し)も撤去済み(2026-10-05)。末尾が assistant の
  wire(API 利用者が送りうる)は普通の履歴として上流へ渡り、応答は空から組み立てて
  そのメッセージに入る(旧本文を種にしない — 追撃・計測でも旧本文は 1 回だけ)

## Filter / tool 機構の縮小(refs #24 / #142、2026-10-05 決定 — ken「filter はほとんど使われていない」)

upstream リリースノート照合で filter / tool のプラグイン機構にバグが複数見つかった(priority・file_handler・
UserValves の混入・tool server の ACL)。**直す前に llens が実際に使っている範囲を洗い出し、使っていない
機構は撤去、使っている範囲だけ直す**方針。

llens(`prism-hu/llens` 35018a3 時点)の実使用:
- Filter は 2 本(`nucllei/functions/large_file_gate.py` / `token_meter.py`)、どちらも **inlet だけ**。
  `Valves`(**priority をコード既定値で指定** — gate=50 / meter=100、保存値なし)と
  `__event_emitter__` / `__metadata__` / `__user__`(id 参照のみ)を使う。添付の間引きは inlet で
  `body["files"]` / `metadata["files"]` を書き換える(gate のコメントに「file_handler は使わない」と明記)
- tool(`doc` / `emr` / `monitoring` / `audio_tools`)は Valves のみ
- **UserValves・file_handler・outlet・stream・外部 OpenAPI tool server はどれも未使用**。nucllei に builtin filter は無い

撤去したもの:
- **UserValves**(filter / tool への注入、`/functions|tools/id/{id}/valves/user{,/spec,/update}` の 6 endpoint)。
  設定 UI も無く(FE に呼び出し元なし)、コード既定値しか入らなかった。`__user__` は plugin ごとの
  浅いコピー(`"valves"` キーは落とす)にして、共有 dict 経由の混入も塞いだ。
  pipe(`functions.py` の get_function_params)と action(`utils/chat.py`)の UserValves 注入も
  2026-10-05 に撤去(pipe の `__user__` も浅いコピーで `"valves"` を落とす)
- **Filter の `file_handler = True`**(inlet 後に添付を丸ごと落とす)。添付無しの送信で KeyError → 400
  になっていた。属性は無視される。tool 側の `metadata.file_handler`(読む所が無かった)も削除
- **Filter の outlet / stream フック**(2026-10-05)。llens の filter は inlet だけ。呼び出し面
  (middleware の応答ストリーム・headless 素通し、`POST /api/chat/completed` と utils/chat.py の
  chat_completed、FE の chatCompleted 呼び出しと応答 messages の適用)を撤去。FE のターン末処理
  (chatCompletedHandler)は完了時保存とサイドバー追従だけになった。`routers/pipelines.py` の
  process_pipeline_outlet_filter は呼び手が無くなった(pipelines router は温存方針なので残置)。
  `utils/filter.py` の process_filter_functions は filter_type を汎用に扱うまま(inlet でしか呼ばれない)

残して直したもの:
- **priority**: `Valves(**保存値).priority` で決める(コード既定が効く)。同 priority は `filterIds` の
  並び順で安定。旧実装は保存 dict の priority しか見ず、さらに `list(set(...))` が PYTHONHASHSEED
  依存だったため、valves を保存していない配備では gate / meter とも 0 で**順序が起動ごとに変わっていた**。valves 保存は明示フィールドだけ(`exclude_unset`。UI の
  「Default」= null は保存しない)— コード既定の変更が保存済み filter / tool にも効く
- tool の関数名衝突: 先勝ち(`meta.toolIds` 順)は従来どおり、残した側と捨てた側をログに出す
- tool server(server:N)の実行時 read ACL(一覧と同じ判定)— セキュリティ修正なので未使用でも入れた。
  ついでに OpenAPI の任意 requestBody を省けない件も修正(path/query 引数を body に重ねない)

仕様から外したもの(コードは残置):
- 外部 OpenAPI tool server も llens 未使用で撤去候補(FE 設定・configs router にまたがる)
