import asyncio
import json
import logging
import mimetypes
import os
import sys
import time

from contextlib import asynccontextmanager
from urllib.parse import urlencode, parse_qs, urlparse
from pydantic import BaseModel
from sqlalchemy import text


from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
    applications,
)

from fastapi.openapi.docs import get_swagger_ui_html

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response


from nucllei.builtin_tools import sync_builtin_tools
from nucllei.utils.executions import sweep_executions_on_boot
from nucllei.utils import execution_resume, image_refs
from nucllei.utils.logger import start_logger
from nucllei.utils.completion_events import (
    CompletionEventStream,
    get_stream,
    sse_event_generator,
)
from nucllei.routers import (
    audio,
    ollama,
    openai,
    pipelines,
    tasks,
    auths,
    chats,
    configs,
    ext,
    groups,
    files,
    functions,
    models,
    skills,
    tools,
    users,
    utils,
)


from nucllei.internal.db import Session, engine

from nucllei.models.models import Models
from nucllei.models.users import Users
from nucllei.models.chats import Chats

from nucllei.config import (
    # Ollama
    ENABLE_OLLAMA_API,
    OLLAMA_BASE_URLS,
    OLLAMA_API_CONFIGS,
    # OpenAI
    ENABLE_OPENAI_API,
    OPENAI_API_BASE_URLS,
    OPENAI_API_KEYS,
    OPENAI_API_CONFIGS,
    # Tool Server Configs
    TOOL_SERVER_CONNECTIONS,
    # Audio
    AUDIO_STT_ENGINE,
    AUDIO_STT_MODEL,
    AUDIO_STT_OPENAI_API_BASE_URL,
    AUDIO_STT_OPENAI_API_KEY,
    AUDIO_STT_AZURE_API_KEY,
    AUDIO_STT_AZURE_REGION,
    AUDIO_STT_AZURE_LOCALES,
    AUDIO_TTS_API_KEY,
    AUDIO_TTS_ENGINE,
    AUDIO_TTS_MODEL,
    AUDIO_TTS_OPENAI_API_BASE_URL,
    AUDIO_TTS_OPENAI_API_KEY,
    AUDIO_TTS_SPLIT_ON,
    AUDIO_TTS_VOICE,
    AUDIO_TTS_AZURE_SPEECH_REGION,
    AUDIO_TTS_AZURE_SPEECH_OUTPUT_FORMAT,
    ENABLE_AUDIO_STT,
    ENABLE_AUDIO_TTS,
    WHISPER_MODEL,
    WHISPER_VAD_FILTER,
    WHISPER_LANGUAGE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_CPU_THREADS,
    WHISPER_NUM_WORKERS,
    WHISPER_BEAM_SIZE,
    AUDIO_STT_SEGMENT_SECONDS,
    AUDIO_STT_SEGMENT_PAUSE_MS,
    AUDIO_STT_SILENCE_THRESHOLD_DB,
    AUDIO_STT_RECORDING_BITRATE_KBPS,
    DEEPGRAM_API_KEY,
    ATTACHMENT_MAX_COUNT,
    ATTACHMENT_MAX_SIZE_MB,
    ATTACHMENT_ALLOWED_EXTENSIONS,
    ATTACHMENT_IMAGE_MAX_DIMENSION,
    PASTED_TEXT_CHARACTER_LIMIT,
    ATTACHMENT_FUNCTION_ID,
    # Retrieval (Web Search)
    WEBUI_AUTH,
    WEBUI_BANNERS,
    WEBHOOK_URL,
    SESSION_IDLE_TIMEOUT,
    ENABLE_SIGNUP,
    ENABLE_LOGIN_FORM,
    ENABLE_LOGIN_AUTOCOMPLETE,
    ENABLE_API_KEY,
    ENABLE_API_KEY_ENDPOINT_RESTRICTIONS,
    API_KEY_ALLOWED_ENDPOINTS,
    ENABLE_USER_WEBHOOKS,
    USER_PERMISSIONS,
    ADDITIONAL_USER_META,
    DEFAULT_USER_ROLE,
    DEFAULT_PROMPT_SUGGESTIONS,
    DEFAULT_MODELS,
    MODEL_ORDER_LIST,
    # WebUI (OAuth)
    ENABLE_OAUTH_ROLE_MANAGEMENT,
    OAUTH_ROLES_CLAIM,
    OAUTH_EMAIL_CLAIM,
    OAUTH_PICTURE_CLAIM,
    OAUTH_USERNAME_CLAIM,
    OAUTH_ALLOWED_ROLES,
    OAUTH_ADMIN_ROLES,
    # WebUI (LDAP)
    ENABLE_LDAP,
    LDAP_SERVER_LABEL,
    LDAP_SERVER_HOST,
    LDAP_SERVER_PORT,
    LDAP_ATTRIBUTE_FOR_MAIL,
    LDAP_ATTRIBUTE_FOR_USERNAME,
    LDAP_SEARCH_FILTERS,
    LDAP_SEARCH_BASE,
    LDAP_APP_DN,
    LDAP_APP_PASSWORD,
    LDAP_USE_TLS,
    LDAP_CA_CERT_FILE,
    LDAP_CIPHERS,
    # Misc
    CACHE_DIR,
    STATIC_DIR,
    FRONTEND_BUILD_DIR,
    CORS_ALLOW_ORIGIN,
    DEFAULT_LOCALE,
    OAUTH_PROVIDERS,
    WEBUI_URL,
    # Admin
    # Tasks
    TASK_MODEL,
    TASK_MODEL_EXTERNAL,
    ENABLE_TITLE_GENERATION,
    TITLE_GENERATION_PROMPT_TEMPLATE,
    AppConfig,
    reset_config,
)
from nucllei.env import (
    CUSTOM_DIR,
    ENV,
    ENABLE_PWA,
    GLOBAL_LOG_LEVEL,
    SRC_LOG_LEVELS,
    VERSION,
    WEBUI_NAME,
    WEBUI_COPYRIGHT,
    WEBUI_VERSION,
    WEBUI_SECRET_KEY,
    WEBUI_SESSION_COOKIE_SAME_SITE,
    WEBUI_SESSION_COOKIE_SECURE,
    WEBUI_AUTH_TRUSTED_EMAIL_HEADER,
    WEBUI_AUTH_TRUSTED_NAME_HEADER,
    RESET_CONFIG_ON_START,
    ENABLE_OTEL,
)


from nucllei.utils.models import (
    get_all_models,
    get_all_base_models,
    check_model_access,
)
from nucllei.utils.chat import (
    generate_chat_completion as chat_completion_handler,
    chat_action as chat_action_handler,
)
from nucllei.utils.middleware import process_chat_payload, process_chat_response
from nucllei.utils.access_control import has_access

from nucllei.utils.auth import (
    apply_session_renewal,
    session_idle_timeout_seconds,
    get_http_authorization_cred,
    decode_token,
    get_admin_user,
    get_verified_user,
)
from nucllei.utils.oauth import OAuthManager
from nucllei.utils.security_headers import SecurityHeadersMiddleware
from nucllei.utils.content_hardening import active_content_headers

from nucllei.tasks import (
    list_task_ids_by_chat_id,
    stop_task,
)  # Import from tasks.py

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except (HTTPException, StarletteHTTPException) as ex:
            if ex.status_code == 404:
                if path.endswith(".js"):
                    # Return 404 for javascript files
                    raise ex
                elif path.lstrip("/").startswith(("api/", "openai/", "ollama/")):
                    # llens(#110): 未マッチの API パスに index.html を 200 で返さない。
                    # ブラウザはその HTML をキャッシュするため、FE/BE のバージョン差で
                    # 一度でも踏むと、後から endpoint を足しても古い HTML を掴み続けて
                    # JSON パース失敗が延々続く(dev で実害。vite の SPA fallback も同型)
                    raise ex
                else:
                    return await super().get_response("index.html", scope)
            else:
                raise ex


class CacheStaticFiles(StaticFiles):
    # llens(GHSA-m8f9-9whg-f4xr): /cache は無認証で公開配信される。万一アクティブ型の
    # ファイル(HTML/SVG/JS)が置かれてもブラウザで実行されないよう、危険な content-type
    # は添付ダウンロード扱いにし、MIME スニッフィングも無効化する。音声・画像等の
    # メディアは inline のまま。ハードニング定義は /ext プロキシと共有(content_hardening)。
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        for key, value in active_content_headers(
            response.headers.get("content-type")
        ).items():
            response.headers[key] = value
        return response


print(rf"""
███╗   ██╗██╗   ██╗ ██████╗██╗     ██╗     ███████╗██╗
████╗  ██║██║   ██║██╔════╝██║     ██║     ██╔════╝██║
██╔██╗ ██║██║   ██║██║     ██║     ██║     █████╗  ██║
██║╚██╗██║██║   ██║██║     ██║     ██║     ██╔══╝  ██║
██║ ╚████║╚██████╔╝╚██████╗███████╗███████╗███████╗██║
╚═╝  ╚═══╝ ╚═════╝  ╚═════╝╚══════╝╚══════╝╚══════╝╚═╝


Nucllei {VERSION}
Forked from Open WebUI v0.6.5
""")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_logger()
    if RESET_CONFIG_ON_START:
        reset_config()

    # llens(#28): LICENSE_KEY / get_license_data(api.openwebui.com への起動時照会)は
    # 外部照会(オフラインで成立しない)のため撤去

    # llens(#42): 組み込みツールを Tools テーブルへ upsert(specs は repo から再生成)
    await asyncio.to_thread(sync_builtin_tools)

    # llens(#139 / #146): /ext/<name> の対応表(EXT_MOUNTS — proxy / 静的。sandbox は同じ物を
    # jail の /ext/<name> に見る規約)を 1 行(sandbox と同じ流儀)。
    image_refs.log_mounts()

    # llens(#110): 前プロセスの走行中 execution は失われている(単一プロセスが
    # 不変条件)— 起動時に orphaned へ倒す(orphaned の権威)+ 保持期限 GC。
    await asyncio.to_thread(sweep_executions_on_boot)

    # llens(#110 契約 T3): サーバ駆動配達 driver の設置。sweep の**後**に
    # 未配達 terminal 行を持つ chat をスキャンして配達を起こす(wake ③)。
    # models は lazy なので、driver 側がターン起動前に get_all_models 相当を踏む。
    await execution_resume.install(app)

    yield


app = FastAPI(
    title="Nucllei",
    docs_url="/docs" if ENV == "dev" else None,
    openapi_url="/openapi.json" if ENV == "dev" else None,
    redoc_url=None,
    lifespan=lifespan,
)

oauth_manager = OAuthManager(app)

app.state.config = AppConfig()

app.state.WEBUI_NAME = WEBUI_NAME


########################################
#
# OPENTELEMETRY
#
########################################

if ENABLE_OTEL:
    from nucllei.utils.telemetry.setup import setup as setup_opentelemetry

    setup_opentelemetry(app=app, db_engine=engine)


########################################
#
# OLLAMA
#
########################################


app.state.config.ENABLE_OLLAMA_API = ENABLE_OLLAMA_API
app.state.config.OLLAMA_BASE_URLS = OLLAMA_BASE_URLS
app.state.config.OLLAMA_API_CONFIGS = OLLAMA_API_CONFIGS

app.state.OLLAMA_MODELS = {}

########################################
#
# OPENAI
#
########################################

app.state.config.ENABLE_OPENAI_API = ENABLE_OPENAI_API
app.state.config.OPENAI_API_BASE_URLS = OPENAI_API_BASE_URLS
app.state.config.OPENAI_API_KEYS = OPENAI_API_KEYS
app.state.config.OPENAI_API_CONFIGS = OPENAI_API_CONFIGS

app.state.OPENAI_MODELS = {}

########################################
#
# TOOL SERVERS
#
########################################

app.state.config.TOOL_SERVER_CONNECTIONS = TOOL_SERVER_CONNECTIONS
app.state.TOOL_SERVERS = []

########################################
#
# WEBUI
#
########################################

app.state.config.WEBUI_URL = WEBUI_URL
app.state.config.ENABLE_SIGNUP = ENABLE_SIGNUP
app.state.config.ENABLE_LOGIN_FORM = ENABLE_LOGIN_FORM
app.state.config.ENABLE_LOGIN_AUTOCOMPLETE = ENABLE_LOGIN_AUTOCOMPLETE

app.state.config.ENABLE_API_KEY = ENABLE_API_KEY
app.state.config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS = (
    ENABLE_API_KEY_ENDPOINT_RESTRICTIONS
)
app.state.config.API_KEY_ALLOWED_ENDPOINTS = API_KEY_ALLOWED_ENDPOINTS

app.state.config.SESSION_IDLE_TIMEOUT = SESSION_IDLE_TIMEOUT

app.state.config.DEFAULT_MODELS = DEFAULT_MODELS
app.state.config.DEFAULT_PROMPT_SUGGESTIONS = DEFAULT_PROMPT_SUGGESTIONS
app.state.config.DEFAULT_USER_ROLE = DEFAULT_USER_ROLE

app.state.config.USER_PERMISSIONS = USER_PERMISSIONS
app.state.config.ADDITIONAL_USER_META = ADDITIONAL_USER_META
app.state.config.WEBHOOK_URL = WEBHOOK_URL
app.state.config.BANNERS = WEBUI_BANNERS
app.state.config.MODEL_ORDER_LIST = MODEL_ORDER_LIST


app.state.config.ENABLE_USER_WEBHOOKS = ENABLE_USER_WEBHOOKS

app.state.config.OAUTH_USERNAME_CLAIM = OAUTH_USERNAME_CLAIM
app.state.config.OAUTH_PICTURE_CLAIM = OAUTH_PICTURE_CLAIM
app.state.config.OAUTH_EMAIL_CLAIM = OAUTH_EMAIL_CLAIM

app.state.config.ENABLE_OAUTH_ROLE_MANAGEMENT = ENABLE_OAUTH_ROLE_MANAGEMENT
app.state.config.OAUTH_ROLES_CLAIM = OAUTH_ROLES_CLAIM
app.state.config.OAUTH_ALLOWED_ROLES = OAUTH_ALLOWED_ROLES
app.state.config.OAUTH_ADMIN_ROLES = OAUTH_ADMIN_ROLES

app.state.config.ENABLE_LDAP = ENABLE_LDAP
app.state.config.LDAP_SERVER_LABEL = LDAP_SERVER_LABEL
app.state.config.LDAP_SERVER_HOST = LDAP_SERVER_HOST
app.state.config.LDAP_SERVER_PORT = LDAP_SERVER_PORT
app.state.config.LDAP_ATTRIBUTE_FOR_MAIL = LDAP_ATTRIBUTE_FOR_MAIL
app.state.config.LDAP_ATTRIBUTE_FOR_USERNAME = LDAP_ATTRIBUTE_FOR_USERNAME
app.state.config.LDAP_APP_DN = LDAP_APP_DN
app.state.config.LDAP_APP_PASSWORD = LDAP_APP_PASSWORD
app.state.config.LDAP_SEARCH_BASE = LDAP_SEARCH_BASE
app.state.config.LDAP_SEARCH_FILTERS = LDAP_SEARCH_FILTERS
app.state.config.LDAP_USE_TLS = LDAP_USE_TLS
app.state.config.LDAP_CA_CERT_FILE = LDAP_CA_CERT_FILE
app.state.config.LDAP_CIPHERS = LDAP_CIPHERS


app.state.AUTH_TRUSTED_EMAIL_HEADER = WEBUI_AUTH_TRUSTED_EMAIL_HEADER
app.state.AUTH_TRUSTED_NAME_HEADER = WEBUI_AUTH_TRUSTED_NAME_HEADER

app.state.TOOLS = {}
app.state.FUNCTIONS = {}

########################################
#
# RETRIEVAL
#
########################################


app.state.config.ATTACHMENT_MAX_SIZE_MB = ATTACHMENT_MAX_SIZE_MB
app.state.config.ATTACHMENT_MAX_COUNT = ATTACHMENT_MAX_COUNT
app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS = ATTACHMENT_ALLOWED_EXTENSIONS
app.state.config.ATTACHMENT_FUNCTION_ID = ATTACHMENT_FUNCTION_ID
app.state.config.ATTACHMENT_IMAGE_MAX_DIMENSION = ATTACHMENT_IMAGE_MAX_DIMENSION
app.state.config.PASTED_TEXT_CHARACTER_LIMIT = PASTED_TEXT_CHARACTER_LIMIT


########################################
#
# AUDIO
#
########################################

app.state.config.ENABLE_AUDIO_STT = ENABLE_AUDIO_STT
app.state.config.ENABLE_AUDIO_TTS = ENABLE_AUDIO_TTS

app.state.config.STT_OPENAI_API_BASE_URL = AUDIO_STT_OPENAI_API_BASE_URL
app.state.config.STT_OPENAI_API_KEY = AUDIO_STT_OPENAI_API_KEY
app.state.config.STT_ENGINE = AUDIO_STT_ENGINE
app.state.config.STT_MODEL = AUDIO_STT_MODEL

app.state.config.WHISPER_MODEL = WHISPER_MODEL
app.state.config.WHISPER_VAD_FILTER = WHISPER_VAD_FILTER
app.state.config.WHISPER_LANGUAGE = WHISPER_LANGUAGE
app.state.config.WHISPER_COMPUTE_TYPE = WHISPER_COMPUTE_TYPE
app.state.config.WHISPER_CPU_THREADS = WHISPER_CPU_THREADS
app.state.config.WHISPER_NUM_WORKERS = WHISPER_NUM_WORKERS
# env 専用(RuntimeConfig ではない = DB にも管理画面にも出ない)ので config の外に置く
app.state.WHISPER_BEAM_SIZE = WHISPER_BEAM_SIZE
# 録音バー(FE)へ /api/config の audio.stt でそのまま渡す値
app.state.AUDIO_STT_INPUT = {
    "segment_seconds": AUDIO_STT_SEGMENT_SECONDS,
    "segment_pause_ms": AUDIO_STT_SEGMENT_PAUSE_MS,
    "silence_threshold_db": AUDIO_STT_SILENCE_THRESHOLD_DB,
    "recording_bitrate_kbps": AUDIO_STT_RECORDING_BITRATE_KBPS,
}
app.state.config.DEEPGRAM_API_KEY = DEEPGRAM_API_KEY

app.state.config.AUDIO_STT_AZURE_API_KEY = AUDIO_STT_AZURE_API_KEY
app.state.config.AUDIO_STT_AZURE_REGION = AUDIO_STT_AZURE_REGION
app.state.config.AUDIO_STT_AZURE_LOCALES = AUDIO_STT_AZURE_LOCALES

app.state.config.TTS_OPENAI_API_BASE_URL = AUDIO_TTS_OPENAI_API_BASE_URL
app.state.config.TTS_OPENAI_API_KEY = AUDIO_TTS_OPENAI_API_KEY
app.state.config.TTS_ENGINE = AUDIO_TTS_ENGINE
app.state.config.TTS_MODEL = AUDIO_TTS_MODEL
app.state.config.TTS_VOICE = AUDIO_TTS_VOICE
app.state.config.TTS_API_KEY = AUDIO_TTS_API_KEY
app.state.config.TTS_SPLIT_ON = AUDIO_TTS_SPLIT_ON


app.state.config.TTS_AZURE_SPEECH_REGION = AUDIO_TTS_AZURE_SPEECH_REGION
app.state.config.TTS_AZURE_SPEECH_OUTPUT_FORMAT = AUDIO_TTS_AZURE_SPEECH_OUTPUT_FORMAT


app.state.faster_whisper_model = None
app.state.speech_synthesiser = None
app.state.speech_speaker_embeddings_dataset = None


########################################
#
# TASKS
#
########################################


app.state.config.TASK_MODEL = TASK_MODEL
app.state.config.TASK_MODEL_EXTERNAL = TASK_MODEL_EXTERNAL


app.state.config.ENABLE_TITLE_GENERATION = ENABLE_TITLE_GENERATION


app.state.config.TITLE_GENERATION_PROMPT_TEMPLATE = TITLE_GENERATION_PROMPT_TEMPLATE


########################################
#
# WEBUI
#
########################################

app.state.MODELS = {}


class RedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Check if the request is a GET request
        if request.method == "GET":
            path = request.url.path
            query_params = dict(parse_qs(urlparse(str(request.url)).query))

            # Check for the specific watch path and the presence of 'v' parameter
            if path.endswith("/watch") and "v" in query_params:
                video_id = query_params["v"][0]  # Extract the first 'v' parameter
                encoded_video_id = urlencode({"youtube": video_id})
                redirect_url = f"/?{encoded_video_id}"
                return RedirectResponse(url=redirect_url)

        # Proceed with the normal flow of other requests
        response = await call_next(request)
        return response


# Add the middleware to the app
app.add_middleware(RedirectMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def commit_session_after_request(request: Request, call_next):
    response = await call_next(request)
    # log.debug("Commit session after request")
    Session.commit()
    return response


@app.middleware("http")
async def check_url(request: Request, call_next):
    start_time = int(time.time())
    # llens(#70 B1): セッションは httpOnly cookie に一本化 — Authorization ヘッダ不在なら
    # cookie の token を採用する(tool server の session 転送など state.token 消費者のため。
    # API key / 外部クライアントは従来どおりヘッダ)。
    request.state.token = get_http_authorization_cred(
        request.headers.get("Authorization")
    )
    # #144: cookie は JWT セッション専用 — `sk-`(API キー)は cookie からは採らない。
    cookie_token = request.cookies.get("token")
    if (
        request.state.token is None
        and cookie_token
        and not cookie_token.startswith("sk-")
    ):
        request.state.token = get_http_authorization_cred(f"Bearer {cookie_token}")

    request.state.enable_api_key = app.state.config.ENABLE_API_KEY
    response = await call_next(request)
    # llens: アイドルタイムアウト(スライディング期限)の延命 cookie。
    # 認証依存関数(utils/auth.py の mark_session_renewal)が request.state に
    # 積んだ token をここで Set-Cookie する。依存関数の sub-response では
    # StreamingResponse を返す endpoint(chat completion 等)で header が
    # 捨てられるため、middleware で書く。
    apply_session_renewal(request, response)
    process_time = int(time.time()) - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGIN,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(ollama.router, prefix="/ollama", tags=["ollama"])
app.include_router(openai.router, prefix="/openai", tags=["openai"])


app.include_router(pipelines.router, prefix="/api/v1/pipelines", tags=["pipelines"])
app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["tasks"])

app.include_router(audio.router, prefix="/api/v1/audio", tags=["audio"])

app.include_router(configs.router, prefix="/api/v1/configs", tags=["configs"])

app.include_router(auths.router, prefix="/api/v1/auths", tags=["auths"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])


app.include_router(chats.router, prefix="/api/v1/chats", tags=["chats"])

app.include_router(models.router, prefix="/api/v1/models", tags=["models"])
app.include_router(skills.router, prefix="/api/v1/skills", tags=["skills"])
app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])

app.include_router(groups.router, prefix="/api/v1/groups", tags=["groups"])
app.include_router(files.router, prefix="/api/v1/files", tags=["files"])
app.include_router(functions.router, prefix="/api/v1/functions", tags=["functions"])
app.include_router(utils.router, prefix="/api/v1/utils", tags=["utils"])

# 認証付き上流プロキシ(#97)。SPA/static mount より前に並べる(明示ルート優先)。
app.include_router(ext.router, prefix="/ext", tags=["ext"])


##################################
#
# Chat Endpoints
#
##################################


@app.get("/api/models")
async def get_models(request: Request, user=Depends(get_verified_user)):
    def get_filtered_models(models, user):
        filtered_models = []
        for model in models:
            # llens(#48): arena モデルは撲滅済み(仮想 arena モデルの分岐を撤去)。
            model_info = Models.get_model_by_id(model["id"])
            if model_info:
                if user.id == model_info.user_id or has_access(
                    user.id, type="read", access_control=model_info.access_control
                ):
                    filtered_models.append(model)

        return filtered_models

    all_models = await get_all_models(request, user=user)

    models = []
    for model in all_models:
        # Filter out filter pipelines
        if "pipeline" in model and model["pipeline"].get("type", None) == "filter":
            continue

        # llens(GHSA-9vvh UX 対応 → #82 で概念確定): Models 行を持たない生モデル
        # (接続由来のベースモデル)は、チャットのモデルピッカーから全員(admin 含む)に
        # 対して隠す。Models 行は base_model_id 必須(= preset のみ、routers/models.py)
        # なので、ここを通るのは常に preset。ベースモデル選択・棚卸しは /api/models/base。
        # 行を持たない arena 等の仮想モデルもここで併せて除外される。
        if not Models.get_model_by_id(model["id"]):
            continue

        models.append(model)

    model_order_list = request.app.state.config.MODEL_ORDER_LIST
    if model_order_list:
        model_order_dict = {model_id: i for i, model_id in enumerate(model_order_list)}
        # Sort models by order list priority, with fallback for those not in the list
        models.sort(
            key=lambda x: (model_order_dict.get(x["id"], float("inf")), x["name"])
        )

    # Filter out models that the user does not have access to
    if user.role == "user":
        models = get_filtered_models(models, user)

    log.debug(
        f"/api/models returned filtered models accessible to the user: {json.dumps([model['id'] for model in models])}"
    )
    return {"data": models}


@app.get("/api/models/base")
async def get_base_models(request: Request, user=Depends(get_admin_user)):
    # llens(#82): 接続由来の生モデル一覧(admin 専用 — 棚卸しビューと preset の base 選択用)。
    # 生モデルは Models 行を持たず、チャットには一切露出しない。
    models = await get_all_base_models(request, user=user)
    return {"data": models}


@app.post("/api/chat/completions")
async def chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
):
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    form_data.pop("model_item", None)
    tasks = form_data.pop("background_tasks", None)

    metadata = {}

    # llens(GHSA-gfm2-xm6c-37qc / CVE-2026-45349): chat_id の所有者を検証する。
    # 既存チャットが要求ユーザーの所有でなければ拒否し、他人のチャットへの
    # 読み書き(クロスユーザー継続)を防ぐ。try の外で行うことで、下の except が
    # この 403 を被害者チャットへ書き込む(かつ 400 にラップする)のを避ける。
    _chat_id = form_data.get("chat_id")
    _message_id = form_data.get("id")
    if _chat_id:
        # llens(#110 契約 M3): サーバ駆動配達が append〜続きターン起動の途中なら
        # FE ターンを短く待たせる(レース窓の緩和 — 超過時は通す。競合の最終
        # 検出は chat 保存側の CAS / FE の save 400 リカバリが持つ)。
        await execution_resume.wait_for_quiet_delivery(_chat_id, timeout=5.0)

        _existing_chat = Chats.get_chat_by_id(_chat_id)
        if _existing_chat is not None:
            if _existing_chat.user_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have access to this chat.",
                )

            # llens(#107): agent session は HTTP 経由では read-only。書き手は
            # 親 tool 実行の内部経路(utils.chat.generate_chat_completion 直呼び)
            # のみで、router を通らないので自然に許可される。
            if _existing_chat.kind == "agent":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="agent session is read-only via HTTP",
                )

            # llens(#98): chat-wire の `id` は「これから生成されるアシスタント
            # メッセージ」の id でなければならない。検証しないと
            # upsert_message_to_chat_by_id_and_message_id が
            #   - 既存の user メッセージへ生成結果をマージ(回答がユーザー発話として残る)
            #   - 未知 id なら role/parentId を欠いたメッセージ片を新規挿入
            # を黙って行い、壊れたチャットが永続化される。サーバーが正しい形を
            # 推測して作り直すのではなく、呼び出し側の誤りとして弾く
            # (この fork は壊れた状態を黙って直さない — 型に忠実の原則)。
            #
            # try の外に置くのは所有者検証と同じ理由: 下の except は message_id へ
            # エラーを upsert するので、不正な id のままそこへ落ちると
            # エラー経路自体がメッセージ片を作ってしまう。
            if _message_id:
                _target = (
                    ((_existing_chat.chat or {}).get("history") or {}).get("messages")
                    or {}
                ).get(_message_id)
                if _target is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "`id` must be an existing message in this chat. "
                            "In chat-wire mode, create the assistant message "
                            "(role: 'assistant', content: '') in the chat first "
                            "and pass its id."
                        ),
                    )
                if _target.get("role") != "assistant":
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "`id` must be the id of an assistant message "
                            f"(got role={_target.get('role')!r}). The generated "
                            "response is written to that message."
                        ),
                    )

    events_stream = None
    queue = None

    try:
        model_id = form_data.get("model", None)
        if model_id not in request.app.state.MODELS:
            raise Exception("Model not found")

        model = request.app.state.MODELS[model_id]

        # Check if user has access to the model
        if user.role == "user":
            check_model_access(user, model)

        metadata = {
            "user_id": user.id,
            "chat_id": form_data.pop("chat_id", None),
            "message_id": form_data.pop("id", None),
            "files": form_data.get("files", None),
            "variables": form_data.get("variables", None),
            "model": model,
            # llens: native function calling のみサポート(issue #18)。
            # params.function_calling は参照しない(常に native 経路)。
        }

        # llens(wire v2): chat メタデータありの UI 経路はイベントバスを介した
        # SSE 契約。バスと購読は payload 処理より先に確立する(inlet filter の
        # __event_emitter__ が撃つイベントも取りこぼさずキューに積むため)。
        if metadata.get("chat_id") and metadata.get("message_id"):
            events_stream = CompletionEventStream(
                chat_id=metadata["chat_id"],
                message_id=metadata["message_id"],
            )
            queue = events_stream.subscribe()

        request.state.metadata = metadata
        form_data["metadata"] = metadata

        form_data, metadata, events = await process_chat_payload(
            request, form_data, user, metadata, model, events_stream
        )

    except Exception as e:
        log.debug(f"Error processing chat payload: {e}")
        if metadata.get("chat_id") and metadata.get("message_id"):
            # Update the chat message with the error
            Chats.upsert_message_to_chat_by_id_and_message_id(
                metadata["chat_id"],
                metadata["message_id"],
                {
                    "error": {"content": str(e)},
                },
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    try:
        response = await chat_completion_handler(request, form_data, user)

        return await process_chat_response(
            request,
            response,
            form_data,
            user,
            metadata,
            model,
            events,
            tasks,
            events_stream=events_stream,
            queue=queue,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# Alias for chat_completion (Legacy)
generate_chat_completions = chat_completion
generate_chat_completion = chat_completion


# `/api/chat/completed`(Filter の outlet を走らせる完了後 POST)は撤去(2026-10-05)—
# llens の filter は inlet しか使わず、outlet は観測専用の仕様だった。


@app.post("/api/chat/actions/{action_id}")
async def chat_action(
    request: Request, action_id: str, form_data: dict, user=Depends(get_verified_user)
):
    # llens(#107): agent session への action 実行も HTTP 経由では拒否
    _chat_id = form_data.get("chat_id")
    if _chat_id:
        _chat = Chats.get_chat_by_id(_chat_id)
        if _chat is not None and _chat.kind == "agent":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="agent session is read-only",
            )

    try:
        form_data.pop("model_item", None)

        return await chat_action_handler(request, action_id, form_data, user)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


def _get_owned_stream(task_id: str, user):
    """task_id からイベントバスを引き、chat 所有者を検証する(IDOR 防止)。"""
    stream = get_stream(task_id)
    if stream is None or not stream.chat_id:
        return None
    chat = Chats.get_chat_by_id(stream.chat_id)
    if chat is None or chat.user_id != user.id:
        return None
    return stream


@app.post("/api/tasks/stop/{task_id}")
async def stop_task_endpoint(task_id: str, user=Depends(get_verified_user)):
    # llens(wire v2): 所有者検証を追加(旧実装は verified user なら誰の task でも
    # 止められた)。stream 登録の無い task id は存在しない扱い。
    if _get_owned_stream(task_id, user) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    try:
        result = await stop_task(task_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get("/api/tasks/{task_id}/events")
async def task_events_endpoint(task_id: str, user=Depends(get_verified_user)):
    """走行中 completion タスクへの再アタッチ(SSE)。

    最新スナップショットを 1 発送ってから以降のライブイベントを流す
    (スナップショット先行なのでバックログ再生は不要 — wire v2)。
    """
    stream = _get_owned_stream(task_id, user)
    if stream is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    queue = stream.subscribe()

    first_events = [
        {
            "chat_id": stream.chat_id,
            "message_id": stream.message_id,
            "data": {"type": "task", "data": {"task_id": task_id}},
        }
    ]
    if stream.latest_snapshot is not None:
        first_events.append(
            {
                "chat_id": stream.chat_id,
                "message_id": stream.message_id,
                "data": {"type": "chat:completion", "data": stream.latest_snapshot},
            }
        )
    # llens(#100): 実行中 tool の進捗はスナップショットの**後**に配る。進捗は
    # ブロックへ紐づけて描かれるので、先に配ると宛先のブロックがまだ無い。
    first_events.extend(stream.latest_progress.values())
    # llens(#102): ライブ usage も揮発イベントなのでスナップショットの後
    # (順序規律: task → snapshot → 揮発イベント)。
    first_events.extend(stream.latest_usage.values())

    return StreamingResponse(
        sse_event_generator(stream, queue, first_events=first_events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/tasks/{task_id}/calls/{call_id}")
async def task_call_reply_endpoint(
    task_id: str, call_id: str, form_data: dict, user=Depends(get_verified_user)
):
    """__event_call__(tool → UI の confirmation/input)への応答。"""
    stream = _get_owned_stream(task_id, user)
    if stream is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    if not stream.resolve_call(call_id, form_data.get("result")):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Call not found"
        )
    return {"status": True}


@app.get("/api/tasks/chat/{chat_id}")
async def list_tasks_by_chat_id_endpoint(chat_id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id(chat_id)
    if chat is None or chat.user_id != user.id:
        return {"task_ids": []}

    task_ids = list_task_ids_by_chat_id(chat_id)
    return {"task_ids": task_ids}


##################################
#
# Config Endpoints
#
##################################


@app.get("/api/config")
async def get_app_config(request: Request):
    user = None
    if "token" in request.cookies:
        # cookie は JWT セッション専用(#144)。`sk-` は decode_token が None を返す
        # = 未ログイン扱いで、API キーとしては解釈しない。
        token = request.cookies.get("token")
        try:
            data = decode_token(token)
        except Exception as e:
            log.debug(e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        if data is not None and "id" in data:
            user = Users.get_user_by_id(data["id"])

    user_count = Users.get_num_users()
    onboarding = False

    if user is None:
        onboarding = user_count == 0

    return {
        **({"onboarding": True} if onboarding else {}),
        "status": True,
        "name": app.state.WEBUI_NAME,
        # 配備層(WEBUI_*)— version キーは本体(Nucllei)なので別名で返す
        "webui_version": WEBUI_VERSION,
        "copyright": WEBUI_COPYRIGHT,
        "version": VERSION,
        "default_locale": str(DEFAULT_LOCALE),
        # llens: アイドルタイムアウトの秒数(= 既存 SESSION_IDLE_TIMEOUT。設定項目は増やさない)。
        # 無期限(`-1` / `0`)なら null。FE は最後の人の操作からこの秒数が経ったら
        # 自分でログイン画面へ逃がす — サーバ側で切れていても、アイドル中のチャットは
        # リクエストを 1 本も出さないので前の人の画面が残り続けるため(共有 PC 対策)。
        # ログイン画面(未認証)からも読める位置に置く。
        "session_idle_timeout": session_idle_timeout_seconds(
            app.state.config.SESSION_IDLE_TIMEOUT
        ),
        "oauth": {
            "providers": {
                name: config.get("name", name)
                for name, config in OAUTH_PROVIDERS.items()
            }
        },
        "features": {
            "auth": WEBUI_AUTH,
            "auth_trusted_header": bool(app.state.AUTH_TRUSTED_EMAIL_HEADER),
            "enable_ldap": app.state.config.ENABLE_LDAP,
            "enable_api_key": app.state.config.ENABLE_API_KEY,
            "enable_signup": app.state.config.ENABLE_SIGNUP,
            "enable_login_form": app.state.config.ENABLE_LOGIN_FORM,
            # 共有 PC 配備でログインフォームの autocomplete を抑止する(未認証で読める必要が
            # ある — ログイン画面が使う)。効果の範囲は config.py の定義コメント参照。
            "enable_login_autocomplete": app.state.config.ENABLE_LOGIN_AUTOCOMPLETE,
            **(
                {
                    # llens(#52/#67/#51): built-in web 検索・retrieval router・WEB_SEARCH_*
                    # config・retrieval/web providers はすべて撤去済み。検索は案件層(prism)の
                    # tool が外部検索システム(SearXNG 等)を直接叩く(docling と同じ線引き)。
                    # llens(#23): code_interpreter(タグ検出実行)も機能ごと撤去。
                    # llens: enable_admin_export / enable_admin_chat_access は撤去
                    # (管理者は常にすべてできる — ken 決定 2026-10-03)
                    "enable_user_webhooks": app.state.config.ENABLE_USER_WEBHOOKS,
                }
                if user is not None
                else {}
            ),
        },
        **(
            {
                "default_models": app.state.config.DEFAULT_MODELS,
                "default_prompt_suggestions": app.state.config.DEFAULT_PROMPT_SUGGESTIONS,
                "audio": {
                    "tts": {
                        # llens(#53 フォロー): enable=False で FE がアイコンごと非表示
                        "enable": app.state.config.ENABLE_AUDIO_TTS,
                        "engine": app.state.config.TTS_ENGINE,
                        "voice": app.state.config.TTS_VOICE,
                        "split_on": app.state.config.TTS_SPLIT_ON,
                    },
                    "stt": {
                        "enable": app.state.config.ENABLE_AUDIO_STT,
                        "engine": app.state.config.STT_ENGINE,
                        # 配備ごとの調整値(env 専用 — _memos/audio.md)
                        **app.state.AUDIO_STT_INPUT,
                    },
                },
                "file": {
                    # wire は互換のため `file` キー維持(フロント enforcement)。値は attachment 設定。
                    "max_size": app.state.config.ATTACHMENT_MAX_SIZE_MB,
                    "max_count": app.state.config.ATTACHMENT_MAX_COUNT,
                    "allowed_extensions": app.state.config.ATTACHMENT_ALLOWED_EXTENSIONS,
                    "image_max_dimension": app.state.config.ATTACHMENT_IMAGE_MAX_DIMENSION,
                    "pasted_text_character_limit": app.state.config.PASTED_TEXT_CHARACTER_LIMIT,
                },
                "permissions": {**app.state.config.USER_PERMISSIONS},
            }
            if user is not None
            else {}
        ),
    }


class UrlForm(BaseModel):
    url: str


@app.get("/api/webhook")
async def get_webhook_url(user=Depends(get_admin_user)):
    return {
        "url": app.state.config.WEBHOOK_URL,
    }


@app.post("/api/webhook")
async def update_webhook_url(form_data: UrlForm, user=Depends(get_admin_user)):
    app.state.config.WEBHOOK_URL = form_data.url
    app.state.WEBHOOK_URL = app.state.config.WEBHOOK_URL
    return {"url": app.state.config.WEBHOOK_URL}


@app.get("/api/version")
async def get_app_version():
    return {
        "version": VERSION,
    }


############################
# OAuth Login & Callback
############################

# SessionMiddleware is used by authlib for oauth
if len(OAUTH_PROVIDERS) > 0:
    app.add_middleware(
        SessionMiddleware,
        secret_key=WEBUI_SECRET_KEY,
        session_cookie="oui-session",
        same_site=WEBUI_SESSION_COOKIE_SAME_SITE,
        https_only=WEBUI_SESSION_COOKIE_SECURE,
    )


@app.get("/oauth/{provider}/login")
async def oauth_login(provider: str, request: Request):
    return await oauth_manager.handle_login(request, provider)


# OAuth login logic is as follows:
# 1. Attempt to find a user with matching subject ID, tied to the provider
# 2. If OAUTH_MERGE_ACCOUNTS_BY_EMAIL is true, find a user with the email address provided via OAuth
#    - This is considered insecure in general, as OAuth providers do not always verify email addresses
# 3. If there is no user, and ENABLE_OAUTH_SIGNUP is true, create a user
#    - Email addresses are considered unique, so we fail registration if the email address is already taken
@app.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, request: Request, response: Response):
    return await oauth_manager.handle_callback(request, provider, response)


@app.get("/manifest.json")
async def get_manifest_json():
    # llens(#84): PWA は既定 off(閉域デプロイでインストール導線が邪魔)。
    # off では {} を返す — 有効な JSON だが name/icons/start_url を欠くため
    # インストール可能性が立たず、app.html の <link rel="manifest"> も console を汚さない。
    # 旧 EXTERNAL_PWA_MANIFEST_URL(外部 URL proxy)は消費者なしの upstream 残骸で撤去。
    if not ENABLE_PWA:
        return {}
    return {
        "name": app.state.WEBUI_NAME,
        "short_name": app.state.WEBUI_NAME,
        "description": "Nucllei is a thin, self-hosted LLM chat platform.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#343541",
        "orientation": "natural",
        "icons": [
            {
                "src": "/static/favicon-192.png",
                "type": "image/png",
                "sizes": "192x192",
            },
            {
                "src": "/static/favicon-512.png",
                "type": "image/png",
                "sizes": "512x512",
            },
            {
                "src": "/static/favicon-512.png",
                "type": "image/png",
                "sizes": "512x512",
                "purpose": "maskable",
            },
        ],
    }


@app.get("/opensearch.xml")
async def get_opensearch_xml():
    xml_content = rf"""
    <OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/" xmlns:moz="http://www.mozilla.org/2006/browser/search/">
    <ShortName>{app.state.WEBUI_NAME}</ShortName>
    <Description>Search {app.state.WEBUI_NAME}</Description>
    <InputEncoding>UTF-8</InputEncoding>
    <Image width="16" height="16" type="image/x-icon">{app.state.config.WEBUI_URL}/static/favicon.png</Image>
    <Url type="text/html" method="get" template="{app.state.config.WEBUI_URL}/?q={"{searchTerms}"}"/>
    <moz:SearchForm>{app.state.config.WEBUI_URL}</moz:SearchForm>
    </OpenSearchDescription>
    """
    return Response(content=xml_content, media_type="application/xml")


@app.get("/health")
async def healthcheck():
    return {"status": True}


@app.get("/health/db")
async def healthcheck_with_db():
    Session.execute(text("SELECT 1;")).all()
    return {"status": True}


# 配備先カスタマイズ(#84): CUSTOM_DIR の規定名ファイルを同梱既定より優先して配信する。
# 明示ルートは下の /static mount より先にマッチする。リクエスト都度ファイルを見るので
# 差し替えに再起動不要(pending.md と同じ)。規定名は消費者のあるものだけに絞る:
#   favicon.png = ブラウザタブ + アプリ内ホーム + webhook
#   brand.png   = 起動スプラッシュ + auth ページのロゴ(旧 splash.png)
#   model.png   = モデル既定アバター(旧 favicon-dark.png、gray 版)
#   favicon-192/512.png = PWA manifest アイコン(ENABLE_PWA 時のみ参照される)
CUSTOMIZABLE_STATIC = (
    "favicon.png",
    "brand.png",
    "model.png",
    "favicon-192.png",
    "favicon-512.png",
)


def _customizable_static_route(filename: str):
    async def serve():
        custom_path = CUSTOM_DIR / filename
        if custom_path.is_file():
            return FileResponse(custom_path)
        return FileResponse(STATIC_DIR / filename)

    return serve


for _filename in CUSTOMIZABLE_STATIC:
    app.get(f"/static/{_filename}", include_in_schema=False)(
        _customizable_static_route(_filename)
    )

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/cache", CacheStaticFiles(directory=CACHE_DIR), name="cache")


def swagger_ui_html(*args, **kwargs):
    return get_swagger_ui_html(
        *args,
        **kwargs,
        swagger_js_url="/static/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui/swagger-ui.css",
        swagger_favicon_url="/static/swagger-ui/favicon.png",
    )


applications.get_swagger_ui_html = swagger_ui_html

if os.path.exists(FRONTEND_BUILD_DIR):
    mimetypes.add_type("text/javascript", ".js")
    app.mount(
        "/",
        SPAStaticFiles(directory=FRONTEND_BUILD_DIR, html=True),
        name="spa-static-files",
    )
else:
    log.warning(
        f"Frontend build directory not found at '{FRONTEND_BUILD_DIR}'. Serving API only."
    )
