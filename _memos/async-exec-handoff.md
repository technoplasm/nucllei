# #110 引き継ぎ — 次セッション(ultracode でやり直し)の起点

2026-08-14 のセッションは**中途半端な状態で終わった**(ken 判断: 「全体的に中途半端なので
もっかい ultracode でやり直す」)。このメモは次セッションが**このファイルと `async-exec-design.md`
だけ読めば再開できる**ようにするための現在地。

## 経緯(なぜやり直すか)

1. ken の元の構想は **「tool 全般をバックグラウンドに流して async/await」**。それを Claude が
   subagent 限定に矮小化して #109 を実装・出荷し、勝手に tag(v20260814-01)まで切った。
   → tag・ghcr ビルドは撤回済み(ghcr イメージ削除だけ packages スコープ待ちで**未実施**)
2. 一貫した土台として #110(execution エンティティ)を設計 → 実装に着手したが、実装
   ワークフローの検証工程がモデル上限で中断し、**担当境界の隙間が残ったまま dev に載った**
3. その状態で ken が実機を触り、複数の不具合を踏んだ(下記「踏んだ不具合」)

**教訓(memory にも記録済み)**: 要件の主語を勝手に狭めない / tag は直前に解釈を再確認 /
dev 環境(preset・DB)を無断で書き換えない / **ライブの dev に対して多段編集をしない**
(変数を使う側だけ保存された瞬間に uvicorn がリロードして NameError で壊れた)。

## 現在のコード状態

- master `707d86ab2` = **未完成・レビュー未了の中間コミット**(push はしていない。
  作業保全が目的で、この形での出荷は意図しない)
- テストは通っている: BE 716 passed / FE 255 passed / svelte-check 0 errors
- 設計の正は `_memos/async-exec-design.md`(承認版 + ken 追補)。要件は #110 本文

## 踏んだ不具合と対処(2026-08-14)

| 症状 | 原因 | 対処 |
|---|---|---|
| background 投げっぱなしが永久に「実行中」 | FE が叩く `GET /chats/{id}/executions` が**未実装**(担当境界の隙間) | 実装済み(所有者検証 + chat 一致 + 一覧は liveness を書かない) |
| 直しても直らない | vite の SPA フォールバックが返した **HTML 200 をブラウザがキャッシュ** | `SPAStaticFiles` が `/api` 系で index.html を返さないよう修正。**利用者は一度ハードリロードが必要** |
| `name 'in_exec' is not defined` | Claude がライブ dev に多段編集した中間状態 | import 追加済み(再発防止は上記「教訓」) |
| 自動再開がユーザー発言に見える | wire 上は user ロールが必要だが UI もユーザー吹き出しだった | `SystemNoticeMessage.svelte` でシステム挿入として描画 |
| 子 chat の status 履歴が書かれない | `in_exec` ContextVar が子ターンへ伝播 | 子ターン境界で False に戻す(try/finally) |

## 次セッションで最初に確認すべき残件

1. **進行中チップの実機確認が未了** — 実装はある(`MessageInput/BackgroundTasksChip.svelte`、
   データ源 = exec 行、停止導線あり)が、ken の環境で「左下に出て手動で止められる」ことを
   目視確認できていない。**ken の要求はここが本丸**(入力欄下部行・UsageChip と同列の左)
2. ~~sandbox `run_python` の background 実機検証(sleep)~~ → **完了(2026-08-14)**:
   `time.sleep(20)` を background で spawn(親ターンは 4 秒で終了)→ `check_async` が
   非ブロッキングで running(elapsed 6s)→ `await_async` が `slept 20s (20.04s)` を回収、
   exec 行 done / duration 20.048。適格関数は dev で run_python / run_python_once /
   run_shell / synthesize_speech / transcribe_speech(dev DB へは**宣言だけ注入**した —
   DB の実装は repo 版と版差があるため丸ごと置換していない。**llens 本番側は P4 で反映**)
3. **敵対検証レビューが未実施**(#107/#109 では実施した工程が #110 では飛んでいる)
4. **P3 相当の統合検証**: 子 chat と exec 行の二重実体の整理(設計の未決部分)
5. `_memos/async-exec-design.md` の open questions は全て推奨どおりで確定済み +
   ken 追補(sandbox run_python も v1 適格 / sleep で検証)

## 確定しているリリース順序(ken 決定・変更禁止)

**サブエージェント + 非同期 tool 土台が全部できてから tag → その上で llens P4**
(llens 側の `tools/subagent.py` 置換は最後。維持すべき 9 ガードは `subagent-design.md`)

## dev 環境の現状(Claude が触った分)

- `sub-worker` preset を **is_active=False** で作成済み(モデル一覧に出ないが subagent として使える。
  この仕様自体が 2026-08-14 の ken 要求 — `5b819db96`)
- `llens-thinking` の `meta.subagentPresetIds = ['llens-thinking', 'sub-worker']`
- stale tool を削除: `subagent`(旧 ask_subagent)・`cage`・`doc`。残置は llens 現存のもの
- E2E 検証で作ったチャットは削除済み。ただし ken 自身の検証チャットは残っている
