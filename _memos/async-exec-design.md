# 非同期 tool 実行基盤(#110)— 統合設計

ステータス: **提案・未承認・ken レビュー待ち**(2026-08-14)。5 次元設計(entity/api/runtime/fe/migration)
+ 各 2 本の敵対批評 + 呼び出し面比較を統合。前提: #109 は f1839349c で巻取り済み・tree clean。
migration head は `b4d19c7a52e3`(実装時に再確認)。file:line は現ツリーで検証済み。

## 1. 要件(issue #110 確定)

1. どの tool 実行も非同期化できる(subagent 専用にしない)
2. ターンを跨ぐ実体 = **execution 行**(一級エンティティ)。親メッセージの tool result は spawn 時の
   running 形のみで以後不変(append-only)。完了の真実は exec 行
3. 進行中の可視化: IN/OUT チップの左に走行中 exec インジケータ(件数・一覧・個別停止)
4. 取消できる(停止セマンティクスの明文化込み)
5. ユーザー入力なしでの親ターン再開(FE 自動再開を exec 行ベースで再設計)

## 2. 決定事項

### ken 確定(上書き禁止)

- **K1**: `run_async(tool_name, args)` の curry 化は不採用(静的 spec で per-tool の引数型を表現できない)。
- **K2**: 呼び出し面 = **tool 単位の静的適格性 `backgroundable`** + **呼び出し時の共有 `background` 引数**
  (適格 tool の spec にのみ注入)。昇格された呼び出しは execution チャンネル(exec 行)に乗る。
- **K3**: `background: true` の返りは一律 `{exec_id, status: "running"}`(結果の形には制約を置かない)。
- **K4**: 経過 poll = 非ブロッキング **`check_async(exec_id)`**(status + 最新 progress)+ FE の poll。
- **K5**: **`await_async(exec_id, timeout?)` は有界待ち**(既定 60〜120 秒程度)。時間切れは running 形を
  返し、モデルは「実行中」と報告してターンを終える → FE 自動再開が後で回収。
- **K6**: 完了時の割り込みはしない。境界にのみ届ける(ターン終了後 = FE 自動再開。ループ中注入は v2)。
- **K7**: リリース順序 = サブエージェント + 本土台の完成 → tag → llens P4。

### 統合判断(批評の fatal を裁定して確定した設計原則)

- **D1 状態遷移はすべて guarded UPDATE**(`WHERE id=? AND status='running'`、rowcount 0 = 負け = no-op
  → 行を再読)。終端は一方向・一度きり。根拠: timeout 孤児スレッド(middleware.py:1199-1204 の
  有界リーク)の遅延書き込みと stop/完走の同着は機構でしか防げない。
- **D2 stop の権威は endpoint 側**: stop API / cascade が「stopped へ CAS → 成功したら task.cancel()」、
  **runner は CancelledError で一切永続化しない**(re-raise のみ。書けなかった running 行は boot
  スイープが orphaned に)。裁定: runner が stopped を書く各次元案は、shutdown の一斉 cancel の誤記録・
  cancel 発生源の判別不能・except 内 DB 書きの中断(いずれも批評 fatal)を解消できない。
- **D3 ターン寿命リソース(extra_params)の硬化**: 全次元の批評が一致した最大の穴。callable は mount 時
  に `partial` で `__event_emitter__` / `__event_call__` を焼き込み済み(tools.py:45-50)。対処は §6 の
  硬化 + ContextVar 遮断。適格性 opt-in(D4)が一次防衛。
- **D4 適格性は opt-in**: builtin は `run_subagent` のみ既定適格。DB / tool server は admin の明示指定。
  `load_skill` / `check_async` / `await_async` と、自前で `background` パラメータを宣言する tool は
  恒久不適格(衝突ガード、warning ログ)。
- **D5 exec のタイムアウト既定は sync と別**: per-tool `timeout` 宣言が最優先、無ければ
  `ASYNC_EXEC_TIMEOUT`(既定 1800)。sync 既定 300 秒(env.py:271)の流用は非同期化の動機と矛盾。
- **D6 親ターンの stop は exec に伝播しない**(fire の意思の尊重 — #109 決定 3 の昇格)。止めたければ
  チップの個別停止。chat 削除だけは cascade(§5)。
- **D7 単一プロセスが不変条件**: `_exec_tasks` は in-process。`UVICORN_WORKERS`(env.py:309)> 1 なら
  起動時 warning + 本機能無効化(multi-worker では liveness 判定が他 worker の生きた exec を殺す)。
- **D8 usage カラムは維持**(issue 確定要件。runtime 次元の削除提案は棄却)。
- **D9 子ターン(agent session)には background を注入しない**: 子 chat の exec は FE 回収者不在で
  宙吊りになる(適格性判定に「親 chat が通常 chat」を含める)。

## 3. データモデル

`nucllei/models/executions.py` 新規(files/chats と同型の Base + Pydantic + Table)。

```python
class Execution(Base):
    __tablename__ = "execution"
    id           = Column(String, primary_key=True)   # exec_id(uuid4)
    user_id      = Column(String, nullable=False)     # 認可述語・per-user 会計
    chat_id      = Column(String, nullable=False)     # チップ一覧・cascade の引き当て
    message_id   = Column(String, nullable=False)     # spawn を含む親 assistant メッセージ
    tool_call_id = Column(String, nullable=False)     # spawn call の id(FE ブロック突合)
    tool_name    = Column(String, nullable=False)
    args         = Column(JSON, nullable=True)        # unknown-drop 後の実引数(監査)
    status       = Column(String, nullable=False)     # running|done|failed|stopped|orphaned
    result       = Column(Text, nullable=True)        # 正規化・切詰め済み文字列(§6)
    error        = Column(Text, nullable=True)        # モデル向け日本語文言
    meta         = Column(JSON, nullable=True)        # {render, render_descriptor, truncated,
                                                      #  original_chars, context_images, display, replayed_at}
    usage        = Column(JSON, nullable=True)        # 生戻り値 dict の "usage" キー転記のみ(合算しない)
    duration     = Column(Float, nullable=True)       # round(x,3) 秒(middleware.py:1265 と同粒度)
    created_at   = Column(BigInteger, nullable=False)
    finished_at  = Column(BigInteger, nullable=True)  # 終端状態になった時刻(実行終了時刻ではない)
    purged_at    = Column(BigInteger, nullable=True)  # GC(§8)
```

- FK なし(repo 慣行)。index は `(chat_id)` と `(chat_id, status)`(チップの主クエリ = chat 内
  running 絞り込み)。migration は `202608132015_b4d19c7a52e3_...` をテンプレに新規 1 本。
- Table 関数: `insert_execution` / `finish_execution(id, *, status, ...) -> bool`(D1 の guarded
  UPDATE + finished_at)/ `mark_running_as_orphaned_on_boot` / `get_execution_by_id` /
  `get_executions_by_chat_id` / `get_running_executions_by_chat_id` /
  `has_awaitable_executions_by_chat_id`(mount 判定、EXISTS)/ `purge_stale_results(before)`。
  async 文脈からは to_thread 必須(#28 規律)。

## 4. 状態機械

```
(INSERT) → running ─┬→ done      runner: callable 正常 return
                    ├→ failed    runner: 例外 / wait_for TimeoutError / spawn 後段失敗
                    ├→ stopped   stop API・cascade: CAS 先行 → cancel(runner は書かない)
                    └→ orphaned  boot スイープ(権威)/ 照会時 liveness ガード(防御)
```

- 書き手: running=spawn の INSERT のみ。done/failed=runner。stopped=stop 経路。orphaned=スイープ +
  liveness ガード。全員 guarded UPDATE(D1)なので同着は先勝ちで壊れない。
- liveness ガード(await_async / 個別 GET / stop): `running ∧ exec_id not in _exec_tasks` を観測したら
  **行を再読し、なお running のときだけ** orphaned へ CAS(完走直後の registry pop との取り違えレース
  対策 — 批評 fatal の修正形)。`created_at` から 10 秒未満は判定しない(spawn 窓グレース)。
  一覧 API は検出のみで書かない。
- orphaned の意味論: 記録は残るが計算は再開しない(冪等性は tool 側の宿題 — issue 明記)。

## 5. 実行系(`nucllei/utils/executions.py` 新規)

registry: `_exec_tasks: dict[str, Optional[str]]`(exec_id → task_id。None = spawn 中センチネル)、
`_active_execs_by_user: dict[str, int]`(per-user 会計。0 で pop)。

### spawn(execute_tool_calls 内の分岐から呼ばれる — §7)

1. 同時数 check → **同期で increment**(check→increment 間に await を挟まない — subagent.py:58 の
   規律)。超過は即 `_error`(行を作らない)。
2. `exec_id = uuid4()` → `_exec_tasks[exec_id] = None`(**INSERT より先にセンチネル登録** — 誤 orphan 防止)。
3. `insert_execution`(to_thread)→ `tasks.create_task(_run_execution(...), id=f"exec:{chat_id}")`
   → `_exec_tasks[exec_id] = task_id`。2〜3 の失敗は except で decrement + registry pop + 行があれば
   failed へ CAS して `_error`(スロット恒久リークの fatal 修正)。done_callback = pop + decrement。
4. spawn 側の返り = `{"ok": true, "exec_id": ..., "tool_name": ..., "status": "running"}`(K3)。
   名前空間 `exec:{chat_id}` の理由: `GET /api/tasks/chat/{chat_id}`(main.py:958)は FE の SSE
   再アタッチ対象で、stream の無い exec task を混ぜると再アタッチ・走行判定が壊れる。

### runner `_run_execution(exec_id, entry, params)`

- 自前 `ToolOutputSink` を張る(create_task の context copy が引き継ぐ親 sink は return 直後に
  close される — middleware.py:1232)。`_in_exec` ContextVar も set(§6)。
- `timeout = entry.get("timeout") or ASYNC_EXEC_TIMEOUT` で `wait_for`(D5)。
- 正常 return → `_normalize_tool_result`(§6)→ 切詰め → guarded `finish_execution(done)`。meta へ sink の
  context_images / display と entry の `render.result`(render_descriptor)、生戻り値 dict の `usage` を転記。
- TimeoutError / Exception → `finish_execution(failed)`。DB 書き自体の例外も握って log(行は running の
  まま → 後で orphaned。「終了形は 2 つ」前提を守る批評修正)。
- **CancelledError → 何も書かず re-raise**(D2)。finally は sink close / ContextVar reset のみ。

### stop / cascade

- `stop_execution(exec_id)`: stopped へ CAS(error="実行は停止されました。副作用は完了している可能性が
  あります。")→ 勝ったら `tasks.stop_task(task_id)`。センチネル期間は「stop 予約」を registry 値に立て
  task_id 確定時に cancel。registry に無い running 行(再起動残骸)もこの CAS で stopped にできる。
- chat 削除 cascade: `_stop_chat_tree_tasks`(routers/chats.py:155)に exec stop を追加。**全削除経路も
  直す**: `delete_all_user_chats`(routers/chats.py:189-202)は per-chat の `_stop_chat_tasks` しか呼ばず、
  「cancel 連鎖で止まる」前提は detach された exec に崩壊(entity 批評 fatal の事実誤認修正)。ユーザー
  削除経路も同様。行は消さない(soft delete と対称の監査。減量は GC)。
- boot スイープ: lifespan(main.py:272)の `sync_builtin_tools`(:281)直後に `mark_running_as_orphaned_on_boot`
  (error="サーバー再起動により実行が失われました。再実行の可否は tool の性質によります。")+ GC purge(§8 GC)。

## 6. ターン寿命リソースの硬化(completion_events / tool_output)

exec は callable に焼き込まれた親ターンのオブジェクトを持ち込む。副作用チャネル別の対処:

| チャネル | ターン終了後の現挙動 | 対処 |
|---|---|---|
| `__progress__`(sink) | closed で no-op | runner が自前 sink に張替え。progress は registry 経由で check_async が読める(最新 1 件を sink に保持) |
| `__feed_image__` / `__display__` | closed チェック無しで死に sink に積まれ消失 | 自前 sink → exec 行 meta へ harvest → await 時 replay |
| `__event_emitter__` の DB 分岐(message/replace — completion_events.py:199-243) | stream 生死と無関係に親メッセージを変異(append-only 破り) | **`_in_exec` ContextVar が立っていたら DB 分岐を全面スキップ**(ターン中の `_persist_message_snapshot` との RMW 競合も同時に塞ぐ)。加えて closed ガードも追加 |
| `__event_call__`(completion_events.py:132) | closed 後は future が永遠に未解決 = timeout まで hang | `call()` 冒頭で closed なら即 RuntimeError。`close()`(:100-109)の pending は cancel でなく `set_exception(RuntimeError)`(in-flight を failed に落とし、cancel 経路の意味論汚染を防ぐ) |
| `chat:message:files`(sandbox exports 添付 — completion_events.py:255) | closed で無音消失 | v1 既知制約: 添付 UI は出ないが実体は Files に永続済み・tool result 文字列に列挙される。回収拡張は v2 |

正規化は middleware.py:1244-1257 を `_normalize_tool_result(raw)` に抽出して共有(挙動不変リファクタ)。
切詰めは `EXEC_RESULT_MAX_CHARS`(既定 65536)。超過時は **render を text に降格** +
`meta.truncated / original_chars`(不正 JSON を FE に渡さない批評修正)。

## 7. 呼び出し面(K2: backgroundable + background 引数)

- **適格性**: tool エントリの per-tool キー `"backgroundable": True`(`timeout` キーと同型)。builtin
  では `build_run_subagent_tool`(middleware.py:529)の entry に付与。DB / tool server は opt-in(D4)。
- **spec 注入**: `form_data["tools"]` 合成(middleware.py:539-543)で、適格 entry の spec の**コピー**に
  `background: {type: boolean, description: "true で非同期実行。即座に exec_id が返り、結果は
  await_async / check_async で扱う。通常は指定しない。"}` を足す。第 2 経路 `inject_skill_tools`
  (middleware.py:493-512)も同じヘルパを通す。`tools_dict` 側の spec は汚さない(unknown-drop の基準)。
- **分岐点**: `execute_tool_calls`(middleware.py:1120)の引数パース(:1141-1153)後、
  `tool = tools[tool_name]`(:1170-1172)の直後で `background = params.pop("background", False)` —
  **allowed_params フィルタ(:1192-1196)より前に必ず pop**。truthy ∧ backgroundable ∧ 通常 chat(D9)
  ∧ metadata に chat_id/message_id あり → §5 の spawn → running 形を results に積んで continue。
  不適格 tool への指定は無視して同期実行(warning ログ)。同期側は無変更。
- **check_async / await_async の mount**: 適格 tool が載るターン、**または** chat に未回収 exec がある
  場合(`has_awaitable_executions_by_chat_id`、to_thread)に builtin 注入(run_subagent mount の直後・
  middleware.py:539 の直前)。builtin なので #101 の履歴再導出は不要。
- 認可: 昇格は「mount 済み = 認可済み」の callable をそのまま使う。exec_id は履歴に載るが単なるキーで、
  権威は server-authored な exec 行の (user_id, chat_id) 束縛(#101 整合)。

## 8. check_async / await_async の spec

```python
check_async(exec_id: str) -> 即返し
  # 述語: 行が存在 ∧ user_id == user.id ∧ chat_id == 親 chat_id(subagent.py:394-410 と同型)
  # 返り: {exec_id, tool_name, status, elapsed_s, progress?} — progress は registry の sink の最新 1 件
  #   (best-effort・プロセス内)。terminal でも結果は返さず「await_async で回収」を促す

await_async(exec_id: str, timeout: int = 90) -> 有界待ち(K5)
  # 述語は check_async と同一。timeout は 10..300 に clamp。entry の per-tool timeout は 0
  #   (middleware.py:1205-1212 の wait_for を無効化 — 内側の自前 wait_for が running 形を返すため)
  # running ∧ task あり → asyncio.wait_for(asyncio.shield(task), timeout)
  #   TimeoutError → {exec_id, status:"running", elapsed_s}(exec には触らない。モデルは「実行中」と
  #     報告してターンを終える → FE 自動再開が回収)
  #   CancelledError → current_task().cancelling() なら親 stop = re-raise(exec は止めない — D6)。
  #     さもなくば行を再読して status どおり返す(subagent.py:258-268 のパターン)
  # running ∧ task 不在 → liveness ガード(§4)
  # done → {ok:true, exec_id, tool_name, status, result, duration, usage?}
  #   result は保存 render が json なら json.loads した生 object(二重エスケープ回避)。
  #   meta.context_images / display は初回のみ自 sink へ replay(meta.replayed_at を guarded UPDATE で
  #   立てる — VLM 再注入の重複防止)。meta.render_descriptor は sink.set_result_render() 経由で
  #   results[].render に上書き(execute_tool_calls に harvest 1 点追加 — subagent 描画に必須)
  # failed/stopped/orphaned → {ok:false, exec_id, tool_name, status, error}。再 await は冪等
```

同時数: `ASYNC_EXEC_MAX_CONCURRENT`(per-user、既定 4)+ `ASYNC_EXEC_MAX_TOTAL`(プロセス全体、
既定 16)。超過は即エラー。SUBAGENT_MAX_CONCURRENT=4(env.py:297)とは別会計(exec 席 ≠ 子ターン席
の AND が正しい会計)。default executor 枯渇(全 DB アクセス共有)は
TOTAL 上限が実効防衛 — 専用 executor 隔離は v2。

## 9. FE(インジケータ・render・自動再開)

- **API**: `GET /api/v1/chats/{id}/executions`(routers/chats.py:589 `/children` の隣、所有者検証同型。
  応答は id/tool_name/status/created_at/finished_at/duration/error — result 本文は載せない)/
  `POST /api/v1/chats/{id}/executions/{exec_id}/stop`。SSE push はしない — 5 秒ポーリング(既存慣行)。
- **store** `executions.svelte.ts`(chat-children.svelte.ts と同型・requestSeq ガード)。
  **`refreshAndScan()` に一本化**: fetch → store 差し替え → terminal 未回収の enqueue 走査を**常に**
  実行し、loadChat seed / onTurnEnd / poll tick / stop 後の全経路がこれを呼ぶ(fe 批評 fatal: tick
  述語ゲート内にだけ走査を置くとリロード後・turn 末完了が永久に再開されない)。poll 述語 P は cadence
  制御のみ: running ∨ taskIds ∨ terminal 未回収 ∨ 直近 refresh 失敗(自己接地)。
- **ExecChip**: UsageChip の左(MessageInput.svelte:437 の floating 行)。running + 未回収 terminal の件数
  バッジ。popover に一覧(tool_name・経過・個別停止)。0 件なら非表示。
- **render**: classifyToolResult(tool-render/index.ts:179)に「parsed が exec_id ∧ status='running'」
  → 新 kind `execution` の分岐を descriptor 判定より先に追加(spawn call の descriptor は実 tool の
  ものなので shape で判定)。新 `ExecutionBlock.svelte` は store を読む 1 行ステータス表示。
  **ToolCallBlock のバッジ特別扱いが必須**: spawn は result が即存在し `isDone`(ToolCallBlock.svelte:91)
  が緑チェックを出すため、kind=execution ∧ store running のときはスピナーに上書き(fe 批評 fatal)。
  await の結果は render_descriptor パススルー(§8)で既存ブロックが描く。
- **自動再開** `execution-resume.svelte.ts`(#109 の once ガード・handled 判定・送信ガード列を exec_id
  ベースで再構築): terminal 未回収を検知 → 定型 user メッセージをバッチ 1 通で送信。
  - 文言は status 別(done/failed:「await_async で回収し続行」/ orphaned:「結果は失われた。状況を
    報告せよ」)。**full uuid を本文に必ず含める**(handled 判定の要)。
  - handled 判定 = 履歴に `await_async` call(arguments が exec_id を含む)+ 同 tool_call_id の
    result。**take 時にも handled drop を再評価**し、送信成功後に消費確定(失敗時 requeue)。
  - **連鎖上限**: ユーザー発話なしの自動 turn を chat 単位 N 回(既定 5)で停止しチップ表示。
    非表示タブは poll ごと休止。観測モード(isAgentSession)は対象外。
  - onTurnEnd フックを events.ts `#finishCompletion`(:315)末尾に再導入(f06626f0a の 12 行)。

## 10. subagent との統合

- 非同期 subagent = `run_subagent(..., background: true)`(#109 の専用 background/await_subagent は
  復活させない)。**session_id = 継続可能ハンドル / exec_id = 1 実行の記録**の二層。継続の非同期化も
  `run_subagent(session_id=..., background: true)` = 新しい exec 行。
- run_subagent 側の変更は原則ゼロ: `_active_turns` 上限(subagent.py:364-369)・並行継続拒否
  (:394-399)・SUBAGENT_TURN_TIMEOUT は callable に閉じており exec 経由でも効く。exec stop →
  runner cancel → shield + cancelling() の子 stop 連鎖(subagent.py:258-268)で子 chat も止まる。
- orphaned 時の回復: 子の部分回答は chat に永続済み — 同期 `run_subagent(session_id=...)` で回復
  (汎用機構に subagent 固有フォールバックは混ぜない)。
- 観測 UI: 走行中はサイドバー tree + /c/ 観測モードが既にカバー。spawn 直後の親メッセージ内インライン
  観測は不可(v1 制約 — 回収後に render_descriptor で復元)。

## 11. フェーズ分割と見積り(#109 実績を係数に、計 5.5〜6.5 セッション)

| P | 内容 | 目安 |
|---|---|---|
| P1 | executions モデル + migration + boot スイープ + 一覧/stop API + cascade(全削除経路込み)+ test_executions.py | 1 |
| P2 | completion_events 硬化 3 点(先行コミット)+ executions.py(spawn/runner/stop)+ spec 注入 + execute_tool_calls 分岐 + check_async/await_async + env 4 本 + test_async_exec.py | 2 |
| P3 | subagent 統合検証(同期 18 テスト無改変 green が回帰条件)+ f06626f0a の async テスト翻訳復活 + README_API §5.9 | 0.5〜1 |
| P4 | FE: store/ExecChip/ExecutionBlock/render/自動再開 + vitest 移植 | 1.5〜2 |
| P5 | 実モデル E2E(fan-out / fire-and-forget / 自動再開 / 再起動→orphaned→同期継続回復)→ `uv run task tag release` | 0.5 |

tag 後に llens P4(userspace subagent.py の builtin 置換・プロンプト整備)— K7 どおり。

## 12. 却下した代替案

- **run_async(tool_name, args) curry builtin**: K1(spec の型表現不能)+ JSON-in-JSON・FE の実 tool 名喪失。
- **spec 非公開の undeclared 引数 / 名前 suffix**: constrained decoding で出力不能リスク / spec 汚染倍化。
  **完全静的(C 単独)**は per-call の同期/非同期選択(#109 E2E で実証)を殺すため却下。
- **await_subagent 温存 / tool 種別ごとの await**: 同一機能の二重機構 / spec 増で利得なし。
- **exec task を素の chat_id 登録 / task_id の DB カラム化**: FE 再アタッチ汚染 / 再起動で無効(§5 で分離)。
- **完了時に親メッセージの tool result を書き換え**: append-only 違反(issue で棄却済み)。
  **exec 状態の SSE push / 完了割り込み**も却下 — 器が無い / K6(ポーリング + 境界配達)。
- **exec_id = session_id の同一視 / read-once 化**: 寿命が違う / 冪等性を壊す利得なし。
- **stateful tool の直列化を core に持つ**: core は stateful 性を知り得ない(sandbox は Session.lock で
  自前直列化済み。実行順不定・キュー待ちの timeout 食い込みは spec 文言で明記)。

## 13. 残リスク(既知として受容 v1)

- sync tool の cancel は待ちが外れるだけでスレッドは走り切る(middleware.py:1199-1204 と同型の有界
  リーク)。stopped でも副作用は完了しうる(error 文言に明記)。
- exec は spawn 時点の認可・token で最後まで走る(ユーザー無効化・tool 剥奪は走行中 exec に効かない。
  tool server の session token は途中失効で failed になりうる)。
- ターン中の chat JSON RMW 競合は ContextVar 遮断(§6)で emitter 経路のみ塞いだ。tool 独自の DB 書き
  (llens sandbox の files upsert 等)は tool 側の宿題。
- sandbox exports の添付 UI はターン終了後出ない(実体・result 文字列は残る — §6)。
  複数タブの自動再開二重送信は秒未満の窓が残る(await の冪等性で無害化 — ムダ 1 turn)。
- default executor(min(32, cpu+4))は遅い sync tool が並ぶと逼迫しうる(専用 executor 隔離は v2)。

## 14. Open questions(ken 判断待ち)

1. **backgroundable の opt-in 粒度**(DB/tool server の admin 設定)。— 推奨: v1 は run_subagent のみ適格。
2. **sandbox run_python の適格化**(exports 添付 UI 消失の許容)。— 推奨: llens P4 で適格化 + 制約明記。
3. **同時数の既定**: per-user 4 / total 16 / 超過即エラー。— 推奨: この既定(admin 口は別 issue 化)。
4. **保持と切詰めの既定**: RETENTION_DAYS=0(GC しない)/ MAX_CHARS=65536。— 推奨: この既定(安全側)。
5. **ユーザー停止した exec も自動再開で通知するか**。— 推奨: する(stopped の報告文で閉じさせる)。
6. **自動再開の連鎖上限 N**(ユーザー発話なしの連続自動 turn)。— 推奨: 5。
7. **completion_events 硬化 3 点の扱い**。— 推奨: #110 内で先行コミット(exec の成立条件そのもの)。
8. **await_async の既定 timeout**(K5 の 60〜120 のどこか)。— 推奨: 90 秒(clamp 10..300)。
9. **check_async の progress 露出**: in-process sink の最新 1 件で良いか。— 推奨: v1 は可(永続化は v2)。
10. **親ターン stop の非伝播(D6)の最終確認**。— 推奨: 非伝播(チップ個別停止がある)。

## 承認と追補(2026-08-14 ken)

- open questions は全項**推奨どおり**で確定。ただし以下を上書き:
  - Q1/Q2: v1 の backgroundable は run_subagent に加えて **sandbox run_python も適格**にする
    (「時間のかかる python も background にできるといい」)。DB tool の適格性フラグ機構を v1 に含める。
    検証は sleep を含む実行で行う
- 進行中インジケータ(チップ)と inactive preset の spawnable 化は先行実装済み
  (`e5f10f00d` / `5b819db96`)— #110 実装でチップのデータ源を exec 行へ切替
- ゴミ tool の基準 = 「llens repo に現存しない・明らかに stale」。dev DB から subagent / cage / doc を削除済み
