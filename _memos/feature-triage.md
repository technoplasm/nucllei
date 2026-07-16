# 機能別トリアージ(fork.md §5 DROP の詳細化)

2026-07-04 決定。fork.md の 4 区分を機能単位に落とした一次判断。矛盾があれば fork.md が優先。

| 機能 | 判断 | 備考 |
|---|---|---|
| ナレッジベース(組み込み) | **不要・撤去済み(#48 ナビ / #52 で完全撤去)** | 独自実装が別にあるため。#48 で Workspace ナビから撤去。**改訂(2026-07-08 #52 第2セッション): route/backend も完全撤去**(workspace KB 画面・チャット `#` ピッカー・モデル KB 割当・knowledge store・knowledge router 全 endpoint)。~~温存は `models/knowledge.py`(Knowledges)のみ(access_control #40 / IDOR test 結線)~~ → **再改訂(2026-07-10 #41): `models/knowledge.py` も撤去・knowledge テーブル drop(migration a7c9e2f4b8d1)。KB 残骸ゼロ** |
| Documents(文書抽出) | **Docling が使えれば十分(実体は prism)+ docling 非依存の素の md 化は markitdown(fork 同梱)** | ~~v0.6.5 で対応済み(`CONTENT_EXTRACTION_ENGINE=docling`)~~ → **改訂(2026-07-08 #52): fork の抽出 preset(`CONTENT_EXTRACTION_ENGINE`/外部エンジン config)は撤去。docling 抽出は案件層(prism)の Attachment Function が直接叩く(`owui/attachments/docling_extract.py`)。fork は「仕組み」だけ** → **再改訂(2026-07-14): docling は遅いので、docling 非依存の軽い md 化に `markitdown[all]` を fork へ同梱。fork は抽出コードを持たず(一度 v0.6.5 langchain ローダ版を `utils/extraction.py` に復元したが markitdown の劣化再実装のため撤回・削除)、prism の Attachment が `from markitdown import MarkItDown` で直接呼ぶ。xlsx→シート別 CSV 等の型別ポリシーも prism が手前で処理。langchain-community/unstructured/networkx/nltk/pypdf/docx2txt/ftfy は撤去(langchain-core のみ tools.py 用に残置)。詳細 `_memos/file-handling.md`** |
| RAG / retrieval | **一切不使用・撤去済み(#52)** | 将来必要になってもツール側で実装。**2026-07-08: embedding/vector/reranking/chunk/RAG_TEMPLATE を config から完全排除** |
| Web 検索 | **残す(prism tool → 外部検索システム直叩き)** | 改訂(2026-07-13、#51 close): 当初の「fork 組み込みツール」案を撤回 — docling と同じ線引きで **fork は何も持たず**、prism の tool が SearXNG 等を Valves 設定で直接叩く。fork の WEB_SEARCH_* config・providers・管理画面は全撤去(`eae47a4bd`)。閉域本体では成立しない機能(メタ検索も外向き通信必須)= 非閉域の横展開先向け |
| 組み込みツール群(upstream の builtin) | **不要** | 簡単に再現可能。必要なものは自前ツールで。※fork 自前の組み込み tool 機構(`nucllei/builtin_tools/`、制御はモデル設定)は別途導入 — 詳細 `builtin-tools.md`(#18-3) |
| コード実行(組み込み Pyodide/Jupyter) | **撤去済み(#23)** | 改訂(2026-07-14、#23): code_interpreter(タグ検出による tool 外コード実行)を機能ごと撤去 — config・permission・UI トグル・実行ループすべて。コード実行は nsjail+IPython の自前 tool 一本で、tool 経路以外の実行裏道は残さない。Pyodide には戻さない(fork.md §6) |
| 会話検索 | **必要・維持** | v0.6.5 に存在(タイトル+本文検索)。デグレさせない |
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
