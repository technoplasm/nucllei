---
name: upstream-check
description: upstream(open-webui/open-webui)の新リリースをタグ名レベルで確認し、#24(GHSA 追跡)の観点で報告する。低頻度(週次目安)で使う。ソースコードや diff の実装内容は閲覧しない。
---

# upstream 新リリース確認(#24 定期監視)

**絶対制約**: v0.6.6 以降のソースコード・タグ間 diff の実装内容は閲覧しない
(`_memos/upstream-policy.md`)。見てよいのはタグ名・リリースノート・GHSA の外形情報のみ。

## 1. タグ確認

```bash
git ls-remote --tags https://github.com/open-webui/open-webui.git 'v*' | awk -F/ '{print $3}' | grep -v '\^{}' | sort -V | tail -3
```

`_memos/upstream-policy.md` の「確認済み最新 upstream タグ」と比較する。

## 2. 新タグがあった場合

- リリースノート(GitHub Releases)の確認を**提案するに留める**(勝手にコードを見ない)
- セキュリティ言及があれば #24 の観点で報告: v0.6.5 に該当しうる GHSA は
  外形情報(影響エンドポイント・条件)から自前バックポートを判断する
  (先例: #36〜#40、`_memos/security-advisories.md`)
- 確認したら `_memos/upstream-policy.md` の確認済みタグと確認日を更新し、
  `docs:` でコミット(refs #24)

## 3. 新タグが無い場合

「確認済み最新タグ(vX.Y.Z)から変化なし(確認日)」を報告し、
`upstream-policy.md` の確認日だけ更新するかを判断(数日以内なら省略可)。
