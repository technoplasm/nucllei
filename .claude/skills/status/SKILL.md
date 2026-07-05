---
name: status
description: llens fork の現在地レポート。ロードマップ(issue #16)の進捗、v0.6.5 基点からの自分の差分、テスト状態、upstream 新リリースの有無をまとめ、次に着手すべき issue を提案する。セッション開始時や「現在地確認」「進捗確認」を求められたときに使う。
---

# llens 現在地レポート

以下を順に収集し、最後に 1 つのレポートにまとめる。独立したコマンドは並列実行してよい。

ブランチモデル: `main` = upstream v0.6.5 分岐点(凍結)、`master` = 出荷、`develop` = 開発 head。
v0.6.5 タグはローカルに存在しないため、基点参照は常に `origin/main` を使う。

## 1. fork の進行状況(v0.6.5 基点 = origin/main からの自分の差分)

```bash
git log --oneline origin/main..develop | head -20   # 基点からの自コミット
git diff --stat origin/main...develop | tail -3     # 変更規模
git status --short                                   # 未コミットの作業
git log --oneline master..develop | wc -l            # 未出荷コミット数(develop が master より先行している分)
```

## 2. ロードマップ進捗

```bash
gh issue view 16 -R technoplasm/open-webui   # tracking issue(フェーズ別チェックリスト)
gh issue list -R technoplasm/open-webui --state open --json number,title,labels
gh issue list -R technoplasm/open-webui --state closed --json number,title
```

closed issue を #16 本文の実行順(⓪ → A〜H トラック)に照らし、「どのトラックまで完了しているか」を判定する。
#16 のチェックリストと closed 状態がズレていたら報告する(実装コミットがあるのに open のままの issue も含む)。
実行順の一次情報は #16 の**本文**(コメントは改訂履歴)。

## 3. テスト状態(CORE 保護)

プロジェクトの venv(リポジトリ直下 `.venv`)の pytest で実行する:

```bash
cd backend && ../.venv/bin/pytest open_webui/test -q 2>&1 | tail -5
```

## 4. upstream 新リリース確認(タグ名レベルのみ)

upstream の remote は意図的に登録していない。タグ名だけを ls-remote で確認する:

```bash
git ls-remote --tags https://github.com/open-webui/open-webui.git 'v*' | awk -F/ '{print $3}' | grep -v '\^{}' | sort -V | tail -3
```

`_memos/upstream-policy.md` の「確認済み最新 upstream タグ」と比較し、新しいタグがあれば報告する。
**注意: 新リリースがあってもソースコードや diff の実装内容は閲覧しない(_memos/upstream-policy.md 参照)。**
リリースノートの確認を提案するに留める。

## 5. レポート出力

以下の形式でまとめる:

- **現在フェーズ**: Phase N(完了: #x, #y / 進行中: #z)
- **fork の差分規模**: v0.6.5(origin/main)から N コミット、M ファイル変更。master への未出荷 K コミット
- **テスト**: green / red / 未整備
- **upstream**: 新リリース有無(あればタグ名のみ)
- **次の一手**: #16 本文の実行順(⓪ → A〜H)で、未完了の最先頭項目を根拠付きで提案。
  ⓪(セキュリティ)が未完了ならトラックに関係なく常に最優先で挙げる。
