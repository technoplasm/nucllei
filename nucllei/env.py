import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from nucllei.constants import ERROR_MESSAGES

####################################
# Load .env file
####################################

NUCLLEI_DIR = Path(__file__).parent  # the path containing this file
BASE_DIR = NUCLLEI_DIR.parent  # the repository / installation root

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(str(BASE_DIR / ".env")))
except ImportError:
    print("dotenv not installed, skipping...")

# device type embedding models - "cpu" (default), "cuda" (nvidia gpu required) or "mps" (apple silicon) - choosing this right can lead to better performance
USE_CUDA = os.environ.get("USE_CUDA_DOCKER", "false")

if USE_CUDA.lower() == "true":
    try:
        import torch

        assert torch.cuda.is_available(), "CUDA not available"
        DEVICE_TYPE = "cuda"
    except Exception as e:
        cuda_error = (
            "Error when testing CUDA but USE_CUDA_DOCKER is true. "
            f"Resetting USE_CUDA_DOCKER to false: {e}"
        )
        os.environ["USE_CUDA_DOCKER"] = "false"
        USE_CUDA = "false"
        DEVICE_TYPE = "cpu"
else:
    DEVICE_TYPE = "cpu"

try:
    import torch

    if torch.backends.mps.is_available() and torch.backends.mps.is_built():
        DEVICE_TYPE = "mps"
except Exception:
    pass

####################################
# LOGGING
####################################

GLOBAL_LOG_LEVEL = os.environ.get("GLOBAL_LOG_LEVEL", "").upper()
if GLOBAL_LOG_LEVEL in logging.getLevelNamesMapping():
    logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL, force=True)
else:
    GLOBAL_LOG_LEVEL = "INFO"

log = logging.getLogger(__name__)
log.info(f"GLOBAL_LOG_LEVEL: {GLOBAL_LOG_LEVEL}")

if "cuda_error" in locals():
    log.exception(cuda_error)
    del cuda_error

log_sources = [
    "AUDIO",
    "CONFIG",
    "DB",
    "MAIN",
    "MODELS",
    "OLLAMA",
    "OPENAI",
    "RAG",
    "WEBHOOK",
    "SOCKET",
    "OAUTH",
]

SRC_LOG_LEVELS = {}

for source in log_sources:
    log_env_var = source + "_LOG_LEVEL"
    SRC_LOG_LEVELS[source] = os.environ.get(log_env_var, "").upper()
    if SRC_LOG_LEVELS[source] not in logging.getLevelNamesMapping():
        SRC_LOG_LEVELS[source] = GLOBAL_LOG_LEVEL
    log.info(f"{log_env_var}: {SRC_LOG_LEVELS[source]}")

log.setLevel(SRC_LOG_LEVELS["CONFIG"])

# llens: 本体(fork)の製品名は Nucllei。WEBUI_NAME は配備側のブランド名
# (LLENS 等)で、旧 upstream の「非既定名に " (Open WebUI)" を付加」は撤去。
WEBUI_NAME = os.environ.get("WEBUI_NAME", "Nucllei")

# 配備側(ハーネス/エージェント設計)のバージョン(License タブの最上段で
# WEBUI_NAME に並べて表示。空なら非表示)。
# 本体(Nucllei)のバージョンはこちらではなく APP_VERSION → VERSION。
# 旧 llens 本番が焼いていた prod20260708_01 形式の deploy tag も APP_VERSION の系譜。
WEBUI_VERSION = os.environ.get("WEBUI_VERSION", "")

# 配備側の権利表記(License タブの最上段に表示。空なら非表示)。例:
# "The agent harness built on top of Nucllei, including its Tools, Skills, and
#  Functions, is copyright © PRISM-HU. All rights reserved."
WEBUI_COPYRIGHT = os.environ.get("WEBUI_COPYRIGHT", "")

# llens: 外部照会を避けるためローカル static を指す(旧 upstream は openwebui.com の
# 外部 URL だった)。使用箇所は webhook 通知(Teams payload)のアイコンのみ。
WEBUI_FAVICON_URL = "/static/favicon.png"

TRUSTED_SIGNATURE_KEY = os.environ.get("TRUSTED_SIGNATURE_KEY", "")

####################################
# ENV (dev,test,prod)
####################################

ENV = os.environ.get("ENV", "dev")

# llens: package=false(pip インストールしない)ため importlib.metadata 分岐は
# 常に失敗する死にコードだった — package.json 読みに一本化(VERSION の挙動は不変)。
try:
    PACKAGE_DATA = json.loads((BASE_DIR / "package.json").read_text())
except Exception:
    PACKAGE_DATA = {"version": "0.0.0"}

# llens(#30/#25): バージョンの真実源は git tag(例 v20260716-01)。
# 優先順: APP_VERSION env(docker build が焼き込む)→ git describe(ソース実行 = dev。
# tag を切るだけで次回起動から反映される)→ package.json の OWUI 基点バージョン(0.6.5)。


def _git_describe():
    try:
        return (
            subprocess.run(
                # バージョンとみなすタグは 2 形式のみ(作業タグ混入防止):
                # リリース = vYYYYMMDD-NN / プレ = pre-NN(日付形式に pre は混ぜない —
                # 日付バージョンは切った時点で本番なので「未来日付の pre」は成立しない)
                [
                    "git",
                    "describe",
                    "--tags",
                    "--always",
                    "--match",
                    "v[0-9]*",
                    "--match",
                    "pre-*",
                ],
                capture_output=True,
                text=True,
                cwd=BASE_DIR,
                timeout=3,
            ).stdout.strip()
            or None
        )
    except Exception:
        return None


VERSION = os.environ.get("APP_VERSION") or _git_describe() or PACKAGE_DATA["version"]


####################################
# ENABLE_FORWARD_USER_INFO_HEADERS
####################################

ENABLE_FORWARD_USER_INFO_HEADERS = (
    os.environ.get("ENABLE_FORWARD_USER_INFO_HEADERS", "False").lower() == "true"
)


####################################
# DATA/FRONTEND BUILD DIR
####################################

# 既定はリポジトリ/インストールルート直下の data/(Docker では /app/data)
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 配備先カスタマイズの単一置き場(#84)。規定名のファイルがあれば同梱既定より優先される:
#   favicon.png(タブ + アプリ内)/ brand.png(起動画面 + auth ロゴ)/
#   model.png(モデル既定アバター)/ pending.md(承認待ち画面の案内)/
#   favicon-192.png・favicon-512.png(PWA アイコン)
# 読み取りはリクエスト都度 — 差し替えは保存だけで反映、再起動不要。
# パスは固定(env で動かせる意味がない): リポジトリでは gitignore 済みの custom/、
# docker では /app/custom に host dir を read-only mount する。
CUSTOM_DIR = (BASE_DIR / "custom").resolve()

# PWA(manifest.json)の配信(#84)。機能としては維持するが、閉域デプロイでは
# インストール導線が邪魔になりがちなので既定 off。on のとき manifest は動的生成で
# name が WEBUI_NAME に追従する。off では {} を返す(インストール性なし)。
ENABLE_PWA = os.environ.get("ENABLE_PWA", "False").lower() == "true"

STATIC_DIR = Path(os.getenv("STATIC_DIR", NUCLLEI_DIR / "static"))

FONTS_DIR = Path(os.getenv("FONTS_DIR", NUCLLEI_DIR / "static" / "fonts"))

FRONTEND_BUILD_DIR = Path(os.getenv("FRONTEND_BUILD_DIR", BASE_DIR / "build")).resolve()

####################################
# Database
####################################

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR}/webui.db")

# Replace the postgres:// with postgresql://
if "postgres://" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

DATABASE_SCHEMA = os.environ.get("DATABASE_SCHEMA", None)

DATABASE_POOL_SIZE = os.environ.get("DATABASE_POOL_SIZE", 0)

if DATABASE_POOL_SIZE == "":
    DATABASE_POOL_SIZE = 0
else:
    try:
        DATABASE_POOL_SIZE = int(DATABASE_POOL_SIZE)
    except Exception:
        DATABASE_POOL_SIZE = 0

DATABASE_POOL_MAX_OVERFLOW = os.environ.get("DATABASE_POOL_MAX_OVERFLOW", 0)

if DATABASE_POOL_MAX_OVERFLOW == "":
    DATABASE_POOL_MAX_OVERFLOW = 0
else:
    try:
        DATABASE_POOL_MAX_OVERFLOW = int(DATABASE_POOL_MAX_OVERFLOW)
    except Exception:
        DATABASE_POOL_MAX_OVERFLOW = 0

DATABASE_POOL_TIMEOUT = os.environ.get("DATABASE_POOL_TIMEOUT", 30)

if DATABASE_POOL_TIMEOUT == "":
    DATABASE_POOL_TIMEOUT = 30
else:
    try:
        DATABASE_POOL_TIMEOUT = int(DATABASE_POOL_TIMEOUT)
    except Exception:
        DATABASE_POOL_TIMEOUT = 30

DATABASE_POOL_RECYCLE = os.environ.get("DATABASE_POOL_RECYCLE", 3600)

if DATABASE_POOL_RECYCLE == "":
    DATABASE_POOL_RECYCLE = 3600
else:
    try:
        DATABASE_POOL_RECYCLE = int(DATABASE_POOL_RECYCLE)
    except Exception:
        DATABASE_POOL_RECYCLE = 3600

RESET_CONFIG_ON_START = (
    os.environ.get("RESET_CONFIG_ON_START", "False").lower() == "true"
)

# llens: native function calling のツール呼び出し反復上限(issue #18)。
# -1 で無制限。上限到達時はチャット内にエラーを表示する。
CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS = os.environ.get(
    "CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS", "30"
)
try:
    CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS = int(CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS)
except ValueError:
    CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS = 30

# llens: tool 実行 1 回のシステム側タイムアウト(秒、-1 で無効)。
# ここが権威的な上限なので sandbox 等の実行側は自前の上限を持たなくてよい。
# タイムアウトは例外にせず tool result として モデルに返す(軌道修正を委ねる)。
CHAT_TOOL_EXECUTION_TIMEOUT = os.environ.get("CHAT_TOOL_EXECUTION_TIMEOUT", "300")
try:
    CHAT_TOOL_EXECUTION_TIMEOUT = int(CHAT_TOOL_EXECUTION_TIMEOUT)
except ValueError:
    CHAT_TOOL_EXECUTION_TIMEOUT = 300


####################################
# UVICORN WORKERS
####################################

# Number of uvicorn worker processes for handling requests
UVICORN_WORKERS = os.environ.get("UVICORN_WORKERS", "1")
try:
    UVICORN_WORKERS = int(UVICORN_WORKERS)
    if UVICORN_WORKERS < 1:
        UVICORN_WORKERS = 1
except ValueError:
    UVICORN_WORKERS = 1
    log.info(f"Invalid UVICORN_WORKERS value, defaulting to {UVICORN_WORKERS}")

####################################
# WEBUI_AUTH (Required for security)
####################################

WEBUI_AUTH = os.environ.get("WEBUI_AUTH", "True").lower() == "true"
WEBUI_AUTH_TRUSTED_EMAIL_HEADER = os.environ.get(
    "WEBUI_AUTH_TRUSTED_EMAIL_HEADER", None
)
WEBUI_AUTH_TRUSTED_NAME_HEADER = os.environ.get("WEBUI_AUTH_TRUSTED_NAME_HEADER", None)

BYPASS_MODEL_ACCESS_CONTROL = (
    os.environ.get("BYPASS_MODEL_ACCESS_CONTROL", "False").lower() == "true"
)

####################################
# WEBUI_SECRET_KEY
####################################

WEBUI_SECRET_KEY = os.environ.get(
    "WEBUI_SECRET_KEY",
    os.environ.get(
        "WEBUI_JWT_SECRET_KEY", "t0p-s3cr3t"
    ),  # DEPRECATED: remove at next major version
)

WEBUI_SESSION_COOKIE_SAME_SITE = os.environ.get("WEBUI_SESSION_COOKIE_SAME_SITE", "lax")

WEBUI_SESSION_COOKIE_SECURE = (
    os.environ.get("WEBUI_SESSION_COOKIE_SECURE", "false").lower() == "true"
)

WEBUI_AUTH_COOKIE_SAME_SITE = os.environ.get(
    "WEBUI_AUTH_COOKIE_SAME_SITE", WEBUI_SESSION_COOKIE_SAME_SITE
)

WEBUI_AUTH_COOKIE_SECURE = (
    os.environ.get(
        "WEBUI_AUTH_COOKIE_SECURE",
        os.environ.get("WEBUI_SESSION_COOKIE_SECURE", "false"),
    ).lower()
    == "true"
)

if WEBUI_AUTH and WEBUI_SECRET_KEY == "":
    raise ValueError(ERROR_MESSAGES.ENV_VAR_NOT_FOUND)

AIOHTTP_CLIENT_TIMEOUT = os.environ.get("AIOHTTP_CLIENT_TIMEOUT", "")

if AIOHTTP_CLIENT_TIMEOUT == "":
    AIOHTTP_CLIENT_TIMEOUT = None
else:
    try:
        AIOHTTP_CLIENT_TIMEOUT = int(AIOHTTP_CLIENT_TIMEOUT)
    except Exception:
        AIOHTTP_CLIENT_TIMEOUT = 300

# llens: chat completion 経路の無応答検出。total(上の AIOHTTP_CLIENT_TIMEOUT)は
# 既定 None のまま長い生成を許容し、connect / sock_read で「死んだ接続」を必ず
# エラー化する。sock_read は「無音区間」の上限 — トークンが出続ける限り切れない。
# ollama の初回モデルロード(応答ゼロの時間)を許容しつつ、5 分を上限とする。
AIOHTTP_CLIENT_TIMEOUT_CONNECT = os.environ.get("AIOHTTP_CLIENT_TIMEOUT_CONNECT", "10")

try:
    AIOHTTP_CLIENT_TIMEOUT_CONNECT = int(AIOHTTP_CLIENT_TIMEOUT_CONNECT)
except Exception:
    AIOHTTP_CLIENT_TIMEOUT_CONNECT = 10

AIOHTTP_CLIENT_TIMEOUT_SOCK_READ = os.environ.get(
    "AIOHTTP_CLIENT_TIMEOUT_SOCK_READ", "300"
)

try:
    AIOHTTP_CLIENT_TIMEOUT_SOCK_READ = int(AIOHTTP_CLIENT_TIMEOUT_SOCK_READ)
except Exception:
    AIOHTTP_CLIENT_TIMEOUT_SOCK_READ = 300

AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = os.environ.get(
    "AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST",
    os.environ.get("AIOHTTP_CLIENT_TIMEOUT_OPENAI_MODEL_LIST", "10"),
)

if AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST == "":
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = None
else:
    try:
        AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = int(AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    except Exception:
        AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = 10


AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = os.environ.get(
    "AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA", "10"
)

if AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA == "":
    AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = None
else:
    try:
        AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = int(
            AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA
        )
    except Exception:
        AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = 10

####################################
# OFFLINE_MODE
####################################

OFFLINE_MODE = os.environ.get("OFFLINE_MODE", "false").lower() == "true"

if OFFLINE_MODE:
    os.environ["HF_HUB_OFFLINE"] = "1"

####################################
# AUDIT LOGGING
####################################
# Where to store log file
AUDIT_LOGS_FILE_PATH = f"{DATA_DIR}/audit.log"
# Maximum size of a file before rotating into a new log file
AUDIT_LOG_FILE_ROTATION_SIZE = os.getenv("AUDIT_LOG_FILE_ROTATION_SIZE", "10MB")
# METADATA | REQUEST | REQUEST_RESPONSE
AUDIT_LOG_LEVEL = os.getenv("AUDIT_LOG_LEVEL", "NONE").upper()
try:
    MAX_BODY_LOG_SIZE = int(os.environ.get("MAX_BODY_LOG_SIZE") or 2048)
except ValueError:
    MAX_BODY_LOG_SIZE = 2048

# Comma separated list for urls to exclude from audit
AUDIT_EXCLUDED_PATHS = os.getenv("AUDIT_EXCLUDED_PATHS", "/chats,/chat,/folders").split(
    ","
)
AUDIT_EXCLUDED_PATHS = [path.strip() for path in AUDIT_EXCLUDED_PATHS]
AUDIT_EXCLUDED_PATHS = [path.lstrip("/") for path in AUDIT_EXCLUDED_PATHS]

####################################
# OPENTELEMETRY
####################################

ENABLE_OTEL = os.environ.get("ENABLE_OTEL", "False").lower() == "true"
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
)
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "nucllei")
OTEL_RESOURCE_ATTRIBUTES = os.environ.get(
    "OTEL_RESOURCE_ATTRIBUTES", ""
)  # e.g. key1=val1,key2=val2
OTEL_TRACES_SAMPLER = os.environ.get(
    "OTEL_TRACES_SAMPLER", "parentbased_always_on"
).lower()

# llens(#84): 旧 EXTERNAL_PWA_MANIFEST_URL(manifest を外部 URL から proxy)は
# 消費者のいない upstream 残骸のため撤去。PWA は ENABLE_PWA 一本(DATA_DIR 節参照)。
