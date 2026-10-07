# 組み込みツールと sandbox 自前ツール搭載(#18-3)

2026-07-06 起草、同日改訂 ×2(設計議論の着地: **制御面はすべてモデル設定** — グローバル
フラグ・workspace ページのトグルは作らない)。#18-3(自前ツール搭載確認 → #18 close)の
要件とテスト設計。

> **決着(2026-09-03、#114)**: 本メモが「sandbox はカスタム tool」で始まっているのは
> 起草時点の判断で、**いまは覆っている** — sandbox はランタイムも tool も nucllei 製品側に
> 移り、tool は env ゲート付きの組み込み `builtin_sandbox` になった。当時の判断の理由は
> 履歴として下に残してあるが、**現在の事実は末尾の「改訂(2026-09-03、#114)」節**を見ること。
> 実装の参照先も `_links/llens` ではなく本 repo の `nucllei/builtin_tools/sandbox.py` と
> `sandbox/README.md`。

## 0. 事前調査の結果(2026-07-06 実測)

### sandbox コンテナ外形

> 当時の実測(コンテナは llens 側でビルドした `llens/sandbox:poc`、host `127.0.0.1:3000`)。
> **現在**: image は `ghcr.io/technoplasm/nucllei-sandbox`(ソースは本 repo の `sandbox/`)、
> host 公開は `127.0.0.1:13302`、`outputs`/`inputs` は `exports`/`imports` に改名済み。
> API の単一情報源は `sandbox/README.md`。

- `GET /healthz` → `{ok, nsjail: true}`
- `POST /exec` (user_id, lang: python|shell, code, timeout_s?, inputs[]?) →
  `{exit_code, timed_out, duration_s, stdout, stderr, *_truncated, outputs[]}`
- `POST /exec_cell` (user_id, **chat_id**, code, timeout_s?) →
  `{stdout, error, timed_out, session_reset, duration_s, stdout_truncated, outputs[]}`
  - (user_id, chat_id) 単位の常駐 kernel。変数・import が実行を跨いで永続(実測: x=42 → 84)
  - chat_id 違いは完全分離(NameError を実測)
  - per-cell timeout は SIGALRM 中断で **namespace 保持**(実測: timeout 後も x 生存)。
    中断不能時のみ session_reset=true で作り直し
  - 最後の式は notebook 風に repr 表示(kernel.py の ast 加工)
- `POST /files/list|read|write` (user_id, path, [b64]) — workspace 相対、traversal ガード付き
- `outputs[]` = **/workspace/outputs/ の実行前後差分**(新規+更新)を b64 で回収
  (plt.show フックではない。FILE_LIMIT 超過は truncated=true で b64 なし)

### dev 暗黙ツールの健全性(現行 master、async 化 #28 後)

| ツール | 経路 | 結果 |
|---|---|---|
| load_skill(合成組み込み) | manifest 常載 + 両入口発火 | PASS(check_lazy_tools / check_skill_command) |
| secret_password(私有) | skill 経由遅延注入 | PASS(正答 kotori-sazanka-9412) |
| render_probe(公開) | toolIds 直載せ(UI が tool_ids 送信) | PASS(echo 実行・返り値一致) |
| get_weather | 未登録(optional fixture) | pytest のフレームリプレイで代替済み |

副産物の知見: **素の API 呼び出し(モデル未割り当て)では load_skill しか載らない**。
~~toolIds はフロントが payload で送る設計(4pcg ガードで read 検証)~~ → **#54(2026-07-12)で
サーバー側解決に変更**: middleware が `model.info.meta.toolIds` から導出し、payload の
`tool_ids` は無視(クライアント申告経路を廃止)。per-user の read ACL 検証(4pcg 由来の
enforce_access_control)は #66 のゲートとして維持。また非ストリーミング
`/api/chat/completions` は native ループを回さず finish_reason=tool_calls で返る(UI 経路が本線)。

### #18-3 実測で見つけた基盤バグ(2026-07-06、すべて修正・回帰テスト付き)

1. **プライベートメソッドの spec 露出**: v0.6.5 の get_functions_from_tool は dunder しか
   除外せず、`_post`/`_exec` 等のヘルパが全部モデルに見えた → `_` 始まりを除外
   (utils/tools.py + test_tool_specs.py)
2. **Optional 引数で全リクエスト死**: Optional の保存 spec は anyOf 形で `type` キーが無く、
   get_tools の str→string hack が `val["type"]` 直参照で KeyError → 応答前に落ちて
   error message 化 → `.get("type")` に修正(+ test_native_tool_calling.py)
3. **`__chat_id__`/`__message_id__` が tool に未注入**: 0.9 系 tool の外形が期待する
   会話スコープ id が v0.6.5 の extra_params に無い(stateful run_python が常に
   「chat_id が取得できず」)→ process_chat_payload の extra_params に追加。
   pytest には process_chat_payload を通す harness が無く、この配線の観測点は
   E2E(check_sandbox_tools.py)が担う
4. 教訓: **同名 tool の衝突は先勝ちで discard**(render_probe の dummy run_python が
   本物を隠し、モデルがダミー応答を取り繕う偽 PASS を作った)→ dummy は撤去。
   組み込みツール導入時は名前空間の衝突検出を意識する

## 改訂(2026-07-21、#89 — view_image を builtin 化 + tool 出力 sink)

真実源はコード(#90 = `7b4f9aac7`〜`688d584f9`)+ issue #89。画像/VLM 経路の詳細は
[[file-handling]] の #89 改訂節、次タスクは [[pr88-display-next]]。

- **第二の組み込みツール = view_image**(`nucllei/builtin_tools/view_image.py`)。旧 seed
  custom(`nucllei/tools/view_image.py`)は撤去し builtin へ移設。理由: Files/Storage 内部 API +
  `__user__` 密結合の汎用 OWUI tool で 1c の builtin 側そのもの(外部叩きの web 検索が prism tool
  なのと対照)。seed の custom 登録撤去に伴い露出は `seed.py` の preset_tools に `builtin_view_image`
  を明示追加して保全。旧 seed custom は list/str 返しの契約ズレ + owner チェック欠落(IDOR)を
  抱えていた — builtin 化でどちらも構造的に解消。
- **tool 出力チャネル基盤 `nucllei/utils/tool_output.py`**(#88 と共用): tool は戻り値と別に
  「誰かに見せたい」出力を tool-push できる。`__feed_image__(file_id)` → `results[i].model_images`
  (モデル/VLM の目)。tool-call ごとの ContextVar sink に蓄積・成功時のみ harvest。#88 で
  `__display__`(人間の目)を対称に足す予定。emitter は `middleware` の extra_params 経由で
  sig 一致注入(`__` 始まりはモデル spec に非露出)。
- **R2 の sandbox 関数名改訂(Q4)**: `import_chat_file` → `export_to_workspace`(chat→workspace/inputs)、
  `export_workspace_file` → `import_from_workspace`(workspace→chat)。前置詞が向きを表す対称命名。
  実体は prism-hu/llens(prism-hu/llens#3 draft)。`list_workspace` は据置。

## 改訂(2026-07-22、#89 D2 反転 + #42 builtin ライフサイクル)

- **view_image は builtin から撤去**(`38895a982`、#89 D2 反転)。上の 2026-07-21 節で builtin 化した
  view_image は prism tool **`feed_image`** へ一本化して撤去(builtin は `builtin_history` のみに戻る)。
  `__feed_image__` プリミティブの正規 model-pull 入口は prism tool 側。経緯は [[pr88-display-next]]。
  → **2026-10-03 に再反転**: `feed_image` は組み込み `builtin_feed_image` として nucllei へ戻った
  (末尾の「改訂(2026-10-03、feed_image / fetch_url の builtin 化)」節)。
- **builtin ライフサイクル = prune + prefix 予約**(`6063bd701`、#42)。`sync_builtin_tools()` を
  「upsert + prune」に:`builtin_` prefix を持つのに BUILTIN_TOOLS 未定義の行を起動時に削除
  (コードから外した builtin は DB からも消える。旧 `builtin_view_image` 等の orphan を残さない)。
  create ガードは `is_reserved_builtin_id`(prefix 判定)へ拡張し、未定義 id でも `builtin_` 始まりは
  一律拒否(update/delete 保護は実 builtin=dict 所属のみ、従来通り)。回帰 13 passed
  (prune / prefix 予約の 2 ケース追加、`test_builtin_tools.py`)。
- 注意: prune は **builtin(`builtin_` prefix)専用**。改名・撤去された **custom tool**(旧
  `view_image` / `doc` 等)は prune 対象外で、`make nuc-sync`(prism sync)も削除しない。
  dev では手削除した([[dev-environment]] のセッション追補)。custom 側 prune は別途検討(未起票)。

## 改訂(2026-08-05、`a2f9cc07c` / `74e24aada` — 戻り値は実型で返す)

builtin tools は自前で `json.dumps(..., ensure_ascii=False, indent=2)` した **str** を
返していた。これは #99 A-1(本体の直列化に `ensure_ascii=False` が無かった)を各ツールが
個別に回避していた名残で、本体が直った時点で害しかない — str には実型ディスクリプタが
付かず、フロントは `{kind:'text'}` で素描画(ハイライト・ヘッダ・コピー無し)になる。

- `history.list_history` / `grep_history` … **list を返す**(`-> list | str`。
  空・エラー時のみ日本語の str)
- `chat_files.list_chat_files` / `grep_chat_files` … **dict を返す**(`-> dict | str`)。
  切り詰め通知は散文の前置きをやめ `{"total": N, "files": [...]}` の構造に載せた —
  モデルが「まだある」を機械的に読めるし、表示も JSON 一本で済む
- `history.read_history` … `"# {title}\n\n{body}"` なので **`Markdown` 戻り型を宣言**
  (JSON ではないため実型からは決まらない)

戻り値注釈はモデルに出ない(spec 生成は `signature.parameters` のみ走査)。
表示宣言の抽出にしか使われないので型を正直にして副作用は無い。詳細は [[tool-arg-render]]
の 2026-08-05 改訂。回帰は `test_builtin_tools.py`(実型で受ける形へ 12 箇所更新 +
`execute_tool_calls` 経由で `render == {kind:"json"}` を見る 1 本 + 3 関数の
ディスクリプタを並べる 1 本)。

## 1. 設計(議論の着地)

### 1a. 制御面はモデル設定に一元化(グローバルフラグなし)

モデル(preset)エディタに 3 セクション:

```
機能
  [x] スキル            ← ON で skill picker が出現(manifest + /command の一括スイッチ)
      [x] kansai / [ ] vault ...   (= meta.skillIds)
組み込みツール
  [x] 履歴管理 — list_history / read_history / grep_history
カスタムツール
  [x] Sandbox — run_python / run_shell / ...
```

- 組み込み/カスタムは**既存の toolIds picker のセクション分け**。接続チェックが
  そのままモデル単位のトグル — per-model capability(tool_calling)や workspace ページの
  is_active のような**追加の間接層は作らない**。CoT 無しモデルには単に接続しない
- 「サイト全体で無効」= どのモデルにも接続しない、で表現(グローバルスイッチ不要)
- workspace > Tools ページは admin の管理面として残す(組み込み行はコード読み取り専用、
  valves / access_control の編集のみ)。有効/無効の概念は持たない
- **load_skill はどのツール一覧にも行として出さない**(toolIds に付けられず、
  access_control を持たず、単独の有効/無効の意味も持たない — 機能「スキル」の実装詳細)

### 1b. 機能「スキル」チェックボックス(per-model)

- ON = load_skill manifest 搭載 + `/command` 発火。OFF = そのモデルではスキル機構が
  全停止(manifest 非搭載・`/command` は平文素通し)。CoT 無しモデルはほぼ使わない想定
  なので入口別の分離はしない(一括で良い、と確認済み)
- **露出/発火の条件は AND**: モデルで機能 ON ∧ その skill が接続(skillIds)∧
  ユーザーに read 可視。モデル設定は必要条件であって認可の代替ではない
- **UI**: skill picker は機能 OFF でも隠さない(隠すと設定が見えなくなる)。常設表示 +
  OFF 時は「スキル機能が無効なので発火しない」ことが分かる注記を出すだけ
- **挙動変更**: `/command` の発火範囲を「そのモデルに接続された skill(skillIds)」に絞る。
  現行は「ユーザー可視ならどのモデルでも発火」— モデル設定を唯一の制御面にする一貫性と、
  admin が「このモデルにはこのスキル」を握れる運用のため変更する。チャット入力の補完も
  選択モデルの skillIds から出す
- 意識済みの帰結: CoT 無しモデルで `/command` を使う場合もスキル ON にする = manifest も
  送られる(モデルは呼ばないだけで無害、コンテキスト消費のみ)。入口別分離が必要になったら
  後から足す(合成 exchange 注入はモデル能力非依存 — この機構は他の強制注入にも流用可能)

### 1c. 組み込みツール = Tools テーブルの builtin 行

- **定義**: `nucllei/builtin_tools/` 配下のモジュール。起動時に Tools テーブルへ
  type="builtin" 行として sync(content は空、ロード時に repo モジュールへ振り向け)
- 既存機構がそのまま効く: spec 生成(docstring → schema)・toolIds picker・skill の
  tool_ids・access_control・valves・4pcg ガード。content 更新 API は builtin 行を拒否
- **境界基準は「出荷単位」**(「内部に触れるか」では引けない — カスタム tool も
  in-process exec で Files/Chats を import できるのが v0.6.5 の仕様):
  - 組み込み = OWUI の内部概念(チャット履歴・skill)の操作が目的そのもの。
    本体と一緒にバージョニングされないと壊れるもの
  - カスタム = 外部サービスへのブリッジ。本体と独立に差し替え・更新したいもの。
    → ~~**Sandbox はカスタム**(現行 prism-hu/llens と同じ DB 登録の配布形を維持)~~
    → **反転(2026-09-03、#114): Sandbox は組み込み**。基準そのものは変えていない —
    「出荷単位」を当てはめ直したら答えが逆になった(下の改訂節)
- この整理により **#18-3 は「カスタム tool として現行基盤に乗ることの確認」に純化**され、
  組み込み機構の実装を待たずに進められる

## 2. 要件

### R1. 組み込みツール機構 + モデル設定の再編

上記 1a〜1c の実装。**出荷済み(2026-07-06)**: 機能「スキル」ゲート + `/command` の
モデル紐づけスコープ化(ba01a0f1f)、組み込みツール機構(builtin 行 sync)。
実装の決定: レジストリ = `nucllei/builtin_tools/`(id は `builtin_` prefix で
予約・create で横取り不可)、行の所有者は固定値 "system"、判別は meta.builtin
(表示専用 — ロード分岐と更新保護はレジストリ id が真実源)、更新は access_control のみ・
削除拒否・specs は起動ごとに repo から再生成。

第一弾の組み込みツールは**履歴管理 — 本実装済み(2026-07-13、`c0b8a14ce`)**。
当初はスタブ(TODO 返し)で機構の検証台だったが、**「履歴ツールは OWUI の Chats に
べったり密結合するので組み込みで提供する」で確定**(ユーザー判断 2026-07-13。#51 で
web 検索を prism へ出したのと対照 — あちらは外部システム、こちらは fork 内部概念):

- `list_history` / `read_history` / `grep_history`(関数名は snake_case — `/xxx` は
  skill command の記法なので混ぜない)
- **認可は owner-only 固定**: `__user__` の chat のみ(gfm2/#37 で塞いだ面をツールで
  開け直さない)。「モデルのコンテキストに過去チャットが入る」こと自体が新しい情報流
  なので要件として明記
- grep は v0.6.5 既存のチャット検索を再利用
- 常載よりも「skill にくっつけて遅延ロード」を推奨経路とする(step2b がそのまま効く)。
  組み込み**スキル**(repo 定義 skill の seed)は作らない — skill はユーザーデータのまま、
  「履歴管理 skill の作り方」は手順書/fixture で提供

### R2. sandbox 自前ツール群の搭載(#18-3 本体)

> **現在は R2 の前提が反転している(#114)**: 下は「カスタム tool のまま搭載検証する」という
> 2026-07-06 時点の要件。搭載形態は組み込みへ変わったが、**確認事項(依存する基盤機能の一覧)
> はそのまま組み込みでも成立が要る**ので、内容は残す。関数名も改名済み(改訂節の表を見ること)。

`_links/llens/owui/tools/sandbox.py` を**カスタム tool のまま**(DB 登録)本 fork に搭載検証:

- 関数: run_python(stateful)/ run_python_once / run_shell / import_chat_file /
  list_workspace / export_workspace_file
- 依存する基盤機能が llens の native ループで全部動くこと(= #18-3 の確認事項):
  - extra params 注入: `__user__` `__chat_id__` `__message_id__` `__event_emitter__` `__files__` `__metadata__`
  - Valves(default_timeout_s 等)の DB 永続と UI 編集
    ※ `sandbox_url` valve は 2026-09-05 に廃止(下の改訂節)。向き先は env のみ
  - 進捗表示(5 秒ごとの経過)と `chat:message:files` イベント
    ※ 進捗は #100 で status → `__progress__` へ移行(行き先が tool 自身の
    ウィジェットに閉じる)。tool の status は落とされる — `_memos/tool-channels.md`
  - OWUI Files/Storage への outputs 登録 + メッセージ添付
  - 型駆動レンダリング(#20): run_python の code 引数は `Code("python")` 宣言
    (dev ダミー run_python が既にこの形 — 置き換え時に宣言を引き継ぐ)
- 認可: access_control で group 単位の出し分け(CORE)
- オフライン完結: `SANDBOX_URL` は docker network 内。外部ネットワーク依存なし(閉域配備でも成立)

### R3. #15 の結論(Responses API 要否)

**不要と結論**。根拠: 接続は SGLang の OpenAI 互換 chat completions 一本(fork.md CONSTRAINT)、
Kimi の tool call は厳密 JSON で現行パーサ一次経路が成立(#15 実測)、sandbox ツールの
戻り値は str(JSON 文字列含む)で native ループを素通しできる。stateful 性はサンドボックス側
(常駐 kernel)が持ち、プロトコル側の状態(Responses API の previous_response_id 等)を要しない。

## 3. テストケース

### pytest(backend、sandbox は HTTP モック — CI で実コンテナに依存しない)

組み込みツール機構 + モデル設定(R1)。**T1〜T6 出荷済み**(T1〜T5=2026-07-06 / T6=2026-07-13):
- [x] T1: builtin id 解決 — builtin 行の tool が tools_dict に spec/callable 付きで載る
  (`test_builtin_tools.py::test_sync_creates_builtin_row_and_get_tools_loads_module`)
- [x] T2: builtin 行の保護 — content 更新・削除・create 横取りを拒否、access_control のみ更新可、
  起動時 sync で repo から復元(`test_update_allows_only_access_control` ほか)
- [x] T3: 機能スキル OFF のモデル — manifest 非搭載 + `/command` 素通し。ON で両方効く
  (`test_skills.py::test_skill_feature_disabled_gates_both_entrances`)。実機は E7
- [x] T4: `/command` の範囲 — モデルに接続していない skill は可視でも発火しない
  (`test_slash_command_requires_model_attachment`)
- [x] T5: access_control — read 権限の無いユーザーのリクエスト由来 id は無視(4pcg 同型、
  builtin 行にも適用。`test_access_control_guards_builtin_row`)
- [x] T6: 履歴ツールの owner-only — 他ユーザーの chat_id を read_history に渡しても見えない。
  list/grep が本人の chat しか返さない(2026-07-13、`c0b8a14ce`。テスト5件)

sandbox ツール(R2、respx 等で sandbox API をモック):
- T7: run_python — /exec_cell へ user_id=呼出ユーザー, chat_id=会話 id が渡る。
  stdout/error/timed_out/session_reset の整形文言(タイムアウト時「変数・import は保持」等)
- T8: `__chat_id__` 欠落時の run_python → run_python_once 誘導のエラー文
- T9: outputs[] → Files/Storage 登録 + `chat:message:files` emit + 画像なら markdown 誘導行。
  truncated エントリは「転送上限超過」報告のみで登録しない
- T10: import_chat_file — 所有者チェック(他ユーザー file_id は skip)、file_id 直指定経路
- T11: sandbox HTTP 障害(接続不能・非 200)→ `ToolError`(#145 — 文面は "ERROR: ..."。生の例外・
  detail は漏らさない)
- T12: spec 生成 — docstring から name/description/parameters が正しく出る + Code("python") render

~~sandbox tool 内部(T7〜T12)の単体は、tool 本体が prism-hu/llens 側の管轄(カスタム tool
として配布)のため fork 側 CI には載せない~~ → **#114 で本 repo に来た**(2026-09-03)。
`nucllei/test/apps/webui/routers/test_builtin_sandbox.py` が T7〜T12 相当を全部持ち、
CI の `uv run pytest` で毎回走る(env ゲート・prune・valves 編集・exports 登録/添付・
send_to_workspace の owner-only、および url= 経路の回帰 — 2026-10-03 に resolver 経由へ書き直し、
llens から引き取った SSRF ガード (CIDR 拒否リスト) のテストは経路ごと撤去)。
**tool と一緒にテストも移設した**のが要点 — tool だけ移して担保を配備層に残さない。

### E2E(実 Kimi × 実 sandbox / 実モデル、CI 外)

- **`check_sandbox_tools.py`(#18-3)— E1〜E6 全 PASS(8 チェック、2026-07-06)**:
  stateful(x=42→84)・timeout 後の変数保持・outputs 自動添付(result.csv)・
  import_chat_file の実読み出し。
  ※ **#114(2026-09-03)で対象を組み込み `builtin_sandbox` に切り替え**、命名も現行へ追随
  (outputs → **exports**、import_chat_file → **send_to_workspace**)。チェック項目 E1〜E6 は
  そのまま。手動 tool 登録の前提は消え、代わりに「`SANDBOX_URL` 付きで起動しているか」を
  preflight で先に落とす(モデルが tool を呼ばないだけの読み取りにくい FAIL を防ぐ)。
- **`check_skill_feature_gate.py`(#42 E7)— 2/2 PASS(2026-07-06)**:
  機能スキル OFF で `/kansai` 非発火(load_skill 呼ばれない)、ON で発火。終了時に既定へ戻す。

答え合わせ方式(check_lazy_tools.py と同パターン):
- E1 計算 / E2 stateful / E3 outputs 自動添付 / E4 timeout(変数保持)/ E5 shell /
  E6 添付取り込み / E7 機能スキルトグル

## 4. 進め方

1. **R2 = #18-3** — **完了(2026-07-06、394b08701)**: sandbox.py 適合版を dev DB に登録、
   基盤バグ 3 件修正、E2E 8/8 PASS。#18 close・#15 結論記載済み
2. **R1 モデル設定再編 + 組み込み機構** — **完了(2026-07-06、ba01a0f1f/40728a2b7/4f8b5e77d)**:
   機能スキルゲート・`/command` スコープ化・builtin 行 sync・T1〜T5 + E7。#42 にロールアップ
3. ~~**履歴ツールの本実装(T6、#11)**~~ → **完了(2026-07-13、`c0b8a14ce`)**。
   スタブの中身を Chats 参照(get_chat_list/get_chat_by_id_and_user_id/検索)に差し替え、
   read_history は get_message_list でスレッド復元。owner-only 固定。builtin-tool 帯に残タスク無し
   (ユーザー判断 2026-07-06)。着手時は E2E に E8(list/read/grep の owner-only)を足す
4. リリースタグ候補点(D 完了)

## 追記(2026-08-16)— 現行 builtin 一覧と attachment_text の追加

> **2026-10-03 更新**: この一覧は当時の 3 本。いまは無条件が 4 本(下の 3 本 +
> `builtin_feed_image`)、env ゲート付きが 2 本(`builtin_sandbox` / `builtin_fetch_url`)。
> 末尾の「改訂(2026-10-03、feed_image / fetch_url の builtin 化)」節を見ること。

無条件に載る builtin は 3 本(**加えて #114 以降は env ゲート付きの `builtin_sandbox`**。
真実源は `nucllei/builtin_tools/__init__.py` の BUILTIN_TOOLS):

- `builtin_history` — list/read/grep_history(自分のチャット履歴)
- `builtin_chat_files` — list/grep_chat_files(現在チャットの添付。**メタデータのみ**・
  chat スコープ — 1 チャットから他チャットのファイルを発見させない)
- `builtin_attachment_text`(#113)— `read_attachment(file_id, offset?, max_chars?)`。
  Attachment 抽出(配備側 function が書く `Files.data.content`)の**読み口**。
  data.content 優先 + text 系 content_type の raw decode フォールバック、offset 窓。
  認可は **owner-only fail-closed の file_id 直指定で chat 非スコープ** —
  子 agent session(#107)は親と別 chat のため、chat 縛りでは資料搬入が成立しない。
  「発見面(一覧)は chat スコープ・読み面(直指定)は owner-only」という分担。
  主用途: サブエージェントの資料搬入(llens ask_subagent の廃止を可能にした)。

## 改訂(2026-09-03、#114)— sandbox の移設と builtin 化(R2 の決着)

**判断の一次根拠は issue #114 の本文**。ここは要点と、本メモの旧記述との対応。

### 何が変わったか

sandbox を **3 層に分けて帰属させ直した**。

| | 帰属 | 実体 |
|---|---|---|
| ランタイム(nsjail イメージ) | **nucllei repo** | `sandbox/` → `ghcr.io/technoplasm/nucllei-sandbox`(本体と同じ workflow・同じ tag・同じ APP_VERSION で焼く。CI = `.github/workflows/docker-build.yaml`) |
| tool(LLM 向け表面) | **nucllei の条件付き builtin** | `nucllei/builtin_tools/sandbox.py` = `builtin_sandbox` |
| quota / prompt / 開放範囲 / compose | 配備層 `prism-hu/llens` | サービス定義・env の実値・host 公開ポート・workspace の GC |

### なぜ 1c の「出荷単位」基準が逆の答えを出したか

起草時(2026-07-06)は「sandbox = 外部サービスへのブリッジ = カスタム」と当てはめた。
実運用で見えたのは逆の事実で、**基準は変えずに答えだけが反転**した:

1. **依存の向きが逆だった**。llens の `tools/sandbox.py` は `nucllei.models.chats` /
   `models.files` / `storage.provider` / `utils.tool_render.Code` / `utils.tool_output.display`
   という**本体の内部 API を掴んでいて**、`#88 未対応の旧 nucllei で display が無くても…`
   という try/except で**ホストの内部 API にバージョン交渉していた**。配備層のコードが
   host の内部を触るのは配置ミスの症状。「本体と一緒にバージョニングされないと壊れるもの」
   = 1c の組み込み側の定義そのものだった。
2. **院内固有物が一つも無い**。同じ棚の `cage.py` / `medical_calculator.py` / `pdf_vision.py`
   とは質が違う(あちらは LLENS 固有の外部システムへのブリッジ)。
3. **出荷単位が境界**。HTTP API 契約(`/exec` `/exec_cell` `/files/*`)が repo を跨いでいると
   「ランタイムと tool のどちらが先に出るか分からない」。同じ repo・同じ tag で出れば交渉が要らない。
4. 実務として、別ホストに立てるたびに 827 行の tool を sync で push し valves を手設定する
   必要があった。ハーネス化のコストを配置が生んでいた。

**却下した案**: nucllei コンテナに sandbox を同梱する。特権境界の内と外が逆転する —
sandbox が `privileged: true` を許されているのは中に nsjail を起こす以外何も無いからで、
一方 nucllei は認証・アップロード・任意文書パース・`/ext` プロキシを持つスタック最大の攻撃面。
同梱すると非特権コンテナ内で止まるパースバグがホスト root に届く(詳細は #114)。

### env ゲート(既存 3 本との違い)

既存 builtin は「nucllei 自身の DB を読むだけ = 外部依存ゼロ = 必ず動く」。sandbox は
外部サービスが要るので**無条件には生やさない**。

- `SANDBOX_URL` が設定されている配備でだけ `BUILTIN_TOOLS` に載り、sync が upsert する
- **既定値は撤去**(既定があると全配備で常に有効になり、ゲートが意味を失う)
- 未設定なら**新機構を足さず**、既存の prune 経路(`builtin_` prefix かつ BUILTIN_TOOLS
  未定義の行を削除)がそのまま片付ける。ログは「repo から消えた」と断定しない文言にした
  — prune の理由が「撤去」か「env ゲートが閉じた」か区別できないため
- **帰結: env を外すと `access_control` 設定が失われる**(行ごと消える)。専用の無効化状態を
  DB に持つより素直、と判断した。再設定は運用の手間として許容
- Valves(timeout / 出力上限等)は維持。`70743aa68` で ValvesModal は builtin/custom
  両方に配線済み。**向き先 `sandbox_url` は 2026-09-05 に valve から外した**(下の改訂節)

素の nucllei を配る相手に、立てていないサービス用の tool を見せない。

### 関数名の現行(旧メモの記述との対応)

`run_python`(stateful)/ `run_python_once` / `run_shell` / **`send_to_workspace`**(旧
`import_chat_file` → `export_to_workspace` を経て現名)/ `write_file` / `list_workspace` /
**`fetch_from_workspace`**(旧 `export_workspace_file` → `import_from_workspace` を経て現名)。
ファイル置き場は `/workspace/imports/` と `/workspace/exports/`(旧 inputs/outputs)。
両者は Files の原本の**期限付きキャッシュ**で、sandbox が最終更新から既定 30 日(env `SANDBOX_CACHE_TTL_DAYS`)で消す
(#149、`memory/` 等は消さない)。モデルへは toolkit 共通注記(`Tools` クラス docstring)で 1 回だけ伝え、
この配下が「無い」ときのエラー文に `send_to_workspace(file_id)` で戻せる旨を足す(`_cache_hint`)。
実際の期限は sandbox が `/files/write` `/exec` `/exec_cell` の応答 `cache_ttl_days` で返し(ken 2026-10-07)、
nucllei が「今日(Asia/Tokyo)+ N 日」を取り込み結果の末尾(`(/workspace/imports/ の写しは YYYY-MM-DD 以降に消える。…)`)と
exports 自動添付の報告ブロック末尾(`(添付は Files に残る。/workspace/exports/ の写しは YYYY-MM-DD 以降に消える)`、
1 件でも添付したときだけ)に 1 行添える(`_cache_expiry`)。キーが無い古い sandbox・0 では添えない。

**`path` 引数は sandbox の fs の絶対パス**(2026-10-03、ken 決定「名前 = 形」: `url` = URL
(絶対、または同一サーバの `/ext/…` — 末尾の「参照の規則の改訂」節)、
`path` = `/workspace/…` `/ext/…` の絶対パス、`file_id` = 添付)。`write_file` / `list_workspace` /
`fetch_from_workspace` の 3 本は以前 workspace 相対で、他の tool の戻り値(絶対)をそのまま渡せず、
`write_file("/workspace/x")` は `/workspace/workspace/x` に黙って書いていた。現行は 3 本とも
`/workspace/…` を受けて絶対で返す(`list_workspace` の各行も絶対)。**相対パスは受けない**
(同日中に改めた — 末尾の「両 repo 通しの検証で直したこと」節。当初は「`/workspace` からの相対」
として通していた)。相対・空・URL・`/workspace` の外は tool 側で固定文で断る
(`/ext/…` は sandbox サービスの `/files/*` が扱えないので「run_shell の ls で」と案内)。
サービスの wire は workspace 相対のまま(変換は tool 側の `_resolve_workspace_path`)。
失敗の文面は `SandboxHTTPError.status` で言い分ける(400 path / 404 不在 / 413 上限 / それ以外の
例外 = 接続不能)。詳細は `sandbox/README.md`「UI tool」。

### 担保

- 単体: `nucllei/test/apps/webui/routers/test_builtin_sandbox.py`(CI の `uv run pytest`)
- ランタイム: `uv run task sandbox-unit`(CI)/ `uv run task sandbox-test`(稼働コンテナ必須、手元)
- E2E: `dev/e2e/check_sandbox_tools.py`(E1〜E6。実 Kimi × 実コンテナ、CI 外)
- **同名関数の衝突に注意**(#18-3 の教訓は今も生きる)。この移設に伴い、衝突源だった
  `dev/tools/sandbox.py`(手動登録用の古いコピー)を削除した

## 改訂(2026-09-04 / 2026-09-05、#132)— sandbox API の共有トークンは opt-in、ゲートは URL のみ

sandbox の HTTP API は**無認証**で、body に書いた `user_id` を無検証で信じていた
(到達 = 任意ユーザーとしてのコード実行・他ユーザー workspace 破棄)。#114 で sandbox が
nucllei の出荷物になり「素の nucllei を配る相手にも届く」ようになったので共有トークンを
入れた。設計の単一情報源は `sandbox/README.md`「セキュリティモデル」。ここは builtin 側の
帰結だけ。

**2026-09-05 に fail-closed → opt-in へ変更(ken 判断)**。当初は「未設定なら sandbox は
起動しない / tool も登録しない」だったが、「キーが無ければ元のまま」= トークンを渡して
いない配備を壊さないことを優先する方針に変えた。トレードオフの事実として、**未設定の
配備は #132 以前と同じ無認証状態のまま**(到達できた者が任意 `user_id` を名乗れる)。

- **env は両側同名 `SANDBOX_TOKEN`**(sandbox サービスと nucllei)。compose で 1 つの値を
  2 サービスに配れば済む。**valve / DB には置かない**(秘密の置き場を増やさない。llens の
  compose が `CAGE_TOKEN` を env にしているのと同じ理由)。**向き先 `SANDBOX_URL` も
  2026-09-05 に env 専用へ揃えた**(次の改訂節)。
- **登録ゲートは `SANDBOX_URL` ただ一つ**(2026-09-04 に一度 URL ∧ TOKEN の AND にしたが
  戻した)。ゲートの基準は 2026-09-03 と同じ「一覧に生えているのに呼ぶと必ずエラー」を
  作らないこと — sandbox 側の認証が opt-in になった今、トークン未設定の組み合わせは
  401 ではなく**普通に動く**ので、ゲートに入れる理由が無い。
- **`_post` はトークンがあるときだけ `Authorization: Bearer` を付ける**(空文字で
  `Bearer ` を送らない)。~~任意 URL を取りに行く `_http_get` には混ぜない~~ — `_http_get` は
  2026-10-03 に撤去(下の「send_to_workspace(url=) を resolver に」節)。
- **設定漏れに気付ける warning**: 「URL はあるのに TOKEN が無い」ときだけ
  `sync_builtin_tools()` の冒頭で `log.warning`「認証なしで接続します」
  (import 時は logging 未設定の可能性があるのでフラグ `_SANDBOX_TOKEN_MISSING` を立てて
  sync で吐く)。tool は生えるので**エラーではなく警告**の水準。sandbox 側も起動ログに
  同種の warning を 1 行出す。
- 401 の tool エラー文は原因(両側の `SANDBOX_TOKEN` 不一致 — 片側だけ未設定を含む)を
  名指しする。**トークン値はログにもレスポンスにも出さない**。
- 回帰: `test_builtin_sandbox.py` に「TOKEN 無しでも登録される」「TOKEN があれば `_post` が
  `Authorization: Bearer` を付け、無ければヘッダ自体を付けない」「401 の文言」。sandbox 側は
  `sandbox/tests/test_auth.py`(`uv run task sandbox-unit` = CI)。

## 改訂(2026-09-05)— `sandbox_url` valve の廃止、向き先は env のみ

`sandbox_url` valve を削除した。**向き先は module 定数 `SANDBOX_URL`(env)だけ**を見る
(`nucllei/builtin_tools/sandbox.py` の `_post`)。

- 根拠(ken 判断): #114 で sandbox は nucllei の出荷物になり、compose で必ず一緒に立つ。
  **admin が実行時に向き先を差し替える正当な用途が無くなった**。
- 副次的効果: **トークン漏洩経路が構造的に消える**。valve だった頃はトークンが env でも
  宛先は DB に載っていたので、admin 権限を取った者が `sandbox_url` を外部に向けて
  `Authorization: Bearer` を回収し、以後 sandbox を直接叩いて任意 `user_id` を名乗れた。
  「env と一致するときだけヘッダを付ける」条件分岐を足すより素直で、**設定も 1 つ減る**。
- **旧配備の DB に `sandbox_url` 行が残っていても壊れない**(実測 2026-09-05):
  valves は `module.Valves(**valves)`(`nucllei/utils/tools.py`)で組み直され、pydantic v2 の
  既定 `extra='ignore'` が未知キーを黙って捨てる。ValidationError にならない。
  **旧データ互換コードは書いていない** — 「落ちない」だけを回帰で固定した。
- 残る valve: `default_timeout_s` / `max_output_chars` / `url_max_mb` / `debug`
  (`url_blocked_cidrs` は 2026-10-03 に撤去 — DB に残った値は同じく `extra='ignore'` で捨てられる)。UI 編集は従来どおり効く。
- 回帰: `test_builtin_sandbox.py::test_destination_is_env_only_and_unreachable_from_db`
  (spec に `sandbox_url` が出ない / `valves/update` に送っても保存されない /
  旧行が DB に残っていてもロードできる / `_post` の宛先は env の値)。

## send_to_workspace(url=) を resolver に(2026-10-03、#141)

ken 決定: モデルに見える tool の引数は意味を固定する — **`url` = URL(スキーム付きの絶対 URL。
同日中に同一サーバの `/ext/…`・`/api/v1/files/{id}/content` も URL として受けることにした —
次の節)/ `path` = sandbox の fs の絶対パス / `file_id` = 添付・生成物**。受け取った形のまま渡し、
モデルに読み替えさせない。`send_to_workspace` をこの規則に揃えた。

- **URL の解決は `image_refs.resolve_ref` 一本**(同日中に url 専用の入口 `resolve_url` へ — 末尾の節)。独自の `_fetch_self`(`/ext/` と
  `EXT_PUBLIC_BASES` 配下を cookie 付きで自オリジンへ)/ `_fetch_lan`(CIDR 拒否リストだけの
  LAN fetch、redirect 3 回)/ `_http_get` / `_blocked_ip_error` / `_derive_name` / `_TooLarge` /
  env `SELF_BASE_URL` / valve `url_blocked_cidrs` / 引数 `__request__` を削除した。
  - 直ったこと: 配備層の `fetch_url` / `feed_image` と同じ URL が同じように通る・同じ理由で
    落ちる。cookie 転送が無いので background 続きターン(リクエスト文脈なし)でも 401 に
    ならない(#141「直す `send_to_workspace(ref)`」)。
  - **挙動変更**: 許可先(`EXT_UPSTREAMS` / `EXT_PUBLIC_BASES`)の外の URL は取り込めない
    (`unknown_upstream`)。旧実装は docker 帯・loopback 以外の LAN を全部取れた。redirect も
    辿らない(旧 LAN 経路は 3 回まで追った)。→ **同日中に「院内の範囲」を足し、さらに同日中に
    撤回して「一般の URL はどこでも」にした**(末尾の 2 節): 登録に当たらない絶対 URL は
    そのホストに直接取りに行き、redirect も 3 回まで辿る。断るのはこのサーバ自身だけ。
  - 取り込み名は URL の basename(decode 済み)。旧実装の Content-Disposition 優先は無くなった
    (`LoadedRef` が応答ヘッダを持たない — 要るなら resolver 側に足す)。
- **上限は valve `url_max_mb` を `max_bytes` に渡す**(既定 50)。失敗は `RefError.code` で
  言い分ける(`_explain_ref_error`。配備層 2 tool と同じ code・同じ言い方 — 同日中に 3 コピーを
  `utils/ref_errors.py` 1 本にまとめ、2 tool も builtin へ。末尾の節)。
- **sandbox から既に見えている物はコピーしない**: `image_refs.mounted_path_for_url(url)` が
  「解決先が file backend で実体が `/mnt` 配下」のときそのパスを返し、tool は取り込まずに
  パスを案内する(cage の図・原本 = `EXT_UPSTREAMS=cage=file:///mnt/cage`)。URL → パスの
  読み替えをモデルにさせず tool が橋渡しする。**sandbox 側に同じ bind があるかは確かめられない**
  — `/mnt` は両者が同じ volume を bind する置き場、という #138 の規約に依存。
- ~~**値の形で振り分ける**(`_classify_source`)~~ — **同日中に撤回**(下の「参照の規則の改訂」節)。
  値の形を見て別の引数に読み替える処理は持たない。

## 参照の規則の改訂(2026-10-03)— 相対 `/ext/…` を URL として受ける / 値の中身で判断しない

ken 決定(同日 2 件)。#139(2026-09-07)の「参照は 3 形・相対 `/ext/…` は受けない(`relative_ext`)」と、
上の節の「値の形で振り分ける(`_classify_source`)」を覆す。**規則の定義は
`nucllei/utils/image_refs.py` の module docstring、英語の正文は `README_API.md` §3.8
「Reference rules」**(ここは決定の経緯だけ持つ)。

規則(要約):

- **引数の名前で解釈が決まる。値の中身を見て別の引数の意味に読み替えない。**
- **`url` = ブラウザが開ける URL**: スキーム付きの絶対 URL、または同一サーバの相対 URL
  (`/` 始まり = 今のサーバの URL。実質 `/ext/{name}/…`)→ `image_refs.resolve_url`
- **`path` = sandbox の fs の絶対パス**(`/workspace/…` `/ext/…`)→ `image_refs.resolve_path`
- **`file_id` = 添付・生成物**(本文リンクは `/api/v1/files/{id}/content`、tool には file_id で渡す)
- **受け取った形のまま渡す**(読み替えない・origin を補わない)

決定 1 — **相対 `/ext/…` を URL として受ける**。cage の図は相対 `/ext/cage/static/X` のままモデルに
渡す(配備層 llens の `read_doc` は origin を補わない)。理由: 生成物の本文リンク
`/api/v1/files/{id}/content` が相対で運用できているのと同じで、相対なら origin や http/https が
変わっても表示が壊れない。origin を補う仕組み(リクエストヘッダから組む・組めないときの
fallback・サブエージェント経由で別ホストになる問題)が要らなくなる。#139 が相対を断った理由
(URL と fs パスが同じ `/` 始まりで同居すると取り違える)は、引数名と文脈(markdown の
リンク・画像か、コードの中か)で防ぐ。既存の絶対 URL(`https://<任意の host>/ext/{name}/…`)は
引き続き解決する。失敗 code `relative_ext` は削除(code の集合から 1 つ減った)。

決定 2 — **値の中身で判断するのをやめる**。`send_to_workspace` の `_classify_source` /
`_already_visible` / `_ERR_RELATIVE_EXT` を削除した:

- `url=` は URL として解決するだけ。UUID・`/workspace/…`・サーバに無い経路は
  どれも同じ「URL として解決できない」+ 固定の案内文(`_URL_ACCEPTS`: url が受ける形 /
  sandbox のファイルは取り込み不要 / 添付は file_id)。値を調べて文面を出し分けない。
- `file_id=` は file の id として引くだけ。URL が来ても url として処理しない(レコード無し)。
- **`url=` の `/api/v1/files/{id}/content` は file に当たる**(相対、または絶対 = host 不問)。
  これは型の当て推量ではなく URL のルーティング — このサーバが実際に持つファイル配信口で、
  本文リンクとして頻出する形。HTTP では取りに行かず、`image_refs.file_id_of_url` で id を取り、
  file_id 経路と同じ読み出し・同じ所有者チェック(`_import_files`)で取り込む。
  絶対形で host を見ないのは `/ext/` と同じ理由(与えられた host には接続しない。他ホストの
  同じ path でも読むのは自サーバの Storage で、所有者チェックを通る)。`/content` の無い
  `/api/v1/files/{id}`(メタデータの JSON を返す API)は配信口ではないので当てない。
- 失敗理由の言い分け(不在 / サイズ超過 / redirect / 上流エラー / 不達 / 許可先の外)は
  解決結果(`RefError.code`)に基づくので残る。「解決先が file backend で `/mnt` 配下 → パスを
  返す」(`mounted_path_for_url`)も値の形ではなく解決結果なので残る。

resolver の入口を分けた: `resolve_url(url, *, max_bytes)`(user を取らない — `/ext/` の公開範囲は
admin 管理で per-user ではない)/ `resolve_path(path, user_id, *, max_bytes)` /
`resolve_ref(ref, user_id, *, max_bytes)`(**保存済みの参照文字列**専用の薄い dispatcher。
`context_images` や image_url の url は引数名を持たないので、ここだけは先頭で機械的に振り分ける:
`http(s)://` と `/ext/` = URL、`/workspace/` = fs、それ以外は `unsupported_form`)。
`resolve_ref` のシグネチャは変えていない(送信直前 inline `file_images.py` が使う)。
配備層の tool は引数名に応じて `resolve_url` / `resolve_path` を呼ぶ形に追従する(llens 側の作業)。

配備の順序: llens の tool は nucllei の resolver を呼ぶ。**nucllei のこの版が配備される前に
llens(cage / read_doc)が相対 `/ext/…` を返し始めると、feed が旧 resolver の `relative_ext` で落ちる**
— nucllei を先に(または同じ機会に)入れ替える。

未対応(dev だけの話): vite dev(:5173)の proxy に `/ext` が無いので、dev でホストの `/mnt` に
資料を置いても FE 経由の `/ext/…` は 404 になる(本番は単一オリジンなので関係ない)。

## fetch_from_workspace(path) が `/mnt/…`(現 `/ext/…`)を受ける(2026-10-03)— パス → 見せられる形の橋渡し

> この節の `/mnt` は 2026-10-03 夜に `/ext` へ改名(末尾の「改訂(2026-10-03 夜)」)。

埋めた穴: モデルが sandbox の中で見つけたファイルを**利用者に見せる**手段が、`/workspace/…` には
あった(`fetch_from_workspace` で file_id、`exports/` の自動添付)が、`/mnt/…`(read-only の参照
データ)には無かった。配備では cage の図・原本が `/mnt/cage/static/…` にあり、同じ物が
`/ext/cage/static/…` で配信されているが、モデルが `/mnt/cage/` を `/ext/cage/` に書き換えるのは
「読み替えない」に反する。ken:「sandbox のファイルを file id に変換するしかない。重複になるけど
しかない。同じモノが ext から配信されているのをうまく伝えるすべがあればよいが」。

決定: `send_to_workspace(url=)` の橋渡し(`mounted_path_for_url`)の**逆向き**を
`fetch_from_workspace(path)` に入れる。値の形ではなく解決結果で分かれる。

| tool | 向き | 同じ実体が両方にあるとき | そうでないとき |
|---|---|---|---|
| `send_to_workspace(url)` | URL → sandbox で開けるパス | コピーせず `/mnt/…` を返す | `/workspace/imports/` にコピー |
| `fetch_from_workspace(path)` | sandbox のパス → 利用者に見せられる URL / file_id | 添付せず `/ext/{name}/…` を返す | Files に登録して添付 |

- `image_refs.url_for_mounted_path(path)`: `RO_MOUNT_ROOT` 配下の実ファイルが EXT_UPSTREAMS の
  file backend の root 配下なら相対 `/ext/{name}/{rest}` を返す。閉じ込めは `_mounted_file` と同じ
  (resolve 済み同士の `is_relative_to`)。rest は percent-encode(日本語・空白・括弧・`#?%`)。
  組んだ URL を `_route_url` → `_ext_file_target` に通して同じファイルに戻ることを確かめてから
  返す(URL の形の規則を二重に持たない)。root が入れ子なら深い方。
- 配信されている → **コピーも添付もしない**。「`<path>` は `<url>` として配信されている。本文に
  この URL を貼る」+ 画像なら `![name](url)`、それ以外は `[name](url)` を返す。display もしない
  (貼るのはモデル。`/workspace` の添付は tool が display するので「貼らない」— ここは逆)。
- 配信されていない `/mnt/…` → `resolve_path`(境界・上限 `EXT_INLINE_MAX_BYTES` は resolver)で
  nucllei 側の同じ置き場から読み、`/workspace` と同じ `_attach`(Files 登録 → 添付 → 画像等は
  display)。sandbox の `/files/read` は workspace 専用なので通さない。
- 失敗の文面は `RefError.code` で分ける(`_explain_mount_error`): `not_found`(不在・ディレクトリ・
  置き場の外を指す symlink を同じ文面で — resolver が同じ code に閉じているので tool も分けない)/
  `too_large`(実サイズと上限、「ファイルは存在する」)。`not_found` には「sandbox からは開けるなら
  run_shell で /workspace にコピーして渡す」を添える — **sandbox にだけ bind した置き場は nucllei
  から読めない**ため(bind の有無は確かめられない。`mounted_path_for_url` と同じ規約依存)。
- `path` は sandbox のパスとしてだけ解く。`/ext/…` や `/api/v1/files/…` が来ても読み替えず
  「/workspace の外」で断る。関数名は変えない(`/mnt` も受けるので名前は実態より狭いが、改名は
  prompt / skill / 既存会話の tool 名を全部動かすので見送り)。
- **公開範囲は広げない**が、URL をモデルに教えることにはなる: backend が配信する範囲は全部
  URL になる。llens は root が `/mnt/cage` で `doc/` と `static/` の 2 本を bind しているので、
  そのままだと `/mnt/cage/doc/*.md`(doc 本文の生ファイル)にも `/ext/cage/doc/…` を返す。
  生ファイルは利用者に配信する設計ではない(置換前の `{{PUBLIC_BASE}}` と frontmatter が入った
  まま。置換済みの本文は cage の read_doc だけが返す — llens セッションの回答)。`doc/` が
  root の下にあるのは sandbox への RO bind と root を共有している副産物で、**/ext ゲートは
  今回以前からそれをログイン済みユーザー全員に配信していた**(per-user 検査なし)。
- **`EXT_FILE_SCOPES`(同日追加)で配備が配信範囲を絞る**: `cage=static`(書式は EXT_UPSTREAMS
  と同じ `name=値`、値は root からの相対ディレクトリ、同じ name の繰り返しで複数)。nucllei に
  `static` は焼かない。URL の形(`/ext/cage/static/X`)は変えずに範囲だけ狭める — root 自体を
  `/mnt/cage/static` にすると URL から `static/` が消え、cage の `{{PUBLIC_BASE}}/static/X`
  (http backend の dev 構成と共通)と合わなくなるため。
  - 規則は `ext_upstreams.ext_file_served(name, resolved)` 1 箇所(resolve 済みの実体が root の
    中 + scope の中)。/ext ゲート(`_serve_local`)・`resolve_url`(`_read_confined` の閉じ込めを
    述語にした)・`mounted_path_for_url`・`url_for_mounted_path` が全部これを通る。範囲外は
    ゲートで 404、resolver で `not_found`、橋渡しは None(`send_to_workspace(url=)` は不在扱い。
    `fetch_from_workspace` は当初「添付側」だったが、同日中に **添付もしない** に改めた —
    末尾の「両 repo 通しの検証で直したこと」節)。
  - `routers/ext.py` の変更は「`is_relative_to(root)` を共有の述語に差し替える」1 行と引数の
    name だけ(個別の分岐は足していない)。
  - sandbox のパスとしての読み(`resolve_path` の `/mnt/…`、jail 内)は範囲と無関係 — 絞るのは
    「利用者に配信する範囲」。
  - fail closed: name が現れたら絞り込みは有効のまま(不正な値だけなら何も配信しない)。
    file backend でない name は起動時 WARNING。起動ログに配信範囲を 1 行出す(`log_mounts`)。
  - **未設定の配備は挙動が変わらない**(root 全体)。
- 担保: `test_image_refs_resolve.py`(`url_for_mounted_path` の境界・入れ子 root・path ⇔ URL の
  往復・/ext ゲート `_serve_local` と同じファイルに当たること・scope のパースと 4 経路への適用)、
  `test_builtin_sandbox.py` 末尾の節(URL を返し Files に登録しない / 配信外・scope 外は添付 /
  失敗の言い分け / path を読み替えない)、`test_ext_proxy.py`(scope 内は配信 / 外は 404 /
  未設定は従来どおり / symlink は実体で判定 / 有効エントリ無しは全 404)。

## 両 repo 通しの検証で直したこと(2026-10-03)— 固定文 / 相対パス廃止 / `reason` / 配信範囲外は渡さない

llens(配備層の tool)と通しで検証して見つかった nucllei 側の修正。規則の定義は引き続き
`nucllei/utils/image_refs.py` の module docstring、英語の正文は `README_API.md` §3.8。

- **完了通知から cage の図が消えるのを直した**: `execution_resume._notice_files` が旧規則
  (「url として載せるのはスキーム付き URL だけ」、`"/"` 始まりは全部落とす)のままで、相対
  `/ext/cage/…png` が background 実行の完了通知から落ちていた。保存済み参照の振り分けを
  **`image_refs.stored_ref_kind` 1 箇所**に置き(`file_url` / `url` / `path` / `file_id` / None)、
  `_notice_files`・`content_blocks.py`(履歴の組み立て)・`resolve_ref` がこれを使う。独自の
  `startswith("/")` 判定は無くした。通知に載せるのは「ブラウザが画像として開ける物」— URL
  (`http(s)://…`・`/ext/…`)と裸の file id。sandbox のパスと **`#page=N` の付いた参照**(PDF の
  ページ。送信直前に画像化される物で img としては開けない)は落とす。後者は絶対 URL +
  `#page=N` も落とすようになった(従来は壊れた img として載っていた)。
- **相対パスを受けない**(llens と合意): `write_file` / `list_workspace` / `fetch_from_workspace`
  の `path` は絶対パスだけ。相対を「`/workspace` からの相対」として通すと
  `write_file(path="https://x/y.html")` が `/workspace/https:/x/y.html` を作る。
  `list_workspace` の無指定の既定は `/workspace`。llens の `query_emr(save_to=)` も絶対のみ。
- **固定文を公開定数にした**(llens と文面を合意。`image_refs.py`): `URL_ARG_FORM` /
  `PATH_ARG_FORM` / `WORKSPACE_PATH_ARG_FORM`(`write_file` / `list_workspace` 用)/
  `FILE_ID_ARG_FORM` / `REF_ARGS_RULE`(行き先の規則。全 tool 共通の 1 文)。配備層の tool は
  これを import して同じ文面を使う。エラーの組み立ては全 tool 共通:
  - url: `ERROR: <値> は URL として解決できない。` + `URL_ARG_FORM` + ` ` + `REF_ARGS_RULE`
  - path: `ERROR: <値> は sandbox のパスの形でない。` + `PATH_ARG_FORM`(または
    `WORKSPACE_PATH_ARG_FORM`)+ ` ` + `REF_ARGS_RULE`
  - file_id: `ERROR: file_id <id> のレコードが無い、またはアクセス権が無い。` + `FILE_ID_ARG_FORM`
    (「無い」と「権限が無い」を言い分けない — 存在の有無を漏らさない。`read_attachment` も
    同じ `ref_errors.file_id_error` を raise する — 2026-10-04 #145 で `{ok: False, error}` を廃止)
  - **2026-10-04 改訂(llens staging のレビュー)**: `REF_ARGS_RULE` は取り違えらしいとき
    (url にスキーム無し・path に URL / id・file_id に UUID でない値)だけ添える
    (`image_refs.ref_args_rule_for`。相対パスの path に付いていたのは無関係な定型文だった)。
    file_id は事実の文に「(どちらなのかは区別できない)」を足し、次の手を説明文
    (`FILE_ID_ARG_FORM`)から「list_chat_files / grep_chat_files で確かめて渡し直す・ユーザーには
    開けなかったと伝え、無いとは言い切らない」に替えた(`FILE_ID_ARG_FORM` は UUID でない値の
    ときだけ前に添える)。traversal の次の手も受ける形の説明文から呼び直し方に。現行の文面は
    README_API §3.8
  - tool 固有の行き先(「sandbox のファイルは取り込み不要」「添付は file_id= に」)はエラーから
    消した。旧 `_URL_ACCEPTS` は「添付・生成物は file_id= に渡す」と言いつつ
    `/api/v1/files/…` を url で受けていて自己矛盾だった。
  - `list_workspace` / `write_file` の `/mnt` 向け案内(「run_shell の ls で」)は、同じ `path`
    引数の中の区分で解決結果に基づくので残した。
  - `read_attachment` が抽出の無いファイルに返す文は「抽出テキストが無い(content_type=…)」の
    事実だけ。旧文面の「視覚が要るものは feed_image」は種類を決めつけた誘導で、音声には誤り。
- **`RefError.reason`**: `unknown_upstream` の内訳を安定したフィールドで返す(`unknown_name` /
  `not_ext_form` / `outside_public_bases` — 後者は同日の「一般の URL」で `server_self` に
  置き換わった。閉じた集合。他の code では None)。code の集合は
  変えていない。llens の tool が文面の出し分けのために resolver の正規表現を複製して値を
  再パースしていたのを不要にする。`detail` は人向けで文面は固定ではない(分岐に使わない)。
  未知の name の判定は `_route_url` に寄せた(`_load_ext` から移動)。
- **`route_of_url(url)`**(llens の要望): URL を解決規則と同じ `_route_url` で分解して
  `UrlRoute(name, rel, query, public_base, origin, ext_form)`(+ `relative`)を返す公開関数。
  読まない純粋な関数で、失敗は `resolve_url` と同じ code / reason。配備層の `fetch_url` が
  リンク一覧のルートを組むために `/ext/{name}/…` の分解・`match_public_base`・`ext_backend` を
  自前で複製していた(その結果、同じ入力に `feed_image` と違うエラー文面を返していた)。
  配信範囲・実在は見ない(`resolve_url` の責務のまま)。
- **配信範囲の外は添付でも渡さない**: `EXT_FILE_SCOPES=cage=static` でも
  `fetch_from_workspace("/mnt/cage/doc/x.md")` は「配信されていない `/mnt`」として添付にして
  いた(上の節の「範囲外は … 添付側」)。file backend の root 配下にあって、どの backend の配信
  範囲にも無い物は、URL も返さず添付にもしない(`ext_upstreams.ext_file_withheld` →
  `image_refs.mounted_path_withheld`)。配信範囲の設定が「利用者に渡してよい範囲」として
  1 つの意味を持つ。どの backend の root 配下でもない `/mnt/…`(`/mnt/shared/…`)は従来どおり
  添付、scope 未設定なら従来どおり URL。実在は見ない(範囲外なら無いファイルでも同じ文)。
  **限界**: モデルが `run_shell` で `/workspace` にコピーしてから添付する経路は塞げない
  (エラー文で「コピーして渡すこともしない」と言うだけ — 規則であって境界ではない)。
- **`/api/v1/files/{id}/content` の fragment**: 意味を持つのは末尾の `#page=N` だけ
  (`split_page`)。他の fragment は無視して同じ file に当てる — `file_id_of_url` が fragment
  付きでも id を返すようにした(従来は呼び手が `split_page` で外す前提で、`#page=N` 以外が
  付くと `unsupported_form` になる tool と通る tool があった)。
- **橋渡しの往復の注記**: `url_for_mounted_path` は percent-encode した相対 URL を返すので、
  cage の本文にある生の日本語 URL とは文字列が一致しない(同じファイルの別表記)。
  `fetch_from_workspace` の docstring(モデルに見える部分)と README に 1 行。
- **spec ビルダの制約を README_API §5.1 に明記**: description は最初の `:param` / `:return` の
  前まで(`:return:` は丸ごと見えない)、`:param` は 1 行目だけ。`read_attachment` の戻り値の形は
  `:return:` にだけ書かれていてモデルに届いていなかったので本文へ移した。builtin の他の tool と
  harness tool(`run_subagent` / skill / task 系 — 手書きの spec dict)に同種の漏れは無かった。
- **name の直下(rel が空)も上流に問い合わせる**(staging の実走で発覚): 原本の
  `<base>/`(`EXT_PUBLIC_BASES` のルートそのもの)を `_route_url` が上流に問い合わせずに
  `not_found` にしていて、`fetch_url("https://…/his-info/")`(上流は 302 → `top.aspx`)が
  「リンク切れ」と誤案内された(#139 で EXT_PUBLIC_BASES を入れたときからの挙動で、今日の
  変更による退行ではない)。`/ext/{name}`・`/ext/{name}/`・`<base>`・`<base>/` はどれも
  「その name のルート」= `rel ""` として、他の rel と同じに backend へ渡す: http backend は
  上流の `<base>/` を GET(redirect なら `upstream_status` + status)、file backend は root =
  ディレクトリなので `not_found`(一覧は出さない)。末尾 `/` の有無は区別しない
  (`match_public_base` が base そのものにも当たる)。**`not_ext_form` の定義を狭めた**: name が
  取れない形(`/ext/`・`/ext//x`・文字種の外)だけ。`/ext/{name}` と `/ext/{name}/` は
  正しい形になった(従来は `not_ext_form`)。`route_of_url` も直下で成功し `rel == ""`
  (従来 `<base>/` は `not_found`)。/ext ゲートは `/ext/{name}/` を元から `<base>/` へ中継して
  いた(file は 404)ので resolver がそれに揃った形。末尾 `/` 無しの `/ext/{name}` だけは
  ルートに当たらず SPA の catch-all に落ちていたので、ゲートに `/{name}` のルートを足した。
  builtin の `send_to_workspace(url=)` の `not_ext_form` の文面(「`/ext/<配信元>/<パス>` の
  形でない」)は未変更。
- 担保: `test_image_refs_resolve.py`(定数の文面 / `stored_ref_kind` / `route_of_url` が
  `resolve_url` と同じ code・reason で落ちること / `mounted_path_withheld` / `file_id_of_url` の
  fragment / reason)、`test_builtin_sandbox.py`(相対パスを断る / 3 つの引数のエラーの形 /
  reason での言い分け / 配信範囲外を断る / spec の `:param` が固定の言葉で 1 行)、
  `test_builtin_tools.py`(`read_attachment` の文面)、`test_execution_resume.py`(完了通知に
  `/ext/…` が載る)。
- 残っている物(今回は触っていない): `subagent.request_origin` / exec 行 `meta["origin"]` /
  `internal_request(origin=)` は「tool が `__request__` から自オリジンを組む」ための仕組みで、
  主な利用者だった llens doc tool は相対 `/ext/…` に切り替えて origin を補わなくなった。
  llens の現行 tool に `X-Forwarded-*` を読むものは無い(2026-10-03 に grep で確認)。
  利用者が居ないなら撤去の候補。
  → **同日に撤去した**(ken 裁定): `request_origin` / `internal_request(origin=)` /
  exec 行 `meta["origin"]` / `execute_tool_calls(origin=)` / resume 側の origin 引数。
  通常ターン・子ターンの `__request__` は従来どおり、続きターンの `__request__` は origin を
  持たない。詳細は `async-exec-design.md` 追補 7。

## 院内の範囲(EXT_INTRANET_CIDRS)— 登録した配信元の外の院内 Web も拾う(2026-10-03、**同日に撤回**)

> **2026-10-03 撤回**(ken 決定、llens セッション経由): この節の仕組みは同日の午後に丸ごと
> 外した(`EXT_INTRANET_CIDRS` / `parse_ext_intranet_cidrs` / `INTRANET_DENIED_NETWORKS` /
> `pin_intranet_address` / reason `outside_intranet` `outside_public_bases` `redirect_outside` は
> 全部無い)。理由と現行の規則は下の「一般の URL」節。以下は経緯として残す — **現行の仕様として
> 読まない**。

`send_to_workspace(url=)` を resolver に一本化した結果、許可先(`EXT_UPSTREAMS` /
`EXT_PUBLIC_BASES`)の外の院内 URL が取り込めなくなっていた(旧 `_fetch_lan` は loopback・
docker 帯以外の LAN を全部取れ、redirect を 3 回まで追った)。ken:「院内 Web も普通に拾えて
いいよ」/「プロンプトで外部接続を試みると失敗で」/「院内は全て 172.16 のセグメントで払い
出してる。閉域の本番は 10. のセグメントに変えてある」/「MiNet(閉域の本番)には DNS も無い」。
規則の定義は `nucllei/utils/image_refs.py` の module docstring「院内の範囲」、英語の正文は
`README_API.md` §3.8「The intranet range」(ここは決定の経緯と配備への申し送りだけ)。

決定:

- **配備が範囲を宣言する**: env `EXT_INTRANET_CIDRS`(CIDR のカンマ区切り。prefix 無しは
  /32・/128。`env.parse_ext_intranet_cidrs`)。**既定は空 = 院内の範囲は無い**(従来どおり
  fail closed)。範囲は環境ごとに違う(staging は `172.16.0.0/16`、本番の閉域は `10.` の
  セグメント = `10.0.0.0/8` か運用が決めるもっと狭い範囲)ので nucllei に既定を焼かず、
  各配備の `.env` に書く。
- **名前空間が先、院内の範囲は最後**: `/ext/{name}/…`・`EXT_PUBLIC_BASES` 配下は従来どおり
  登録した配信元の規則(backend 経由、redirect は辿らない — 今日 staging で「転送された」
  の案内を確認済みの挙動を変えない)。どれにも当たらない絶対 URL だけが院内の規則へ。
  同じホストでも `<base>/` の中と外で規則が変わる(中 = 配信元、外 = 院内の一般 URL)。
- **外は失敗のまま**: 範囲の外(インターネット・他 LAN)は `unknown_upstream` に新しい
  reason `outside_intranet`(範囲未設定の配備は従来の `outside_public_bases`)。配備が外部
  ネットワークに出られる環境でも範囲に無ければ失敗 — 外に出したければ範囲に足す。
  `send_to_workspace` の固定文は「院内の範囲の外 (この配備が院内として設定したアドレス
  範囲に無い)。外部の Web は取りに行かない。」(値は見ない。解決結果に基づく)。
- **IP リテラルが主経路**: 閉域の本番には DNS が無く、院内の URL は `http://10.x.x.x/…`。
  リテラル(v4 / v6、`[]` 付き)は名前解決せずにそのまま範囲判定して接続する。ホスト名は
  `getaddrinfo`(= `/etc/hosts` も効く)。引けなければ `upstream_unreachable` +
  reason `unresolvable`、tool の固定文は「ホスト名を解決できない (この環境に DNS が無い
  場合は、ホスト名ではなく IP アドレスで指定する)」。
- **接続先は解決したアドレスに固定**(`ext_upstreams.pin_intranet_address`): 範囲内の
  IP に接続し、Host ヘッダ / SNI(https)は元のホスト名。httpx に名前で繋がせない(DNS
  rebinding で 2 度目の解決が範囲の外を指しても繋ぐ先は変わらない)。範囲内と拒否リストを
  同時に返す名前は取らない。
- **拒否リスト**(`ext_upstreams.INTRANET_DENIED_NETWORKS`。設定に含めても常に除外、重なりは
  起動時 WARNING): `0.0.0.0/8` / `127.0.0.0/8` / `169.254.0.0/16`(クラウドのメタデータ
  169.254.169.254 を含む)/ **`172.17.0.0/16`(docker の既定 bridge だけ — `172.16.0.0/16` は
  巻き込まない。/12 で宣言すれば 172.17.x だけ落ちる。テストで固定)** / `224.0.0.0/4` /
  `240.0.0.0/4` / `::` / `::1` / `fe80::/10` / `ff00::/8` / `fd00:ec2::254`(AWS IMDS v6)。
  IPv4-mapped IPv6(`::ffff:127.0.0.1`)は v4 として判定。
  - cage の API(llens の `CAGE_API_BASE`、既定 `http://172.17.0.1:13303`)は tool の内部
    通信で resolver を通らないので拒否リストと無関係(影響なし)。
- **redirect**: 院内の URL は 3xx + Location を最大 3 回辿る(`INTRANET_MAX_REDIRECTS`)。
  転送先は URL として同じ規則で解決し直す(リテラルはそのまま、名前は引いてから範囲判定):
  院内 → 辿る(また固定)、登録した配信元 → その backend で 1 回だけ(そこから先は辿らない)、
  どちらでもない(範囲外・拒否リスト・http(s) でない・制御文字)→ `upstream_status`
  (転送を返した 3xx の status、reason `redirect_outside`)。4 回目 → `upstream_status`
  reason `too_many_redirects`。登録した http backend の挙動は不変(3xx は reason 無しの
  `upstream_status`、辿らない)。
- **上限・種別・404・5xx・接続失敗**は http backend と同じ(`_read_response` を共有)。
- **`route_of_url`**: 院内の一般 URL は `UrlRoute(kind="intranet", name=None, rel=<path の
  先頭 / 抜き>, query, public_base=None, origin, ext_form=False)` + property `intranet` /
  `page_url`(= `{origin}/{rel}{query}`。リンクを解決する基準 — 配信元のルートは無い、
  通常の Web と同じ)。既存の形は不変(`kind` は既定 `"upstream"`、`page_url` は None)。
  純粋な関数のまま **DNS は引かない** — 範囲が設定された配備では名前空間に当たらない絶対
  URL をすべて intranet 候補として返す。範囲内かは `resolve_url` が決める。`resolve_url` の
  成功後に呼ぶ用途(llens `fetch_url._link_roots`)ではずれない。
- `mounted_path_for_url` / `url_for_mounted_path` / `/ext` ゲートは無関係(`mounted_path_for_url`
  は intranet route で None)。

llens への申し送り(配備層):

> **2026-10-03 更新**: 下の 2 項目(`fetch_url._link_roots` の intranet 対応・ERROR 文の reason
> 追加)は、同日中に `fetch_url` が `builtin_fetch_url` として nucllei へ移ったので **nucllei 側の
> 仕事になった**(末尾の節)。llens に残る申し送りは `.env` の範囲設定だけ。

- compose / `.env` に範囲を足す(環境ごとに値が違う。staging の例):

  ```
  EXT_INTRANET_CIDRS=172.16.0.0/16
  # 本番の閉域(10. のセグメント。運用が決めるもっと狭い範囲でもよい。複数はカンマ区切り)
  # EXT_INTRANET_CIDRS=10.0.0.0/8
  ```

  本番は DNS が無いので院内の URL は IP リテラル(`http://10.1.2.3/his-info/top.aspx`)。
  `EXT_PUBLIC_BASES` の原本 URL も本番では IP になりうる。
- `fetch_url._link_roots`: `route_of_url` が `route.intranet`(`name is None`)を返したら、
  リンクのルートは `/ext/{name}` でも `public_base` でもなく **そのページ自身の URL
  `route.page_url`** を基準に `urljoin` で解決する(通常の Web と同じ。`_base_prefix` に
  `name=None` を渡さない)。今の実装のままだと `link_base = f"{origin}/ext/None"` になる
  (落ちはしないが壊れたリンクを出す)。
- ERROR 文の出し分けに新しい reason を足す: `unknown_upstream` / `outside_intranet`
  (「院内の範囲の外。外部の Web は読めない」)、`upstream_status` / `redirect_outside`・
  `too_many_redirects`、`upstream_unreachable` / `unresolvable`(「名前を解決できない — DNS
  の無い環境では IP で指定」)。固定文は nucllei の `send_to_workspace`(`_explain_ref_error`)と
  揃える。
- 担保(nucllei): `test_image_refs_resolve.py` 末尾の節(パース / 拒否リストと 172.16 /
  範囲内の取得と固定・Host・SNI / IP リテラル主経路(DNS を呼ばない)/ 範囲外・拒否・混在 /
  引けない名前 / 未設定は従来どおり / 名前空間の優先 / redirect 3 回・4 回目・範囲外への転送・
  配信元への転送 / 配信元は辿らない / 上限・404・5xx・不達 / `route_of_url` の形 /
  起動 WARNING)、`test_builtin_sandbox.py`(範囲内の取り込みと redirect / 固定文)。

## 改訂(2026-10-03、feed_image / fetch_url の builtin 化)— 配備層 2 tool を nucllei へ

ken 決定(2026-10-03)。prism-hu/llens の custom tool `nucllei/tools/feed_image.py` /
`fetch_url.py` を、nucllei の組み込み **`builtin_feed_image`** / **`builtin_fetch_url`** へ移した
(`nucllei/builtin_tools/`)。関数名は `feed_image` / `fetch_url` のまま。2026-07-22 節で
「view_image は builtin から撤去し prism tool `feed_image` へ」とした判断の**再反転**で、
#114(sandbox)と同じ理由構造。英語の正文は `README_API.md` §5.7(一覧)と §3.8(固定文の節)。

### なぜ移すか

- **規則は既に nucllei にある**: 参照の解決(resolve_url / resolve_path / file_id_of_url)・
  許可先(`EXT_UPSTREAMS` / `EXT_PUBLIC_BASES`)・院内の範囲(`EXT_INTRANET_CIDRS`)・redirect・
  サイズ上限・リンクのルート(`route_of_url`)は全部 `utils/image_refs.py` / `utils/ext_upstreams.py`
  が持つ(#139 → #141 で寄せきった)。llens に残っていた 2 tool は「resolver を呼んで結果を
  文にする殻」だけだった。
- **殻が repo を跨ぐと規則が二重になる**: `_link_roots` が `/ext/{name}/…` の分解・
  `match_public_base` を自前で複製していた(上の「参照の規則の改訂」節)、ERROR 文の出し分けが
  `_explain_ref_error` として feed_image / fetch_url / send_to_workspace の **3 コピー**にあって
  同じ入力に違う文面を返した、`EXT_INTRANET_CIDRS` を足すたび llens 側に「申し送り」が要った
  (上の節の `_link_roots` / reason 追加)。
- **版ずれの分岐が消える**: llens の tool は `resolve_url` / `file_id_of_url` の無い旧 nucllei を
  try/except で検出して「この nucllei の版では使えない」の固定文を返していた(`_OLD_RESOLVER`)。
  builtin は nucllei と同じ tag で出荷されるので、この種の交渉が要らない(#114 の「出荷単位が
  境界」と同じ)。
- **院内固有物が無い**: 2 tool に LLENS の知識はない(許可先・原本ホスト・院内の範囲は env の値、
  「doc に収録済みの資料は fetch_url で取らない」等の使い分けは prompt / skill の文)。

### 何が nucllei に来て、何が llens に残るか

| | 帰属 | 実体 |
|---|---|---|
| tool(LLM 向け表面・検証・文面) | **nucllei の builtin** | `nucllei/builtin_tools/` の `builtin_feed_image` / `builtin_fetch_url` |
| ERROR 文(形の固定文 + `RefError.code` / `reason` の言い分け) | **nucllei** | `nucllei/utils/ref_errors.py` 1 本(旧 3 コピーを統合。**文面は変えていない**。`send_to_workspace` も同じ関数を使う) |
| env の実値(EXT_* を compose で渡す)・prompt / skill・配備固有の知識 | 配備層 `prism-hu/llens` | compose / `.env` / `nucllei/prompts/` / `nucllei/skills/` |

### 固定した設計(再決定しない)

- **id とゲート**: `builtin_feed_image` も `builtin_fetch_url` も**常時登録**。fetch_url は移設の
  時点では「`EXT_UPSTREAMS` / `EXT_PUBLIC_BASES` / `EXT_INTRANET_CIDRS` のいずれかが設定されている
  ときだけ」のゲート付きだったが、同日の「一般の URL はどこでも」(下の節)で env が空でも
  必ず失敗する builtin ではなくなったので外した。
- **引数**: `feed_image(url | path | file_id, filename?, pages?)` / `fetch_url(url, query?, offset?,
  limit?, max_mb?)`。参照の規則(#141)は不変 — `url` = 絶対 URL か同一サーバの `/ext/…`・
  `/api/v1/files/{id}/content`、`path` = sandbox の絶対パス、`file_id` = 添付・生成物。
  **解釈は引数名だけ**で決め、値の形で読み替えない。
- **valve は維持**(admin が DB で編集できる tool valve): feed_image `max_image_mb=8` /
  `max_pages=3`、fetch_url `max_fetch_mb=25` / `max_fetch_mb_ceiling=200` / `max_output_chars=40000` /
  `pdf_text_char_threshold=100`。許可先の valve は持たない(真実源は env — 2026-09-07 レビューの
  「valve と docstring の許可先が割れる」を再発させない)。
- **HTML の平文化は lxml で汎用**: charset は Content-Type にあればそれ、無ければ utf-8
  (自動判定はしない — his-info で数ページだけ別 encoding に推定され全文化けた実測、llens 時代の
  メモ)。HTML に markitdown は通さない(レイアウト table をそのまま Markdown 表に起こして
  膨れる — his-info の実測で markitdown 191K 字 vs 自前変換 70K 字)。PDF のテキスト層は markitdown。
- **文面は不変**: `ref_errors.py` へ統合しても ERROR 文は 1 文字も変えない(prompt / skill /
  既存テストが文面を見ている)。

### 経緯の対応

- 2026-07-21: view_image を builtin 化(#89)→ 2026-07-22: 撤回して prism tool `feed_image` へ
  (「権限チェックは owner チェックで足りるので builtin である必然性が無い」)→ **2026-10-03: 戻す**。
  当時の論点は「builtin でなくても書ける」だったが、今回の論点は「規則が nucllei にあるのに殻だけ
  外に置くと二重になる」で、別の基準。
- 2026-08-26 / 09-04 / 09-07 / 10-03 の llens 側の実測知見(原本 PDF の feed、拡張子でなく
  content-type / magic bytes で種別判定、valve でなく env を真実源に、utf-8 決め打ち)は
  各 builtin の dev コメントに持ち込んで保存する(llens の docstring が spec だった)。
- llens 側: `nucllei/tools/feed_image.py` / `fetch_url.py` は撤去(sync 対象から外す。sandbox と
  同じく custom の DB 行は `nuc-sync` が prune しないので、既存配備では旧 custom tool の手削除と
  preset の `meta.toolIds` を `builtin_feed_image` / `builtin_fetch_url` に張り替える admin 作業が要る
  — [[dev-environment]] の 2026-07-22 追補と同じ罠)。

## 一般の URL — `url=` はどこでも取りに行く / 断るのはこのサーバ自身だけ(2026-10-03、院内の範囲を撤回)

ken 決定(2026-10-03、llens セッション経由。同日の「院内の範囲」節を**撤回**)。英語の正文は
`README_API.md` §3.8「General URLs」、規則の定義は `nucllei/utils/image_refs.py` の module
docstring「一般の URL」。ここは決定の経緯と、配備への申し送りだけ。

決定:

- **`url=`(fetch_url / feed_image / send_to_workspace)はスキーム付きの `http(s)://` URL なら
  どこでも取りに行く** — 院内でもインターネットでも。「閉域だから外には届かない」は prompt /
  skill に書く文であって、tool のゲートではない(届かなければ `upstream_unreachable`)。
  行き先で機能を絞らない。
- **同日の朝に入れた `EXT_INTRANET_CIDRS`(宣言した範囲の中だけ取る)は同日に撤去**。
  理由は 2 つ: (1) 行き先で機能を絞っていた(配備が外に出られる環境でも範囲に無ければ失敗 —
  ken の方針に反する)。(2) 固定 CIDR の拒否リスト(docker の既定 bridge `172.17.0.0/16`)は
  本番の bridge が `10.106.x` なので**効かない**上に、固定で private 帯を書けば本物の院内
  (MiNet は `10.`、staging は `172.16.`)を巻き込む。env・パーサ・`INTRANET_DENIED_NETWORKS`・
  `pin_intranet_address`・reason `outside_intranet` / `outside_public_bases` / `redirect_outside` は
  全部無い。`.env.example` / README の env 表からも消した。
- **唯一断るのは「このサーバ自身(と同居するサービス)」**(`unknown_upstream` reason
  `server_self`。redirect の転送先なら `upstream_status` reason `redirect_self`)。ホストの
  unauthenticated な cage API(:13303)・sandbox API・クラウドのメタデータが相手。その集合は
  **実行時に求める**(`ext_upstreams.discover_self_addresses` → `self_addresses`、60 秒キャッシュ):
  - 固定で分かる「正当な行き先になり得ないアドレス」: loopback(127/8・::1)、unspecified
    (0/8・::)、リンクローカル(169.254/16 = メタデータ 169.254.169.254 を含む・fe80::/10)、
    multicast / 予約(224/4・240/4・ff00::/8)。**ネットワークポリシーではない** — private 帯
    (10/8・172.16/12・192.168/16)も docker 帯も書かない。
  - 実行時: 自分のインターフェースの全アドレス(`/proc/net/fib_trie` の `LOCAL` 葉 +
    `/proc/net/if_inet6` + `getaddrinfo(gethostname())`)、既定ゲートウェイ(`/proc/net/route` の
    RTF_GATEWAY + `/proc/net/ipv6_route`。コンテナでは docker bridge のホスト側 = ホストで
    待ち受ける同居サービスの口)、`host.docker.internal` / `gateway.docker.internal` の解決結果
    (引けなければ無視)。新しい依存は無し(psutil を入れない)。
  - **限界**: Linux 以外(macOS の開発機)は /proc が無く hostname の分だけ(ゲートウェイは
    取れない)。ホストが bridge のゲートウェイでも自分のアドレスでもない**別のインターフェース**
    (ホストの LAN 側 `10.106.x.y` 等)で待ち受ける同居サービスは拾えない — そういう配備では
    同居サービス側が認証を持つ(sandbox の `SANDBOX_TOKEN` と同型)。発見した集合は起動時に
    1 行ログ(`log_mounts`)。
- **接続先の固定(DNS rebinding 対策)は維持**(`ext_upstreams.pin_address`): 解決した IP に
  接続し、Host ヘッダ / SNI は元のホスト名。解決結果の 1 つでも自分自身なら取らない
  (外と loopback を同時に返す名前は怪しい)。採るのは最初のアドレス(OS の優先順)。
- **redirect は 3 回まで**(`MAX_REDIRECTS`)、各ホップで同じ自分自身の判定。転送先が自分自身 →
  `upstream_status` + `redirect_self`。転送先が URL として組めない(http(s) でない・制御文字・
  未知の `/ext/{name}/`)→ reason 無しの `upstream_status`(辿らなかった redirect)。
- `route_of_url` の `kind="intranet"` / property `intranet` は **`kind="general"` / `general`** に
  改名(`fetch_url._link_roots` が読む)。`page_url` は同じ。
- `builtin_fetch_url` のゲートを外して常時登録(上の「固定した設計」を更新)。docstring の
  `{PUBLIC_BASES}` は「登録された原本ホスト = その配下のリンク一覧のルートになる配信元」として
  残す(許可先の一覧ではない)。ERROR 文の `unknown_upstream_extra` も「登録された原本ホストは …」。
- 現行の reason の集合: `unknown_upstream` = `unknown_name` / `not_ext_form` / `server_self`、
  `upstream_status` = `redirect_self` / `too_many_redirects`(/ None)、`upstream_unreachable` =
  `unresolvable`(/ None)。`ref_errors.UNKNOWN_UPSTREAM_WHAT` もこの集合。
- 担保: `test_image_refs_resolve.py` 末尾の節(env に CIDR が無い / 固定の集合と発見した集合 /
  TTL キャッシュ / `/proc` と hostname の読み / env 無しで一般 URL を取る・固定・Host・SNI /
  IP リテラル主経路 / 自分自身の拒否(loopback・自分・ゲートウェイ・host.docker.internal・
  混在)/ 引けない名前 / 名前空間の優先 / redirect 3 回・4 回目・自分自身への転送・組めない
  転送先・配信元への転送 / 配信元は辿らない / 上限・404・5xx・不達 / `route_of_url` の形 /
  起動ログ / 差し替え無しの loopback)、`test_builtin_fetch_url.py`(一般 URL は取る・自分自身は
  断る・常時登録)、`test_builtin_feed_image.py` / `test_builtin_sandbox.py`(reason の文面)、
  `test_ref_errors.py`(文面の恒等)。

llens への申し送り(配備層):

- `.env` / compose から `EXT_INTRANET_CIDRS` を消す(残っていても読まれないだけ)。
- prompt / skill 側に「閉域の本番では外部サイト(インターネット)には届かない」を書く
  (tool は断らないので、届かなければ「届かない」の ERROR になる)。
- cage の API がホストの `0.0.0.0:13303` で認証無しなら、nucllei コンテナからは
  bridge のゲートウェイ経由で届きうる — ゲートウェイは実行時発見で拒否されるが、ホストの
  LAN 側アドレス経由は拾えない(上の限界)。cage 側の認証を検討。

## 改訂(2026-10-03 夕)— `/ext` の設定を全部無くす: `/mnt` をそのまま配信 / `SELF_ADDRESSES` / `FETCH_MAX_BYTES`

ken 決定(同日 3 段階で収束): **「nucllei の `/mnt` に volume したものをそのまま `/ext/` で配信する」**。
上の節々にある `EXT_UPSTREAMS`(name ごとの http 中継 / `file://` root)・`EXT_FILE_SCOPES`
(root からの相対の配信範囲)・`EXT_PUBLIC_BASES`(原本 URL のルート → リンク一覧のルート)は
**全部撤去**(途中で `EXT_DIRS`(`name=/abs/path` → docker volume 風 `<dir>:<mount>`)に畳む案を
2 段階経て、最後に「設定そのものが要らない」に落ちた)。URL の形は変わらない(`/ext/cage/static/X`)。

理由: 同日の「`url=` は http(s) URL ならどこでも」(上の「一般の URL」節)で、http 中継
(`EXT_UPSTREAMS=his=http://…`)と原本ホストの対応(`EXT_PUBLIC_BASES`)の存在理由が消えた —
院内 Web は原本 URL をそのまま渡せばよい。残るのは cage の図・原本(ローカルの静的ファイル)の
配信だけで、それは「nucllei に bind した物」と同じ集合。配信範囲(`EXT_FILE_SCOPES`)も
「配信したくない物を nucllei 側に mount しない」で足りる(sandbox 側にだけ bind すれば、
モデルは `/mnt` で読めるが利用者には渡らない)。

何がどうなったか:

- **ゲート `routers/ext.py`**: `GET /ext/{rest}` → `RO_MOUNT_ROOT/{rest}`(`image_refs.RO_MOUNT_ROOT`
  = `/mnt`、定数。env での差し替えは無い)。閉じ込めは resolve 済みの実体が `/mnt` の中の通常ファイル
  (resolver の `_read_confined` と同じ)。ディレクトリ(`/ext/` 自体を含む)・不在・symlink 越えは 404、
  traversal は 400。http 中継(httpx ストリーミング・`PASSTHROUGH_RESPONSE_HEADERS`・302 → 502)は
  コードごと消えた。
- **resolver `utils/image_refs.py`**: `EXT_URL_PATH_PATTERN = ^/ext(?:/(.*))?$`(name の概念が無い)。
  `_route_url` は `/ext/{rest}` を `UrlRoute(rel=rest, query, origin, kind="ext")` にするだけで
  失敗しない — `/ext/…` の失敗は `not_found` だけ。**reason `unknown_name` / `not_ext_form` は無い**
  (`ref_errors.UNKNOWN_UPSTREAM_WHAT` は `server_self` と None の 2 行。sandbox の表も同じ)。
  `UrlRoute` は `(rel, query, origin, kind)` — `name` / `public_base` / `ext_form` を落とし、
  `kind` は `"ext"` / `"general"` / `"file"`(`/api/v1/files/{id}/content` — `route_of_url` だけが
  返す。`rel` = file id。`resolve_url` は従来どおり相対形を `unsupported_form`)。`page_url` は
  **全 kind でそのページ自身の URL**(`/ext/{rel}{query}` / `{origin}/ext/{rel}{query}` /
  `{origin}/{rel}{query}`、file は None)。
- **橋渡し**: `mounted_path_for_url` / `url_for_mounted_path` は `/ext/` ⇔ `/mnt/` の前置きの
  入れ替え + nucllei 側での実在確認(symlink を実体に畳み、`/mnt` の中に閉じる)。
  **`mounted_path_withheld` と「利用者に配信しない範囲にある」の断りは撤去** — `fetch_from_workspace`
  は URL にできれば URL、できなければ `resolve_path` で読んで添付(nucllei 側に無い物は「読めない」)。
- **fetch_url**: リンク一覧は `urljoin(page_url, href)` 一本(`_resolve_href(href, page_url)` /
  `_html_to_lines(raw, page_url, content_type)` / `Tools._page_url(u)`)。`_link_roots` /
  `_base_prefix` / `_head`(原本行・「原本ホストが無い」の注意書き)/ `_describe_bases` /
  docstring の `{PUBLIC_BASES}` スロット / ERROR 文の `unknown_upstream_extra` は消えた。相対の
  `/ext/…` ページでは `/ext/` の外へ出る href(ルート相対・別ホスト)を落とし、絶対の `/ext/…`
  ページと一般の URL では http(s) の結果を全部残す。
- **改名**: `EXT_SELF_ADDRESSES` → **`SELF_ADDRESSES`**(`env.parse_self_addresses`。起動ログは
  `設定で追加 (SELF_ADDRESSES)`)、`EXT_INLINE_MAX_BYTES` → **`FETCH_MAX_BYTES`**(既定 25 MB、
  意味は同じ — inline だけの上限ではないので)。`EXT_` 接頭辞の env は無い。
- **起動ログ** `log_mounts`: `/mnt` 直下の項目を `read-only mount (served at /ext/): /mnt/cage → /ext/cage`
  の形で 1 行(無ければ `参照用データは無い(/ext/** は全て 404)`)。
- **dev(docker 無し)**: ホストに `/mnt/<path>` を作る(symlink 可)。[[dev-environment]]。
- 担保: `test_ext_proxy.py`(ゲートが tmp の `RO_MOUNT_ROOT` を配信 / 404・400 / symlink の両向き /
  mount 自体が symlink)、`test_image_refs_resolve.py`(`/ext/…` の失敗は not_found だけ / 橋渡しの
  往復と実在 / `route_of_url` の形と `"file"` / `/mnt` に無い物は配信されない)、
  `test_builtin_fetch_url.py`(ページ URL 基準のリンク / 原本ホストの残骸が無い)、
  `test_builtin_sandbox.py`(URL ⇔ パスの橋渡し / withheld の概念が無い)、`test_file_images_ext.py`
  (inline が `/mnt` と一般の URL で解決)、`test_ext_upstreams.py`(旧 env・helper が無い)、
  `test_ref_errors.py` / `test_builtin_feed_image.py`(reason の集合)。

llens への申し送り(配備層):

- compose: `.env` から `EXT_UPSTREAMS` / `EXT_FILE_SCOPES` / `EXT_PUBLIC_BASES` を消し、
  `EXT_SELF_ADDRESSES` → `SELF_ADDRESSES`、`EXT_INLINE_MAX_BYTES` → `FETCH_MAX_BYTES` に改名。
  nucllei コンテナの volume を「利用者に配信してよい物だけ」`/mnt/<name>` に bind する
  (cage の `static/` だけ配信したいなら `…/cage/static:/mnt/cage/static:ro` のように深く bind する —
  `doc/` を nucllei に bind しなければ URL にならない。sandbox 側は従来どおり全体を bind してよい)。
  URL の形(`/ext/cage/static/X`)は変わらないので cage の `PUBLIC_BASE=/ext/cage` はそのまま。
- 院内 Web(旧 `his=http://…` の http 中継)は `fetch_url(url="http://<原本ホスト>/…")` で直接取る
  (prompt / skill に原本 URL を書く。`/ext/his/…` の形は無い)。

## 改訂(2026-10-03 夜)— 配信元は `EXT_DIR`、sandbox の置き場は `/mnt` → `/ext`

> **2026-10-05 に改めた**: `EXT_DIR`(置いた物を暗黙に全部配る)は撤去し、`/ext/<name>` の行き先は
> env `EXT_MOUNTS` 1 本の対応表(値が `http(s)://` なら proxy、絶対パスなら静的)。resolver・橋渡しも同じ表を
> 引く。経緯と規則は [[file-handling]] の 2026-10-05 節、正本は `README_API.md` §3.8。以下は当時の記録。

ken 指摘: 上の「nucllei の `/mnt` をそのまま配信」は host 直起動を忘れていた。nucllei は docker
専用でなく(dev は host)、固定 `/mnt` だと host 直起動で root が要り、何より host の `/mnt`(NFS 等)
をログイン済み全員に配信してしまう。同時に ken 決定で sandbox 側の置き場も `/ext` に。

- **root は 2 つ**(`image_refs` 冒頭のコメントが正文):
  - `EXT_DIR` = `EXT_DIR` — nucllei がローカルに読む・配信する root(image `/ext`、
    host `./ext`。起動時 mkdir、DATA_DIR と同じく env。Docker image は /ext)。Docker は volume を `/ext/<path>` に bind、
    host は `ext/` にファイル / symlink。`ext/` の木に置いた**ディレクトリ symlink は
    mount**(`served_mount`: `EXT_DIR` の実ディレクトリを辿って最初に当たったディレクトリ symlink の
    先を閉じ込めの root にする。mount の中身の symlink は外へ出られない。ファイルの symlink は
    content の `escape.txt → /etc/passwd` と区別がつかないので mount にならない)。これが無いと
    dev の `ext/cage/static → <llens>/…` が「外へ出る symlink」で 404 になる。
  - `SANDBOX_MOUNT_ROOT` = `/ext` — jail 側の read-only 置き場(sandbox サービス `RO_MOUNT_ROOT`、
    tool 側 `_RO_MOUNT_ROOT`)。モデルが `path=` に書く形で、nucllei の fs ではない。
- **sandbox のパス = URL = 同じ文字列**: `/ext/cage/static/X` は jail でも、ブラウザでも、`url=` /
  `path=` でも同じ物(nucllei 側の実体は `EXT_DIR/cage/static/X`)。`/ext/…` は url と path の両方が
  受ける唯一の意図した重なり — url は percent-decode して読み、path はそのまま fs パスとして読む。
  `stored_ref_kind("/ext/…")` は `"url"`(保存済みは URL として読む — 同じ物に当たる)。
  `/mnt/…` はもう何でもない(`unsupported_form`)。`PATH_ARG_FORM` は `(`/workspace/…` `/ext/…`)`。
- **橋渡し**は文字列としては恒等: `mounted_path_for_url("/ext/X")` → `"/ext/X"`(decode 済み)iff
  `EXT_DIR/X` が実ファイル、`url_for_mounted_path("/ext/X")` → `"/ext/X"`(encode 済み)iff 同じ。
  rest は配備が置いた名前(mount point まで)+ mount 内の実体(symlink を畳む)。
- **sandbox サービス**(`sandbox/app/main.py`)`RO_MOUNT_ROOT = Path("/ext")` — `/ext` 直下の
  エントリを同じパスで `--bindmount_ro`。**image の再ビルドが要る**(main.py の定数)。
  `sandbox/README.md`「参照用データを jail に見せる (`/ext`)」。
- **起動ログ** `log_mounts`: `ext data in <EXT_DIR> (served at /ext/, sandbox sees them at /ext/): cage → /ext/cage`。
- 担保: `test_ext_proxy.py::test_host_mnt_is_never_served`(EXT_DIR の外は同名でも 404、置けば 200)/
  `test_directory_symlink_in_ext_dir_is_a_mount`(mount の 3 則)、`test_image_refs_resolve.py::
  test_bridge_never_touches_host_mnt_or_ext` / `test_bridge_keeps_deployer_names_across_directory_symlink_mounts`、
  `test_builtin_sandbox.py`(`/ext/…` の往復・percent-encoded を path に渡すと読めない)、
  `test_builtin_feed_image.py`(`/ext/…` は path でも読める)、sandbox `test_ro_mounts.py`。

llens への申し送り(配備層。compose の mount 先が変わる):

- nucllei: `- <host>/cage/data/static:/ext/cage/static:ro`(`/app/data` = DATA_DIR の
  volume の**中**に bind する。`DATA_DIR` は Dockerfile の `ENV DATA_DIR=/app/data`)。
- sandbox: `- <host>/cage/data/static:/ext/cage/static:ro`(+ `doc/` 等、sandbox にだけ見せる物も
  `/ext/<name>` に)。**sandbox image を再ビルドして差し替える**(`/mnt` 固定の旧 image は jail に
  `/mnt` を出す)。
- cage の `PUBLIC_BASE=/ext/cage`・URL の形は不変。prompt / skill の「sandbox では `/mnt/cage/…`」は
  `/ext/cage/…` に書き換える。


## 改訂(2026-10-03、context_budget の builtin 化)— llens の `get_context_budget` を nucllei へ

ken 決定(2026-10-03)。prism-hu/llens の custom tool `nucllei/tools/context_budget.py` を、
nucllei の組み込み **`builtin_context_budget`**(`nucllei/builtin_tools/context_budget.py`、
name "Context Budget")へ移した。関数名は **`get_context_budget` のまま**(llens の prompt / skill が
名前で呼ぶ)。常時登録(nucllei 自身が記録する `chat.meta.usage` を読むだけで外部依存ゼロ)。
英語の正文は `README_API.md` §5.7。

### なぜ移すか

- **真実源が nucllei の記録そのもの**: usage の永続化はモデル呼び出しごと(tool ループの各 stream 末、
  `utils/middleware._record_usage` → `Chats.record_usage_by_id_and_message_id`、#57 / #102)。
  tool はモデル呼び出しの**間**で走るので、ループの途中で呼べば**今ターンの最新呼び出し**の値が
  読める。llens 版は「直前ターン終了時点の累積」で 1 ターン古く、docstring でもそう断っていた —
  記録が呼び出し単位になった時点で、その断りは builtin なら要らない。
- **context 長の手書き定数が要らない**: llens 版は `fallback_context_size` valve(Kimi の 262144)を
  持ち、token_meter filter の同名 valve と数字を合わせる運用だった。nucllei では記録済みの
  `context_size`(= `get_model_context_size` が解決した値)が usage に同梱されており、無ければ
  同じ関数で `__request__` / `__model__` から引く(endpoint の `max_model_len` → モデルの
  `meta.context_size`)。それでも不明なら **null で返す**(定数で誤魔化さない)。
- **owner-only の検証が無かった**: llens 版は `Chats.get_chat_by_id` で chat_id だけで読んでいた。
  builtin は `get_chat_by_id_and_user_id`(builtin_history と同じ)で `__user__` の chat だけ読む。
- 院内固有物は無い(`__metadata__.chat_id` と記録済み usage だけ)。「いつ呼ぶか」は prompt / skill の文。

### 返す形(llens 版と互換)

`{ok, used_tokens, remaining_tokens, context_size, used_pct, note}`。usage がまだ無ければ
`{ok: False, error}`(文言は llens 版に揃えた)。

- `used_tokens`: **`carried_tokens`**(最後の呼び出しの prompt + 再送される非 reasoning 出力 = 次の
  呼び出しの先頭の量)> `prompt_tokens` > `total_tokens` の順。llens 版の分子は `prompt_tokens`
  だったが、FE のゲージ(#102)と同じ `carried_tokens` を優先する。
- `context_size` が不明なら `remaining_tokens` / `used_pct` も `null`、`note` に理由を添える。
- `note` に「いま実行中のステップの tool 結果はまだ含まれない」と書く(次の呼び出しで加算される)。

### 担保

`test_builtin_context_budget.py`(7 件): 登録・usage 無し・carried / context_size からの数値・
prompt_tokens へのフォールバック・owner-only(他人の chat_id は無い扱い)・context 長不明の null・
`__request__` / `__model__` からの解決(endpoint 申告 → `meta.context_size`)。

### llens への申し送り(配備層)

- `nucllei/tools/context_budget.py` を削除する(builtin と同名関数 `get_context_budget` が並ぶと
  先勝ち discard — #18-3 の教訓)。
- preset の toolIds / skill の `tool_ids` は custom tool の id → **`builtin_context_budget`** に差し替える。
- prompt / skill の「値は直前ターン終了時点で 1 ターン古い」の断りは「このターンの最新呼び出し時点、
  いまのステップの tool 結果は未反映」に書き換える。token_meter filter の `fallback_context_size`
  と数字を合わせる運用は builtin には無い。

## 改訂(2026-10-03、#140)— run_subagent を builtin `builtin_subagent` に

- 常に登録の builtin が 7 本になった(history / chat_files / attachment_text / context_budget /
  feed_image / fetch_url / **subagent**)+ env ゲートの sandbox。
- subagent は「ターンによって出る/出ない・spec の一部が user × preset 依存」な初の builtin。これを
  特例にせず、`get_tools` に **toolkit の汎用フック**を 2 つ足して解いた(カスタム tool も使える):
  `available(self, request, user, model, metadata) -> bool`(read ACL の後、False / 例外で toolkit ごと
  載せない)と `spec_overrides(self, request, user, model, metadata) -> dict[str, dict]`(関数名 → spec の
  部分パッチを deep-merge。DB の静的 spec は汚さない)。フック名は spec 化しない(`TOOLKIT_HOOK_NAMES`)。
  詳細は README_API §5.1 / `_memos/subagent-design.md` の #140 節。
- 1a「制御面はモデル設定に一元化」の例外だった `meta.features.subagent` は撤去
  (migration `e7b3c90d2f14`)。「使えるか」は tool 行の ACL × `meta.toolIds` に揃った — 機能フラグでは
  user/group 単位で止められない、が理由(issue #140)。
- `timeouts` クラス属性の非正値(0 / -1)は「システム側上限なし」として per-entry に載るようになった
  (以前は無視して `TOOL_EXEC_TIMEOUT_S`)。

## 改訂(2026-10-04、#145)— tool の失敗は `ToolError` を raise する

ken 指示「ツールのエラーも結果として表示できるように。形式も揃える」。

- **形の一本化**: builtin は失敗を文字列 / `{"ok": false, "error"}` で返さず、
  `nucllei/utils/tool_errors.py` の `ToolError(fact, next, kind=…)`(`ToolCallError` /
  `ToolFailure` / `ToolOutcome` / `ToolFatal`。`ToolViolation` は middleware 専用)を raise する。モデルに渡る文は `format_tool_error` 1 箇所で
  `ERROR: {fact}。次の手: {next}` に組む(`NEXT` は tool_errors が持ち、`ref_errors` は再輸出)。
  `ref_errors` の helper / `explain_ref_error` は `ToolError` を返す(テンプレートの文面はそのまま
  `ToolError.from_text` で事実と次の手に分ける。kind は `REF_ERROR_KINDS`)。**文面は変えていない**
  (llens 判定済みのため)— 変わったのは仕組みと、JSON 包みだったもの(subagent / exec_tools /
  context_budget / attachment_text / background の起動失敗)が `ERROR: 同文` になったこと、
  「。」で終わっていなかった文(`preset を使えない: X`)に「。」が付いたこと、一覧に無い tool の
  文に `ERROR: ` が付いたこと。
- **kind**(5 種。2026-10-04 改訂 — ken「あんまり赤はバグとか致命的なものでもないと出ないように
  したい / ちょっと値が見つからないとかは黄色でいい」「モデルの挙動や規約違反は赤としてみえると
  こっちもわかりやすい」): amber は `call`(tool の中で見つけた呼び方の誤り「呼び直し」)/
  `failure`(壊れてはいない実行時の失敗「失敗」)/ `outcome`(呼べたが答えが「無い」「該当なし」—
  旧: 成功と同じ見た目)、赤は `violation`(モデルの規約違反「規約違反」— 一覧に無い tool・arguments が
  読めない・必須引数の欠落・署名に無い引数。tool を呼ぶ前に middleware が弾くもの)/ `fatal`
  (tool / サーバ自体が壊れている「エラー」)。アイコンは種別ごとに別、amber 3 種は同じ色調
  (FE は `tool-render` の `TOOL_ERROR_LOOKS` + `ToolErrorLabel.svelte`)。モデル向けの文面は種別に
  よらず不変。定義と表は README_API §5.1「Failures」。
  - **fatal へ移したもの**(同日。ToolFailure の raise 箇所を見直し、壊れたサーバ・設定・バグだけ):
    sandbox の認証失敗(鍵の設定の誤り — `_sandbox_auth`)、sandbox `/files/read` の応答が読めない、
    sandbox のファイル登録失敗、fetch_from_workspace の言い分けられない resolver code、
    fetch_url の変換器が落ちた、fetch_url の file レコードはあるのに実体が読めない、
    run_subagent の子セッション行の INSERT 失敗、ref_errors の unexplained(RefError 無し・
    表に無い code)、捕らえていない例外すべて(`tool_exception_text` — 同期・background とも)。
    timeout・到達不能・上流の 5xx / 401 / 403・同時数 / 入れ子の上限・multi-worker・spawn の失敗・
    ユーザー不明・チャット文脈なし・PDF が壊れている / 暗号化(feed_image)は failure のまま。
  llens の判定 (同日) で直したもの: 上流の HTTP 404 / 410 は outcome (他の status は failure)、
  サブエージェントの「セッションは実行中」は call (次の手を「実行中の分の結果を待ってから
  (background なら await_task で受け取ってから) 同じ session_id で続ける」に)、arguments が読めない
  ときの次の手は「引数を正しい JSON で呼び直す。」、feed_image の「対象が見つからない」は
  何も指していない = call / filename で指した添付 PDF が無い = outcome に分けた (文面は同じ)。
- **wire**: `content_blocks` の `results[i].error_kind`(永続。リロードでも出る)。background は exec 行の
  `meta.error_kind` → 一覧 API / `execution:done` / check_task・await_task の per-entry の `error_kind`
  (per-entry は llens staging の wire 確認で追加)。outcome は `done`(result に文)、それ以外は `failed`。
  FE は本文を解釈せずフラグで見た目を決める(ToolCallBlock / ExecutionBlock / BackgroundTasksChip。
  exec 行が failed で種別を持たないときは failure の見た目)。
- **サーバ側で組むもの**: 一覧に無い tool・arguments が JSON object として読めない → violation。
  **必須引数の欠落**(#145 項目 2 — luna の空 `read_doc` が生の `TypeError` を返した件)は呼ぶ前に
  callable の署名で検出して violation(`ERROR: ツール {tool} の必須の引数 {name} が無い。次の手: 引数を
  付けて呼び直す。`)。spec のフィルタを通ったのに署名に無い引数も violation。タイムアウトは failure、
  その他の例外は fatal(例外は型名 + 先頭 200 字。background も同じ式に揃えた — 旧: 生の `str(e)`)。
- **raise しない旗**(同日、ken「sandbox の stderr とかは？実行した python のエラーとか」):
  `tool_output.mark_error_kind(kind="failure")` で返す結果に種別だけを付ける(sink の
  `error_kind` を middleware / exec runner が harvest)。本文・render・display・context_images は
  成功時のまま、wire に `results[i].error_kind` が立つ。background は結果があるので `done` +
  `meta.error_kind`(FE は done でも種別があれば amber)。raise の ToolError が優先。sandbox の
  run_python / run_python_once が使う: exit_code != 0・セルの error・timed_out で failure
  (stdout・traceback・生成ファイル・タイムアウトの次の手はモデルに要るので raise しない)。
  exit_code 0 の stderr(warning)は通常の結果。**run_shell も同じ**(同日 ken 判定 — staging で
  `command not found` (exit 127) が緑だった。grep の不一致 = exit 1 も amber「失敗」になるが、amber は
  穏やかなので許容)。
- **「何も無い」を通常の文で返していた箇所の見直し**(同日、ken が staging で list_workspace の
  不在ディレクトリが緑の「空」だったのを見て。スイープの結果):
  - list_workspace: sandbox の `/files/list` に `exists` / `is_dir` を追加(**sandbox image の更新が
    要る**。追加フィールドなので旧 nucllei とも互換)。不在 = `ToolOutcome`(`{path} は存在しない。
    次の手: 親ディレクトリを list_workspace で一覧して、パスを確かめる。`)、ファイル = `ToolCallError`、
    空 = 通常の結果(「空 (ファイルが無い)」)。旧 image(フィールド無し)の空は旧文面のまま outcome の旗。
  - send_to_workspace(添付一覧から): 文面は変えず旗だけ。filename で指した添付が無い = outcome /
    何も指さず添付も無い = call(feed_image の無指定と同じ線引き)/ 対象はあったが 1 件も取り込めない
    = failure(一部でも取り込めたら旗なし)。
  - feed_image の file 経路(file_id= / url= の配信口 / 添付 PDF): `_feed_file` が目に入れずに返していた
    行(画像でも PDF でもない / 実体が読めない / 大きすぎる / PDF が開けない)が緑だった。文面は変えず、
    `_feed_path` が同じ場面で raise する種別(call / fatal / outcome / failure)の旗。複数の添付 PDF は
    1 件も目に入らなかったときだけ(種別が揃えばそれ、混在は failure)。
  - feed_image の `pages`(バグ修正): 指定が 1 ページも当たらないと**全ページを黙って積んでいた**。
    当たらない = `ToolOutcome`(`{名前} は全 N ページで、指定の {pages} は範囲外。次の手: 1-N の範囲で
    pages を付けて呼び直す。`。`#page=N` の fragment も同じ)、ページ範囲として読めない指定 =
    `ToolCallError`、一部だけ当たる = そのページを積み「指定のうち p.12-15 は範囲外なので見ていない。」を
    1 行添える(旗なし)。
  - 据え置き(検索・一覧の「0 件」は正当な答え): grep_history / grep_chat_files の一致なし、
    list_history / list_chat_files の「無い」、read_history の「読み出せるメッセージが無い」(チャットは
    ある)。fetch_url の画像 / テキスト層の無い PDF は feed_image への誘導で行き止まりではないので緑のまま
    (平文にできない other は既に outcome)。
- **JSON を返す tool の線引き**: 中身の無い失敗は raise、中身(部分回答・session_id・切詰め)を
  運ぶ結果は結果のまま(run_subagent の子のエラー / stop、await_task の per-entry の failed 行、
  check_task の不正 id 行)。「一致なし」のように `ERROR:` で返していなかった通常の結果も
  そのまま(文面を変えないため。「無い」が要求した対象の不在だったものは上の見直しで旗 / raise)。
- **カスタム tool**: `from nucllei.utils.tool_errors import ToolError, ToolCallError, ToolFailure,
  ToolOutcome, ToolFatal` を raise する(DB の tool も plugin loader で import できる)。**返した文字列は常に正常な
  結果** — `ERROR:` で始まっていてもフラグは立てない(本文の嗅ぎ分けはどこにも置かない。ken 決定
  2026-10-04 — 初版にあった「builtin 以外の `ERROR:` 文字列は failure」の推定は同日に撤去)。

## 改訂(2026-10-06)— `fetch_from_workspace` の橋渡しが cage の 302 を辿る / doc の URL は `/ext/doc`

llens が doc の公開 URL を `/ext/doc/…`(リーフ `….md`・ディレクトリ `…/`)・`/ext/doc-static/…` に
変えた(`/ext/cage` は廃止)。cage は正規形でない住所(`.md` 無し・末尾 `/` 無し・`…/_index.md`)を 302 で
正規形へ飛ばすので、`url_for_mounted_path` の proxy 判定(HEAD が 2xx か)は base の中の redirect を
3 回まで辿り、飛び先の `/ext/<name>/…` を返すようにした(`image_refs._proxy_served_rest`)。辿らないと
`fetch_from_workspace(path="/ext/doc/…/X/_index.md")` が URL にならず取り込みに落ち、HTML が添付になる。
base の外への redirect・上限超え・2xx 以外は従来どおり「このサーバの /ext に無い」。担保は
`test_image_refs_resolve.py::test_proxy_bridge_follows_redirects_inside_the_base`。経緯と配備の形は
[[file-handling]] の 2026-10-06 節。上の節の `/ext/cage/…` の例は当時の形。
