#!/bin/sh
# nucllei sandbox: LibreOffice の wrapper (Dockerfile が /usr/local/bin/soffice に置く)。
# jail は rootfs read-only・HOME=/workspace (永続・quota 対象) なので、LibreOffice が書くもの
# を揮発の /tmp に寄せる:
#   - ユーザープロファイル → -env:UserInstallation=file:///tmp/lo-profile
#     (呼び出し側が -env:UserInstallation= を渡していればそちらを優先)
#   - $HOME/.config (プロファイルとは別に空ディレクトリを作る) → HOME を /tmp 配下へ
# /tmp は実行ごとに消えるので、毎回まっさらなプロファイルで起動する (前回のロックが残らない)。
# 初回起動のプロファイル生成込みで 1 秒未満。
T="${TMPDIR:-/tmp}"
HOME="$T/lo-home"
export HOME
mkdir -p "$HOME"
for a in "$@"; do
  case "$a" in
    -env:UserInstallation=*) exec /usr/lib/libreoffice/program/soffice "$@" ;;
  esac
done
exec /usr/lib/libreoffice/program/soffice "-env:UserInstallation=file://$T/lo-profile" "$@"
