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

**P0(ken の手が要る)**

| # | 内容 |
|---|---|
| 1 | **チップの目視確認**(入力欄下部行の左・停止できること)— 自動検証・スクショ済みだが ken 要求の本丸なので最終確認は ken の目で |
| 2 | ghcr の `v20260814-01` イメージ削除(**packages スコープが要る** — Claude では消せない) |

**P2 — 堅牢性(残るレビュー B 群)**

| # | 内容 |
|---|---|
| 3 | B-2 exec 一覧 poll の失敗時リトライ(backoff・上限)— lastRefreshFailed で poll は継続するが間隔は固定 5 秒 |
| 4 | B-3 モデルが回収しない running 行の滞留(DB のゴミ)— 配達で大半消えるが running 放置は残る |
| 5 | B-5 `delete_all_user_chats` の N+1 |
| 6 | B-6 exec 一覧が全行を毎 5 秒返す(単調増加。一覧上限は check_task 側のみ) |

**P3 — 出荷**

| # | 内容 |
|---|---|
| 7 | **tag**(サブエージェント + 非同期土台が揃った — `uv run task tag release`。切る前に ken と解釈の再確認) |
| 8 | **llens P4**: `tools/subagent.py` 置換 / sandbox・audio の `backgroundable` 宣言を本番 tool へ / **プロンプト・skill の案内文を task 用語(check_task/await_task/stop_task・task_id)へ追随** / sandbox description の直列化文言 / **STT は遅い(ken 2026-08-14)**: 長尺音声は background へ誘導する案内 + `transcribe_speech` の per-tool `timeout` 宣言を検討(同期既定 300 秒では長尺が死ぬ。background 側は `ASYNC_EXEC_TIMEOUT`=1800 を維持 — 20 分案は STT の遅さを理由に見送り) |

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
