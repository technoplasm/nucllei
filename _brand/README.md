# _brand — Nucllei ブランドマーク素材

Nucllei のマーク(H&E 染色モチーフ: エオジンのピンク円 + ヘマトキシリンの青紫核)の
ソース素材と生成スクリプト。refs #72。

| ファイル | 役割 |
| --- | --- |
| `favicon_base.png` | 元画像(2048²、生成 AI 出力)。右下の Gemini 透かしは vectorize.py で除外 |
| `favicon.svg` | カラー版 master(外 `#F76687` / 中 `#39095B`、背景透過) |
| `favicon-gray.svg` | グレー版 master(輝度換算 外 `#959595` / 中 `#212121`)。favicon-dark = モデル既定アイコン用 |
| `vectorize.py` | `favicon_base.png` → 2 つの master SVG(要 potrace。cv2/numpy/pillow は本体依存) |
| `generate.sh` | master SVG → favicon 資産一式を生成・配置(要 rsvg-convert / ImageMagick) |

- ソースオブトゥルースは master SVG。画像を替えたら 2 つのスクリプトを順に流す(詳細は各スクリプト冒頭)。
- **コミットするのは `static/static/*` と `static/favicon.png` の生成物だけ**。
  `nucllei/static/*` は起動時に `build/static` から上書きされる派生物なので追跡しない
  (`nucllei/config.py:650`、.gitignore 参照)。
