---
name: status
description: llens fork の現在地レポート。ロードマップ(issue #16)の進捗、v0.6.5 基点からの自分の差分、テスト状態、upstream 新リリースの有無をまとめ、次に着手すべき issue を提案する。セッション開始時や「現在地確認」「進捗確認」を求められたときに使う。
---

# llens 現在地レポート

以下を順に収集し、最後に 1 つのレポートにまとめる。独立したコマンドは並列実行してよい。

## 1. fork の進行状況(v0.6.5 基点からの自分の差分)

```bash
git log --oneline v0.6.5..HEAD | head -20   # 基点からの自コミット
git diff --stat v0.6.5...HEAD | tail -3     # 変更規模
git status --short                           # 未コミットの作業
```

## 2. ロードマップ進捗

```bash
gh issue view 16 -R technoplasm/open-webui   # tracking issue(フェーズ別チェックリスト)
gh issue list -R technoplasm/open-webui --state open --json number,title,labels
gh issue list -R technoplasm/open-webui --state closed --json number,title
```

closed issue をフェーズ順(Phase 0 → 4)に照らし、「どのフェーズまで完了しているか」を判定する。

## 3. テスト状態(CORE 保護)

CORE 回帰テスト(issue #3 以降で整備)が存在する場合は実行する:

```bash
cd backend && pytest open_webui/test -q 2>&1 | tail -5
```

テストが未整備の段階では「CORE 保護テスト未整備(issue #3 未着手)」と報告する。

## 4. upstream 新リリース確認(タグ・リリースノートレベルのみ)

```bash
git fetch remote --tags --quiet && git tag --list 'v*' | sort -V | tail -3
```

`_memos/upstream-policy.md` の「確認済み最新 upstream タグ」と比較し、新しいタグがあれば報告する。
**注意: 新リリースがあってもソースコードや diff の実装内容は閲覧しない(_memos/upstream-policy.md 参照)。**
リリースノートの確認を提案するに留める。

## 5. レポート出力

以下の形式でまとめる:

- **現在フェーズ**: Phase N(完了: #x, #y / 進行中: #z)
- **fork の差分規模**: v0.6.5 から N コミット、M ファイル変更
- **テスト**: green / red / 未整備
- **upstream**: 新リリース有無(あればタグ名のみ)
- **次の一手**: ロードマップ #16 のフェーズ順で、未着手の最若番 issue を根拠付きで提案。
  ただし依存関係を優先する(例: #17 は #3 のテスト整備後が安全、#5 は #4 と統合して進める)。
