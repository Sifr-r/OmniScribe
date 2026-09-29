# Deployment Guide

This document walks through deploying OmniScribe in four common
profiles. **Start at the top, stop at the profile that matches your
use case.** The local-desktop default is correct for almost every
user; only step up to LAN, public-internet, or multi-worker clusters
when you actually need to.

> **Install path (updated 2026-09-07):** the supported end-user install
> is the **source install** (Profile 1 below). The single-binary
> Windows PyInstaller bundle per
> [RFC 001 — End-User Install Path](rfcs/2026-09-end-user-install.md)
> Option A **shipped in v0.3.0**; see
> [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
> for build and distribution details.

## Profile 1: Local Desktop (Default)

You're running OmniScribe on your own laptop. The browser landing
page at `http://localhost:8000` is a status page only — the
**interactive UI is the Flutter desktop client** under `client/`.
The in-browser workstation that earlier versions shipped was
deprecated in 2026-08-28 (audit Domain 3, Critical-1); see
[RFC 001 — End-User Install Path](rfcs/2026-09-end-user-install.md)
for the path to a single-binary distribution.

```bash
# Backend (Python 3.11+ and uv required)
uv sync --extra web --extra preprocessing
uv run omniscribe-server --port 8000

# Frontend (in another terminal)
cd client
flutter pub get
flutter run -d windows   # committed desktop target; use -d chrome for web
```

That's it for now. No auth, no reverse proxy, no Docker. The
Settings tab in the Flutter client points at `http://localhost:1234/v1`
(LM Studio) by default; start LM Studio, load a model, OCR.

**What you get:**

- Upload, SSRF, and placeholder-token guards are available, but bearer auth
  is disabled on the loopback default and rate limiting is disabled unless
  `OMNISCRIBE_RATE_LIMIT_PER_MIN` is set. Any process that can reach
  `localhost:8000` is trusted.
- Per-run `omniscribe-ocr-*` work directories are removed upon run completion.
  Preview cache files are cleaned during cache eviction or process shutdown, and
  token-bound result artifacts use the configured artifact TTL and the runtime's
  periodic cleanup loop.
- The VLM endpoint defaults to LM Studio on `localhost:1234`; if
  you point it at a third-party provider, see "Third-party VLM"
  below.

## Profile 2: LAN / Trusted Network

You have a small home-lab or office server. You want to reach it from
your laptop on the same Wi-Fi.

```bash
export OMNISCRIBE_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export ALLOW_SSRF_LOCAL=false
export OMNISCRIBE_MAX_UPLOAD_MB=2048
export OMNISCRIBE_RATE_LIMIT_PER_MIN=30
uv run omniscribe-server --host 0.0.0.0 --port 8000
```

Add the bearer token on each Flutter client. The token is held in
process memory for the duration of the session; restarting the client
requires re-entering it.

**What changed from profile 1:**

- `OMNISCRIBE_AUTH_TOKEN` gates protected routes. A 32+ character random secret
  is strongly recommended (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
  The runtime checks presence on non-loopback binds and rejects known placeholders
  (`change-me-in-prod`, `placeholder`, `example-token-replace-me`,
  `replace-this-with-a-real-secret`) at startup with a clear `SystemExit` (override with
  `--allow-placeholder-token` or `OMNISCRIBE_ALLOW_PLACEHOLDER_TOKEN=true` for local dev).
  Public exceptions are `/`, static assets, `/api/sample-pdf/*`, `/api/health`,
  `/api/healthz`, `/ready`, `/readyz`, and CORS `OPTIONS` requests.
  `BearerAuthMiddleware` gates both HTTP and WebSocket handshakes, validating bearer
  tokens via `Authorization: Bearer` or `?auth_token=` / `?token=` query parameters,
  and closing unauthenticated WebSocket handshakes with close code 4401.
- `ALLOW_SSRF_LOCAL=false` blocks the URL fetcher from reaching
  `localhost` / private IPs. Only public URLs work.
- The upload cap drops to 2 GB and the rate limit to 30/min — adjust
  to taste.

## Profile 3: Public Internet (Reverse Proxy)

You're hosting OmniScribe on a VPS or behind a domain. **Do not skip
the reverse proxy** — OmniScribe ships no TLS termination and you do
not want credentials in cleartext on a public IP.

The reference deployment uses [Caddy](https://caddyserver.com/) for
TLS + basic auth fallback + automatic HTTPS. nginx or Traefik work
the same way.

### Caddyfile

```caddy
omniscribe.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

### compose.yaml

```yaml
services:
  api:
    build: .
    image: omniscribe:app
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"   # M9: localhost only
    environment:
      OMNISCRIBE_AUTH_TOKEN: "${OMNISCRIBE_AUTH_TOKEN:?required}"
      ALLOW_SSRF_LOCAL: "false"
      OMNISCRIBE_MAX_UPLOAD_MB: "10240"
      OMNISCRIBE_RATE_LIMIT_PER_MIN: "30"
      LLM_API_BASE: "${OMNISCRIBE_LLM_API_BASE:-http://host.docker.internal:1234/v1}"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
```

The `healthcheck` block (M11) lets Docker restart the container on
silent crashes; configure your orchestrator accordingly.

### Generate a Token

```bash
export OMNISCRIBE_AUTH_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
echo "$OMNISCRIBE_AUTH_TOKEN" >> ~/.config/omniscribe/token
```

A 32+ character random secret is strongly recommended (`secrets.token_urlsafe(32)`).
The startup placeholder check refuses to start on any non-loopback bind if
`OMNISCRIBE_AUTH_TOKEN` is unset or matches known placeholders such as
`change-me-in-prod`, `placeholder`, `example-token-replace-me`, or
`replace-this-with-a-real-secret`.

### Token scope (what is actually enforced)

There is a **single enforced bearer token**: `OMNISCRIBE_AUTH_TOKEN`.
The `BearerAuthMiddleware` wired in `server.py` checks it (constant-time
compare via `hmac.compare_digest`) on every route it guards across both `http`
and `websocket` ASGI scopes. For HTTP routes, bearer tokens are accepted via
`Authorization: Bearer <token>` (or `?token=` on SSE `/events` endpoints).
For WebSocket endpoints, tokens are accepted via `Authorization: Bearer <token>`
or `?auth_token=` / `?token=` query parameters; unauthenticated handshakes are
closed with WebSocket close code 4401. Per-route token scoping does not
exist — earlier versions of this section described
`OMNISCRIBE_OCR_AUTH_TOKEN` / `OMNISCRIBE_TRANSLATION_AUTH_TOKEN`
variables that were never implemented.

One related variable exists: `OMNISCRIBE_TRANSCRIPTION_AUTH_TOKEN` is
**not** an auth credential — it is only consumed by the transcription
config store as the mask source for the `/api/config/transcription`
preview, so a configured token can be displayed as `******` without
being revealed. Setting it grants no access and exempts nothing.

> **P10 (audit 4.6):** The ASGI Middleware Suite (bearer auth + rate
> limit + upload size) shipped in Waves 11, 13, and 14. The live
> middleware contract — placeholder-token rejection on non-loopback,
> constant-time comparison — is documented in
> [SECURITY.md](SECURITY.md) §"Security Features".

## Profile 4: Multi-Worker LAN / Cluster (Redis + Distributed Workers)

You want to scale OCR and async translation across multiple background worker
processes or distributed compute nodes on a private network, using Redis as
the shared broker, state store, and event coordinator.

In this profile:
- Redis acts as the centralized state backend (`OMNISCRIBE_STATE_BACKEND=redis`)
  and distributed job broker (`OMNISCRIBE_JOBS_MODE=redis`).
- The API server handles document ingestion, uploads, client queries, and
  WebSocket / SSE subscriptions.
- Standalone `omniscribe-worker` processes run concurrently to execute OCR and
  translation workloads.

### 1. Provision Redis

Ensure Redis is running with authentication configured:

```bash
docker run -d --name omniscribe-redis -p 6379:6379 \
  redis:7-alpine redis-server --requirepass "$REDIS_PASSWORD"
```

### 2. Run the API Server

On your web / API host:

```bash
export REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:6379/0"
export OMNISCRIBE_STATE_BACKEND=redis
export OMNISCRIBE_JOBS_MODE=redis
export OMNISCRIBE_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export ALLOW_SSRF_LOCAL=false
uv run omniscribe-server --host 0.0.0.0 --port 8000
```

### 3. Run Standalone Workers

On one or more worker nodes (or in separate background processes):

```bash
export REDIS_URL="redis://:${REDIS_PASSWORD}@${REDIS_HOST}:6379/0"
export OMNISCRIBE_STATE_BACKEND=redis
export OMNISCRIBE_JOBS_MODE=redis
export LLM_API_BASE="http://127.0.0.1:1234/v1"

# Launch worker with 4 concurrent execution loops
uv run omniscribe-worker --concurrency 4
```

#### CLI Options for `omniscribe-worker`:
- `--concurrency` / `-c`: Number of concurrent worker loops (default: 2).
- `--redis-url`: Redis connection URL (default: read from `REDIS_URL` env).
- `--visibility-timeout`: Visibility lease timeout in seconds for claimed jobs (default: 300.0).
- `--poll-interval`: Polling interval in seconds when the queue is empty (default: 0.2).
- `--log-level`: Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default: `INFO`).

#### Distributed Worker Operational Guarantees:
- **Atomic Job Claims & Leases:** Workers claim queued jobs atomically from
  `omniscribe:jobs:queue` using Lua scripts, acquiring a claim in
  `omniscribe:jobs:active` and setting `omniscribe:jobs:lease:{job_id}`. A background
  heartbeat loop refreshes worker liveness (`omniscribe:jobs:heartbeat:{worker_id}`);
  orphaned leases from crashed workers automatically expire and requeue.
- **Spool Containment Invariants:** `src/omniscribe/plugins/jobs_redis.py` enforces
  strict spool containment. Deserialized `input_path` locations must reside strictly
  beneath trusted spool roots (`OMNISCRIBE_SPOOL_DIR` or `OMNISCRIBE_ARTIFACT_DIR`)
  and are validated against the authoritative `input_path` in the persisted
  `JobRecord`. Staged input files are cleaned up upon cancellation or completion.
- **Cancellation & Pub/Sub:** Real-time cancellation signals broadcast across
  `omniscribe:jobs:control` Pub/Sub channels. Active tasks cancel in-flight work and
  reclaim resources immediately.
- **Graceful Shutdown:** On SIGINT or SIGTERM, workers stop accepting new jobs,
  drain active jobs up to a drain timeout, and cleanly requeue uncompleted jobs
  back to Redis before exiting.

## Third-party VLM (OpenAI / Anthropic / Groq)

To send OCR images to a hosted VLM instead of LM Studio:

1. Set `OMNISCRIBE_LLM_API_BASE` to the provider's OpenAI-compatible endpoint
   (e.g. `https://api.openai.com/v1`).
2. Set `OMNISCRIBE_LLM_API_KEY` to your provider key.
3. Set `OMNISCRIBE_LLM_MODEL` to the model ID you have access to (e.g.
   `gpt-4o-mini`).

The Settings tab stores the third-party provider coordinates
(endpoint, key, model) in the in-memory `/api/config` store; the
server-side bearer contract is the single `OMNISCRIBE_AUTH_TOKEN`
described above.

**Privacy warning:** documents and extracted text leave your machine
when you point at a third-party endpoint. Review the provider's
data-retention policy before uploading sensitive material.

## Async Translation (harness JobQueue)

The synchronous `/api/translate` endpoint works without any extra
infrastructure. `/api/translate/async` dispatches tree-aware translation
on the in-process harness JobQueue (single worker, `plugins/jobs.py`);
poll `GET /api/translate/status/{job_id}` for the client status
vocabulary. There is no Celery worker service and no `--profile async` —
the compose stack is `api` + `redis` only. Redis serves double duty:
as the optional `REDIS_URL` state backend (`OMNISCRIBE_STATE_BACKEND=redis`)
and as the multi-worker dispatch transport (see Profile 4 above).

```bash
uv sync --extra web --extra preprocessing --extra async-translation
docker compose up -d   # api + redis
```

Before starting Compose, set both `REDIS_PASSWORD` and
`OMNISCRIBE_AUTH_TOKEN` in the project `.env` (see `.env.example`). Generate
independent values with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Compose forwards the auth token to the API container and rejects an empty
value. The server requires authentication because it listens on `0.0.0.0`
inside the container, even with a loopback-only host port. Enter the same
`OMNISCRIBE_AUTH_TOKEN` in the Flutter client's backend Bearer Token setting;
provider API keys remain separate.

The `async-translation` extra installs the LangGraph translation core
(`async-translation`) dependencies; translated output is stored as a
token-bound text artifact and fetched via `GET /api/text/{artifact_id}`.

## Local Troubleshooting

When running the OmniScribe server locally:

- If the server fails to start, verify that dependencies are synced (`uv sync --extra web`) and run `uv run omniscribe-server --port 8000`.
- To connect the Flutter desktop client, navigate to `client/` and run `flutter run -d windows` (or macos/linux).
- Ensure your local VLM (e.g. LM Studio / Ollama) is running on the configured `LLM_API_BASE` (default `http://localhost:1234/v1`).


## Backup & Recovery

By default (since 2026-09-05), OmniScribe persists job and artifact
state to a single SQLite file via
`SQLiteStateBackend` (`src/omniscribe/plugins/state_backend_sqlite.py`).
A restart preserves the state. The SQLite database lives at
`<artifact_dir>/omniscribe-state.db` (WAL mode); artifact binaries are
stored alongside it under `<artifact_dir>/<id>.bin`. Backing up this
directory captures all job records and artifacts.

To opt back into the previous in-memory behaviour (every restart loses
history):

```bash
export OMNISCRIBE_STATE_BACKEND=memory
```

The server logs a `WARN` line at boot to remind you that state is
ephemeral. See
[`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md#async-translation-result-is-gone-after-restart)
for the full story.

## Upgrading

1. `uv sync` (or `docker compose pull`)
2. `uv run omniscribe-server` (or `docker compose up -d`)
3. Visit `http://localhost:8000/api/health` (or `/api/healthz`) to confirm the new version
4. Review the [CHANGELOG](CHANGELOG.md) for breaking changes

The Flutter client's backend URL, bearer token, theme, and similar UI choices
are process-local and must be re-entered after a client restart. VLM endpoint,
key, model, and numeric OCR settings changed through `/api/config` are written
server-side to `.env`.

### Upgrading from a pre-LanceDB Glossary

Migrate the legacy `glossary_library/library.json` +
`chroma_db/lanes_lexicon` pair to the new LanceDB store with the
`omniscribe-migrate-lexicon` console script (the server itself does not
auto-migrate on boot):

```bash
uv run omniscribe-migrate-lexicon --dry-run      # preview the plan
uv run omniscribe-migrate-lexicon               # run (idempotent)
uv run omniscribe-migrate-lexicon --verify-only # check the result
uv run omniscribe-migrate-lexicon --strict      # exit 2 on empty store
```

Exit codes: `0` = success (including a valid empty `lexicon.lance`
after `--verify-only`); `1` = migration error; `2` = `--strict` only —
empty live store when a backup manifest reports glossaries.

A `--verify-only` of a valid empty store is a successful verification
(it is not an error to have zero glossaries). Use `--strict` to opt
into the old "empty store = exit 2" behavior for scripted pre-deploy
checks.

### Importing Lane's Lexicon into LanceDB

To import Edward William Lane's *Arabic-English Lexicon* into the LanceDB
glossary store, use the `omniscribe-import-lanes-lexicon` console script.
The utility parses either the official SQLite snapshot or the TEI.2 XML dump
through the standard glossary ingestion pipeline:

```bash
# Preview import with dry run (safe for interactive terminals)
uv run omniscribe-import-lanes-lexicon --sqlite D:/Lanes/lexicon.sqlite --dry-run

# Import from SQLite database snapshot (~264 MB, 47,919 entries)
uv run omniscribe-import-lanes-lexicon --sqlite D:/Lanes/lexicon.sqlite

# Import from TEI.2 XML directory with entry limit
uv run omniscribe-import-lanes-lexicon --xml D:/Lanes/lexicon_xml --limit 1000

# Specify custom artifact directory (default: $OMNISCRIBE_ARTIFACT_DIR or ./omniscribe_artifacts)
uv run omniscribe-import-lanes-lexicon --sqlite D:/Lanes/lexicon.sqlite --artifact-dir /data/artifacts
```

Flag options:
- `--sqlite <path>`: Path to Lane's Lexicon SQLite database snapshot (mutually exclusive with `--xml`).
- `--xml <path-or-dir>`: Path to TEI.2 XML file or directory containing Lane's Lexicon XMLs.
- `--dry-run`: Parse and validate entries without writing to the LanceDB store.
- `--limit <N>`: Maximum number of entries to parse and ingest (useful for sanity checking).
- `--artifact-dir <path>`: Target glossary storage root (defaults to `OMNISCRIBE_ARTIFACT_DIR` or `./omniscribe_artifacts`).
- `--domain <domain>`: Glossary domain classification (defaults to `general`).

## Uninstall

```bash
# Local install
uv pip uninstall omniscribe

# Docker
docker compose down --rmi all --volumes
```

Per-run `omniscribe-ocr-*` work directories are removed upon run completion.
Preview cache files are cleaned during cache eviction or process shutdown, and
token-bound result artifacts use the configured artifact TTL and the runtime's
periodic cleanup loop.

## See Also

- [README.md](../README.md) — feature overview, install, web workspace
- [CHANGELOG.md](CHANGELOG.md) — version history and breaking changes
- [SECURITY.md](SECURITY.md) — threat model, hardening checklist,
  vulnerability disclosure
- [ARCHITECTURE.md](ARCHITECTURE.md) — component map and API
  surface
- [AGENTS.md](AGENTS.md) — contributor guide and full env-var
  reference

_Last updated: 2026-09-27_
