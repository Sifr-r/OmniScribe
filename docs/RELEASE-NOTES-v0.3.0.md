# OmniScribe v0.3.0 — Release Report

> **Released:** 2026-09-06
> **Tag:** [`v0.3.0`](https://github.com/Sifr-r/OmniScribe/releases/tag/v0.3.0)
> **Compare:** [v0.2.0...v0.3.0](https://github.com/Sifr-r/OmniScribe/compare/v0.2.0...v0.3.0)
> **Status:** Beta-stable; single-binary Windows distribution **SHIPS** alongside the source install.

This is the v0.3.0 release of OmniScribe — the
**single-binary Windows distribution** lands, and the
**2026-09-04 Five-Lens Audit** remediation closes
end-to-end (Phases 0-3, 5, 6 + Phase 4 retry).
The headline: a 307 MB `omniscribe-server.exe` replaces
the 12-step source install on Windows. The source install
is still supported (Linux / macOS / Windows) and is the
recommended path for development.

---

## 1. TL;DR

| | |
|---|---|
| **Install (Windows, recommended)** | Download `omniscribe-server-windows-x.y.z.exe` from the [release page](https://github.com/Sifr-r/OmniScribe/releases/tag/v0.3.0). Double-click. Visit `http://127.0.0.1:8000/api/health`. Done. |
| **Install (source, all platforms)** | `git clone` → `uv sync --extra web --extra preprocessing` → `uv run omniscribe-server --port 8000` → start LM Studio → `cd client && flutter run -d windows` |
| **Tests** | **2094 passed**, 19 skipped, 13 deselected, 0 failures in 102.35 s |
| **Lint** | `make check` (lint + typecheck + fast tests with `--cov-fail-under=80`) is the pre-PR contract |
| **mypy strict** | `omniscribe.core.*`, `omniscribe.plugins.*`, `omniscribe.harness.*` all pass with `disallow_untyped_defs` and `disallow_untyped_calls` |
| **Supported platforms** | Windows 10/11 (binary + source), macOS 13+, Ubuntu 22.04+ (source) — Flutter client covers desktop + mobile |
| **Single-binary distribution** | **✅ SHIPS** — 307 MB `omniscribe-server.exe`, `/api/health -> 200`, `/api/jobs -> 200`, `/openapi.json -> 200`. See [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md). |
| **Migration from v0.2.0** | None required. The state-backend default is unchanged (`sqlite`). The bundle is additive; existing source installs keep working. |

## 2. What's new

### 2.1 Phase 4 (RETRY) — Single-binary Windows distribution SHIPS

The single-binary Windows distribution per
[RFC 001 Option A](rfcs/2026-09-end-user-install.md) was
**deferred from v0.2.0** because the PyInstaller bundle
crashed at boot with `ModuleNotFoundError: No module named
'anyio'` after 14 build attempts. Sprint 1 of
RFC 002 identified the
actual cause and shipped the fix. The full root-cause
analysis is at
[`docs/rfcs/2026-09-bundle-sprint-1-findings.md`](rfcs/2026-09-bundle-sprint-1-findings.md).

**The bundle now works.** Verified 2026-09-06 on a Windows
11 dev box:

```
$ .\omniscribe-server.exe --port 18766
INFO:     Started server process [39512]
...
$ curl http://127.0.0.1:18766/api/health
{"status":"ok"}
```

Smoke gate (must be green for a release tag):

- `GET /api/health` → `200 {"status":"ok"}`
- `GET /api/jobs` → `200 []`
- `GET /openapi.json` → `200` (45 KB)

#### What was actually wrong

The 14-attempt failure record in
[`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
was the predictable outcome of **four local spec
misclassifications**, not an upstream PyInstaller bug. The
fix is five lines:

1. Remove `"anyio"` from `EXCLUDES` in `omniscribe_server.spec`
   (was actively fighting `collect_submodules("anyio")` on
   the same file; EXCLUDES wins).
2. Add `collect_submodules("fastapi")` to `_RUNTIME_SUBMODULES`
   (was missing — `fastapi.staticfiles` was "not installed"
   on boot).
3. Remove `"pydantic-settings"` from `EXCLUDES` and add
   `collect_submodules("pydantic_settings")` (paired bug;
   EXCLUDES would have masked even adding the collect call).
4. Add `import anyio.abc  # noqa: F401` to
   `scripts/run_server.py` so the static analyzer follows
   the import edge that FastAPI / Starlette / uvicorn
   normally carry internally.
5. Add `"scipy._external.array_api_compat.numpy.fft"` to the
   manual hiddenimports block (private underscore-prefixed
   submodule that `collect_submodules` skips by default;
   discovered automatically by `scripts/iterative_bundle.py`).

The minimal reproducer at
[`repro/`](../repro) (40 lines of spec + 21 lines of
entry script) proves the anyio part is local, not upstream.
The 14 prior attempts spent on hooks, force-imports, and
version downgrades were fighting a phantom.

#### Bundle install (3 steps)

```powershell
# 1. Download omniscribe-server-windows-0.3.0.exe from the
#    release page. Drop it anywhere; Desktop\OmniScribe\ is
#    the convention.

# 2. Start LM Studio (or your preferred OpenAI-compatible
#    VLM server). Load a vision model. Start its local
#    server on http://localhost:1234/v1.

# 3. Double-click omniscribe-server-windows-0.3.0.exe. A
#    console window appears with the server log. Visit
#    http://127.0.0.1:8000/api/health in a browser.
```

That's it. No `git clone`, no `uv sync`, no Flutter SDK,
no Python on `PATH`. The binary is 307 MB (Python 3.12 +
torch + surya-ocr + pymupdf + the Cordis plugin tree).

> **Codesigning is intentionally out of scope for v0.3.0.**
> SmartScreen shows the "Unknown publisher" warning the
> first time. Click **More info** → **Run anyway**. The
> warning is not a malware flag; it's a side effect of
> not paying for a $200-500/year codesigning cert. See
> [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
> §"SmartScreen" for the full context and the SHA-256
> verification path.

#### What's still deferred to v0.3.x follow-ups

- **macOS + Linux bundles** (Sprint 4 territory per
  RFC 002; no current user need beyond Windows).
- **Codesigned installer / Inno Setup** (no cert budget).
- **Bundle in CI** (still a developer-machine flow;
  `make bundle` + `make bundle-smoke`).

### 2.2 Phase 6 batch 5 — D9 mypy strict for plugins + harness

- **`pyproject.toml`**: new mypy override for
  `["omniscribe.plugins.*", "omniscribe.harness.*"]` with
  `disallow_untyped_defs = true` and
  `disallow_untyped_calls = true` (matching the existing
  `omniscribe.core.*` override).
- **14 → 0 errors** in the plugin + harness tree:
  - 4 missing return-type annotations added
    (`_progress_adapter`, `_warning_adapter`,
    `_cancel_check` in `plugins/ocr/service.py`; the inner
    `stream()` in `plugins/ocr/plugin.py`).
  - 9 `# type: ignore[no-untyped-call]` for pymupdf, which
    ships partial type stubs. (`pymupdf.fitz.open`,
    `pix.tobytes`, `doc.close`.)
- The harness was already at 0 errors; the plugin tree
  was the last untyped area.
- Closes audit finding **D9** ("enable
  `mypy.disallow_untyped_defs` for `omniscribe.plugins.*`
  and `omniscribe.harness.*`").

### 2.3 Phase 6 batch 5 — Q9 calibration script determinism test

- **New test `test_seed_actually_controls_the_platt_split`**
  in `tests/scripts/test_calibrate_model_script.py` pins the
  `scripts/calibrate_model.py --seed` contract:
  - Different seeds produce different `a` / `b` / `n_train`
    (the seed is actually consumed somewhere on the path).
  - The same seed is byte-for-byte deterministic on
    `n_train`, `n_test`, `a`, and `b` (with `math.isclose`
    on the floats to allow platform-level RNG jitter).
- Would catch a regression where the script "uses the seed
  once, then drifts" via ambient numpy state.
- Closes audit finding **Q9**.

### 2.4 Phase 6 batch 4 — Q8 (under-tested modules wave)

178+ new tests across five modules surfaced by the QA
audit as under-tested, in five new test files:

- `tests/core/transcription/test_transcription_engines.py`
  (766 lines): local + API audio transcription engine paths.
- `tests/core/grounded/test_prompted_grounded_ocr.py`
  (710 lines): prompt builder, chunking, coordinate
  clamping, reading order, JSON repair.
- `tests/plugins/test_glossary_http_fetch.py` (585 lines):
  glossary HTTP fetch, redirect limits, SSRF private-IP
  blocking, body size guards.
- `tests/routers/test_glossary_library_routes.py`
  (446 lines): library routes, source toggle/reorder, query
  pagination, LanceDB 503 fallback.
- `tests/core/glossary_sources/test_encoding_and_xliff.py`
  (518 lines): encoding auto-detection and XLIFF 1.2/2.0
  parsing.

5 real bugs in `glossary_sources` + `plugins/glossary`
were fixed by the Q8 coverage:

- BOM-stripping in `encoding.py`.
- XLIFF `(@id, @xml:lang)` merge key.
- SSRF guard bypass via fragment.
- `GlossaryTooLargeError` (413) instead of `ValueError`.
- Paginating `state_backend.list()` instead of in-memory.

Closes audit finding **Q8**.

### 2.5 Phase 6 batch 4 — Q10, P13 (closing low-risk tails)

- **Q10**: `tests/ops/` merged into `tests/scripts/`
  (one canonical directory for CLI / script tests).
- **P13**: closed as **N/A**. The out-of-band fingerprint
  handshake in [`docs/SECURITY.md`](SECURITY.md) §"Security
  contact" is the policy; no static PGP key is published
  to avoid unmanaged key rot. Sensitive reports request
  the fingerprint out-of-band.

### 2.6 Bundle infrastructure improvements

- **`scripts/smoke_existing.py`** (new, 80 LOC): standalone
  smoke test for an already-built binary. Boots, hits
  `/api/health`, asserts 200. Used by both the iterative
  bundler and manual verification. Doesn't re-run `uv sync`
  (which is the part that hits Windows file locks during
  dev).
- **`scripts/iterative_bundle.py`** (new, 110 LOC): test-driven
  "catch the next missing module and add it" tool. Boots
  the binary, parses the first `ModuleNotFoundError` /
  `ImportError`, adds the missing module to the spec, and
  rebuilds. Exits when the binary boots successfully.
  Auto-fixed the `scipy._external.array_api_compat.numpy.fft`
  gap during Sprint 1. Useful for future maintenance if a
  new dep tree has a similar gap.
- **`repro/`** (new): minimal 30-line spec that proves the
  anyio bundling bug is local. Tracked alongside the main
  spec so the next maintainer can re-run the minimal build
  if a regression hits.
  - `repro/minimal_anyio.spec` (40 lines)
  - `repro/run_minimal.py` (21 lines)
  - `repro/smoke.py` (50 lines)

## 3. Verification

```
$ uv run pytest -m "not slow and not slow_dataset" --no-cov
...
2094 passed, 19 skipped, 13 deselected, 0 failures in 102.35 s

$ uv run ruff check src tests
All checks passed!

$ uv run mypy src/omniscribe/plugins src/omniscribe/harness
Success: no issues found in 56 source files
```

`make check` is the full pre-PR contract (lint + typecheck
+ fast tests with `--cov-fail-under=80`); it's the same
gate CI uses.

The 19 test skips are pre-existing (pyarrow / lancedb /
langgraph not installed in the dev venv; pytesseract not
installed; per-test durations in one file). The release
`make check` gate passes on the installed subset.

## 4. Install

### 4.1 Single-binary install (Windows 10/11, recommended for end users)

```powershell
# 1. Download omniscribe-server-windows-0.3.0.exe from
#    https://github.com/Sifr-r/OmniScribe/releases/tag/v0.3.0

# 2. Start LM Studio (or your preferred OpenAI-compatible
#    VLM server). Load a vision model. Start its local
#    server on http://localhost:1234/v1.

# 3. Run the binary. A console window appears with the
#    server log. Visit http://127.0.0.1:8000/api/health
#    in a browser to confirm.

# 4. Run the Flutter client (separate download from the
#    same release page). It connects to http://127.0.0.1:8000
#    by default. Drag a PDF onto the Workstation tab.
```

See [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
for the full end-user guide, including the SmartScreen
"Run anyway" walkthrough, the VCRUNTIME140.dll pointer,
and the SHA-256 verification path.

### 4.2 Source install (Windows / macOS / Linux, recommended for development)

```bash
# 1. Backend (Python 3.11+, uv)
git clone https://github.com/Sifr-r/OmniScribe.git
cd OmniScribe
uv sync --extra web --extra preprocessing
uv run omniscribe-server --port 8000

# 2. VLM endpoint (in another terminal)
#    Start LM Studio, load a vision model, start the local server on
#    http://localhost:1234/v1. See README.md §"Before you start".

# 3. Flutter client (in another terminal — Flutter SDK 3.x required)
cd client
flutter pub get
flutter run -d windows   # or: macos / linux
```

### 4.3 Upgrading from v0.2.0

1. `git pull` (or `uv sync` if you have a local clone).
2. Restart the server. **No breaking changes** to env vars
   or the HTTP surface.
3. The bundle is a fresh download from the
   [v0.3.0 release page](https://github.com/Sifr-r/OmniScribe/releases/tag/v0.3.0).
   If you're on the source install, you don't need to
   switch — the bundle is an alternative, not a replacement.

### 4.4 Three deployment profiles

See [`docs/DEPLOYMENT.md`](DEPLOYMENT.md):

- **Profile 1** — Local Desktop (loopback bind, no token,
  Flutter client). The v0.3.0 default. The binary install
  is Profile 1 with no dev tools required.
- **Profile 2** — LAN / Trusted Network (bearer token
  required, rate limit + upload cap applied).
- **Profile 3** — Public Internet (Caddy/nginx reverse
  proxy, TLS termination on the proxy).

## 5. Known issues

- **U12 — in-UI "try with sample PDF" affordance** is
  deferred to v0.3.x Sprint 3 (per
  RFC 002 §4). The
  end-user audit finding U12 ("a new user has no easy way
  to confirm the install works without finding their own
  PDF") is real; the fix is a FastAPI `/api/sample-pdf/{name}`
  route + a Flutter Workstation "Try sample PDF" button.
- **Q11 chaos tests** — multi-day, deferred to v0.3.1+.
- **Q12 Flutter `integration_test/`** — multi-day, Flutter-side.
- **Q13 Flutter widget test balance** — multi-day, Flutter-side.
- **Redis state backend** — `OMNISCRIBE_STATE_BACKEND=redis`
  still crashes at plugin apply. Multi-day work, no
  current user need (Profile 2/3 use SQLite). Deferred to
  v0.3.1+ unless a Profile 4 (multi-worker LAN) deployment
  is in flight.
- **Model pre-flight route** — `ensure_model_loaded()`
  exists in `core/ocr/processor.py` and
  `core/grounded/prompted.py` as private; the public API
  route is unbuilt. Out of scope for v0.3.0.
- **Codesigned installer** — no cert budget for v0.3.0.
  SmartScreen shows the "Unknown publisher" warning.
  See [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
  §"SmartScreen."
- **macOS / Linux bundles** — Sprint 4 territory per
  RFC 002; no current user need beyond Windows.

## 6. The 4 commits (since v0.2.0)

```
79fea9f v0.3.0 Sprint 1: bundle ships (307 MB, /api/health -> 200)
ce6ddd6 docs: v0.3.0 RFC 002 (approved) + outstanding-work Phase 7 entry
983930f phase6 long-tail batch 5: D9 mypy strict (plugins+harness), Q9 Platt seed test
a84d93c phase6 long-tail batch 4: Q8 (under-tested modules wave) + Q10
```

Diff stats (commits on top of `612f017`):

| Commit | Files | Insertions | Deletions |
|---|---|---|---|
| `a84d93c` Phase 6 batch 4 (Q8 + Q10) | 20 | +3765 | −68 |
| `983930f` Phase 6 batch 5 (D9 + Q9) | 6 | +112 | −25 |
| `ce6ddd6` RFC 002 + outstanding-work | 2 | +207 | −2 |
| `79fea9f` Sprint 1 (bundle ships) | 13 | +845 | −104 |
| **Total** | **41** | **+4929** | **−199** |

## 7. The audit + plan + RFCs

The full spec for v0.2.0 was the audit and remediation plan
(both preserved in Git history). The v0.3.0 spec extends that with:

- **2026-09-04 Five-Lens Audit** (preserved in Git history)
  (51 KB) — the 5-lens audit that drove the v0.2.0 work.
- **2026-09-04 Remediation Plan** (preserved in Git history)
  — the 6-phase plan; v0.3.0 closes Phase 4 (the
  single-binary distribution).
- **RFC 002: v0.3.0 Scope** (preserved in Git history)
  (RFC 002, 9.2 KB) — the v0.3.0 plan, including the bundle
  decision (Option (a) — wait for upstream PyInstaller
  fix; superseded by Sprint 1's local-bug finding), the
  U12 product call (Option (b) — FastAPI route), and the
  4-sprint phasing.
- **[`rfcs/2026-09-bundle-sprint-1-findings.md`](rfcs/2026-09-bundle-sprint-1-findings.md)**
  (RFC 003 / Sprint 1 report) — the full root-cause
  analysis of the bundling failure, the minimal
  reproducer, the chronological fix log, and the
  implications for future maintenance.

Every audit finding (Critical, High, Medium, Low, Info) is
in at least one phase; none is dropped.

## 8. Next steps (post-v0.3.0)

Per RFC 002 §5:

- **Sprint 3 (next)** — U12 "try with sample PDF": FastAPI
  `/api/sample-pdf/{name}` route + Flutter Workstation
  "Try sample PDF" button. Backend ~2 hours; Flutter ~2
  hours.
- **Sprint 4** — Buffer / spillover. Pick up Redis state
  backend (only if Profile 4 in flight), Q11 chaos test
  first slice, or additional mypy strict areas, per user
  direction. Or clean cut to v0.3.0 release.
- **v0.3.1+** — Q11 chaos tests, Q12 Flutter integration
  tests, Q13 widget test balance, Redis state backend,
  model pre-flight route. All multi-day; pick up as
  separate work streams.
- **v0.4+** — macOS + Linux bundles (if user demand
  emerges), codesigned installer (if cert budget
  appears), bundle in CI.

---

*End of release report. ~3,800 words. Read top-down; sign
off on §1-3 first, then §4-5 for install/upgrade, then §6-8
for the commit history and the next-step backlog.*

_Last updated: 2026-09-06_
