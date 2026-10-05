import json
import logging
import os
import shutil

from datetime import datetime
from pathlib import Path
from typing import Generic, Optional, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel

from nucllei.env import (
    DATA_DIR,
    FRONTEND_BUILD_DIR,
    OFFLINE_MODE,
    NUCLLEI_DIR,
    WEBUI_AUTH,
    env_int,
    log,
)
from nucllei.internal.db import get_db
from nucllei.models.configs import Config


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find("/health") == -1


# Filter out /endpoint
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

####################################
# Config helpers
####################################


# Function to run the alembic migrations
def run_migrations():
    log.info("Running migrations")
    try:
        from alembic import command
        from alembic.config import Config

        alembic_cfg = Config(NUCLLEI_DIR / "alembic.ini")

        # Set the script location dynamically
        migrations_path = NUCLLEI_DIR / "migrations"
        alembic_cfg.set_main_option("script_location", str(migrations_path))

        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        log.exception(f"Error running migrations: {e}")


run_migrations()


def save_to_db(data):
    with get_db() as db:
        existing_config = db.query(Config).first()
        if not existing_config:
            new_config = Config(data=data, version=0)
            db.add(new_config)
        else:
            existing_config.data = data
            existing_config.updated_at = datetime.now()
            db.add(existing_config)
        db.commit()


def reset_config():
    with get_db() as db:
        db.query(Config).delete()
        db.commit()


DEFAULT_CONFIG = {
    "version": 0,
    "ui": {
        "default_locale": "",
        "prompt_suggestions": [
            {
                "title": [
                    "Help me study",
                    "vocabulary for a college entrance exam",
                ],
                "content": "Help me study vocabulary: write a sentence for me to fill in the blank, and I'll try to pick the correct option.",
            },
            {
                "title": [
                    "Give me ideas",
                    "for what to do with my kids' art",
                ],
                "content": "What are 5 creative things I could do with my kids' art? I don't want to throw them away, but it's also so much clutter.",
            },
            {
                "title": ["Tell me a fun fact", "about the Roman Empire"],
                "content": "Tell me a random fun fact about the Roman Empire",
            },
            {
                "title": [
                    "Show me a code snippet",
                    "of a website's sticky header",
                ],
                "content": "Show me a code snippet of a website's sticky header in CSS and JavaScript.",
            },
            {
                "title": [
                    "Explain options trading",
                    "if I'm familiar with buying and selling stocks",
                ],
                "content": "Explain options trading in simple terms if I'm familiar with buying and selling stocks.",
            },
            {
                "title": ["Overcome procrastination", "give me tips"],
                "content": "Could you start by asking me about instances when I procrastinate the most and then give me some suggestions to overcome it?",
            },
            {
                "title": [
                    "Grammar check",
                    "rewrite it for better readability ",
                ],
                "content": 'Check the following sentence for grammar and clarity: "[sentence]". Rewrite it for better readability while maintaining its original meaning.',
            },
        ],
    },
}


def get_config():
    with get_db() as db:
        config_entry = db.query(Config).order_by(Config.id.desc()).first()
        return config_entry.data if config_entry else DEFAULT_CONFIG


CONFIG_DATA = get_config()


def get_config_value(config_path: str):
    path_parts = config_path.split(".")
    cur_config = CONFIG_DATA
    for key in path_parts:
        if key in cur_config:
            cur_config = cur_config[key]
        else:
            return None
    return cur_config


RUNTIME_CONFIG_REGISTRY = []


def save_config(config):
    global CONFIG_DATA
    global RUNTIME_CONFIG_REGISTRY
    try:
        save_to_db(config)
        CONFIG_DATA = config

        # Trigger updates on all registered RuntimeConfig entries
        for config_item in RUNTIME_CONFIG_REGISTRY:
            config_item.update()
    except Exception as e:
        log.exception(e)
        return False
    return True


T = TypeVar("T")


class RuntimeConfig(Generic[T]):
    def __init__(self, env_name: str, config_path: str, env_value: T):
        self.env_name = env_name
        self.config_path = config_path
        self.env_value = env_value
        self.config_value = get_config_value(config_path)
        # llens #30: 起動時の優先順位 = 明示 env > DB(UI 保存)> 既定。
        # - env(env_name)が非空 = 明示設定 → env が正。実効値に採用し、起動時に DB へ
        #   書き込む(config-as-code の権威シード。env を変えれば必ず効く)。
        # - env が空 → DB の保存値(実行中の UI 変更)を採用(= 再起動後も残る永続設定)。
        # - DB も無ければ既定(env 空時の env_value は既定合成済み)。
        if os.environ.get(env_name, "") != "":
            self.value = env_value
            if self.value != self.config_value:
                self.save()
        elif self.config_value is not None:
            self.value = self.config_value
        else:
            self.value = env_value

        RUNTIME_CONFIG_REGISTRY.append(self)

    def __str__(self):
        return str(self.value)

    @property
    def __dict__(self):
        raise TypeError(
            "RuntimeConfig object cannot be converted to dict, use config_get or .value instead."
        )

    def __getattribute__(self, item):
        if item == "__dict__":
            raise TypeError(
                "RuntimeConfig object cannot be converted to dict, use config_get or .value instead."
            )
        return super().__getattribute__(item)

    def update(self):
        new_value = get_config_value(self.config_path)
        if new_value is not None:
            self.value = new_value
            log.info(f"Updated {self.env_name} to new value {self.value}")

    def save(self):
        # llens #30: 実行中の変更を DB へ永続(稼働中のみ有効・再起動で env が再強制)。
        log.info(f"Saving '{self.env_name}' to the database")
        path_parts = self.config_path.split(".")
        sub_config = CONFIG_DATA
        for key in path_parts[:-1]:
            if key not in sub_config:
                sub_config[key] = {}
            sub_config = sub_config[key]
        sub_config[path_parts[-1]] = self.value
        save_to_db(CONFIG_DATA)
        self.config_value = self.value


class AppConfig:
    # llens(#80): Redis mirror(multi-worker 設定同期)は撤去 — wire v2 のタスク
    # レジストリが in-process のため multi-worker 自体が非サポート。単一プロセス前提。
    _state: dict[str, RuntimeConfig]

    def __init__(self):
        super().__setattr__("_state", {})

    def __setattr__(self, key, value):
        if isinstance(value, RuntimeConfig):
            self._state[key] = value
        else:
            self._state[key].value = value
            self._state[key].save()

    def __getattr__(self, key):
        if key not in self._state:
            raise AttributeError(f"Config key '{key}' not found")

        return self._state[key].value


####################################
# WEBUI_AUTH (Required for security)
####################################

ENABLE_API_KEY = RuntimeConfig(
    "ENABLE_API_KEY",
    "auth.api_key.enable",
    os.environ.get("ENABLE_API_KEY", "True").lower() == "true",
)

ENABLE_API_KEY_ENDPOINT_RESTRICTIONS = RuntimeConfig(
    "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS",
    "auth.api_key.endpoint_restrictions",
    os.environ.get("ENABLE_API_KEY_ENDPOINT_RESTRICTIONS", "False").lower() == "true",
)

API_KEY_ALLOWED_ENDPOINTS = RuntimeConfig(
    "API_KEY_ALLOWED_ENDPOINTS",
    "auth.api_key.allowed_endpoints",
    os.environ.get("API_KEY_ALLOWED_ENDPOINTS", ""),
)


SESSION_IDLE_TIMEOUT = RuntimeConfig(
    "SESSION_IDLE_TIMEOUT",
    "auth.session_idle_timeout",
    os.environ.get("SESSION_IDLE_TIMEOUT", "-1"),
)

####################################
# OAuth config
####################################


ENABLE_OAUTH_SIGNUP = RuntimeConfig(
    "ENABLE_OAUTH_SIGNUP",
    "oauth.enable_signup",
    os.environ.get("ENABLE_OAUTH_SIGNUP", "False").lower() == "true",
)


OAUTH_MERGE_ACCOUNTS_BY_EMAIL = RuntimeConfig(
    "OAUTH_MERGE_ACCOUNTS_BY_EMAIL",
    "oauth.merge_accounts_by_email",
    os.environ.get("OAUTH_MERGE_ACCOUNTS_BY_EMAIL", "False").lower() == "true",
)

OAUTH_PROVIDERS = {}

GOOGLE_CLIENT_ID = RuntimeConfig(
    "GOOGLE_CLIENT_ID",
    "oauth.google.client_id",
    os.environ.get("GOOGLE_CLIENT_ID", ""),
)

GOOGLE_CLIENT_SECRET = RuntimeConfig(
    "GOOGLE_CLIENT_SECRET",
    "oauth.google.client_secret",
    os.environ.get("GOOGLE_CLIENT_SECRET", ""),
)


GOOGLE_OAUTH_SCOPE = RuntimeConfig(
    "GOOGLE_OAUTH_SCOPE",
    "oauth.google.scope",
    os.environ.get("GOOGLE_OAUTH_SCOPE", "openid email profile"),
)

GOOGLE_REDIRECT_URI = RuntimeConfig(
    "GOOGLE_REDIRECT_URI",
    "oauth.google.redirect_uri",
    os.environ.get("GOOGLE_REDIRECT_URI", ""),
)

MICROSOFT_CLIENT_ID = RuntimeConfig(
    "MICROSOFT_CLIENT_ID",
    "oauth.microsoft.client_id",
    os.environ.get("MICROSOFT_CLIENT_ID", ""),
)

MICROSOFT_CLIENT_SECRET = RuntimeConfig(
    "MICROSOFT_CLIENT_SECRET",
    "oauth.microsoft.client_secret",
    os.environ.get("MICROSOFT_CLIENT_SECRET", ""),
)

MICROSOFT_CLIENT_TENANT_ID = RuntimeConfig(
    "MICROSOFT_CLIENT_TENANT_ID",
    "oauth.microsoft.tenant_id",
    os.environ.get("MICROSOFT_CLIENT_TENANT_ID", ""),
)

MICROSOFT_OAUTH_SCOPE = RuntimeConfig(
    "MICROSOFT_OAUTH_SCOPE",
    "oauth.microsoft.scope",
    os.environ.get("MICROSOFT_OAUTH_SCOPE", "openid email profile"),
)

MICROSOFT_REDIRECT_URI = RuntimeConfig(
    "MICROSOFT_REDIRECT_URI",
    "oauth.microsoft.redirect_uri",
    os.environ.get("MICROSOFT_REDIRECT_URI", ""),
)

GITHUB_CLIENT_ID = RuntimeConfig(
    "GITHUB_CLIENT_ID",
    "oauth.github.client_id",
    os.environ.get("GITHUB_CLIENT_ID", ""),
)

GITHUB_CLIENT_SECRET = RuntimeConfig(
    "GITHUB_CLIENT_SECRET",
    "oauth.github.client_secret",
    os.environ.get("GITHUB_CLIENT_SECRET", ""),
)

GITHUB_CLIENT_SCOPE = RuntimeConfig(
    "GITHUB_CLIENT_SCOPE",
    "oauth.github.scope",
    os.environ.get("GITHUB_CLIENT_SCOPE", "user:email"),
)

GITHUB_CLIENT_REDIRECT_URI = RuntimeConfig(
    "GITHUB_CLIENT_REDIRECT_URI",
    "oauth.github.redirect_uri",
    os.environ.get("GITHUB_CLIENT_REDIRECT_URI", ""),
)

OAUTH_CLIENT_ID = RuntimeConfig(
    "OAUTH_CLIENT_ID",
    "oauth.oidc.client_id",
    os.environ.get("OAUTH_CLIENT_ID", ""),
)

OAUTH_CLIENT_SECRET = RuntimeConfig(
    "OAUTH_CLIENT_SECRET",
    "oauth.oidc.client_secret",
    os.environ.get("OAUTH_CLIENT_SECRET", ""),
)

OPENID_PROVIDER_URL = RuntimeConfig(
    "OPENID_PROVIDER_URL",
    "oauth.oidc.provider_url",
    os.environ.get("OPENID_PROVIDER_URL", ""),
)

OPENID_REDIRECT_URI = RuntimeConfig(
    "OPENID_REDIRECT_URI",
    "oauth.oidc.redirect_uri",
    os.environ.get("OPENID_REDIRECT_URI", ""),
)

OAUTH_SCOPES = RuntimeConfig(
    "OAUTH_SCOPES",
    "oauth.oidc.scopes",
    os.environ.get("OAUTH_SCOPES", "openid email profile"),
)

OAUTH_CODE_CHALLENGE_METHOD = RuntimeConfig(
    "OAUTH_CODE_CHALLENGE_METHOD",
    "oauth.oidc.code_challenge_method",
    os.environ.get("OAUTH_CODE_CHALLENGE_METHOD", None),
)

OAUTH_PROVIDER_NAME = RuntimeConfig(
    "OAUTH_PROVIDER_NAME",
    "oauth.oidc.provider_name",
    os.environ.get("OAUTH_PROVIDER_NAME", "SSO"),
)

OAUTH_USERNAME_CLAIM = RuntimeConfig(
    "OAUTH_USERNAME_CLAIM",
    "oauth.oidc.username_claim",
    os.environ.get("OAUTH_USERNAME_CLAIM", "name"),
)


OAUTH_PICTURE_CLAIM = RuntimeConfig(
    "OAUTH_PICTURE_CLAIM",
    "oauth.oidc.avatar_claim",
    os.environ.get("OAUTH_PICTURE_CLAIM", "picture"),
)

OAUTH_EMAIL_CLAIM = RuntimeConfig(
    "OAUTH_EMAIL_CLAIM",
    "oauth.oidc.email_claim",
    os.environ.get("OAUTH_EMAIL_CLAIM", "email"),
)

OAUTH_GROUPS_CLAIM = RuntimeConfig(
    "OAUTH_GROUPS_CLAIM",
    "oauth.oidc.group_claim",
    # 旧 env 名は OAUTH_GROUP_CLAIM(単数)— RuntimeConfig の明示 env 判定(env_name =
    # OAUTH_GROUPS_CLAIM)と値ソースが食い違っていたため複数形に統一(OAUTH_ROLES_CLAIM と対称)。
    os.environ.get("OAUTH_GROUPS_CLAIM", "groups"),
)

ENABLE_OAUTH_ROLE_MANAGEMENT = RuntimeConfig(
    "ENABLE_OAUTH_ROLE_MANAGEMENT",
    "oauth.enable_role_mapping",
    os.environ.get("ENABLE_OAUTH_ROLE_MANAGEMENT", "False").lower() == "true",
)

ENABLE_OAUTH_GROUP_MANAGEMENT = RuntimeConfig(
    "ENABLE_OAUTH_GROUP_MANAGEMENT",
    "oauth.enable_group_mapping",
    os.environ.get("ENABLE_OAUTH_GROUP_MANAGEMENT", "False").lower() == "true",
)

OAUTH_ROLES_CLAIM = RuntimeConfig(
    "OAUTH_ROLES_CLAIM",
    "oauth.roles_claim",
    os.environ.get("OAUTH_ROLES_CLAIM", "roles"),
)

OAUTH_ALLOWED_ROLES = RuntimeConfig(
    "OAUTH_ALLOWED_ROLES",
    "oauth.allowed_roles",
    [
        role.strip()
        for role in os.environ.get("OAUTH_ALLOWED_ROLES", "user,admin").split(",")
    ],
)

OAUTH_ADMIN_ROLES = RuntimeConfig(
    "OAUTH_ADMIN_ROLES",
    "oauth.admin_roles",
    [role.strip() for role in os.environ.get("OAUTH_ADMIN_ROLES", "admin").split(",")],
)

OAUTH_ALLOWED_DOMAINS = RuntimeConfig(
    "OAUTH_ALLOWED_DOMAINS",
    "oauth.allowed_domains",
    [
        domain.strip()
        for domain in os.environ.get("OAUTH_ALLOWED_DOMAINS", "*").split(",")
    ],
)


def load_oauth_providers():
    OAUTH_PROVIDERS.clear()
    if GOOGLE_CLIENT_ID.value and GOOGLE_CLIENT_SECRET.value:

        def google_oauth_register(client):
            client.register(
                name="google",
                client_id=GOOGLE_CLIENT_ID.value,
                client_secret=GOOGLE_CLIENT_SECRET.value,
                server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                client_kwargs={"scope": GOOGLE_OAUTH_SCOPE.value},
                redirect_uri=GOOGLE_REDIRECT_URI.value,
            )

        OAUTH_PROVIDERS["google"] = {
            "redirect_uri": GOOGLE_REDIRECT_URI.value,
            "register": google_oauth_register,
        }

    if (
        MICROSOFT_CLIENT_ID.value
        and MICROSOFT_CLIENT_SECRET.value
        and MICROSOFT_CLIENT_TENANT_ID.value
    ):

        def microsoft_oauth_register(client):
            client.register(
                name="microsoft",
                client_id=MICROSOFT_CLIENT_ID.value,
                client_secret=MICROSOFT_CLIENT_SECRET.value,
                server_metadata_url=f"https://login.microsoftonline.com/{MICROSOFT_CLIENT_TENANT_ID.value}/v2.0/.well-known/openid-configuration?appid={MICROSOFT_CLIENT_ID.value}",
                client_kwargs={
                    "scope": MICROSOFT_OAUTH_SCOPE.value,
                },
                redirect_uri=MICROSOFT_REDIRECT_URI.value,
            )

        OAUTH_PROVIDERS["microsoft"] = {
            "redirect_uri": MICROSOFT_REDIRECT_URI.value,
            "picture_url": "https://graph.microsoft.com/v1.0/me/photo/$value",
            "register": microsoft_oauth_register,
        }

    if GITHUB_CLIENT_ID.value and GITHUB_CLIENT_SECRET.value:

        def github_oauth_register(client):
            client.register(
                name="github",
                client_id=GITHUB_CLIENT_ID.value,
                client_secret=GITHUB_CLIENT_SECRET.value,
                access_token_url="https://github.com/login/oauth/access_token",
                authorize_url="https://github.com/login/oauth/authorize",
                api_base_url="https://api.github.com",
                userinfo_endpoint="https://api.github.com/user",
                client_kwargs={"scope": GITHUB_CLIENT_SCOPE.value},
                redirect_uri=GITHUB_CLIENT_REDIRECT_URI.value,
            )

        OAUTH_PROVIDERS["github"] = {
            "redirect_uri": GITHUB_CLIENT_REDIRECT_URI.value,
            "register": github_oauth_register,
            "sub_claim": "id",
        }

    if (
        OAUTH_CLIENT_ID.value
        and OAUTH_CLIENT_SECRET.value
        and OPENID_PROVIDER_URL.value
    ):

        def oidc_oauth_register(client):
            client_kwargs = {
                "scope": OAUTH_SCOPES.value,
            }

            if (
                OAUTH_CODE_CHALLENGE_METHOD.value
                and OAUTH_CODE_CHALLENGE_METHOD.value == "S256"
            ):
                client_kwargs["code_challenge_method"] = "S256"
            elif OAUTH_CODE_CHALLENGE_METHOD.value:
                raise Exception(
                    'Code challenge methods other than "%s" not supported. Given: "%s"'
                    % ("S256", OAUTH_CODE_CHALLENGE_METHOD.value)
                )

            client.register(
                name="oidc",
                client_id=OAUTH_CLIENT_ID.value,
                client_secret=OAUTH_CLIENT_SECRET.value,
                server_metadata_url=OPENID_PROVIDER_URL.value,
                client_kwargs=client_kwargs,
                redirect_uri=OPENID_REDIRECT_URI.value,
            )

        OAUTH_PROVIDERS["oidc"] = {
            "name": OAUTH_PROVIDER_NAME.value,
            "redirect_uri": OPENID_REDIRECT_URI.value,
            "register": oidc_oauth_register,
        }


load_oauth_providers()

####################################
# Static DIR
####################################

STATIC_DIR = Path(os.getenv("STATIC_DIR", NUCLLEI_DIR / "static")).resolve()

# 同梱既定(ブランド派生物)の供給元。ビルド済みなら build/static、無ければ
# FE ソースの static/static(= build/static の元)へフォールバック — fresh clone や
# CI の pytest(FE ビルドなし)でも /static の同梱既定 fallback(#84)が成立する。
# 本番 docker は build があるためこの分岐に入らない。
_frontend_static_src = FRONTEND_BUILD_DIR / "static"
if not _frontend_static_src.is_dir():
    _frontend_static_src = NUCLLEI_DIR.parent / "static" / "static"

for file_path in _frontend_static_src.glob("**/*"):
    if file_path.is_file():
        target_path = STATIC_DIR / file_path.relative_to(_frontend_static_src)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(file_path, target_path)
        except Exception as e:
            logging.error(f"An error occurred: {e}")

# llens(#84): 旧 favicon/splash/loader の個別コピー 3 連は上の glob コピーと
# 完全重複だったため削除。配備先ブランドの差し替えは CUSTOM_DIR(main.py の
# 規定名オーバーレイ配信)で行う — build/static へ被せる mount は不要になった。


# llens: 旧 upstream の CUSTOM_NAME(api.openwebui.com への起動時 fetch)は
# 外部照会(オフラインで成立しない)のため機能ごと撤去。WEBUI_NAME は env で指定する。

####################################
# STORAGE PROVIDER
####################################

STORAGE_PROVIDER = os.environ.get("STORAGE_PROVIDER", "local")  # defaults to local, s3

S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", None)
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", None)
S3_REGION_NAME = os.environ.get("S3_REGION_NAME", None)
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", None)
S3_KEY_PREFIX = os.environ.get("S3_KEY_PREFIX", None)
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", None)
S3_USE_ACCELERATE_ENDPOINT = (
    os.environ.get("S3_USE_ACCELERATE_ENDPOINT", "False").lower() == "true"
)
S3_ADDRESSING_STYLE = os.environ.get("S3_ADDRESSING_STYLE", None)

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", None)
GOOGLE_APPLICATION_CREDENTIALS_JSON = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS_JSON", None
)

AZURE_STORAGE_ENDPOINT = os.environ.get("AZURE_STORAGE_ENDPOINT", None)
AZURE_STORAGE_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", None)
AZURE_STORAGE_KEY = os.environ.get("AZURE_STORAGE_KEY", None)

####################################
# File Upload DIR
####################################

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


####################################
# Cache DIR
####################################

CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


####################################
# OLLAMA_BASE_URL
####################################

# nucllei: 既定 false(2026-10-03、旧既定 true)。推論エンジンは OpenAI 互換接続が本線で、
# ollama は dev の代替手段に留まる。
ENABLE_OLLAMA_API = RuntimeConfig(
    "ENABLE_OLLAMA_API",
    "ollama.enable",
    os.environ.get("ENABLE_OLLAMA_API", "False").lower() == "true",
)

# llens: K8S_FLAG(kubernetes 内 ollama-service への切替)は k8s サポート撤去済みのため撤去
# nucllei: 旧 alias OLLAMA_API_BASE_URL("…/api" 付き)と USE_OLLAMA_DOCKER(prod で
# "/ollama" を docker 内アドレスへ書き換える)は撤去。既定値だけ引き継ぐ。
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "")
if OLLAMA_BASE_URL:
    # Remove trailing slash
    OLLAMA_BASE_URL = (
        OLLAMA_BASE_URL[:-1] if OLLAMA_BASE_URL.endswith("/") else OLLAMA_BASE_URL
    )
else:
    OLLAMA_BASE_URL = "http://localhost:11434"


OLLAMA_BASE_URLS = os.environ.get("OLLAMA_BASE_URLS", "")
OLLAMA_BASE_URLS = OLLAMA_BASE_URLS if OLLAMA_BASE_URLS != "" else OLLAMA_BASE_URL

OLLAMA_BASE_URLS = [url.strip() for url in OLLAMA_BASE_URLS.split(";")]
OLLAMA_BASE_URLS = RuntimeConfig(
    "OLLAMA_BASE_URLS", "ollama.base_urls", OLLAMA_BASE_URLS
)

OLLAMA_API_CONFIGS = RuntimeConfig(
    "OLLAMA_API_CONFIGS",
    "ollama.api_configs",
    {},
)

####################################
# OPENAI_API
####################################


ENABLE_OPENAI_API = RuntimeConfig(
    "ENABLE_OPENAI_API",
    "openai.enable",
    os.environ.get("ENABLE_OPENAI_API", "True").lower() == "true",
)


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_BASE_URL = os.environ.get("OPENAI_API_BASE_URL", "")

if OPENAI_API_BASE_URL == "":
    OPENAI_API_BASE_URL = "https://api.openai.com/v1"

OPENAI_API_KEYS = os.environ.get("OPENAI_API_KEYS", "")
OPENAI_API_KEYS = OPENAI_API_KEYS if OPENAI_API_KEYS != "" else OPENAI_API_KEY

OPENAI_API_KEYS = [url.strip() for url in OPENAI_API_KEYS.split(";")]
OPENAI_API_KEYS = RuntimeConfig("OPENAI_API_KEYS", "openai.api_keys", OPENAI_API_KEYS)

OPENAI_API_BASE_URLS = os.environ.get("OPENAI_API_BASE_URLS", "")
OPENAI_API_BASE_URLS = (
    OPENAI_API_BASE_URLS if OPENAI_API_BASE_URLS != "" else OPENAI_API_BASE_URL
)

OPENAI_API_BASE_URLS = [
    url.strip() if url != "" else "https://api.openai.com/v1"
    for url in OPENAI_API_BASE_URLS.split(";")
]
OPENAI_API_BASE_URLS = RuntimeConfig(
    "OPENAI_API_BASE_URLS", "openai.api_base_urls", OPENAI_API_BASE_URLS
)

OPENAI_API_CONFIGS = RuntimeConfig(
    "OPENAI_API_CONFIGS",
    "openai.api_configs",
    {},
)

# Get the actual OpenAI API key based on the base URL
OPENAI_API_KEY = ""
try:
    OPENAI_API_KEY = OPENAI_API_KEYS.value[
        OPENAI_API_BASE_URLS.value.index("https://api.openai.com/v1")
    ]
except Exception:
    pass
OPENAI_API_BASE_URL = "https://api.openai.com/v1"

####################################
# TOOL_SERVERS
####################################


TOOL_SERVER_CONNECTIONS = RuntimeConfig(
    "TOOL_SERVER_CONNECTIONS",
    "tool_server.connections",
    [],
)

####################################
# WEBUI
####################################


WEBUI_URL = RuntimeConfig(
    "WEBUI_URL", "webui.url", os.environ.get("WEBUI_URL", "http://localhost:3000")
)


ENABLE_SIGNUP = RuntimeConfig(
    "ENABLE_SIGNUP",
    "ui.enable_signup",
    (
        False
        if not WEBUI_AUTH
        else os.environ.get("ENABLE_SIGNUP", "True").lower() == "true"
    ),
)

ENABLE_LOGIN_FORM = RuntimeConfig(
    "ENABLE_LOGIN_FORM",
    "ui.ENABLE_LOGIN_FORM",
    os.environ.get("ENABLE_LOGIN_FORM", "True").lower() == "true",
)

# 共有 PC 配備向け: False にするとログインフォームの autocomplete 属性を抑止方向に
# 切り替える(form 全体 `autocomplete="off"` + パスワード欄 `new-password` + 他欄 `off`)。
# 既定 True = 現状維持。
# **効果の範囲を誤解しないこと**: これで抑えられるのは「保存済み資格情報のオートフィル」まで。
# 「パスワードを保存しますか」プロンプト自体は Web 側からは抑止できない
# (Chrome/Edge は password 欄の autocomplete=off を意図的に無視する仕様)。
# 端末で確実に止めるにはブラウザポリシー(`PasswordManagerEnabled=false` の GPO/Intune 等)が要る。
# 切り分けの詳細は `_memos/auth-profile.md`「共有 PC 配備」節。
ENABLE_LOGIN_AUTOCOMPLETE = RuntimeConfig(
    "ENABLE_LOGIN_AUTOCOMPLETE",
    "ui.enable_login_autocomplete",
    os.environ.get("ENABLE_LOGIN_AUTOCOMPLETE", "True").lower() == "true",
)


DEFAULT_LOCALE = RuntimeConfig(
    "DEFAULT_LOCALE",
    "ui.default_locale",
    os.environ.get("DEFAULT_LOCALE", ""),
)

DEFAULT_MODELS = RuntimeConfig(
    "DEFAULT_MODELS", "ui.default_models", os.environ.get("DEFAULT_MODELS", None)
)

DEFAULT_PROMPT_SUGGESTIONS = RuntimeConfig(
    "DEFAULT_PROMPT_SUGGESTIONS",
    "ui.prompt_suggestions",
    [
        {
            "title": ["Help me study", "vocabulary for a college entrance exam"],
            "content": "Help me study vocabulary: write a sentence for me to fill in the blank, and I'll try to pick the correct option.",
        },
        {
            "title": ["Give me ideas", "for what to do with my kids' art"],
            "content": "What are 5 creative things I could do with my kids' art? I don't want to throw them away, but it's also so much clutter.",
        },
        {
            "title": ["Tell me a fun fact", "about the Roman Empire"],
            "content": "Tell me a random fun fact about the Roman Empire",
        },
        {
            "title": ["Show me a code snippet", "of a website's sticky header"],
            "content": "Show me a code snippet of a website's sticky header in CSS and JavaScript.",
        },
        {
            "title": [
                "Explain options trading",
                "if I'm familiar with buying and selling stocks",
            ],
            "content": "Explain options trading in simple terms if I'm familiar with buying and selling stocks.",
        },
        {
            "title": ["Overcome procrastination", "give me tips"],
            "content": "Could you start by asking me about instances when I procrastinate the most and then give me some suggestions to overcome it?",
        },
    ],
)

MODEL_ORDER_LIST = RuntimeConfig(
    "MODEL_ORDER_LIST",
    "ui.model_order_list",
    [],
)

DEFAULT_USER_ROLE = RuntimeConfig(
    "DEFAULT_USER_ROLE",
    "ui.default_user_role",
    os.getenv("DEFAULT_USER_ROLE", "pending"),
)

# llens(#66): 権限は3層(admin / skill editor / user)。
# workspace.skills = skill editor ロール(全 skill の CRUD + use-ACL 編集)。
# 旧 workspace.models / workspace.tools / workspace.knowledge と sharing.* は撤去済み
# (model/tool/function の定義変更は admin 専有 — _memos/permissions.md)。
USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS = (
    os.environ.get("USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS", "False").lower()
    == "true"
)


# llens: chat.controls フラグは廃止 — Controls サイドバー(Valves/System Prompt/
# Advanced Params)は 2c4e63d07 で撤去済みで、ゲート対象が存在しない死にフラグだった。

USER_PERMISSIONS_CHAT_FILE_UPLOAD = (
    os.environ.get("USER_PERMISSIONS_CHAT_FILE_UPLOAD", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_DELETE = (
    os.environ.get("USER_PERMISSIONS_CHAT_DELETE", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_EDIT = (
    os.environ.get("USER_PERMISSIONS_CHAT_EDIT", "True").lower() == "true"
)

# llens(#41): チャット共有と共有チャットの clone(_memos/chat-share-files.md)。
# 院内運用は share 中心のため clone は既定 off。
USER_PERMISSIONS_CHAT_SHARE = (
    os.environ.get("USER_PERMISSIONS_CHAT_SHARE", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_CLONE = (
    os.environ.get("USER_PERMISSIONS_CHAT_CLONE", "False").lower() == "true"
)

# llens(#66): chat.stt / chat.tts / chat.call フラグは廃止(STT/TTS は全員使用可 —
# 設定は admin、tool から呼ぶ内部 API は #53)。
# llens(#108): chat.multiple_models も機能(マルチモデル送信)ごと廃止。


DEFAULT_USER_PERMISSIONS = {
    "workspace": {
        "skills": USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS,
    },
    "chat": {
        "file_upload": USER_PERMISSIONS_CHAT_FILE_UPLOAD,
        "delete": USER_PERMISSIONS_CHAT_DELETE,
        "edit": USER_PERMISSIONS_CHAT_EDIT,
        "share": USER_PERMISSIONS_CHAT_SHARE,
        "clone": USER_PERMISSIONS_CHAT_CLONE,
    },
}

USER_PERMISSIONS = RuntimeConfig(
    "USER_PERMISSIONS",
    "user.permissions",
    DEFAULT_USER_PERMISSIONS,
)

# llens(#55): ユーザー追加 meta(仮想カラム)の宣言。外部システムへの参照権限マーカー
# (カルテ利用者ID 等)を User.info に載せるためのキー定義。値は文字列のみ・OWUI は解釈しない。
# 例: ADDITIONAL_USER_META='[{"key": "carte_user_id", "label": "カルテ利用者ID"}]'
# 宣言したキーだけが admin のユーザー編集モーダル/一覧に現れる。label 省略時は key を表示。
try:
    additional_user_meta = [
        {"key": str(field["key"]), "label": str(field.get("label") or field["key"])}
        for field in json.loads(os.environ.get("ADDITIONAL_USER_META", "[]"))
        if isinstance(field, dict) and field.get("key")
    ]
except Exception as e:
    log.exception(f"Error loading ADDITIONAL_USER_META: {e}")
    additional_user_meta = []

ADDITIONAL_USER_META = RuntimeConfig(
    "ADDITIONAL_USER_META",
    "user.additional_meta",
    additional_user_meta,
)

# llens(#48): evaluation/arena は撲滅(config・モデル注入・chat 経路・UI を全撤去)。

WEBHOOK_URL = RuntimeConfig(
    "WEBHOOK_URL", "webhook_url", os.environ.get("WEBHOOK_URL", "")
)

# llens: ENABLE_ADMIN_EXPORT / ENABLE_ADMIN_CHAT_ACCESS は撤去(ken 決定 2026-10-03)。
# 管理者は常にすべてできる — 「管理者の中で見れる人と見れない人が分かれるなら別の権限が要る」。

# llens(#10): openwebui.com コミュニティ共有(ストア導線)は外部サービス前提のため機能ごと撤去。
# llens(#48): 評価(👍/👎)機能撤去に伴い ENABLE_MESSAGE_RATING を廃止。

# llens: 既定 OFF — 通知 webhook は任意の外部連携(必要な配備だけ明示的に有効化する)
ENABLE_USER_WEBHOOKS = RuntimeConfig(
    "ENABLE_USER_WEBHOOKS",
    "ui.enable_user_webhooks",
    os.environ.get("ENABLE_USER_WEBHOOKS", "False").lower() == "true",
)


def validate_cors_origins(origins):
    for origin in origins:
        if origin != "*":
            validate_cors_origin(origin)


def validate_cors_origin(origin):
    parsed_url = urlparse(origin)

    # Check if the scheme is either http or https
    if parsed_url.scheme not in ["http", "https"]:
        raise ValueError(
            f"Invalid scheme in CORS_ALLOW_ORIGIN: '{origin}'. Only 'http' and 'https' are allowed."
        )

    # Ensure that the netloc (domain + port) is present, indicating it's a valid URL
    if not parsed_url.netloc:
        raise ValueError(f"Invalid URL structure in CORS_ALLOW_ORIGIN: '{origin}'.")


# For production, you should only need one host as
# fastapi serves the svelte-kit built frontend and backend from the same host and port.
# To test CORS_ALLOW_ORIGIN locally, you can set something like
# CORS_ALLOW_ORIGIN=http://localhost:5173;http://localhost:8080
# in your .env file depending on your frontend port, 5173 in this case.
# llens(2026-10-03 ken 決定): 空文字は「CORS を一切許さない = 同一 origin のみ」。UI は同一
# origin で配信され cross-origin の API 利用は無いので、機ごとの origin を env に書く knob を
# 持たずに済む(compose に `CORS_ALLOW_ORIGIN=` と literal で書く)。空の要素は捨てる。
def parse_cors_allow_origin(raw: str) -> list[str]:
    """`;` 区切りの origin の list。空要素は捨てる(空文字 → `[]` = 同一 origin のみ)。
    `*` は WARNING(本番非推奨)。不正な origin は ValueError(起動を止める)。"""
    origins = [o.strip() for o in (raw or "").split(";") if o.strip()]
    if "*" in origins:
        log.warning(
            "\n\nWARNING: CORS_ALLOW_ORIGIN IS SET TO '*' - NOT RECOMMENDED FOR PRODUCTION DEPLOYMENTS.\n"
        )
    validate_cors_origins(origins)
    return origins


CORS_ALLOW_ORIGIN = parse_cors_allow_origin(os.environ.get("CORS_ALLOW_ORIGIN", "*"))


class BannerModel(BaseModel):
    id: str
    type: str
    title: Optional[str] = None
    content: str
    dismissible: bool
    timestamp: int


try:
    banners = json.loads(os.environ.get("WEBUI_BANNERS", "[]"))
    banners = [BannerModel(**banner) for banner in banners]
except Exception as e:
    log.exception(f"Error loading WEBUI_BANNERS: {e}")
    banners = []

WEBUI_BANNERS = RuntimeConfig("WEBUI_BANNERS", "ui.banners", banners)


####################################
# TASKS
####################################


TASK_MODEL = RuntimeConfig(
    "TASK_MODEL",
    "task.model.default",
    os.environ.get("TASK_MODEL", ""),
)

TASK_MODEL_EXTERNAL = RuntimeConfig(
    "TASK_MODEL_EXTERNAL",
    "task.model.external",
    os.environ.get("TASK_MODEL_EXTERNAL", ""),
)

TITLE_GENERATION_PROMPT_TEMPLATE = RuntimeConfig(
    "TITLE_GENERATION_PROMPT_TEMPLATE",
    "task.title.prompt_template",
    os.environ.get("TITLE_GENERATION_PROMPT_TEMPLATE", ""),
)

DEFAULT_TITLE_GENERATION_PROMPT_TEMPLATE = """### Task:
Generate a concise, 3-5 word title with an emoji summarizing the chat history.
### Guidelines:
- The title should clearly represent the main theme or subject of the conversation.
- Use emojis that enhance understanding of the topic, but avoid quotation marks or special formatting.
- Write the title in the chat's primary language; default to English if multilingual.
- Prioritize accuracy over excessive creativity; keep it clear and simple.
### Output:
JSON format: { "title": "your concise title here" }
### Examples:
- { "title": "📉 Stock Market Trends" },
- { "title": "🍪 Perfect Chocolate Chip Recipe" },
- { "title": "Evolution of Music Streaming" },
- { "title": "Remote Work Productivity Tips" },
- { "title": "Artificial Intelligence in Healthcare" },
- { "title": "🎮 Video Game Development Insights" }
### Chat History:
<chat_history>
{{MESSAGES:END:2}}
</chat_history>"""

# llens(#67): chat tag は機能ごと撤去(TAGS_GENERATION 系 config も削除)

ENABLE_TITLE_GENERATION = RuntimeConfig(
    "ENABLE_TITLE_GENERATION",
    "task.title.enable",
    os.environ.get("ENABLE_TITLE_GENERATION", "True").lower() == "true",
)


# llens: 非 native(プロンプトベース)tool calling 経路は削除済み(issue #18)。
# TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE / DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE も撤去。
# llens(#67): autocomplete(入力補完)/ emoji / moa の生成機能は endpoint・config ごと撤去。


####################################
# Code Interpreter
####################################

# llens(#67): CODE_EXECUTION_*(UI の Run ボタン → /utils/code/execute)は
# 機能ごと撤去。コード実行は nsjail + IPython の外部サンドボックス
# (自前ツール側)一本(issue #11 の保留分を完全削除)。

# llens(#23): 組み込み code_interpreter(タグ検出による tool 外コード実行)も
# 機能ごと撤去した。CODE_INTERPRETER_* config は存在しない。


####################################
# Information Retrieval (RAG)
####################################


# llens(#54): Google Drive / OneDrive 統合(外部クラウドからの添付取り込み)は
# 機能ごと撤去した(薄い業務基盤に不要)。

# llens(#52): 文書抽出は fork 本体にコードを持たない。docling 等の外部エンジンも、docling で
# ない「素の」md 化(markitdown)も、prism の Attachment Function が直接担う。fork は抽出用の
# config を持たず、markitdown を同梱するだけ(prism が import して使う)。

# llens(#52): 添付ファイルはベクタ検索せず全文注入する(issue #9、fork.md §5)。RAG 検索の
# config(top_k / reranker / relevance / hybrid / full_context / bypass)は撤去済み。
# embedding/reranking モデルの起動時ロードは main.py で無条件にスキップ(起動時のモデル外部 DL を排除)。
# llens(#67): memories も撤去し vector/embedding 基盤の消費者が全滅したため、
# 防御的に残していた app.state.EMBEDDING_FUNCTION も撤去済み。

# llens(#52): 添付ファイルの受け入れゲート(upload の入口=Attachment 前で server-side 強制)。
ATTACHMENT_MAX_COUNT = RuntimeConfig(
    "ATTACHMENT_MAX_COUNT",
    "attachment.max_count",
    env_int("ATTACHMENT_MAX_COUNT", None, minimum=0),
)

# 受け入れ上限(MB)。既定 25MB。sandbox の SANDBOX_FILE_LIMIT_BYTES(既定 25MB)とは別 config
# だが、原本をサンドボックスへ渡す運用では同値に揃えるのが筋。
ATTACHMENT_MAX_SIZE_MB = RuntimeConfig(
    "ATTACHMENT_MAX_SIZE_MB",
    "attachment.max_size",
    env_int("ATTACHMENT_MAX_SIZE_MB", 25, minimum=0),
)

# llens: 添付画像のクライアント側縮小。長辺がこの px を超える画像はアップロード前に
# アスペクト比維持で収まるよう縮小される(MIME は維持)。未設定/0 = 縮小しない。
ATTACHMENT_IMAGE_MAX_DIMENSION = RuntimeConfig(
    "ATTACHMENT_IMAGE_MAX_DIMENSION",
    "attachment.image_max_dimension",
    env_int("ATTACHMENT_IMAGE_MAX_DIMENSION", None, minimum=0),
)

# llens: 長文ペーストの .txt ファイル化閾値(文字数)。ペーストがこれを超えると
# .txt 添付に変換される。未設定/0 = 変換しない(配備設定のみ — per-user トグルは廃止)。
PASTED_TEXT_CHARACTER_LIMIT = RuntimeConfig(
    "PASTED_TEXT_CHARACTER_LIMIT",
    "attachment.pasted_text_character_limit",
    env_int("PASTED_TEXT_CHARACTER_LIMIT", None, minimum=0),
)

# 受け入れ拡張子の whitelist(小文字・ドット無し、カンマ区切り env)。空 = 全許可(非破壊の既定)。
ATTACHMENT_ALLOWED_EXTENSIONS = RuntimeConfig(
    "ATTACHMENT_ALLOWED_EXTENSIONS",
    "attachment.allowed_extensions",
    [
        ext.strip().lower().lstrip(".")
        for ext in os.environ.get("ATTACHMENT_ALLOWED_EXTENSIONS", "").split(",")
        if ext.strip()
    ],
)

# llens(#69): グローバル Attachment Function(admin が 1 つ選ぶ)。per-model の
# meta.attachmentIds 割り当ては廃止(複数同時有効は content 上書き衝突のため単一 id)。
# 空文字 = 未設定(何も発火しない)。
ATTACHMENT_FUNCTION_ID = RuntimeConfig(
    "ATTACHMENT_FUNCTION_ID",
    "attachment.function_id",
    os.environ.get("ATTACHMENT_FUNCTION_ID", ""),
)


# llens(#52): reranking(ベクタ検索の再ランキング)は RAG 撤去に伴い廃止。config・
# 起動時ロード(rf)ともに撤去済み。
# tiktoken(TIKTOKEN_CACHE_DIR / TIKTOKEN_ENCODING_NAME)も読む側が無く撤去。
# トークン数はモデルサーバーが返す usage を使う(models/chats.py)。


####################################
# Audio
####################################

# llens(#53 フォロー): 配備レベルの機能フラグ。#66 で廃止した per-user 権限フラグ
# (誰に使わせるか)とは別物で、「この配備に STT/TTS 機能があるか」を表す。
# False でアイコン非表示(FE)+ /transcriptions・/speech を 403(サーバー側 enforcement)。
ENABLE_AUDIO_STT = RuntimeConfig(
    "ENABLE_AUDIO_STT",
    "audio.stt.enable",
    os.getenv("ENABLE_AUDIO_STT", "True").lower() == "true",
)

ENABLE_AUDIO_TTS = RuntimeConfig(
    "ENABLE_AUDIO_TTS",
    "audio.tts.enable",
    os.getenv("ENABLE_AUDIO_TTS", "True").lower() == "true",
)

# Transcription
WHISPER_MODEL = RuntimeConfig(
    "WHISPER_MODEL",
    "audio.stt.whisper_model",
    os.getenv("WHISPER_MODEL", "base"),
)

WHISPER_MODEL_DIR = os.getenv("WHISPER_MODEL_DIR", f"{CACHE_DIR}/whisper/models")
WHISPER_MODEL_AUTO_UPDATE = (
    not OFFLINE_MODE
    and os.environ.get("WHISPER_MODEL_AUTO_UPDATE", "").lower() == "true"
)

WHISPER_VAD_FILTER = RuntimeConfig(
    "WHISPER_VAD_FILTER",
    "audio.stt.whisper_vad_filter",
    os.getenv("WHISPER_VAD_FILTER", "False").lower() == "true",
)

# llens 追加(issue #12 キャッチアップ、_memos/prod-config-snapshot.md):
# 認識言語の固定(空 = 自動判定)。北大運用では ja 固定を推奨
WHISPER_LANGUAGE = RuntimeConfig(
    "WHISPER_LANGUAGE",
    "audio.stt.whisper_language",
    os.getenv("WHISPER_LANGUAGE", ""),
)

# llens 追加: faster-whisper の compute_type(従来は int8 固定だった)
WHISPER_COMPUTE_TYPE = RuntimeConfig(
    "WHISPER_COMPUTE_TYPE",
    "audio.stt.whisper_compute_type",
    os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
)

# llens 追加(#53 フォロー): faster-whisper のスレッド系つまみ(強い CPU 向け)。
# CPU_THREADS = 1 本の文字起こしの計算スレッド数(0 = CTranslate2 の既定。
#   既定は環境依存で強い CPU を使い切る保証がないため、本気運用では明示指定)
# NUM_WORKERS = 同時に走れる文字起こしの本数(既定 1 = 並列リクエストは
#   モデル内で直列化される。CTranslate2 は推論中 GIL を解放するので >1 で真の並列)
WHISPER_CPU_THREADS = RuntimeConfig(
    "WHISPER_CPU_THREADS",
    "audio.stt.whisper_cpu_threads",
    env_int("WHISPER_CPU_THREADS", 0, minimum=0),
)

WHISPER_NUM_WORKERS = RuntimeConfig(
    "WHISPER_NUM_WORKERS",
    "audio.stt.whisper_num_workers",
    env_int("WHISPER_NUM_WORKERS", 1, minimum=1),
)


def env_number(name: str, default, minimum, maximum):
    """env 専用の数値設定を読む。未設定は既定、非数・範囲外は warning を出して既定に落とす。

    型は default の型(int / float)に合わせる。
    """
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        value = type(default)(raw)
    except ValueError:
        value = None
    # NaN は比較が常に偽になるので、範囲の内側にあることを肯定形で確かめる
    if value is None or not (minimum <= value <= maximum):
        log.warning(
            f"{name}={raw!r} is not a number in {minimum}..{maximum}; "
            f"using the default {default}"
        )
        return default
    return value


# llens: 配備先(マイク・部屋の雑音・CPU の速さ)で調整が要る音声入力の値。**env 専用** —
# 配備の設定ファイルで決める値なので RuntimeConfig(DB 保存・管理画面)にはしない。
# 既定値の根拠と動かし方は _memos/audio.md「配備ごとの調整」。
# 上の 4 つは /api/config の audio.stt で FE(録音バー)へ渡す。
# - SEGMENT_SECONDS: 区切りの目安秒。これを過ぎたら次の息継ぎで切る(強制切りは + 4 秒で固定)
# - SEGMENT_PAUSE_MS: 息継ぎとみなす無音の長さ
# - SILENCE_THRESHOLD_DB: 無音判定のしきい値(AnalyserNode の minDecibels)。入力の音量に依存する
# - RECORDING_BITRATE_KBPS: 通し録音(添付に残す原本)のビットレート
AUDIO_STT_SEGMENT_SECONDS = env_number("AUDIO_STT_SEGMENT_SECONDS", 12.0, 4, 30)
AUDIO_STT_SEGMENT_PAUSE_MS = env_number("AUDIO_STT_SEGMENT_PAUSE_MS", 400, 100, 2000)
AUDIO_STT_SILENCE_THRESHOLD_DB = env_number(
    "AUDIO_STT_SILENCE_THRESHOLD_DB", -45.0, -90, -10
)
AUDIO_STT_RECORDING_BITRATE_KBPS = env_number(
    "AUDIO_STT_RECORDING_BITRATE_KBPS", 32, 8, 128
)
# 内蔵 faster-whisper の beam size(外部 STT エンジンには効かない)
WHISPER_BEAM_SIZE = env_number("WHISPER_BEAM_SIZE", 5, 1, 10)


# Add Deepgram configuration
DEEPGRAM_API_KEY = RuntimeConfig(
    "DEEPGRAM_API_KEY",
    "audio.stt.deepgram.api_key",
    os.getenv("DEEPGRAM_API_KEY", ""),
)


AUDIO_STT_OPENAI_API_BASE_URL = RuntimeConfig(
    "AUDIO_STT_OPENAI_API_BASE_URL",
    "audio.stt.openai.api_base_url",
    os.getenv("AUDIO_STT_OPENAI_API_BASE_URL", OPENAI_API_BASE_URL),
)

AUDIO_STT_OPENAI_API_KEY = RuntimeConfig(
    "AUDIO_STT_OPENAI_API_KEY",
    "audio.stt.openai.api_key",
    os.getenv("AUDIO_STT_OPENAI_API_KEY", OPENAI_API_KEY),
)

AUDIO_STT_ENGINE = RuntimeConfig(
    "AUDIO_STT_ENGINE",
    "audio.stt.engine",
    os.getenv("AUDIO_STT_ENGINE", ""),
)

AUDIO_STT_MODEL = RuntimeConfig(
    "AUDIO_STT_MODEL",
    "audio.stt.model",
    os.getenv("AUDIO_STT_MODEL", ""),
)

AUDIO_STT_AZURE_API_KEY = RuntimeConfig(
    "AUDIO_STT_AZURE_API_KEY",
    "audio.stt.azure.api_key",
    os.getenv("AUDIO_STT_AZURE_API_KEY", ""),
)

AUDIO_STT_AZURE_REGION = RuntimeConfig(
    "AUDIO_STT_AZURE_REGION",
    "audio.stt.azure.region",
    os.getenv("AUDIO_STT_AZURE_REGION", ""),
)

AUDIO_STT_AZURE_LOCALES = RuntimeConfig(
    "AUDIO_STT_AZURE_LOCALES",
    "audio.stt.azure.locales",
    os.getenv("AUDIO_STT_AZURE_LOCALES", ""),
)

AUDIO_TTS_OPENAI_API_BASE_URL = RuntimeConfig(
    "AUDIO_TTS_OPENAI_API_BASE_URL",
    "audio.tts.openai.api_base_url",
    os.getenv("AUDIO_TTS_OPENAI_API_BASE_URL", OPENAI_API_BASE_URL),
)
AUDIO_TTS_OPENAI_API_KEY = RuntimeConfig(
    "AUDIO_TTS_OPENAI_API_KEY",
    "audio.tts.openai.api_key",
    os.getenv("AUDIO_TTS_OPENAI_API_KEY", OPENAI_API_KEY),
)

AUDIO_TTS_API_KEY = RuntimeConfig(
    "AUDIO_TTS_API_KEY",
    "audio.tts.api_key",
    os.getenv("AUDIO_TTS_API_KEY", ""),
)

AUDIO_TTS_ENGINE = RuntimeConfig(
    "AUDIO_TTS_ENGINE",
    "audio.tts.engine",
    os.getenv("AUDIO_TTS_ENGINE", ""),
)


AUDIO_TTS_MODEL = RuntimeConfig(
    "AUDIO_TTS_MODEL",
    "audio.tts.model",
    os.getenv("AUDIO_TTS_MODEL", "tts-1"),  # OpenAI default model
)

AUDIO_TTS_VOICE = RuntimeConfig(
    "AUDIO_TTS_VOICE",
    "audio.tts.voice",
    os.getenv("AUDIO_TTS_VOICE", "alloy"),  # OpenAI default voice
)

AUDIO_TTS_SPLIT_ON = RuntimeConfig(
    "AUDIO_TTS_SPLIT_ON",
    "audio.tts.split_on",
    os.getenv("AUDIO_TTS_SPLIT_ON", "punctuation"),
)

AUDIO_TTS_AZURE_SPEECH_REGION = RuntimeConfig(
    "AUDIO_TTS_AZURE_SPEECH_REGION",
    "audio.tts.azure.speech_region",
    os.getenv("AUDIO_TTS_AZURE_SPEECH_REGION", "eastus"),
)

AUDIO_TTS_AZURE_SPEECH_OUTPUT_FORMAT = RuntimeConfig(
    "AUDIO_TTS_AZURE_SPEECH_OUTPUT_FORMAT",
    "audio.tts.azure.speech_output_format",
    os.getenv(
        "AUDIO_TTS_AZURE_SPEECH_OUTPUT_FORMAT", "audio-24khz-160kbitrate-mono-mp3"
    ),
)


####################################
# LDAP
####################################

ENABLE_LDAP = RuntimeConfig(
    "ENABLE_LDAP",
    "ldap.enable",
    os.environ.get("ENABLE_LDAP", "false").lower() == "true",
)

LDAP_SERVER_LABEL = RuntimeConfig(
    "LDAP_SERVER_LABEL",
    "ldap.server.label",
    os.environ.get("LDAP_SERVER_LABEL", "LDAP Server"),
)

LDAP_SERVER_HOST = RuntimeConfig(
    "LDAP_SERVER_HOST",
    "ldap.server.host",
    os.environ.get("LDAP_SERVER_HOST", "localhost"),
)

LDAP_SERVER_PORT = RuntimeConfig(
    "LDAP_SERVER_PORT",
    "ldap.server.port",
    env_int("LDAP_SERVER_PORT", 389, minimum=1),
)

LDAP_ATTRIBUTE_FOR_MAIL = RuntimeConfig(
    "LDAP_ATTRIBUTE_FOR_MAIL",
    "ldap.server.attribute_for_mail",
    os.environ.get("LDAP_ATTRIBUTE_FOR_MAIL", "mail"),
)

LDAP_ATTRIBUTE_FOR_USERNAME = RuntimeConfig(
    "LDAP_ATTRIBUTE_FOR_USERNAME",
    "ldap.server.attribute_for_username",
    os.environ.get("LDAP_ATTRIBUTE_FOR_USERNAME", "uid"),
)

LDAP_APP_DN = RuntimeConfig(
    "LDAP_APP_DN", "ldap.server.app_dn", os.environ.get("LDAP_APP_DN", "")
)

LDAP_APP_PASSWORD = RuntimeConfig(
    "LDAP_APP_PASSWORD",
    "ldap.server.app_password",
    os.environ.get("LDAP_APP_PASSWORD", ""),
)

LDAP_SEARCH_BASE = RuntimeConfig(
    "LDAP_SEARCH_BASE", "ldap.server.users_dn", os.environ.get("LDAP_SEARCH_BASE", "")
)

LDAP_SEARCH_FILTERS = RuntimeConfig(
    "LDAP_SEARCH_FILTER",
    "ldap.server.search_filter",
    os.environ.get("LDAP_SEARCH_FILTER", ""),
)

LDAP_USE_TLS = RuntimeConfig(
    "LDAP_USE_TLS",
    "ldap.server.use_tls",
    os.environ.get("LDAP_USE_TLS", "True").lower() == "true",
)

LDAP_CA_CERT_FILE = RuntimeConfig(
    "LDAP_CA_CERT_FILE",
    "ldap.server.ca_cert_file",
    os.environ.get("LDAP_CA_CERT_FILE", ""),
)

LDAP_CIPHERS = RuntimeConfig(
    "LDAP_CIPHERS", "ldap.server.ciphers", os.environ.get("LDAP_CIPHERS", "ALL")
)
