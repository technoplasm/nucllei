# 組み込みツールと sandbox 自前ツール搭載(#18-3)

2026-07-06 起草、同日改訂 ×2(設計議論の着地: **制御面はすべてモデル設定** — グローバル
フラグ・workspace ページのトグルは作らない)。#18-3(自前ツール搭載確認 → #18 close)の
要件とテスト設計。現行実装の参照は `_links/llens`(owui/tools/sandbox.py、sandbox/app/)。

## 0. 事前調査の結果(2026-07-06 実測)

### sandbox コンテナ(llens/sandbox:poc、127.0.0.1:3000)外形

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

副産物の知見: **素の API 呼び出し(tool_ids なし)では load_skill しか載らない**。
toolIds はフロントが payload で送る設計(4pcg ガードで read 検証)。また非ストリーミング
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

- **定義**: `backend/open_webui/tools/builtin/` 配下のモジュール。起動時に Tools テーブルへ
  type="builtin" 行として sync(content は空、ロード時に repo モジュールへ振り向け)
- 既存機構がそのまま効く: spec 生成(docstring → schema)・toolIds picker・skill の
  tool_ids・access_control・valves・4pcg ガード。content 更新 API は builtin 行を拒否
- **境界基準は「出荷単位」**(「内部に触れるか」では引けない — カスタム tool も
  in-process exec で Files/Chats を import できるのが v0.6.5 の仕様):
  - 組み込み = OWUI の内部概念(チャット履歴・skill)の操作が目的そのもの。
    本体と一緒にバージョニングされないと壊れるもの
  - カスタム = 外部サービスへのブリッジ。本体と独立に差し替え・更新したいもの。
    → **Sandbox はカスタム**(現行 prism-hu/llens と同じ DB 登録の配布形を維持)
- この整理により **#18-3 は「カスタム tool として現行基盤に乗ることの確認」に純化**され、
  組み込み機構の実装を待たずに進められる

## 2. 要件

### R1. 組み込みツール機構 + モデル設定の再編

上記 1a〜1c の実装。**出荷済み(2026-07-06)**: 機能「スキル」ゲート + `/command` の
モデル紐づけスコープ化(ba01a0f1f)、組み込みツール機構(builtin 行 sync)。
実装の決定: レジストリ = `backend/open_webui/builtin_tools/`(id は `builtin_` prefix で
予約・create で横取り不可)、行の所有者は固定値 "system"、判別は meta.builtin
(表示専用 — ロード分岐と更新保護はレジストリ id が真実源)、更新は access_control のみ・
削除拒否・specs は起動ごとに repo から再生成。

第一弾の組み込みツールは**履歴管理 — 現在はスタブ(TODO 返し)で機構の検証台**。
本実装は必須でないため後回し(ユーザー判断 2026-07-06):

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

`_links/llens/owui/tools/sandbox.py` を**カスタム tool のまま**(DB 登録)本 fork に搭載検証:

- 関数: run_python(stateful)/ run_python_once / run_shell / import_chat_file /
  list_workspace / export_workspace_file
- 依存する基盤機能が llens の native ループで全部動くこと(= #18-3 の確認事項):
  - extra params 注入: `__user__` `__chat_id__` `__message_id__` `__event_emitter__` `__files__` `__metadata__`
  - Valves(sandbox_url, default_timeout_s)の DB 永続と UI 編集
  - status イベント(5 秒ごとの経過表示)、`chat:message:files` イベント
  - OWUI Files/Storage への outputs 登録 + メッセージ添付
  - 型駆動レンダリング(#20): run_python の code 引数は `Code("python")` 宣言
    (dev ダミー run_python が既にこの形 — 置き換え時に宣言を引き継ぐ)
- 認可: access_control で group 単位の出し分け(CORE)
- air-gapped: sandbox_url は docker network 内。外部ネットワーク依存なし

### R3. #15 の結論(Responses API 要否)

**不要と結論**。根拠: 接続は SGLang の OpenAI 互換 chat completions 一本(fork.md CONSTRAINT)、
Kimi の tool call は厳密 JSON で現行パーサ一次経路が成立(#15 実測)、sandbox ツールの
戻り値は str(JSON 文字列含む)で native ループを素通しできる。stateful 性はサンドボックス側
(常駐 kernel)が持ち、プロトコル側の状態(Responses API の previous_response_id 等)を要しない。

## 3. テストケース

### pytest(backend、sandbox は HTTP モック — CI で実コンテナに依存しない)

組み込みツール機構 + モデル設定(R1):
- T1: builtin id 解決 — builtin 行の tool が tools_dict に spec/callable 付きで載る
  (toolIds 経由・skill tool_ids 経由の両方)
- T2: builtin 行の保護 — content 更新 API は拒否、削除も拒否(または起動時 sync で復元)。
  一覧 API に builtin フラグ付きで出る
- T3: 機能スキル OFF のモデル — manifest 非搭載 + `/command` は平文素通し(発火しない)。
  ON のモデルでは両方効く
- T4: `/command` の範囲 — モデルに接続していない skill は可視でも発火しない。
  接続済み + 可視で発火(既存の可視性テストは維持)
- T5: access_control — read 権限の無いユーザーのリクエスト由来 id は無視(4pcg 同型、
  builtin 行にも適用)
- T6: 履歴ツールの owner-only — 他ユーザーの chat_id を read_history に渡しても見えない。
  list/grep が本人の chat しか返さない

sandbox ツール(R2、respx 等で sandbox API をモック):
- T7: run_python — /exec_cell へ user_id=呼出ユーザー, chat_id=会話 id が渡る。
  stdout/error/timed_out/session_reset の整形文言(タイムアウト時「変数・import は保持」等)
- T8: `__chat_id__` 欠落時の run_python → run_python_once 誘導のエラー文
- T9: outputs[] → Files/Storage 登録 + `chat:message:files` emit + 画像なら markdown 誘導行。
  truncated エントリは「転送上限超過」報告のみで登録しない
- T10: import_chat_file — 所有者チェック(他ユーザー file_id は skip)、file_id 直指定経路
- T11: sandbox HTTP 障害(接続不能・非 200)→ "ERROR: ..." 文字列で graceful(例外を漏らさない)
- T12: spec 生成 — docstring から name/description/parameters が正しく出る + Code("python") render

sandbox tool 内部(T7〜T12)の単体は、tool 本体が prism-hu/llens 側の管轄(カスタム tool
として配布)のため fork 側 CI には載せない — fork 側の担保は基盤回帰(上記 3 件)+ E2E。
組み込みツール(R1)を実装する際に同型の単体を fork 側に持つ。

### E2E(dev/e2e/check_sandbox_tools.py — 実 Kimi × 実 sandbox、CI 外)

**2026-07-06 実施: E1〜E6 全 PASS(8 チェック)**。stateful(x=42→84)・timeout 後の
変数保持・outputs 自動添付(result.csv)・import_chat_file の実読み出しを実機で確認。

答え合わせ方式(check_lazy_tools.py と同パターン):
- E1 計算: 「run_python で 2**10 を計算して」→ 応答に 1024
- E2 stateful: 同一チャット turn1 で `x=42` 定義 → turn2 で「x を 2 倍して」→ 84
- E3 outputs: 「CSV を outputs/ に保存して」→ チャットにファイル添付が付く(API で files 確認)
- E4 timeout: sleep(10) を timeout_s=2 で → 中断文言、続くセルで変数生存
- E5 shell: run_shell で `echo` + `jq` → 出力一致
- E6 添付取り込み: ファイル添付 → import_chat_file → run_python で内容を読んで正答
- E7 モデル設定: 機能スキル OFF のモデル → `/kansai` が平文素通し・manifest 非搭載。
  ツール未接続モデル → 同じ依頼で tools 非搭載(bare API で確認)

## 4. 進め方

1. **R2 = #18-3 を先行** — **完了(2026-07-06)**: 適合版 sandbox.py(dev/tools/)を
   dev DB に登録、基盤バグ 3 件を修正、E2E 8/8 PASS。#18 close・#15 結論記載は
   /sync-docs で issue へ反映
2. R1 モデル設定再編 + 組み込み機構 + 履歴ツール(T1〜T6 + E7)← 次
3. リリースタグ候補点(D 完了)
