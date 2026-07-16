---
name: no-git-ops-on-llens
description: prism-hu/llens リポでは git 操作(commit/push/rebase 等)を一切しない
metadata:
  type: feedback
---

`_links/llens` → prism-hu/llens リポでは **git 操作を一切しない**（commit / push / rebase / fetch を跨いだ書き込み全部）。ファイルの作成・編集までは可。コミットが必要なら、変更を作った上でユーザーに委ねる。

**Why:** llens は北大で稼働中の現行運用リポで、他者も push する共有リポ。エージェントが勝手にコミット/rebase/push すると運用履歴を汚す。2026-07-08 に docling attachment を llens へ足した際、勝手に commit→（remote 分岐で）rebase→push してしまい注意を受けた。

**How to apply:** llens 配下のファイルは Write/Edit で置くだけに留める。「コミットして」と言われても対象が llens なら確認する。fork(technoplasm/nucllei)側の git 操作は従来通り可（master へ直接 refs #N でコミット）。
