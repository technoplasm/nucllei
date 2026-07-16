# syntax=docker/dockerfile:1
# llens(#34): Docker が唯一の配布形態。uv sync --frozen(uv.lock 忠実)+ CLI serve 起動の最小構成。
# 旧 upstream の USE_CUDA/USE_OLLAMA(イメージ内 ollama)/RAG モデル preload/
# HuggingFace Space 分岐/requirements.txt/start.sh は全撤去。

######## frontend build ########
FROM node:22-alpine3.20 AS build
# バージョン焼き込み(真実源は git tag — リリース CI が ref 名を渡す。#25)
ARG APP_VERSION

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile

COPY . .
ENV APP_VERSION=${APP_VERSION}
RUN pnpm build

######## backend + runtime ########
FROM python:3.11-slim-bookworm AS base

ENV ENV=prod \
    PORT=8080 \
    DOCKER=true \
    DATA_DIR=/app/data \
    WHISPER_MODEL_DIR=/app/data/cache/whisper/models \
    HF_HOME=/app/data/cache/hf

WORKDIR /app

# curl/jq = HEALTHCHECK、ffmpeg = 音声変換(STT/TTS)、pandoc = pypandoc(文書変換)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl jq ffmpeg pandoc && \
    rm -rf /var/lib/apt/lists/*

# python 依存は uv.lock に忠実に /app/.venv へ(--extra stt = 内蔵 faster-whisper、#53)
RUN pip3 install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra stt --no-cache
ENV PATH="/app/.venv/bin:$PATH"

# whisper モデルの焼き込み(air-gapped 先は初回 DL 不可 — 必要なら build 時に指定):
#   docker build --build-arg PRELOAD_WHISPER_MODEL=small ...
ARG PRELOAD_WHISPER_MODEL=""
RUN if [ -n "$PRELOAD_WHISPER_MODEL" ]; then \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['PRELOAD_WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
    fi

COPY --from=build /app/build /app/build
COPY --from=build /app/package.json /app/package.json
COPY nucllei /app/nucllei

RUN mkdir -p /app/data

EXPOSE 8080

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-8080}/health | jq -ne 'input.status == true' || exit 1

ARG APP_VERSION
ENV APP_VERSION=${APP_VERSION}

# serve = 秘密鍵の生成/永続(DATA_DIR)+ uvicorn 起動(workers は UVICORN_WORKERS env)
CMD exec python -c 'from nucllei import app; app()' serve --port "${PORT:-8080}"
