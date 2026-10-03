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
                # バージョンとみなすタグはリリース `vYYYYMMDD-NN` の 1 形式のみ
                # (作業タグの混入防止)。--always があるので、リリースタグが
                # 1 つも無い repo でも short sha に落ちて解決自体は壊れない。
                ["git", "describe", "--tags", "--always", "--match", "v[0-9]*"],
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

# `/ext/**` で配信する参照資料の置き場 (read-only、配備者が置く)。DATA_DIR と同じ作法: 既定は
# インストールルート直下の ext/、Docker image は Dockerfile で `EXT_DIR=/ext` にする (compose には
# 書かない)。URL `/ext/X` ⇔ `EXT_DIR/X` で、sandbox も同じ物を jail の `/ext` に bind する規約なので
# コンテナ内のパス・sandbox のパス・URL が同じ文字列になる (ken 決定 2026-10-03 夜)。
# DATA_DIR の中に置かない理由: DATA_DIR は DB・アップロード・秘密鍵など書き換わる状態の置き場で
# volume ごとバックアップする対象。読み取り専用の配信物を同じ木に混ぜない。
EXT_DIR = Path(os.getenv("EXT_DIR", BASE_DIR / "ext")).resolve()
EXT_DIR.mkdir(parents=True, exist_ok=True)

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

# llens(#107): サブエージェント(run_subagent)1 ターンのタイムアウト(秒、-1 で無効)。
# 子ターンはモデル呼び出しと tool 実行の反復を丸ごと包むため、通常 tool の
# CHAT_TOOL_EXECUTION_TIMEOUT より長い既定を持つ(tool エントリの per-tool
# timeout キーとして execute_tool_calls に適用される)。
SUBAGENT_TURN_TIMEOUT = os.environ.get("SUBAGENT_TURN_TIMEOUT", "1800")
try:
    SUBAGENT_TURN_TIMEOUT = int(SUBAGENT_TURN_TIMEOUT)
except ValueError:
    SUBAGENT_TURN_TIMEOUT = 1800

# llens(#107): agent session の入れ子深さ上限(user chat = depth 0)。
# 親 depth+1 が上限を超えるチャットでは run_subagent を合成しない(実行時にも再チェック)。
SUBAGENT_MAX_DEPTH = os.environ.get("SUBAGENT_MAX_DEPTH", "2")
try:
    SUBAGENT_MAX_DEPTH = int(SUBAGENT_MAX_DEPTH)
except ValueError:
    SUBAGENT_MAX_DEPTH = 2

# llens(#107): プロセス内の subagent 同時実行数上限。超過時は待たずにエラーを
# tool result で返す(モデルに再試行を委ねる)。
SUBAGENT_MAX_CONCURRENT = os.environ.get("SUBAGENT_MAX_CONCURRENT", "4")
try:
    SUBAGENT_MAX_CONCURRENT = int(SUBAGENT_MAX_CONCURRENT)
except ValueError:
    SUBAGENT_MAX_CONCURRENT = 4

# llens(#110): 非同期 tool 実行(execution)1 本のタイムアウト(秒、-1 で無効)。
# per-tool の timeout 宣言が最優先で、無ければこれ。sync 既定
# (CHAT_TOOL_EXECUTION_TIMEOUT=300)の流用は非同期化の動機と矛盾するため別建て(設計 D5)。
ASYNC_EXEC_TIMEOUT = os.environ.get("ASYNC_EXEC_TIMEOUT", "1800")
try:
    ASYNC_EXEC_TIMEOUT = int(ASYNC_EXEC_TIMEOUT)
except ValueError:
    ASYNC_EXEC_TIMEOUT = 1800

# llens(#110): 非同期実行の同時数上限(per-user)。超過は待たずに即エラーを
# tool result で返す(モデルに再試行を委ねる)。SUBAGENT_MAX_CONCURRENT とは
# 別会計(exec 席 ≠ 子ターン席の AND)。
ASYNC_EXEC_MAX_CONCURRENT = os.environ.get("ASYNC_EXEC_MAX_CONCURRENT", "4")
try:
    ASYNC_EXEC_MAX_CONCURRENT = int(ASYNC_EXEC_MAX_CONCURRENT)
except ValueError:
    ASYNC_EXEC_MAX_CONCURRENT = 4

# llens(#110): 非同期実行の同時数上限(プロセス全体)。default executor
# (全 DB アクセス共有)の枯渇に対する実効防衛。
ASYNC_EXEC_MAX_TOTAL = os.environ.get("ASYNC_EXEC_MAX_TOTAL", "16")
try:
    ASYNC_EXEC_MAX_TOTAL = int(ASYNC_EXEC_MAX_TOTAL)
except ValueError:
    ASYNC_EXEC_MAX_TOTAL = 16

# llens(#110): tool 結果の切詰め長(文字)— 同期・バックグラウンド共通(契約 T5:
# 暴走出力のリスクは同期でも同じで、無人経路 = サーバ駆動配達が詰まない保険も兼ねる)。
# 適用は結果の生産点で一度だけ(同期 = execute_tool_calls の wire 正規化直後、
# background = runner の保存時。中継 = 回収 builtin の envelope・配達 notice は透過)。
# 超過時は render を text に降格し truncated / original_chars を立てる
# (不正 JSON を FE / json.loads に渡さない)。
TOOL_RESULT_MAX_CHARS = os.environ.get("TOOL_RESULT_MAX_CHARS", "65536")
try:
    TOOL_RESULT_MAX_CHARS = int(TOOL_RESULT_MAX_CHARS)
except ValueError:
    TOOL_RESULT_MAX_CHARS = 65536

# llens(#110): サーバ駆動配達(execution_resume)の連鎖上限 — ユーザー発話なしに
# 連続できる自動ターン(通知 + 続きターン)の数。上限到達で配達を保留し、人間の
# 発話で自然解除される(無人連続ターンの暴走ガード — 契約 T3)。
ASYNC_EXEC_RESUME_CHAIN_LIMIT = os.environ.get("ASYNC_EXEC_RESUME_CHAIN_LIMIT", "5")
try:
    ASYNC_EXEC_RESUME_CHAIN_LIMIT = int(ASYNC_EXEC_RESUME_CHAIN_LIMIT)
except ValueError:
    ASYNC_EXEC_RESUME_CHAIN_LIMIT = 5

# llens(#110): exec 行の result 保持日数。0(既定)= GC しない。正の値なら boot
# スイープ時に、終端化からこの日数を過ぎた行の重量フィールド(result/args/meta)を
# 落とす(行自体は監査のため消さない)。
EXEC_RETENTION_DAYS = os.environ.get("EXEC_RETENTION_DAYS", "0")
try:
    EXEC_RETENTION_DAYS = int(EXEC_RETENTION_DAYS)
except ValueError:
    EXEC_RETENTION_DAYS = 0


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

# nucllei: 旧 alias WEBUI_JWT_SECRET_KEY は撤去(旧データ互換を持たない方針)
WEBUI_SECRET_KEY = os.environ.get("WEBUI_SECRET_KEY", "t0p-s3cr3t")

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
    "AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST", "10"
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
# OPENTELEMETRY
####################################

ENABLE_OTEL = os.environ.get("ENABLE_OTEL", "False").lower() == "true"
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
)
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "nucllei")
# OTEL_RESOURCE_ATTRIBUTES / OTEL_TRACES_SAMPLER は nucllei 側に読む側が無いので定数を持たない
# (OpenTelemetry SDK 自身が環境変数を読む)。

# llens(#84): 旧 EXTERNAL_PWA_MANIFEST_URL(manifest を外部 URL から proxy)は
# 消費者のいない upstream 残骸のため撤去。PWA は ENABLE_PWA 一本(DATA_DIR 節参照)。

####################################
# /ext(参照資料の配信)と url= の一般 URL 取得
####################################

# `/ext/**` の配信に設定は無い(ken 決定 2026-10-03): `EXT_DIR`(上の DATA/FRONTEND BUILD DIR 節。
# Docker では `/ext`、host 直起動では `./ext`)に置いたものを、そのままの相対パスで `/ext/<rel>` に
# read-only 配信する(`ext/cage/static/X` ⇔ `/ext/cage/static/X`。ファイルのみ、ディレクトリ一覧は
# 出さない)。何を配信するかは配備が `EXT_DIR` に何を置くか(volume / symlink)で決める。sandbox は
# 同じ物を jail の `/ext` に bind する規約(#138)なので、sandbox のパス `/ext/X` と URL
# `/ext/X` は同じ文字列で、nucllei の `EXT_DIR/X` に実在すれば同じ実体(utils/image_refs
# の橋渡し)。nucllei 自身の `/mnt` も `/ext` も見ない — 同日の朝は両側とも `/mnt` 固定だったが、
# nucllei は docker 専用でなく、host 直起動で host の `/mnt` を晒してしまう(ken 指摘 2026-10-03)。
# 2026-10-03 までの `EXT_UPSTREAMS`(http 中継 / `file://` root)・`EXT_FILE_SCOPES`(配信
# 範囲)・`EXT_PUBLIC_BASES`(原本 URL のルート)— と同日に一度置いた `EXT_DIRS` — は撤去。
# `url=` の一般 URL がどこでも取れるようになり、http 中継と原本ルートの対応は要らなくなった。


def parse_self_addresses(raw: str) -> frozenset:
    """`SELF_ADDRESSES` をパースする — カンマ区切りの IP アドレス(IPv4 / IPv6)。
    url= の一般 URL で「このサーバ自身」として取りに行かないアドレスに**追加**する
    (`utils/ext_upstreams.is_self_address`)。コンテナからは host の物理 / VPN
    インタフェースの IP を列挙できない(見えるのは bridge の gateway だけ)ので、host の
    外向き IP 宛てに同居サービスへ届く穴を配備側が塞ぐための口(ken 決定 2026-10-03。
    既定は空 = 実行時に求めた集合だけ)。IP でないもの・CIDR は捨てて WARNING。"""
    import ipaddress

    found = set()
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            found.add(ipaddress.ip_address(entry))
        except ValueError:
            log.warning(
                f"SELF_ADDRESSES: ignoring invalid entry {entry!r} (IP アドレスのみ。CIDR は不可)"
            )
    return frozenset(found)


# 2026-10-03 までの名前は EXT_SELF_ADDRESSES(同日に EXT_ 接頭辞を外した — /ext とは無関係)
SELF_ADDRESSES = parse_self_addresses(os.environ.get("SELF_ADDRESSES", ""))


# 参照(URL / sandbox fs パス)の解決(utils/image_refs.py — ローカル読み / サーバ側 fetch →
# base64)の既定のサイズ上限。既定 25MB(sandbox 転送上限と同値)。呼び手が `max_bytes` で
# 上書きできる(fetch_url / send_to_workspace の valve)。2026-10-03 までの名前は
# EXT_INLINE_MAX_BYTES(同日に改名 — inline だけの上限ではない)。
FETCH_MAX_BYTES = os.environ.get("FETCH_MAX_BYTES", "")
try:
    FETCH_MAX_BYTES = int(FETCH_MAX_BYTES)
except Exception:
    FETCH_MAX_BYTES = 25 * 1024 * 1024
