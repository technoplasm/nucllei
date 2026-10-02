# nucllei sandbox イメージ (設計・API は sandbox/README.md)
# CI が ghcr.io/technoplasm/nucllei-sandbox として publish する
# (.github/workflows/docker-build.yaml の build-and-push-sandbox job)。build context は sandbox/ のみ。
#
# - nsjail: apt パッケージが無いためソースビルド (builder ステージにキャッシュされる)
# - /opt/venv: jail 内でユーザーコードが使う Python 環境
# - /opt/app: サンドボックスサービス (FastAPI) 用の venv (ユーザー環境と分離)
#
# TODO(閉域搬入前): venv パッケージと apt ツールのリスト確定 + version pin

FROM debian:bookworm-slim AS nsjail-build
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git ca-certificates build-essential pkg-config bison flex \
        libprotobuf-dev protobuf-compiler libnl-route-3-dev \
 && rm -rf /var/lib/apt/lists/*
ARG NSJAIL_VERSION=3.4
RUN git clone --depth 1 --branch ${NSJAIL_VERSION} \
        https://github.com/google/nsjail /nsjail \
 && make -C /nsjail -j"$(nproc)"

FROM python:3.12-slim-bookworm

# nsjail ランタイム依存 + jail 内シェルツール + 日本語フォント
#   + e2fsprogs/mount/util-linux: ユーザー単位の quota 用 loopback ext4 の作成/マウント
#   + libpango*: weasyprint (HTML → PDF) のテキスト整形バックエンド
#   + libreoffice-*-nogui: レガシー Office (.doc/.xls/.ppt) を含む相互変換 (soffice --headless)。
#     GUI 無し・Java 無しの最小構成。日本語は上の fonts-noto-cjk で出る
#   + antiword / catdoc (catdoc・xls2csv・catppt): .doc/.xls/.ppt の軽量テキスト抽出
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libprotobuf32 libnl-route-3-200 \
        jq ripgrep pandoc \
        unzip zip nkf file poppler-utils \
        ffmpeg libsndfile1 \
        libpango-1.0-0 libpangoft2-1.0-0 \
        fonts-noto-cjk fontconfig \
        e2fsprogs mount util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f  # フォントキャッシュを焼いておく (jail からは /var 非 bind のため)

# Office 系は別層 (重いので、上の層を変えずに足し引きできるように)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        antiword catdoc \
 && rm -rf /var/lib/apt/lists/*

# レガシーデータ層 (病院に残る古い形式を開く軽い道具。Office 層の後ろに別層で足し引きする)
#   + xxd / bsdextrautils (hexdump): 未知のバイナリの先頭を覗く (モデルが xxd を使いにいく)
#   + mdbtools: Access .mdb / .accdb (mdb-tables / mdb-export / mdb-schema)
#   + unrtf: RTF → テキスト / HTML
#   + p7zip-full / unar / lhasa / cabextract: 7z・rar 等 / 汎用展開 (lsar) / LZH / .cab
#   + sqlite3 / libxml2-utils (xmllint) / dos2unix / qpdf (PDF の復号・分割・修復)
#   + fonts-ipafont: 「ＭＳ 明朝 / ＭＳ ゴシック」指定の古い Word を soffice で PDF 化するとき、
#     字幅の合う代替に落とす (Noto CJK だと字幅がずれて改行位置が原本と変わる)。
#     代替の向き先は下の 64-nucllei-fonts.conf。fc-cache は最初の層と同じ理由で焼いておく。
#     IPA を入れると Debian の 65-nonlatin.conf が和文の既定 (sans-serif / serif の lang=ja) を
#     IPA に倒すので、同じ conf で Noto CJK を先に戻す (64 < 65 なので Noto が前に入る。
#     欧文は 60-latin の DejaVu が更に前なので変わらない)。IPA に寄せるのは MS 指定だけ
# 入れないもの (重い。別判断): ghostscript / imagemagick / tesseract
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        xxd bsdextrautils \
        mdbtools unrtf \
        p7zip-full unar lhasa cabextract \
        sqlite3 libxml2-utils dos2unix qpdf \
        fonts-ipafont \
 && rm -rf /var/lib/apt/lists/* \
 && printf '%s\n' \
        '<?xml version="1.0"?>' \
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">' \
        '<!-- nucllei sandbox: 和文の既定は Noto CJK のまま、MS 明朝 / ゴシック指定 (半角・全角表記とも) だけ IPA へ -->' \
        '<fontconfig>' \
        '  <alias><family>sans-serif</family><prefer><family>Noto Sans CJK JP</family></prefer></alias>' \
        '  <alias><family>serif</family><prefer><family>Noto Serif CJK JP</family></prefer></alias>' \
        '  <alias><family>monospace</family><prefer><family>Noto Sans Mono CJK JP</family></prefer></alias>' \
        '  <alias binding="same"><family>ＭＳ 明朝</family><prefer><family>IPAMincho</family></prefer></alias>' \
        '  <alias binding="same"><family>MS 明朝</family><prefer><family>IPAMincho</family></prefer></alias>' \
        '  <alias binding="same"><family>MS Mincho</family><prefer><family>IPAMincho</family></prefer></alias>' \
        '  <alias binding="same"><family>ＭＳ Ｐ明朝</family><prefer><family>IPAPMincho</family></prefer></alias>' \
        '  <alias binding="same"><family>MS P明朝</family><prefer><family>IPAPMincho</family></prefer></alias>' \
        '  <alias binding="same"><family>MS PMincho</family><prefer><family>IPAPMincho</family></prefer></alias>' \
        '  <alias binding="same"><family>ＭＳ ゴシック</family><prefer><family>IPAGothic</family></prefer></alias>' \
        '  <alias binding="same"><family>MS ゴシック</family><prefer><family>IPAGothic</family></prefer></alias>' \
        '  <alias binding="same"><family>MS Gothic</family><prefer><family>IPAGothic</family></prefer></alias>' \
        '  <alias binding="same"><family>ＭＳ Ｐゴシック</family><prefer><family>IPAPGothic</family></prefer></alias>' \
        '  <alias binding="same"><family>MS Pゴシック</family><prefer><family>IPAPGothic</family></prefer></alias>' \
        '  <alias binding="same"><family>MS PGothic</family><prefer><family>IPAPGothic</family></prefer></alias>' \
        '  <alias binding="same"><family>MS UI Gothic</family><prefer><family>IPAPGothic</family></prefer></alias>' \
        '</fontconfig>' \
        > /etc/fonts/conf.d/64-nucllei-fonts.conf \
 && fc-cache -f

# OCR・画像・研究データ層 (ken 2026-10-02「軽くてよく使うものは入れておく」。レガシー層と別層で増分を見る)
#   + tesseract-ocr (+ jpn / jpn-vert / eng): スキャン束の一括テキスト化 (venv の ocrmypdf / pytesseract が呼ぶ)。
#     モデルの目 (画像入力) とは役割が違う — 何十ページを機械的に検索可能 PDF / テキストにする用途
#   + ghostscript: ocrmypdf の前提。スキャン PDF の圧縮 (gs -sDEVICE=pdfwrite -dPDFSETTINGS=/ebook) にも
#   + libtiff-tools (tiffsplit / tiffinfo) / zbar-tools (zbarimg) / libimage-exiftool-perl (exiftool)
#     + libarchive-zip-perl: exiftool が docx/xlsx (zip) のメタを読むのに要る (Recommends なので明示)
#   + graphviz (dot) / tree / wdiff / mediainfo
#   + git: workspace 内の版管理と git diff --word-diff。jail のユーザー (nobody) には名前もメールも
#     無く commit が落ちるので、/etc/gitconfig (system) で既定の identity を与える
#     (HOME=/workspace の ~/.gitconfig を作らせない)。利用者が -c / --global で上書きすればそちらが勝つ
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert tesseract-ocr-eng \
        ghostscript \
        libtiff-tools zbar-tools libimage-exiftool-perl libarchive-zip-perl graphviz \
        tree wdiff mediainfo git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system user.name "sandbox" \
 && git config --system user.email "sandbox@localhost" \
 && git config --system init.defaultBranch main \
 && git config --system core.quotePath false \
 && fc-cache -f

# soffice / libreoffice の wrapper (jail の PATH では /usr/local/bin が /usr/bin より前)。
# LibreOffice は起動のたびにユーザープロファイルを書く。既定の置き場は $HOME (jail では
# 永続 workspace = quota 対象) なので、揮発の /tmp へ向ける。呼び出し側が
# -env:UserInstallation を自分で渡したときはそれを尊重する
COPY soffice-wrapper.sh /usr/local/bin/soffice
RUN chmod 755 /usr/local/bin/soffice \
 && ln -s soffice /usr/local/bin/libreoffice

COPY --from=nsjail-build /nsjail/nsjail /usr/local/bin/nsjail

# jail 内ユーザーコード用 venv。パッケージ一覧は requirements.txt が単一情報源
# (搬入前に pin すること。requirements.txt 冒頭コメント参照)
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# jail 内 matplotlib の既定 rc (日本語フォント)。JAIL_ENV の MATPLOTLIBRC が指す
COPY matplotlibrc /opt/venv/matplotlibrc

# サービス用 venv (ユーザーコードからは bind しない)
RUN python -m venv /opt/app \
 && /opt/app/bin/pip install --no-cache-dir \
        fastapi==0.115.12 \
        uvicorn==0.34.0

COPY app /opt/app-src

EXPOSE 8000

# バージョン焼き込み(真実源は git tag — CI が ref 名を渡す。本体 Dockerfile と同じ流儀)。
# **ENV で持つ必要がある**: 閉域で image の版を表示する llens の scripts/depot/restore.sh は、
# 自 repo 産 (own) の image だけ OCI ラベル org.opencontainers.image.version を読み、
# それ以外 (ext) は **ENV APP_VERSION しか見ない** — ラベル側には他人の版体系
# (ubuntu なら 22.04 等) が入っていて自前の版として読むと嘘になるため。
# nucllei-sandbox は nucllei repo から llens へ配る = 向こうから見て ext なので、
# これが無いと閉域の版一覧に `-` としか出ない (どの版の sandbox が載っているか読めない)。
# metadata-action のラベルも付くが、それだけでは足りないので消さないこと。
# 位置が末尾なのは、タグごとに変わる値で上の重い層 (nsjail ビルド・pip) のキャッシュを
# 落とさないため。
ARG APP_VERSION
ENV APP_VERSION=${APP_VERSION}
CMD ["/opt/app/bin/uvicorn", "--app-dir", "/opt/app-src", "main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
