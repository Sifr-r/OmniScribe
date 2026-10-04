# OmniScribe on Windows — the bundled binary

> **Status:** v0.3.0 and later support a single-file Windows server
> bundle. The source install remains supported on every backend platform.
> See the [Sprint 1 findings](../rfcs/2026-09-bundle-sprint-1-findings.md)
> for the original PyInstaller root-cause analysis.

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

## Install (the 4-step path)

1. **Download** `omniscribe-server-windows-x.y.z.exe` from the
   [latest GitHub release](https://github.com/Sifr-r/OmniScribe/releases/latest).
   Drop it anywhere — `Desktop\OmniScribe\` is the convention.

   > **How these assets get published:** the automated
   > [release workflow](../../.github/workflows/release.yml) currently builds
   > and attaches the **Python wheel and sdist only**. The Windows server
   > binary and the Flutter client are built and attached **manually** by a
   > maintainer — see [Publishing release assets](#publishing-release-assets).
   > If a release page has no `.exe` on it, that release shipped without the
   > Windows assets; use a source install (`uv sync` / `pip install`) instead.

2. **Start LM Studio** (or your preferred OpenAI-compatible VLM
   server). Load a vision model. Start its local server on
   `http://localhost:1234/v1`. The
   [main README §Before you start](../../README.md#before-you-start)
   has the model recommendations.

3. **Run the binary.** Double-click `omniscribe-server-windows-x.y.z.exe`
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

The PyInstaller onefile bundle is large because it includes the Python and
machine-learning runtime. Exact size varies by release; use the checksum and
asset size published with the release. It includes:

- **Python 3.12 runtime** embedded directly.
- **The full `omniscribe` package** (server + plugin harness + 14 plugins).
- **The 4 entrypoint CLIs** defined in the project:
  1. `omniscribe-server` (`omniscribe.server:main`): The primary FastAPI web server,
     mounting the Cordis plugin harness, ASGI auth/rate/upload middlewares, and REST/WebSocket APIs.
  2. `omniscribe-worker` (`omniscribe.worker:main`): Standalone multi-worker runner for
     distributed job processing over Redis (`--concurrency`, `--redis-url`, `--visibility-timeout`).
  3. `omniscribe-migrate-lexicon` (`omniscribe.cli.migrate_lexicon:main`): Database migration
     tool upgrading legacy ChromaDB glossaries to the LanceDB vector store.
  4. `omniscribe-import-lanes-lexicon` (`omniscribe.cli.import_lanes_lexicon:main`): Utility for
     importing Lane's Arabic-English Lexicon into LanceDB from SQLite databases or TEI.2 XMLs.
- **All runtime dependencies:** `torch`, `torchvision`, `surya-ocr`, `pymupdf`, `pydantic`,
  `fastapi`, `uvicorn`, `httpx`, `redis`, `pyspellchecker`, `python-docx`, `defusedxml`, `numpy`.
- **Runtime data resources at `src/omniscribe/resources/`** (mirrored via `DATAS`):
  - `cordis.yml`: The declarative plugin tree defining active plugins, dependencies, and routes.
  - **Bundled dictionaries:** Compressed language dictionaries under `resources/dictionaries/`
    (`ara.json.gz`, `eng.json.gz`) used for dictionary-assisted spelling verification.
  - **Calibration data:** Empirical confidence calibration models under `resources/calibration/`
    (`qwen2_5_vl_72b.json`) used for token confidence evaluation and scoring.
  - **Sample PDFs:** Five canonical test fixtures under `resources/sample_pdfs/` (`digital.pdf`,
    `handwritten.pdf`, `hybrid.pdf`, `dense.pdf`, `notes.pdf`) serving the `/api/sample-pdf/*`
    first-run demo endpoints.

The first run takes ~5–10 seconds to extract the onefile archive
to a temp dir. Subsequent runs are ~1 second to start.

## What is NOT in the binary

- **A vision model.** You must bring your own. LM Studio (free,
  ~250 MB), Ollama, or any OpenAI-compatible endpoint.
- **The Flutter client.** It's a separate download. The Flutter
  build pipeline is independent of the Python one.
- **Your documents.** State uses `OMNISCRIBE_ARTIFACT_DIR`; when unset, it
  falls back to the Windows temporary directory. Set a dedicated persistent
  directory for normal use. The SQLite state backend is the default.

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
  (the bundle depends on it for `torch`'s native extensions).
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

Both build-time and existing-bundle smoke use the same gate. It refuses an
occupied loopback port, polls without waiting for stdout, validates health and
the bundled digital sample, and stops only the process tree it launched.
Artifacts, SQLite state, spool files, extraction and logs are isolated under
`build/bundle-smoke-*`; the printed evidence directory is retained for review.
Retaining it also avoids Windows extraction-file locks masking the probe result.

### Exclusion audit (2026-10-04)

The installed Transformers 5.16.1 import tree was checked against every
`EXCLUDES` entry. OmniScribe and Surya contain no direct import of an excluded
module. The development/tool exclusions are `pytest`, `pytest_asyncio`,
`pytest_cov`, `hypothesis`, `mypy`, `ruff`, `pip`, `setuptools`, `wheel`, `twine`,
`_pytest`, `tests`, `IPython`, `jupyter`, `notebook`, `sphinx`, and `PyInstaller`.
Transformers' pytest imports occur in its test helper, rather than its runtime
processing/modeling import chain.

The seven excluded model packages are `deepseek_ocr2`, `glm_ocr`, `got_ocr2`,
`lighton_ocr`, `paddleocr_vl`, `pp_ocrv5_mobile_det`, and `pp_ocrv5_mobile_rec`.
References outside those packages occur in the models `TYPE_CHECKING` branch
and modular model-generation sources; the server/Surya runtime does not select
those models. The redundant `pp_ocrv5_mobile_*` entry was removed because
PyInstaller exclusions are explicit module names. `transformers.quantizers`
remains included: `finegrained_fp8` imports it during runtime model initialization.

`tests/scripts/test_bundle_imports.py` makes every excluded module unavailable
in a fresh interpreter, then imports the server, Surya detection, and Transformers
processing/modeling. This guards the observed runtime chain when dependencies
change; it does not prove every optional Transformers model can load.
`tests/scripts/test_bundle_smoke.py` checks occupied-port rejection, silent-log
deadline behavior, isolated state, response validation, and owned-process cleanup.

The existing 482,629,422-byte Windows binary served `/api/health` and
`/api/sample-pdf/digital.pdf` with HTTP 200 on 2026-10-04. It mounted all 14
plugins with isolated SQLite storage. This proves that existing artifact boots;
a release of newer source still requires a fresh build and its own smoke result.

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

## Publishing release assets

The automated release pipeline does **not** produce the Windows binaries. It
runs on `ubuntu-latest` and attaches only `dist/*.whl` and `dist/*.tar.gz`.
Publishing the server binary and the client is a manual maintainer step.

### Server binary

```powershell
uv run pytest -m "not slow and not slow_dataset"  # must be green first
uv run python scripts/build_windows.py --smoke    # builds dist/omniscribe-server.exe and smoke checks it
```

### Client

```powershell
Push-Location client
flutter build windows --release
Pop-Location
```

### Naming, packaging, checksums and attaching to the release

Run from the repository root after both builds pass. Set the exact release tag
being published; the unversioned build outputs are copied into the versioned
release filenames below. The client archive includes its DLLs and data directory.

```powershell
$releaseTag = 'vX.Y.Z' # replace with the actual release tag
$releaseVersion = $releaseTag.TrimStart('v')
$serverAsset = "dist/omniscribe-server-windows-$releaseVersion.exe"
$clientAsset = "dist/omniscribe-client-windows-$releaseVersion.zip"
Copy-Item -LiteralPath dist/omniscribe-server.exe -Destination $serverAsset -Force
Compress-Archive -Path client/build/windows/x64/runner/Release/* -DestinationPath $clientAsset -Force
@($serverAsset, $clientAsset) | ForEach-Object {
    $assetHash = Get-FileHash -LiteralPath $_ -Algorithm SHA256
    "$($assetHash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($_))"
} | Set-Content -LiteralPath dist/SHA256SUMS.txt -Encoding ascii
gh release upload $releaseTag $serverAsset $clientAsset dist/SHA256SUMS.txt --clobber
```

### Rules

- Source changes do **not** update an existing binary. Every release needs a
  freshly built artifact; a new tag is not enough.
- A release may only be tagged once the bundle smoke check
  (`scripts/build_windows.py --smoke`) and the Flutter Windows/web builds
  above have passed **for the working tree being released**.
- Publish the SHA-256 of each asset in `SHA256SUMS.txt`; the install path
  above tells users to verify it.

Until a Windows build job is added to the workflow, treat every one of these
steps as the release gate. Do not describe a release as "distributable" on
the strength of the wheel alone.

## FAQ

**Why is the binary large?** Torch, Surya-OCR, native PDF libraries, and the
Python runtime are bundled together. The exact compressed size changes with
dependency versions; the VLM weights and your documents are not included.

**Can I just run `omniscribe-server` from a terminal instead of
double-clicking?** Yes. The console window is real stdout / stderr,
so you can redirect, pipe, and daemonize as you would any other
CLI. The default config still reads `.env` from the current
working directory.

**Will the binary auto-update?** No. Watch the GitHub releases page and replace
the executable manually after verifying the published checksum.

**Where does state go?** `OMNISCRIBE_ARTIFACT_DIR` defaults to a durable
per-user data directory — `%LOCALAPPDATA%\OmniScribe` on Windows,
`~/Library/Application Support/OmniScribe` on macOS, and
`$XDG_DATA_HOME/omniscribe` (or `~/.local/share/omniscribe`) on Linux. The
SQLite state file is `<artifact-dir>\omniscribe-state.db`, and artifact
blobs are sibling `.bin` files. This used to default to the operating
system temporary directory, which meant job history and result handles were
deleted by ordinary temp cleanup while the UI still listed them; that is why
you should now leave the default alone rather than pointing it at `%TEMP%`.
Set `OMNISCRIBE_ARTIFACT_DIR` yourself to relocate or to deliberately use a
disposable directory.

## See also

- [RFC 001 — End-User Install Path](../rfcs/2026-09-end-user-install.md) — the design discussion.
- [`scripts/build_windows.py`](../../scripts/build_windows.py) — the build orchestration.
- [`omniscribe_server.spec`](../../omniscribe_server.spec) — the PyInstaller spec.
- [`README.md`](../../README.md) — the product overview.

_Last updated: 2026-09-27_
