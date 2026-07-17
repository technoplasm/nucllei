# Nucllei — API & Extension Guide

This document is for people (and coding-agent sessions) **using** a Nucllei deployment
from the outside: operators deploying the image, harness authors writing Tools /
Skills / Functions, and clients calling the HTTP API. It is self-contained — you
should not need to read the Nucllei source, or any reference deployment repo, to
build a harness on top of it.

Contents:

1. [The mental model](#1-the-mental-model)
2. [Deployment (Docker)](#2-deployment-docker)
3. [Configuration & environment variables](#3-configuration--environment-variables)
4. [HTTP API](#4-http-api)
5. [Writing Tools](#5-writing-tools)
6. [Writing Skills](#6-writing-skills)
7. [Writing Functions (Filter / Attachment / Pipe / Action)](#7-writing-functions)

---

## 1. The mental model

Nucllei is a thin chat platform in front of an OpenAI-compatible inference engine
(the reference deployment uses SGLang). It deliberately ships **mechanism, not
policy**: document extraction, de-identification, code execution, department
routing and similar behavior are all authored at the _deployment layer_ as Tools,
Skills and Functions, and pushed into the server via the admin API. Design rules
that shape everything below:

- **The Filter pipeline is the core feature.** Every chat request flows
  `inlet filters → model (stream filters) → outlet filters`. Deployment policy code
  (e.g. de-identification) lives in Filters assigned to models.
- **Native function calling only.** Tools are exposed through the OpenAI `tools`
  array and executed in a native tool-call loop. There is no prompt-template
  function calling and no tag-detection code-execution backdoor.
- **Honest agent.** Tools belong to the _model configuration_, not the chat: there
  is no per-chat tool picker, and `tool_ids` sent by a client is discarded. If the
  model calls a tool that is not currently available it receives an explicit
  `tool not available: <name>` result instead of a silent drop. Skill activation is
  represented as a visible `load_skill` tool exchange, whether the user or the model
  triggered it.
- **Admin-authored code.** Tools and Functions are in-process Python with full
  access to server internals, so their create/update/delete (and Valves, which may
  hold secrets) are admin-only. Skills are text, so they have a softer three-tier
  permission model (admin / skill editor / user).
- **No runtime installs, no frontmatter.** The upstream Open WebUI
  `"""title: ... requirements: ..."""` docstring frontmatter is gone; nothing pip-installs at
  runtime. Python dependencies used by your Tools/Functions must be pre-installed
  in the image. Name/description live in the DB row, not in the source.
- **Offline-complete.** Deployments range from air-gapped to internet-connected.
  No feature may _require_ external network; optional external integrations are fine.
- **Fresh DB only.** There is no backward compatibility with pre-fork / stock
  Open WebUI data or with old wire formats. Anything targeting stock Open WebUI's
  socket.io wire will not work — the wire is SSE only.

---

## 2. Deployment (Docker)

Docker is the only distribution form (no wheel / PyPI package).

```sh
docker build -t nucllei \
  --build-arg APP_VERSION=v20260717-01 \
  --build-arg PRELOAD_WHISPER_MODEL=small \
  .

docker run -d -p 8080:8080 -v nucllei-data:/app/data nucllei
```

- **Build args**
  - `APP_VERSION` — product version baked into backend and frontend. Truth source
    is the git tag (`vYYYYMMDD-NN` releases, `pre-NN` pre-releases). Resolution at
    runtime: `APP_VERSION` env → `git describe` (source runs) → `package.json` fallback.
  - `PRELOAD_WHISPER_MODEL` — if non-empty (e.g. `small`), the faster-whisper STT
    model is downloaded into the image at _build_ time, so air-gapped targets never
    fetch at first use.
- **Image facts**: `python:3.11-slim-bookworm` runtime; `ffmpeg` (audio), `pandoc`
  and `markitdown[all]` (available to your Attachment Functions), bundled
  faster-whisper STT. Baked env: `ENV=prod`, `PORT=8080`, `DATA_DIR=/app/data`.
- **Port**: `EXPOSE 8080`. `PORT` is consumed by the container CMD / HEALTHCHECK
  shell only; the app itself takes `serve --port`.
- **Volume contract**: mount `/app/data` (= `DATA_DIR`). It holds the SQLite DB
  (`webui.db`), uploads, model caches (whisper / HF / tiktoken), `audit.log`, and
  the auto-generated secret key at `/app/data/.webui_secret_key`. No volume ⇒ all
  state is lost on container replacement.
- **Secret key**: if `WEBUI_SECRET_KEY` is unset, `serve` loads
  `$DATA_DIR/.webui_secret_key`, generating and persisting it on first boot. Set the
  env explicitly if you rotate keys or run multiple replicas.
- **HEALTHCHECK**: `GET /health` must return `{"status": true}`.
- **DB migrations** run automatically on startup (and on `create-admin`).

### CLI

The container CMD is `python -c 'from nucllei import app; app()' serve --port $PORT`.
Commands:

| Command                                            | Purpose                                                                                        |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `serve --host 0.0.0.0 --port 8080`                 | production server (uvicorn, `UVICORN_WORKERS` workers)                                         |
| `dev --reload`                                     | development server, single worker, no secret-key persistence                                   |
| `create-admin --email X [--name N] [--password P]` | provision an admin from the server side (prompts for password if omitted; works on a fresh DB) |
| `main --version`                                   | print version                                                                                  |

The recommended bootstrap is `ENABLE_SIGNUP=false` + `create-admin` (e.g.
`docker exec -it <ctr> python -c 'from nucllei import app; app()' create-admin --email ...`),
then create users/groups from the admin UI or API.

When running from source, a gitignored `.env` at the repo root is loaded at startup
(real environment variables win over `.env` entries).

---

## 3. Configuration & environment variables

### 3.1 The config model

There are two kinds of settings:

- **[env]** — plain environment variables. Read once at process start, never stored
  in the DB, not editable in the UI.
- **[RC]** — _runtime config_. Stored in the DB (`config` table) and editable in the
  admin UI. The env var is an **explicit override** with this precedence, evaluated
  at startup:

  1. env var set to a **non-empty** string → env wins, and the value is written to
     the DB (an env you set can never be shadowed by an old UI save);
  2. env unset/empty → the DB value (last UI save) is used and survives restarts;
  3. no DB value → built-in default.

  Setting an RC env to `""` counts as _unset_. Practical rule: put everything you
  want operator-fixed into env/compose; leave everything you want UI-tunable unset.

Run a **single worker** (the default `UVICORN_WORKERS=1`): runtime config and the
chat task registry are in-process, so multi-worker deployments are not supported.
`RESET_CONFIG_ON_START=true` wipes the stored runtime config at boot (env-only mode).

**Naming families**: `APP_*` refers to the Nucllei product itself (`APP_VERSION`);
`WEBUI_*` is the deployment layer on top — branding and site behavior
(`WEBUI_NAME`, `WEBUI_VERSION`, `WEBUI_COPYRIGHT` show _your harness's_ identity in
the UI, next to the Nucllei product version).

### 3.2 Core / server

| Var                                                                                                                         | Kind | Default                                  | Effect                                                                   |
| --------------------------------------------------------------------------------------------------------------------------- | ---- | ---------------------------------------- | ------------------------------------------------------------------------ |
| `ENV`                                                                                                                       | env  | `dev` (`prod` in Docker)                 | dev exposes Swagger UI at `/docs`                                        |
| `DATA_DIR`                                                                                                                  | env  | `./data` (`/app/data`)                   | root of all persistent state                                             |
| `DATABASE_URL`                                                                                                              | env  | `sqlite:///$DATA_DIR/webui.db`           | SQLAlchemy URL; Postgres supported (`postgres://` auto-rewritten)        |
| `DATABASE_SCHEMA` / `DATABASE_POOL_SIZE` / `DATABASE_POOL_MAX_OVERFLOW` / `DATABASE_POOL_TIMEOUT` / `DATABASE_POOL_RECYCLE` | env  | — / `0` (NullPool) / `0` / `30` / `3600` | Postgres tuning                                                          |
| `UVICORN_WORKERS`                                                                                                           | env  | `1`                                      | worker count for `serve` — keep at 1 (multi-worker unsupported)          |
| `WEBUI_URL`                                                                                                                 | RC   | `http://localhost:3000`                  | public base URL (webhooks, links)                                        |
| `WEBUI_NAME`                                                                                                                | env  | `Nucllei`                                | brand name shown in the UI                                               |
| `WEBUI_VERSION` / `WEBUI_COPYRIGHT`                                                                                         | env  | `""`                                     | deployment-layer version / copyright on the License tab (empty = hidden) |
| `ENABLE_FORWARD_USER_INFO_HEADERS`                                                                                          | env  | `false`                                  | send `X-Nucllei-User-{Name,Id,Email,Role}` to the upstream engine        |
| `CORS_ALLOW_ORIGIN`                                                                                                         | env  | `*`                                      | `;`-separated origins                                                    |
| `EXTERNAL_PWA_MANIFEST_URL`                                                                                                 | env  | unset                                    | proxy `/manifest.json` from this URL                                     |
| `OFFLINE_MODE`                                                                                                              | env  | `false`                                  | forces HF hub offline + disables whisper auto-update                     |
| `USE_CUDA_DOCKER`                                                                                                           | env  | `false`                                  | run local ML (whisper) on CUDA                                           |
| `RESET_CONFIG_ON_START`                                                                                                     | env  | `false`                                  | wipe runtime config at startup                                           |

### 3.3 Auth & security

| Var                                                                                                                                                                                     | Kind | Default                 | Effect                                                                                                           |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ----------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `WEBUI_AUTH`                                                                                                                                                                            | env  | `true`                  | `false` = single-user, no-auth mode                                                                              |
| `WEBUI_SECRET_KEY`                                                                                                                                                                      | env  | auto-generated (see §2) | JWT signing key                                                                                                  |
| `JWT_EXPIRES_IN`                                                                                                                                                                        | RC   | `-1` (no expiry)        | session lifetime: `-1` \| `0` \| `<n>(s                                                                          | m   | h   | d   | w)` |
| `ENABLE_SIGNUP`                                                                                                                                                                         | RC   | `true`                  | public signup (auto-disabled after the first user becomes admin; production reference: `false` + `create-admin`) |
| `ENABLE_LOGIN_FORM`                                                                                                                                                                     | RC   | `true`                  | email/password form (off for SSO-only)                                                                           |
| `DEFAULT_USER_ROLE`                                                                                                                                                                     | RC   | `pending`               | role given to new signups                                                                                        |
| `ENABLE_API_KEY`                                                                                                                                                                        | RC   | `true`                  | allow user API keys (`sk-...`)                                                                                   |
| `ENABLE_API_KEY_ENDPOINT_RESTRICTIONS`                                                                                                                                                  | RC   | `false`                 | restrict API keys to a path allowlist                                                                            |
| `API_KEY_ALLOWED_ENDPOINTS`                                                                                                                                                             | RC   | `""`                    | comma-separated path prefixes; **empty list + restrictions on = deny all API-key requests**                      |
| `WEBUI_SESSION_COOKIE_SAME_SITE` / `WEBUI_SESSION_COOKIE_SECURE`                                                                                                                        | env  | `lax` / `false`         | session cookie flags                                                                                             |
| `WEBUI_AUTH_COOKIE_SAME_SITE` / `WEBUI_AUTH_COOKIE_SECURE`                                                                                                                              | env  | = session values        | auth (`token`) cookie flags                                                                                      |
| `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` / `WEBUI_AUTH_TRUSTED_NAME_HEADER`                                                                                                                    | env  | unset                   | header-based SSO behind a trusted proxy                                                                          |
| `BYPASS_MODEL_ACCESS_CONTROL`                                                                                                                                                           | env  | `false`                 | skip per-model group ACL                                                                                         |
| `SHOW_ADMIN_DETAILS` / `ADMIN_EMAIL`                                                                                                                                                    | RC   | `true` / unset          | admin contact shown to pending users                                                                             |
| `ENABLE_ADMIN_EXPORT` / `ENABLE_ADMIN_CHAT_ACCESS`                                                                                                                                      | env  | `true` / `true`         | admin DB export / admin access to user chats                                                                     |
| `CACHE_CONTROL`, `HSTS`, `PERMISSIONS_POLICY`, `REFERRER_POLICY`, `XCONTENT_TYPE`, `XDOWNLOAD_OPTIONS`, `XFRAME_OPTIONS`, `XPERMITTED_CROSS_DOMAIN_POLICIES`, `CONTENT_SECURITY_POLICY` | env  | unset                   | each sets the corresponding response security header when present                                                |

OAuth (Google / Microsoft / GitHub / generic OIDC via `GOOGLE_CLIENT_ID`&`_SECRET`,
`MICROSOFT_CLIENT_ID`&`_SECRET`&`_TENANT_ID`, `GITHUB_CLIENT_ID`&`_SECRET`,
`OAUTH_CLIENT_ID`&`_SECRET`&`OPENID_PROVIDER_URL`; gates `ENABLE_OAUTH_SIGNUP`,
`OAUTH_MERGE_ACCOUNTS_BY_EMAIL`, role/group management flags) and LDAP
(`ENABLE_LDAP`, `LDAP_SERVER_HOST/PORT`, `LDAP_APP_DN/PASSWORD`, `LDAP_SEARCH_BASE/FILTER`,
attribute mappings, TLS options) are available as RC settings; all default off.

### 3.4 User permissions & metadata

Env vars here only seed the **default** permission set; actual permissions are
managed per-group in the DB/UI.

| Var                                                 | Default            | Effect                                                                                                                                                                                                      |
| --------------------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS`          | `false`            | grants the "skill editor" tier (see §6)                                                                                                                                                                     |
| `USER_PERMISSIONS_CHAT_FILE_UPLOAD`                 | `true`             | file upload (server-enforced)                                                                                                                                                                               |
| `USER_PERMISSIONS_CHAT_DELETE` / `_EDIT` / `_SHARE` | `true`             | chat deletion / message editing / share links                                                                                                                                                               |
| `USER_PERMISSIONS_CHAT_CLONE`                       | `false`            | cloning shared chats                                                                                                                                                                                        |
| `USER_PERMISSIONS_CHAT_MULTIPLE_MODELS`             | `true`             | multi-model chat                                                                                                                                                                                            |
| `ADDITIONAL_USER_META`                              | `[]` (RC)          | JSON array `[{"key":"carte_user_id","label":"..."}]` declaring extra string user fields, editable by admins and delivered to tools as `__user__["info"]` — opaque markers for external-system authorization |
| `DEFAULT_LOCALE`                                    | `""` (RC)          | UI locale (shipped: `ja`, `en`, `zh-CN`)                                                                                                                                                                    |
| `DEFAULT_MODELS`                                    | unset (RC)         | default model(s) for new chats                                                                                                                                                                              |
| `WEBUI_BANNERS`                                     | `[]` (RC)          | JSON banner list `{id,type,title?,content,dismissible,timestamp}`                                                                                                                                           |
| `WEBHOOK_URL` / `ENABLE_USER_WEBHOOKS`              | `""` / `true` (RC) | server webhook (signup events) / per-user webhooks                                                                                                                                                          |

### 3.5 Model backend (OpenAI-compatible engine)

| Var                                                       | Kind     | Default                            | Effect                                                        |
| --------------------------------------------------------- | -------- | ---------------------------------- | ------------------------------------------------------------- |
| `ENABLE_OPENAI_API`                                       | RC       | `true`                             | master switch                                                 |
| `OPENAI_API_BASE_URL` / `OPENAI_API_KEY`                  | env seed | `https://api.openai.com/v1` / `""` | single-connection convenience; folded into the plural forms   |
| `OPENAI_API_BASE_URLS` / `OPENAI_API_KEYS`                | RC       | from above                         | `;`-separated endpoint/key lists, index-matched               |
| `ENABLE_OLLAMA_API`                                       | RC       | `true`                             | ollama backend (reference deployment: `false`)                |
| `OLLAMA_BASE_URL(S)`                                      | env/RC   | localhost                          | ollama endpoints                                              |
| `AIOHTTP_CLIENT_TIMEOUT`                                  | env      | unset (unlimited)                  | total chat-request timeout — leave unset for long generations |
| `AIOHTTP_CLIENT_TIMEOUT_CONNECT`                          | env      | `10`                               | TCP connect timeout (dead-connection detection)               |
| `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ`                        | env      | `300`                              | max silent gap between streamed bytes                         |
| `AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST` / `_TOOL_SERVER_DATA` | env      | `10` / `10`                        | model-list / tool-server fetch timeouts                       |

Reference wiring for a local engine:
`OPENAI_API_BASE_URL=http://host.docker.internal:13300/v1`, `OPENAI_API_KEY=EMPTY`,
`ENABLE_OLLAMA_API=false`. Reasoning models are supported: `reasoning_content`
deltas from the engine's reasoning parser flow through the wire and render as a
separate reasoning block.

### 3.6 Tool calling & tasks

| Var                                                            | Kind | Default               | Effect                                                                                |
| -------------------------------------------------------------- | ---- | --------------------- | ------------------------------------------------------------------------------------- |
| `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`                       | env  | `30` (`-1` unlimited) | native tool-loop cap; hitting it surfaces an in-chat error                            |
| `CHAT_TOOL_EXECUTION_TIMEOUT`                                  | env  | `300` s (`-1` off)    | authoritative per-tool-call timeout; expiry is returned to the model as a tool result |
| `TASK_MODEL` / `TASK_MODEL_EXTERNAL`                           | RC   | `""`                  | model used for background tasks (title generation)                                    |
| `ENABLE_TITLE_GENERATION` / `TITLE_GENERATION_PROMPT_TEMPLATE` | RC   | `true` / built-in     | auto chat titles                                                                      |

### 3.7 Attachments / files / storage

| Var                              | Kind | Default           | Effect                                                                                                                                                      |
| -------------------------------- | ---- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ATTACHMENT_MAX_SIZE`            | RC   | `25` MB           | upload size cap (server-enforced)                                                                                                                           |
| `ATTACHMENT_MAX_COUNT`           | RC   | unlimited         | max attachments per message                                                                                                                                 |
| `ATTACHMENT_ALLOWED_EXTENSIONS`  | RC   | `[]` = allow all  | lowercase, dot-less whitelist                                                                                                                               |
| `ATTACHMENT_FUNCTION_ID`         | RC   | `""` = none       | id of the single global Attachment Function (§7.3); empty ⇒ uploads store raw bytes, no text extraction                                                     |
| `ATTACHMENT_IMAGE_MAX_DIMENSION` | RC   | unset = no resize | client-side downscale of attached images before upload: if the longest edge exceeds this (px), the image is resized to fit, aspect ratio and MIME preserved |
| `PASTED_TEXT_CHARACTER_LIMIT`    | RC   | unset = disabled  | pasted plain text longer than this (chars) becomes a `.txt` attachment                                                                                      |
| `STORAGE_PROVIDER`               | env  | `local`           | `local` \| `s3` \| `gcs` \| `azure` (each with its own credential envs: `S3_*`, `GCS_BUCKET_NAME`+`GOOGLE_APPLICATION_CREDENTIALS_JSON`, `AZURE_STORAGE_*`) |
| `TIKTOKEN_ENCODING_NAME`         | RC   | `cl100k_base`     | tokenizer for token counting                                                                                                                                |

### 3.8 Audio (STT / TTS)

Feature gates: `ENABLE_AUDIO_STT`, `ENABLE_AUDIO_TTS` (RC, both `true`; `false`
hides the UI affordance and 403s the endpoints).

STT: `AUDIO_STT_ENGINE` (RC, `""` = bundled faster-whisper; or `openai`, `deepgram`,
`azure`). Whisper knobs: `WHISPER_MODEL` (`base`; a name or a local model path),
`WHISPER_MODEL_DIR`, `WHISPER_LANGUAGE` (`""` = auto; set `ja` for Japanese-only
sites), `WHISPER_COMPUTE_TYPE` (`int8`), `WHISPER_CPU_THREADS` (`0`),
`WHISPER_NUM_WORKERS` (`1`), `WHISPER_VAD_FILTER` (`false`). Engine-specific:
`AUDIO_STT_OPENAI_API_BASE_URL/_KEY`, `AUDIO_STT_MODEL`, `DEEPGRAM_API_KEY`,
`AUDIO_STT_AZURE_*`.

TTS: `AUDIO_TTS_ENGINE` (RC, `""` = browser speech API; or `openai`, `azure`,
`elevenlabs`, `transformers`). `AUDIO_TTS_OPENAI_API_BASE_URL` accepts any
OpenAI-compatible `/v1` speech endpoint (the reference deployment points it at a
VOICEVOX/AivisSpeech bridge), plus `AUDIO_TTS_MODEL`, `AUDIO_TTS_VOICE`,
`AUDIO_TTS_SPLIT_ON`, `AUDIO_TTS_API_KEY`, `AUDIO_TTS_AZURE_*`.

### 3.9 Logging / audit / telemetry

| Var                                                                                                                   | Default                                   | Effect                                                                                                                                                                                                        |
| --------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GLOBAL_LOG_LEVEL`                                                                                                    | `INFO`                                    | root level; per-source overrides: `MAIN_LOG_LEVEL`, `MODELS_LOG_LEVEL`, `OPENAI_LOG_LEVEL`, `OLLAMA_LOG_LEVEL`, `AUDIO_LOG_LEVEL`, `CONFIG_LOG_LEVEL`, `DB_LOG_LEVEL`, `WEBHOOK_LOG_LEVEL`, `OAUTH_LOG_LEVEL` |
| `AUDIT_LOG_LEVEL`                                                                                                     | `NONE`                                    | `NONE` \| `METADATA` \| `REQUEST` \| `REQUEST_RESPONSE`; writes `$DATA_DIR/audit.log`                                                                                                                         |
| `AUDIT_LOG_FILE_ROTATION_SIZE` / `MAX_BODY_LOG_SIZE` / `AUDIT_EXCLUDED_PATHS`                                         | `10MB` / `2048` / `/chats,/chat,/folders` | audit tuning                                                                                                                                                                                                  |
| `ENABLE_OTEL` + `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER` | `false`                                   | OpenTelemetry traces                                                                                                                                                                                          |

Vars you may know from stock Open WebUI that **do not exist here** (removed with
their features): all `RAG_*` / embedding / web-search / image-generation config,
`CODE_EXECUTION_*` / `CODE_INTERPRETER_*`, `WEBSOCKET_*`, community-sharing and
telemetry flags, `ENABLE_PERSISTENT_CONFIG` (replaced by the 3-tier model).

---

## 4. HTTP API

Interactive docs: `GET /docs` (Swagger UI) when `ENV=dev`.

### 4.1 Authentication

Every authenticated request accepts, in priority order:

1. `Authorization: Bearer <JWT>` — the session token returned by signin;
2. `Authorization: Bearer sk-...` — a user API key;
3. the `token` cookie (httpOnly; set by signin — what the browser UI uses).

All three are interchangeable on all endpoints (API keys can additionally be
restricted to a path allowlist — §3.3). Role tiers: `pending` (no access),
`user`, `admin`.

```sh
# sign in → JWT
JWT=$(curl -s http://HOST/api/v1/auths/signin -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"..."}' | jq -r .token)

# mint an API key (one per user; POST replaces)
KEY=$(curl -s -X POST http://HOST/api/v1/auths/api_key \
  -H "Authorization: Bearer $JWT" | jq -r .api_key)
```

Other auth endpoints: `GET /api/v1/auths/` (session info + refreshed token),
`GET /api/v1/auths/signout`, `POST /api/v1/auths/add` (admin: create a user),
`POST /api/v1/auths/ldap`.

### 4.2 Models

`GET /api/models` — the canonical model list for clients: only _workspace models_
(presets and admin-configured base models; raw connection models are not exposed),
filtered by the caller's group ACL. The `id` field is what you pass as `model` in
completions. Model CRUD lives under `/api/v1/models/*` (create/update/delete are
admin-only).

A model preset is where all capability wiring happens (`meta` fields):
`toolIds` (tools available to the model), `filterIds` / `actionIds` (functions),
`skillIds` + `features.skills` (skills), plus params and system prompt.

### 4.3 Chat completions

`POST /api/chat/completions` — OpenAI-style body, `model` required. Two modes:

**Headless mode** (no `chat_id`/`id` in the body) — what external clients should
use. Nothing is persisted; no chat appears in the UI.

```sh
curl -N http://HOST/api/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"kimi-k2","stream":true,
       "messages":[{"role":"user","content":"hello"}],
       "files":[{"type":"file","id":"<file_id>"}]}'
```

- `stream:false` → one OpenAI JSON response; `stream:true` → standard OpenAI SSE
  (`data: {...}` frames, terminated by `data: [DONE]`). A usage chunk is always
  included on streams (the server forces `stream_options.include_usage`).
- **Inlet and stream Filters run** on this path (model params, system prompt, skill
  `/command` expansion, file context injection too). Outlet filters do not run
  headless (call `POST /api/chat/completed` yourself if you need them).
- **Tools are attached but not executed**: the model's configured tools are put in
  the request, so you may receive raw `tool_calls` in the response — executing them
  is your client's job in this mode. The native execution loop only runs on the
  chat-wire path.
- `tool_ids` and `features` in your payload are silently discarded (tool
  availability is model configuration — §1).
- `files` entries are ACL-checked against the caller, and each file's extracted
  text is injected into the system message as a `<source id=".." file_id="..">`
  block (full text — there is no RAG retrieval step).

**Chat-wire mode** (`chat_id` + message `id` present) — the UI's own wire, usable
by API clients that want the conversation to exist in the UI (create the chat via
`POST /api/v1/chats/new` first; you must own it). The POST response is an SSE
subscription to a background task that always runs to completion (disconnecting
does not cancel). Events are envelopes
`data: {"chat_id","message_id","data":{"type","data"}}`; the first is
`{type:"task",data:{task_id}}`, then `chat:completion` events carrying either
`{delta:{block:"text"|"reasoning",content}}` appends, periodic full
`{content, content_blocks}` snapshots, or terminals
(`{done:true,...}` / `{error:...}` / `{usage}`). `done` is not EOF — read until the
server closes. Companions: `GET /api/tasks/{task_id}/events` (re-attach),
`POST /api/tasks/stop/{task_id}`, `POST /api/tasks/{task_id}/calls/{call_id}`
(reply to a tool's `__event_call__` dialog), `GET /api/tasks/chat/{chat_id}`.
On this path the native tool loop, skills, and all filter phases run server-side.

**Raw passthrough**: `POST /openai/chat/completions` proxies straight to the
configured engine — model-preset params and ACL apply, but **no filters, no tools,
no file injection**. `GET /openai/models` lists raw connection models. Prefer
`/api/chat/completions` unless you specifically need an unfiltered proxy.

Reverse-proxy note: SSE must not be buffered (the server sends
`X-Accel-Buffering: no`; make sure nginx honors it).

### 4.4 Files

| Endpoint                                                            | Notes                                                                                                                                                                                                                  |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/files/`                                               | multipart field `file`; optional `?process=false` to skip extraction. Enforces the upload permission, extension whitelist and size cap. Returns `{id, filename, meta{name,content_type,size}, data{content?,status?}}` |
| `POST /api/v1/files/{id}/process`                                   | re-run the Attachment Function (`__event__="attach"`)                                                                                                                                                                  |
| `GET /api/v1/files/{id}` / `/{id}/content` / `/{id}/data/content`   | metadata / raw bytes / extracted text                                                                                                                                                                                  |
| `POST /api/v1/files/{id}/data/content/update`                       | overwrite extracted text                                                                                                                                                                                               |
| `POST /api/v1/files/{id}/access_control/update`                     | `null` (public) or `{"read":{group_ids:[],user_ids:[]}}`                                                                                                                                                               |
| `GET /api/v1/files/` `/list` `/search`, `DELETE /api/v1/files/{id}` | listing / deletion                                                                                                                                                                                                     |

To use a file in a completion: pass `{"type":"file","id":...}` in the request
`files` array (text injection), or reference `/api/v1/files/{id}/content` as an
`image_url` in message content — internal image URLs are inlined to base64 before
reaching the engine. Client-facing limits are advertised in `GET /api/config`
(`file.max_size`, `file.max_count`, `file.allowed_extensions`).

What extraction happens at upload is entirely up to the deployment's single
**Attachment Function** (§7.3). With none configured, files are stored raw and the
model sees the attachment marked as unextracted.

### 4.5 Deploying extensions programmatically

All extension kinds are DB rows, so a harness repo can push them via API (this is
the intended workflow — keep sources in your repo, register on deploy):

| Kind     | Endpoint                                                         | Gate          | Form                                                                                                                                                                         |
| -------- | ---------------------------------------------------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tool     | `POST /api/v1/tools/create`, `POST /api/v1/tools/id/{id}/update` | admin         | `{id, name, description?, content, meta?, access_control?}` — `content` is the Python source; the module is imported and spec'd at save time (syntax errors reject the save) |
| Function | `POST /api/v1/functions/create`, `.../id/{id}/update`            | admin         | `{id, name, description?, content}` — type (filter/pipe/action/attachment) is auto-detected from the class in `content`                                                      |
| Skill    | `POST /api/v1/skills/create`, `.../id/{id}/update`               | skill editor+ | `{id, name, description?, content, tool_ids?, access_control?}` (markdown content; `tool_ids` admin-only)                                                                    |

`id` for tools/functions must be a valid Python identifier (lowercased on save)
and is immutable. Valves values: `GET/POST /api/v1/{tools,functions}/id/{id}/valves*`
(admin). Exports: `GET /api/v1/{tools,functions}/export` (admin).

Remember to **assign** what you deploy: tools/filters/actions/skills only take
effect on models whose preset lists them (`POST /api/v1/models/model/update`,
fields `meta.toolIds` / `meta.filterIds` / `meta.actionIds` / `meta.skillIds`).
The Attachment Function is instead activated globally via
`POST /api/v1/configs/attachment` (`{"function_id": "..."}`).

### 4.6 Miscellaneous endpoints

- `GET /health` → `{"status":true}`; `GET /health/db` (does a DB roundtrip). Unauthenticated.
- `GET /api/config` — instance metadata (name, version, auth features, locale;
  with a session also default models, audio config, file limits, permissions).
  `GET /api/version`.
- Audio: `POST /api/v1/audio/transcriptions` (STT), `POST /api/v1/audio/speech` (TTS).
- Chats CRUD: `/api/v1/chats/*` (`/new`, `/{id}`, `/search`, share/archive/clone...).
- Users & groups (admin): `/api/v1/users/*`, `/api/v1/groups/*`.
- Config import/export (admin): `POST /api/v1/configs/import`, `GET /api/v1/configs/export`.
- External OpenAPI tool servers (admin): `GET/POST /api/v1/configs/tool_servers`.

---

## 5. Writing Tools

A **Tool** is one Python module stored in the DB and executed **in-process** —
it can import server internals (`nucllei.models.*`, `nucllei.storage.provider`, ...)
and call external services over HTTP. Tool authoring is admin-only.

### 5.1 Anatomy

```python
class Tools:
    def get_weather(self, city: str, unit: str = "celsius") -> str:
        """Return current weather for a city.

        :param city: City name (e.g. Sapporo)
        :param unit: "celsius" or "fahrenheit"
        """
        return f"{city}: 12°C, cloudy"
```

- The module must define `class Tools`; **every public method becomes a
  model-callable function**. Prefix helpers with `_` to keep them private.
- The model-facing spec is generated from **type hints + the reST docstring**:
  description = text before the first `:param:`; per-argument descriptions from
  `:param name: ...` lines. (The DB `description` field is list-UI only — the model
  never sees it.)
- Methods may be `def` or `async def`; sync methods run in a worker thread, so
  blocking I/O is fine.
- Function names must be **globally unique across all tools assigned to a model**
  — on collision the first-loaded wins and the duplicate is dropped with a warning.

### 5.2 Valves (admin config) and UserValves (per-user config)

```python
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):                  # admin-wide; may hold secrets
        service_url: str = Field(default="http://svc:8000", description="Endpoint")

    class UserValves(BaseModel):              # per-user preference
        verbose: bool = Field(default=False)

    def __init__(self):
        self.valves = self.Valves()
```

Valves values are stored in the DB and re-injected as `self.valves` before every
call (admin edits take effect immediately). UserValves arrive per-call as
`__user__["valves"]` — **a pydantic instance, not a dict**
(`__user__["valves"].verbose`).

### 5.3 Reserved (injected) parameters

Declare any of these in a method signature (with a default, e.g.
`__user__: Optional[dict] = None`) and the server injects it. They are stripped
from the model-facing spec, and the model cannot spoof them.

| Parameter                       | Contents                                                                                                                            |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `__user__`                      | `{"id","email","name","role","info","valves"?}` — `info` is the opaque extra-metadata dict (§3.4) for external-system authorization |
| `__event_emitter__`             | `async fn(event)` — push UI events mid-call (§5.5); `None` on the headless API path                                                 |
| `__event_call__`                | `async fn(event)` — round-trip to the user (`confirmation` / `input` / `execute`), awaits their reply; `None` headless              |
| `__chat_id__`, `__message_id__` | current chat / message ids                                                                                                          |
| `__id__`                        | this tool's id                                                                                                                      |
| `__files__`                     | the chat's attachment refs (already ACL-filtered)                                                                                   |
| `__messages__`                  | the outbound message list                                                                                                           |
| `__metadata__`                  | request metadata (chat/message ids, files, skill activations, ...)                                                                  |
| `__request__`                   | the FastAPI `Request` (→ `request.app.state.config` etc.)                                                                           |
| `__model__`                     | a model dict — note: for tools this is the _task_ model, not necessarily the chat model                                             |

### 5.4 Argument & result rendering

To get IDE-quality rendering of tool calls in the chat UI, annotate types —
there are no name-based heuristics:

```python
from nucllei.utils.tool_render import Code, Text

async def run_sql(self, query: Code("sql")) -> Text:
    ...
```

- `Code(language)` renders that string argument as highlighted, live-streaming
  code (`Annotated[str, RenderAs(kind="code", language=...)]` under the hood;
  reflected into the JSON-schema as `contentMediaType`, which is left visible to
  the model — harmless).
- `-> Text` renders the return value as pre-wrap text instead of a JSON tree.
- Un-annotated arguments render by JSON type (scalars as `key: value`,
  structures as a tree). Streaming partial-argument rendering is automatic.
- External OpenAPI tools get the same treatment by declaring
  `"contentMediaType": "text/x-python"` (etc.) on string parameters.

### 5.5 Return values, events, limits

- Return a `str` (recommended; passed to the model verbatim) or a `dict`/`list`
  (JSON-serialized). Raising is safe — the exception string becomes the tool
  result — but prefer returning `"ERROR: ..."` strings to steer the model.
- Generators are not supported.
- Per-call timeout `CHAT_TOOL_EXECUTION_TIMEOUT` (default 300 s) and loop cap
  `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` (default 30) — see §3.6.
- `__event_emitter__({"type": ..., "data": ...})` event types:
  `status` (`{"description","done"}` progress line), `chat:message:files`
  (attach files to the message), `message` / `replace` (append to / replace the
  message text), `source`/`citation`, `notification` (toast), `chat:title`.
- File outputs: write bytes via `nucllei.storage.provider.Storage.upload_file` +
  `nucllei.models.files.Files.insert_new_file`, then emit `chat:message:files`;
  images become visible when the model writes
  `![name](/api/v1/files/{id}/content)`.

### 5.6 Access control & assignment

- Tool CRUD and Valves: **admin only**. Usage ACL per tool: `access_control =
null` (all users) / `{}` (owner only) / `{"read":{group_ids,user_ids}}`.
- A tool is live on a model iff it is listed in the model preset's `meta.toolIds`
  (and the calling user passes the tool's read ACL). There is no per-chat toggle
  and no global switch — "off everywhere" = assigned to no model.

### 5.7 Builtin tools, code execution, tool servers

- **Builtin tools** ship with the server (`builtin_` id prefix; currently
  `builtin_history` — `list_history` / `read_history` / `grep_history` over the
  calling user's own chats). Only their ACL is editable.
- **Code execution is a deployment-layer tool**, not a server feature: the
  reference implementation (`dev/tools/sandbox.py` in the source tree) bridges to
  an external nsjail + IPython sandbox service over HTTP and provides a stateful
  `run_python` (per user+chat kernel), `run_shell`, and workspace file exchange
  wired to chat attachments. There is no Pyodide and no in-server interpreter —
  don't target either.
- **External OpenAPI tool servers**: admin-configured connections whose
  `/openapi.json` operations are exposed as tools and invoked over HTTP (no
  reserved-param injection there).

---

## 6. Writing Skills

A **Skill** is a markdown instruction package with optional lazy-loaded tools.
It activates through a single server-side path, triggered either by the user
typing `/<id>` or by the model calling the `load_skill` pseudo-tool — both leave
the same visible `load_skill` tool exchange in the conversation.

### 6.1 Definition

```json
POST /api/v1/skills/create
{
  "id": "vault",
  "name": "金庫番",
  "description": "Retrieve secrets from the vault. Load when the user asks about stored passwords.",
  "content": "You now have vault access tools. Use secret_password to retrieve the stored secret.\nToday is {{CURRENT_DATE}}; the user is {{USER_NAME}}.",
  "tool_ids": ["vault-tools"],
  "access_control": null
}
```

- `id` — the `/command` trigger **and** the identifier the model sees
  (`[a-zA-Z0-9-]+`, immutable; rename = delete + recreate).
- `name` — display only; never shown to the model.
- `description` — the model-facing manifest line. **Empty description ⇒ the skill
  is invisible to the model** (only `/command` works). Write it as a "when to load
  this" hint.
- `content` — markdown instructions. Template variables expanded at every
  request: `{{CURRENT_DATE}}`, `{{CURRENT_TIME}}`, `{{CURRENT_DATETIME}}`,
  `{{CURRENT_WEEKDAY}}`, `{{USER_NAME}}`. There is no call-time parameter
  mechanism.
- `tool_ids` — workspace tool ids to load on activation (**admin-only** to set,
  since it grants tool access through skill visibility).
- `access_control` — `null` public / `{}` private / `{"read":{group_ids,user_ids}}`.

### 6.2 Permissions (three tiers)

- **user** — uses skills they can read (via `/command` or model auto-load).
- **skill editor** (`USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS`) — browses all
  skills, creates skills (forced private), edits/deletes their own.
- **admin** — everything, including sharing (ACL) and `tool_ids`.

### 6.3 Activation

A skill fires only if all three hold: the model preset has skills enabled
(`meta.features.skills`, default on) ∧ the skill id is in the preset's
`meta.skillIds` ∧ the caller can read the skill. A `/command` that fails any
condition passes through as plain text (no error).

- **User path**: a message starting with `/<id>` (first line). The server injects,
  right after that message, a synthetic assistant `load_skill` call plus its tool
  result. Activation is **conversation-persistent and stateless**: every request
  re-derives it by scanning the user messages, so branching from before the
  command deactivates it naturally.
- **Model path**: when a preset lists skills with non-empty descriptions, the
  model gets one `load_skill(skill_id)` pseudo-tool whose description embeds the
  manifest (`- <id>: <description>` lines, enum-constrained). Tools loaded this
  way are available from the next tool-loop iteration.

Both paths return the same tool result:

```json
{ "result": "<expanded skill content>", "tools": ["secret_password"] }
```

`result` enters the model history as a `role:"tool"` message (not a system
message); the UI renders it as a collapsible "load_skill" card with the loaded
tool names. One asymmetry to know: on the `/command` path the skill's tools stay
loaded for the rest of the conversation; on the model path they are loaded for
the current request's tool loop — on later turns the model sees the instructions
in history but must call `load_skill` again (idempotent) if it needs the tools.

---

## 7. Writing Functions

A **Function** is admin-authored, in-process Python, one module per DB row. The
**type is auto-detected from the class you define**: `class Filter`, `class Pipe`,
`class Action`, or `class Attachment`. Functions have no usage ACL — they apply
via model assignment (`meta.filterIds` / `meta.actionIds`), except Attachment,
which is a single global hook. There is no "global filter" and no enable/disable
toggle: assignment _is_ activation, so a policy filter (e.g. de-identification)
must be assigned to **every** model preset it should govern — forgetting one is
the classic operational mistake.

All function types support `class Valves` / `class UserValves` exactly as tools
do (§5.2), and the same signature-based reserved-parameter injection (`__user__`,
`__event_emitter__`, `__event_call__`, `__metadata__`, `__request__`, `__model__`,
`__id__`; availability varies slightly per hook). Sync handlers run in worker
threads; async is fine everywhere.

### 7.1 Filter — the core pipeline

```python
from pydantic import BaseModel, Field

class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Lower runs earlier")

    def __init__(self):
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: dict) -> dict:
        # body = full OpenAI-style payload before the engine sees it.
        # Mutate and return. Raise to reject the request (client gets 400,
        # the error is written into the chat message).
        return body

    def stream(self, event: dict) -> dict:
        # one parsed SSE chunk; return it (possibly modified),
        # or return None to drop the chunk. Sees reasoning_content deltas too.
        return event

    async def outlet(self, body: dict) -> dict:
        # completed-turn payload {model, chat_id, id, messages:[...]};
        # messages carry usage/sources/error/interrupted markers.
        return body
```

- All three hooks optional. Chain order across filters: ascending
  `valves["priority"]` — expose `priority` as a Valves field so admins can order.
- **Where hooks run**: `inlet` runs on every `/api/chat/completions` call (UI and
  headless) _before_ tool resolution and built-in file-content injection; `stream`
  wraps the model stream on both paths (headless: `__event_emitter__`/`__event_call__`
  are `None` — tolerate that); `outlet` runs when the turn completes on the chat
  wire (the frontend posts `/api/chat/completed`) — headless clients must call
  that endpoint themselves to get outlet processing.
- A module-level `file_handler = True` on an inlet filter claims file handling:
  the built-in `<source>` injection is skipped and your filter owns the files.

### 7.2 Pipe — self-made models

A `class Pipe` with a `pipe(self, body, ...)` method appears as a model (or, with
a module-level `pipes = [{"id","name"},...]` list, as several — a "manifold").
Use it for routing/aggregation agents that should sit in the model selector.
`pipe` may return a `str`, a (sync or async) generator of `str` (wrapped into
OpenAI delta chunks) or of `dict` (sent as raw SSE data lines, so you can emit
real OpenAI chunks), a single `dict`, or a `StreamingResponse` to proxy verbatim.
Pipes receive `__tools__`, `__task__`/`__task_body__` (when used as the task
model) among the injected params, but not `__model__`. Filters assigned to the
wrapping preset still run around a pipe.

### 7.3 Attachment — the upload extraction hook

Nucllei ships **no document extractor**. At most one Attachment Function (chosen
globally by `ATTACHMENT_FUNCTION_ID`, §3.7 / §4.5) decides how an uploaded file
becomes model-visible text. Acceptance itself (size / extension / permission) is
core config, not the function's job.

```python
class Attachment:
    class Valves(BaseModel):
        docling_url: str = "http://docling:5001"

    def __init__(self):
        self.valves = self.Valves()

    def process(self, file, __event__: str) -> str | None:
        # file: the file record object (file.id, file.filename, file.meta, file.path)
        # __event__: "upload" (new upload) | "attach" (explicit re-process)
        if __event__ == "attach" and (file.data or {}).get("content"):
            return None                          # keep existing extraction
        if not file.filename.lower().endswith((".pdf", ".docx", ".xlsx")):
            return None                          # skip → file stays unextracted
        from markitdown import MarkItDown        # bundled in the image
        from nucllei.storage.provider import Storage
        return MarkItDown().convert(Storage.get_file(file.path)).text_content
```

Contract — `process` is a pure function; the server persists the outcome:
return `str` → `file.data = {"content": ..., "status": "success"}`;
**raise** → `status: "failed"` with the exception message as content;
return `None` → skip (file stays unextracted). All three states are surfaced to
the model on the injected `<source>` tag (`extraction="failed"/"none"`), so an
unextracted file can still be handled by a tool via its `file_id`. Multiple
providers should be routed _inside_ one function, not via multiple functions.

When it fires — timing facts for clients and hook authors:

- **Upload** (`POST /api/v1/files/`): the hook runs _inline in the upload
  request_, after the acceptance gate (permission → extension → size) passes and
  the file row is stored. The upload **response waits for extraction** and
  already carries `data.content` / `data.status` — no polling needed. Sync
  `process` runs in a threadpool, so slow extraction doesn't stall the server,
  but the uploading client does wait; budget client timeouts accordingly (or
  upload with `?process=false` and call `/process` separately). If the hook
  machinery itself errors, the upload still returns the file with an `error`
  field rather than failing.
- **Re-process** (`POST /api/v1/files/{id}/process`, `__event__="attach"`): the
  hook is dispatched only when the caller is the file's **owner or an admin** —
  a non-owner with read access just gets the current file back unchanged (no
  error); without read access it's a 404.

### 7.4 Action — human-triggered buttons

A `class Action` with an `action(self, body, ...)` method adds a button under the
last assistant message (module-level `actions = [{"id","name","icon_url"?},...]`
for several). Clicking posts the conversation to your handler; the returned dict
can update messages, and emitted events are delivered in the response. Actions
are kept for deliberately _human-initiated_ operations — for model-initiated
interaction, tools with `__event_call__` cover dialogs/modals already.

### 7.5 Porting notes from stock Open WebUI

If you've written Open WebUI functions before, these no longer exist: docstring
frontmatter and `requirements:` auto-install, `is_active`/global toggles and the
per-chat function controls, toggle-filters with icons, community/store import,
and `__session_id__` (no socket.io — use `__chat_id__`/`__message_id__`).
