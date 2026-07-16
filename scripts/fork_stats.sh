#!/usr/bin/env bash
# fork の書き換え量レポート: v0.6.5 起点(origin/main)→ HEAD の累積 diff。
# 「起点 = 空ツリーとの diff(+XXX -0)」の流儀で総量も出す。
# 実コード = lock / static / i18n locales / CHANGELOG を除外(体感に近い数字)。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

BASE=origin/main
EMPTY=$(git hash-object -t tree /dev/null)
EXCLUDES=(':!package-lock.json' ':!pnpm-lock.yaml' ':!static' ':!src/lib/i18n/locales' ':!CHANGELOG.md')

echo "== 起点(${BASE} = v0.6.5)の総量 =="
git diff --shortstat "$EMPTY" "$BASE"
echo "== 起点・実コード =="
git diff --shortstat "$EMPTY" "$BASE" -- . "${EXCLUDES[@]}"
echo
echo "== ${BASE} → HEAD($(git rev-parse --short HEAD))全体 =="
git diff --shortstat "$BASE" HEAD
echo "== 同・実コード =="
git diff --shortstat "$BASE" HEAD -- . "${EXCLUDES[@]}"
echo "==   内訳: backend(*.py) =="
git diff --shortstat "$BASE" HEAD -- 'backend/**/*.py'
echo "==   内訳: frontend(src/、locales 除く) =="
git diff --shortstat "$BASE" HEAD -- src ':!src/lib/i18n/locales'
echo
echo "== HEAD の実コード総量 =="
git diff --shortstat "$EMPTY" HEAD -- . "${EXCLUDES[@]}"
