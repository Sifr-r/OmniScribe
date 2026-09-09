# OmniScribe on Windows — the bundled binary

> **Status (2026-09-06): v0.3.0 SHIPS the single-binary Windows
> distribution.** Sprint 1 of the v0.3.0 RFC 002 (preserved in git
> history) identified the bundling failure as a local spec
> misclassification, not an upstream PyInstaller bug. The fix was four
> lines in `omniscribe_server.spec` + one force-import in
> `scripts/run_server.py`; the full 307 MB bundle now boots, serves
> `/api/health -> 200`, `/api/jobs -> 200 []`, and `/openapi.json -> 200`
> (45 KB) on a Windows 11 dev box. See the
> [Sprint 1 findings doc](../rfcs/2026-09-bundle-sprint-1-findings.md)
> for the full root-cause analysis. v0.2.0 shipped the **source
> install** as the supported end-user path; v0.3.0 adds the bundle
> on top.

## What you get

`omniscribe-server-windows-x.y.z.exe` — a single-file binary that
embeds Python 3.12, FastAPI, uvicorn, surya-ocr, torch, pymupdf, and
the rest of the runtime stack. No Python install. No `uv`. No
`PATH` wrangling. You download one file, double-click it, and a
console window appears with the server log.

> **Codesigning is intentionally out of scope for v0.3.0.** SmartScreen
> will show an "Unknown publisher" warning the first time you run
> the binary. Click **More info** → **Run anyway** to proceed. The
> warning is not a malware flag; it's a side effect of not paying
> for a $200–500/year codesigning cert. The full RFC discussion is
> in `docs/rfcs/2026-09-end-user-install.md` §"Open questions."

## Install (the 3-step path)

1. **Download** `omniscribe-server-windows-x.y.z.exe` from the
   [latest GitHub release](https://github.com/Sifr-r/OmniScribe/releases/latest).
   Drop it anywhere — `Desktop\OmniScribe\` is the convention.

2. **Start LM Studio** (or your preferred OpenAI-compatible VLM
   server). Load a vision model. Start its local server on
   `http://localhost:1234/v1`. The
   [main README §Before you start](../../README.md#before-you-start)
   has the model recommendations.

3. **Run the binary.** Double-click `omniscribe-server-windows.exe`
   (or run it from a terminal). A console window appears with the
   server log. You should see, within ~5 seconds:

   ```
   omniscribe state_backend=sqlite
   INFO     Loaded application state from ...
   INFO     Uvicorn running on http://127.0.0.1:8000
   ```

   The server is now listening on `http://127.0.0.1:8000`. Visit
   `http://127.0.0.1:8000/api/health` in a browser to confirm.

4. **Run the Flutter client** (separate download from the same
   release page). The client connects to `http://127.0.0.1:8000` by
   default. Drag a PDF onto the Workstation tab; OCR runs against
   the VLM endpoint you started in step 2.

That's it. No `git clone`, no `uv sync`, no Flutter SDK, no Python
on `PATH`.

## What the binary contains

The PyInstaller onefile bundle is roughly **307 MB** (v0.3.0) and
includes:

- Python 3.12 runtime
- The full `omniscribe` package (server + plugin harness + 14
  plugins)
- All runtime dependencies: `torch`, `torchvision`, `surya-ocr`,
  `pymupdf`, `pydantic`, `fastapi`, `uvicorn`, `httpx`, `redis`,
  `pyspellchecker`, `python-docx`, `defusedxml`, `numpy`
- The runtime data files at `src/omniscribe/resources/` —
  `cordis.yml` (the plugin tree) and the bundled dictionaries

The first run takes ~5–10 seconds to extract the onefile archive
to a temp dir. Subsequent runs are ~1 second to start.

## What is NOT in the binary

- **A vision model.** You must bring your own. LM Studio (free,
  ~250 MB), Ollama, or any OpenAI-compatible endpoint.
- **The Flutter client.** It's a separate download. The Flutter
  build pipeline is independent of the Python one.
- **Your documents.** All state lives in `%LOCALAPPDATA%\omniscribe\`
  (or wherever `OMNISCRIBE_ARTIFACT_DIR` points). The SQLite
  state backend is the default since 2026-09-05.

## What changed from the source install

If you used to install from source with `uv sync`, the binary
behaves identically:

- Same env-var contract (`OMNISCRIBE_AUTH_TOKEN`,
  `OMNISCRIBE_STATE_BACKEND`, `LLM_API_BASE`, etc.)
- Same plugin tree, same default state backend (SQLite)
- Same security model: loopback bind is open, non-loopback bind
  requires a real `OMNISCRIBE_AUTH_TOKEN`

The only differences are packaging, not behavior. You can
interchange the binary and the source install — they read the
same `.env` and write to the same SQLite file.

## SmartScreen: how to run anyway

The first time you double-click the binary, Windows SmartScreen
shows:

> Windows protected your PC
> Microsoft Defender SmartScreen prevented an unrecognized app
> from starting. Running this app might put your PC at risk.

This is the codesigning-not-present warning. It is **not** a
malware flag. The "publisher" field is empty because the binary
isn't signed. To proceed:

1. Click **More info**.
2. Click **Run anyway** at the bottom of the dialog.

The binary will run normally. SmartScreen remembers your choice
for that exact binary on subsequent launches.

If you'd rather verify the binary before running it:

1. Right-click the `.exe` → **Properties** → **Digital Signatures**
   tab. The tab will say "This file is not digitally signed" — that's
   expected, and it confirms what you already know.
2. Or, verify the SHA-256 against the one in the GitHub release
   notes: `Get-FileHash .\omniscribe-server-windows-x.y.z.exe`.

## Troubleshooting

The full troubleshooting guide is at
[`docs/TROUBLESHOOTING.md`](../TROUBLESHOOTING.md). The
binary-specific entries:

- **"Windows protected your PC"** — see the SmartScreen section
  above. Codesigning is a v0.3.0 stretch.
- **"VCRUNTIME140.dll not found"** — install the
  [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170)
  (the bundle depends on it for `torch`'s native extensions). The
  v0.2.0 release notes will include a one-click installer link.
- **"First run is very slow"** — the onefile extract is
  ~5–10 seconds. Subsequent runs are <1 second. The Windows
  Defender real-time scan can add 30+ seconds the first time;
  the release notes will include a SmartScreen exclusion tip.
- **"Server boots, OCR returns nothing"** — see the
  [main README §Before you start](../../README.md#before-you-start).
  The binary doesn't include a vision model.
- **"`/api/health` returns 200 but OCR 503s** — same as above; the
  VLM endpoint isn't reachable from the binary. The default
  `LLM_API_BASE` is `http://localhost:1234/v1`.

## Building from source (maintainers only)

If you're the maintainer and want to build the binary yourself:

```bash
# One-time: install the dev dep that includes PyInstaller
uv sync --extra web --extra preprocessing

# Build (cold cache: ~5-10 min; warm: ~2-3 min)
make bundle

# Build + smoke test
make bundle-smoke
```

The spec lives at `omniscribe_server.spec` (repo root). The entry
wrapper at `scripts/run_server.py` is the single file PyInstaller
analyses. The build orchestration at `scripts/build_windows.py`
wraps the spec + the smoke test (boots the binary, hits
`/api/health`, kills the process). See the source for details.

### Known build issue: anyio + PyInstaller static analysis

**Status (2026-09-06): RESOLVED.** The 14-attempt failure record
above was the predictable outcome of a local spec misclassification
in `omniscribe_server.spec`: the `"anyio"` entry in `EXCLUDES`
actively fought `collect_submodules("anyio")` on the same file,
and EXCLUDES wins. Plus three related gaps surfaced in the
follow-on full-bundle boot (`fastapi.staticfiles`, `pydantic_settings`,
and a private scipy submodule). The full fix is five lines:

1. Remove `"anyio"` from `EXCLUDES` in `omniscribe_server.spec`.
2. Add `collect_submodules("fastapi")` to `_RUNTIME_SUBMODULES`.
3. Remove `"pydantic-settings"` from `EXCLUDES` and add
   `collect_submodules("pydantic_settings")` to `_RUNTIME_SUBMODULES`.
4. Add `import anyio.abc  # noqa: F401` to `scripts/run_server.py`
   so the static analyzer follows the import edge.
5. Add `"scipy._external.array_api_compat.numpy.fft"` to the manual
   hiddenimports block (a private submodule that
   `collect_submodules` skips by default).

The full root-cause analysis, the minimal reproducer that proves
the anyio part is local, and the chronological fix log are at
[`docs/rfcs/2026-09-bundle-sprint-1-findings.md`](../rfcs/2026-09-bundle-sprint-1-findings.md).

The smoke test in `scripts/build_windows.py --smoke` (or the
standalone `scripts/smoke_existing.py`) is the gate: it must report
`/api/health -> 200` before a release tag can ship. **Verified
2026-09-06 on Windows 11:** `/api/health -> 200 {"status":"ok"}`,
`/api/jobs -> 200 []`, `/openapi.json -> 200` (45 KB) on a 307 MB
`omniscribe-server.exe`.

## FAQ

**Why is the binary 1 GB?** Torch alone is ~700 MB. Surya-OCR's
ONNX models and pymupdf's native binaries add another ~300 MB.
The bundle includes everything except your VLM and your documents.
A 1 GB download is the realistic floor for a local-OCR product
in 2026.

**Can I just run `omniscribe-server` from a terminal instead of
double-clicking?** Yes. The console window is real stdout / stderr,
so you can redirect, pipe, and daemonize as you would any other
CLI. The default config still reads `.env` from the current
working directory.

**Will the binary auto-update?** No. v0.2.0 ships the auto-update
roadmap in v0.3.0 (the in-binary version check is ~50 LOC; not
worth the code review weight for v0.2). For now, watch the
GitHub releases page.

**Where does state go?** `OMNISCRIBE_ARTIFACT_DIR` defaults to
`<binary-parent-dir>\omniscribe-data\` (next to the binary). The
SQLite state file is at `<that-dir>\omniscribe-state.db`. Override
with `OMNISCRIBE_ARTIFACT_DIR` to put it anywhere.

## See also

- [RFC 001 — End-User Install Path](../rfcs/2026-09-end-user-install.md) — the design discussion.
- [`scripts/build_windows.py`](../../scripts/build_windows.py) — the build orchestration.
- [`omniscribe_server.spec`](../../omniscribe_server.spec) — the PyInstaller spec.
- [`README.md`](../../README.md) — the product overview.

_Last updated: 2026-09-05_
