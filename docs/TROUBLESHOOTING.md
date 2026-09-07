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

## OCR returns nothing

The server is up, you drop a PDF, and the result PDF has no selectable
text. Or the OCR runs but every page comes back blank.

**Cause.** The VLM endpoint isn't reachable. OmniScribe returns nothing
rather than failing loud when the configured `LLM_API_BASE` doesn't
respond.

**Fix.**

1. Run `make doctor` — the "Model server" line should say `OK` and
   show how many models are loaded.
2. If `make doctor` says the model server is `WARN`:
   - **LM Studio:** open the **Developer** tab and click **Start
     Server** (default port 1234). Make sure a vision-capable model is
     loaded in the **Search** tab — not all LM Studio models support
     images.
   - **Ollama:** run `ollama serve` in another terminal; pull a vision
     model (`ollama pull llava`).
3. If the model server is OK but OCR still returns nothing, check the
   model name. The default in `.env.example` is whatever LM Studio
   reports; the `LLM_MODEL` env var (and the Settings tab in the
   Flutter client) override it.
4. See [`docs/AGENTS.md`](AGENTS.md) for the full env-var catalogue.

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
wired unconditionally in `src/omniscribe/server.py:184-202`; the
guard is in `_validate_runtime_settings` at `server.py:375-381`.

**Fix.** Either:

- **Bind to loopback** (the default): `uv run omniscribe-server
  --host 127.0.0.1 --port 8000`. No token needed.
- **Bind to a LAN address with a real token:**

  ```bash
  export OMNISCRIBE_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  uv run omniscribe-server --host 0.0.0.0 --port 8000
  ```

  The token must be **at least 32 characters**. Placeholder values
  like `changeme`, `change-me-in-prod`, or empty strings are
  rejected — see the next entry.

If you genuinely need a placeholder token (e.g. for a one-off dev
container), pass `--allow-placeholder-token` to opt out. The flag is
audited; the server prints a `WARN` log line on every request.

---

## Placeholder auth token rejected on LAN bind

```
RuntimeError: Refusing to start: --host 192.168.1.42 is non-loopback
and OMNISCRIBE_AUTH_TOKEN=change-me-in-prod is in the placeholder denylist.
Set a real 32+ char secret or bind to 127.0.0.1 / ::1 / localhost.
```

**Cause.** The token you set is in the boot-time placeholder denylist
(`server.py:39-46`). The list catches every well-known "fix-me" value
that has been copy-pasted into production configs over the years.

**Fix.** Generate a fresh token with the same one-liner as above, or
any CSPRNG:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
openssl rand -hex 32
tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32
```

The token only needs to be ≥ 32 characters; the alphabet is irrelevant.

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
4. **The state backend is misconfigured** — e.g. you set
   `OMNISCRIBE_STATE_BACKEND=redis` (deferred; the harness will
   refuse). Set it to `memory` or `sqlite`.

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

The empty default in `.env.example` is intentional — see
[Phase 0 of the remediation plan](audits/2026-09-04-remediation-plan.md#phase-0--stop-the-bleeding)
for the rationale.

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
flutter run -d windows    # or: macos / linux / chrome / web
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

```bash
# Find the data directory (defaults to $OMNISCRIBE_ARTIFACT_DIR, then
# the platform user-data dir, then ./omniscribe-data in the repo root)
uv run python -c "from omniscribe.config import load_settings; print(load_settings().artifact_base_dir)"

# Delete the SQLite database and blob directory
rm -rf "<artifact_base_dir>/omniscribe-state.db"*
rm -rf "<artifact_base_dir>/blobs"
```

The server must be **stopped** while you do this — the SQLite WAL
will be re-created on next boot.

**For the in-memory state backend:**

Restart the server. There's nothing to delete because nothing is
persisted.

**To start over with the dev defaults** (no `.env`, loopback bind,
SQLite):

```bash
unset OMNISCRIBE_STATE_BACKEND
unset OMNISCRIBE_AUTH_TOKEN
uv run omniscribe-server --host 127.0.0.1 --port 8000
```

---

## See also

- [`make doctor`](../Makefile) — runs the four-check health probe from
  the repo root. Now points you back here on failure.
- [`docs/SECURITY.md`](SECURITY.md) — full env-var reference and
  threat-model walkthrough.
- [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) — the three deployment
  profiles (loopback, LAN, public internet).
- [`docs/AGENTS.md`](AGENTS.md) — contributor guide; everything in
  this file is repeated there in more detail.
- [`README.md`](../README.md) §Before you start — if you haven't
  installed yet, that's where to begin.
- [`audits/2026-09-04-remediation-plan.md`](audits/2026-09-04-remediation-plan.md) —
  the long-form plan that produced this document.

_Last updated: 2026-09-05_
