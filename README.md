# Nucllei

Nucllei is a foundation platform for AI agent harnesses.

A self-hosted AI agent platform that runs on-premise.

- Simple tool definitions
- Skills with lazy tool loading
- Attachment Functions for flexible file upload gating

Deploying Nucllei, configuring it, calling its HTTP API, or writing Tools / Skills /
Functions for it — see **[README_API.md](./README_API.md)**.

## Requirements

- **Node** — version pinned in `.node-version`, managed with your tool of choice (e.g. mise). Use **pnpm**, not npm.
- **Python 3.11–3.13** — managed with **uv** (creates `.venv` at the repo root).
- **Docker** (or rootless Podman) — for backend integration tests (postgres) and image builds.

## Development

```sh
pnpm install
uv sync

# backend — FastAPI on :3000, hot reload
uv run task dev

# frontend — Vite on :5173, proxies /api, /openai, /ws etc. to :3000
pnpm dev
```

Open http://localhost:5173. The dev setup mirrors the production single-origin layout,
so cookie auth works out of the box. Create the first admin with:

```sh
uv run task create-dev-admin   # conventional dev admin; any account: uv run nucllei create-admin --email ...
```

The backend reads a gitignored `.env` at the repo root on startup (`--reload` does not
watch it — restart the backend after changing it).

## Tests and checks

```sh
uv run pytest         # backend; postgres integration tests auto-start a container
pnpm test:frontend    # vitest
pnpm check            # svelte-check — keep warnings at 0
pnpm build            # catches orphan imports that vitest misses
```

Formatting is enforced in CI (black for backend, prettier for frontend, ruff for lint).
Enable the local pre-push hook once per clone:

```sh
git config core.hooksPath dev/hooks
```

Always format via `pnpm format` (not `pnpm exec prettier`, which can resolve plugins
differently and produce different output than CI).

## Versioning

The single source of truth is git tags, in a single format: `vYYYYMMDD-NN`. Cut one with
`uv run task release`. The version is resolved at startup/build time via `APP_VERSION` env →
`git describe` → `package.json` fallback. Release images pass `--build-arg APP_VERSION=<tag>`.

One tag builds both images at the same version — `ghcr.io/technoplasm/nucllei` and
`ghcr.io/technoplasm/nucllei-sandbox` — as two jobs of a single workflow
(`.github/workflows/docker-build.yaml`) behind one set of CI gates. To smoke-test the
image pipeline without cutting a tag, run that workflow via `workflow_dispatch`
(it publishes `:dev`).

## License

Nucllei is source-available under the
[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)
with additional permissions — see [LICENSE](./LICENSE). It is free for noncommercial use,
including self-deployment by hospitals, universities, research and public institutions for
their own staff. Vendors that deploy, operate, host, or maintain it for others as a business
need a commercial license: contact Technoplasm, Inc. (see the repository's GitHub page).
Upstream code is Open WebUI v0.6.5, [BSD-3-Clause](./LICENSE.open-webui); that notice is retained.
