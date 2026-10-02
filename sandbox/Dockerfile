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
