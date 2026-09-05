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
send_to_workspace の owner-only、および llens から引き取った url= 経路の SSRF ガード回帰)。
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
  `Bearer ` を送らない)。任意 URL を取りに行く `_http_get` には混ぜない。
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
- 残る valve: `default_timeout_s` / `max_output_chars` / `url_blocked_cidrs` / `url_max_mb` /
  `debug`。UI 編集は従来どおり効く。
- 回帰: `test_builtin_sandbox.py::test_destination_is_env_only_and_unreachable_from_db`
  (spec に `sandbox_url` が出ない / `valves/update` に送っても保存されない /
  旧行が DB に残っていてもロードできる / `_post` の宛先は env の値)。
