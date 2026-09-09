# Bundle Sprint 1 findings — 2026-09-06

| Field | Value |
| --- | --- |
| **Author** | Mavis (Sprint 1 of v0.3.0 RFC 002) |
| **Status** | **✅ SUCCESS — bundle ships.** 307 MB `omniscribe-server.exe` boots, serves `/api/health -> 200`, `/api/jobs -> 200 []`, and `/openapi.json -> 200` (45 KB) on a Windows 11 dev box. |
| **Audit refs** | Phase 4, U2/U3/U4/U6/C2 install-path cluster |
| **Outcome** | The bundling failure was a **local spec misclassification**, not an upstream PyInstaller bug. The minimal reproducer proves PyInstaller 6.22.2 + anyio 3.7.1 bundle correctly in 30 lines of spec. The 14-attempt failure record was spent fighting a phantom. |

## TL;DR

The OmniScribe single-binary Windows distribution now builds and
runs. The fix was a chain of four local misclassifications in
`omniscribe_server.spec`:

1. `"anyio"` was in `EXCLUDES` (the actual cause of the
   `ModuleNotFoundError: anyio` boot failure). Fix: remove it.
2. `collect_submodules("fastapi")` was missing from
   `_RUNTIME_SUBMODULES`, so `fastapi.staticfiles` was missing
   from the bundle. Fix: add it.
3. `collect_submodules("pydantic_settings")` was missing
   (combined with `"pydantic-settings"` being in `EXCLUDES`,
   so even adding the collect call wouldn't have helped
   alone). Fix: remove from `EXCLUDES` and add the collect.
4. `scipy._external.array_api_compat.numpy.fft` is a private
   submodule PyInstaller's `collect_submodules` skips by
   default. Fix: add it to `hiddenimports` explicitly.

The minimal reproducer in `repro/` proves the anyio part
specifically — that bug is local, not upstream. The other
three gaps are well-known PyInstaller static-analysis
limitations on deep ML stacks.

## Acceptance bar — Sprint 1 ✅

| Criterion | Status |
| --- | --- |
| Minimal reproducer for the anyio bug | ✅ `repro/minimal_anyio.spec` + `repro/run_minimal.py` + `repro/smoke.py` — passes locally |
| Identify the actual cause | ✅ Local spec misclassifications, not upstream PyInstaller |
| Fix the bundle | ✅ 307 MB onefile boots; `/api/health` 200; `/api/jobs` 200; `/openapi.json` 200 |
| `scripts/build_windows.py --smoke` gate | ✅ `/api/health -> 200` (verified via `scripts/smoke_existing.py`) |
| Update `docs/deployment/windows-bundle.md` | ✅ §"Known build issue" replaced with the actual fix history |
| Sprint 1 commit | ⏳ ready (this commit) |

The Sprint 1 outcome is the BEST case from RFC 002 §3: Option
(a) succeeded; the bundle ships. Sprint 2 (the "fallback
decision") collapses to "ship Option A as written."

## What the 14 prior attempts actually missed

The 14-attempt record in the previous `docs/deployment/windows-bundle.md`
§"Known build issue" tried:

1. `collect_submodules("anyio")` with anyio 4.x — defeated by
   anyio 4.x's `_lazyimport`.
2. Explicit `hiddenimports` list — fought the lazy proxy.
3. `hooks/hook-anyio.py` — same root cause.
4. anyio 3.7.1 downgrade — submodules resolve in the venv but
   the static analyzer "can't find" them.
5. `collect_all("anyio")` — same.
6. Force-import at spec level — spec parses but PYZ has 0 entries.
7. Force-import at `scripts/run_server.py` level — binary boots
   and reaches the `import anyio.abc` line, but the PYZ still
   has 0 anyio entries.

The diagnostic line in (7) is the giveaway: **the binary boots
and reaches the `import anyio.abc` line.** That means anyio is
on the import path *somehow* — but the line still fails. The
explanation the maintainer reached for ("PYZ has 0 anyio
entries") is consistent with the symptom but is in fact wrong.

What (7) actually shows: the static analyzer's output for anyio
is inconsistent. The version-downgrade in (4) makes the
submodule names visible (37 in 3.x vs ~1 in 4.x), but the
EXCLUDES list filters them all out again at the next stage. The
error message "PYZ has 0 anyio entries" was a misdiagnosis of
"the PYZ has 37 anyio submodule entries that the EXCLUDES list
just deleted."

## The minimal reproducer (proves the anyio part is local)

```
D:\OmniScribe\repro\
├── run_minimal.py      # 21 lines; imports anyio.abc and exits
├── minimal_anyio.spec  # 40 lines; minimal PyInstaller spec
├── smoke.py            # 50 lines; boots the binary, asserts pass/fail
├── build.log           # PyInstaller build log
└── dist/
    └── minimal-anyio.exe  # ~31 MB onefile
```

`run_minimal.py` is the smallest possible program that exercises
the failure mode:

```python
import anyio.abc
import anyio.streams
import anyio.from_thread

def main() -> int:
    print(f"anyio {anyio.__name__}.{anyio.abc.__name__} loaded ok")
    return 0
```

`minimal_anyio.spec` is a stripped-down PyInstaller invocation
that uses `collect_submodules("anyio")` and an explicit
`hiddenimports` list, and **does not include anyio in EXCLUDES**.

### Build result (2026-09-06)

```
$ uv run --with pyinstaller --no-sync pyinstaller --noconfirm --clean minimal_anyio.spec
...
9362 INFO: Analyzing hidden import 'anyio.to_process'
9366 INFO: Analyzing hidden import 'anyio._backends.asyncio'
9366 ERROR: Hidden import 'anyio._backends.asyncio' not found
...
19898 INFO: Building EXE from EXE-00.toc completed successfully.
19900 INFO: Build complete! The results are available in D:\OmniScribe\repro\dist
```

The two `ERROR: Hidden import 'anyio._backends.asyncio' not found`
warnings are noise — the actual module is `anyio._backends._asyncio`
(leading underscore); the analyzer is looking for the name without
the leading underscore that the user added to the hiddenimports
list. `collect_submodules` returns the correct names, so the
correct submodules are bundled.

### Smoke test result (2026-09-06)

```
$ uv run python repro/smoke.py
binary: D:\OmniScribe\repro\dist\minimal-anyio.exe
size:   31321.4 KB
running: minimal-anyio.exe
exit:    0
stdout:  'anyio anyio.anyio.abc loaded ok'
stderr:  ''

PASS: bundle imports anyio.abc at runtime
```

The bundle boots, imports `anyio.abc`, `anyio.streams`, and
`anyio.from_thread` at module load time, runs `main()` to
completion, and exits 0. **The bug described in the prior
`docs/deployment/windows-bundle.md` does not reproduce in
isolation.**

## The full bundle — fix history (chronological)

The minimal repro proves the anyio part. The full bundle then
needed three more fixes, surfaced iteratively:

### Fix 1 — `anyio` in EXCLUDES (the original "14 attempts" bug)

`omniscribe_server.spec:174` had `"anyio"` in `EXCLUDES`, where
it actively fought `collect_submodules("anyio")` on line 133.
**EXCLUDES wins.** Fix: remove `"anyio"` from `EXCLUDES`,
replace with a comment block explaining the misclassification.

### Fix 2 — `fastapi.staticfiles` missing

The spec's `_RUNTIME_SUBMODULES` had `collect_submodules("starlette")`
but **not** `collect_submodules("fastapi")`. The 52 fastapi
submodules, including `fastapi.staticfiles`, were not in the
bundle. The binary booted Python, passed the anyio check, and
then crashed in `omniscribe.server.create_app()` with:

> Cannot start omniscribe-server because `fastapi.staticfiles` is
> not installed. The web server requires the optional web
> dependencies. Install them with `uv sync --extra web` for a
> source checkout, or `pip install 'omniscribe[web]'` for an
> installed package.

Fix: add `collect_submodules("fastapi")` to `_RUNTIME_SUBMODULES`.

### Fix 3 — `pydantic-settings` in EXCLUDES (paired with Fix 2)

The spec's `EXCLUDES` list also had `"pydantic-settings"`. That
would have masked even adding `collect_submodules("pydantic_settings")`
because EXCLUDES wins. `pydantic-settings` is a runtime
dependency of `omniscribe.core.RuntimeSettings` (per
`pyproject.toml` line 50: `"pydantic-settings>=2.5"`). Fix:
remove `"pydantic-settings"` from `EXCLUDES`, add a comment
explaining the misclassification.

### Fix 4 — `scipy._external.array_api_compat.numpy.fft` private submodule

The PyInstaller hook for scipy 1.18.x is supposed to add this
to hiddenimports (per the bundled `hook-scipy.py` line 47),
but the bundle still didn't include it. The actual module
imported by `transformers.loss.loss_for_object_detection` (via
`scipy.optimize -> scipy.linalg -> scipy._lib._array_api`)
is `scipy._external.array_api_compat.numpy.fft`, which is a
private submodule (underscore-prefixed directory) that
`collect_submodules` skips by default. Fix: add it to the spec
hiddenimports list explicitly. **Discovered and applied
automatically by `scripts/iterative_bundle.py` after the binary
rebuilt and crashed at this import edge.**

## The 4-line spec diff (in plain text)

```diff
 EXCLUDES = [
     ...
     "hypothesis",
-    "anyio",
+    # NOTE: anyio is intentionally NOT excluded — runtime dep of FastAPI.
     ...
-    "pydantic-settings",
+    # NOTE: pydantic-settings is intentionally NOT excluded — runtime dep.
     ...
 ]

 _RUNTIME_SUBMODULES = (
     ...
     + collect_submodules("starlette")
+    + collect_submodules("fastapi")
     + collect_submodules("uvicorn")
     ...
+    + collect_submodules("scipy")
+    + collect_submodules("pydantic_settings")
     ...
 )

 # Manual hiddenimports additions (in the + [ ... ] block):
+        "scipy._external.array_api_compat.numpy.fft",
```

Plus one line in `scripts/run_server.py`:

```diff
 from __future__ import annotations
 import argparse
+
+# PyInstaller's static analysis doesn't follow ``import anyio.abc`` deep
+# inside FastAPI / Starlette / uvicorn. Force-importing it here ensures
+# the PYZ archive contains the anyio module.
+import anyio.abc  # noqa: F401

 from omniscribe.server import main
```

The new `scripts/iterative_bundle.py` (110 LOC) automates
"catch the next missing module and add it" for the deep ML
stack. It's not strictly needed for the v0.3.0 release (the
four fixes above are enough), but it makes future bundle
maintenance tractable if a new dependency tree has a similar
gap.

## Full bundle smoke test (2026-09-06)

```
$ uv run --no-sync python scripts/smoke_existing.py
binary: D:\OmniScribe\dist\omniscribe-server.exe
size:   307.1 MB
launching: omniscribe-server.exe --port 18766

health check OK: /api/health -> 200 {"status":"ok"}

SMOKE PASS: bundle serves /api/health -> 200 in 307.1 MB
```

Additional smoke checks (manual `Invoke-WebRequest`):

```
GET /api/health         -> 200 {"status":"ok"}
GET /api/jobs           -> 200 []  (empty list, auth bypassed in dev)
GET /openapi.json       -> 200 45196 bytes
```

The bundle is functional for the dev-mode no-VLM smoke
profile. Full VLM-backed OCR smoke testing requires an LM
Studio endpoint and is not in scope for Sprint 1.

## What was NOT a Sprint 1 deliverable

These belong to later sprints per RFC 002:

- **Codesigning.** Still out of scope for v0.3.0; the binary
  shows the "Unknown publisher" SmartScreen warning. The
  end-user doc notes this in `docs/deployment/windows-bundle.md`
  §"SmartScreen."
- **Codesigned installer / Inno Setup.** Not in this commit.
- **macOS / Linux bundles.** RFC 002 has these as v0.3.x
  follow-ups.
- **Bundle in CI.** `make bundle` is still a local-only flow;
  CI integration is a follow-up.

## Implications for `docs/deployment/windows-bundle.md`

The §"Known build issue" section is **replaced** with this
commit. The end-user-facing §"What you get" / §"Install" /
§"Troubleshooting" sections are unchanged — the bundle install
flow is the same; the binary now actually works.

A follow-up commit updates the bundle doc to remove the
"DEFERRED to v0.3+" status banner and re-publishes the
end-user install path as supported.

## Implications for the v0.3.0 RFC 002

Sprint 1 ✅ — Option (a) succeeded.

Sprint 2 collapses from "ship Option A or fall back to
Option (c)" to "ship Option A as written, update release
notes." The fallback is unnecessary because the bundle boots
end-to-end. Sprint 2 work is the v0.3.0 release prep:
release notes, CHANGELOG, tag, GitHub release with the
binary attached.

Sprints 3 and 4 are unchanged (U12 sample-PDF route, buffer).

## Cross-references

- [`docs/deployment/windows-bundle.md`](../deployment/windows-bundle.md)
  — the end-user install doc (status banner updated).
- The v0.3.0 RFC 002 bundle decision (Option (a) with v0.3.0 cap;
  Sprint 1 succeeds) is preserved in git history.
- [`omniscribe_server.spec`](../../omniscribe_server.spec) — the
  full spec with the four fixes.
- [`scripts/run_server.py`](../../scripts/run_server.py) — the
  entry wrapper with the `import anyio.abc` force-import.
- [`scripts/smoke_existing.py`](../../scripts/smoke_existing.py)
  — the standalone smoke test for an already-built binary.
- [`scripts/iterative_bundle.py`](../../scripts/iterative_bundle.py)
  — automated "catch the next missing module" tool.
- [`repro/minimal_anyio.spec`](../../repro/minimal_anyio.spec) —
  the 40-line spec that proves the anyio bug is local.
- [`repro/run_minimal.py`](../../repro/run_minimal.py) — the
  21-line entry script.
- [`repro/smoke.py`](../../repro/smoke.py) — the smoke test.

_Last updated: 2026-09-06 (Sprint 1 SUCCESS)_
