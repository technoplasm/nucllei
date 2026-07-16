"""favicon_base.png (H&E マーク) → ベクター化して 2 つの master SVG を出力。
   color:  favicon.svg       (外=#F76687 エオジン / 中=#39095B ヘマトキシリン)
   gray :  favicon-gray.svg  (輝度換算 外=#959595 / 中=#212121 — favicon-dark 用)

   依存: cv2(opencv-python-headless)/ numpy / pillow は本体の依存に含まれるので
         プロジェクトの .venv でそのまま動く。加えて potrace(CLI)が要る。
   マークの素材を差し替えたときだけ実行する。出力の master SVG をコミットし、
   raster 生成は generate.sh が担う。

   使い方(リポジトリルート or _brand どちらからでも可):
       .venv/bin/python _brand/vectorize.py
"""
import subprocess
import re
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE / "favicon_base.png"
DIST = HERE / "dist"
DIST.mkdir(exist_ok=True)

PINK, PURPLE = "#F76687", "#39095B"
GRAY_OUT, GRAY_IN = "#959595", "#212121"  # Rec.601 luma of PINK / PURPLE
PAD = 0.08  # マーク周囲の余白(各辺、frame 比)

bgr = cv2.imread(str(SRC), cv2.IMREAD_COLOR)
im = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)

# 参照色への最近傍で分類(単純しきい値だと灰背景ノイズを誤検出するため)
refs = {"bg": (64, 64, 64), "pink": (247, 102, 135),
        "purple": (57, 15, 91), "sparkle": (200, 200, 200)}  # sparkle=Gemini 透かし→除外
names = list(refs)
dist = np.stack([((im - np.array(refs[n], np.float32)) ** 2).sum(-1) for n in names], -1)
label = np.array(names)[dist.argmin(-1)]
pink = (label == "pink").astype(np.uint8) * 255
purple = (label == "purple").astype(np.uint8) * 255

K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def largest(m):
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return m
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(lbl == big, 255, 0).astype(np.uint8)


def fill_holes(m):
    ff = m.copy()
    mask = np.zeros((m.shape[0] + 2, m.shape[1] + 2), np.uint8)
    cv2.floodFill(ff, mask, (0, 0), 255)  # 角(=背景)から外側を塗る
    return m | cv2.bitwise_not(ff)         # 外側に届かなかった穴を埋める


def clean(m):
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, K, iterations=6)
    m = largest(m)
    m = fill_holes(m)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, K, iterations=6)
    return largest(m)


outer = clean(pink | purple)   # 塗り全体(ピンク)
inner = clean(purple)          # 中心核(紫)

# アイコン余白を詰めた正方形 viewBox を bbox から算出
ys, xs = np.where(outer > 0)
cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
frame = round(max(xs.max() - xs.min(), ys.max() - ys.min()) / (1 - 2 * PAD))
x0, y0 = round(cx - frame / 2), round(cy - frame / 2)
vb = f"{x0} {y0} {frame} {frame}"


def trace(mask, name):
    pbm = DIST / f"{name}.pbm"
    svg = DIST / f"{name}.svg"
    # potrace: 0(黒)= トレース対象
    cv2.imwrite(str(pbm), np.where(mask > 0, 0, 255).astype(np.uint8))
    subprocess.run(["potrace", str(pbm), "-s", "-o", str(svg),
                    "--turdsize", "200", "--alphamax", "1", "--opttolerance", "0.2"], check=True)
    return re.search(r'<path d="(.*?)"', svg.read_text(), re.S).group(1)


d_outer, d_inner = trace(outer, "outer"), trace(inner, "inner")


def write(fn, c_out, c_in):
    (HERE / fn).write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" fill="none">\n'
        f'  <g transform="translate(0,2048) scale(0.1,-0.1)" stroke="none">\n'
        f'    <path fill="{c_out}" d="{d_outer}"/>\n'
        f'    <path fill="{c_in}" d="{d_inner}"/>\n'
        f"  </g>\n</svg>\n")
    print("wrote", fn)


write("favicon.svg", PINK, PURPLE)
write("favicon-gray.svg", GRAY_OUT, GRAY_IN)
print("viewBox:", vb)
