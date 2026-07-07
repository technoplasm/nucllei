# 機能別トリアージ(fork.md §5 DROP の詳細化)

2026-07-04 決定。fork.md の 4 区分を機能単位に落とした一次判断。矛盾があれば fork.md が優先。

| 機能 | 判断 | 備考 |
|---|---|---|
| ナレッジベース(組み込み) | **不要・ナビ撤去済み(#48)** | 独自実装が別にあるため。OWUI 側はキャッチアップしない。#48 で Workspace ナビから Knowledge タブを撤去(添付は file_id でチャット内スコープ、恒久ファイル/RAG は組み込みツール基盤側。route/backend は温存、KB backend の掃除は #9 相当で後日) |
| Documents(文書抽出) | **Docling が使えれば十分** | v0.6.5 で対応済み(`CONTENT_EXTRACTION_ENGINE=docling`)。それ以上の投資不要 |
| RAG / retrieval | **一切不使用** | 将来必要になってもツール側で実装する方針 |
| Web 検索(組み込み) | **不要** | |
| 組み込みツール群(upstream の builtin) | **不要** | 簡単に再現可能。必要なものは自前ツールで |
| コード実行(組み込み Pyodide/Jupyter) | **無くてもよい** | nsjail+IPython は自前ツール側で提供。Pyodide には戻さない(fork.md §6) |
| 会話検索 | **必要・維持** | v0.6.5 に存在(タイトル+本文検索)。デグレさせない |
| メモリー | **放置** | 使っていないが消すほどでもない。無効化も削除も投資もしない(issue #8 は close) |
| ノート | 無いままでよい | v0.6.5 に存在しない。追加しない |
| タスク / オートメーション / カレンダー | 無いままでよい | v0.6.5 に存在しない。追加しない |
| 画像生成 | **機能として維持** | 厳密なキャッチアップは不要。動く状態を保つ(KEEP-MINIMAL 扱い) |
| プロンプト管理(`/` 呼び出し) | あってもよいが**スキルとの棲み分け再設計** | プロンプト内に「スキルを使え」と書く形は不可。`/` はユーザーがスキルを能動発火させる UI に置き換える案(issue #5) |
| ツール(Tools) | **完全キャッチアップ** | 外形仕様・公式ドキュメントベースで機能パリティを目指す(コード移植は不可) |
| スキル(Skills) | **完全キャッチアップ** | 同上。llens の権限モデル(診療科×職位)と統合(issue #5) |
| ストア(openwebui.com 共有・取得) | **不要** | air-gapped 前提とも整合(issue #10) |
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
残: i18n(新タブ含む未訳 → #34 に集約)/ Knowledge KB backend の掃除(#9 相当)/ #48 で作った
`feat/48-admin-workspace-merge` ブランチは ff マージ後に削除済み。
