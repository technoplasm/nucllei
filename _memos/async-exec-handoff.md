# #110 引き継ぎ — redo 完了後の現在地(2026-08-14 夜 改訂)

前版(中途半端な中断の申し送り)は役目を終えたため全面書き換え。
**redo は完了した** — 設計の正は `_memos/async-exec-design.md` の追補 5、
実装は master `35ab3e885` / `49042324d` / `0211083ee` / `9ed4bd13f`。

## 完了したこと(2026-08-14 redo セッション)

- **task 用語統一**: `check_task`(状態確認のみ・include_done)/ `await_task`(結果
  取り出し一本化・複数 id・部分回収)/ `stop_task`(強制終了)。`task_id` キー。
- **サーバ駆動配達**: FE 駆動自動再開を全廃し、`execution_resume.py` の driver が
  notice(結果全文差し込み)+ 続きターンを BE 自身で起動。`delivered_at` で配達状態を
  事実として管理。ブラウザ完全クローズ中でも配達される(実測済み)。
- **切詰め一般化**: `TOOL_RESULT_MAX_CHARS`(生産時一度だけ)。
- A-3 / elapsed float / 保持・直列化の description / B-1・B-4(機構ごと消滅・根治)。
- **品質工程**: 設計批評(FATAL 6 裁定)→ 実装 → 敵対検証レビュー(9 件確定・全修正)
  → 全テスト緑(BE 774 / FE 268 / svelte-check 0)→ 実機 E2E(チップ・停止・
  閉タブ配達・stopped 通知)。
- dev 実機の E2E チャット「バックグラウンド ジョブ 実行」は**残置**(ken の目視確認用。
  不要になったら削除してよい)。

## 残タスク

**P0 — 完了(2026-08-16 ken 確認)**

| # | 内容 |
|---|---|
| 1 | ~~チップの目視確認~~ ✔ ken 目視済み(2026-08-16)。dev の E2E チャット「バックグラウンド ジョブ 実行」は不要になったら削除してよい |
| 2 | ~~ghcr の `v20260814-01` イメージ削除~~ ✔ ken 対応済み(2026-08-16) |

**P2 — 堅牢性(残るレビュー B 群)**

| # | 内容 |
|---|---|
| 3 | ~~B-2 exec 一覧 poll の失敗時リトライ~~ ✔ 出荷(2026-08-16、`1d95a9ea4`): 連続失敗で 5s→倍々→60s cap、成功で解除。述語(続ける根拠)とは分離した間引きゲート |
| 4 | ~~B-3 running 行の滞留~~ **close(2026-08-16、ken 裁定「明らかにおかしいなら直す、でなければ閉じる」)**: 実装確認の結果、running が終端に到達しない穴は無し — runner の finally + finish_execution CAS / boot スイープ(orphaned)/ liveness ガードの三層。terminal はサーバ駆動配達が回収し、行の単調増加は監査目的の設計で GC(`EXEC_RETENTION_DAYS`、既定 0=無効)実装済み — 残るのは llens 側で retention を有効にするかの運用判断のみ |
| 5 | ~~B-5 `delete_all_user_chats` の N+1~~ ✔ 出荷(2026-08-16、`71c04d019`): user_id スコープの一括読み(id 専用 chat 一覧 + running / 未配達 terminal)へ。cascade の意味(契約 M2)は不変 |
| 6 | ~~B-6 exec 一覧が全行を毎 5 秒返す~~ ✔ 出荷(2026-08-16、`71c04d019`): `?scope=active`(running ∨ 未配達 ∨ 直近終端)で tick を有界化。seed は全量のまま(ExecutionBlock は store に無い行を running 扱い)・FE はマージで終端行を保持・非最終行が active から消えたら全量で照合し直す(>追従窓の休止の追いつき) |

**→ B 群はこれで全消化**(B-1/B-4 は redo で機構ごと消滅、B-2/B-5/B-6 出荷、B-3 close)。残は P3(tag → llens P4)のみ。

**P3 — 出荷**

| # | 内容 |
|---|---|
| 7 | **tag**(サブエージェント + 非同期土台が揃った — `uv run task tag release`。切る前に ken と解釈の再確認) |
| 8 | ~~llens P4~~ ✔ コード出荷(2026-08-16、llens `0a3b672`〜`c06293f` — ローカルコミット・push は ken 判断): subagent を agent session 機構へ置換(session_id 化・9 ガード維持・画像は子の feed_image へ誘導)/ sandbox・audio の backgroundable + 直列化文言 / transcribe_speech の timeouts=1800(nucllei `2564a7857` の per-entry timeout 宣言を新設して使用)/ プロンプト・skill の task 用語追随 / README_API スナップショット同期。**残る配備手順(admin 操作)**: sub-reader preset 作成(toolIds に feed_image)+ 主 preset の meta.subagentPresetIds 追加 + ask_subagent valve の preset id 確認 |

## 実装の勘所(次セッション向けメモ)

- 配達 driver の設計不変条件: **notice 追記と delivered_at 確定は同一トランザクション**
  (`append_delivery_turn`、updated_at CAS)。delivered_at は一度立てたら戻さない。
  claim はプロセス内(D7 単一プロセス前提)。
- chat 行の updated_at は**厳密単調増加**(同秒 ABA 対策)。FE 保存 endpoint も CAS 化
  (負け → 再読 → append-only 再検証 → 再試行 3 回 → 400)。
- 非表示タブは poll 休止 → 復帰後 `deliveredAdvanced` 増分で history refetch(実 E2E で発見・修正)。
- `visibilityState=hidden` の窓(claude-in-chrome 含む)では poll が止まるのは仕様。
- ブラウザ自動操作の type は CJK 混じりで ASCII 部分を落とすことがある(E2E 2 通目で実害。
  `document.execCommand('insertText')` の方が確実)。
