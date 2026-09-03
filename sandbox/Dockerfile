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
