---
name: catchup
description: 開発セッション開始時に前回の文脈を取り戻し、dev 環境が動く状態を確認して、次の一手を出す(/sync-docs の読み側)。「開発再開」「どこまでやったっけ」「始めよう」で使う。速さ優先 — テストと upstream 確認はやらない。
---

# llens 開発再開(catchup)

速さ優先で 30 秒以内に「どこまでやったか・環境は動くか・次に何をするか」を出す。
**やらないこと**: フルテスト(CORE 回帰は「変更を加えるとき」に回す — CLAUDE.md 規約)、
upstream 確認(低頻度でよい — `/upstream-check`)、v0.6.5 からの差分規模の集計。

## 1. git 現在地

```bash
git log --oneline -10 master
git status --short                                        # 未コミット(あれば最優先で始末の判断)
git rev-list --count origin/master..master 2>/dev/null    # 未 push 数(origin/master が無ければ省略)
```

## 2. 前回の区切りを読む

- 直近コミットの `refs #N` から対象 issue を特定し、**自分の最新コメント(前回の
  /sync-docs ロールアップ)** を読む: `gh issue view N -R technoplasm/nucllei --comments | tail`
- TaskList で持ち越しタスクを確認
- 迷ったら open issue(`gh issue list -R technoplasm/nucllei`)と、対象 issue の
  handoff メモ(例: #110 → `_memos/async-exec-handoff.md`)を見る。
  ※ 旧 tracking #16 は 2026-08-13 close — **記録としてのみ参照**(実行順の判断に使わない)

## 3. dev 環境ヘルス(落ちていれば起動まで面倒を見る)

```bash
curl -sf -m 2 http://localhost:3000/health                # backend
ss -tlnp | grep -E ':(517[0-9])\b'                        # vite(5173 が他プロジェクトで埋まっていれば 5174 等)
curl -s -m 2 http://prism-llens:13300/v1/models | head -c 80   # SGLang(不達なら ollama gpt-oss:20b が代替)
```

- 起動手順・dev アカウント・fixture は `_memos/dev-environment.md`(backend は
  `PYTHONPATH=backend` 必須)。クリーン環境なら venv(`uv sync`)と
  `pnpm install`(node は `.node-version` 準拠)から。

## 4. レポート

- **前回どこまで**: 直近の出荷内容 1〜2 行 + 持ち越しタスク
- **足元**: 未コミット・未 push の有無 / 環境の生死(起動したなら何をしたか)
- **次の一手**: open issue と handoff メモの残タスク優先度(P0 > P1 > …)で
  未完了の最先頭を根拠付きで 1 つ提案(セキュリティ系の未完了があれば常に最優先)
