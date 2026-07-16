#!/usr/bin/env bash
# master SVG(favicon.svg / favicon-gray.svg)から favicon 資産一式を生成し配置する。
#
# 配置先は 2 種類:
#   [SOURCE] リポジトリ追跡の真のソース。ここだけをコミットする。
#     - static/static/*        (SvelteKit static。vite build → build/static へ入る)
#     - static/favicon.png     (/favicon.png 用)
#   [RUNTIME] 起動時に build/static から生成される派生物(gitignore 済み)。
#     ローカルの動作確認用に best-effort で同期するだけ。正規経路は `pnpm build` + 再起動。
#     backend 起動時に config.py:650 が build/static/** → nucllei/static/** を上書きコピーする。
#     - build/static/*, build/favicon.png
#     - nucllei/static/*       (即時反映用。中身は次回起動で build から巻き戻る)
#
# 依存: rsvg-convert, ImageMagick(magick)。
# マーク自体を編集したら先に vectorize.py で master SVG を作り直すこと。
set -euo pipefail

BRAND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$BRAND/.." && pwd)"
COLOR="$BRAND/favicon.svg"
GRAY="$BRAND/favicon-gray.svg"
DIST="$BRAND/dist"
rm -rf "$DIST"; mkdir -p "$DIST"

png() { rsvg-convert -w "$2" -h "$2" "$1" -o "$3"; }

# --- color raster ---
png "$COLOR" 500 "$DIST/favicon.png"
cp "$DIST/favicon.png" "$DIST/logo.png"
cp "$DIST/favicon.png" "$DIST/splash.png"
png "$COLOR" 96  "$DIST/favicon-96x96.png"
png "$COLOR" 180 "$DIST/apple-touch-icon.png"
png "$COLOR" 192 "$DIST/web-app-manifest-192x192.png"
png "$COLOR" 512 "$DIST/web-app-manifest-512x512.png"
cp "$COLOR" "$DIST/favicon.svg"

# --- favicon.ico (16/32/48) ---
png "$COLOR" 16 "$DIST/_i16.png"
png "$COLOR" 32 "$DIST/_i32.png"
png "$COLOR" 48 "$DIST/_i48.png"
magick "$DIST/_i16.png" "$DIST/_i32.png" "$DIST/_i48.png" "$DIST/favicon.ico"
rm -f "$DIST"/_i*.png

# --- gray(Nucllei のモデル既定アイコン: favicon-dark.png)---
png "$GRAY" 500 "$DIST/favicon-dark.png"

ASSETS=(favicon.svg favicon.png favicon-dark.png favicon-96x96.png favicon.ico
        apple-touch-icon.png logo.png splash.png
        web-app-manifest-192x192.png web-app-manifest-512x512.png)

place() {  # $1 = 宛先ディレクトリ(存在すれば配置)
  local dst="$1"
  [ -d "$dst" ] || return 0
  for a in "${ASSETS[@]}"; do cp "$DIST/$a" "$dst/$a"; done
  echo "  -> $dst"
}

echo "[SOURCE] リポジトリ追跡:"
place "$ROOT/static/static"
cp "$DIST/favicon.png" "$ROOT/static/favicon.png"; echo "  -> $ROOT/static/favicon.png"

echo "[RUNTIME] 起動時派生(gitignore・ローカル確認用):"
place "$ROOT/build/static"
[ -d "$ROOT/build" ] && cp "$DIST/favicon.png" "$ROOT/build/favicon.png" && echo "  -> $ROOT/build/favicon.png"
place "$ROOT/nucllei/static"

echo "done."
