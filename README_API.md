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
  faster-whisper STT. Baked env: `ENV=prod`, `PORT=8080`, `DATA_DIR=/app/data`.
- **Port**: `EXPOSE 8080`. `PORT` is consumed by the container CMD / HEALTHCHECK
  shell only; the app itself takes `serve --port`.
- **Volume contract**: mount `/app/data` (= `DATA_DIR`). It holds the SQLite DB
  (`webui.db`), uploads, model caches (whisper / HF / tiktoken), and
  the auto-generated secret key at `/app/data/.webui_secret_key`. No volume ⇒ all
  state is lost on container replacement.
- **Secret key**: if `WEBUI_SECRET_KEY` is unset, `serve` loads
  `$DATA_DIR/.webui_secret_key`, generating and persisting it on first boot. Set the
  env explicitly if you rotate keys or run multiple replicas.
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
`RESET_CONFIG_ON_START=true` wipes the stored runtime config at boot (env-only mode).

**Naming families**: `APP_*` refers to the Nucllei product itself (`APP_VERSION`);
`WEBUI_*` is the deployment layer on top — branding and site behavior
(`WEBUI_NAME`, `WEBUI_VERSION`, `WEBUI_COPYRIGHT` show _your harness's_ identity in
the UI, next to the Nucllei product version).

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

| Var                                                                                                                         | Kind | Default                                  | Effect                                                                              |
| --------------------------------------------------------------------------------------------------------------------------- | ---- | ---------------------------------------- | ----------------------------------------------------------------------------------- |
| `ENV`                                                                                                                       | env  | `dev` (`prod` in Docker)                 | dev exposes Swagger UI at `/docs`                                                   |
| `DATA_DIR`                                                                                                                  | env  | `./data` (`/app/data`)                   | root of all persistent state                                                        |
| `DATABASE_URL`                                                                                                              | env  | `sqlite:///$DATA_DIR/webui.db`           | SQLAlchemy URL; Postgres supported (`postgres://` auto-rewritten)                   |
| `DATABASE_SCHEMA` / `DATABASE_POOL_SIZE` / `DATABASE_POOL_MAX_OVERFLOW` / `DATABASE_POOL_TIMEOUT` / `DATABASE_POOL_RECYCLE` | env  | — / `0` (NullPool) / `0` / `30` / `3600` | Postgres tuning                                                                     |
| `UVICORN_WORKERS`                                                                                                           | env  | `1`                                      | worker count for `serve` — keep at 1 (multi-worker unsupported)                     |
| `WEBUI_URL`                                                                                                                 | RC   | `http://localhost:3000`                  | public base URL (webhooks, links)                                                   |
| `WEBUI_NAME`                                                                                                                | env  | `Nucllei`                                | brand name shown in the UI                                                          |
| `WEBUI_VERSION` / `WEBUI_COPYRIGHT`                                                                                         | env  | `""`                                     | deployment-layer version / copyright on the License tab (empty = hidden)            |
| `ENABLE_FORWARD_USER_INFO_HEADERS`                                                                                          | env  | `false`                                  | send `X-Nucllei-User-{Name,Id,Email,Role}` to the upstream engine                   |
| `CORS_ALLOW_ORIGIN`                                                                                                         | env  | `*`                                      | `;`-separated origins                                                               |
| `ENABLE_PWA`                                                                                                                | env  | `false`                                  | serve an installable `/manifest.json` (name follows `WEBUI_NAME`); off returns `{}` |
| `OFFLINE_MODE`                                                                                                              | env  | `false`                                  | forces HF hub offline + disables whisper auto-update                                |
| `USE_CUDA_DOCKER`                                                                                                           | env  | `false`                                  | run local ML (whisper) on CUDA                                                      |
| `RESET_CONFIG_ON_START`                                                                                                     | env  | `false`                                  | wipe runtime config at startup                                                      |

### 3.3 Auth & security

| Var                                                                                                                                                                                     | Kind | Default                 | Effect                                                                                                                                                                                                                                        |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WEBUI_AUTH`                                                                                                                                                                            | env  | `true`                  | `false` = single-user, no-auth mode                                                                                                                                                                                                           |
| `WEBUI_SECRET_KEY`                                                                                                                                                                      | env  | auto-generated (see §2) | JWT signing key                                                                                                                                                                                                                               |
| `JWT_EXPIRES_IN`                                                                                                                                                                        | RC   | `-1` (none)             | **idle** timeout: log out after this long _without user input_ — the clock restarts on every keypress/click, so an in-use session never expires (see §4.1). `-1` / `0` = none; otherwise `<n>` + `s`,`m`,`h`,`d`,`w` (e.g. `30m`, `4h`, `1d`) |
| `ENABLE_SIGNUP`                                                                                                                                                                         | RC   | `true`                  | public signup (auto-disabled after the first user becomes admin)                                                                                                                                                                              |
| `ENABLE_LOGIN_FORM`                                                                                                                                                                     | RC   | `true`                  | email/password form (off for SSO-only)                                                                                                                                                                                                        |
| `DEFAULT_USER_ROLE`                                                                                                                                                                     | RC   | `pending`               | role given to new signups                                                                                                                                                                                                                     |
| `ENABLE_LOGIN_AUTOCOMPLETE`                                                                                                                                                             | RC   | `true`                  | `false` = shared-PC posture for the login form (see `_memos/auth-profile.md`)                                                                                                                                                                 |
| `ENABLE_API_KEY`                                                                                                                                                                        | RC   | `true`                  | allow user API keys (`sk-...`)                                                                                                                                                                                                                |
| `ENABLE_API_KEY_ENDPOINT_RESTRICTIONS`                                                                                                                                                  | RC   | `false`                 | restrict API keys to a path allowlist                                                                                                                                                                                                         |
| `API_KEY_ALLOWED_ENDPOINTS`                                                                                                                                                             | RC   | `""`                    | comma-separated path prefixes; **empty list + restrictions on = deny all API-key requests**                                                                                                                                                   |
| `WEBUI_SESSION_COOKIE_SAME_SITE` / `WEBUI_SESSION_COOKIE_SECURE`                                                                                                                        | env  | `lax` / `false`         | session cookie flags                                                                                                                                                                                                                          |
| `WEBUI_AUTH_COOKIE_SAME_SITE` / `WEBUI_AUTH_COOKIE_SECURE`                                                                                                                              | env  | = session values        | auth (`token`) cookie flags                                                                                                                                                                                                                   |
| `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` / `WEBUI_AUTH_TRUSTED_NAME_HEADER`                                                                                                                    | env  | unset                   | header-based SSO behind a trusted proxy                                                                                                                                                                                                       |
| `BYPASS_MODEL_ACCESS_CONTROL`                                                                                                                                                           | env  | `false`                 | skip per-model group ACL                                                                                                                                                                                                                      |
| `ENABLE_ADMIN_EXPORT` / `ENABLE_ADMIN_CHAT_ACCESS`                                                                                                                                      | env  | `true` / `true`         | admin DB export / admin access to user chats                                                                                                                                                                                                  |
| `CACHE_CONTROL`, `HSTS`, `PERMISSIONS_POLICY`, `REFERRER_POLICY`, `XCONTENT_TYPE`, `XDOWNLOAD_OPTIONS`, `XFRAME_OPTIONS`, `XPERMITTED_CROSS_DOMAIN_POLICIES`, `CONTENT_SECURITY_POLICY` | env  | unset                   | each sets the corresponding response security header when present                                                                                                                                                                             |

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
| `USER_PERMISSIONS_CHAT_MULTIPLE_MODELS`             | `true`              | multi-model chat                                                                                                                                                                                            |
| `ADDITIONAL_USER_META`                              | `[]` (RC)           | JSON array `[{"key":"carte_user_id","label":"..."}]` declaring extra string user fields, editable by admins and delivered to tools as `__user__["info"]` — opaque markers for external-system authorization |
| `DEFAULT_LOCALE`                                    | `""` (RC)           | UI locale (shipped: `ja`, `en`, `zh-CN`)                                                                                                                                                                    |
| `DEFAULT_MODELS`                                    | unset (RC)          | default model(s) for new chats                                                                                                                                                                              |
| `WEBUI_BANNERS`                                     | `[]` (RC)           | JSON banner list `{id,type,title?,content,dismissible,timestamp}`                                                                                                                                           |
| `WEBHOOK_URL` / `ENABLE_USER_WEBHOOKS`              | `""` / `false` (RC) | server webhook (signup events) / per-user webhooks                                                                                                                                                          |

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
| `SANDBOX_URL` / `SANDBOX_TOKEN`                                | env  | `""` / `""`           | sandbox base URL (gates `builtin_sandbox`) + optional shared bearer token (§5.7)      |

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

### 3.8 External reference gate (`/ext`)

`GET /ext/{name}/**` is a session-authenticated gate to admin-configured
backends serving figures/PDFs referenced from documents. Point the upstream's
public base at **`/ext/{name}`** (relative — same-origin, so it keeps working
whatever origin or scheme the user reached the server by; an absolute
`https://<nucllei host>/ext/{name}` works too) and in-document references
resolve in the browser with the session cookie, and are handed back to tools
as `url` unchanged (see the reference rules below). Two
backends, chosen by the value's form:

- `http(s)://…` — streaming proxy (2xx passed through, 404 → 404, anything else
  incl. redirects → 502 without `Location`). The root of a name (`/ext/{name}/`,
  `/ext/{name}`) proxies the upstream `<base>/`.
- `file:///abs/path` — static serving from a local root the container sees
  read-only (`FileResponse`: Range / etag / last-modified). The root is resolved
  once; the requested path is resolved and confined to it (symlinks pointing
  outside → 404); directories (including the root of the name, `/ext/{name}/`)
  and missing files → 404; content-type from the extension
  (`application/octet-stream` when unknown).

**Serving scope of a file backend (`EXT_FILE_SCOPES`).** By default everything
under the root is served. When the root is shared with something else — e.g.
the same directory is bound read-only into the sandbox and holds both files
meant for users (`static/`) and raw material only tools should read (`doc/`) —
set `EXT_FILE_SCOPES=cage=static` (repeat the name for several directories:
`cage=static,cage=figs`; a value is a directory relative to the root). Then only
files whose **resolved** path lies inside one of those directories are served;
everything else under the root is a 404, indistinguishable from a missing file.
URLs keep their shape (`/ext/cage/static/X`) — only the range narrows. The rule
lives in one function (`utils/ext_upstreams.ext_file_served`) used by the gate,
by `resolve_url`, and by both bridges below, so a file is never "404 in the
browser but readable through a tool" or the reverse. Reading the same file as a
**sandbox path** (`/mnt/…` via `resolve_path`, or inside the jail) is not
affected: the scope is about what is _served to users_. A name that appears in
`EXT_FILE_SCOPES` stays restricted even if all its entries are invalid (it then
serves nothing — a typo never reopens the whole root); a name that is not a
file backend is ignored with a boot warning.

Both: `..`/backslash/control-char paths → 400; active types (HTML/SVG/JS) are
forced to `Content-Disposition: attachment` + `nosniff`. There is no per-user
access check — the admin-managed allowlist is the source of truth for what a
backend exposes.

**Reference rules** (the single statement of how a tool argument or a stored
reference is interpreted; the code-side definition is the module docstring of
`nucllei/utils/image_refs.py`):

- **The argument name decides the interpretation. The value is never inspected
  to reinterpret it as a different argument.** A sandbox path in `url`, or a URL
  in `path`, fails as what the argument says it is.
- **`url`** — a URL a browser can open: an absolute `http(s)://…` URL, **or a
  same-server relative URL** (resolved as "a URL of this server", against the
  routes the server really has): `/ext/{name}/…` — entry point
  `resolve_url(url, *, max_bytes=None)` — and `/api/v1/files/{id}/content`,
  which `file_id_of_url(url)` routes to a file that the caller then reads with
  its own per-user check.
- **`path`** — an **absolute** path on the sandbox filesystem (`/workspace/…`,
  `/mnt/…`). Entry point: `resolve_path(path, user_id, *, max_bytes=None)`.
  Relative paths are not accepted (since 2026-10-03; they used to be resolved
  against `/workspace`, which turned `path="https://x/y.html"` into
  `/workspace/https:/x/y.html`).
- **`file_id`** — the id of an attachment or a generated file.
- **Pass references along exactly as received** — do not rewrite them, and do
  not prepend an origin to a relative URL.

**Fixed sentences for argument-form errors.** A tool never inspects the value
to choose its wording; a form error states what the argument accepts in a fixed
sentence. The sentences are public constants of `nucllei/utils/image_refs.py`,
and **deployment-layer tools import these constants and use the same wording**
(the text was agreed with the llens tools; change both sides together):

| Constant                  | Text                                                                                                                                     |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `URL_ARG_FORM`            | ``url が受けるのは、スキーム付きの絶対 URL (`http(s)://…`) と、同一サーバの `/ext/…`・`/api/v1/files/{id}/content` だけ。``              |
| `PATH_ARG_FORM`           | ``path が受けるのは、sandbox の絶対パス (`/workspace/…` `/mnt/…`) だけ。``                                                               |
| `WORKSPACE_PATH_ARG_FORM` | ``path が受けるのは、sandbox の絶対パス (`/workspace/…`) だけ。`` (tools that only handle the workspace: `write_file`, `list_workspace`) |
| `FILE_ID_ARG_FORM`        | `file_id は添付・生成物の id。`                                                                                                          |
| `REF_ARGS_RULE`           | `URL は url、sandbox のパスは path、添付・生成物は file_id に、受け取った形のまま渡す (値を書き換えて別の引数に入れ直さない)。`          |

`REF_ARGS_RULE` is the one sentence about where things go, identical in every
tool (it mentions arguments a given tool may not have — it states the rule, not
that tool's signature); tools do not add destinations of their own. Errors are
assembled the same way everywhere:

- `url` not resolvable as a URL:
  `ERROR: <value> は URL として解決できない。` + `URL_ARG_FORM` + `" "` + `REF_ARGS_RULE`
- `path` not a sandbox path:
  `ERROR: <value> は sandbox のパスの形でない。` + `PATH_ARG_FORM` (or
  `WORKSPACE_PATH_ARG_FORM`) + `" "` + `REF_ARGS_RULE`
- `file_id` cannot be read:
  `ERROR: file_id <id> のレコードが無い、またはアクセス権が無い。` + `FILE_ID_ARG_FORM`
  — "no such record" and "not yours" are deliberately one sentence (existence
  is not leaked). `read_attachment` returns a dict, so it carries the same
  sentence without the `ERROR: ` prefix in `{ok: False, error}`.

Wording that depends on the **resolution result** (missing / oversize /
redirect / upstream status / unreachable / outside the allowed upstreams /
outside the serving scope) is separate and built per `RefError.code` (and
`reason`), still without looking at the value.

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
| `http://` / `https://`                           | absolute URL                                      | path `/ext/{name}/{rest}` → that name's `EXT_UPSTREAMS` backend, **whatever the host** (the host is never connected to). Otherwise, if the URL is the `<base>` of an `EXT_PUBLIC_BASES` entry or starts with `<base>/`, the remainder goes to that name's backend. Anything else → `unknown_upstream` (fail closed, not a generic fetch)                                |
| `/ext/`                                          | same-server URL                                   | `/ext/{name}/{rest}` → the same backend as the absolute form. Unknown `name`, or no `name` at all (`/ext/`) → `unknown_upstream`                                                                                                                                                                                                                                        |
| `/workspace/`, `/mnt/`                           | sandbox filesystem                                | `/mnt/…` is read locally from the read-only mount root the sandbox binds (#138); `/workspace/…` goes through the sandbox `/files/read` of the calling user                                                                                                                                                                                                              |
| `/api/v1/files/{id}/content`                     | this server's file delivery URL                   | not read by the resolver (it needs a per-user check): `file_id_of_url(url)` extracts the id (relative, or absolute on any host — same reasoning as `/ext/`), and the caller reads the file with its own check — the pre-send inline step (read access) and `send_to_workspace(url=)` (owner). Passed to `resolve_url` / `resolve_ref` directly it is `unsupported_form` |
| a bare string (no leading `/`, not `http(s)://`) | a file id (what `__feed_image__(file_id)` stores) | the caller builds `/api/v1/files/{id}/content` from it (history builder, completion notice; the frontend's `contextImageUrl` does the same)                                                                                                                                                                                                                             |
| anything else (any other `/…`, empty)            | —                                                 | `unsupported_form`                                                                                                                                                                                                                                                                                                                                                      |

**The root of a name (empty `rest`).** `/ext/{name}`, `/ext/{name}/`, and an
`EXT_PUBLIC_BASES` root with or without its trailing slash (`<base>`, `<base>/`)
are all well-formed and mean the same thing: the root of that name (`rel` is
`""`; the trailing slash is not significant). It is not special-cased — the
empty `rel` goes to the backend like any other: an http backend is asked for
its `<base>/` (exactly what the gate proxies for `/ext/{name}/`), so a `200` is
returned as content and a redirect is `upstream_status` with the `3xx` status; a
file backend's root is a directory, so it is `not_found` (no listing — the gate
answers 404). Until 2026-10-03 the public-base root was answered `not_found`
without asking the upstream, which made a tool report a live top page (the
upstream answers `302`) as a broken link.

**Fragments on `/api/v1/files/{id}/content`.** Only a trailing `#page=N` means
something (read with `split_page`); any other fragment is ignored and the URL
routes to the same file — `file_id_of_url` returns the id with or without a
fragment, so every tool treats `…/content#section` alike.

**Where a URL belongs, without reading it.**
`route_of_url(url) -> (UrlRoute | None, RefError | None)` runs the same single
routing step as `resolve_url` and returns it: `UrlRoute(name, rel, query,
public_base, origin, ext_form)` plus the property `relative` — the upstream
`name` (present in `EXT_UPSTREAMS`), the path under it (`rel`, as written in the
URL, i.e. still percent-encoded, no leading `/`; `""` for the root of a name), `query` (`"?a=b"` or `""`),
that name's `EXT_PUBLIC_BASES` root or `None`, the `origin` (`"scheme://host"`
as given, `None` for a relative URL) and whether the URL had the
`/ext/{name}/…` shape (`ext_form`) or matched a public base. It is pure — no
network, no filesystem, no logging — and fails with the same `code` / `reason`
`resolve_url` would give for that input. It does **not** check existence or the
serving scope (`EXT_FILE_SCOPES`); whether the URL can be read stays
`resolve_url`'s job. Intended for a tool that, after a successful
`resolve_url`, needs the root for in-page links (relative input → `/ext/{name}`;
absolute with a public base → that base; otherwise `{origin}/ext/{name}`)
without re-implementing the URL routing.

**Bridging a URL and a sandbox path.** The same file can have both names (a
file backend whose root lives under `/mnt`, e.g. `EXT_UPSTREAMS=cage=file:///mnt/cage`:
`/ext/cage/static/X` ⇔ `/mnt/cage/static/X`). The model never rewrites one into
the other; the tool it calls does, based on **what the reference resolves to**
(the real file's location), not on the value's shape:

| Direction                                  | Tool (builtin sandbox)       | Resolver function            | Result                                                                                                                                                                                                                    |
| ------------------------------------------ | ---------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| URL → a path the sandbox can open          | `send_to_workspace(url=)`    | `mounted_path_for_url(url)`  | the `/mnt/…` path, nothing copied; otherwise the bytes are copied to `/workspace/imports/`                                                                                                                                |
| sandbox path → something the user can open | `fetch_from_workspace(path)` | `url_for_mounted_path(path)` | the relative `/ext/{name}/…` URL, nothing attached; a file under a file backend's root but outside what it serves is refused (`mounted_path_withheld`); any other file is attached and a `file_id` returned (a duplicate) |

Only URLs can be shown to the user (`/ext/…` or `/api/v1/files/{id}/content`); a
sandbox path means nothing to a browser. `url_for_mounted_path` returns a URL
only for a regular file that the file backend serves (inside its root, and
inside `EXT_FILE_SCOPES` when set) **and** that is under the
read-only mount root, after resolving symlinks (deepest root wins when roots
nest); the path part is percent-encoded, and the URL round-trips through
`resolve_url` / the `/ext` gate to the same file. It never widens what `/ext`
serves — it only names what the gate already serves. Because the path is
percent-encoded, the returned URL can differ **as a string** from the URL a
document's text carries for the same file (e.g. raw Japanese
`…/forms/参考書式_5.pdf` vs `…/forms/%E5%8F%82…_5.pdf`): two spellings of one
file, not two files.

**The serving scope is also the hand-over scope.** `mounted_path_withheld(path)`
is true for a `/mnt/…` path whose resolved file lies under the root of some
`EXT_UPSTREAMS` file backend but in **no** backend's serving scope (the
predicate is `ext_upstreams.ext_file_withheld`, the complement of the gate's
`ext_file_served`). `fetch_from_workspace` refuses such a file — no URL and no
attachment — so `EXT_FILE_SCOPES=cage=static` means one thing: what may be
handed to the user (otherwise `/mnt/cage/doc/x.md`, a raw document with
unreplaced placeholders, would be 404 as a URL yet obtainable as an
attachment). A `/mnt/…` file under no file backend's root (`/mnt/shared/…`) is
still attached, and a backend without `EXT_FILE_SCOPES` serves its whole root,
so nothing changes there. Existence is not checked (a missing path in the
withheld range gets the same refusal). The model reading such a file itself
(`resolve_path`, the jail) is unaffected. Note the limit: a model can still
copy the file into `/workspace` with `run_shell` and attach the copy — the
refusal message tells it not to, but this is a rule, not an enforced boundary.

Relative `/ext/…` was rejected (`relative_ext`) from #139 (2026-09-07) until
2026-10-03, on the grounds that a URL and a filesystem path sharing the leading
`/` invite mix-ups. That was reversed: document figures are now handed to the
model as relative `/ext/{name}/…`, like `/api/v1/files/{id}/content` links
already are — a relative URL keeps rendering when the origin or the scheme
changes, and nothing has to compose an origin (from request headers, with a
fallback, or across a sub-agent on another host). The mix-up is prevented by
the argument name (`url` vs `path`) and by context (a markdown link/image vs
code). Absolute `https://<any host>/ext/{name}/…` URLs keep resolving.

An optional trailing `#page=N` (1-origin) selects a PDF page on any form (only
the trailing `#page=N` is parsed, so it does not collide with a URL's own
fragment). Resolved references are inlined as base64 right before the model
call: images as-is, PDFs rasterized page by page (default page 1) — no File is
created. Anything outside those roots, oversize, missing, or neither image nor
PDF becomes `(image unavailable)` — as does any other `image_url` form
(protocol-relative `//host/…`, non-http(s) schemes, relative paths, any other
`/…`), including
an external URL passed in `image_url` on the headless `/api/chat/completions`
path. The mount contents (and `EXT_PUBLIC_BASES` names lacking an
`EXT_UPSTREAMS` backend) are logged once at boot.

**Failure reasons.** `resolve_url` / `resolve_path` / `resolve_ref` all return
`(LoadedRef | None, RefError | None)` — `(loaded, None)` on success and
`(None, RefError)` on failure, never raising; `max_bytes` overrides the `EXT_INLINE_MAX_BYTES` size cap for that call
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
values are a closed set too, and today only `unknown_upstream` carries one
(`None` on every other code):

| `reason`               | When                                                                                                                                                                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `unknown_name`         | `/ext/{name}/…` (relative or absolute) or an `EXT_PUBLIC_BASES` URL whose `name` has no `EXT_UPSTREAMS` backend                                                                                                                      |
| `not_ext_form`         | a relative `/ext/…` URL from which **no name can be taken**: `/ext/`, `/ext//x`, or a name outside `[a-z0-9_-]+` (`/ext/CAGE/x`). `/ext/{name}` and `/ext/{name}/` are well-formed (the root of that name) and never get this reason |
| `outside_public_bases` | an absolute URL that is neither `/ext/{name}/…` nor under an `EXT_PUBLIC_BASES` root (an external site)                                                                                                                              |

| `code`                  | When                                                                                                                                                                                                                                                                     | Extra fields                              |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------- |
| `unsupported_form`      | not a form the entry point accepts: for `resolve_url`, not an `http(s)://` URL nor same-server `/ext/…`; for `resolve_path`, not `/workspace/…` / `/mnt/…`; for `resolve_ref`, neither (relative path, empty, `ftp:`, `//host/…`, `/etc/…`)                              |                                           |
| `traversal`             | `..`, backslash, NUL or other control chars anywhere in the reference                                                                                                                                                                                                    |                                           |
| `unknown_upstream`      | `/ext/{name}/…` (relative or absolute) or `EXT_PUBLIC_BASES` URL whose `name` has no `EXT_UPSTREAMS` backend, **or** an absolute URL matching no namespace at all (external site), **or** a relative `/ext/…` with no usable `name` (`/ext/`). `reason` tells them apart | `reason` (see above)                      |
| `not_found`             | file backend / `/mnt/…`: missing, a directory (incl. the root of a file backend, `/ext/{name}/`), or resolves outside the root (symlink escape); http backend `404`; sandbox `/files/read` `404`                                                                         | `status=404` for http                     |
| `too_large`             | over the size cap (`max_bytes`, default `EXT_INLINE_MAX_BYTES`) on any route (file, http stream, workspace) or over the sandbox's own read limit (`413`)                                                                                                                 | `size`, `limit`, `size_exact` (see below) |
| `upstream_status`       | http backend answered non-2xx other than 404 — including `3xx` redirects (not followed)                                                                                                                                                                                  | `status`                                  |
| `upstream_unreachable`  | http backend connection failure, timeout, or `InvalidURL`                                                                                                                                                                                                                |                                           |
| `workspace_unavailable` | `/workspace/…` without `SANDBOX_URL`, or the sandbox API failed for a reason other than 404 / 413 (unreachable, 401 token mismatch, 5xx)                                                                                                                                 | `status` when the sandbox answered        |

`too_large.size_exact` says whether `size` is the real size or only a lower
bound: `True` when it comes from `stat` (file backend, `/mnt/…`, sandbox `413`),
a fully read workspace file, or an http `Content-Length`; `False` when the http
stream had no `Content-Length` and was aborted just past `limit` — `size` is
then the bytes received so far (slightly above `limit`) and the real size is
unknown. On every other code it is meaningless and stays `False`. Tool hint:
when `False`, say "about N MB or more" rather than quoting `size` as the file's
size, and suggest retrying with a larger `max_bytes` (e.g. twice the cap).

| Var                    | Kind | Default            | Effect                                                                                                                                                                                                                                                                                              |
| ---------------------- | ---- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `EXT_UPSTREAMS`        | env  | empty = disabled   | comma-separated `name=value` allowlist; value is an http(s) base URL or `file:///abs/path` (e.g. `cage=file:///mnt/cage,his=http://172.17.0.1:13303`); invalid entries are dropped with a warning                                                                                                   |
| `EXT_PUBLIC_BASES`     | env  | empty              | comma-separated `name=baseURL` (same format as `EXT_UPSTREAMS`, http(s) only): the public root of the original intranet site for that name (e.g. `his=http://his-info.example/docs`). Absolute URLs under `<base>/` resolve via that name's `EXT_UPSTREAMS` backend; the name must also exist there |
| `EXT_FILE_SCOPES`      | env  | empty = whole root | comma-separated `name=subdir` (same format; repeat the name for several): restrict what a **file backend** serves to those directories under its root (e.g. `cage=static`). See "Serving scope" above                                                                                               |
| `EXT_INLINE_MAX_BYTES` | env  | `26214400` (25 MB) | size cap per reference when inlining context images (URL and filesystem references alike)                                                                                                                                                                                                           |

### 3.9 Audio (STT / TTS)

Feature gates: `ENABLE_AUDIO_STT`, `ENABLE_AUDIO_TTS` (RC, both `true`; `false`
hides the UI affordance and 403s the endpoints).

STT: `AUDIO_STT_ENGINE` (RC, `""` = bundled faster-whisper; or `openai`, `deepgram`,
`azure`). Whisper knobs: `WHISPER_MODEL` (`base`; a name or a local model path),
`WHISPER_MODEL_DIR`, `WHISPER_LANGUAGE` (`""` = auto; set `ja` for Japanese-only
sites), `WHISPER_COMPUTE_TYPE` (`int8`), `WHISPER_CPU_THREADS` (`0`),
`WHISPER_NUM_WORKERS` (`1`), `WHISPER_VAD_FILTER` (`false`). Engine-specific:
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

| Var                                                                                                                   | Default | Effect                                                                                                                                                                                                        |
| --------------------------------------------------------------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GLOBAL_LOG_LEVEL`                                                                                                    | `INFO`  | root level; per-source overrides: `MAIN_LOG_LEVEL`, `MODELS_LOG_LEVEL`, `OPENAI_LOG_LEVEL`, `OLLAMA_LOG_LEVEL`, `AUDIO_LOG_LEVEL`, `CONFIG_LOG_LEVEL`, `DB_LOG_LEVEL`, `WEBHOOK_LOG_LEVEL`, `OAUTH_LOG_LEVEL` |
| `ENABLE_OTEL` + `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER` | `false` | OpenTelemetry traces                                                                                                                                                                                          |

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
restricted to a path allowlist — §3.3). Role tiers: `pending` (no access),
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

**Session idle timeout.** `JWT_EXPIRES_IN` (§3.3) is an _idle_ timeout, not an
absolute deadline: the session is extended only on requests the browser marks as
human-driven (an `X-Nucllei-Activity: 1` header the web UI adds after a keypress,
click or tap), and only once the token is past half its lifetime. Background
polling therefore never keeps a session alive, so leaving a tab open at an unattended
shared terminal does not keep the user logged in. `GET /api/v1/auths/` does **not**
refresh the session (that would let a page reload extend it forever), and API-key
requests are never extended — they carry their own credential. `/api/config`
exposes the value as `session_idle_timeout` (seconds, `null` when unlimited) so the
UI can send the user back to the login screen once it elapses. Default `-1` keeps
sessions unlimited, unchanged from before.

### 4.2 Models

`GET /api/models` — the canonical model list for clients: only _workspace models_
(presets), filtered by the caller's group ACL. The `id` field is what you pass as
`model` in completions. Model CRUD lives under `/api/v1/models/*` (create/update/
delete are admin-only). Every model row **must** declare a `base_model_id`
(pointing at a raw connection model) — raw connection models themselves are never
exposed as chat models; they only exist as base choices (admin inventory:
`GET /api/models/base`).

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
(`ATTACHMENT_MAX_SIZE`, `ATTACHMENT_MAX_COUNT`, `ATTACHMENT_ALLOWED_EXTENSIONS`,
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
  `id, tool_name, status, created_at, finished_at, duration, error, delivered`
  but never the result body. `delivered` is true once the result reached the
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

| Parameter                       | Contents                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__user__`                      | `{"id","email","name","role","info","valves"?}` — `info` is the opaque extra-metadata dict (§3.4) for external-system authorization                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `__event_emitter__`             | `async fn(event)` — push UI events mid-call (§5.5); `None` on the headless API path. **`status` is gone platform-wide** — use `__progress__`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `__event_call__`                | `async fn(event)` — round-trip to the user (`confirmation` / `input`), awaits their reply; `None` headless                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `__progress__`                  | `fn(description="", *, current=None, total=None, done=False)` — report progress while the tool runs; shown in this tool's own block (§5.5). Volatile                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `__feed_image__`                | `fn(ref)` — feed an image into the model's context (the "model's eyes") — injected after the tool results as a user-role `image_url` message for the follow-up call, kept as a reference in `results[].context_images`, and re-sent in the same position on every later turn (re-resolved each time; a reference that no longer resolves becomes `(image unavailable)`); `ref` is a file_id, a URL (same-server `/ext/{name}/…`, absolute `https://…/ext/{name}/…`, or one under `EXT_PUBLIC_BASES`), or a sandbox path (`/mnt/…`, `/workspace/…`); optional `#page=N` for PDFs — resolved by `utils/image_refs.py`, §3.8; chat path only |
| `__display__`                   | `fn(content, kind="markdown")` — render content to the user outside the collapsed tool block (not fed to the model); chat path only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `__chat_id__`, `__message_id__` | current chat / message ids                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `__id__`                        | this tool's id                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `__files__`                     | the chat's attachment refs (already ACL-filtered)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `__messages__`                  | the outbound message list                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `__metadata__`                  | request metadata (chat/message ids, files, skill activations, ...)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `__request__`                   | the FastAPI `Request` (→ `request.app.state.config` etc.)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `__model__`                     | a model dict — note: for tools this is the _task_ model, not necessarily the chat model                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

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
  (JSON-serialized). Raising is safe — the exception string becomes the tool
  result — but prefer returning `"ERROR: ..."` strings to steer the model.
- Generators are not supported.
- Per-call timeout `CHAT_TOOL_EXECUTION_TIMEOUT` (default 300 s) and loop cap
  `CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS` (default 30) — see §3.6.
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
  the follow-up call; an `/ext/` URL (relative or absolute), an
  `EXT_PUBLIC_BASES` URL, a `/mnt/…` or a `/workspace/…` reference needs no File
  (§3.8 for the reference rules).

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
    the calling user's own chats.
  - `builtin_chat_files` — `list_chat_files` / `grep_chat_files`: attachments
    issued in the current chat, metadata only.
  - `builtin_attachment_text` — `read_attachment(file_id, offset?, max_chars?)`:
    the extracted text of an owned attachment (`Files.data.content` written by
    the deployment's Attachment function — §7; text-typed files fall back to a
    raw decode). Owner-only by direct file_id, deliberately **not** chat-scoped:
    mount it on an agent preset and a subagent (§5.8) can read materials the
    parent references by file_id without the content ever entering the parent's
    context.
  - `builtin_sandbox` — `run_python` / `run_python_once` / `run_shell` /
    `send_to_workspace` / `write_file` / `list_workspace` /
    `fetch_from_workspace`. **Registered only when `SANDBOX_URL` is set**; with
    no value the tool is absent from `/api/v1/tools/` entirely (and an existing
    row is pruned on the next boot, ACL included). It is gated because, unlike
    the three above, it needs an external service to be reachable — a builtin
    that is listed but always errors would be worse than one that is absent.
    `SANDBOX_TOKEN` is the **optional** shared bearer token of the sandbox API
    and is _not_ part of that gate: sandbox-side authentication is opt-in, so a
    URL-only deployment still works (unauthenticated). When set, it must carry
    the same value on both sides — the backend then sends
    `Authorization: Bearer` on every sandbox call and the sandbox rejects
    everything else with 401; a URL-without-token deployment logs a warning at
    boot. **Both are read from the environment only — never a valve or a DB
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
    `WORKSPACE_PATH_ARG_FORM` / `PATH_ARG_FORM` + `REF_ARGS_RULE`); `/mnt/…`
    (read-only reference data) is refused by `write_file` and `list_workspace`
    with a pointer to `run_shell` (a distinction inside the same `path`
    argument, based on what the path resolves to).
    `fetch_from_workspace(path)` turns a sandbox file into something the user
    can open, and **also takes `/mnt/…`**: a `/workspace/…` file is read through
    the sandbox, registered as a File and attached to the chat (`file_id`
    returned). A `/mnt/…` file that an `EXT_UPSTREAMS` file backend already
    serves (within `EXT_FILE_SCOPES` when the deployment sets it) is **not copied or attached** — the tool answers with its relative
    `/ext/{name}/…` URL (`image_refs.url_for_mounted_path`) and tells the model
    to put that URL in the message (image syntax for images, a link otherwise).
    A `/mnt/…` file under a file backend's root but **outside its serving
    scope** is refused — neither a URL nor an attachment (§3.8, "The serving
    scope is also the hand-over scope").
    Any other `/mnt/…` file is read by the server from its own read-only mount
    (`resolve_path`: confined to the mount root, capped at
    `EXT_INLINE_MAX_BYTES`) and attached like a workspace file. Failures are
    worded per `RefError.code`: unreadable (missing, a directory, or a link
    leaving the mount — one wording, fail closed) vs. over the cap (with actual
    size and limit). `path` is only ever a sandbox path — a URL or a file URL in
    it is refused as "not a sandbox path" (fixed sentence). The returned
    `/ext/…` URL is percent-encoded, so it may be a different spelling of the
    URL a document shows for the same file. This is the reverse of
    `send_to_workspace(url=)` below (§3.8, bridging table).
    `send_to_workspace(filename?, file_id?, url?)` copies into
    `/workspace/imports/` and returns the absolute path. Its arguments are
    interpreted by name, as everywhere else (§3.8 reference rules): `file_id` =
    an owned attachment / generated file, `url` = a **URL** — absolute
    `http(s)://…` or same-server `/ext/{name}/…` — resolved by the shared
    resolver (`resolve_url`), so exactly the URLs the feed tools can read
    (`/ext/{name}/…` relative or on any host, and URLs under
    `EXT_PUBLIC_BASES`), through the `EXT_UPSTREAMS` backend, no redirects
    followed, no cookie forwarded.
    **Any other URL is refused (`unknown_upstream`); it is not a generic
    LAN/web fetcher** (until 2026-10-03 it fetched any LAN URL outside a CIDR
    deny-list — valve `url_blocked_cidrs`, now removed). Failures are reported
    per `RefError.code` (missing / oversize with actual size and limit /
    redirect / upstream status / unreachable / outside the allowed upstreams).
    Valve `url_max_mb` (default 50) is the per-file cap passed as `max_bytes`.
    When the URL resolves to a **file backend whose file lives under `/mnt`**
    (`image_refs.mounted_path_for_url`), nothing is copied: the tool answers
    with that `/mnt/…` path, because `/mnt` is the same read-only mount the
    sandbox binds (#138) — this relies on the deployment binding the same
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

### 5.8 Subagents (`run_subagent`)

When a model preset lists agent presets in `meta.subagentPresetIds` (and
`meta.features.subagent` is not set to `false`), one pseudo-tool is mounted on
the parent chat's tool loop:

- `run_subagent(preset_id, instruction, session_id?)` — spawns a child **agent
  session** (a first-class chat with `kind: "agent"`, linked to the parent
  chat/message) and runs one turn on the selected preset. Returns
  `{ok, session_id, answer, usage?, truncated?}` — the child's final assistant
  text only (8000 chars max), never the transcript. Pass `session_id` instead
  of `preset_id` to send a follow-up instruction to an existing session.

`run_subagent` is **backgroundable** (§5.9): calling it with the shared
`background: true` argument runs the child turn as a background task and
returns `{ok, task_id, tool_name, status: "running"}` immediately. On
completion the result is delivered automatically by the server (a notice turn
in the parent chat — §5.9); to wait for it right now use
`await_task(task_ids)`. `session_id` stays the resumable session handle,
`task_id` is the record of one run. There is no dedicated `await_subagent` —
async collection is unified on the execution channel.

Limits: nesting depth `SUBAGENT_MAX_DEPTH` (default 2), process-wide
concurrency `SUBAGENT_MAX_CONCURRENT` (default 4 — spawns over the limit fail
immediately, background included), per-call timeout `SUBAGENT_TURN_TIMEOUT`
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
- `check_task(task_ids?, include_done?)` — non-blocking status poll. With no
  arguments lists the chat's running tasks (oldest first); `include_done:
true` lists terminal ones too (newest first, max 50); with `task_ids` returns
  those entries in order. Each entry:
  `{task_id, tool_name, status, started_at, elapsed_s}` plus `progress` while
  running or `duration`/`error` when terminal. Never returns the result body
  and never writes (read-only).
- `await_task(task_ids, timeout?)` — collect results now. One bounded window
  (default 90 s, clamped to 10–300) covers all ids (max 20 per call);
  already-terminal tasks return immediately (idempotent — results are kept
  indefinitely). Per entry: done →
  `{ok, task_id, tool_name, status, result, duration?, usage?, truncated?}`
  (with the original tool's render descriptor when exactly one done task was
  requested, and a one-time replay of `context_images`/`display`
  side-channels); `failed/stopped/orphaned` → `{ok: false, ..., error}`;
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
reads the notice and continues on its own. `await_task`/`stop_task` reports
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

Over HTTP the rows are readable and stoppable by the chat's owner:
`GET /api/v1/chats/{id}/executions` (object form with `delivered` /
`resume_suspended`, no result bodies — §4.6) and
`POST /api/v1/chats/{id}/executions/{exec_id}/stop` (the UI's per-execution stop
button). There is no SSE push — the frontend polls.

**Eligibility is opt-in** (`backgroundable` key on the tool entry):

- builtin: `run_subagent`, plus `builtin_sandbox`'s three execution entry points
  (`run_python`, `run_python_once`, `run_shell`) so long-running code can be
  backgrounded.
- DB tools: declare a `backgroundable` class attribute on the `Tools` class —
  `True` (all functions) or a list/tuple/set of function names (tool CRUD is
  admin-only, so this is an admin decision). Builtins declare it the same way;
  the entry is built from the loaded module either way.
  Note that the sandbox serializes calls per user, so a
  synchronous call made while a backgrounded one is still running waits for it
  (head-of-line blocking — backgrounding frees the parent turn, not the
  sandbox).
- Never eligible: `load_skill`, `check_task`, `await_task`, `stop_task`, any
  tool that declares its own `background` parameter (collision guard), and
  tool-server tools (no opt-in surface in v1). `background: true` on a
  non-eligible tool is ignored (synchronous run, warning logged).

Timeouts and limits: per-entry `timeout` wins, else `ASYNC_EXEC_TIMEOUT`
(default 1800 s). DB tools declare per-entry timeouts with a `timeouts` class
attribute on the `Tools` class — a `{function_name: seconds}` dict, symmetric
with `backgroundable`; the same value also overrides the synchronous
`CHAT_TOOL_EXECUTION_TIMEOUT` (default 300 s) for that entry, which is how a
long-running tool (e.g. speech transcription) survives a synchronous call.
Concurrency: `ASYNC_EXEC_MAX_CONCURRENT` (default 4 per
user) and `ASYNC_EXEC_MAX_TOTAL` (default 16 per process); exceeding either
fails immediately. Tool results — synchronous and background alike — are
truncated once at the production point at `TOOL_RESULT_MAX_CHARS` (default
65536; truncation demotes structured rendering to text; the collectors'
envelopes are exempt because their contents are already truncated at save
time). Rows are optionally purged after `EXEC_RETENTION_DAYS` (default 0 =
keep forever — results stay re-collectable indefinitely). Spawning is
single-process only: with `UVICORN_WORKERS > 1` it is refused (collection and
listing still work). Executions are stopped (not deleted) when their chat is
deleted; a server restart marks in-flight rows `orphaned` — the record
survives, the computation does not resume.

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
