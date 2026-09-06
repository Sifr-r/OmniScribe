# Deployment Guide

This document walks through deploying OmniScribe in three common
profiles. **Start at the top, stop at the profile that matches your
use case.** The local-desktop default is correct for almost every
user; only step up to LAN / public-internet when you actually need
to.

> **v0.2.0 install path (2026-09-05):** the supported end-user install
> is the **source install** (Profile 1 below). The single-binary
> Windows distribution per
> [RFC 001 — End-User Install Path](rfcs/2026-09-end-user-install.md)
> Option A is **deferred to v0.3+** (blocked on a PyInstaller + anyio
> bundling issue; see
> [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
> §"Known build issue"). The bundle infrastructure
> (`omniscribe_server.spec`, `scripts/build_windows.py`,
> `scripts/run_server.py`) is kept in tree for the next maintainer
> to pick up. The v0.2.0 user-facing improvement is the Phase 2
> first-run affordances — `docs/TROUBLESHOOTING.md` (13 sections)
> and `make doctor` remediation hints — not the install steps
> themselves.

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
flutter run -d windows   # or: macos / linux
```

That's it for now. No auth, no reverse proxy, no Docker. The
Settings tab in the Flutter client points at `http://localhost:1234/v1`
(LM Studio) by default; start LM Studio, load a model, OCR.

**What you get:**

- All guards on (rate limit, upload cap, SSRF, placeholder-token
  rejection) but **no** bearer-token auth. Any process that can
  reach `localhost:8000` is trusted.
- Documents stay on disk until cleaned up by the startup sweep
  (`M6`).
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

- `OMNISCRIBE_AUTH_TOKEN` gates every HTTP route except the liveness probes (`/api/health`, `/health`, `/healthz`, `/ready`, `/readyz`). The middleware is wired unconditionally in `src/omniscribe/server.py:184-202`; placeholder tokens are rejected on non-loopback binds with a clear `SystemExit`. The WebSocket handshake uses per-channel session tokens on top of the same bearer.
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

### docker-compose.yml

```yaml
services:
  api:
    image: ghcr.io/sifr-r/omniscribe:latest
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
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/health"]
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

The placeholder-check (M10) refuses to start if the value is the
example `change-me-in-prod` or any other known placeholder.

### Token scope (what is actually enforced)

There is a **single enforced bearer token**: `OMNISCRIBE_AUTH_TOKEN`.
The `BearerAuthMiddleware` wired in `server.py` checks it (constant-time
compare) on every route it guards; per-route token scoping does not
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
the compose stack is `api` + `redis` only. Redis stays in the stack for
the `REDIS_URL` env-var contract (the api service still exports it; the
Redis state backend that would consume it remains deferred in the
harness rebuild).

```bash
uv sync --extra web --extra preprocessing --extra async-translation
docker compose up -d   # api + redis
```

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

The settings tab persists user preferences via `localStorage`, not
server-side state. A version upgrade does not lose user settings.

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

## Uninstall

```bash
# Local install
uv pip uninstall omniscribe

# Docker
docker compose down --rmi all --volumes
```

Job artifacts in `/tmp/ocr_*` are removed by the startup sweep
(M6); manual cleanup is rarely needed.

## See Also

- [README.md](README.md) — feature overview, install, web workspace
- [CHANGELOG.md](CHANGELOG.md) — version history and breaking changes
- [SECURITY.md](SECURITY.md) — threat model, hardening checklist,
  vulnerability disclosure
- [ARCHITECTURE.md](ARCHITECTURE.md) — component map and API
  surface
- [AGENTS.md](AGENTS.md) — contributor guide and full env-var
  reference

_Last updated: 2026-09-05_