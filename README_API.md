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
    is the git tag, one format only: `vYYYYMMDD-NN` (releases). Resolution at
    runtime: `APP_VERSION` env → `git describe` (source runs) → `package.json` fallback.
  - `PRELOAD_WHISPER_MODEL` — if non-empty (e.g. `small`), the faster-whisper STT
    model is downloaded into the image at _build_ time, so air-gapped targets never
    fetch at first use.
- **Image facts**: `python:3.11-slim-bookworm` runtime; `ffmpeg` (audio), `pandoc`
  and `markitdown[pdf,docx,pptx,xlsx,xls,outlook,epub]` (available to your Attachment Functions), bundled
  faster-whisper STT. Baked env: `ENV=prod`, `PORT=8080`, `DATA_DIR=/app/data`,
  `EXT_DIR=/ext` (§3.8), `WHISPER_MODEL_DIR=/app/data/cache/whisper/models`,
  `HF_HOME=/app/data/cache/hf` (both model caches live under the data volume).
- **Port**: `EXPOSE 8080`. `PORT` is consumed by the container CMD / HEALTHCHECK
  shell only; the app itself takes `serve --port`.
- **Volume contract**: mount `/app/data` (= `DATA_DIR`). It holds the SQLite DB
  (`webui.db`), uploads, model caches (whisper / HF), and
  the auto-generated secret key at `/app/data/.webui_secret_key`. No volume ⇒ all
  state is lost on container replacement.
- **Secret key**: if `WEBUI_SECRET_KEY` is unset, `serve` (and `create-admin`) loads
  `$DATA_DIR/.webui_secret_key`, generating and persisting it on first boot. Set the
  env explicitly if you rotate keys or run multiple replicas. Any other entrypoint
  (`uvicorn nucllei.main:app`, `nucllei dev`, a plain `import nucllei.env`) has no key
  file to fall back on: with `ENV=dev` it uses a fixed development key and logs a
  warning; with any other `ENV` (the image bakes `ENV=prod`) it **refuses to start**
  until `WEBUI_SECRET_KEY` is set (2026-10-03 — a known fixed key would let anyone
  forge sessions).
- **HEALTHCHECK**: `GET /health` must return `{"status": true}`.
- **DB migrations** run automatically on startup (and on `create-admin`).

### CLI

The backend ships a `nucllei` console script (the container CMD is `nucllei serve --port $PORT`;
from a source checkout use `uv run nucllei ...`). Commands:

| Command                                            | Purpose                                                                                        |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `serve --host 0.0.0.0 --port 8080`                 | production server (uvicorn, `UVICORN_WORKERS` workers)                                         |
| `dev --reload`                                     | development server, single worker, no secret-key persistence                                   |
| `create-admin --email X [--name N] [--password P]` | provision an admin from the server side (prompts for password if omitted; works on a fresh DB) |
| `--version`                                        | print version                                                                                  |

Bootstrap the first admin with `create-admin` (e.g.
`docker exec -it <ctr> nucllei create-admin --email ...`),
then manage users/groups from the admin UI or API.

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
`RESET_CONFIG_ON_START=true` deletes the stored runtime config (`config` table) during
app startup (lifespan). Note the ordering: RC values are resolved at import time,
_before_ the wipe, so the process that performs the wipe still runs with the old DB
values; the clean state (env / built-in defaults only) takes effect from the next
restart. Set it only for that one boot and unset it again.

**Naming families**: `APP_*` refers to the Nucllei product itself (`APP_VERSION`);
`WEBUI_*` is the deployment layer on top — branding and site behavior
(`WEBUI_NAME`, `WEBUI_VERSION`, `WEBUI_COPYRIGHT` show _your harness's_ identity in
the UI, next to the Nucllei product version).

**Renamed / removed 2026-10-03** (no aliases — the old names are dead; update
compose/env files):

| Old                                                | New                          | Note                                                                                                                                                                                                                     |
| -------------------------------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `JWT_EXPIRES_IN`                                   | `SESSION_IDLE_TIMEOUT`       | RC; DB path `auth.jwt_expiry` → `auth.session_idle_timeout` (an old DB value is not migrated — set the env or re-save in the admin UI)                                                                                   |
| `CHAT_TOOL_EXECUTION_TIMEOUT`                      | `TOOL_EXEC_TIMEOUT_S`        | §3.6                                                                                                                                                                                                                     |
| `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS`           | `TOOL_CALL_MAX_ITERATIONS`   | §3.6                                                                                                                                                                                                                     |
| `SUBAGENT_TURN_TIMEOUT`                            | `SUBAGENT_TURN_TIMEOUT_S`    | §3.6 / §5.8                                                                                                                                                                                                              |
| `SUBAGENT_MAX_CONCURRENT`                          | `SUBAGENT_MAX_CONCURRENCY`   | §3.6 / §5.8                                                                                                                                                                                                              |
| `ASYNC_EXEC_TIMEOUT`                               | `ASYNC_EXEC_TIMEOUT_S`       | §3.6 / §5.9                                                                                                                                                                                                              |
| `ASYNC_EXEC_MAX_CONCURRENT`                        | `ASYNC_EXEC_MAX_CONCURRENCY` | §3.6 / §5.9                                                                                                                                                                                                              |
| `EXEC_RETENTION_DAYS`                              | `ASYNC_EXEC_RETENTION_DAYS`  | §3.6 / §5.9                                                                                                                                                                                                              |
| `ATTACHMENT_MAX_SIZE`                              | `ATTACHMENT_MAX_SIZE_MB`     | RC (DB path `attachment.max_size` unchanged); the admin form field `/api/v1/configs/attachment` follows the new name; `/api/config` still exposes `file.max_size`                                                        |
| `BYPASS_MODEL_ACCESS_CONTROL`                      | _(removed)_                  | group ACL on models always applies                                                                                                                                                                                       |
| `USE_CUDA_DOCKER`                                  | _(removed)_                  | built-in whisper STT runs on `cpu`; the torch device probe is gone (§3.9)                                                                                                                                                |
| `ENABLE_OLLAMA_API`                                | (default `true` → `false`)   | §3.5                                                                                                                                                                                                                     |
| `ENABLE_ADMIN_EXPORT` / `ENABLE_ADMIN_CHAT_ACCESS` | _(removed)_                  | admins can always export the DB / all chats and open any user's chat (a narrower admin needs a separate role, not a toggle); `/api/config` no longer exposes `features.enable_admin_export` / `enable_admin_chat_access` |

`SANDBOX_URL` / `SANDBOX_TOKEN` / `SANDBOX_WRITE_FILE_LIMIT_BYTES` keep their names
but are now read in `nucllei/env.py` like every other env (they used to be read
inside `builtin_tools/sandbox.py`).

**File-based customization (`/app/custom`)**: brand _text_ comes from env vars
above; brand _files_ come from a single fixed directory. Mount a host dir
read-only at `/app/custom` (repo checkout: `./custom`, gitignored) and drop
files with these designated names — each one overrides the bundled default and
is read per request (edits apply without a restart):

| File                                  | Used as                                             |
| ------------------------------------- | --------------------------------------------------- |
| `favicon.png`                         | browser tab, in-app home button, webhook icon       |
| `brand.png`                           | boot splash + auth page logo                        |
| `model.png`                           | default model avatar (bundled default: gray prism)  |
| `pending.md`                          | pending-approval page content (§5)                  |
| `favicon-192.png` / `favicon-512.png` | PWA manifest icons (only referenced when PWA is on) |

### 3.2 Core / server

| Var                                                                                                                         | Kind | Default                                       | Effect                                                                                    |
| --------------------------------------------------------------------------------------------------------------------------- | ---- | --------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `ENV`                                                                                                                       | env  | `dev` (`prod` in Docker)                      | dev exposes Swagger UI at `/docs`                                                         |
| `DATA_DIR`                                                                                                                  | env  | `./data` (`/app/data`)                        | root of all persistent state; `$EXT_DIR` is the `/ext/**` served root (§3.8)              |
| `DATABASE_URL`                                                                                                              | env  | `sqlite:///$DATA_DIR/webui.db`                | SQLAlchemy URL; Postgres supported (`postgres://` auto-rewritten)                         |
| `DATABASE_SCHEMA` / `DATABASE_POOL_SIZE` / `DATABASE_POOL_MAX_OVERFLOW` / `DATABASE_POOL_TIMEOUT` / `DATABASE_POOL_RECYCLE` | env  | — / `0` (NullPool) / `0` / `30` / `3600`      | Postgres tuning                                                                           |
| `UVICORN_WORKERS`                                                                                                           | env  | `1`                                           | worker count for `serve` — keep at 1 (multi-worker unsupported)                           |
| `WEBUI_URL`                                                                                                                 | RC   | `http://localhost:3000`                       | public base URL (webhooks, links)                                                         |
| `WEBUI_NAME`                                                                                                                | env  | `Nucllei`                                     | brand name shown in the UI                                                                |
| `WEBUI_VERSION` / `WEBUI_COPYRIGHT`                                                                                         | env  | `""`                                          | deployment-layer version / copyright on the License tab (empty = hidden)                  |
| `ENABLE_FORWARD_USER_INFO_HEADERS`                                                                                          | env  | `false`                                       | send `X-Nucllei-User-{Name,Id,Email,Role}` to the upstream engine                         |
| `CORS_ALLOW_ORIGIN`                                                                                                         | env  | `*`                                           | `;`-separated origins; empty = same-origin only (no CORS, no warning)                     |
| `ENABLE_PWA`                                                                                                                | env  | `false`                                       | serve an installable `/manifest.json` (name follows `WEBUI_NAME`); off returns `{}`       |
| `OFFLINE_MODE`                                                                                                              | env  | `false`                                       | forces HF hub offline + disables whisper auto-update                                      |
| `STATIC_DIR` / `FONTS_DIR`                                                                                                  | env  | `<package>/static` / `<package>/static/fonts` | bundled static assets / font files (source-run overrides; the image uses the defaults)    |
| `FRONTEND_BUILD_DIR`                                                                                                        | env  | `./build` (`/app/build`)                      | compiled SvelteKit frontend served at `/`                                                 |
| `RESET_CONFIG_ON_START`                                                                                                     | env  | `false`                                       | delete stored runtime config at startup — takes effect from the _next_ restart (see §3.1) |

### 3.3 Auth & security

| Var                                                                                                                                                                                     | Kind | Default                 | Effect                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WEBUI_AUTH`                                                                                                                                                                            | env  | `true`                  | `false` = single-user, no-auth mode                                                                                                                                                                                                                                                                              |
| `WEBUI_SECRET_KEY`                                                                                                                                                                      | env  | auto-generated (see §2) | JWT signing key. Required outside `ENV=dev` unless started via `serve` (§2)                                                                                                                                                                                                                                      |
| `SESSION_IDLE_TIMEOUT`                                                                                                                                                                  | RC   | `-1` (none)             | **idle** timeout: log out after this long _without user input_ — the clock restarts on every keypress/click, so an in-use session never expires (see §4.1). `-1` / `0` = none; otherwise `<n>` + `s`,`m`,`h`,`d`,`w` (e.g. `30m`, `4h`, `1d`). Was `JWT_EXPIRES_IN` (DB path `auth.jwt_expiry`) until 2026-10-03 |
| `ENABLE_SIGNUP`                                                                                                                                                                         | RC   | `true`                  | public signup (auto-disabled after the first user becomes admin)                                                                                                                                                                                                                                                 |
| `ENABLE_LOGIN_FORM`                                                                                                                                                                     | RC   | `true`                  | email/password form (off for SSO-only)                                                                                                                                                                                                                                                                           |
| `DEFAULT_USER_ROLE`                                                                                                                                                                     | RC   | `pending`               | role given to new signups                                                                                                                                                                                                                                                                                        |
| `ENABLE_LOGIN_AUTOCOMPLETE`                                                                                                                                                             | RC   | `true`                  | `false` = shared-PC posture for the login form (see `_memos/auth-profile.md`)                                                                                                                                                                                                                                    |
| `ENABLE_API_KEY`                                                                                                                                                                        | RC   | `true`                  | allow user API keys (`sk-...`, `Authorization` header only — never the `token` cookie; §4.1)                                                                                                                                                                                                                     |
| `ENABLE_API_KEY_ENDPOINT_RESTRICTIONS`                                                                                                                                                  | RC   | `false`                 | restrict API keys to a path allowlist                                                                                                                                                                                                                                                                            |
| `API_KEY_ALLOWED_ENDPOINTS`                                                                                                                                                             | RC   | `""`                    | comma-separated path prefixes; **empty list + restrictions on = deny all API-key requests**                                                                                                                                                                                                                      |
| `WEBUI_SESSION_COOKIE_SAME_SITE` / `WEBUI_SESSION_COOKIE_SECURE`                                                                                                                        | env  | `lax` / `false`         | session cookie flags                                                                                                                                                                                                                                                                                             |
| `WEBUI_AUTH_COOKIE_SAME_SITE` / `WEBUI_AUTH_COOKIE_SECURE`                                                                                                                              | env  | = session values        | auth (`token`) cookie flags                                                                                                                                                                                                                                                                                      |
| `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` / `WEBUI_AUTH_TRUSTED_NAME_HEADER`                                                                                                                    | env  | unset                   | header-based SSO behind a trusted proxy                                                                                                                                                                                                                                                                          |
| `CACHE_CONTROL`, `HSTS`, `PERMISSIONS_POLICY`, `REFERRER_POLICY`, `XCONTENT_TYPE`, `XDOWNLOAD_OPTIONS`, `XFRAME_OPTIONS`, `XPERMITTED_CROSS_DOMAIN_POLICIES`, `CONTENT_SECURITY_POLICY` | env  | unset                   | each sets the corresponding response security header when present                                                                                                                                                                                                                                                |

OAuth (Google / Microsoft / GitHub / generic OIDC via `GOOGLE_CLIENT_ID`&`_SECRET`,
`MICROSOFT_CLIENT_ID`&`_SECRET`&`_TENANT_ID`, `GITHUB_CLIENT_ID`&`_SECRET`,
`OAUTH_CLIENT_ID`&`_SECRET`&`OPENID_PROVIDER_URL`; gates `ENABLE_OAUTH_SIGNUP`,
`OAUTH_MERGE_ACCOUNTS_BY_EMAIL`, role/group management flags) and LDAP
(`ENABLE_LDAP`, `LDAP_SERVER_HOST/PORT`, `LDAP_APP_DN/PASSWORD`, `LDAP_SEARCH_BASE/FILTER`,
attribute mappings, TLS options) are available as RC settings; all default off.

### 3.4 User permissions & metadata

Env vars here only seed the **default** permission set; actual permissions are
managed per-group in the DB/UI.

| Var                                                 | Default             | Effect                                                                                                                                                                                                      |
| --------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `USER_PERMISSIONS_WORKSPACE_SKILLS_ACCESS`          | `false`             | grants the "skill editor" tier (see §6)                                                                                                                                                                     |
| `USER_PERMISSIONS_CHAT_FILE_UPLOAD`                 | `true`              | file upload (server-enforced)                                                                                                                                                                               |
| `USER_PERMISSIONS_CHAT_DELETE` / `_EDIT` / `_SHARE` | `true`              | chat deletion / message editing / share links                                                                                                                                                               |
| `USER_PERMISSIONS_CHAT_CLONE`                       | `false`             | cloning shared chats                                                                                                                                                                                        |
| `ADDITIONAL_USER_META`                              | `[]` (RC)           | JSON array `[{"key":"carte_user_id","label":"..."}]` declaring extra string user fields, editable by admins and delivered to tools as `__user__["info"]` — opaque markers for external-system authorization |
| `DEFAULT_LOCALE`                                    | `""` (RC)           | UI locale (shipped: `ja`, `en`, `zh-CN`)                                                                                                                                                                    |
| `DEFAULT_MODELS`                                    | unset (RC)          | default model(s) for new chats                                                                                                                                                                              |
| `WEBUI_BANNERS`                                     | `[]` (RC)           | JSON banner list `{id,type,title?,content,dismissible,timestamp}`                                                                                                                                           |
| `WEBHOOK_URL` / `ENABLE_USER_WEBHOOKS`              | `""` / `false` (RC) | server webhook (signup events) / per-user webhooks                                                                                                                                                          |

### 3.5 Model backend (OpenAI-compatible engine)

| Var                                                       | Kind     | Default                            | Effect                                                                                                                                                                                                                                                     |
| --------------------------------------------------------- | -------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ENABLE_OPENAI_API`                                       | RC       | `true`                             | master switch                                                                                                                                                                                                                                              |
| `OPENAI_API_BASE_URL` / `OPENAI_API_KEY`                  | env seed | `https://api.openai.com/v1` / `""` | single-connection convenience; folded into the plural forms. **Not an RC override**: the env-wins rule (§3.1) keys on the plural names, so once an admin has saved the connection in the UI, the singulars are ignored. Deployments should set the plurals |
| `OPENAI_API_BASE_URLS` / `OPENAI_API_KEYS`                | RC       | from above                         | `;`-separated endpoint/key lists, index-matched. These are the names the env override honours on every boot                                                                                                                                                |
| `ENABLE_OLLAMA_API`                                       | RC       | `false`                            | ollama backend (default was `true` until 2026-10-03)                                                                                                                                                                                                       |
| `OLLAMA_BASE_URL(S)`                                      | env/RC   | localhost                          | ollama endpoints                                                                                                                                                                                                                                           |
| `AIOHTTP_CLIENT_TIMEOUT`                                  | env      | unset (unlimited)                  | total chat-request timeout — leave unset for long generations                                                                                                                                                                                              |
| `AIOHTTP_CLIENT_TIMEOUT_CONNECT`                          | env      | `10`                               | TCP connect timeout (dead-connection detection)                                                                                                                                                                                                            |
| `AIOHTTP_CLIENT_TIMEOUT_SOCK_READ`                        | env      | `300`                              | max silent gap between streamed bytes                                                                                                                                                                                                                      |
| `AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST` / `_TOOL_SERVER_DATA` | env      | `10` / `10`                        | model-list / tool-server fetch timeouts                                                                                                                                                                                                                    |

Reference wiring for a local engine:
`OPENAI_API_BASE_URLS=http://host.docker.internal:13300/v1`, `OPENAI_API_KEYS=EMPTY`,
`ENABLE_OLLAMA_API=false`. Reasoning models are supported: `reasoning_content`
deltas from the engine's reasoning parser flow through the wire and render as a
separate reasoning block.

### 3.6 Tool calling & tasks

| Var                                                            | Kind | Default               | Effect                                                                                                                                                           |
| -------------------------------------------------------------- | ---- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TOOL_CALL_MAX_ITERATIONS`                                     | env  | `30` (`-1` unlimited) | native tool-loop cap; hitting it surfaces an in-chat error                                                                                                       |
| `TOOL_EXEC_TIMEOUT_S`                                          | env  | `300` s (`-1` off)    | authoritative per-tool-call timeout; expiry is returned to the model as a tool result                                                                            |
| `TOOL_RESULT_MAX_CHARS`                                        | env  | `65536`               | tool results longer than this are truncated once at the producer (sync and background alike), flagged `truncated` / `original_chars` (§5.5)                      |
| `SUBAGENT_TURN_TIMEOUT_S`                                      | env  | `1800` s (`-1` off)   | timeout of one `run_subagent` child turn (§5.8)                                                                                                                  |
| `SUBAGENT_MAX_DEPTH`                                           | env  | `2`                   | nesting limit of agent sessions (user chat = depth 0); `run_subagent` is not offered beyond it (§5.8)                                                            |
| `SUBAGENT_MAX_CONCURRENCY`                                     | env  | `4`                   | per-process cap on simultaneously running subagents; excess calls fail immediately as a tool result (§5.8)                                                       |
| `ASYNC_EXEC_TIMEOUT_S`                                         | env  | `1800` s (`-1` off)   | timeout of one background execution unless the tool declares its own (§5.9)                                                                                      |
| `ASYNC_EXEC_MAX_CONCURRENCY` / `ASYNC_EXEC_MAX_TOTAL`          | env  | `4` / `16`            | background executions running at once per user / per process; excess fails immediately as a tool result (§5.9)                                                   |
| `ASYNC_EXEC_RESUME_CHAIN_LIMIT`                                | env  | `5`                   | how many server-driven delivery turns may chain without a user message; further deliveries wait for the next user turn (§5.9)                                    |
| `ASYNC_EXEC_RETENTION_DAYS`                                    | env  | `0` (keep)            | `> 0`: at boot, drop result/args/meta of finished execution rows older than this many days (rows themselves are kept) (§5.9)                                     |
| `SANDBOX_WRITE_FILE_LIMIT_BYTES`                               | env  | `1048576` (1 MB)      | body cap of the sandbox `write_file` tool (§5.7)                                                                                                                 |
| `TASK_MODEL` / `TASK_MODEL_EXTERNAL`                           | RC   | `""`                  | model used for background tasks (title generation)                                                                                                               |
| `ENABLE_TITLE_GENERATION` / `TITLE_GENERATION_PROMPT_TEMPLATE` | RC   | `true` / built-in     | auto chat titles                                                                                                                                                 |
| `SANDBOX_URL` / `SANDBOX_TOKEN`                                | env  | `""` / `""`           | sandbox base URL (gates `builtin_sandbox`) + optional shared bearer token (§5.7)                                                                                 |
| `SELF_ADDRESSES`                                               | env  | `""`                  | comma-separated IPs added to the "this server itself" set refused by `url=` fetches (host's outward-facing IPs; §3.8). Was `EXT_SELF_ADDRESSES` until 2026-10-03 |
| `FETCH_MAX_BYTES`                                              | env  | `26214400` (25 MB)    | default size cap when a reference (URL or sandbox path) is read by the resolver (§3.8); tools override it per call. Was `EXT_INLINE_MAX_BYTES` until 2026-10-03  |
| (none for `/ext`)                                              | —    | —                     | `/ext/**` serves `$EXT_DIR` as is (§3.8); there is no `EXT_*` serving configuration since 2026-10-03                                                             |

### 3.7 Attachments / files / storage

| Var                              | Kind | Default           | Effect                                                                                                                                                      |
| -------------------------------- | ---- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ATTACHMENT_MAX_SIZE_MB`         | RC   | `25` MB           | upload size cap (server-enforced)                                                                                                                           |
| `ATTACHMENT_MAX_COUNT`           | RC   | unlimited         | max attachments per message                                                                                                                                 |
| `ATTACHMENT_ALLOWED_EXTENSIONS`  | RC   | `[]` = allow all  | lowercase, dot-less whitelist                                                                                                                               |
| `ATTACHMENT_FUNCTION_ID`         | RC   | `""` = none       | id of the single global Attachment Function (§7.3); empty ⇒ uploads store raw bytes, no text extraction                                                     |
| `ATTACHMENT_IMAGE_MAX_DIMENSION` | RC   | unset = no resize | client-side downscale of attached images before upload: if the longest edge exceeds this (px), the image is resized to fit, aspect ratio and MIME preserved |
| `PASTED_TEXT_CHARACTER_LIMIT`    | RC   | unset = disabled  | pasted plain text longer than this (chars) becomes a `.txt` attachment                                                                                      |
| `STORAGE_PROVIDER`               | env  | `local`           | `local` \| `s3` \| `gcs` \| `azure` (each with its own credential envs: `S3_*`, `GCS_BUCKET_NAME`+`GOOGLE_APPLICATION_CREDENTIALS_JSON`, `AZURE_STORAGE_*`) |

### 3.8 External reference gate (`/ext`)

`GET /ext/**` is a session-authenticated, read-only gate that serves **whatever
is under `$EXT_DIR`** (`image_refs.EXT_DIR`; `/ext` in the image,
`./ext` on a host install) at the same relative path:
`ext/cage/static/X` ↔ `/ext/cage/static/X`. There is no configuration: the
deployment decides what is served by what it puts there — a Docker deployment
binds volumes at `/ext/<path>`, a host install puts files or symlinks
under `ext/`. **The sandbox sees the same tree at `/ext`** (its read-only
mount root, #138), so a sandbox path and a `/ext` URL are **the same string**
naming the same file (`/ext/cage/static/X` in the jail, in the browser, and in
a tool's `url=` or `path=`). Point the
upstream's public base at **`/ext/<name>`** (relative — same-origin, so it keeps
working whatever origin or scheme the user reached the server by; an absolute
`https://<nucllei host>/ext/<name>` works too) and in-document references
resolve in the browser with the session cookie, and are handed back to tools
as `url` unchanged (see the reference rules below).

- Files only: `FileResponse` (Range / etag / last-modified). The requested path
  is resolved and confined to its **mount** (`image_refs.served_mount`): the
  resolved `$EXT_DIR` itself, or — walking real directories down from it —
  the first **directory symlink** the deployment placed in that tree
  (`ext/cage/static → ~/…/cage/data/static`), which is served as a mount
  the way a Docker volume is. Symlinks _inside_ mounted content cannot leave
  their mount (→ 404), and a symlink to a single file is never a mount (it is
  indistinguishable from `escape.txt → /etc/passwd` inside content; link a
  directory or copy the file). Directories (including `/ext/` itself) and
  missing files → 404, no listing; content-type from the extension
  (`application/octet-stream` when unknown). `..`/backslash/control-char paths
  → 400; active types (HTML/SVG/JS) are forced to
  `Content-Disposition: attachment` + `nosniff`. There is no per-user access
  check — what is placed under `$EXT_DIR` is what is exposed.
- The startup log lists the top-level entries of `$EXT_DIR` as "served at
  /ext/, sandbox sees them at /ext/".
- Development without Docker: put a symlink under `ext/` (e.g.
  `ext/cage/static → <llens>/cage/data/static`; `data/` is gitignored, no
  root needed) — there is no env override of the served root.

> 2026-10-03 (ken): nucllei serves `$EXT_DIR`; the sandbox sees the same
> tree at `/ext`; `/mnt` abandoned. Earlier that day both sides were fixed to
> the container's `/mnt` — wrong for host installs: nucllei is not Docker-only
> (dev runs on the host), populating `/mnt` needs root, and a host install
> would silently serve whatever the host has under `/mnt` (NFS mounts etc.) to
> every logged-in user. `$EXT_DIR` is nucllei's own directory. The
> sandbox mount root moved from `/mnt` to `/ext` at the same time so that the
> sandbox path and the URL are one string and the model never rewrites one into
> the other (the one deliberate overlap between `url=` and `path=`; the
> argument name still decides how the value is read — `url` percent-decodes,
> `path` is taken as a filesystem path).
>
> Same day, earlier: 「nucllei の /mnt に volume したものをそのまま /ext/ で配信する」.
> Until that day `/ext/{name}/**` was configured by `EXT_UPSTREAMS` (per-name
> http relay or `file://` root), `EXT_FILE_SCOPES` (serving scope under a
> root) and `EXT_PUBLIC_BASES` (original-site roots for link lists); an
> `EXT_DIRS` variable existed for a few hours in between. Once `url=` fetched
> any http(s) URL (see "General URLs"), the http relay and the original-host
> mapping had no purpose left, so the whole `EXT_*` serving configuration was
> removed. The URL shape is unchanged (`/ext/cage/static/X`). In the same
> change `EXT_SELF_ADDRESSES` → `SELF_ADDRESSES` and `EXT_INLINE_MAX_BYTES` →
> `FETCH_MAX_BYTES` (same meaning, same default).

**Reference rules** (the single statement of how a tool argument or a stored
reference is interpreted; the code-side definition is the module docstring of
`nucllei/utils/image_refs.py`):

- **The argument name decides the interpretation. The value is never inspected
  to reinterpret it as a different argument.** A sandbox path in `url`, or a URL
  in `path`, fails as what the argument says it is.
- **`url`** — a URL a browser can open: an absolute `http(s)://…` URL, **or a
  same-server relative URL** (resolved as "a URL of this server", against the
  routes the server really has): `/ext/{rest}` — entry point
  `resolve_url(url, *, max_bytes=None)` — and `/api/v1/files/{id}/content`,
  which `file_id_of_url(url)` routes to a file that the caller then reads with
  its own per-user check.
- **`path`** — an **absolute** path on the sandbox filesystem (`/workspace/…`,
  `/ext/…`). Entry point: `resolve_path(path, user_id, *, max_bytes=None)`.
  `/ext/{rest}` is accepted by both `url` and `path` and names the same file
  (`$EXT_DIR/{rest}` on nucllei's side); `url` percent-decodes `rest`,
  `path` reads it as given.
  Relative paths are not accepted (since 2026-10-03; they used to be resolved
  against `/workspace`, which turned `path="https://x/y.html"` into
  `/workspace/https:/x/y.html`).
- **`file_id`** — the id of an attachment or a generated file.
- **Pass references along exactly as received** — do not rewrite them, and do
  not prepend an origin to a relative URL.

**Fixed sentences for argument-form errors.** A tool never inspects the value
to choose its wording; a form error states what the argument accepts in a fixed
sentence. The sentences are public constants of `nucllei/utils/image_refs.py`.
The builtin tools that take reference arguments (`builtin_feed_image`,
`builtin_fetch_url`, `builtin_sandbox` — §5.7) build their errors from them
through one shared module, `nucllei/utils/ref_errors.py` (both the form errors
below and the per-`RefError.code` wording), so the same input fails with the
same text in every tool. A **deployment-layer tool that takes a `url` / `path`
/ `file_id` argument must import the same constants and use the same wording**
(until 2026-10-03 `feed_image` / `fetch_url` were deployment-layer tools and
each carried its own copy of this text; the copies drifted — see §5.7):

| Constant                  | Text                                                                                                                                     |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `URL_ARG_FORM`            | ``url が受けるのは、スキーム付きの絶対 URL (`http(s)://…`) と、同一サーバの `/ext/…`・`/api/v1/files/{id}/content` だけ。``              |
| `PATH_ARG_FORM`           | ``path が受けるのは、sandbox の絶対パス (`/workspace/…` `/ext/…`) だけ。``                                                               |
| `WORKSPACE_PATH_ARG_FORM` | ``path が受けるのは、sandbox の絶対パス (`/workspace/…`) だけ。`` (tools that only handle the workspace: `write_file`, `list_workspace`) |
| `FILE_ID_ARG_FORM`        | `file_id は添付・生成物の id。`                                                                                                          |
| `REF_ARGS_RULE`           | `URL は url、sandbox のパスは path、添付・生成物は file_id に、受け取った形のまま渡す (値を書き換えて別の引数に入れ直さない)。`          |

`REF_ARGS_RULE` is the one sentence about where things go, identical in every
tool (it mentions arguments a given tool may not have — it states the rule, not
that tool's signature); tools do not add destinations of their own. These
constants are **model-only** (§5.1), so they always come after `次の手: `.
Errors are assembled the same way everywhere:

- `url` not resolvable as a URL:
  `ERROR: <value> は URL として解決できない。次の手: ` + `URL_ARG_FORM` + `" "` + `REF_ARGS_RULE`
- `path` not a sandbox path:
  `ERROR: <value> は sandbox のパスの形でない。次の手: ` + `PATH_ARG_FORM` (or
  `WORKSPACE_PATH_ARG_FORM`) + `" "` + `REF_ARGS_RULE`
- `file_id` cannot be read:
  `ERROR: file_id <id> のレコードが無い、またはアクセス権が無い。次の手: ` + `FILE_ID_ARG_FORM`
  — "no such record" and "not yours" are deliberately one sentence (existence
  is not leaked). `read_attachment` raises the same error (`ref_errors.file_id_error`).

These helpers (and `ref_errors.explain_ref_error`) return a `ToolError` that
the tool raises (§5.1 "Failures"); the kind follows the code — `REF_ERROR_KINDS`:
`not_found` / `too_large` → `outcome`, `unsupported_form` / `unknown_upstream` /
`traversal` (and the three fixed-form helpers) → `call`, `upstream_status` /
`upstream_unreachable` / `workspace_unavailable` → `failure`, and an
unexplained error (no `RefError`, or a code the table does not know — a gap on
our side) → `fatal` — except `upstream_status` with HTTP 404 / 410, which is `outcome`
like `not_found` (401 / 403, other 4xx, 5xx and redirects that could not be
followed stay `failure`).

Wording that depends on the **resolution result** (missing / oversize /
redirect that could not be followed / upstream status / unreachable / the
server itself) is separate and built per `RefError.code` (and `reason`), still
without looking at the value. Each text is a **fact sentence + `次の手:`**
(§5.1): e.g. `ERROR: <url> は存在しない (404 / ファイル無し)。次の手: ユーザーには
リンク切れと伝える。` Where the model may hand the URL to the user, the next
step is one shared sentence, `ref_errors.SHOW_AS_LINK` —
`ユーザーに示すなら [ラベル](<url>) のリンクにする (ブラウザでは開ける)。` — so a
relative `/ext/…` never reaches an answer as a bare path. The defaults
(`RefErrorHints`) are the same in all three tools; a tool overrides only the
codes whose next step really differs (`too_large`, and `not_found` /
`unreachable` for a `path` argument, which has no browser URL). On
2026-10-04 `send_to_workspace`'s own variants (「登録した配信元の redirect は辿らない」
— stale since redirects are followed —, 「許可先の外」, 「3 回を超えて」) were
dropped for the shared defaults, and no text shows a `RefError.code` any more.

Stored reference strings (entries of `context_images`, the `url` of an
`image_url` part) carry no argument name, so they — and only they — are
dispatched mechanically by their prefix, in one place: `stored_ref_kind(ref)`
(returns `"file_url"`, `"url"`, `"path"`, `"file_id"` or `None`; a trailing
`#page=N` is allowed). `resolve_ref(ref, user_id, *, max_bytes=None)` is a thin
dispatcher over the two entry points above built on it, and the history
builder (`content_blocks.py`) and the background-task completion notice
(`execution_resume._notice_files`) use the same function — nothing else carries
a prefix test of its own. Tools must call `resolve_url` / `resolve_path`
according to the argument's name, not `resolve_ref`.

| Prefix                                           | Meaning                                           | Resolution                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------ | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `http://` / `https://`                           | absolute URL                                      | path `/ext/{rest}` → the local file `$EXT_DIR/{rest}`, **whatever the host** (the host is never connected to). Otherwise it is a **general URL** (below): fetched directly from its host, pinned to the resolved address — **anywhere**, intranet or internet; the only refused destination is this server itself (`unknown_upstream` / `server_self`)                  |
| `/ext/`                                          | same-server URL (= the sandbox's read-only mount) | `/ext/{rest}` → the same local file `$EXT_DIR/{rest}` as the absolute form. Missing, a directory (`/ext/` itself included) or a symlink leaving its mount → `not_found`. A stored `/ext/…` is read as a URL; given as `path` it is the sandbox's `/ext/{rest}` and read locally from the same `$EXT_DIR/{rest}` without percent-decoding (#138)                         |
| `/workspace/`                                    | sandbox filesystem                                | `/workspace/…` goes through the sandbox `/files/read` of the calling user                                                                                                                                                                                                                                                                                               |
| `/api/v1/files/{id}/content`                     | this server's file delivery URL                   | not read by the resolver (it needs a per-user check): `file_id_of_url(url)` extracts the id (relative, or absolute on any host — same reasoning as `/ext/`), and the caller reads the file with its own check — the pre-send inline step (read access) and `send_to_workspace(url=)` (owner). Passed to `resolve_url` / `resolve_ref` directly it is `unsupported_form` |
| a bare string (no leading `/`, not `http(s)://`) | a file id (what `__feed_image__(file_id)` stores) | the caller builds `/api/v1/files/{id}/content` from it (history builder, completion notice; the frontend's `contextImageUrl` does the same)                                                                                                                                                                                                                             |
| anything else (any other `/…`, empty)            | —                                                 | `unsupported_form`                                                                                                                                                                                                                                                                                                                                                      |

**Directories.** `/ext/` and `/ext/{dir}/` are well-formed URLs (`rel` is the
path under `$EXT_DIR`, possibly empty) but a directory is never served: `not_found`
from the resolver, 404 from the gate, no listing.

**General URLs (2026-10-03).** Registered backends are not the only web a
hospital has, and the `url=` argument of a tool is a URL the way a browser
understands it. An absolute `http(s)://` URL that is not of the `/ext/{rest}` shape is fetched directly
from its host, with no registration, the way an ordinary browser would — on the
intranet or on the internet. **The tool does not block by destination.**
Whether a host can be reached is a property of the deployment's network: on the
air-gapped production network the internet is simply unreachable
(`upstream_unreachable`), and "the closed network does not reach outside" is a
sentence for the prompt / skill, not a gate in the resolver. The only
destination refused is **this server itself and the services co-located with
it** (`unknown_upstream` / `server_self`) — e.g. the host's unauthenticated
cage API, the sandbox API, a cloud metadata address. The self set is computed
at runtime (own interfaces, default gateway, `host.docker.internal`) **plus**
`SELF_ADDRESSES`, a comma-separated list of IP addresses the deployment
adds — a container cannot enumerate the host's physical / VPN interfaces, so
the host's outward-facing IPs (where a co-located service is published)
must be declared there (default empty; the startup log lists both sets as
`self addresses` / `設定で追加 (SELF_ADDRESSES)`).

> History: on the same day an allowlist `EXT_INTRANET_CIDRS` ("fetch only
> inside the declared intranet range") was added in the morning and removed in
> the afternoon (ken). It gated a function by destination, and its fixed deny
> entry for Docker's default bridge (`172.17.0.0/16`) was wrong anyway: the
> production bridge is `10.106.x`, so a fixed CIDR could never have protected
> the host while it risked excluding real intranet segments. There is no CIDR
> variable left; the self-deny set below is computed at runtime.

- **Hosts are usually IP literals.** The air-gapped production network has no
  DNS, so intranet URLs there look like `http://10.1.2.3/his-info/top.aspx`. An
  IPv4 / IPv6 literal (bracketed `[fd00::7]` included) is checked as is, no
  lookup. A host **name** is resolved with `getaddrinfo` (so `/etc/hosts`
  works); a name that does not resolve is `upstream_unreachable` with
  `reason=unresolvable` (tools say "use the IP address when there is no DNS").
- **The connection is pinned to the resolved address.** The resolver connects
  to the IP it checked and sends the original host in the `Host` header (and as
  SNI for https); it never lets the HTTP client resolve the name a second
  time, so a DNS-rebinding answer cannot steer the fetch to the server itself
  (`ext_upstreams.pin_address`). A name that resolves to both an outside
  address and a self address is refused (`server_self`).
  TLS is verified normally: a hostname URL is checked against the certificate
  with SNI = that hostname; an **IP-literal https URL** (closed-network
  production) sends no SNI and the certificate must list that IP. Intranet
  servers with self-signed or private-CA certificates therefore fail with a
  verification error unless the CA is trusted in the container — mount the CA
  bundle and point `SSL_CERT_FILE` (or `SSL_CERT_DIR`) at it; httpx honours
  both. nucllei does not disable verification.
- **The self-deny set is computed at runtime, not configured**
  (`ext_upstreams.self_addresses` / `is_self_address`, cached 60 s). It is
  the union of (1) addresses that can never be a legitimate destination —
  loopback (`127.0.0.0/8`, `::1`), unspecified (`0.0.0.0/8`, `::`), link-local
  (`169.254.0.0/16`, which includes the cloud metadata address
  `169.254.169.254`, and `fe80::/10`), multicast and the reserved
  `240.0.0.0/4` — **this is not a network policy: no private range (`10/8`,
  `172.16/12`, `192.168/16`) and no Docker bridge range is listed, because on
  the production network those are the real intranet** — and (2) what this
  process discovers about its own environment: every address on its own
  interfaces (`/proc/net/fib_trie` for IPv4, `/proc/net/if_inet6` for IPv6,
  plus `getaddrinfo(gethostname())`), the IPv4 / IPv6 default gateway
  (`/proc/net/route`, `/proc/net/ipv6_route` — inside a container this is the
  Docker bridge's host side, i.e. where co-located host services listen), and
  the resolutions of `host.docker.internal` / `gateway.docker.internal` when
  they resolve. Known limits: outside Linux there is no `/proc`, so only the
  hostname's addresses are found and the gateway is not; a host service bound
  to a **different** host interface (not the bridge gateway, not a container
  address — e.g. the host's LAN address) is not discovered, so such a service
  must carry its own authentication (as the sandbox does with `SANDBOX_TOKEN`).
  The discovered set is logged once at boot.
- **Redirects are followed, at most 3 times.** The `Location` (absolute or
  relative) is resolved as a URL by the same rules: a general URL → followed
  (pinned again, same self check); a `/ext/{rest}` URL → the local file under
  `$EXT_DIR` (no further redirect); a `Location` pointing at this server itself →
  `upstream_status` with the `3xx` status and `reason=redirect_self`; a
  `Location` that is not a URL this server resolves (non-http(s) scheme,
  control characters) → `upstream_status` with the `3xx` status and no reason
  (a redirect that was not followed). A fourth redirect → `upstream_status`
  with `reason=too_many_redirects`.
- Size cap (`max_bytes`), content-type (upstream header), `404 → not_found`,
  other non-2xx → `upstream_status`, connection failure →
  `upstream_unreachable`. No cookie or credential is sent.
- `route_of_url` returns such a URL as `UrlRoute(kind="general", …)` without
  resolving the name (it stays pure); see below. The bridges
  (`mounted_path_for_url` / `url_for_mounted_path`) and the `/ext` gate are
  unaffected — a general URL has no sandbox counterpart.
- Internal calls that tools make themselves (e.g. a deployment tool talking to
  its own API over the Docker bridge) do not go through the resolver and are
  unaffected by the self-deny set.

**Fragments on `/api/v1/files/{id}/content`.** Only a trailing `#page=N` means
something (read with `split_page`); any other fragment is ignored and the URL
routes to the same file — `file_id_of_url` returns the id with or without a
fragment, so every tool treats `…/content#section` alike.

**Where a URL belongs, without reading it.**
`route_of_url(url) -> (UrlRoute | None, RefError | None)` runs the same single
routing step as `resolve_url` and returns it: `UrlRoute(rel, query, origin,
kind)` plus the properties `relative`, `general` and `page_url`. `kind` is
`"ext"` (`/ext/{rest}`; `rel` = the path under `$EXT_DIR` as written in the URL,
i.e. still percent-encoded, no leading `/`, `""` for `/ext/` itself),
`"general"` (`rel` = the URL path without its leading `/`, `""` for
`http://h/`) or `"file"` (this server's `/api/v1/files/{id}/content`, relative
or on any host; `rel` = the file id — `route_of_url` is the only place that
returns it, since `resolve_url` cannot read a file without a per-user check and
answers `unsupported_form` for the relative form). `query` is `"?a=b"` or `""`;
`origin` is `"scheme://host[:port]"` as given (never connected to) or `None`
for a relative URL. It is pure — no network, no filesystem, no logging — and
fails with the same `code` / `reason` `resolve_url` would give for that input
(`unsupported_form`, `traversal`); it does **not** check existence (a
`/ext/{rest}` that is not mounted routes fine and is `not_found` only when
read). Because the function is pure it does not resolve the host either — a
general URL whose host is this server itself is `unknown_upstream` /
`server_self` only at fetch time; calling it after a successful `resolve_url`,
as intended, never disagrees.

`page_url` is the base for in-page links, **the page's own URL** in every
case (the ordinary web rule): `{origin}/{rel}{query}` for a general URL,
`/ext/{rel}{query}` for a relative `/ext` input, `{origin}/ext/{rel}{query}`
for an absolute one, `None` for a file. `builtin_fetch_url` builds its link
list by `urljoin(page_url, href)`; there is no "upstream root" concept any
more (until 2026-10-03 `UrlRoute` carried `name`, `public_base` and
`ext_form`, and the link root was the `EXT_PUBLIC_BASES` root or
`/ext/{name}`).

**Bridging a URL and a sandbox path.** The same file has the same name on both
sides since 2026-10-03: URL `/ext/cage/static/X` = sandbox path
`/ext/cage/static/X` = nucllei's local `$EXT_DIR/cage/static/X`. The model
never has to rewrite anything; what the tool still decides, based on **what the
reference resolves to** (whether the file exists on nucllei's side), is whether
to copy / attach or to hand the name back:

| Direction                                  | Tool (builtin sandbox)       | Resolver function            | Result                                                                                                                                                                                                                                                               |
| ------------------------------------------ | ---------------------------- | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| URL → a path the sandbox can open          | `send_to_workspace(url=)`    | `mounted_path_for_url(url)`  | the sandbox path `/ext/{rest}` (same string, percent-decoded) when `/ext/{rest}` names a regular file that exists under nucllei's `$EXT_DIR`, nothing copied; otherwise the bytes are copied to `/workspace/imports/`                                                |
| sandbox path → something the user can open | `fetch_from_workspace(path)` | `url_for_mounted_path(path)` | the relative `/ext/{rest}` URL (same string, percent-encoded) when `$EXT_DIR/{rest}` exists on nucllei's side, nothing attached; otherwise the file is read from nucllei's `$EXT_DIR` and attached — a path that is not there is "not on this server's /ext" (below) |

Both are an existence check on nucllei's own filesystem (`$EXT_DIR`), after
resolving symlinks and confining to the mount (`served_mount`); the names the
deployment placed in `$EXT_DIR` (including directory-symlink mounts) are
kept in the result, symlinks inside a mount are folded to the real file.
The path part of the returned URL is percent-encoded, and the URL round-trips
through `resolve_url` / the `/ext` gate to the same file; because of the
encoding it can differ **as a string** from the URL a document's text carries
for the same file (e.g. raw Japanese `…/forms/参考書式_5.pdf` vs
`…/forms/%E5%8F%82…_5.pdf`): two spellings of one file, not two files. Only
URLs can be shown to the user (`/ext/…` or `/api/v1/files/{id}/content`); a
sandbox path means nothing to a browser. Whether the sandbox really binds the
same volume cannot be verified from nucllei — it is the deployment convention
(#138). **Deployments mount the same `/ext` set into nucllei and the sandbox**
(ken, 2026-10-04), so every `/ext` file the sandbox sees has a URL. `/ext`
files are shown to users **by URL only** — there is no extraction path that
reads a sandbox-only `/ext` file and attaches it (it would only mint needless
file_ids; raw corpus files are rarely what a user wants). A `/ext` path that
is not under nucllei's `$EXT_DIR` is therefore a true not-found or a mount
mismatch, and `fetch_from_workspace` / `feed_image(path=)` say so in one
neutral sentence: `ERROR: <path> はこのサーバの /ext に無い (存在しないか、sandbox にだけ mount されている)。次の手: 中身が要るなら sandbox で読んで使う。`
(no "withheld" wording, no "copy it to `/workspace` and attach" route). Until
2026-10-03 there was a serving scope (`EXT_FILE_SCOPES`) and a
`mounted_path_withheld` refusal 「利用者に配信しない範囲にある」 — both gone.

Relative `/ext/…` was rejected (`relative_ext`) from #139 (2026-09-07) until
2026-10-03, on the grounds that a URL and a filesystem path sharing the leading
`/` invite mix-ups. That was reversed: document figures are now handed to the
model as relative `/ext/{rest}`, like `/api/v1/files/{id}/content` links
already are — a relative URL keeps rendering when the origin or the scheme
changes, and nothing has to compose an origin (from request headers, with a
fallback, or across a sub-agent on another host). The mix-up is prevented by
the argument name (`url` vs `path`) and by context (a markdown link/image vs
code). Absolute `https://<any host>/ext/{rest}` URLs keep resolving.

An optional trailing `#page=N` (1-origin) selects a PDF page on any form (only
the trailing `#page=N` is parsed, so it does not collide with a URL's own
fragment). Resolved references are inlined as base64 right before the model
call: images as-is, PDFs rasterized page by page (default page 1) — no File is
created. Anything outside those roots, oversize, missing, or neither image nor
PDF becomes `(image unavailable)` — as does any other `image_url` form
(protocol-relative `//host/…`, non-http(s) schemes, relative paths, any other
`/…`), including
an external URL passed in `image_url` on the headless `/api/chat/completions`
path. The mount contents are logged once at boot.

**Failure reasons.** `resolve_url` / `resolve_path` / `resolve_ref` all return
`(LoadedRef | None, RefError | None)` — `(loaded, None)` on success and
`(None, RefError)` on failure, never raising; `max_bytes` overrides the `FETCH_MAX_BYTES` size cap for that call
on every route (file, http stream, workspace) — the cap is the caller's concern
(inlining an image vs. reading a document), and `too_large.limit` carries the
value actually used. `load_ref(ref, user_id)` is a thin wrapper over
`resolve_ref` that drops the reason, keeps the env cap, and returns
`LoadedRef | None`. `RefError` is a frozen dataclass
`(code, detail, size=None, limit=None, status=None, size_exact=False, reason=None)`: `detail` is a one-line
human/log message **whose wording is not stable — never branch on it or
re-parse the value to guess what happened**; tools build the model-facing
wording from `code` and, where a code has a breakdown, `reason` (the inline
step above still emits the same `(image unavailable)` for every code and only
logs the reason). The `code` set is closed — do not add to it. `reason` is a
stable field for telling cases of one code apart without growing that set; its
values are a closed set too, carried by `unknown_upstream`, `upstream_status`
and `upstream_unreachable` (`None` on every other code and in every other
case):

| `code`                 | `reason`             | When                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `unknown_upstream`     | `server_self`        | a general URL (absolute, not `/ext/…`) whose host — an IP literal, or any of a name's resolved addresses — is **this server itself or a co-located service** (`is_self_address`: loopback, link-local / metadata, own interfaces, default gateway, `host.docker.internal`). The only destination a general URL is refused; an external site never gets this (unreachable hosts are `upstream_unreachable`) |
| `upstream_status`      | `redirect_self`      | a general URL answered `3xx` to a `Location` pointing at this server itself. `status` is the `3xx`. A `Location` that is not a resolvable URL (non-http(s), control characters) is `upstream_status` with the `3xx` and no reason                                                                                                                                                                          |
| `upstream_status`      | `too_many_redirects` | a general URL redirected more than 3 times                                                                                                                                                                                                                                                                                                                                                                 |
| `upstream_unreachable` | `unresolvable`       | a general URL whose host **name** does not resolve (`getaddrinfo`; the air-gapped production network has no DNS — use the IP literal)                                                                                                                                                                                                                                                                      |

| `code`                  | When                                                                                                                                                                                                                                                                 | Extra fields                              |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `unsupported_form`      | not a form the entry point accepts: for `resolve_url`, not an `http(s)://` URL nor same-server `/ext/…`; for `resolve_path`, not `/workspace/…` / `/ext/…`; for `resolve_ref`, neither (relative path, empty, `ftp:`, `//host/…`, `/etc/…`, `/mnt/…`)                |                                           |
| `traversal`             | `..`, backslash, NUL or other control chars anywhere in the reference                                                                                                                                                                                                |                                           |
| `unknown_upstream`      | a general URL whose host is this server itself (`reason=server_self`). Until 2026-10-03 it also covered unknown `/ext/{name}/` names (`unknown_name`) and malformed `/ext/` (`not_ext_form`); those reasons are gone — a `/ext/…` that is not mounted is `not_found` | `reason` (see above)                      |
| `not_found`             | `/ext/{rest}` (URL or sandbox path): missing, a directory (incl. `/ext/` itself), or resolves outside its mount under `$EXT_DIR` (symlink escape); general URL `404`; sandbox `/files/read` `404`                                                                    | `status=404` for http                     |
| `too_large`             | over the size cap (`max_bytes`, default `FETCH_MAX_BYTES`) on any route (file, http stream, workspace) or over the sandbox's own read limit (`413`)                                                                                                                  | `size`, `limit`, `size_exact` (see below) |
| `upstream_status`       | a general URL answered non-2xx other than 404 — including `3xx` redirects that could not be followed (see `reason`)                                                                                                                                                  | `status`, `reason`                        |
| `upstream_unreachable`  | general URL connection failure, timeout, `InvalidURL`, or a host name that does not resolve (`reason=unresolvable`)                                                                                                                                                  | `reason`                                  |
| `workspace_unavailable` | `/workspace/…` without `SANDBOX_URL`, or the sandbox API failed for a reason other than 404 / 413 (unreachable, 401 token mismatch, 5xx)                                                                                                                             | `status` when the sandbox answered        |

`too_large.size_exact` says whether `size` is the real size or only a lower
bound: `True` when it comes from `stat` (`$EXT_DIR`, sandbox `413`),
a fully read workspace file, or an http `Content-Length`; `False` when the http
stream had no `Content-Length` and was aborted just past `limit` — `size` is
then the bytes received so far (slightly above `limit`) and the real size is
unknown. On every other code it is meaningless and stays `False`. Tool hint:
when `False`, say "about N MB or more" rather than quoting `size` as the file's
size, and suggest retrying with a larger `max_bytes` (e.g. twice the cap).

| Var               | Kind | Default            | Effect                                                                                                                                                                                                |
| ----------------- | ---- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SELF_ADDRESSES`  | env  | empty              | comma-separated IP addresses (no CIDR) added to the self-deny set of general URL fetches — the host's outward-facing IPs a container cannot discover. Was `EXT_SELF_ADDRESSES` until 2026-10-03       |
| `FETCH_MAX_BYTES` | env  | `26214400` (25 MB) | default size cap per reference when the resolver reads a URL or a sandbox path (pre-send inlining of context images, and any tool that does not pass its own `max_bytes`). Was `EXT_INLINE_MAX_BYTES` |

There is no variable for `/ext`: it serves `$EXT_DIR`. `builtin_fetch_url` and
`builtin_feed_image` (§5.7) are always registered: since 2026-10-03 the `url=`
argument fetches any `http(s)://` URL (see "General URLs"), so an empty `$EXT_DIR`
does not make the tool always fail (until that day `builtin_fetch_url` was
gated on any of `EXT_UPSTREAMS` / `EXT_PUBLIC_BASES` / the removed
`EXT_INTRANET_CIDRS`).

### 3.9 Audio (STT / TTS)

Feature gates: `ENABLE_AUDIO_STT`, `ENABLE_AUDIO_TTS` (RC, both `true`; `false`
hides the UI affordance and 403s the endpoints).

STT: `AUDIO_STT_ENGINE` (RC, `""` = bundled faster-whisper, always on `cpu` since
2026-10-03 — no CUDA device probe; or `openai`, `deepgram`,
`azure`). Whisper knobs: `WHISPER_MODEL` (`base`; a name or a local model path),
`WHISPER_MODEL_DIR`, `WHISPER_LANGUAGE` (`""` = auto; set `ja` for Japanese-only
sites), `WHISPER_COMPUTE_TYPE` (`int8`), `WHISPER_CPU_THREADS` (`0`),
`WHISPER_NUM_WORKERS` (`1`), `WHISPER_VAD_FILTER` (`false`), `WHISPER_MODEL_AUTO_UPDATE`
(env, `false`; `true` re-checks the hub for a newer model at load — forced off by
`OFFLINE_MODE`). Engine-specific:
`AUDIO_STT_OPENAI_API_BASE_URL/_KEY`, `AUDIO_STT_MODEL`, `DEEPGRAM_API_KEY`,
`AUDIO_STT_AZURE_*`.

Microphone dictation tuning — env only (not RC: no DB value, no admin UI). A value
that is not a number or is out of range falls back to the default with one startup
warning. Tune per site (microphone, room noise, CPU speed); background in
`_memos/audio.md`.

| Var                                | Kind | Default | Range      | Effect                                                                                                                                                                                  |
| ---------------------------------- | ---- | ------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AUDIO_STT_SEGMENT_SECONDS`        | env  | `12`    | 4 – 30     | while the user keeps talking, cut a segment at the first pause after this many seconds (forced cut at this + 4 s). Shorter = text appears sooner but more words break at the cuts       |
| `AUDIO_STT_SEGMENT_PAUSE_MS`       | env  | `400`   | 100 – 2000 | silence length that counts as a pause to cut at. Shorter = fewer forced cuts, but more cuts inside a word                                                                               |
| `AUDIO_STT_SILENCE_THRESHOLD_DB`   | env  | `-45`   | -90 – -10  | level below which input counts as silence (`AnalyserNode.minDecibels`). Depends on the microphone level: lower it for quiet input, raise it when room noise is never treated as silence |
| `AUDIO_STT_RECORDING_BITRATE_KBPS` | env  | `24`    | 8 – 128    | bitrate of the full recording kept as an attachment (§4.4)                                                                                                                              |
| `WHISPER_BEAM_SIZE`                | env  | `5`     | 1 – 10     | beam size of the bundled faster-whisper (ignored by external STT engines). Smaller = faster, less accurate                                                                              |

TTS: `AUDIO_TTS_ENGINE` (RC, `""` = browser speech API; or `openai`, `azure`,
`elevenlabs`, `transformers`). `AUDIO_TTS_OPENAI_API_BASE_URL` accepts any
OpenAI-compatible `/v1` speech endpoint (the reference deployment points it at a
VOICEVOX/AivisSpeech bridge), plus `AUDIO_TTS_MODEL`, `AUDIO_TTS_VOICE`,
`AUDIO_TTS_SPLIT_ON`, `AUDIO_TTS_API_KEY`, `AUDIO_TTS_AZURE_*`.

### 3.10 Logging / telemetry

| Var                                                                | Default | Effect                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------ | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GLOBAL_LOG_LEVEL`                                                 | `INFO`  | root level; per-source overrides: `MAIN_LOG_LEVEL`, `MODELS_LOG_LEVEL`, `OPENAI_LOG_LEVEL`, `OLLAMA_LOG_LEVEL`, `AUDIO_LOG_LEVEL`, `CONFIG_LOG_LEVEL`, `DB_LOG_LEVEL`, `WEBHOOK_LOG_LEVEL`, `OAUTH_LOG_LEVEL`, `RAG_LOG_LEVEL` (historical name: it covers attachment-text injection into the prompt and the task-model helpers, not retrieval) |
| `ENABLE_OTEL` + `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` | `false` | OpenTelemetry traces (endpoint default `http://localhost:4317`, service name `nucllei`)                                                                                                                                                                                                                                                         |
| `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER`                  | unset   | read by the OpenTelemetry SDK itself (`Resource.create` / default sampler), not by nucllei                                                                                                                                                                                                                                                      |

Vars you may know from stock Open WebUI that **do not exist here** (removed with
their features): all `RAG_*` / embedding / web-search / image-generation config,
`CODE_EXECUTION_*` / `CODE_INTERPRETER_*`, `WEBSOCKET_*`, `AUDIT_LOG_*` (the audit-log
file mechanism is removed; logs go to stdout only), community-sharing and
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
restricted to a path allowlist — §3.3). The `token` cookie carries JWT sessions
only: an API key (`sk-...`) is accepted solely in the `Authorization` header, and an
`sk-` value in the cookie is rejected like any invalid token (`401`). Role tiers: `pending` (no access),
`user`, `admin`.

Pending users land on a dedicated `/pending` page. Its content is customized by
placing a markdown file at **`/app/custom/pending.md`** (§3.1) (read per request — edits
apply without a restart; served via `GET /api/v1/auths/pending_message`). When
the file is absent the page shows a built-in default. There is no env/config
knob for this message.

```sh
# sign in → JWT
JWT=$(curl -s http://HOST/api/v1/auths/signin -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"..."}' | jq -r .token)

# mint an API key (one per user; POST replaces)
KEY=$(curl -s -X POST http://HOST/api/v1/auths/api_key \
  -H "Authorization: Bearer $JWT" | jq -r .api_key)
```

Other auth endpoints: `GET /api/v1/auths/` (session info; it does **not** issue
or refresh a token — see below),
`GET /api/v1/auths/signout`, `POST /api/v1/auths/add` (admin: create a user),
`POST /api/v1/auths/ldap`, `GET /api/v1/auths/pending_message` (the
`custom/pending.md` content as `{"content": string | null}`).

**Session idle timeout.** `SESSION_IDLE_TIMEOUT` (§3.3) is an _idle_ timeout, not an
absolute deadline: the session is extended only on requests the browser marks as
human-driven (an `X-Nucllei-Activity: 1` header the web UI adds after a keypress,
click or tap), and only once the token is past half its lifetime. Background
polling therefore never keeps a session alive, so leaving a tab open at an unattended
shared terminal does not keep the user logged in. `GET /api/v1/auths/` does **not**
refresh the session (that would let a page reload extend it forever), and API-key
requests are never extended — they carry their own credential. `/api/config`
exposes the value as `session_idle_timeout` (seconds, `null` when unlimited) so the
UI can send the user back to the login screen once it elapses. When the UI's own idle
timer fires it first calls `GET /api/v1/auths/signout` and only then navigates to
`/auth`, so a client-side expiry always ends the server-side session too (#137) —
`/auth` can never bounce the next person back into the previous user's chats because
a still-valid cookie happened to survive. `signout` needs no valid session (an expired
or missing token still gets `200`) and clears the `token` cookie with the same
`httponly` / `samesite` / `secure` attributes it was issued with. Sessions are
stateless JWTs: "invalidating" one means removing the httpOnly cookie from the
browser; a copy of the token taken out of the browser stays valid until its `exp`
(= the idle deadline, re-stamped on activity). Default `-1` keeps sessions
unlimited, unchanged from before.

### 4.2 Models

`GET /api/models` — the canonical model list for clients: only _workspace models_
(presets), filtered by the caller's group ACL. The `id` field is what you pass as
`model` in completions. Model CRUD lives under `/api/v1/models/*` (create/update/
delete are admin-only). Every model row **must** declare a `base_model_id`
(pointing at a raw connection model) — raw connection models themselves are never
exposed as chat models; they only exist as base choices (admin inventory:
`GET /api/models/base`).

The raw connection model list is fetched from the engine's `/models` when
`GET /api/models` (or `/api/models/base`, `/openai/models`) is called, and
cached in-process. Completions do not re-fetch it: they look the base model up
in the cache and only refetch when the id is missing (a model just added
upstream) or after the connection settings were updated. Preset fields
(params, system prompt, ACL) are read from the database on every completion,
so preset edits apply immediately.

A model preset is where all capability wiring happens (`meta` fields):
`toolIds` (tools available to the model), `filterIds` / `actionIds` (functions),
`skillIds` + `features.skills` (skills), plus params and system prompt.

`params.custom_params` (editable in the model editor's Advanced tab, "Custom")
is a free-form key/value escape hatch merged **top-level** into the outbound
OpenAI-compatible request body — use it for engine extensions outside the
whitelisted params, e.g. SGLang's
`chat_template_kwargs: {"thinking": true, "preserve_thinking": false}`.
Values are parsed as JSON literals (invalid JSON falls back to a string);
same-name keys win over whitelisted params (last-write).

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
  `/command` expansion and attachment auto-load, file context injection too). Outlet filters do not run
  headless (call `POST /api/chat/completed` yourself if you need them).
- **Tools are attached but not executed**: the model's configured tools are put in
  the request, so you may receive raw `tool_calls` in the response — executing them
  is your client's job in this mode. The native execution loop only runs on the
  chat-wire path.
- `tool_ids` and `features` in your payload are silently discarded (tool
  availability is model configuration — §1).
- `files` entries are ACL-checked against the caller, and each file's extracted
  text is injected into the system message as a `<source id=".." file_id="..">`
  block (full text — there is no RAG retrieval step). Attributes, in this order:
  `id` (1-based index in this request), `file_id`, then for a voice-dictation
  recording (§4.4) `dictation="true"` — plus `part=".." parts=".."` only when the
  recording was split into two or more files — then `extraction="failed"` /
  `extraction="none"` when the file has no extracted text.

**Chat-wire mode** (`chat_id` + the **assistant** message `id` present) — the UI's
own wire, usable by API clients that want the conversation to exist in the UI.
Create the chat via `POST /api/v1/chats/new` first (you must own it) with **both**
the user message and an **empty assistant message** (`role: "assistant"`,
`content: ""`, `parentId` = the user message id), set `history.currentId` to the
assistant message id, and pass **that** id as `id`. The generated response is
written into that message, so it must already exist in `history.messages` with
`role: "assistant"` — otherwise the request is rejected with `400`. Minimal
example:

```jsonc
// 1. POST /api/v1/chats/new
{"chat": {"models": ["<model>"],
          "messages": [ {"id": "<uid>", "parentId": null, "childrenIds": ["<aid>"],
                         "role": "user", "content": "1+1?"},
                        {"id": "<aid>", "parentId": "<uid>", "childrenIds": [],
                         "role": "assistant", "content": ""} ],
          "history": {"currentId": "<aid>",
                      "messages": {"<uid>": {...}, "<aid>": {...}}}}}

// 2. POST /api/chat/completions
{"model": "<model>", "stream": true,
 "chat_id": "<chat id from step 1>", "id": "<aid>",   // ← assistant message id
 "messages": [{"role": "user", "content": "1+1?"}]}
```

The POST response is an SSE
subscription to a background task that always runs to completion (disconnecting
does not cancel). Events are envelopes
`data: {"chat_id","message_id","data":{"type","data"}}`; the first is
`{type:"task",data:{task_id}}`, then `chat:completion` events carrying either
`{delta:{block:"text"|"reasoning",content}}` appends, periodic full
`{content, content_blocks}` snapshots, or terminals
(`{done:true,...}` / `{error:...}` / `{usage}`). `done` is not EOF — read until the
server closes. Volatile `chat:usage` events (cumulative usage of the model call in
progress, ~0.5 s) may precede the terminal usage. Two more volatile event types
ride the same stream (never persisted):

- `tool:progress` — `{tool_call_id, description, done, current?, total?,
subagent?}` for a running tool call (§5.5 `__progress__`). The call emits one
  at start (`description: ""`) and a terminal `done: true` when it returns.
  `subagent: {session_id, task_id?}` is present only for `run_subagent` (§5.8):
  `session_id` (= the child agent-session chat id) arrives at dispatch,
  `task_id` (the child turn's task — `GET /api/tasks/{task_id}/events`) follows
  as soon as the child turn starts; once set it is repeated on every later
  payload of that call. For a background call (§5.9) these are forwarded to the
  spawning turn's stream while it is still open.
- `execution:done` — `{task_id, tool_call_id, tool_name, status, finished_at,
duration, error, error_kind}` when a background task (§5.9) reaches a terminal state
  (`done|failed|stopped`), sent to every open stream of the chat; the envelope's
  `message_id` is the spawning message.

The terminal `{usage}` is the merged, persisted `message.usage`:

| Field               | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `prompt_tokens`     | input of the **last** model call of the turn (the context the model actually saw)                                                                                                                                                                                                                                                                                                                                                                                                             |
| `completion_tokens` | output, **summed over the turn** (every call of the native tool loop)                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `total_tokens`      | `prompt_tokens + completion_tokens`                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `duration`          | generation wall time in seconds, summed over the turn (tool execution excluded)                                                                                                                                                                                                                                                                                                                                                                                                               |
| `context_size`      | the model's context window, when known                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `carried_tokens`    | estimated prompt of the **next** call = `prompt_tokens + (completion − reasoning)` of the last call — what this turn carries into the next (text and tool_calls are re-sent, reasoning is not). `reasoning` is the engine's `reasoning_tokens` (top-level or `completion_tokens_details`); when not reported it is **estimated** from the character ratio of the last call's output blocks. Absent when the usage has no `completion_tokens`. The UI's context gauge shows this when present. |

Companions: `GET /api/tasks/{task_id}/events` (re-attach),
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

| Endpoint                                                            | Notes                                                                                                                                                                                                                                                    |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/files/`                                               | multipart field `file` (and optional `dictation`, below); optional `?process=false` to skip extraction. Enforces the upload permission, extension whitelist and size cap. Returns `{id, filename, meta{name,content_type,size}, data{content?,status?}}` |
| `POST /api/v1/files/{id}/process`                                   | re-run the Attachment Function (`__event__="attach"`)                                                                                                                                                                                                    |
| `GET /api/v1/files/{id}` / `/{id}/content` / `/{id}/data/content`   | metadata / raw bytes / extracted text                                                                                                                                                                                                                    |
| `POST /api/v1/files/{id}/data/content/update`                       | overwrite extracted text                                                                                                                                                                                                                                 |
| `POST /api/v1/files/{id}/access_control/update`                     | `null` (public) or `{"read":{group_ids:[],user_ids:[]}}`                                                                                                                                                                                                 |
| `GET /api/v1/files/` `/list` `/search`, `DELETE /api/v1/files/{id}` | listing / deletion                                                                                                                                                                                                                                       |

To use a file in a completion: pass `{"type":"file","id":...}` in the request
`files` array (text injection), or reference `/api/v1/files/{id}/content` as an
`image_url` in message content — internal image URLs are inlined to base64 before
reaching the engine. Client-facing limits are advertised in `GET /api/config`
(`file.max_size`, `file.max_count`, `file.allowed_extensions`).

**Voice-dictation recordings (`meta.dictation`).** The chat UI transcribes
microphone input in 12–16 s segments, so a word can break at a cut. When a
dictation had two or more transcribed segments, the UI also uploads the
continuous recording as an ordinary audio attachment of that message
(`meta.content_type` is `audio/webm` — Opus, mono, ~22 kbps — from Chrome / Edge /
Firefox, `audio/mp4` from a browser whose MediaRecorder cannot produce WebM (Safari); never with a
`;codecs=` suffix) and passes the optional multipart field `dictation` (a JSON
string). It is the only client-writable `meta` key: the shape below is validated
(unknown keys rejected, ≤ 512 KB, audio files only) and stored as `meta.dictation`.
The model only sees the `<source … dictation="true">` mark (§4.3); a deployment
tool reads the segment list from `GET /api/v1/files/{id}` to re-transcribe a span
from the original.

```json
{
	"version": 1,
	"session_id": "812704ee-f883-4e7c-b97c-19f585663868",
	"part": 1,
	"parts": 1,
	"offset_sec": 0.0,
	"duration_sec": 41.8,
	"container": "webm/opus",
	"segments": [
		{ "i": 1, "start_sec": 0.3, "end_sec": 11.6, "cut": "breath", "text": "…" },
		{ "i": 2, "start_sec": 11.6, "end_sec": 27.6, "cut": "forced", "text": "…" }
	],
	"joined_text": "…"
}
```

- A recording longer than about 60 minutes is split into several files that share
  `session_id`; `part` / `parts` number them and `offset_sec` is where this file
  starts in the whole recording. A segment never spans two files. A file's tail
  overlaps the next file's head by about 0.3 s (so nothing is lost at the switch);
  segments do not overlap.
- `start_sec` / `end_sec` are seconds **from the start of this file**: when the
  segment recorder started and when it was stopped. Segments leave gaps where the
  UI was waiting for speech. `i` numbers segments across the whole recording.
- `cut`: `breath` (cut at a pause after the 12 s target), `forced` (16 s hard
  limit, possibly mid-word), `silence` (the user's silence timeout), `end` (closed
  by confirm / send).
- `text` is that segment's transcript (empty if it was too short to transcribe or
  transcription failed); `joined_text` joins the non-empty texts **of this file**
  with a space — what was appended to the input box.
- `container`: `webm/opus`, `ogg/opus` or `mp4/aac`.

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
| Skill    | `POST /api/v1/skills/create`, `.../id/{id}/update`               | skill editor+ | `{id, name, description?, content, tool_ids?, auto_load_content_types?, access_control?}` (markdown content; `tool_ids` admin-only)                                          |

`id` for tools/functions must be a valid Python identifier (lowercased on save)
and is immutable. Valves values: `GET/POST /api/v1/{tools,functions}/id/{id}/valves*`
(admin). Exports: `GET /api/v1/{tools,functions}/export` (admin).

Remember to **assign** what you deploy: tools/filters/actions/skills only take
effect on models whose preset lists them (`POST /api/v1/models/model/update`,
fields `meta.toolIds` / `meta.filterIds` / `meta.actionIds` / `meta.skillIds`).
The Attachment Function is instead activated globally via
`POST /api/v1/configs/attachment`, whose body is the whole attachment config
(`ATTACHMENT_MAX_SIZE_MB`, `ATTACHMENT_MAX_COUNT`, `ATTACHMENT_ALLOWED_EXTENSIONS`,
and `ATTACHMENT_FUNCTION_ID` — the function to activate; `""` disables it).

### 4.6 Miscellaneous endpoints

- `GET /health` → `{"status":true}`; `GET /health/db` (does a DB roundtrip). Unauthenticated.
- `GET /api/config` — instance metadata (name, version, auth features, locale;
  with a session also default models, audio config, file limits, permissions).
  `GET /api/version`.
- Audio: `POST /api/v1/audio/transcriptions` (STT; multipart `file`, plus optional
  `prompt` — context such as a term list, like the OpenAI field of the same name; only the
  last 200 chars are used; the built-in microphone input does not send it),
  `POST /api/v1/audio/speech` (TTS).
- Chats CRUD: `/api/v1/chats/*` (`/new`, `/{id}`, `/search`, share/archive/clone...).
- Executions of a chat (owner only, see §5.9):
  `GET /api/v1/chats/{id}/executions` →
  `{executions: [...], resume_suspended: bool}`. Each row carries
  `id, tool_name, status, created_at, finished_at, duration, error, delivered`,
  `session_id` (run_subagent's child session) and `error_kind` (§5.1
  "Failures"; `null` on success / stopped / orphaned) but never the result body. `delivered` is true once the result reached the
  model's context (server-driven delivery notice, `await_task`/`stop_task`
  report, or permanent abandonment on chat deletion); `resume_suspended` is
  true while server-driven delivery is held back by the unattended-chain limit
  (`ASYNC_EXEC_RESUME_CHAIN_LIMIT`) — the next human message releases it.
  `POST /api/v1/chats/{id}/executions/{exec_id}/stop` → stop one execution and
  return its updated row (idempotent — already-terminal rows are unchanged).
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
- **What the spec builder drops** (`nucllei/utils/tools.py`) — write docstrings
  with this in mind:
  - The description is cut at the **first `:param` or `:return`**. Everything
    after it — including the whole `:return:` text — never reaches the model.
    The cut is a plain text split (`re.split(":(param|return)", doc, 1)`), so
    do not write the literal `:param` / `:return` inside the description
    prose either; the description would end there.
  - A `:param name:` description is **one line only**. The parser matches
    `:param (\w+):\s*(.+)` line by line, so continuation lines are silently
    dropped (real incident: an enum-like list of choices on the following
    lines was invisible to the model).
  - Therefore: **rules, cautions, the return shape and "what to do next" go in
    the description (before the first `:param`); each `:param` fits on one
    line.** `:return:` is developer documentation at best — prefer a `#`
    comment.
  - The description is passed through `inspect.cleandoc` — the source
    indentation is removed (relative indentation and line breaks are kept),
    so indent docstrings normally. The stored spec (`/api/v1/tools/`, written
    at sync / create / update) and the per-turn spec use the same cut, so the
    stored `description` equals what the model gets (before per-turn hooks
    and the class-docstring note).
  - **Returned texts: a fact sentence + `次の手:`** (2026-10-04). Anything a
    tool returns can be copied into an answer — an internal instruction
    (「〜を案内」) once became a heading, and a bare relative `/ext/…` path was
    shown to the user. So every ERROR / status text the builtins return is in
    two parts: first the **fact** (what happened — safe to relay to the user),
    then, after the literal `次の手: `, the **model-only** part (what to do
    next). A deployment can therefore tell its model "relay the facts, never
    the 次の手 part". Rules: plain style (だ / である, no です / ます); a link the
    model may hand to the user is written as a markdown link `[ラベル](URL)`,
    never as a bare path (`ref_errors.SHOW_AS_LINK` / `show_as_link(url)`);
    a next step that names a tool from another toolkit (`run_subagent`,
    `run_python`, `run_shell`, `send_to_workspace`, `feed_image`, `fetch_url`,
    …) is conditional — 「〜が使えるなら …」 — because that tool may not be
    mounted; no internal names (codes, env names, storage terms) and nothing
    deployment-specific. Anything addressed only to the model — the argument
    form sentences (`*_ARG_FORM`, `REF_ARGS_RULE`, §3.8), display instructions
    (「本文に貼り直さない」, 「本文で示すなら …」), 「回答に写さない」 — goes after
    `次の手: `, never in the fact sentence. Raw exception text, HTTP `detail`,
    internal URLs and storage paths are logged, not returned (a tool exception
    reaches the model as its class name + the first 200 characters only). `nucllei/test/apps/webui/utils/test_ref_errors.py`
    audits `ref_errors` / `feed_image` / `fetch_url` / `sandbox` /
    `context_budget` / `attachment_text` for this. Deployment-layer tools should follow
    the same contract.
  - **Failures: raise `ToolError`** (#145, 2026-10-04). A failed call is not
    returned as a string or as `{ok: false, error}` — the tool raises
    `nucllei.utils.tool_errors.ToolError(fact, next=None, *, kind=...)` (or a
    subclass: `ToolCallError` / `ToolFailure` / `ToolOutcome` / `ToolFatal`;
    `ToolViolation` is the server's own). The model text
    is assembled in one place: `ERROR: {fact}。次の手: {next}` (`ERROR: {fact}。`
    without `next`; a trailing 「。」 on `fact` is normalised). `fact` follows the
    fact-sentence rules above (user-safe); `next` is the model-only part.
    `kind` says whose problem it is, and the chat UI shows it on the tool card.
    **Red is kept for bugs / broken servers and for the model breaking the call
    protocol**; ordinary errors are amber (ken 2026-10-04):

    | kind        | meaning                                                                                                                                                                                                            | UI                                    |
    | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------- |
    | `call`      | a tool found it was called wrong — empty / wrong-form value, bad `task_id` / `session_id` / `file_id`, a subagent session that is still running                                                                    | amber 「呼び直し」 (Retry needed)     |
    | `failure`   | runtime failure that is not a bug — timeout, sandbox / upstream unreachable, upstream 5xx / 401 / 403, concurrency / depth limits, multi-worker, background start refused, no user / chat context                  | amber 「失敗」 (Failed)               |
    | `outcome`   | the call worked but the answer is "none" — not found / 404 / 410, out of range, too large, a named attachment that is not there                                                                                    | amber 「該当なし」 (Not found)        |
    | `violation` | the model broke the call protocol (checked by the server before the tool runs) — tool not in this turn's list, `arguments` not parseable JSON, required argument missing, argument not in the signature            | red 「規約違反」 (Protocol violation) |
    | `fatal`     | the tool / server itself is broken, not the model's or user's doing — sandbox authentication (misconfigured key), unreadable sandbox response, file registration failed, a converter crash, any uncaught exception | red 「エラー」 (Error)                |

    Each kind has its own icon; the three amber kinds share one tone.

    The server builds failures it detects itself the same way: unknown tool and
    unparseable `arguments` → `violation`; a required argument missing from the
    call (checked against the callable's signature before invoking — reserved
    `__x__` parameters and parameters with defaults are not required) →
    `violation` 「ERROR: ツール {tool} の必須の引数 {name} が無い。次の手: 引数を付けて呼び直す。」;
    an argument that passed the spec filter but the callable does not accept →
    `violation`; timeout → `failure`; any other (uncaught) exception is a bug →
    `fatal` with the class name + the first 200 characters only (the full
    trace goes to the log). The model text is the same whatever the kind.
    A JSON-returning tool raises only when there is nothing else to return; a
    result that carries real payload next to a caveat (`run_subagent` stopped
    with a partial answer, a truncated read) stays a normal result.
    **Wire**: the tool result in `content_blocks` (`results[]`, persisted, so a
    reload shows it) carries
    `error_kind: "call" | "failure" | "outcome" | "violation" | "fatal"` next to
    `content` (the model text); absent on success. Background tasks store it in
    the execution row's `meta.error_kind` and expose it as `error_kind` in the
    executions list, the `execution:done` event and the `check_task` /
    `await_task` entries (§5.9).
    Clients decide by this flag, never by parsing `ERROR:`.
    **Custom tools** import the classes and raise them — the path works inside
    a DB-stored tool (the plugin loader runs it with nucllei importable):

    ```python
    from nucllei.utils.tool_errors import ToolCallError, ToolFailure, ToolOutcome

    class Tools:
        def read_doc(self, path: str) -> str:
            """..."""
            if not path.startswith("/doc/"):
                raise ToolCallError(f"{path} は文書のパスでない", "/doc/… のパスで呼び直す。")
            doc = _lookup(path)
            if doc is None:
                raise ToolOutcome(f"{path} は存在しない", "一覧から選び直す。")
            return doc
    ```

    (`ToolError(fact, next, kind=...)` is the base; guard the import like §5.4
    if the file must lint outside nucllei.) **A returned string is always a
    normal result** — even one that starts with `ERROR:`; nothing sniffs the
    text. Any other uncaught exception goes the generic `fatal` path.
    **Flagging a result without raising**: when a call failed but its output
    is still the model's working data (the run's stdout and traceback, files
    already attached), return the result as usual and call
    `nucllei.utils.tool_output.mark_error_kind(kind="failure")` during the
    call (kinds `call` / `failure` / `outcome` / `fatal`; `violation` is the
    server's own). The text, `render`, `display` and `context_images` stay
    exactly as for a success; only `results[i].error_kind` is added (background:
    the row stays `done` with the result, and `meta.error_kind` carries the
    kind). A raised `ToolError` wins over a mark. The builtins use it: `run_python`
    / `run_python_once` / `run_shell` flag `failure` when the code exits
    non-zero, a cell raises, or the run times out (stderr with exit code 0 —
    warnings — is not flagged; for `run_shell` this includes exits that are not
    really failures, e.g. `grep` without a match, accepted because amber is
    mild); `list_workspace` on a workspace from a sandbox image without the
    `exists` field of `/files/list` flags `outcome` on an empty listing (it
    cannot tell "missing" from "empty"; newer images raise `outcome` for a
    missing directory and `call` for a file); `send_to_workspace` over the
    attachment list flags `outcome` / `call` when nothing matched (with / without
    `filename`) and `failure` when nothing could be imported; `feed_image` on a
    file (`file_id`, the file URL, attached PDFs) flags the lines that fed
    nothing — `call` (neither image nor PDF), `fatal` (stored bytes unreadable),
    `outcome` (too large), `failure` (PDF does not open); with several attached
    PDFs only when none was fed. Custom tools can use it too:

    ```python
    from nucllei.utils.tool_output import mark_error_kind

    class Tools:
        def run_job(self, job: str) -> str:
            """..."""
            log, ok = _run(job)
            if not ok:
                mark_error_kind("failure")  # amber 「失敗」, log still returned
            return log
    ```

  - The description is re-sent on **every turn** for every mounted function:
    keep it to what the model needs to decide whether and how to call
    (purpose, argument forms, return shape, a one-line ERROR shape) and put
    longer usage guidance in deployment prompts / skills (example: §5.7.1).
- Methods may be `def` or `async def`; sync methods run in a worker thread, so
  blocking I/O is fine.
- **Shared notes**: the `Tools` **class docstring** is prepended **once** — to
  the first function's spec description only — instead of being copied into
  every function docstring. Use it for caveats that apply to the whole toolkit
  (execution-environment properties, serialization behavior, …). Docstrings in
  a tool file are model-facing text across the board (function docstrings
  already are); keep developer notes in `#` comments.
- Function names must be **globally unique across all tools assigned to a model**
  — on collision the first-loaded wins and the duplicate is dropped with a warning.
- **Per-turn hooks** (optional, 2026-10-03 — #140). Two method names on
  `Tools` are reserved: they are called by the tool loader on every turn and
  are **never exposed as functions** to the model. Both are plain (sync)
  methods — the loader runs in a worker thread, so DB reads are fine.
  - `available(self, request, user, model, metadata) -> bool` — return
    `False` to leave the whole toolkit out of this turn. Evaluated **after**
    the read ACL (§5.6), so it answers "does it make sense here", not "may
    this user use it". An exception counts as `False` (fail-closed, logged).
  - `spec_overrides(self, request, user, model, metadata) -> dict[str, dict]`
    — `{function_name: partial_spec}` patches deep-merged into this turn's
    spec (dicts merge recursively; strings, lists and numbers replace). Use it
    for per-user / per-preset parts such as an `enum` of allowed values. It is
    applied after the docstring-derived description (a `description` key
    replaces it) and before the class-docstring note is prepended. The stored
    (sync-time) spec is never modified, so it must be valid and generic on its
    own; validate arguments in the function anyway. An exception keeps the
    static spec (logged).
  - Arguments: `request` and `user` as for the turn; `model` is the **parent
    preset** of the turn (`metadata["model"]`, falling back to `__model__`,
    which may be the task model); `metadata` is `__metadata__` (`chat_id`,
    `message_id`, …; `{}` outside the chat path).

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

| Parameter                       | Contents                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__user__`                      | `{"id","email","name","role","info","valves"?}` — `info` is the opaque extra-metadata dict (§3.4) for external-system authorization                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `__event_emitter__`             | `async fn(event)` — push UI events mid-call (§5.5); `None` on the headless API path. **`status` is gone platform-wide** — use `__progress__`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `__event_call__`                | `async fn(event)` — round-trip to the user (`confirmation` / `input`), awaits their reply; `None` headless                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `__progress__`                  | `fn(description="", *, current=None, total=None, done=False)` — report progress while the tool runs; shown in this tool's own block (§5.5). Volatile                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `__feed_image__`                | `fn(ref)` — feed an image into the model's context (the "model's eyes") — injected after the tool results as a user-role `image_url` message for the follow-up call, kept as a reference in `results[].context_images`, and re-sent in the same position on every later turn (re-resolved each time; a reference that no longer resolves becomes `(image unavailable)`); `ref` is a file_id, a URL (same-server `/ext/{rest}`, absolute `https://…/ext/{rest}`, or any http(s) URL — the same-server `/ext/{rest}` is also the sandbox's path), or a sandbox path (`/workspace/…`); optional `#page=N` for PDFs — resolved by `utils/image_refs.py`, §3.8; chat path only. The model-facing entry point built on it is the builtin `feed_image` tool (§5.7), which validates the reference (kind / size / page count) before pushing it; a tool of your own calls the emitter directly |
| `__display__`                   | `fn(content, kind="markdown")` — render content to the user outside the collapsed tool block (not fed to the model); chat path only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `__chat_id__`, `__message_id__` | current chat / message ids                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `__id__`                        | this tool's id                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `__files__`                     | the chat's attachment refs (already ACL-filtered)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `__messages__`                  | the outbound message list                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `__metadata__`                  | request metadata (chat/message ids, files, skill activations, ...)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `__request__`                   | the FastAPI `Request` (→ `request.app.state.config` etc.). In a resumed turn (§5.9 server-driven delivery) it is an internal request with no incoming origin — no `Host` / `X-Forwarded-*` — so a tool cannot build absolute URLs from it there; keep URLs relative                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `__model__`                     | a model dict — note: for tools this is the _task_ model, not necessarily the chat model                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

### 5.4 Argument & result rendering

To get IDE-quality rendering of tool calls in the chat UI, annotate types —
there are no name-based heuristics:

```python
from nucllei.utils.tool_render import Code, Markdown, Text

async def run_sql(self, query: Code("sql")) -> Text:
    ...

async def read_doc(self, path: str) -> Markdown:
    ...
```

- `Code(language)` renders that string argument as highlighted, live-streaming
  code (`Annotated[str, RenderAs(kind="code", language=...)]` under the hood;
  reflected into the JSON-schema as `contentMediaType`, which is left visible to
  the model — harmless).
- `-> Text` renders the return value as pre-wrap text instead of a JSON tree.
- `-> Markdown` renders the return value as _rendered_ markdown (headings,
  tables, fenced code). Distinct contract from `Code("markdown")`, which shows
  highlighted markdown _source_. Use it for doc-reading tools that return md.
- Un-annotated arguments render by JSON type (scalars as `key: value`,
  structures as a tree). Streaming partial-argument rendering is automatic.
- External OpenAPI tools get the same treatment by declaring
  `"contentMediaType": "text/x-python"` (etc.) on string parameters.
- Tool sources usually live in your harness repo, where `nucllei` is not
  importable. Guard the import so the file stays lintable/testable there;
  annotations degrade to plain `str` and behavior is unchanged:

  ```python
  try:
      from nucllei.utils.tool_render import Code
  except Exception:

      def Code(language: str, base: type = str):
          return base

  PythonCode = Code("python")   # aliases keep signatures readable

  async def run_python(self, code: PythonCode) -> str: ...
  ```

### 5.5 Return values, events, limits

- Return a `str` (recommended; passed to the model verbatim) or a `dict`/`list`
  (JSON-serialized). Report a failure by raising `ToolError` (§5.1 "Failures")
  — the fact sentence and `次の手:` steer the model, and the kind is shown in
  the UI. Any other exception is safe too, but reaches the model only as
  「ツール X の実行中にエラーが起きた (<class>: <first 200 chars>)」 (kind `fatal`
  — an uncaught exception is treated as a bug).
- Generators are not supported.
- Per-call timeout `TOOL_EXEC_TIMEOUT_S` (default 300 s) and loop cap
  `TOOL_CALL_MAX_ITERATIONS` (default 30) — see §3.6.
- **Calls in one model response run concurrently** (since 2026-10-04; earlier
  they ran one after another). Results still go back to the model in call
  order, each call keeps its own timeout, and a call that raises or times out
  does not cancel its siblings. Stopping the turn cancels every running call.
  Two `run_subagent` calls in one response therefore run their child turns
  side by side without `background`. Two exceptions:
  - **`serial = True`** (class attribute on `Tools`) opts a toolkit out: its
    calls run one at a time, in call order (keyed by tool id), while calls to
    other tools still run alongside. Use it when the backend serializes anyway
    or order matters — `builtin_sandbox` declares it (a stateful `run_python`
    cell usually depends on the previous one, and the sandbox queues per
    chat/user, so concurrency would only reorder cells and eat into timeouts).
  - `load_skill` calls run first, so a later call in the same response can use
    the tools the skill loaded.
    A tool that mutates shared state through `__event_emitter__` `message` /
    `replace` events can now interleave with a sibling doing the same; keep such
    tools `serial` if that matters.
- **Elapsed time is shown for you.** A running tool's block already displays a live
  seconds counter, and the total is shown once it returns. Use `__progress__` only
  for information the server cannot infer — which item, which stage.
- **Progress**: call `__progress__("converting page 3")` while the tool runs. It
  renders in **this tool's own block header**, so concurrent tools and filters
  never overwrite each other. Pass `total=` (with `current=`) for a determinate
  ratio; omit it for an indeterminate spinner. Omit `description` to keep the
  previous wording and update only the numbers (like tqdm's `set_description`),
  so a loop can call `__progress__(current=i, total=n)`. Callable from sync and
  async tools alike; emissions are throttled server-side (~200 ms), and `done=True`
  always gets through. Progress is **volatile** — it is never written to the chat
  history. What remains after the call is the elapsed time, shown automatically.
  Completion is decided by the tool returning, not by `done=True`, so a tool that
  times out or raises still stops the spinner. Use `__display__` instead for
  anything the user should be able to read back later.
- `__event_emitter__({"type": ..., "data": ...})`: **every type passes through** (it is
  not an allowlist). The one exception is `status`, which **no longer exists** — the
  message-wide shared slot it wrote to was removed platform-wide, for tools and
  filters alike; emitting it is a no-op. Use `__progress__` instead.
  Types tools actually use: `chat:message:files`
  (attach files to the message), `message` / `replace` (append to / replace the
  message text), `source`/`citation`, `notification` (toast), `chat:title`.
- File outputs: write bytes via `nucllei.storage.provider.Storage.upload_file` +
  `nucllei.models.files.Files.insert_new_file`, then emit `chat:message:files`;
  images become visible when the model writes
  `![name](/api/v1/files/{id}/content)`. To let the _model_ see an image or a
  PDF page, call `__feed_image__(file_id_or_ref)` (§5.3) — it is injected into
  the follow-up call; an `/ext/` URL (relative or absolute), any http(s) URL
  or a `/workspace/…` reference needs no File
  (§3.8 for the reference rules). The model itself does this through the
  builtin `feed_image` tool (§5.7); a deployment does not need a tool of its
  own for it.

### 5.6 Access control & assignment

- Tool CRUD and Valves: **admin only**. Usage ACL per tool: `access_control =
null` (all users) / `{}` (owner only) / `{"read":{group_ids,user_ids}}`.
- A tool is live on a model iff it is listed in the model preset's `meta.toolIds`
  (and the calling user passes the tool's read ACL). There is no per-chat toggle
  and no global switch — "off everywhere" = assigned to no model.

### 5.7 Builtin tools, code execution, tool servers

- **Builtin tools** ship with the server (`builtin_` id prefix). Only their ACL
  is editable. Currently:
  - `builtin_history` — `list_history` / `read_history` / `grep_history` over
    the calling user's own chats. `grep_history` hits carry `matches` (up to 3
    per chat: `{role, excerpt}` with ±60 chars of context around the match,
    NFKC + casefold matching; empty when only the title matched — 2026-10-03).
  - `builtin_chat_files` — `list_chat_files` / `grep_chat_files`: attachments
    issued in the current chat, metadata only.
  - `builtin_attachment_text` — `read_attachment(file_id, offset?, max_chars?)`:
    the extracted text of an owned attachment (`Files.data.content` written by
    the deployment's Attachment function — §7; text-typed files fall back to a
    raw decode). Owner-only by direct file_id, deliberately **not** chat-scoped:
    mount it on an agent preset and a subagent (§5.8) can read materials the
    parent references by file_id without the content ever entering the parent's
    context.
  - `builtin_context_budget` — `get_context_budget()`: the current chat's
    measured context usage, for the model to check how much room it has left
    in a long piece of work (tool chains, long reasoning). Reads the usage
    nucllei records per model call (`chat.meta.usage`; the fields are the
    usage table in §4.3):
    `used_tokens` = `carried_tokens` of the latest call of this turn (prompt +
    the non-reasoning output that is re-sent, i.e. what the next call starts
    from; falls back to `prompt_tokens`, then `total_tokens`),
    `context_size` = the recorded one, else `get_model_context_size` (endpoint
    `max_model_len` → the model's `meta.context_size` → the tool valve
    `fallback_context_size` (admin, tool list gear; default 0 = unused — for
    connections that do not report a context length; the note then says the
    size is an admin default — 「管理者が設定した既定値 (モデルの接続先が返した値ではない)」)),
    `remaining_tokens`, `used_pct` (one decimal) and a `note`. With no usable
    context size the three derived fields are `null` and the note says why
    (「context 長が分からない (モデルの接続先が context 長を返さず、既定値も未設定) ので残量は出せない」 — no
    internal names such as `meta.context_size` in model-visible text).
    Before the first model call of a chat completes: an `outcome` ToolError
    (「このチャットの usage がまだ無い…」).
    Within a native tool loop the tool runs between model calls, so the value
    is as of the latest completed call — the pending tool results of the
    current step are not yet counted. Owner-only (the chat must belong to
    `__user__`); chat path only. Always registered. Moved from
    the deployment layer (`tools/context_budget.py`) on 2026-10-03: nucllei records
    usage per call, so the builtin sees the current turn where the deployment-layer
    version was one turn stale and needed a hand-written context-size valve.
  - `builtin_feed_image` — `feed_image(url?, path?, file_id?, filename?, pages?)`:
    shows an image or PDF page(s) to the model itself (a VLM) in the same turn,
    through `__feed_image__` (§5.3). Exactly one reference argument, interpreted
    by name (§3.8): `url` = an absolute URL or a same-server `/ext/{rest}` /
    `/api/v1/files/{id}/content`, `path` = a sandbox absolute path (`/ext/…`,
    `/workspace/…`; `/ext/…` is the same file whichever argument it comes in),
    `file_id` = an attachment or a generated file (owner
    check in the tool; the pre-send inline step checks read access again).
    `filename` is a substring filter on the target's name, `pages` a PDF page
    range (`"3"`, `"4-6"`, `"1-2,7"`). The tool validates what the reference
    resolves to — kind by content-type / magic bytes (not extension), size,
    page count — and then only pushes the reference; **no File or Storage
    object is created** (PDF pages are pushed as `…#page=N` references and
    rasterised at send time). Valves: `max_image_mb` (8), `max_pages` (3).
    Always registered. Usage guidance beyond the docstring: §5.7.1.
  - `builtin_fetch_url` — `fetch_url(url, query?, offset?, limit?, max_mb?)`:
    fetches one page / document by URL and returns it as plain text plus a link
    list, for browsing web that no document corpus holds. `url` is
    resolved by the shared resolver (`resolve_url`, §3.8) and nothing else —
    the tool has no allowlist and no on/off valve of its own; it accepts
    `/ext/{rest}` (relative or on any host — nucllei's own `$EXT_DIR/{rest}`),
    **any other scheme-qualified `http(s)://` URL** (fetched directly —
    intranet or internet, whatever the deployment's network reaches; the only
    refused destination is this server itself, §3.8 "General URLs"), and this
    server's
    `/api/v1/files/{id}/content` (owner check). HTML is flattened in the server
    with lxml (generic: layout tables are not turned into Markdown tables, the
    charset is the `Content-Type` one when present, otherwise utf-8 — no
    detection; markitdown is **not** used for HTML); PDFs go through
    markitdown's text layer, and a PDF whose text layer is below
    `pdf_text_char_threshold` characters is reported as scanned so the model
    turns to `feed_image`. The link list is resolved against the page's own
    URL (`route_of_url(url).page_url`, §3.8 — ordinary `urljoin`; a relative
    `/ext/…` page keeps its links relative and drops hrefs that leave `/ext/`).
    `query` keeps only the body and
    link lines containing it (substring; case- and full/half-width-insensitive);
    `offset` / `limit` (default 8000 chars) window the text, the result naming
    the next offset. Valves: `max_fetch_mb` (25; `max_mb` per call may raise it
    up to `max_fetch_mb_ceiling`, 200), `max_output_chars` (40000; also caps
    `limit`), `pdf_text_char_threshold` (100).
    **Always registered** (since 2026-10-03; before that it was gated on any
    of `EXT_UPSTREAMS` / `EXT_PUBLIC_BASES` / the removed `EXT_INTRANET_CIDRS`,
    because without them no URL could resolve). The docstring the model sees
    names no hosts (the "registered original hosts" sentence went away with
    `EXT_PUBLIC_BASES`). Usage guidance beyond the docstring (corpus vs. fetch,
    `query`, `run_subagent` + `max_mb`, link list): §5.7.1.
    Both tools word their failures per `RefError.code` / `reason` and the fixed
    argument-form sentences through `nucllei/utils/ref_errors.py`, shared with
    `send_to_workspace`. Until 2026-10-03 they were deployment-layer tools
    (`nucllei/tools/feed_image.py` / `fetch_url.py`); they moved
    here because every rule they enforce (resolution, registered upstreams,
    redirects, size caps, link roots) already lived in `utils/image_refs.py`,
    so the deployment-layer shells only duplicated those rules and their error texts,
    and had to carry "this nucllei version lacks X" branches. What stays in the
    deployment is the env values, the prompts / skills that teach the model
    when to call them, and site knowledge.
  - `builtin_subagent` — `run_subagent(preset_id?, instruction, session_id?)`:
    delegates one turn to a child agent session (§5.8). Always registered;
    whether it appears in a turn is the row ACL × the preset's `meta.toolIds`
    × the preset's delegation allowlist (`meta.subagentPresetIds`, §5.8).
  - `builtin_sandbox` — `run_python` / `run_python_once` / `run_shell` /
    `send_to_workspace` / `write_file` / `list_workspace` /
    `fetch_from_workspace`. **Registered only when `SANDBOX_URL` is set**; with
    no value the tool is absent from `/api/v1/tools/` entirely (and an existing
    row is pruned on the next boot, ACL included). It is gated because, unlike
    the always-registered builtins above, it needs an external service to be
    reachable — a builtin that is listed but always errors would be worse than
    one that is absent.
    `SANDBOX_TOKEN` is the **optional** shared bearer token of the sandbox API
    and is _not_ part of that gate: sandbox-side authentication is opt-in, so a
    URL-only deployment still works (unauthenticated). When set, it must carry
    the same value on both sides — the backend then sends
    `Authorization: Bearer` on every sandbox call and the sandbox rejects
    everything else with 401; a URL-without-token deployment logs a warning at
    boot. On a 401 the model only gets 「ERROR: sandbox との認証に失敗した
    (サーバの設定の問題)。次の手: 呼び直さない。…」 — the token-mismatch
    explanation (with the env name) goes to the log, as do the exception text
    and `detail` of every other sandbox failure (the model gets 「sandbox に
    接続できない」 / 「使えない path」 / 「大きすぎて取り出せない」 / 「HTTP <status>」). **Both are read from the environment only — never a valve or a DB
    row.** The sandbox ships with the server (same git tag) and compose brings
    it up alongside, so there is no legitimate reason to repoint it at runtime;
    keeping the destination out of the DB also means an admin account cannot
    aim it at an outside host and harvest the bearer token.
    The `path` argument of `write_file(path, content)` / `list_workspace(path?)`
    / `fetch_from_workspace(path)` is an **absolute path on the sandbox
    filesystem under `/workspace/`** — the same form `send_to_workspace`
    returns (`/workspace/imports/<name>`) and that code sees inside
    `run_python` / `run_shell` — and everything these tools return is absolute
    too, so one tool's result can be passed straight to another. **Relative
    paths are refused** (`path` has one form; `list_workspace()` without an
    argument lists `/workspace`). Anything that is not a sandbox path the tool
    handles — relative, empty, a URL, outside `/workspace` — gets the common
    fixed-sentence error (§3.8: `… は sandbox のパスの形でない。` +
    `次の手: ` + `WORKSPACE_PATH_ARG_FORM` / `PATH_ARG_FORM` + `REF_ARGS_RULE`); `/ext/…`
    (read-only reference data) is refused by `write_file` and `list_workspace`
    with a pointer to `run_shell` (a distinction inside the same `path`
    argument, based on what the path resolves to).
    `fetch_from_workspace(path)` turns a sandbox file into something the user
    can open, and **also takes `/ext/…`**: a `/workspace/…` file is read through
    the sandbox, registered as a File and attached to the chat (`file_id`
    returned). Where the attachment shows up decides the returned note
    (2026-10-04): image / audio / video are also pushed to the display
    channel, i.e. **already on the user's screen** under the tool call, and the
    result says 「ユーザーの画面には既に表示されている。次の手: 本文に貼り直さない」; any
    other file is only listed in the chat's file pane (`message.files` entries
    of type `file` are not drawn under the message), so the result carries a
    ready link 「次の手: 本文で示すなら [<name>](/api/v1/files/<id>/content) と書く」. The
    `exports/` auto-attach of `run_python` / `run_shell` reports each file the
    same way (for media it adds that a `[task]` notification of a background
    run must paste it, since nothing was displayed then). An `/ext/…` file that exists under nucllei's own `$EXT_DIR`
    (= the `/ext` gate serves it) is **not copied or attached** — the tool
    answers with its `/ext/{rest}` URL (`image_refs.url_for_mounted_path`; the
    same string as the path for ASCII names, percent-encoded otherwise) and
    returns it ready to paste (`![name](/ext/…)` for images, `[name](/ext/…)`
    otherwise). Any other `/ext/…` path is read by the server from its own
    `$EXT_DIR` (`resolve_path`: confined to the mount, capped at
    `FETCH_MAX_BYTES`) and attached like a workspace file — which in practice
    means a path the sandbox sees but nucllei does not is reported as
    unreadable (the deployment withholds by not mounting; §3.8). Failures are
    worded per `RefError.code`: unreadable (missing, a directory, or a link
    leaving the mount — one wording, fail closed) vs. over the cap (with actual
    size and limit). The unreadable wording (2026-10-04) is the neutral
    「このサーバの /ext に無い (存在しないか、sandbox にだけ mount されている)」
    sentence of §3.8 "Bridging" — `/ext` files are shown by URL only.
    `path` is only ever a sandbox path — a URL or a file URL in it is refused
    as "not a sandbox path" (fixed sentence). The returned
    `/ext/…` URL is percent-encoded, so it may be a different spelling of the
    URL a document shows for the same file. This is the reverse of
    `send_to_workspace(url=)` below (§3.8, bridging table).
    `send_to_workspace(filename?, file_id?, url?)` copies into
    `/workspace/imports/` and returns the absolute path. Its arguments are
    interpreted by name, as everywhere else (§3.8 reference rules): `file_id` =
    an owned attachment / generated file, `url` = a **URL** — absolute
    `http(s)://…` or same-server `/ext/{rest}` — resolved by the shared
    resolver (`resolve_url`), so exactly the URLs `feed_image` / `fetch_url`
    above can read:
    `/ext/{rest}` (relative or on any host — nucllei's own `$EXT_DIR/{rest}`,
    nothing fetched); and any other scheme-qualified `http(s)://` URL (§3.8 "General
    URLs"), fetched directly from its host and pinned to the resolved address,
    redirects followed up to 3 times — wherever the deployment's network
    reaches. No cookie is forwarded. **The only refused destination is this
    server itself and its co-located services** (`unknown_upstream` /
    `server_self`, computed at runtime). (History: until 2026-10-03 the tool
    fetched any LAN URL outside a CIDR deny-list — valve `url_blocked_cidrs`,
    removed; the move to the shared resolver then made it refuse everything
    outside the registered upstreams; the same day an `EXT_INTRANET_CIDRS`
    allowlist was added and withdrawn — ken decided the tool must not gate by
    destination.) Failures are reported
    per `RefError.code` and `reason` (missing / oversize with actual size and
    limit / redirect — to an unusable target, to the server itself, too many /
    upstream status / unreachable or unresolvable name / the server itself),
    each with a fixed sentence that never inspects the value — the shared
    defaults of `ref_errors` (§3.8) except `too_large` (the cap is a valve and
    cannot be raised by an argument).
    Valve `url_max_mb` (default 50) is the per-file cap passed as `max_bytes`.
    When the URL is `/ext/{rest}` and **`$EXT_DIR/{rest}` exists on
    nucllei's side** (`image_refs.mounted_path_for_url`), nothing is copied: the
    tool answers with the sandbox path `/ext/{rest}` (the same string as the
    URL), because the sandbox binds the same tree at its `/ext` (#138) — this
    relies on the deployment binding the same
    volume on both sides, which the server cannot verify; the size cap does not
    apply. A `url` that is this server's file delivery URL
    (`/api/v1/files/{id}/content`, relative or absolute) is routed to that file
    and imported with the same owner check as `file_id` (URL routing, not HTTP).
    **No value is reinterpreted as another argument**: a UUID or a sandbox path
    in `url` is "not resolvable as a URL", a URL in `file_id` is "no such
    record, or no access". Form errors are assembled from the shared fixed
    sentences (§3.8) and never vary with the value; `unknown_upstream` is
    worded by `RefError.reason`.
- **Code execution runs in an external sandbox service**, never in the server
  process. `builtin_sandbox` bridges over HTTP to an nsjail + IPython service
  and provides a stateful `run_python` (per user+chat kernel), `run_shell`, and
  workspace file exchange wired to chat attachments. That service ships from
  this repo as a second image, `ghcr.io/technoplasm/nucllei-sandbox` (source in
  `sandbox/`, built from the same git tag as the server image); the deployment
  decides whether to run it. There is no Pyodide and no in-server interpreter —
  don't target either.
- **External OpenAPI tool servers**: admin-configured connections whose
  `/openapi.json` operations are exposed as tools and invoked over HTTP (no
  reserved-param injection there).

#### 5.7.1 Builtin tool usage guide (`feed_image` / `fetch_url`)

The docstrings of `feed_image` and `fetch_url` carry only what the model needs
to decide whether and how to call them — purpose, the accepted form of each
argument, the return shape and the one-line ERROR shape. They are sent on every
turn, so the finer guidance was moved here on 2026-10-04 (it cost ~900 tokens
per turn). A deployment that wants the model to know it carries it in its
prompts / skill bodies; the text below is a **template** (テンプレート — skill
を書くときの雛形) that names no deployment: put the deployment's own corpus
tools, entry URLs and sites in place of the generic wording. The ERROR texts
the tools return at runtime still carry the per-failure next step, after
`次の手:` (§5.1). The template follows the same wording rules as the returned
texts (plain style, no 「案内」, links as `[ラベル](URL)`), because a prompt is
copied into answers just as easily.

**feed_image**

- 図・レイアウト・印影・手書きなど、文字起こしではなく実際に見て判断するためのもの。
  このターン内に自分の入力へ入るので、続けて内容を評価・説明・修正できる。
- ユーザーには何も表示されない。ユーザーに見せるなら、見た上で本文に markdown
  (`![図: …](URL)`) を書く。Files には登録しないので、ユーザーの添付にも残らない。
- `url`: tool の戻り値の literal をそのまま渡す (図・原本 PDF・Web の資料)。
  組み立てない・書き換えない・ホストを補わない。
  - `#page=N` 付きの literal はそのまま渡してよい (`pages` を付ければそちらが優先。
    `pages` が無ければ `#page=N` のページを見る)。
  - http(s) の URL はどのネットワークの URL でもそのまま渡す — どこに届くかは配備の
    ネットワークが決める (断るのはこのサーバ自身を指す URL だけ)。
- `path`: `/ext/…` は url でも path でも同じファイル。
- `/ext/…` のファイルはユーザーには URL で示す。生のファイル (コーパスの md 等) を添付して
  要らない file_id を増やさない。
- `file_id`: run_python の戻り値やシステム注記の file_id もここに渡す。本文リンク
  `/api/v1/files/{id}/content` しか手元に無ければ、それを `url=` にそのまま渡す。
- 3 つとも省略したときの対象は添付 PDF だけ。直接添付の画像は既に見えているので対象に
  しない (見直すなら file_id を明示する。docstring にも短く残している)。
- `pages` は PDF のときだけ意味を持つ (画像はそのまま、PDF は指定ページを画像化して目に入れる)。
- 中身が画像か PDF かは tool が中身を読んで判定する (拡張子の無い URL でも同じ)。
- 失敗の種類: 存在しない / サイズ上限超過 / redirect を辿れない / 上流のエラー / 届かない /
  このサーバ自身 / sandbox に繋がらない。ERROR 文は何が起きたか (事実) を書き、`次の手:` の後に
  次にすることを書く。ユーザーに伝えるのは事実だけ。「読めなかった」を全部リンク切れと
  言い換えない。

**fetch_url**

- **配備の文書コーパス tool に収録済みの資料はそちらで引く** — fetch_url で取りに
  行かない。fetch_url は**コーパスに無い情報を Web から辿る**ための道具。
- http(s) の URL はどのネットワークの URL でもそのまま渡す (IP アドレスの URL もそのまま
  渡せる。ホスト名を推測で IP に書き換えない)。**どのホストに届くかは配備のネットワークが決める** — この
  tool は行き先で断らず、届かなければ「届かない」と返す。断るのはこのサーバ自身を指す URL だけ。
- 入口 URL は配備の prompt / skill が名乗る。2 枚目からは組み立てず、tool の戻り値に
  出てきた URL の literal をそのままコピーする (相対ならホストを補わない)。
- 戻り値のリンク一覧の URL は 1 行に 1 本 — Web のページはそのページ基準の絶対 URL、
  同一サーバの `/ext/…` のページは同じ形の `/ext/…`。次の fetch_url(url=) / feed_image(url=) /
  send_to_workspace(url=) にそのまま渡せ、ユーザーにも `[ラベル](URL)` のリンクで示せる。
  どちらの用途でも 1 文字も変えずにコピーする。
- 大きいページを丸ごと読むと会話が埋まる。索引ページ (お知らせ一覧など) は必ず `query` を
  付けて該当行だけ取る (部分一致・大小文字と全角半角は無視。1,000 件級の一覧ページには必ず
  付ける)。実体ページや PDF は run_subagent に 1 ページ 1 本で投げる。
- 既定のサイズ上限を超える大きい資料は、サブエージェントの中で `max_mb` を付けて読む
  (この会話では読まない)。サイズ上限超過の ERROR が出たら run_subagent の子が `max_mb` を
  付けて読む (中身が要らなければ、ユーザーには URL を `[ラベル](URL)` のリンクで示す)。
- テキスト層の無い PDF (スキャン PDF) は ERROR ではなく、戻り値のヘッダが
  「種別: PDF / テキスト層が無い …」と言い、次の手に feed_image(url=…) を挙げる — feed_image で
  見る。画像も同様に「種別: 画像 …」で feed_image を挙げる。テキスト層のある PDF でも
  読んだのはテキストだけなので、レイアウト・図・表・印影は feed_image で見る。
- 平文にできない形式 (Excel / Word 等) は ERROR になる。加工が要るなら
  send_to_workspace(url=…) で sandbox に取り込み、中身が要らなければ URL をリンクで示す。
- 失敗の種類: 存在しない / サイズ上限超過 / redirect を辿れない / 上流のエラー / 届かない /
  このサーバ自身。ERROR 文は事実と `次の手:` を書く。「取れなかった」を全部リンク切れと
  言い換えない。

### 5.8 Subagents (`run_subagent`)

`run_subagent` is the builtin tool `builtin_subagent` (§5.7; since 2026-10-03
— #140, before that a pseudo-tool injected outside the tool loader). Two
layers decide whether it appears in a turn:

1. **Who may use it** — the ordinary tool path: `builtin_subagent` is in the
   parent preset's `meta.toolIds` and the user passes the tool row's read ACL
   (§5.6; admins bypass). This replaces the removed `meta.features.subagent`
   flag (migration `e7b3c90d2f14` added `builtin_subagent` to `toolIds` of
   every preset whose flag was not `false` and whose allowlist was non-empty,
   then deleted the flag).
2. **Where it may delegate** — the parent preset's `meta.subagentPresetIds`
   (agent presets) ∩ presets the user can access (owner or read ACL;
   `is_active=false` presets stay spawnable). The tool's `available` hook
   (§5.1) hides it when this set is empty, outside a chat (no `chat_id` /
   `message_id`), or when the nesting depth would exceed the limit; its
   `spec_overrides` hook puts the accessible preset ids into `preset_id`'s
   `enum` and lists them (`id: name — description`) at the end of the
   description. The same predicate is re-evaluated at call time.

The toolkit's class docstring (operating rules: when to delegate, how to run
children in parallel — several calls in one response, `background` only to keep
working meanwhile — and how to treat the answer) reaches the model as the §5.1
shared note, so deployment prompts need not repeat it.

- `run_subagent(preset_id, instruction, session_id?)` — spawns a child **agent
  session** (a first-class chat with `kind: "agent"`, linked to the parent
  chat/message) and runs one turn on the selected preset. Returns
  `{ok, session_id, answer, usage?, truncated?, note?, error?}` — the child's
  final assistant text only (8000 chars max), never the transcript. A truncated
  answer carries `note` (the cut length and "ask for the rest with the same
  `session_id`"). A child that errored or was stopped still returns this
  shape with `ok: false` and `error` (the partial answer and `session_id` are
  payload); a call that never started a child (empty instruction, bad
  `session_id` / `preset_id`, concurrency or depth limit, …) raises a §5.1
  `ToolError` instead. Pass `session_id` instead
  of `preset_id` to send a follow-up instruction to an existing session.
  While it runs, the call's `tool:progress` carries
  `subagent: {session_id, task_id?}` (§4.3 stream events) so a client can follow the
  child turn live before the result exists (the web UI renders the child's
  thinking/answer inside the running tool card).

`run_subagent` is **backgroundable** (§5.9): calling it with the shared
`background: true` argument runs the child turn as a background task and
returns `{ok, task_id, tool_name, status: "running"}` immediately. On
completion the result is delivered automatically by the server (a notice turn
in the parent chat — §5.9); to wait for it right now use
`await_task(task_ids)`. `session_id` stays the resumable session handle,
`task_id` is the record of one run. There is no dedicated `await_subagent` —
async collection is unified on the execution channel.
Parallel children do not need `background`: calls placed in **one** model
response run concurrently (§5.5) and the turn continues when all are done;
`background` is for continuing other work while children run (and a lone
`background: true` call in a response runs inline — §5.9).

Limits: nesting depth `SUBAGENT_MAX_DEPTH` (default 2), process-wide
concurrency `SUBAGENT_MAX_CONCURRENCY` (default 4 — spawns over the limit fail
immediately with an error that says the server-wide limit (N) was reached and
that this call was not started — the counter is process-wide, not per caller,
so the next step is "wait for your own children, else retry later or do it
yourself"; background included), per-call timeout `SUBAGENT_TURN_TIMEOUT_S`
(default 1800 s; also the execution timeout when backgrounded). Agent sessions
never appear in the user's chat list, are read-only over HTTP, and are
cascade-deleted (running turns stopped) with the parent chat.

### 5.9 Background tasks (`background` / `check_task` / `await_task` / `stop_task`)

Any **backgroundable** tool call can be promoted to a background task — an
asynchronous **execution**: a first-class DB row that outlives the parent
turn. On a normal user chat (never on agent-session child turns or headless
completions), the server injects a shared `background: boolean` parameter into
the model-facing spec of every eligible tool.

- `background: true` → the call returns
  `{ok, task_id, tool_name, status: "running"}` immediately and that running
  form stays in the parent message's tool result forever (append-only); the
  truth about completion lives in the execution row
  (`running|done|failed|stopped|orphaned`). The execution keeps running after
  the parent turn ends; stopping the parent turn does **not** stop it.
- **A lone background call runs synchronously** (since 2026-10-04): if the
  model response contains exactly one tool call and it asks for
  `background: true`, the call runs inline as if `background` were false — the
  result is the normal synchronous result (no `task_id`, no execution row),
  because the extra `await_task` round trip would cost more than it saves.
  The inline run uses the background timeout (per-entry `timeout`, else
  `ASYNC_EXEC_TIMEOUT_S`), but like any synchronous call it stops when the
  parent turn is stopped. With two or more calls in the response, `background:
true` is honoured as before (plain synchronous calls already run
  concurrently — §5.5 — so background is only needed to let the turn move on).
- `check_task(task_ids?, include_done?)` — non-blocking status poll. With no
  arguments lists the chat's running tasks (oldest first); `include_done:
true` lists terminal ones too (newest first, max 50); with `task_ids` returns
  those entries in order. Each entry:
  `{task_id, tool_name, status, started_at, elapsed_s}` plus `progress` while
  running or `duration`/`error`/`error_kind` (§5.1, when the row has one) when
  terminal. Never returns the result body
  and never writes (read-only).
- `await_task(task_ids, timeout?)` — collect results now. One bounded window
  (default 90 s, clamped to 10–300) covers all ids (max 20 per call);
  already-terminal tasks return immediately (idempotent — results stay
  re-collectable while the row is retained: indefinitely by default, see
  `ASYNC_EXEC_RETENTION_DAYS`; the tool description states the configured
  period). Per entry: done →
  `{ok, task_id, tool_name, status, result, duration?, usage?, truncated?}`
  (with the original tool's render descriptor when exactly one done task was
  requested, and a one-time replay of `context_images`/`display`
  side-channels; `error_kind: "outcome"` when the answer was "none");
  `failed/stopped/orphaned` → `{ok: false, ..., error, error_kind?}`
  (`error_kind` from the row's `meta.error_kind` — absent for stopped /
  orphaned);
  still running at the deadline → the running form (partial collection — the
  server delivers the rest later).
- `stop_task(task_ids)` — force-stop; returns each task's actual state
  afterwards. Idempotent on already-terminal tasks. Side effects may already
  have completed.

All three collectors are builtin pseudo-tools, mounted automatically whenever
an eligible tool is on the turn or the chat has execution rows (independent of
the single-process spawn gate, so past rows stay collectible even under
`UVICORN_WORKERS > 1`).

**Server-driven delivery** (no browser required): when a background task
reaches a terminal state, the **server** waits for the parent chat to be quiet
(no running turn), appends a delivery notice — a user-role message with
`meta: {"system_notice": "task"}` carrying the full result text (and
`context_images` as message images) — marks the row `delivered` in the same
transaction, and starts a continuation turn on the chat's model so the model
reads the notice and continues on its own. The notice is plain text, not
markdown: a `[task]` header line (fact: an automatic system notice, not the
user; results are below; produced images/files are not on screen yet — then,
after `次の手:`, the model-only part: do not re-collect with `await_task`, link
produced files to show them, do not copy the notice's format (the `task_id:` /
`status:` / `note:` lines) into the answer), then one section per task starting
with a plain `task_id: {id} ({tool})` line followed by `status:` and `result:`
(fenced) / `error:` / `note:` lines. It has no headings, and its instructions
sit only in the `次の手:` part, because the model may echo user-role text into
its answer (§5.1). `await_task`/`stop_task` reports
also mark rows `delivered` (the result already reached the model's context).
Unattended chains are limited to `ASYNC_EXEC_RESUME_CHAIN_LIMIT` (default 5)
consecutive notice turns; the next human message resets the chain. If the
continuation turn cannot start (engine down), the notice stays in history and
the turn is retried with backoff; if the chat's model is unresolvable, the
notice still gets read on the next user turn. Deleting the chat stops its
executions and marks them delivered (permanent abandonment). On boot the
server scans for undelivered rows and resumes delivery. Known v1 limit:
workspace-export chat attachments emitted after the parent turn ended are not
re-attached by the delivery notice (files themselves are persisted and listed
in the result string).

Failures in a background run follow §5.1 "Failures": a `ToolError` of any kind
but `outcome` ends the row `failed` with the model text in `error`; an
`outcome` ends it `done` with the text as `result` (the call worked — the
answer is "none"); either way `meta.error_kind` records the kind. A timeout is
`failed` 「ERROR: ツール X の background 実行が N 秒の上限でタイムアウトした。…」
(`failure`) and any other exception is `failed` / `fatal` with the same
class-name + 200-character text as the synchronous path (the raw exception
string is logged, not stored). A result flagged with `mark_error_kind` ends
`done` with the result and `meta.error_kind` set.

Over HTTP the rows are readable and stoppable by the chat's owner:
`GET /api/v1/chats/{id}/executions` (object form with `delivered` /
`resume_suspended`, no result bodies — §4.6) and
`POST /api/v1/chats/{id}/executions/{exec_id}/stop` (the UI's per-execution stop
button). Terminal states are also pushed as `execution:done` on any open
completion stream of the chat (§4.3 stream events); with no stream open the frontend
falls back to polling.

**Eligibility is opt-in** (`backgroundable` key on the tool entry):

- builtin: `run_subagent` (`builtin_subagent`), plus `builtin_sandbox`'s three execution entry points
  (`run_python`, `run_python_once`, `run_shell`) so long-running code can be
  backgrounded.
- DB tools: declare a `backgroundable` class attribute on the `Tools` class —
  `True` (all functions) or a list/tuple/set of function names (tool CRUD is
  admin-only, so this is an admin decision). Builtins declare it the same way;
  the entry is built from the loaded module either way.
  Note that the sandbox serializes calls per user, so a
  synchronous call made while a backgrounded one is still running waits for it
  (head-of-line blocking — backgrounding frees the parent turn, not the
  sandbox). For the same reason `builtin_sandbox` is `serial` (§5.5): its
  synchronous calls in one response run in call order, not concurrently.
- Never eligible: `load_skill`, `check_task`, `await_task`, `stop_task`, any
  tool that declares its own `background` parameter (collision guard), and
  tool-server tools (no opt-in surface in v1). `background: true` on a
  non-eligible tool is ignored (synchronous run, warning logged).

Timeouts and limits: per-entry `timeout` wins, else `ASYNC_EXEC_TIMEOUT_S`
(default 1800 s). DB tools declare per-entry timeouts with a `timeouts` class
attribute on the `Tools` class — a `{function_name: seconds}` dict, symmetric
with `backgroundable`; the same value also overrides the synchronous
`TOOL_EXEC_TIMEOUT_S` (default 300 s) for that entry, which is how a
long-running tool (e.g. speech transcription) survives a synchronous call.
A non-positive value (`0` / `-1`) means no system-side limit for the
synchronous call (since 2026-10-03; earlier it was ignored).
Concurrency: `ASYNC_EXEC_MAX_CONCURRENCY` (default 4 per
user) and `ASYNC_EXEC_MAX_TOTAL` (default 16 per process); exceeding either
fails immediately. Tool results — synchronous and background alike — are
truncated once at the production point at `TOOL_RESULT_MAX_CHARS` (default
65536; truncation demotes structured rendering to text and appends a marker
line `(ここで切れている: 全 N 字のうち先頭 MAX 字。次の手: 続きが要るなら offset / query などで範囲を絞って呼び直す)` so the model never
mistakes a cut result for the whole; the collectors' envelopes are exempt
because their contents are already truncated at save time). Rows are optionally purged after `ASYNC_EXEC_RETENTION_DAYS` (default 0 =
keep forever — results stay re-collectable indefinitely). Spawning is
single-process only: with `UVICORN_WORKERS > 1` it is refused (collection and
listing still work). Executions are stopped (not deleted) when their chat is
deleted; a server restart marks in-flight rows `orphaned` — the record
survives, the computation does not resume. Stopped and orphaned rows carry one
fixed `error` text each (the same wording in `check_task` / `await_task` and in
the delivery notice): stopped = side effects may already have completed
(次の手: do not re-run what the user stopped); orphaned = the result was lost,
re-run read-only work with the same arguments and, for anything that writes or
sends, check whether a result remains before deciding to re-run.

---

## 6. Writing Skills

A **Skill** is a markdown instruction package with optional lazy-loaded tools.
It activates through a single server-side path, triggered by the user typing
`/<id>`, by the model calling the `load_skill` pseudo-tool, or automatically by
an attachment whose content type the skill declares — all three leave the same
visible `load_skill` tool exchange in the conversation.

### 6.1 Definition

```json
POST /api/v1/skills/create
{
  "id": "vault",
  "name": "金庫番",
  "description": "Retrieve secrets from the vault. Load when the user asks about stored passwords.",
  "content": "You now have vault access tools. Use secret_password to retrieve the stored secret.\nToday is {{CURRENT_DATE}}; the user is {{USER_NAME}}.",
  "tool_ids": ["vault-tools"],
  "auto_load_content_types": [],
  "access_control": null
}
```

- `id` — the `/command` trigger **and** the identifier the model sees
  (`[a-zA-Z0-9_-]+`, immutable; rename = delete + recreate).
- `name` — display only; never shown to the model.
- `description` — the model-facing manifest line. **Empty description ⇒ the skill
  is invisible to the model** (only `/command` works). Write it as a "when to load
  this" hint.
- `content` — markdown instructions. Template variables expanded at every
  request: `{{CURRENT_DATE}}`, `{{CURRENT_TIME}}`, `{{CURRENT_DATETIME}}`,
  `{{CURRENT_WEEKDAY}}`, `{{USER_NAME}}`, `{{BASE_MODEL_ID}}` (id of the base model
  that actually runs inference — the selected preset's `base_model_id`; `Unknown`
  if the server cannot resolve it). The same variables are expanded in a preset's
  system prompt (`params.system`). Inside a subagent's turn `{{BASE_MODEL_ID}}`
  refers to the child preset's base. There is no call-time parameter mechanism.
- `tool_ids` — workspace tool ids to load on activation (**admin-only** to set,
  since it grants tool access through skill visibility).
- `auto_load_content_types` — content-type **prefixes** (e.g. `["audio/"]`,
  `["image/png", "application/pdf"]`). When the conversation has an attachment
  whose stored content type starts with one of them, the skill is loaded
  automatically (§6.3). Empty (the default) disables it. Normalized on save
  (trimmed, lowercased, empty entries and duplicates dropped). **An update
  replaces the list** — omit the field and it is reset to `[]`.
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

- **Attachment path** (auto-load): for every skill of the preset that declares
  `auto_load_content_types`, the server checks the request's attachments — the
  top-level `files` list (after the caller's read-ACL filter) and `image_url`
  parts of user messages (internal `/api/v1/files/{id}/content` URLs and
  `data:` URLs). The content type is always the one stored with the file, never
  a client-supplied value or the file extension. On a match the server injects
  the same synthetic `load_skill` exchange as the user path (call id
  `skill:<id>:auto`) and loads the skill's tools. Same AND condition as above;
  if it fails, nothing happens.
  - Placement: an image match goes right after the first user message carrying
    a matching image. A match from `files` goes right after the **first user
    message** — `files` is a per-chat list and does not say which message a
    file was attached to.
  - Persistence: stateless. Attachments are part of every request, so the
    activation is re-derived each time and the tools stay available on later
    turns that add no attachment. A headless client keeps it active simply by
    sending the same `files` again.
  - No double activation: if the history already contains a `load_skill` call
    for that skill (from `/command` or from the model), nothing is added.
    Several matching attachments still activate a skill once; several matching
    skills all activate, in `meta.skillIds` order.
  - In the UI the `load_skill` card appears on the response to the message the
    attachment was added with.

All paths return the same tool result:

```json
{ "result": "<expanded skill content>", "tools": ["secret_password"] }
```

`result` enters the model history as a `role:"tool"` message (not a system
message); the UI renders it as a collapsible "load_skill" card with the loaded
tool names. All entry points persist for the rest of the conversation: the
`/command` and attachment paths re-derive their activation (and tool ids) from
the request on every turn, and the model path re-derives its tool ids by
replaying the `load_skill` calls found in the history (`replay_skill_tool_ids`). The model does not need to call `load_skill`
again on a later turn to keep the tools.

Authorization is never inherited from the history. Every re-derivation re-checks
the same AND as the `/command` path (feature flag, the model's `meta.skillIds`,
and read visibility), so removing a skill from the preset drops its tools even
though the earlier `load_skill` call is still sitting in the conversation.

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

Functions **cannot** emit `status` either — the message-wide slot above the message
body was removed (it is a no-op now). Token-meter style information is native: the
action row under a message shows live context usage. `__progress__` is tool-scoped
and is not available here.

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
- A `file_handler = True` attribute on the `Filter` class claims file handling:
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
