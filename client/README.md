# OmniScribe Flutter Client

The OmniScribe Flutter client is the supported user workflow — targeting
**Windows desktop** and **web** (Chrome / web-server) — that talks to the
OmniScribe FastAPI backend over HTTP and WebSocket. The previous in-browser
workstation is deprecated.

The rest of the OmniScribe project lives at the repository root. Start
there if you haven't already: see [`../README.md`](../README.md) for the
product overview, install, and the LM Studio / VLM prerequisite.

## Prerequisites

1. **Flutter SDK** (3.x or newer). Install from
   [docs.flutter.dev](https://docs.flutter.dev/get-started/install).
   Verify with `flutter doctor` — at minimum, the Flutter and Dart
   toolchain checks should pass.
2. **The OmniScribe FastAPI backend running locally** on
   `http://127.0.0.1:8000`. The client uses this URL by default and lets
   you change it in Settings for the current session.
3. **A local VLM endpoint** (LM Studio / Ollama) running on
   `http://127.0.0.1:1234/v1` (or whichever address is exposed in
   Docker via `host.docker.internal:1234`).

## Install & run

```bash
# 1. Start the backend in one terminal (from the repo root)
uv sync --extra web --extra preprocessing
uv run omniscribe-server --port 8000

# 2. Start the Flutter client in another terminal
cd client
flutter pub get
flutter run -d windows   # or: flutter run -d chrome / web-server
```

The first `flutter run` may take a few minutes to compile a native
binary for your platform. Subsequent runs are fast (incremental build).

## What the client does

The client exposes seven primary tabs:

- **Workstation** — drag-and-drop or file picker for PDFs and images,
  interactive document viewport with bounding-box inspection, live
  WebSocket streaming progress with block retry telemetry, document page
  selection, and export to searchable sandwich PDF, GFM Markdown, plain
  text, or DOCX.
- **Translation** — tree-aware, asynchronous document translation backed
  by optional glossary terminology grounding.
- **Transcription** — audio and speech-to-text transcription for meeting
  notes and media, with plain text and SRT subtitle exports.
- **Extraction** — structured JSON schema extraction powered by built-in
  templates (`invoice`, `resume`, `academic`, `table`, `table_extraction`,
  `custom`) or bespoke prompts.
- **Glossary** — browse, search, and import domain terminology libraries
  into the LanceDB lexicon backend from diverse sources (SQLite, TEI XML,
  CSV/TSV, XLSX, XLIFF/TBX/TMX, SQL pair tables, Git repositories).
- **Job History** — inspect, cancel, and monitor background asynchronous
  jobs, with direct result download.
- **Settings** — configure backend host URL, bearer authentication token,
  VLM provider endpoints, and toggle Advanced Configuration processors
  (preprocessing, reading order, quality analysis, structure, sections,
  layout enrichment, table extraction, quality routing).

## Resilience & Operational Guarantees

- **Local Cancellation Guarantee**: When cancelling an active OCR run
  (`cancelOcr`), the client guarantees local cancellation. It immediately
  resets orchestration state (`isProcessing: false`, stage `'Cancelled'`),
  cancels status poll timers, and tears down WebSocket progress channels.
  Even if the remote server cancellation call fails or times out, the
  client attributes warnings (e.g. `'Failed to cancel active OCR job/channel on server'`
  or `'Partial server cancellation failure'`) to state rather than leaving
  the UI locked in a processing state.
- **Bounded Polling Failure Retry**: Background job synchronization
  implements exponential backoff (2 s to 10 s clamp) on network failures.
  Polling retry is strictly bounded: if consecutive errors exceed the
  threshold (`_maxConsecutiveStatusFailures`), polling terminates cleanly
  and transitions the job to an explicit error state with descriptive
  diagnostics instead of looping indefinitely.
- **Server URL Validation & Feedback**: Server base URL overrides entered
  in the Settings tab undergo strict format and URI validation. Invalid
  inputs trigger immediate, floating SnackBar notifications displaying
  the exact validation message, alerting the user before invalid network
  requests are dispatched.

## Pointing the client at a non-default backend

The default backend URL is `http://127.0.0.1:8000`. To point at a
different host (a LAN server, a remote dev box, a tunnel), use the
**Settings** tab in the client UI. The override is session-only. If an
invalid URL is supplied, the client rejects the change and presents a
descriptive SnackBar notification.

## Troubleshooting

- **"Connection refused" on the Workstation tab** — the backend isn't
  running. Check the first terminal; `uv run omniscribe-server` should
  print a "Uvicorn running on" line.
- **"OCR returns nothing"** — the backend is up but your VLM endpoint
  isn't reachable. See [the main README's "Before you start" section](../README.md#before-you-start).
- **"Authentication required" banner** — the backend armed
  `OMNISCRIBE_AUTH_TOKEN` and the client isn't sending it, or is sending
  the wrong one. Enter it under **Settings → General & Server → OmniScribe
  Backend Connection → Bearer Token** and press "Apply token". It is held
  for the session only, so re-enter it after a restart. The default
  loopback profile needs no token at all — if you've moved off loopback
  without setting a real 32+ char secret, the server refuses to start.
  See [`../docs/SECURITY.md`](../docs/SECURITY.md).
- **Anything else** — run `make doctor` from the repo root. It checks
  Python, `uv`, Redis reachability, and VLM reachability in one pass.
  See the full [troubleshooting guide](../docs/TROUBLESHOOTING.md).

## Where the code lives

- `client/lib/main.dart` and `client/lib/app/` — bootstrap, theme selection,
  navigation, health and shell composition.
- `client/lib/features/` — feature-owned models, state, API adapters and UI
  for workstation, settings, providers, jobs, translation, transcription,
  glossary and documents.
- `client/lib/shared/` — reusable widgets and transport/auth provider wiring.
- `client/lib/core/` — HTTP/WebSocket transport, strict wire-field decoding,
  constants, enums and theme.
- `client/lib/data/` — import-only compatibility exports used by existing
  cross-feature tests; production callers import each feature directly.
- `client/test/` — widget tests, state-notifier tests, and repository
  tests. Run with `flutter test`.
- `client/tool/` — standalone feature contract and source-layout checks.

## Real-server integration verification

The desktop integration test starts its own `uv run omniscribe-server` on a
free loopback port. From `client/`, run the no-VLM default:

```bash
flutter test integration_test/app_real_server_test.dart -d windows
```

This checks sample staging and real preview rendering. It skips if local
Python/server tooling is unavailable. The child uses memory state and
in-process jobs, with owned artifact/spool/temp files beneath `.agent-tmp/`
in the repository. Windows teardown terminates the test-owned process tree
before removing runtime files; failed or timed-out termination retains those
files and fails the test. If the launcher has already exited (including failed
startup), teardown refuses to target its potentially reused PID, retains files,
and reports the cleanup failure even in the default no-VLM test. Other desktop
platforms use direct child termination;
the documented Windows target provides process-tree cleanup. For
model-backed OCR, load a vision
model on your OpenAI-compatible endpoint, install the backend dependencies
(`uv sync --extra web --extra preprocessing` from the repository root), and
configure the existing server environment or root `.env`:

```dotenv
LLM_API_BASE=http://127.0.0.1:1234/v1
LLM_MODEL=allenai/olmocr-2-7b
LLM_API_KEY=lm-studio
```

The existing `OMNISCRIBE_LLM_API_BASE`, `OMNISCRIBE_LLM_MODEL`, and
`OMNISCRIBE_LLM_API_KEY` aliases also work. `LLM_MODEL` must match a loaded
vision model. The test fetches `/api/config` and sends that endpoint/model
through the normal workstation orchestration; the backend uses its configured
VLM key, including masked keys. If backend authentication is enabled, export
`OMNISCRIBE_AUTH_TOKEN` into the shell running Flutter so the spawned server
and the client session receive the same token (a root `.env` token alone is
insufficient for the client).

```bash
flutter test integration_test/app_real_server_test.dart -d windows --dart-define=OMNISCRIBE_LIVE_OCR=true
```

The opt-in run fails on missing tooling, server startup, invalid configuration,
unreachable model, failed OCR, or missing recognized content/output. It stages
the fixture's first-page raster preview as PNG, preventing an embedded PDF text
layer from satisfying the check, and requires recognized `computer science`
text, terminal `Complete` state, and a returned PDF that the real server can
render. The OCR request has a
five-minute limit and the whole test a ten-minute limit; startup is bounded to
120 seconds. This is one real hybrid OCR pass with quality repair disabled,
not an accuracy benchmark or Redis/async-worker check. It requires a desktop
target because it starts a local process; the default still makes no VLM call.

## See also

- [Main `README.md`](../README.md) — product overview, install, feature
  list.
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — full component
  map, including the Flutter client's role in the request flow.
- [`../docs/AGENTS.md`](../docs/AGENTS.md) — contributor guide and full
  env-var reference.

_Last updated: 2026-09-30_
