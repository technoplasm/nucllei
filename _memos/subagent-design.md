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
   `CHAT_TOOL_EXECUTION_TIMEOUT` より優先)。run_subagent は `SUBAGENT_TURN_TIMEOUT`(既定 1800s)。
8. **depth 上限**: `SUBAGENT_MAX_DEPTH`(既定 2、user chat = depth 0)。上限超は tool を合成しない + 実行時再チェック。
9. **同時数上限**: `SUBAGENT_MAX_CONCURRENT`(既定 4)。プロセス内カウンタ、超過は**待たずに即エラー**。
10. **stop 連鎖**: `asyncio.shield(child_task)` + `Task.cancelling()` で親 cancel と子側発 cancel を判別。
    親 stop / turn timeout → 子を stop して伝播。子の外部 stop → 部分回答を tool result で返し**親は続行**。
11. **usage は親子で合算しない**(既存機構で自動成立)。親へは tool result に子 usage の要点を載せる。
12. **機能フラグ** `meta.features.subagent`(既定 ON、skills と対称)。tool の出現は
    「フラグ ON ∧ `meta.subagentPresetIds` 非空 ∧ アクセス可 preset ≥ 1」。
13. **allowlist 認可は enum 構築と実行時で同一述語を二重評価**(allowlist ∩ 所有者 or has_access(read))。
    `check_model_access` に依存しない(admin バイパス・`BYPASS_MODEL_ACCESS_CONTROL` の穴)。
    子ターン自体は generate_chat_completion を通るので既存検証も重ねて効く(#101 の AND)。
14. **子の回答規律**: tool result は最終 assistant テキストのみ(transcript は返さない)。8000 字で切詰 + truncated。
15. **継続**: `session_id` 引数で既存子セッションへ追いメッセージ(所有者 ∧ kind=agent ∧ 親一致を実行時検証)。
    同一 session への並行継続は実行中集合で拒否(read-modify-write の lost update 防止)。

## 実装配置

- エンティティ + enforce: `nucllei/models/chats.py` / migration / `nucllei/routers/chats.py` / `nucllei/main.py`
- 実行系: `nucllei/utils/subagent.py`(build_run_subagent_tool — `skills.py` の build_load_skill_tool 同型)。
  子ターンは内部 3 段(process_chat_payload → generate_chat_completion → process_chat_response)+
  task 完走待ち。**子 Request を新規生成**(親 request.state.metadata の汚染 = 親の追撃呼び出しが
  子 metadata を拾う罠の回避)。mount は `middleware.py` の load_skill 直後(to_thread、#28 規律)。
- 子一覧 API: `GET /api/v1/chats/{id}/children`(P3 のサイドバー tree / 観測 view の土台)
- admin UI: `SubagentPresetsSelector.svelte`(SkillsSelector 同型)+ Capabilities の featureSubagent
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
- [ ] P4 llens 追従: `tools/subagent.py`(486 行)置換。資料ハンドリング 9 ガード
      (所有者チェック/max_input_chars 等 — `llens:nucllei/tests/test_tool_guards.py` が固定)維持。
      skill `large-file-handling.md`・プロンプト・audio_tools の案内文も追随必要

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
