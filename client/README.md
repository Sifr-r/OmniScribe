# OmniScribe Flutter Client

The OmniScribe Flutter client is the supported user workflow — a
multi-platform desktop / mobile UI that talks to the OmniScribe FastAPI
backend over HTTP and WebSocket. The previous in-browser workstation is
deprecated.

The rest of the OmniScribe project lives at the repository root. Start
there if you haven't already: see [`../README.md`](../README.md) for the
product overview, install, and the LM Studio / VLM prerequisite.

## Prerequisites

1. **Flutter SDK** (3.x or newer). Install from
   [docs.flutter.dev](https://docs.flutter.dev/get-started/install).
   Verify with `flutter doctor` — at minimum, the Flutter and Dart
   toolchain checks should pass.
2. **The OmniScribe FastAPI backend running locally** on
   `http://127.0.0.1:8000`. The client hard-codes this URL by default.
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
flutter run -d windows   # or: macos / linux / web / chrome
```

The first `flutter run` may take a few minutes to compile a native
binary for your platform. Subsequent runs are fast (incremental build).

## What the client does

The client exposes three primary surfaces:

- **Workstation** — drop a PDF or image, watch the OCR progress over a
  WebSocket, preview the result, export to searchable PDF, Markdown,
  plain text, or DOCX.
- **Settings** — point the client at the backend, switch the VLM
  endpoint, and toggle the Advanced Configuration processors
  (preprocessing, reading order, quality analysis, structure, sections,
  layout enrichment, table extraction, quality routing).
- **Library** — browse the glossary / translation terminology (the
  LanceDB-backed `lexicon` store, when installed) and the OCR quality
  trust layer output.

## Pointing the client at a non-default backend

The default backend URL is `http://127.0.0.1:8000`. To point at a
different host (a LAN server, a remote dev box, a tunnel), use the
**Settings** tab in the client UI.

For development, you can also override at build time:

```bash
flutter run --dart-define=OMNISCRIBE_API_BASE=http://192.168.1.42:8000
```

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
  The full cross-platform troubleshooting guide is being added under
  `docs/TROUBLESHOOTING.md` (Phase 2 of the remediation plan).

## Where the code lives

- `client/lib/main.dart` — app entry, theme, navigation.
- `client/lib/presentation/` — screens and widgets.
- `client/lib/data/` — repositories, providers, and the WebSocket
  progress channel.
- `client/test/` — widget tests, state-notifier tests, and repository
  tests. Run with `flutter test`.

## See also

- [Main `README.md`](../README.md) — product overview, install, feature
  list.
- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — full component
  map, including the Flutter client's role in the request flow.
- [`../docs/AGENTS.md`](../docs/AGENTS.md) — contributor guide and full
  env-var reference.
