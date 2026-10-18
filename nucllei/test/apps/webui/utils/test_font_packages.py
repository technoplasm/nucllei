"""フォントの一覧が sandbox と本体で取り残されないことの回帰テスト(2026-10-18)。

送信直前に SVG (resvg) と PDF のページ (pdfium) を画像にするとき、本体の image に和文の
フォントが無く、和文が 1 画素も描かれなかった(llens の staging 実測)。フォントの apt
パッケージは `sandbox/fonts.apt.txt` 1 か所にまとめ、sandbox/Dockerfile と repo root の
Dockerfile の両方がそこを読む。ここでは Dockerfile を読んで参照を確かめるだけ(docker は使わない)。

描画の確認は、IPA フォントがある環境(本体の image)でだけ行う — ホストの dev に無くても落とさない。
"""

import asyncio
import io
import re
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[5]
FONT_LIST = REPO_ROOT / "sandbox" / "fonts.apt.txt"


def _packages() -> list[str]:
    lines = FONT_LIST.read_text(encoding="utf-8").splitlines()
    return [p for line in lines for p in line.split("#", 1)[0].split()]


def _body(dockerfile: Path) -> str:
    """Dockerfile のコメント行を除き、行継続 (\\) を 1 行につないだ本文。"""
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    return re.sub(r"\\\n", " ", text)


def _apt_install_args(body: str) -> list[str]:
    """`apt-get install` に直に並べたパッケージ名 (次の && / | / 行末まで、- 始まりは除く)。"""
    return [
        token
        for args in re.findall(r"apt-get install([^&|\n]*)", body)
        for token in args.split()
        if not token.startswith("-")
    ]


def test_font_list_has_the_japanese_fonts():
    packages = _packages()
    assert len(packages) == len(set(packages)), packages
    for package in ("fonts-ipafont", "fonts-noto-cjk", "fontconfig"):
        assert package in packages


@pytest.mark.parametrize(
    "dockerfile, copy_src",
    [
        ("Dockerfile", "sandbox/fonts.apt.txt"),  # build context = repo root
        ("sandbox/Dockerfile", "fonts.apt.txt"),  # build context = sandbox/
    ],
)
def test_both_dockerfiles_install_fonts_from_the_shared_list(dockerfile, copy_src):
    body = _body(REPO_ROOT / dockerfile)
    assert re.search(rf"^COPY {re.escape(copy_src)} /tmp/fonts\.apt\.txt$", body, re.M)
    assert re.search(r"/tmp/fonts\.apt\.txt \| xargs apt-get install", body)
    # 一覧のパッケージを apt-get install に直書きしない (片方だけ増えるずれの元)
    direct = set(_apt_install_args(body)) & set(_packages())
    assert (
        not direct
    ), f"{dockerfile} に直書き: {sorted(direct)} — sandbox/fonts.apt.txt に寄せる"


def test_font_list_is_not_excluded_from_either_build_context():
    for ignore in (
        REPO_ROOT / ".dockerignore",
        REPO_ROOT / "sandbox" / ".dockerignore",
    ):
        patterns = [
            line.strip()
            for line in ignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        for pattern in patterns:
            assert pattern.rstrip("/") not in ("sandbox", "fonts.apt.txt", "*.txt")


def _has_ipa_fonts() -> bool:
    roots = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    names = {"ipagp.ttf", "ipamp.ttf", "ipag.ttf"}
    return all(any(any(r.rglob(n)) for r in roots if r.is_dir()) for n in names)


@pytest.mark.skipif(
    not _has_ipa_fonts(), reason="IPA フォントが無い環境(ホストの dev 等)"
)
@pytest.mark.parametrize("family", [None, "serif", "sans-serif", "monospace", "Arial"])
def test_svg_text_is_drawn_with_ipa(family):
    """和文・欧文のラベルが描かれる(resvg の既定の書体は image に無い Times New Roman 等)。"""
    from nucllei.utils.image_refs import convert_image_to_png

    attr = f'font-family="{family}"' if family else ""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">'
        f'<text x="10" y="70" font-size="40" {attr}>日本語ラベル</text>'
        f'<text x="10" y="170" font-size="40" {attr}>English label</text></svg>'
    ).encode()
    png = asyncio.run(convert_image_to_png(svg, "image/svg+xml"))
    image = Image.open(io.BytesIO(png)).convert("L")
    width, height = image.size
    top = image.crop((0, 0, width, height // 2)).histogram()
    bottom = image.crop((0, height // 2, width, height)).histogram()
    assert sum(top[:128]) > 500 and sum(bottom[:128]) > 500
