# サブエージェント(agent session)設計 — issue #107

一次情報は issue #107(本文 = 2026-08-13 全面改稿の設計、コメント = 実装前の懸念洗い出し)。
このメモは実装時に確定した決定事項と実装配置の記録。P1+P2 は 2026-08-13 出荷
(`4a583200c` エンティティ + enforce / `0048adc87` run_subagent + admin UI)。

## 芯(issue 本文の要約)

サブエージェント = **一級の chat(agent session)**。chat 行に `kind` を導入し、
親リンク(`parent_chat_id` / `parent_message_id` / `depth`)付きの agent session を
親の native ループが builtin tool `run_subagent` で spawn する。オーケストレータは新設しない。
リロード耐性・再アタッチ・stop・usage 計測(#102)・監査は既存機構からタダで付いてくる。

## 決定事項(実装時に確定、2026-08-13)

1. **kind / 親リンクはカラム**(meta ではない)。理由: 一覧クエリは全てカラム述語 /
   JSON 述語は dialect 分岐が必要 / `ChatImportForm` は meta を丸ごと受けるため meta 置きは
   kind 詐称を許す。migration `b4d19c7a52e3`(`parent_chat_id` に fork 初の index)。
2. **親削除時の子は cascade soft delete** + 削除前に子孫の走行中タスク stop(孤児タスクを作らない)。
   `get_descendant_chat_ids` は visited 集合で循環データ(DB 破損時)に防御。
3. **共有(/s/)は拒否**、**clone は許可**(= 「介入したいときは fork」。生成物は kind=user・親リンク無し)。
4. **書き手の定義 v1**: HTTP 経由の chat 変異は全部拒否(router 層で enforce —
   内部経路は同じ model 関数を使うため model 層拒否は不可)。適用点は
   update / delete / pin / archive / share / completions POST(403)/ completed / action。
   task stop・call reply は現状維持(所有者検証済み)。#106(API 起動)の外部 driver は #106 の宿題。
5. **一覧分離**: ユーザー向け一覧(サイドバー・検索・archived 等)から kind=agent を除外。
   admin 一覧は kind フィルタ(deleted/shared と同型)で見える。全 DB export は除外しない(監査)。
6. **タイトル**: spawn 時に決定的付与(`{preset名} — {instruction先頭40字}`)。
   生成タスクとフォールバックは子 metadata の `agent_session` マーカで抑止(webhook 抑止も同マーカ)。
7. **タイムアウト**: tool エントリの per-tool `timeout` キー(execute_tool_calls が
   `TOOL_EXEC_TIMEOUT_S` より優先)。run_subagent は `SUBAGENT_TURN_TIMEOUT_S`(既定 1800s)。
8. **depth 上限**: `SUBAGENT_MAX_DEPTH`(既定 2、user chat = depth 0)。上限超は tool を合成しない + 実行時再チェック。
9. **同時数上限**: `SUBAGENT_MAX_CONCURRENCY`(既定 4)。プロセス内カウンタ、超過は**待たずに即エラー**。
10. **stop 連鎖**: `asyncio.shield(child_task)` + `Task.cancelling()` で親 cancel と子側発 cancel を判別。
    親 stop / turn timeout → 子を stop して伝播。子の外部 stop → 部分回答を tool result で返し**親は続行**。
11. **usage は親子で合算しない**(既存機構で自動成立)。親へは tool result に子 usage の要点を載せる。
12. ~~**機能フラグ** `meta.features.subagent`(既定 ON、skills と対称)。tool の出現は
    「フラグ ON ∧ `meta.subagentPresetIds` 非空 ∧ アクセス可 preset ≥ 1」。~~
    → **2026-10-03 撤去(#140)**: 下の「決定(2026-10-03、#140)」節。
13. **allowlist 認可は enum 構築と実行時で同一述語を二重評価**(allowlist ∩ 所有者 or has_access(read))。
    `check_model_access` に依存しない(admin バイパスの穴。`BYPASS_MODEL_ACCESS_CONTROL` は 2026-10-03 に撤去)。
    子ターン自体は generate_chat_completion を通るので既存検証も重ねて効く(#101 の AND)。
14. **子の回答規律**: tool result は最終 assistant テキストのみ(transcript は返さない)。8000 字で切詰 + truncated。
15. **継続**: `session_id` 引数で既存子セッションへ追いメッセージ(所有者 ∧ kind=agent ∧ 親一致を実行時検証)。
    同一 session への並行継続は実行中集合で拒否(read-modify-write の lost update 防止)。

## 決定(2026-10-03、#140)— run_subagent を builtin tool にして ACL を付ける

問題: 擬似 tool は `get_tools` の外で合成・直接注入されていたため、tool 行の read ACL も
`meta.toolIds` も効かず、ユーザー/グループ単位で止める面が無かった(決定 12–13 の帰結。
ken 2026-09-06「機能として提供されるものは ACL が付けられない。サブエージェントは組み込み
ツールになるべき」)。

1. **入口は builtin `builtin_subagent`**(`nucllei/builtin_tools/subagent.py` の `Tools.run_subagent`)。
   常に登録。実行本体は `utils/subagent.run_subagent_turn`(旧 closure を関数化)。callable は
   reserved param(`__request__` / `__user__` / `__metadata__`)で受け、`__user__` から UserModel を
   引き直す。allowlist は `__metadata__["model"]`(親 preset)から読む — `__model__` はタスクモデルに
   差し替えられうるので使わない。middleware の直接注入と `build_subagent_tools` /
   `build_run_subagent_tool` / `is_subagent_feature_enabled` は削除。
2. **二層**: 使えるか = tool 行の ACL × `meta.toolIds`(get_tools の per-user enforce)/
   どこへ飛べるか = `meta.subagentPresetIds` ∩ アクセス可(従来どおり。UI もそのまま)。
3. **`meta.features.subagent` 撤去**: migration `e7b3c90d2f14` が「フラグ ≠ false ∧ allowlist 非空」の
   preset の toolIds に `builtin_subagent` を足し(dedupe)、全 preset からフラグを消す(空 features も)。
   FE の Capabilities トグルを撤去し、SubagentPresetsSelector に「ツールに割り当てたときだけ有効」の
   注記(未割り当てなら黄色)。
4. **get_tools の汎用フック 2 つ**(他の toolkit も使える。`TOOLKIT_HOOK_NAMES` — spec 化しない):
   - `available(self, request, user, model, metadata) -> bool`: read ACL の後に評価、False / 例外で
     toolkit ごと載せない(fail-closed)。subagent は旧 None 条件(allowlist 空・アクセス可 preset ゼロ・
     chat 文脈なし・親不在・depth 超過)
   - `spec_overrides(self, request, user, model, metadata) -> dict[str, dict]`: 関数名 → spec 部分パッチを
     deep-merge(dict 再帰・他は置き換え)。docstring 由来 description の後・toolkit 注記の前に適用。
     例外なら静的 spec のまま。subagent は preset_id の enum と description 末尾の preset 一覧
   - model は `metadata["model"]` 優先(無ければ `__model__`)
   静的 spec(sync 時)は enum 無しの汎用形。実行時の認可は enum に委ねず同一述語で再評価(決定 13 維持)。
5. **backgroundable / timeout はクラス属性宣言**(`backgroundable = ("run_subagent",)` /
   `timeouts = {"run_subagent": SUBAGENT_TURN_TIMEOUT_S}`)。`exec_tools.py` に tool 名の特例は無い。
   `timeouts` の非正値は「システム側上限なし」として通すよう get_tools を拡張
   (`SUBAGENT_TURN_TIMEOUT_S=-1` = 無効を保つため。以前は非正値を無視していた)。
6. **運用規律は toolkit クラス docstring**(get_tools が先頭 spec に 1 回前置 — tool が載ったターンに
   だけ出る)。llens `llens-thinking.md` のサブエージェント規律(並列 4 本・background 併用・
   使わない場面・結果の扱い)を汎用化して移した。ドメイン依存の「いつ使うか」は書かない
   (llens の skill 側)。同時数は `SUBAGENT_MAX_CONCURRENCY` が既定 4 以外なら数値を差し替える。
7. 担保: `test_subagent.py`(get_tools 経由に書き換え + 静的 spec / ACL / available / spec_overrides /
   特例なし)、`utils/test_toolkit_hooks.py`(汎用フック・非正値 timeout・migration の meta 変換)。

llens への申し送り: `README.md` のサブエージェント手順に「親 preset の toolIds に
`builtin_subagent`」を足し、ACL の置き場(tool 行)を書く / `RELEASE.md` の該当手順 /
`llens-thinking.md` のサブエージェント規律(並列・background・使わない場面)を docstring と
二重にしないよう削る(ドメイン依存の「いつ使うか」だけ残す)。

## 実装配置

- エンティティ + enforce: `nucllei/models/chats.py` / migration / `nucllei/routers/chats.py` / `nucllei/main.py`
- 実行系: `nucllei/utils/subagent.py`(#140 以降は run_subagent_turn + フックの中身。入口は
  `nucllei/builtin_tools/subagent.py`。当初は build_run_subagent_tool — `skills.py` の build_load_skill_tool 同型)。
  子ターンは内部 3 段(process_chat_payload → generate_chat_completion → process_chat_response)+
  task 完走待ち。**子 Request を新規生成**(親 request.state.metadata の汚染 = 親の追撃呼び出しが
  子 metadata を拾う罠の回避)。mount は ~~`middleware.py` の load_skill 直後~~ → #140 で get_tools 経路。
- 子一覧 API: `GET /api/v1/chats/{id}/children`(P3 のサイドバー tree / 観測 view の土台)
- admin UI: `SubagentPresetsSelector.svelte`(SkillsSelector 同型)。~~Capabilities の featureSubagent~~
  は #140 で撤去(ToolsSelector の `builtin_subagent` に吸収)
- テスト: `test_agent_sessions.py`(enforce/一覧/cascade)、`test_subagent.py`(spawn/認可/stop 連鎖/継続/上限)、
  authz・timeout の既存ファイル拡張

## 残フェーズ

- [x] P1 エンティティ + spawn 基盤(`4a583200c`)
- [x] P2 builtin tool + preset 管理(`0048adc87`)
- [x] P3 web UI(2026-08-13 出荷): live-usage / tool-progress store の chatId スコープ化(前提作業)/
      `SubagentSessionBlock`(tool ブロック内の遅延インライン表示・live 追従・fork/開く/停止)/
      サイドバー tree(/c/ で選択中の chat の項目下に子を展開、子の観測中は親の下に出し続ける)/
      /c/ 観測モード(readOnly バナー・親リンク・fork。events の isObserver ガードで DB 書き込み経路を遮断)。
      `chatHandlers` は chatId→Set に多重化(同一 session の複数ブロック展開対応)。
      観測モードは 5 秒ポーリングで継続 turn の新 task に追従
- [x] dev fixture: `tools/seed.py` に `ensure_subagent_preset`(sub-worker)+ `wire_preset` の
      subagentPresetIds 配線(dev-environment.md 項目 8)
- [x] P4 llens 追従 — **改訂の上で完了(2026-08-16)**: まず `tools/subagent.py` を
      agent session 機構へ置換(llens `0a3b672` — 9 ガード維持・session_id 化)したが、
      同日 ken 裁定で **ask_subagent 自体を廃止**(llens `e47d9aa`)。固有価値だった
      「file_id → 抽出テキストの搬入」は nucllei builtin `builtin_attachment_text`
      (#113、`fb06797e4`)へ本体化し、素の run_subagent + 子 preset
      (read_attachment / feed_image / cage)に一本化。skill・プロンプト・audio の
      案内文も同体制へ追随済み。残は配備 admin 操作(llens-subagent preset 作成 +
      主 preset の subagentPresetIds — #110 handoff 参照)

## P3 の既知の意図的挙動

- 子の live usage はグローバル store に書かない(親の floating ゲージへの混線を構造的に断つ)。
  子ターンの usage は persisted 値(message.usage)で完走後に見える
- 折りたたみ中/非表示の子完了で fallback の完了トーストが出る — クリックで /c/ 観測 view に飛べるので v1 許容
- 観測モードで子の `__event_call__`(confirmation/input)に応答できる — #106 の降格ポリシー
  「reply 先が居れば既存機構で成立」と一致する意図的挙動(read-only は履歴の書き手の話)

## 後続(別 issue)

- ~~非同期形(#109、`f06626f0a`)~~ → **廃止(2026-08-14、`f1839349c` で巻取り)**。
  subagent 専用の background/await は「一貫した非同期の土台」(ken)に反する矮小化だった。
  非同期は **#110 の汎用 execution エンティティ**(どの tool も `run_async` で包める curry 化
  builtin + 進行中インジケータ)に一本化する。#109 で得た知見(shield+cancelling の待ち方・
  FE 自動再開の二段検知・once ガード)は #110 設計の入力として引き継ぐ。
  実モデル E2E(並列 fan-out / fire-and-forget / 自動再開)は成立を確認済み — 機構の
  実現可能性の証拠として記録のみ残す。
  **リリース順序(ken 決定)**: サブエージェント + #110 土台が全部できてから tag →
  その上で llens P4。v20260814-01 は撤回済み(ghcr イメージ削除は packages スコープ待ち)。
- 子ストリーム消費中の stop で tool result が `ok=True` + 部分回答になる(error マーク無し)—
  厳密化には子 message への interrupted マーカ永続化が要る(現状は報告のみ)

## 実行中カードの live 追従(2026-10-04、ken 承認 — #140 UI 追従)

実測(dev / qwen3.8-flash-next): 子の実行中(2〜6 秒)カードは `agent session <uuid>` と
秒カウンタだけで、子の思考・本文は**完走まで一切ページに届かなかった**
(SubagentSessionBlock は結果の subagent 形が来てから初めてマウント)。background は
exec 行の 5 秒ポーリングでしか終端を知らず、スピナーが子の完走から平均 ~2.8 秒遅れた。

- **所在を構造化して流す**: run_subagent は dispatch 時に `__progress__` へ
  `subagent={"session_id"}`、子ターン起動直後に `task_id` を追送する
  (`tool_output.progress(subagent=...)`。粘着 = 以後の payload・終端・再アタッチの
  最新値にも載る / 値が変わった 1 通はスロットル素通し)。description
  (`agent session <uuid>`)は check_task 越しにモデルが読む手がかりとして残す
- **background も同じ経路**: `_ExecSink` は最新値保持(check_task)に加えて、spawn した
  ターンの stream が開いていれば `tool:progress` を転送する(閉じていれば捨てる)。
  終端(done/failed/stopped)は chat の開いている stream 全部へ `execution:done` を push
  (`completion_events.get_streams_for_chat`)。middleware は触らない — exec 側だけで完結
- **FE**: ToolCallBlock は progress の `subagent` を観測した時点で(結果を待たず)
  SubagentSessionBlock を live モード(`live={session_id, task_id?}`)でマウントし、
  task_id 到着で `attachToTask`。結果(display)が後着しても**同じ位置・同じインスタンス**の
  まま続く(二重描画しない)。子の content_blocks からラベル「子を起動しました → 考え中 →
  回答中」(i18n `Subagent started/thinking/answering`)。子の走行中はカードを自動展開し、
  終わったら expandDetails 設定へ戻す(途中で手で開閉したら触らない)
- **終端の即時反映**: `execution:done` → `applyExecutionDone`(executions store の行を
  即終端化)+ activeOnly 読み直し。stream が無いときは子の live 追従の完走観測を合図に
  `refreshExecutionsUntilTerminal`(150/400/1000/2000 ms の短い再試行)。5 秒ポーリングは
  どちらも無いときの最後の fallback
- 実測(after): sync — dispatch から 30 ms でカードに子(指示 + 実行中)、子の最初の
  reasoning は dispatch +1.1 s(=子モデルの初トークン待ちそのもの)。background 並列 2 本 —
  各カードが自分の子を追従、子完走 → カードのスピナー停止 ≤ 0.1 s(計測の刻み)
- 既知の割り切り: 所在は揮発(progress)なので、リロード後の background spawn カードは
  従来どおり ExecutionBlock のみ(子 transcript は await_task / 配達側のカードで見える)。
  カードを手で畳むと子ブロックは unmount され、ヘッダのラベルは最後の段階で止まる
  (完了表示の権威は結果 / exec 行のまま)

## skill 経由の run_subagent(2026-10-19、llens の依頼・ken 決定)

ken の決定で、llens はサブエージェントを**skill の公開範囲で絞って**配る(preset の toolIds に
`builtin_subagent` を入れず、tool_ids に `builtin_subagent` を持つ skill を preset の skillIds に入れる)。
nucllei 側は実装不要だった(HEAD 5d28c6cea で書き捨てテストで実証):

- `load_skill`(/command・自動ロードも)を呼んだそのターンの途中から `run_subagent` が生える
  (middleware の skill tool 注入は `get_tools(enforce_access_control=False)`)。次のターン・続きターンは
  `replay_skill_tool_ids` で復元。backgroundable / timeout / background 引数 / await_task 等も付く。
- 認可は「preset の skillIds ∧ skill の read 可視性」だけ。`builtin_subagent` の tool 行の ACL は**この経路では
  効かない**。skill の可視性判定は admin を特別扱いしない(管理者にも使わせるなら skill の ACL に入れる)。
- 二層目は従来どおり: `meta.subagentPresetIds` が空だと `available()` が False で、load_skill は本文だけ返して
  `tools: []`。子 preset の skillIds にその skill を入れない(手順書だけ届いて tool が無い食い違い)。
- 使えない人に `run_subagent` の名前を見せない: ERROR・次の手で別の tool を名指しするのはそのターンに出ている
  tool だけ(`tool_errors.tool_in_turn` — `builtin-tools.md`「モデル向け文面の監査(2026-10-19)」)。skill を
  読み込んでいない人には fetch_url / feed_image の too_large でも名前が出ない。
- FE: preset 編集の SubagentPresetsSelector の黄色い警告は、`toolIds` か、割り当てた skill の `tool_ids`
  (機能「スキル」が ON のとき)のどちらかに `builtin_subagent` があれば出さない
  (`src/lib/utils/skills.ts::isToolReachableFromPreset`)。説明文も「…それを tool に持つスキルを割り当てたときだけ
  有効。スキル経由の場合、使える人はそのスキルの公開範囲で決まる」に。手順書 `docs/manual/11_models.md`。
- docstring の文面(同日、llens 監査 (8)・(10)): 同時数は「サーバー全体で 4 本まで (ほかの利用者の分も数える)」
  (`_active_turns` はプロセス内の数 — worker が複数なら worker ごと)、超過の次の手から「時間を置いて」を消し
  「1 回だけ呼び直し、それでも同じなら自分で処理する」。サブエージェントどうしは並べると同時、sandbox の tool
  どうしは並べても順番待ち、を分けて書く。
