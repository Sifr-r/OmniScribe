# OmniScribe — Troubleshooting

A field guide for the first-run errors and "I changed one thing and now
nothing works" cases that don't deserve a stack trace. Each entry
answers **what's happening**, **why**, and **how to fix it** — usually
in under a minute of reading.

If you don't find your error here, run `make doctor` from the repo
root — it checks Python, `uv`, Redis reachability, and VLM
reachability in one pass. Many failures in this guide are exactly the
ones `make doctor` flags.

> **Reading order:** start at the top if you've never run OmniScribe
> before. Jump to the matching section if you've already used it once
> and a specific thing is broken. The "I want a fresh state" entry at
> the bottom is the one most people wish they'd found earlier.

---

## OCR returns nothing (VLM preflight failure)

The server is up, you drop a PDF, and the result PDF has no selectable
text. Or the OCR runs but every page comes back blank, or you get an immediate failure.

**Cause.** The pipeline runs a strict VLM preflight check before processing document pages.
It queries the VLM endpoint (`GET /v1/models` at `LLM_API_BASE`) to ensure the server is
reachable, the configured `LLM_MODEL` is loaded and advertised, and the model supports vision.
If the endpoint is unreachable or the model is not found, the preflight check fails immediately,
returning a typed error or failing the async job before any rendering begins.

**Fix.**

1. Run `make doctor` — the "Model server" line should say `OK` and
   show how many models are loaded.
2. If `make doctor` says the model server is `WARN` or preflight fails:
   - **LM Studio:** open the **Developer** tab and click **Start
     Server** (default port 1234). Make sure a vision-capable model is
     loaded in the **Search** tab — not all LM Studio models support
     images (e.g. use `allenai/olmocr-2-7b` or `qwen2.5-vl-7b`).
   - **Ollama:** run `ollama serve` in another terminal; pull a vision
     model (`ollama pull llava` or `ollama pull qwen2.5-vl`).
3. If the model server is OK but OCR still fails, check the model name.
   `.env.example` currently seeds `allenai/olmocr-2-7b`; `LLM_MODEL`
   (and the Settings tab in the Flutter client) can override it. The value
   must match an ID returned by the endpoint's `/v1/models` response.
4. If connecting to a remote or cloud VLM endpoint (OpenAI, Anthropic, Groq),
   ensure `OMNISCRIBE_LLM_API_BASE`, `OMNISCRIBE_LLM_API_KEY`, and
   `OMNISCRIBE_LLM_MODEL` are set correctly, and that network/firewall allows
   outbound connections (with `ALLOW_SSRF_LOCAL=false` blocking private IPs).
5. See [`docs/AGENTS.md`](AGENTS.md) for the full env-var catalogue.

---

## Server won't start: non-loopback bind requires a real auth token

```
SystemExit: Refusing to start: --host 0.0.0.0 is non-loopback and
OMNISCRIBE_AUTH_TOKEN is unset. Set OMNISCRIBE_AUTH_TOKEN (32+ chars)
or bind to 127.0.0.1 / ::1 / localhost. See SECURITY.md.
```

**Cause.** OmniScribe refuses to bind a non-loopback address (anything
other than `127.0.0.1`, `::1`, or `localhost`) without a real
`OMNISCRIBE_AUTH_TOKEN`. This is by design — the auth middleware is
wired unconditionally in `src/omniscribe/server.py:240-256`; the
guard is in `_validate_runtime_settings` at `server.py:485+`.

**Fix.** Either:

- **Bind to loopback** (the default): `uv run omniscribe-server
  --host 127.0.0.1 --port 8000`. No token needed.
- **Bind to a LAN address with a real token:**

  ```bash
  export OMNISCRIBE_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  uv run omniscribe-server --host 0.0.0.0 --port 8000
  ```

  Use a **32+ character random token**. That length is the security
  recommendation; the current runtime checks presence and known placeholder
  values rather than enforcing a minimum length. Placeholder values
  like `changeme`, `change-me-in-prod`, or empty strings are
  rejected — see the next entry.

If you genuinely need a placeholder token (e.g. for a one-off dev
container), pass `--allow-placeholder-token` to opt out. Do not use this
override on a shared or public network.

---

## Placeholder auth token rejected on LAN bind

```
SystemExit: Refusing to start: OMNISCRIBE_AUTH_TOKEN is a known
placeholder value. Replace it with a random secret or pass
--allow-placeholder-token if you understand the risk.
```

**Cause.** The token you set is in the boot-time placeholder denylist
(`_PLACEHOLDER_AUTH_TOKENS` at `server.py:68-75`). The list catches every well-known "fix-me" value
that has been copy-pasted into production configs over the years.

**Fix.** Generate a fresh token with the same one-liner as above, or
any CSPRNG:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
openssl rand -hex 32
tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32
```

Use at least 32 random characters; this is a recommendation, not a current
runtime length check.

---

## WebSocket connection rejected with close code 4401

```
WebSocket closed: code 4401 (unauthorized)
```

**Cause.** When `OMNISCRIBE_AUTH_TOKEN` is set, `BearerAuthMiddleware` gates both
HTTP endpoints and WebSocket connections (such as `/api/progress/ws/{channel_id}`).
If a client connects to the WebSocket endpoint without providing a valid bearer token,
the handshake is rejected immediately with WebSocket close code `4401`.

**Fix.** Ensure the client passes the bearer token during the WebSocket handshake via:
1. An HTTP `Authorization: Bearer <token>` header (for desktop/native clients).
2. A URL query parameter: `?auth_token=<token>` or `?token=<token>` (for browser
   WebSocket clients that cannot set custom handshake headers).

In the Flutter client, enter your bearer token under **Settings** → **Backend Bearer Token**.

---

## Server boots, but `uv run omniscribe-server` exits immediately

**Cause.** Almost always one of:

1. **`OMNISCRIBE_AUTH_TOKEN` is set to a placeholder** and the bind
   host is non-loopback. See the previous entry.
2. **The `OMNISCRIBE_CORDIS_CONFIG` path doesn't exist** — the
   harness can't load `src/omniscribe/resources/cordis.yml`.
3. **A patch file references a plugin that doesn't exist** — the
   harness rolls back partial registrations and exits with a clear
   `PluginLoadError` (see `src/omniscribe/harness/context.py:204-221`).
4. **The state backend is misconfigured** — supported values are `memory`,
   `sqlite`, and `redis`. Redis also requires a reachable, correctly
   authenticated `REDIS_URL`.

**Fix.** Re-run with `--log-level debug` for a stack trace:

```bash
uv run omniscribe-server --port 8000 --log-level debug
```

The most common exit messages are documented in
[`docs/SECURITY.md`](SECURITY.md) and
[`docs/AGENTS.md`](AGENTS.md).

---

## `uv` is not recognized

```
'uv' is not recognized as an internal or external command,
operable program or batch file.
```

**Cause.** The `uv` package manager (used for all install / sync /
`uv run` invocations in this project) isn't on `PATH`. This is a
Windows / PowerShell error message; the macOS / Linux variant is
`command not found: uv`.

**Fix.** Install `uv` per the [official one-liner](https://docs.astral.sh/uv/getting-started/installation/):

- **macOS / Linux:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Windows (PowerShell):** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **Homebrew:** `brew install uv`
- **pipx:** `pipx install uv`

After install, restart your terminal and verify with `uv --version`.

## Python 3.11+ is not installed

```
ERROR Python: 3.10.12 (requires 3.11+)
```

**Cause.** The `pyproject.toml` declares `requires-python = ">=3.11"`.
Older Python versions will fail the install step (`uv sync`) or
runtime type checks.

**Fix.** Install Python 3.11 or newer:

- **macOS:** `brew install python@3.12` (or `pyenv install 3.12 && pyenv global 3.12`)
- **Linux:** use your distro's package manager, `pyenv`, or the
  [official installer](https://www.python.org/downloads/).
- **Windows:** the [official installer](https://www.python.org/downloads/)
  or `winget install Python.Python.3.12`.

If you have multiple Python versions installed, `uv` will pick the
right one automatically as long as 3.11+ is on `PATH` and discoverable
by `py --list` / `python3 --version`.

## `make doctor` says Redis is unreachable, but I don't use Redis

```
WARN Redis: unavailable at localhost:6379 ([Errno 111] Connection refused)
```

**Cause.** `make doctor` always probes Redis on `localhost:6379`
because the previous default was in-memory and the async-translation
extra can use Redis as a job broker. **Since 2026-09-05, OmniScribe
defaults to the SQLite state backend** (see the [Backup & Recovery
section in `DEPLOYMENT.md`](DEPLOYMENT.md#backup--recovery) for the
new contract), so Redis is genuinely optional. The `WARN` is a
historical probe, not a regression.

**Fix.**

- **You don't use Redis:** ignore the `WARN`. SQLite is doing the
  right thing.
- **You do use Redis** (e.g. for the async-translation JobQueue on
  distributed deployments): start a Redis server. The fastest local
  path is `docker run -p 6379:6379 redis:7-alpine`. Compose ships
  one out of the box; `docker compose up` brings it up alongside the
  API.

The `WARN` will turn into `OK` once Redis is reachable. The probe
is `WARN` not `ERROR` precisely so this case doesn't fail the doctor
gate.

---

## Redis multi-worker: `omniscribe-worker` cannot claim jobs or encounters spool errors

```
ValueError: Invalid or unsafe input_path in OCR payload
# or
ValueError: OCR payload input_path does not match claimed job
# or
Worker heartbeats timing out / jobs requeued repeatedly
```

**Cause.** Multi-worker execution uses Redis for centralized state
(`OMNISCRIBE_STATE_BACKEND=redis`) and job dispatch (`OMNISCRIBE_JOBS_MODE=redis`),
running standalone worker processes via `omniscribe-worker --concurrency 4`.
Common failures:
1. **Spool containment violation:** `src/omniscribe/plugins/jobs_redis.py` enforces
   strict spool containment. The job payload's `input_path` must reside strictly beneath
   a trusted spool directory (`OMNISCRIBE_SPOOL_DIR` or `OMNISCRIBE_ARTIFACT_DIR`) and
   must match the authoritative path recorded in `JobRecord.input_path`. If worker nodes
   run on separate hosts without a shared spool mount, or if the spool path differs between
   server and worker, input validation fails closed with `ValueError`.
2. **Backend mode mismatch:** The API server or worker is missing
   `OMNISCRIBE_STATE_BACKEND=redis` or `OMNISCRIBE_JOBS_MODE=redis`. Both the server
   and every worker process must point to the identical `REDIS_URL` and have both
   backend settings configured.
3. **Lease visibility timeout expiration:** If processing a complex or high-page-count PDF
   takes longer than `--visibility-timeout` (default: 300s) and worker heartbeats stall,
   another worker loop will assume the job was abandoned and claim it.

**Fix.**
1. Ensure API server and workers share the same spool mount or identical
   `OMNISCRIBE_SPOOL_DIR` / `OMNISCRIBE_ARTIFACT_DIR` path.
2. Confirm both server and worker environments have `OMNISCRIBE_STATE_BACKEND=redis` and
   `OMNISCRIBE_JOBS_MODE=redis` exported.
3. Increase the visibility timeout when processing large documents:
   `uv run omniscribe-worker --concurrency 4 --visibility-timeout 600`.
4. Run workers with `--log-level debug` to inspect claim, lease renewal, and heartbeat
   traces in detail.

---

## Defender quarantined `arrow_substrait.dll`

```
Threat detected: Trojan:Win32/Wacatac.B!ml
File: C:\Users\...\omniscribe\.venv\Lib\site-packages\pyarrow\arrow_substrait.dll
```

**Cause.** This is a well-known Windows Defender false positive on the
Apache Arrow native binary that ships with `lancedb` (the optional
`[lexicon]` extra). The DLL is unmodified and signed by the Apache
Arrow maintainers; the heuristic that triggers is over-broad.

**Fix.** Three options, in order of preference — full context in
[`docs/SECURITY.md`](SECURITY.md) §Platform Notes:

1. **Update Defender** (Windows Security → Virus & threat protection →
   Check for updates). The false-positive signature is usually
   re-classified within days. Reinstall `omniscribe[lexicon]` after.
2. **Add a folder exclusion** for the venv site-packages directory
   containing `arrow_substrait.dll` (typical path
   `.venv\Lib\site-packages\pyarrow\`). Scoped to one file; no
   broader weakening.
3. **Run in Docker** (`docker compose up`) — the multi-stage
   `Dockerfile` builds on a clean Debian base, so the host-side
   heuristic doesn't fire.

If you genuinely believe the DLL is malicious, **do not** exclude it.
Report upstream to [apache/arrow](https://github.com/apache/arrow/issues).

---

## Flutter not on PATH

```
'flutter' is not recognized as an internal or external command,
operable program or batch file.
```

**Cause.** The Flutter SDK is installed but not on `PATH`. The
OmniScribe backend runs fine without Flutter; only the desktop /
mobile client needs it.

**Fix.** Install Flutter from
[docs.flutter.dev/get-started/install](https://docs.flutter.dev/get-started/install).
Add `<flutter-sdk>/bin` to your `PATH` (Windows: System
Environment Variables; macOS / Linux: `~/.zshrc` or `~/.bashrc`).
Verify with:

```bash
flutter doctor
```

At minimum, the **Flutter** and **Dart** toolchain lines should
report `OK`. Android / iOS / Chrome lines are optional depending on
which target you want to build for.

If you don't need the Flutter client, you can use the Python API
directly (see [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)) and skip this
section.

---

## Compose refuses to start: `REDIS_PASSWORD` ... variable empty or unset

```
ERROR: The Compose file './compose.yaml' is invalid because:
services.api.environment.REDIS_URL: unmatched '?' in substitution;
REDIS_PASSWORD must be set in .env (see .env.example)
```

**Cause.** `compose.yaml` uses Compose's `:?` substitution on
`REDIS_PASSWORD` in three places: the `REDIS_URL` env var, the
`--requirepass` flag on the `redis-server` command, and the
`redis-cli` healthcheck. If `REDIS_PASSWORD` is missing or empty in
your `.env`, Compose refuses to start the stack — by design, so a
misconfigured stack can never come up with an empty or known
password.

**Fix.** Generate a real password and put it in `.env`:

```bash
tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32
# or
openssl rand -hex 32
```

Paste the result into `.env` (gitignored) as `REDIS_PASSWORD=<value>`.
Then `docker compose up` will pick it up via Docker's automatic `.env`
loading.

The empty default in `.env.example` is intentional (security hardening
from the 2026-09-04 five-lens audit).

---

## Async translation result is gone after restart

**Cause.** OmniScribe's default state backend is now **SQLite**
(changed in the September 2026 remediation). The default used to be
in-memory (`MemoryStateBackend`), which loses all job records and
artifacts on every restart. If you explicitly opted into
`OMNISCRIBE_STATE_BACKEND=memory`, you keep the old behaviour and
this entry is yours.

**Fix.** Three options:

1. **Accept the new default.** Remove the `OMNISCRIBE_STATE_BACKEND=memory`
   line from your `.env` / shell. SQLite writes to
   `<artifact_dir>/omniscribe-state.db` (WAL mode) and persists
   across restarts.
2. **Re-declare the in-memory default** by adding
   `OMNISCRIBE_STATE_BACKEND=memory` to your environment. The server
   will print a loud `WARN` log line at boot to remind you that
   results are ephemeral.
3. **Migrate to SQLite cleanly** by unsetting
   `OMNISCRIBE_STATE_BACKEND`. Existing in-memory state is lost (it
   was process-local anyway); new state from this boot onward
   persists.

See the [Backup & Recovery section in `docs/DEPLOYMENT.md`](DEPLOYMENT.md#backup--recovery)
for the SQLite layout, WAL mode, and backup strategy.

---

## "I just installed this — does it work?" (no PDF handy)

The Workstation screen has a **Try sample PDF** button in the
empty-state header (visible when no document is loaded). Clicking
it fetches a canonical fixture PDF from the server's
`/api/sample-pdf/{name}` route and stages it as the active
document; the existing **Run OCR** button then processes it. This
is the U12 affordance — a new user has no PDF of their own to
upload, and the sample removes that friction.

If the button does nothing or shows an error:

1. **The server isn't running yet.** The Flutter client connects
   to the same backend you started the server on (default
   `http://127.0.0.1:8000`). Check that the backend boot log
   shows `Uvicorn running on http://127.0.0.1:8000` or that the
   binary console window is open.
2. **The server is on a non-loopback host.** The sample-PDF
   route is path-prefix-exempt in
   `middleware/auth.py` (so a Profile 1 loopback Flutter client
   has no token to send). For Profile 2/3, the route is also
   open; the fixtures are public-domain test assets.
3. **The bundle doesn't include the fixtures.** The PyInstaller
   `DATAS` block copies `src/omniscribe/resources/` wholesale
   into the bundle, so `src/omniscribe/resources/sample_pdfs/`
   ships automatically. If you built the bundle from a working
   tree missing that directory, the route will return 500 with
   "sample PDF 'X' is in the allowlist but missing on disk" — see
   the bundle's boot log.
4. **You get a 404.** The server-side allowlist
   (`ALLOWED_SAMPLE_PDFS` in
   `omniscribe/plugins/sample_pdfs.py`) is the only accepted
   name set; the Flutter UI lists them in
   `SamplePdfRepository.availableFixtures`. Adding a new fixture
   requires updating both sides.

The same five canonical fixtures (`digital.pdf`,
`handwritten.pdf`, `hybrid.pdf`, `dense.pdf`, `notes.pdf`) are
also used by `tests/fixtures/pdfs/` for the dev / test path.

---

## Open the browser, see a "5-line placeholder page"

```
OmniScribe API server is running.
The interactive client ships as a Flutter desktop application under `client/`.
```

**Cause.** The in-browser workstation that older versions of
OmniScribe shipped was deprecated in the Wave 14 cleanup. The page at
`http://127.0.0.1:8000/` is a static landing page pointing you at the
Flutter client.

**Fix.** Use the Flutter client:

```bash
# Terminal A — backend
uv run omniscribe-server --port 8000

# Terminal B — Flutter client
cd client
flutter pub get
flutter run -d windows    # committed desktop target; use -d chrome for web
```

Full install + connect instructions are in
[`client/README.md`](../client/README.md). The Flutter client hard-codes
`http://127.0.0.1:8000` by default; if your backend is on a different
host, point the client at it via the **Settings** tab.

---

## I want a fresh state

Sometimes you just want to nuke everything and start over — to clear a
stuck job, reclaim disk, or reproduce a bug from a clean slate.

**For the SQLite state backend (the default):**

First resolve and inspect the exact data directory:

```bash
uv run python -c "from omniscribe.config import load_settings; s=load_settings(); print(s.artifact_base_dir); print(s.artifact_base_dir / 'omniscribe-state.db')"
```

When `OMNISCRIBE_ARTIFACT_DIR` is unset, `artifact_base_dir` is the operating
system temporary directory. Stop the server and back up that directory before
changing it.
1. Delete the SQLite database files: `omniscribe-state.db`, `omniscribe-state.db-wal`,
   and `omniscribe-state.db-shm`.
2. Delete artifact blobs: these are individual `<artifact_id>.bin` files directly in
   the same directory, not in a `blobs/` subdirectory. Verify each exact file belongs
   to OmniScribe before removing it. Do not recursively delete the entire directory if
   it points to the shared system temp folder.
3. Clean up scratch files: per-run `omniscribe-ocr-*` work directories are removed
   automatically upon run completion, preview cache is cleaned during cache eviction or
   process shutdown, and token-bound result artifacts use the configured artifact TTL.
   If a previous process was killed abruptly with `kill -9` or Task Manager, search for
   and remove orphaned `omniscribe-ocr-*` folders in your temporary directory.

**For the Redis state backend (multi-worker / distributed):**

1. Stop the API server and all standalone `omniscribe-worker` processes.
2. Flush OmniScribe keys in Redis:
   ```bash
   redis-cli --scan --pattern "omniscribe:*" | xargs redis-cli del
   # or on a dedicated Redis database:
   redis-cli FLUSHDB
   ```
3. Purge staged files in the trusted spool directory (`OMNISCRIBE_SPOOL_DIR` or
   `OMNISCRIBE_ARTIFACT_DIR`).

**For the in-memory state backend:**

Restart the server. There's nothing to delete because nothing is
persisted across process lifetimes.

**To start over with the dev defaults** (no `.env`, loopback bind, SQLite):
Rename the project `.env` to a recoverable backup and clear any shell-level
OmniScribe overrides before starting
`uv run omniscribe-server --host 127.0.0.1 --port 8000`. Merely unsetting two
shell variables is insufficient because `RuntimeSettings` automatically loads
the project's `.env` file.

---

## See also

- [`make doctor`](../Makefile) — runs the four-check health probe from
  the repo root. Now points you back here on failure.
- [`docs/SECURITY.md`](SECURITY.md) — full env-var reference, threat-model
  walkthrough, and security middleware contracts.
- [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) — the four deployment
  profiles (loopback, LAN, public internet, and multi-worker Redis cluster).
- [`docs/AGENTS.md`](AGENTS.md) — contributor guide; everything in
  this file is repeated there in more detail.
- [`README.md`](../README.md) §Before you start — if you haven't
  installed yet, that's where to begin.
- [`audits/COMPREHENSIVE-AUDIT.md`](audits/COMPREHENSIVE-AUDIT.md) —
  historical audit snapshot; predecessor reports remain in Git history.

_Last updated: 2026-09-27_
