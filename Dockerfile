# syntax=docker/dockerfile:1
# llens(#34): Docker が唯一の配布形態。uv sync --frozen(uv.lock 忠実)+ CLI serve 起動の最小構成。
# 旧 upstream の USE_CUDA/USE_OLLAMA(イメージ内 ollama)/RAG モデル preload/
# HuggingFace Space 分岐/requirements.txt/start.sh は全撤去。

######## frontend build ########
# --platform=$BUILDPLATFORM: ビルド成果物(/app/build)はアーキ非依存なので、
# multi-arch ビルドでもフロントはランナーのネイティブ側で 1 回だけ走らせる
# (QEMU 下で pnpm build を回すと時間が跳ね上がり OOM リスクもある)
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build
# バージョン焼き込み(真実源は git tag — リリース CI が ref 名を渡す。#25)
ARG APP_VERSION

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile

COPY . .
ENV APP_VERSION=${APP_VERSION}
# 依存クレジット(設定 > About の License タブ)の frontend 分をここで採る。
# **node_modules があるのはこのステージだけ**なので、生の pnpm 出力を持ち越して
# backend ステージで合流させる。コミット済みの生成物を配るのをやめたのは、
# 依存を触るたびに腐って「正しく開示している」という誤った主張になっていたため
RUN pnpm licenses list --prod --json > /app/licenses.frontend.json
# multi-arch ビルドでは arm64 ステージの QEMU 実行が並走しメモリを圧迫する。
# V8 の既定 heap 上限は環境依存でギリギリ(pre-11 で OOM 実績)— 明示して固定する
RUN NODE_OPTIONS=--max-old-space-size=4096 pnpm build

######## backend + runtime ########
FROM python:3.11-slim-bookworm AS base

ENV ENV=prod \
    PORT=8080 \
    DATA_DIR=/app/data \
    WHISPER_MODEL_DIR=/app/data/cache/whisper/models \
    HF_HOME=/app/data/cache/hf

WORKDIR /app

# curl/jq = HEALTHCHECK、ffmpeg = 音声変換(STT/TTS)、pandoc = pypandoc(文書変換)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl jq ffmpeg pandoc && \
    rm -rf /var/lib/apt/lists/*

# python 依存は uv.lock に忠実に /app/.venv へ(--extra stt = 内蔵 faster-whisper、#53)。
# この層はソース未 COPY なので --no-install-project(依存だけ入れてレイヤーキャッシュを最大化)
RUN pip3 install --no-cache-dir uv
# LICENSE は hatchling の project build(下の editable install)が要求する
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra stt --no-cache --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

# whisper モデルの焼き込み(air-gapped 先は初回 DL 不可 — 必要なら build 時に指定):
#   docker build --build-arg PRELOAD_WHISPER_MODEL=small ...
ARG PRELOAD_WHISPER_MODEL=""
RUN if [ -n "$PRELOAD_WHISPER_MODEL" ]; then \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['PRELOAD_WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
    fi

COPY --from=build /app/build /app/build
COPY --from=build /app/package.json /app/package.json
COPY --from=build /app/licenses.frontend.json /tmp/licenses.frontend.json
COPY nucllei /app/nucllei
COPY scripts/generate_licenses.py /app/scripts/generate_licenses.py
# nucllei 本体を editable install(console script `nucllei` の生成。依存は上の層で解決済みなので一瞬)
RUN uv sync --frozen --no-dev --extra stt --no-cache

# 依存クレジットを **この image の実際の中身から** 作る。frontend は上のステージで採った
# 一覧、backend はいま入れた venv の importlib.metadata。リポジトリにコミットした JSON を
# 配ると依存を触るたびに腐るので、build 時に作って /app/build へ置く
RUN uv run --no-sync python scripts/generate_licenses.py \
      --frontend-json /tmp/licenses.frontend.json \
      --out /app/build/licenses.json \
    && rm -f /tmp/licenses.frontend.json

RUN mkdir -p /app/data

EXPOSE 8080

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-8080}/health | jq -ne 'input.status == true' || exit 1

ARG APP_VERSION
ENV APP_VERSION=${APP_VERSION}

# serve = 秘密鍵の生成/永続(DATA_DIR)+ uvicorn 起動(workers は UVICORN_WORKERS env)
CMD exec nucllei serve --port "${PORT:-8080}"
