import json
import logging
import os
import shutil
import base64
import redis

from datetime import datetime
from pathlib import Path
from typing import Generic, Optional, TypeVar
from urllib.parse import urlparse

import requests
from pydantic import BaseModel
from sqlalchemy import JSON, Column, DateTime, Integer, func

from open_webui.env import (
    DATA_DIR,
    DATABASE_URL,
    ENV,
    REDIS_URL,
    REDIS_SENTINEL_HOSTS,
    REDIS_SENTINEL_PORT,
    FRONTEND_BUILD_DIR,
    OFFLINE_MODE,
    OPEN_WEBUI_DIR,
    WEBUI_AUTH,
    WEBUI_FAVICON_URL,
    WEBUI_NAME,
    log,
)
from open_webui.internal.db import Base, get_db
from open_webui.utils.redis import get_redis_connection


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

        alembic_cfg = Config(OPEN_WEBUI_DIR / "alembic.ini")

        # Set the script location dynamically
        migrations_path = OPEN_WEBUI_DIR / "migrations"
        alembic_cfg.set_main_option("script_location", str(migrations_path))

        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        log.exception(f"Error running migrations: {e}")


run_migrations()


class Config(Base):
    __tablename__ = "config"

    id = Column(Integer, primary_key=True)
    data = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())


def load_json_config():
    with open(f"{DATA_DIR}/config.json", "r") as file:
        return json.load(file)


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


# When initializing, check if config.json exists and migrate it to the database
if os.path.exists(f"{DATA_DIR}/config.json"):
    data = load_json_config()
    save_to_db(data)
    os.rename(f"{DATA_DIR}/config.json", f"{DATA_DIR}/old_config.json")

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


PERSISTENT_CONFIG_REGISTRY = []


def save_config(config):
    global CONFIG_DATA
    global PERSISTENT_CONFIG_REGISTRY
    try:
        save_to_db(config)
        CONFIG_DATA = config

        # Trigger updates on all registered RuntimeConfig entries
        for config_item in PERSISTENT_CONFIG_REGISTRY:
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
        # llens #30: env を単一の正の源とする。起動時は env が必ず DB(config テーブル)より
        # 優先される(旧来は DB 値があれば DB 勝ち=「env を変えたのに反映されない」#30 の温床)。
        # 実行中の UI 変更は DB へ永続し稼働中は有効(複数ワーカーで共有)だが、次の起動で
        # env が再び上書きする。恒久変更の唯一の経路は env/compose。
        self.value = env_value

        PERSISTENT_CONFIG_REGISTRY.append(self)

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
    _state: dict[str, RuntimeConfig]
    _redis: Optional[redis.Redis] = None

    def __init__(
        self, redis_url: Optional[str] = None, redis_sentinels: Optional[list] = []
    ):
        super().__setattr__("_state", {})
        if redis_url:
            super().__setattr__(
                "_redis",
                get_redis_connection(redis_url, redis_sentinels, decode_responses=True),
            )

    def __setattr__(self, key, value):
        if isinstance(value, RuntimeConfig):
            self._state[key] = value
        else:
            self._state[key].value = value
            self._state[key].save()

            if self._redis:
                redis_key = f"open-webui:config:{key}"
                self._redis.set(redis_key, json.dumps(self._state[key].value))

    def __getattr__(self, key):
        if key not in self._state:
            raise AttributeError(f"Config key '{key}' not found")

        # If Redis is available, check for an updated value
        if self._redis:
            redis_key = f"open-webui:config:{key}"
            redis_value = self._redis.get(redis_key)

            if redis_value is not None:
                try:
                    decoded_value = json.loads(redis_value)

                    # Update the in-memory value if different
                    if self._state[key].value != decoded_value:
                        self._state[key].value = decoded_value
                        log.info(f"Updated {key} from Redis: {decoded_value}")

                except json.JSONDecodeError:
                    log.error(f"Invalid JSON format in Redis for {key}: {redis_value}")

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


JWT_EXPIRES_IN = RuntimeConfig(
    "JWT_EXPIRES_IN", "auth.jwt_expiry", os.environ.get("JWT_EXPIRES_IN", "-1")
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
    os.environ.get("OAUTH_GROUP_CLAIM", "groups"),
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

STATIC_DIR = Path(os.getenv("STATIC_DIR", OPEN_WEBUI_DIR / "static")).resolve()

for file_path in (FRONTEND_BUILD_DIR / "static").glob("**/*"):
    if file_path.is_file():
        target_path = STATIC_DIR / file_path.relative_to(
            (FRONTEND_BUILD_DIR / "static")
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(file_path, target_path)
        except Exception as e:
            logging.error(f"An error occurred: {e}")

frontend_favicon = FRONTEND_BUILD_DIR / "static" / "favicon.png"

if frontend_favicon.exists():
    try:
        shutil.copyfile(frontend_favicon, STATIC_DIR / "favicon.png")
    except Exception as e:
        logging.error(f"An error occurred: {e}")

frontend_splash = FRONTEND_BUILD_DIR / "static" / "splash.png"

if frontend_splash.exists():
    try:
        shutil.copyfile(frontend_splash, STATIC_DIR / "splash.png")
    except Exception as e:
        logging.error(f"An error occurred: {e}")

frontend_loader = FRONTEND_BUILD_DIR / "static" / "loader.js"

if frontend_loader.exists():
    try:
        shutil.copyfile(frontend_loader, STATIC_DIR / "loader.js")
    except Exception as e:
        logging.error(f"An error occurred: {e}")


####################################
# CUSTOM_NAME (Legacy)
####################################

CUSTOM_NAME = os.environ.get("CUSTOM_NAME", "")

if CUSTOM_NAME:
    try:
        r = requests.get(f"https://api.openwebui.com/api/v1/custom/{CUSTOM_NAME}")
        data = r.json()
        if r.ok:
            if "logo" in data:
                WEBUI_FAVICON_URL = url = (
                    f"https://api.openwebui.com{data['logo']}"
                    if data["logo"][0] == "/"
                    else data["logo"]
                )

                r = requests.get(url, stream=True)
                if r.status_code == 200:
                    with open(f"{STATIC_DIR}/favicon.png", "wb") as f:
                        r.raw.decode_content = True
                        shutil.copyfileobj(r.raw, f)

            if "splash" in data:
                url = (
                    f"https://api.openwebui.com{data['splash']}"
                    if data["splash"][0] == "/"
                    else data["splash"]
                )

                r = requests.get(url, stream=True)
                if r.status_code == 200:
                    with open(f"{STATIC_DIR}/splash.png", "wb") as f:
                        r.raw.decode_content = True
                        shutil.copyfileobj(r.raw, f)

            WEBUI_NAME = data["name"]
    except Exception as e:
        log.exception(e)
        pass


####################################
# LICENSE_KEY
####################################

LICENSE_KEY = os.environ.get("LICENSE_KEY", "")

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
# DIRECT CONNECTIONS
####################################

ENABLE_DIRECT_CONNECTIONS = RuntimeConfig(
    "ENABLE_DIRECT_CONNECTIONS",
    "direct.enable",
    os.environ.get("ENABLE_DIRECT_CONNECTIONS", "True").lower() == "true",
)

####################################
# OLLAMA_BASE_URL
####################################

ENABLE_OLLAMA_API = RuntimeConfig(
    "ENABLE_OLLAMA_API",
    "ollama.enable",
    os.environ.get("ENABLE_OLLAMA_API", "True").lower() == "true",
)

OLLAMA_API_BASE_URL = os.environ.get(
    "OLLAMA_API_BASE_URL", "http://localhost:11434/api"
)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "")
if OLLAMA_BASE_URL:
    # Remove trailing slash
    OLLAMA_BASE_URL = (
        OLLAMA_BASE_URL[:-1] if OLLAMA_BASE_URL.endswith("/") else OLLAMA_BASE_URL
    )


K8S_FLAG = os.environ.get("K8S_FLAG", "")
USE_OLLAMA_DOCKER = os.environ.get("USE_OLLAMA_DOCKER", "false")

if OLLAMA_BASE_URL == "" and OLLAMA_API_BASE_URL != "":
    OLLAMA_BASE_URL = (
        OLLAMA_API_BASE_URL[:-4]
        if OLLAMA_API_BASE_URL.endswith("/api")
        else OLLAMA_API_BASE_URL
    )

if ENV == "prod":
    if OLLAMA_BASE_URL == "/ollama" and not K8S_FLAG:
        if USE_OLLAMA_DOCKER.lower() == "true":
            # if you use all-in-one docker container (Open WebUI + Ollama)
            # with the docker build arg USE_OLLAMA=true (--build-arg="USE_OLLAMA=true") this only works with http://localhost:11434
            OLLAMA_BASE_URL = "http://localhost:11434"
        else:
            OLLAMA_BASE_URL = "http://host.docker.internal:11434"
    elif K8S_FLAG:
        OLLAMA_BASE_URL = "http://ollama-service.open-webui.svc.cluster.local:11434"


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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE_URL = os.environ.get("GEMINI_API_BASE_URL", "")


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

USER_PERMISSIONS_WORKSPACE_MODELS_ACCESS = (
    os.environ.get("USER_PERMISSIONS_WORKSPACE_MODELS_ACCESS", "False").lower()
    == "true"
)

USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ACCESS = (
    os.environ.get("USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ACCESS", "False").lower()
    == "true"
)

USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS = (
    os.environ.get("USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS", "False").lower() == "true"
)

# Skill は Prompt の後継(issue #35。prompts は退役済み)。
USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS = (
    os.environ.get("USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS", "False").lower()
    == "true"
)

USER_PERMISSIONS_WORKSPACE_MODELS_ALLOW_PUBLIC_SHARING = (
    os.environ.get(
        "USER_PERMISSIONS_WORKSPACE_MODELS_ALLOW_PUBLIC_SHARING", "False"
    ).lower()
    == "true"
)

USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ALLOW_PUBLIC_SHARING = (
    os.environ.get(
        "USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ALLOW_PUBLIC_SHARING", "False"
    ).lower()
    == "true"
)

USER_PERMISSIONS_WORKSPACE_TOOLS_ALLOW_PUBLIC_SHARING = (
    os.environ.get(
        "USER_PERMISSIONS_WORKSPACE_TOOLS_ALLOW_PUBLIC_SHARING", "False"
    ).lower()
    == "true"
)

USER_PERMISSIONS_WORKSPACE_SKILLS_ALLOW_PUBLIC_SHARING = (
    os.environ.get(
        "USER_PERMISSIONS_WORKSPACE_SKILLS_ALLOW_PUBLIC_SHARING", "False"
    ).lower()
    == "true"
)


USER_PERMISSIONS_CHAT_CONTROLS = (
    os.environ.get("USER_PERMISSIONS_CHAT_CONTROLS", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_FILE_UPLOAD = (
    os.environ.get("USER_PERMISSIONS_CHAT_FILE_UPLOAD", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_DELETE = (
    os.environ.get("USER_PERMISSIONS_CHAT_DELETE", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_EDIT = (
    os.environ.get("USER_PERMISSIONS_CHAT_EDIT", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_STT = (
    os.environ.get("USER_PERMISSIONS_CHAT_STT", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_TTS = (
    os.environ.get("USER_PERMISSIONS_CHAT_TTS", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_CALL = (
    os.environ.get("USER_PERMISSIONS_CHAT_CALL", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_MULTIPLE_MODELS = (
    os.environ.get("USER_PERMISSIONS_CHAT_MULTIPLE_MODELS", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_TEMPORARY = (
    os.environ.get("USER_PERMISSIONS_CHAT_TEMPORARY", "True").lower() == "true"
)

USER_PERMISSIONS_CHAT_TEMPORARY_ENFORCED = (
    os.environ.get("USER_PERMISSIONS_CHAT_TEMPORARY_ENFORCED", "False").lower()
    == "true"
)


USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS = (
    os.environ.get("USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS", "False").lower()
    == "true"
)

USER_PERMISSIONS_FEATURES_WEB_SEARCH = (
    os.environ.get("USER_PERMISSIONS_FEATURES_WEB_SEARCH", "True").lower() == "true"
)

USER_PERMISSIONS_FEATURES_IMAGE_GENERATION = (
    os.environ.get("USER_PERMISSIONS_FEATURES_IMAGE_GENERATION", "True").lower()
    == "true"
)

USER_PERMISSIONS_FEATURES_CODE_INTERPRETER = (
    os.environ.get("USER_PERMISSIONS_FEATURES_CODE_INTERPRETER", "True").lower()
    == "true"
)


DEFAULT_USER_PERMISSIONS = {
    "workspace": {
        "models": USER_PERMISSIONS_WORKSPACE_MODELS_ACCESS,
        "knowledge": USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ACCESS,
        "skills": USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS,
        "tools": USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS,
    },
    "sharing": {
        "public_models": USER_PERMISSIONS_WORKSPACE_MODELS_ALLOW_PUBLIC_SHARING,
        "public_knowledge": USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ALLOW_PUBLIC_SHARING,
        "public_skills": USER_PERMISSIONS_WORKSPACE_SKILLS_ALLOW_PUBLIC_SHARING,
        "public_tools": USER_PERMISSIONS_WORKSPACE_TOOLS_ALLOW_PUBLIC_SHARING,
    },
    "chat": {
        "controls": USER_PERMISSIONS_CHAT_CONTROLS,
        "file_upload": USER_PERMISSIONS_CHAT_FILE_UPLOAD,
        "delete": USER_PERMISSIONS_CHAT_DELETE,
        "edit": USER_PERMISSIONS_CHAT_EDIT,
        "stt": USER_PERMISSIONS_CHAT_STT,
        "tts": USER_PERMISSIONS_CHAT_TTS,
        "call": USER_PERMISSIONS_CHAT_CALL,
        "multiple_models": USER_PERMISSIONS_CHAT_MULTIPLE_MODELS,
        "temporary": USER_PERMISSIONS_CHAT_TEMPORARY,
        "temporary_enforced": USER_PERMISSIONS_CHAT_TEMPORARY_ENFORCED,
    },
    "features": {
        "direct_tool_servers": USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS,
        "web_search": USER_PERMISSIONS_FEATURES_WEB_SEARCH,
        "image_generation": USER_PERMISSIONS_FEATURES_IMAGE_GENERATION,
        "code_interpreter": USER_PERMISSIONS_FEATURES_CODE_INTERPRETER,
    },
}

USER_PERMISSIONS = RuntimeConfig(
    "USER_PERMISSIONS",
    "user.permissions",
    DEFAULT_USER_PERMISSIONS,
)

ENABLE_CHANNELS = RuntimeConfig(
    "ENABLE_CHANNELS",
    "channels.enable",
    os.environ.get("ENABLE_CHANNELS", "False").lower() == "true",
)


# llens(#48): evaluation/arena は撲滅(config・モデル注入・chat 経路・UI を全撤去)。

WEBHOOK_URL = RuntimeConfig(
    "WEBHOOK_URL", "webhook_url", os.environ.get("WEBHOOK_URL", "")
)

ENABLE_ADMIN_EXPORT = os.environ.get("ENABLE_ADMIN_EXPORT", "True").lower() == "true"

ENABLE_ADMIN_CHAT_ACCESS = (
    os.environ.get("ENABLE_ADMIN_CHAT_ACCESS", "True").lower() == "true"
)

# llens(#10): openwebui.com コミュニティ共有(ストア導線)は air-gapped 前提のため機能ごと撤去。
# llens(#48): 評価(👍/👎)機能撤去に伴い ENABLE_MESSAGE_RATING を廃止。

ENABLE_USER_WEBHOOKS = RuntimeConfig(
    "ENABLE_USER_WEBHOOKS",
    "ui.enable_user_webhooks",
    os.environ.get("ENABLE_USER_WEBHOOKS", "True").lower() == "true",
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
CORS_ALLOW_ORIGIN = os.environ.get("CORS_ALLOW_ORIGIN", "*").split(";")

if "*" in CORS_ALLOW_ORIGIN:
    log.warning(
        "\n\nWARNING: CORS_ALLOW_ORIGIN IS SET TO '*' - NOT RECOMMENDED FOR PRODUCTION DEPLOYMENTS.\n"
    )

validate_cors_origins(CORS_ALLOW_ORIGIN)


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


SHOW_ADMIN_DETAILS = RuntimeConfig(
    "SHOW_ADMIN_DETAILS",
    "auth.admin.show",
    os.environ.get("SHOW_ADMIN_DETAILS", "true").lower() == "true",
)

ADMIN_EMAIL = RuntimeConfig(
    "ADMIN_EMAIL",
    "auth.admin.email",
    os.environ.get("ADMIN_EMAIL", None),
)


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

TAGS_GENERATION_PROMPT_TEMPLATE = RuntimeConfig(
    "TAGS_GENERATION_PROMPT_TEMPLATE",
    "task.tags.prompt_template",
    os.environ.get("TAGS_GENERATION_PROMPT_TEMPLATE", ""),
)

DEFAULT_TAGS_GENERATION_PROMPT_TEMPLATE = """### Task:
Generate 1-3 broad tags categorizing the main themes of the chat history, along with 1-3 more specific subtopic tags.

### Guidelines:
- Start with high-level domains (e.g. Science, Technology, Philosophy, Arts, Politics, Business, Health, Sports, Entertainment, Education)
- Consider including relevant subfields/subdomains if they are strongly represented throughout the conversation
- If content is too short (less than 3 messages) or too diverse, use only ["General"]
- Use the chat's primary language; default to English if multilingual
- Prioritize accuracy over specificity

### Output:
JSON format: { "tags": ["tag1", "tag2", "tag3"] }

### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>"""

IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE = RuntimeConfig(
    "IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE",
    "task.image.prompt_template",
    os.environ.get("IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE", ""),
)

DEFAULT_IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE = """### Task:
Generate a detailed prompt for am image generation task based on the given language and context. Describe the image as if you were explaining it to someone who cannot see it. Include relevant details, colors, shapes, and any other important elements.

### Guidelines:
- Be descriptive and detailed, focusing on the most important aspects of the image.
- Avoid making assumptions or adding information not present in the image.
- Use the chat's primary language; default to English if multilingual.
- If the image is too complex, focus on the most prominent elements.

### Output:
Strictly return in JSON format:
{
    "prompt": "Your detailed description here."
}

### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>"""

ENABLE_TAGS_GENERATION = RuntimeConfig(
    "ENABLE_TAGS_GENERATION",
    "task.tags.enable",
    os.environ.get("ENABLE_TAGS_GENERATION", "True").lower() == "true",
)

ENABLE_TITLE_GENERATION = RuntimeConfig(
    "ENABLE_TITLE_GENERATION",
    "task.title.enable",
    os.environ.get("ENABLE_TITLE_GENERATION", "True").lower() == "true",
)



ENABLE_AUTOCOMPLETE_GENERATION = RuntimeConfig(
    "ENABLE_AUTOCOMPLETE_GENERATION",
    "task.autocomplete.enable",
    os.environ.get("ENABLE_AUTOCOMPLETE_GENERATION", "False").lower() == "true",
)

AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH = RuntimeConfig(
    "AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH",
    "task.autocomplete.input_max_length",
    int(os.environ.get("AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH", "-1")),
)

AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE = RuntimeConfig(
    "AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE",
    "task.autocomplete.prompt_template",
    os.environ.get("AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE", ""),
)


DEFAULT_AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE = """### Task:
You are an autocompletion system. Continue the text in `<text>` based on the **completion type** in `<type>` and the given language.  

### **Instructions**:
1. Analyze `<text>` for context and meaning.  
2. Use `<type>` to guide your output:  
   - **General**: Provide a natural, concise continuation.  
   - **Search Query**: Complete as if generating a realistic search query.  
3. Start as if you are directly continuing `<text>`. Do **not** repeat, paraphrase, or respond as a model. Simply complete the text.  
4. Ensure the continuation:
   - Flows naturally from `<text>`.  
   - Avoids repetition, overexplaining, or unrelated ideas.  
5. If unsure, return: `{ "text": "" }`.  

### **Output Rules**:
- Respond only in JSON format: `{ "text": "<your_completion>" }`.

### **Examples**:
#### Example 1:  
Input:  
<type>General</type>  
<text>The sun was setting over the horizon, painting the sky</text>  
Output:  
{ "text": "with vibrant shades of orange and pink." }

#### Example 2:  
Input:  
<type>Search Query</type>  
<text>Top-rated restaurants in</text>  
Output:  
{ "text": "New York City for Italian cuisine." }  

---
### Context:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>
<type>{{TYPE}}</type>  
<text>{{PROMPT}}</text>  
#### Output:
"""

# llens: 非 native(プロンプトベース)tool calling 経路は削除済み(issue #18)。
# TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE / DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE も撤去。


DEFAULT_EMOJI_GENERATION_PROMPT_TEMPLATE = """Your task is to reflect the speaker's likely facial expression through a fitting emoji. Interpret emotions from the message and reflect their facial expression using fitting, diverse emojis (e.g., 😊, 😢, 😡, 😱).

Message: ```{{prompt}}```"""

DEFAULT_MOA_GENERATION_PROMPT_TEMPLATE = """You have been provided with a set of responses from various models to the latest user query: "{{prompt}}"

Your task is to synthesize these responses into a single, high-quality response. It is crucial to critically evaluate the information provided in these responses, recognizing that some of it may be biased or incorrect. Your response should not simply replicate the given answers but should offer a refined, accurate, and comprehensive reply to the instruction. Ensure your response is well-structured, coherent, and adheres to the highest standards of accuracy and reliability.

Responses from models: {{responses}}"""


####################################
# Code Interpreter
####################################

# llens: 組み込みコード実行は既定 OFF(issue #11)。
# コード実行は nsjail + IPython の外部サンドボックス(自前ツール側)で行う。
# 有効化する場合も Pyodide には戻さず外部 Jupyter 互換接続を前提とする
# (CLAUDE.md 絶対制約)。コード自体は温存(削除しない)。
ENABLE_CODE_EXECUTION = RuntimeConfig(
    "ENABLE_CODE_EXECUTION",
    "code_execution.enable",
    os.environ.get("ENABLE_CODE_EXECUTION", "False").lower() == "true",
)

CODE_EXECUTION_ENGINE = RuntimeConfig(
    "CODE_EXECUTION_ENGINE",
    "code_execution.engine",
    os.environ.get("CODE_EXECUTION_ENGINE", "jupyter"),
)

CODE_EXECUTION_JUPYTER_URL = RuntimeConfig(
    "CODE_EXECUTION_JUPYTER_URL",
    "code_execution.jupyter.url",
    os.environ.get("CODE_EXECUTION_JUPYTER_URL", ""),
)

CODE_EXECUTION_JUPYTER_AUTH = RuntimeConfig(
    "CODE_EXECUTION_JUPYTER_AUTH",
    "code_execution.jupyter.auth",
    os.environ.get("CODE_EXECUTION_JUPYTER_AUTH", ""),
)

CODE_EXECUTION_JUPYTER_AUTH_TOKEN = RuntimeConfig(
    "CODE_EXECUTION_JUPYTER_AUTH_TOKEN",
    "code_execution.jupyter.auth_token",
    os.environ.get("CODE_EXECUTION_JUPYTER_AUTH_TOKEN", ""),
)


CODE_EXECUTION_JUPYTER_AUTH_PASSWORD = RuntimeConfig(
    "CODE_EXECUTION_JUPYTER_AUTH_PASSWORD",
    "code_execution.jupyter.auth_password",
    os.environ.get("CODE_EXECUTION_JUPYTER_AUTH_PASSWORD", ""),
)

CODE_EXECUTION_JUPYTER_TIMEOUT = RuntimeConfig(
    "CODE_EXECUTION_JUPYTER_TIMEOUT",
    "code_execution.jupyter.timeout",
    int(os.environ.get("CODE_EXECUTION_JUPYTER_TIMEOUT", "60")),
)

# llens: code interpreter も既定 OFF・有効化時は jupyter 前提(issue #11)
ENABLE_CODE_INTERPRETER = RuntimeConfig(
    "ENABLE_CODE_INTERPRETER",
    "code_interpreter.enable",
    os.environ.get("ENABLE_CODE_INTERPRETER", "False").lower() == "true",
)

CODE_INTERPRETER_ENGINE = RuntimeConfig(
    "CODE_INTERPRETER_ENGINE",
    "code_interpreter.engine",
    os.environ.get("CODE_INTERPRETER_ENGINE", "jupyter"),
)

CODE_INTERPRETER_PROMPT_TEMPLATE = RuntimeConfig(
    "CODE_INTERPRETER_PROMPT_TEMPLATE",
    "code_interpreter.prompt_template",
    os.environ.get("CODE_INTERPRETER_PROMPT_TEMPLATE", ""),
)

CODE_INTERPRETER_JUPYTER_URL = RuntimeConfig(
    "CODE_INTERPRETER_JUPYTER_URL",
    "code_interpreter.jupyter.url",
    os.environ.get(
        "CODE_INTERPRETER_JUPYTER_URL", os.environ.get("CODE_EXECUTION_JUPYTER_URL", "")
    ),
)

CODE_INTERPRETER_JUPYTER_AUTH = RuntimeConfig(
    "CODE_INTERPRETER_JUPYTER_AUTH",
    "code_interpreter.jupyter.auth",
    os.environ.get(
        "CODE_INTERPRETER_JUPYTER_AUTH",
        os.environ.get("CODE_EXECUTION_JUPYTER_AUTH", ""),
    ),
)

CODE_INTERPRETER_JUPYTER_AUTH_TOKEN = RuntimeConfig(
    "CODE_INTERPRETER_JUPYTER_AUTH_TOKEN",
    "code_interpreter.jupyter.auth_token",
    os.environ.get(
        "CODE_INTERPRETER_JUPYTER_AUTH_TOKEN",
        os.environ.get("CODE_EXECUTION_JUPYTER_AUTH_TOKEN", ""),
    ),
)


CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD = RuntimeConfig(
    "CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD",
    "code_interpreter.jupyter.auth_password",
    os.environ.get(
        "CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD",
        os.environ.get("CODE_EXECUTION_JUPYTER_AUTH_PASSWORD", ""),
    ),
)

CODE_INTERPRETER_JUPYTER_TIMEOUT = RuntimeConfig(
    "CODE_INTERPRETER_JUPYTER_TIMEOUT",
    "code_interpreter.jupyter.timeout",
    int(
        os.environ.get(
            "CODE_INTERPRETER_JUPYTER_TIMEOUT",
            os.environ.get("CODE_EXECUTION_JUPYTER_TIMEOUT", "60"),
        )
    ),
)


DEFAULT_CODE_INTERPRETER_PROMPT = """
#### Tools Available

1. **Code Interpreter**: `<code_interpreter type="code" lang="python"></code_interpreter>`
   - You have access to a Python shell that runs directly in the user's browser, enabling fast execution of code for analysis, calculations, or problem-solving.  Use it in this response.
   - The Python code you write can incorporate a wide array of libraries, handle data manipulation or visualization, perform API calls for web-related tasks, or tackle virtually any computational challenge. Use this flexibility to **think outside the box, craft elegant solutions, and harness Python's full potential**.
   - To use it, **you must enclose your code within `<code_interpreter type="code" lang="python">` XML tags** and stop right away. If you don't, the code won't execute. Do NOT use triple backticks.
   - When coding, **always aim to print meaningful outputs** (e.g., results, tables, summaries, or visuals) to better interpret and verify the findings. Avoid relying on implicit outputs; prioritize explicit and clear print statements so the results are effectively communicated to the user.  
   - After obtaining the printed output, **always provide a concise analysis, interpretation, or next steps to help the user understand the findings or refine the outcome further.**  
   - If the results are unclear, unexpected, or require validation, refine the code and execute it again as needed. Always aim to deliver meaningful insights from the results, iterating if necessary.  
   - **If a link to an image, audio, or any file is provided in markdown format in the output, ALWAYS regurgitate word for word, explicitly display it as part of the response to ensure the user can access it easily, do NOT change the link.**
   - All responses should be communicated in the chat's primary language, ensuring seamless understanding. If the chat is multilingual, default to English for clarity.

Ensure that the tools are effectively utilized to achieve the highest-quality analysis for the user."""



####################################
# Information Retrieval (RAG)
####################################


# If configured, Google Drive will be available as an upload option.
ENABLE_GOOGLE_DRIVE_INTEGRATION = RuntimeConfig(
    "ENABLE_GOOGLE_DRIVE_INTEGRATION",
    "google_drive.enable",
    os.getenv("ENABLE_GOOGLE_DRIVE_INTEGRATION", "False").lower() == "true",
)

GOOGLE_DRIVE_CLIENT_ID = RuntimeConfig(
    "GOOGLE_DRIVE_CLIENT_ID",
    "google_drive.client_id",
    os.environ.get("GOOGLE_DRIVE_CLIENT_ID", ""),
)

GOOGLE_DRIVE_API_KEY = RuntimeConfig(
    "GOOGLE_DRIVE_API_KEY",
    "google_drive.api_key",
    os.environ.get("GOOGLE_DRIVE_API_KEY", ""),
)

ENABLE_ONEDRIVE_INTEGRATION = RuntimeConfig(
    "ENABLE_ONEDRIVE_INTEGRATION",
    "onedrive.enable",
    os.getenv("ENABLE_ONEDRIVE_INTEGRATION", "False").lower() == "true",
)

ONEDRIVE_CLIENT_ID = RuntimeConfig(
    "ONEDRIVE_CLIENT_ID",
    "onedrive.client_id",
    os.environ.get("ONEDRIVE_CLIENT_ID", ""),
)

# llens(#52): 文書抽出(docling/tika/document_intelligence/mistral_ocr)は案件層(prism)の
# Attachment Function が直接担うため、fork 本体の抽出エンジン config は撤去した。

# llens(#52): 添付ファイルはベクタ検索せず全文注入する(issue #9、fork.md §5)。RAG 検索の
# config(top_k / reranker / relevance / hybrid / full_context / bypass)は撤去済み。
# embedding/reranking モデルの起動時ロードは main.py で無条件にスキップ(air-gapped)。
# vector connector と EMBEDDING_FUNCTION は memories(#8 放置)のため温存。

# llens(#52): 添付ファイルの受け入れゲート(upload の入口=Attachment 前で server-side 強制)。
ATTACHMENT_MAX_COUNT = RuntimeConfig(
    "ATTACHMENT_MAX_COUNT",
    "attachment.max_count",
    (
        int(os.environ.get("ATTACHMENT_MAX_COUNT"))
        if os.environ.get("ATTACHMENT_MAX_COUNT")
        else None
    ),
)

# 受け入れ上限(MB)。既定 25MB。sandbox の SANDBOX_FILE_LIMIT_BYTES(既定 25MB)とは別 config
# だが、原本をサンドボックスへ渡す運用では同値に揃えるのが筋。
ATTACHMENT_MAX_SIZE = RuntimeConfig(
    "ATTACHMENT_MAX_SIZE",
    "attachment.max_size",
    (
        int(os.environ.get("ATTACHMENT_MAX_SIZE"))
        if os.environ.get("ATTACHMENT_MAX_SIZE")
        else 25
    ),
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


# llens(#52): reranking(ベクタ検索の再ランキング)は RAG 撤去に伴い廃止。config・
# 起動時ロード(rf)ともに撤去済み。


TIKTOKEN_CACHE_DIR = os.environ.get("TIKTOKEN_CACHE_DIR", f"{CACHE_DIR}/tiktoken")
TIKTOKEN_ENCODING_NAME = RuntimeConfig(
    "TIKTOKEN_ENCODING_NAME",
    "rag.tiktoken_encoding_name",
    os.environ.get("TIKTOKEN_ENCODING_NAME", "cl100k_base"),
)




ENABLE_RAG_LOCAL_WEB_FETCH = (
    os.getenv("ENABLE_RAG_LOCAL_WEB_FETCH", "False").lower() == "true"
)


####################################
# Web Search (RAG)
####################################

ENABLE_WEB_SEARCH = RuntimeConfig(
    "ENABLE_WEB_SEARCH",
    "rag.web.search.enable",
    os.getenv("ENABLE_WEB_SEARCH", "False").lower() == "true",
)

WEB_SEARCH_ENGINE = RuntimeConfig(
    "WEB_SEARCH_ENGINE",
    "rag.web.search.engine",
    os.getenv("WEB_SEARCH_ENGINE", ""),
)

BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL = RuntimeConfig(
    "BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL",
    "rag.web.search.bypass_embedding_and_retrieval",
    os.getenv("BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL", "False").lower() == "true",
)


WEB_SEARCH_RESULT_COUNT = RuntimeConfig(
    "WEB_SEARCH_RESULT_COUNT",
    "rag.web.search.result_count",
    int(os.getenv("WEB_SEARCH_RESULT_COUNT", "3")),
)


# You can provide a list of your own websites to filter after performing a web search.
# This ensures the highest level of safety and reliability of the information sources.
WEB_SEARCH_DOMAIN_FILTER_LIST = RuntimeConfig(
    "WEB_SEARCH_DOMAIN_FILTER_LIST",
    "rag.web.search.domain.filter_list",
    [
        # "wikipedia.com",
        # "wikimedia.org",
        # "wikidata.org",
    ],
)

WEB_SEARCH_CONCURRENT_REQUESTS = RuntimeConfig(
    "WEB_SEARCH_CONCURRENT_REQUESTS",
    "rag.web.search.concurrent_requests",
    int(os.getenv("WEB_SEARCH_CONCURRENT_REQUESTS", "10")),
)

WEB_LOADER_ENGINE = RuntimeConfig(
    "WEB_LOADER_ENGINE",
    "rag.web.loader.engine",
    os.environ.get("WEB_LOADER_ENGINE", ""),
)

ENABLE_WEB_LOADER_SSL_VERIFICATION = RuntimeConfig(
    "ENABLE_WEB_LOADER_SSL_VERIFICATION",
    "rag.web.loader.ssl_verification",
    os.environ.get("ENABLE_WEB_LOADER_SSL_VERIFICATION", "True").lower() == "true",
)

WEB_SEARCH_TRUST_ENV = RuntimeConfig(
    "WEB_SEARCH_TRUST_ENV",
    "rag.web.search.trust_env",
    os.getenv("WEB_SEARCH_TRUST_ENV", "False").lower() == "true",
)


SEARXNG_QUERY_URL = RuntimeConfig(
    "SEARXNG_QUERY_URL",
    "rag.web.search.searxng_query_url",
    os.getenv("SEARXNG_QUERY_URL", ""),
)

GOOGLE_PSE_API_KEY = RuntimeConfig(
    "GOOGLE_PSE_API_KEY",
    "rag.web.search.google_pse_api_key",
    os.getenv("GOOGLE_PSE_API_KEY", ""),
)

GOOGLE_PSE_ENGINE_ID = RuntimeConfig(
    "GOOGLE_PSE_ENGINE_ID",
    "rag.web.search.google_pse_engine_id",
    os.getenv("GOOGLE_PSE_ENGINE_ID", ""),
)

BRAVE_SEARCH_API_KEY = RuntimeConfig(
    "BRAVE_SEARCH_API_KEY",
    "rag.web.search.brave_search_api_key",
    os.getenv("BRAVE_SEARCH_API_KEY", ""),
)

KAGI_SEARCH_API_KEY = RuntimeConfig(
    "KAGI_SEARCH_API_KEY",
    "rag.web.search.kagi_search_api_key",
    os.getenv("KAGI_SEARCH_API_KEY", ""),
)

MOJEEK_SEARCH_API_KEY = RuntimeConfig(
    "MOJEEK_SEARCH_API_KEY",
    "rag.web.search.mojeek_search_api_key",
    os.getenv("MOJEEK_SEARCH_API_KEY", ""),
)

BOCHA_SEARCH_API_KEY = RuntimeConfig(
    "BOCHA_SEARCH_API_KEY",
    "rag.web.search.bocha_search_api_key",
    os.getenv("BOCHA_SEARCH_API_KEY", ""),
)

SERPSTACK_API_KEY = RuntimeConfig(
    "SERPSTACK_API_KEY",
    "rag.web.search.serpstack_api_key",
    os.getenv("SERPSTACK_API_KEY", ""),
)

SERPSTACK_HTTPS = RuntimeConfig(
    "SERPSTACK_HTTPS",
    "rag.web.search.serpstack_https",
    os.getenv("SERPSTACK_HTTPS", "True").lower() == "true",
)

SERPER_API_KEY = RuntimeConfig(
    "SERPER_API_KEY",
    "rag.web.search.serper_api_key",
    os.getenv("SERPER_API_KEY", ""),
)

SERPLY_API_KEY = RuntimeConfig(
    "SERPLY_API_KEY",
    "rag.web.search.serply_api_key",
    os.getenv("SERPLY_API_KEY", ""),
)

JINA_API_KEY = RuntimeConfig(
    "JINA_API_KEY",
    "rag.web.search.jina_api_key",
    os.getenv("JINA_API_KEY", ""),
)

SEARCHAPI_API_KEY = RuntimeConfig(
    "SEARCHAPI_API_KEY",
    "rag.web.search.searchapi_api_key",
    os.getenv("SEARCHAPI_API_KEY", ""),
)

SEARCHAPI_ENGINE = RuntimeConfig(
    "SEARCHAPI_ENGINE",
    "rag.web.search.searchapi_engine",
    os.getenv("SEARCHAPI_ENGINE", ""),
)

SERPAPI_API_KEY = RuntimeConfig(
    "SERPAPI_API_KEY",
    "rag.web.search.serpapi_api_key",
    os.getenv("SERPAPI_API_KEY", ""),
)

SERPAPI_ENGINE = RuntimeConfig(
    "SERPAPI_ENGINE",
    "rag.web.search.serpapi_engine",
    os.getenv("SERPAPI_ENGINE", ""),
)

BING_SEARCH_V7_ENDPOINT = RuntimeConfig(
    "BING_SEARCH_V7_ENDPOINT",
    "rag.web.search.bing_search_v7_endpoint",
    os.environ.get(
        "BING_SEARCH_V7_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search"
    ),
)

BING_SEARCH_V7_SUBSCRIPTION_KEY = RuntimeConfig(
    "BING_SEARCH_V7_SUBSCRIPTION_KEY",
    "rag.web.search.bing_search_v7_subscription_key",
    os.environ.get("BING_SEARCH_V7_SUBSCRIPTION_KEY", ""),
)

EXA_API_KEY = RuntimeConfig(
    "EXA_API_KEY",
    "rag.web.search.exa_api_key",
    os.getenv("EXA_API_KEY", ""),
)

PERPLEXITY_API_KEY = RuntimeConfig(
    "PERPLEXITY_API_KEY",
    "rag.web.search.perplexity_api_key",
    os.getenv("PERPLEXITY_API_KEY", ""),
)

SOUGOU_API_SID = RuntimeConfig(
    "SOUGOU_API_SID",
    "rag.web.search.sougou_api_sid",
    os.getenv("SOUGOU_API_SID", ""),
)

SOUGOU_API_SK = RuntimeConfig(
    "SOUGOU_API_SK",
    "rag.web.search.sougou_api_sk",
    os.getenv("SOUGOU_API_SK", ""),
)

TAVILY_API_KEY = RuntimeConfig(
    "TAVILY_API_KEY",
    "rag.web.search.tavily_api_key",
    os.getenv("TAVILY_API_KEY", ""),
)

TAVILY_EXTRACT_DEPTH = RuntimeConfig(
    "TAVILY_EXTRACT_DEPTH",
    "rag.web.search.tavily_extract_depth",
    os.getenv("TAVILY_EXTRACT_DEPTH", "basic"),
)

PLAYWRIGHT_WS_URL = RuntimeConfig(
    "PLAYWRIGHT_WS_URL",
    "rag.web.loader.playwright_ws_url",
    os.environ.get("PLAYWRIGHT_WS_URL", ""),
)

PLAYWRIGHT_TIMEOUT = RuntimeConfig(
    "PLAYWRIGHT_TIMEOUT",
    "rag.web.loader.playwright_timeout",
    int(os.environ.get("PLAYWRIGHT_TIMEOUT", "10000")),
)

FIRECRAWL_API_KEY = RuntimeConfig(
    "FIRECRAWL_API_KEY",
    "rag.web.loader.firecrawl_api_key",
    os.environ.get("FIRECRAWL_API_KEY", ""),
)

FIRECRAWL_API_BASE_URL = RuntimeConfig(
    "FIRECRAWL_API_BASE_URL",
    "rag.web.loader.firecrawl_api_url",
    os.environ.get("FIRECRAWL_API_BASE_URL", "https://api.firecrawl.dev"),
)


####################################
# Images
####################################

IMAGE_GENERATION_ENGINE = RuntimeConfig(
    "IMAGE_GENERATION_ENGINE",
    "image_generation.engine",
    os.getenv("IMAGE_GENERATION_ENGINE", "openai"),
)

ENABLE_IMAGE_GENERATION = RuntimeConfig(
    "ENABLE_IMAGE_GENERATION",
    "image_generation.enable",
    os.environ.get("ENABLE_IMAGE_GENERATION", "").lower() == "true",
)

ENABLE_IMAGE_PROMPT_GENERATION = RuntimeConfig(
    "ENABLE_IMAGE_PROMPT_GENERATION",
    "image_generation.prompt.enable",
    os.environ.get("ENABLE_IMAGE_PROMPT_GENERATION", "true").lower() == "true",
)

AUTOMATIC1111_BASE_URL = RuntimeConfig(
    "AUTOMATIC1111_BASE_URL",
    "image_generation.automatic1111.base_url",
    os.getenv("AUTOMATIC1111_BASE_URL", ""),
)
AUTOMATIC1111_API_AUTH = RuntimeConfig(
    "AUTOMATIC1111_API_AUTH",
    "image_generation.automatic1111.api_auth",
    os.getenv("AUTOMATIC1111_API_AUTH", ""),
)

AUTOMATIC1111_CFG_SCALE = RuntimeConfig(
    "AUTOMATIC1111_CFG_SCALE",
    "image_generation.automatic1111.cfg_scale",
    (
        float(os.environ.get("AUTOMATIC1111_CFG_SCALE"))
        if os.environ.get("AUTOMATIC1111_CFG_SCALE")
        else None
    ),
)


AUTOMATIC1111_SAMPLER = RuntimeConfig(
    "AUTOMATIC1111_SAMPLER",
    "image_generation.automatic1111.sampler",
    (
        os.environ.get("AUTOMATIC1111_SAMPLER")
        if os.environ.get("AUTOMATIC1111_SAMPLER")
        else None
    ),
)

AUTOMATIC1111_SCHEDULER = RuntimeConfig(
    "AUTOMATIC1111_SCHEDULER",
    "image_generation.automatic1111.scheduler",
    (
        os.environ.get("AUTOMATIC1111_SCHEDULER")
        if os.environ.get("AUTOMATIC1111_SCHEDULER")
        else None
    ),
)

COMFYUI_BASE_URL = RuntimeConfig(
    "COMFYUI_BASE_URL",
    "image_generation.comfyui.base_url",
    os.getenv("COMFYUI_BASE_URL", ""),
)

COMFYUI_API_KEY = RuntimeConfig(
    "COMFYUI_API_KEY",
    "image_generation.comfyui.api_key",
    os.getenv("COMFYUI_API_KEY", ""),
)

COMFYUI_DEFAULT_WORKFLOW = """
{
  "3": {
    "inputs": {
      "seed": 0,
      "steps": 20,
      "cfg": 8,
      "sampler_name": "euler",
      "scheduler": "normal",
      "denoise": 1,
      "model": [
        "4",
        0
      ],
      "positive": [
        "6",
        0
      ],
      "negative": [
        "7",
        0
      ],
      "latent_image": [
        "5",
        0
      ]
    },
    "class_type": "KSampler",
    "_meta": {
      "title": "KSampler"
    }
  },
  "4": {
    "inputs": {
      "ckpt_name": "model.safetensors"
    },
    "class_type": "CheckpointLoaderSimple",
    "_meta": {
      "title": "Load Checkpoint"
    }
  },
  "5": {
    "inputs": {
      "width": 512,
      "height": 512,
      "batch_size": 1
    },
    "class_type": "EmptyLatentImage",
    "_meta": {
      "title": "Empty Latent Image"
    }
  },
  "6": {
    "inputs": {
      "text": "Prompt",
      "clip": [
        "4",
        1
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "7": {
    "inputs": {
      "text": "",
      "clip": [
        "4",
        1
      ]
    },
    "class_type": "CLIPTextEncode",
    "_meta": {
      "title": "CLIP Text Encode (Prompt)"
    }
  },
  "8": {
    "inputs": {
      "samples": [
        "3",
        0
      ],
      "vae": [
        "4",
        2
      ]
    },
    "class_type": "VAEDecode",
    "_meta": {
      "title": "VAE Decode"
    }
  },
  "9": {
    "inputs": {
      "filename_prefix": "ComfyUI",
      "images": [
        "8",
        0
      ]
    },
    "class_type": "SaveImage",
    "_meta": {
      "title": "Save Image"
    }
  }
}
"""


COMFYUI_WORKFLOW = RuntimeConfig(
    "COMFYUI_WORKFLOW",
    "image_generation.comfyui.workflow",
    os.getenv("COMFYUI_WORKFLOW", COMFYUI_DEFAULT_WORKFLOW),
)

COMFYUI_WORKFLOW_NODES = RuntimeConfig(
    "COMFYUI_WORKFLOW",
    "image_generation.comfyui.nodes",
    [],
)

IMAGES_OPENAI_API_BASE_URL = RuntimeConfig(
    "IMAGES_OPENAI_API_BASE_URL",
    "image_generation.openai.api_base_url",
    os.getenv("IMAGES_OPENAI_API_BASE_URL", OPENAI_API_BASE_URL),
)
IMAGES_OPENAI_API_KEY = RuntimeConfig(
    "IMAGES_OPENAI_API_KEY",
    "image_generation.openai.api_key",
    os.getenv("IMAGES_OPENAI_API_KEY", OPENAI_API_KEY),
)

IMAGES_GEMINI_API_BASE_URL = RuntimeConfig(
    "IMAGES_GEMINI_API_BASE_URL",
    "image_generation.gemini.api_base_url",
    os.getenv("IMAGES_GEMINI_API_BASE_URL", GEMINI_API_BASE_URL),
)
IMAGES_GEMINI_API_KEY = RuntimeConfig(
    "IMAGES_GEMINI_API_KEY",
    "image_generation.gemini.api_key",
    os.getenv("IMAGES_GEMINI_API_KEY", GEMINI_API_KEY),
)

IMAGE_SIZE = RuntimeConfig(
    "IMAGE_SIZE", "image_generation.size", os.getenv("IMAGE_SIZE", "512x512")
)

IMAGE_STEPS = RuntimeConfig(
    "IMAGE_STEPS", "image_generation.steps", int(os.getenv("IMAGE_STEPS", 50))
)

IMAGE_GENERATION_MODEL = RuntimeConfig(
    "IMAGE_GENERATION_MODEL",
    "image_generation.model",
    os.getenv("IMAGE_GENERATION_MODEL", ""),
)

####################################
# Audio
####################################

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
    int(os.environ.get("LDAP_SERVER_PORT", "389")),
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
    os.environ.get("LDAP_SEARCH_FILTER", os.environ.get("LDAP_SEARCH_FILTERS", "")),
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
