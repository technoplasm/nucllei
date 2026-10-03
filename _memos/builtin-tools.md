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
- T11: sandbox HTTP 障害(接続不能・非 200)→ "ERROR: ..." 文字列で graceful(例外を漏らさない)
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

**`path` 引数は sandbox の fs の絶対パス**(2026-10-03、ken 決定「名前 = 形」: `url` = URL
(絶対、または同一サーバの `/ext/…` — 末尾の「参照の規則の改訂」節)、
`path` = `/workspace/…` `/mnt/…` の絶対パス、`file_id` = 添付)。`write_file` / `list_workspace` /
`fetch_from_workspace` の 3 本は以前 workspace 相対で、他の tool の戻り値(絶対)をそのまま渡せず、
`write_file("/workspace/x")` は `/workspace/workspace/x` に黙って書いていた。現行は 3 本とも
`/workspace/…` を受けて絶対で返す(`list_workspace` の各行も絶対)。**相対パスは受けない**
(同日中に改めた — 末尾の「両 repo 通しの検証で直したこと」節。当初は「`/workspace` からの相対」
として通していた)。相対・空・URL・`/workspace` の外は tool 側で固定文で断る
(`/mnt/…` は sandbox サービスの `/files/*` が扱えないので「run_shell の ls で」と案内)。
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
    辿らない(旧 LAN 経路は 3 回まで追った)。→ **同日中に「院内の範囲」を足した**(末尾の節):
    配備が `EXT_INTRANET_CIDRS` で院内のアドレス範囲を宣言すれば、その範囲のホストの URL は
    登録無しで取れ、redirect も範囲内で 3 回まで辿る。未設定なら上記のまま。
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
- **`path` = sandbox の fs の絶対パス**(`/workspace/…` `/mnt/…`)→ `image_refs.resolve_path`
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

- `url=` は URL として解決するだけ。UUID・`/workspace/…`・`/mnt/…`・サーバに無い経路は
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
`http(s)://` と `/ext/` = URL、`/workspace/` `/mnt/` = fs、それ以外は `unsupported_form`)。
`resolve_ref` のシグネチャは変えていない(送信直前 inline `file_images.py` が使う)。
配備層の tool は引数名に応じて `resolve_url` / `resolve_path` を呼ぶ形に追従する(llens 側の作業)。

配備の順序: llens の tool は nucllei の resolver を呼ぶ。**nucllei のこの版が配備される前に
llens(cage / read_doc)が相対 `/ext/…` を返し始めると、feed が旧 resolver の `relative_ext` で落ちる**
— nucllei を先に(または同じ機会に)入れ替える。

未対応(dev だけの話): vite dev(:5173)の proxy に `/ext` が無いので、dev で `EXT_UPSTREAMS` を
設定しても FE 経由の `/ext/…` は 404 になる(本番は単一オリジンなので関係ない)。

## fetch_from_workspace(path) が `/mnt/…` を受ける(2026-10-03)— パス → 見せられる形の橋渡し

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
    (「無い」と「権限が無い」を言い分けない — 存在の有無を漏らさない。`read_attachment` は
    dict を返すので `{ok: False, error}` の error に `ERROR: ` 抜きの同文)
  - tool 固有の行き先(「sandbox のファイルは取り込み不要」「添付は file_id= に」)はエラーから
    消した。旧 `_URL_ACCEPTS` は「添付・生成物は file_id= に渡す」と言いつつ
    `/api/v1/files/…` を url で受けていて自己矛盾だった。
  - `list_workspace` / `write_file` の `/mnt` 向け案内(「run_shell の ls で」)は、同じ `path`
    引数の中の区分で解決結果に基づくので残した。
  - `read_attachment` が抽出の無いファイルに返す文は「抽出テキストが無い(content_type=…)」の
    事実だけ。旧文面の「視覚が要るものは feed_image」は種類を決めつけた誘導で、音声には誤り。
- **`RefError.reason`**: `unknown_upstream` の内訳を安定したフィールドで返す(`unknown_name` /
  `not_ext_form` / `outside_public_bases`。閉じた集合。他の code では None)。code の集合は
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

## 院内の範囲(EXT_INTRANET_CIDRS)— 登録した配信元の外の院内 Web も拾う(2026-10-03)

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

- **id とゲート**: `builtin_feed_image` は**常時登録**(file_id / path 経路は `/ext` 設定が無くても
  動く)。`builtin_fetch_url` は **`EXT_UPSTREAMS` / `EXT_PUBLIC_BASES` / `EXT_INTRANET_CIDRS` の
  いずれかが設定されているときだけ登録**(3 つとも空なら解決できる URL が無い — 載っていて必ず
  失敗する builtin より、無い方がよい。`SANDBOX_URL` → `builtin_sandbox` と同じ型。未設定なら
  起動時 prune で DB からも消える、#42)。
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
