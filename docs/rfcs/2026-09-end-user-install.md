# RFC 001 — End-User Install Path for OmniScribe

| Field | Value |
| --- | --- |
| **Author** | Mavis (synthesized from the 2026-09-04 [Five-Lens Audit](../audits/2026-09-04-five-lens-audit.md) and [Remediation Plan](../audits/2026-09-04-remediation-plan.md)) |
| **Status** | **Accepted — source install as v0.2.0; PyInstaller bundle deferred to v0.3+** |
| **Target** | v0.2.0 source install (live now); v0.3+ PyInstaller bundle (blocked on PyInstaller + anyio; see `docs/deployment/windows-bundle.md` §"Known build issue") |
| **Audit refs** | U2, U3, U4, U6, C2 |

## Problem

The audit's end-user lens is unambiguous: a non-developer cannot install OmniScribe today. The shortest path is 12–16 steps (clone → install Python 3.11+ → install `uv` → `uv sync` with multiple `--extra` flags → install Flutter SDK → `flutter pub get` → `flutter run` → start LM Studio → download a vision model → drop a PDF in). Five of five lenses flagged something in the install-path cluster.

The product is currently shippable as a **local single-user research tool** for someone who is already a Python + Flutter developer and runs LM Studio. It is not shippable as a v1.0 to a non-technical audience.

This RFC picks a distribution shape so the Phase 4 sprint can land it.

## Options

### Option A — PyInstaller bundle of the FastAPI server

A single-file `omniscribe-server.exe` (Windows) / `.app` (macOS) / `.AppImage` (Linux) that bundles the FastAPI server + all Python dependencies (torch, surya-ocr, pymupdf, etc.). The user downloads one binary, runs it, and the server starts on `http://127.0.0.1:8000`. The Flutter client is downloaded separately from the GitHub release page.

- **Bundle size:** ~1.0–1.5 GB per platform (torch + surya-ocr dominate).
- **First-run:** ~3 seconds to extract + ~1 second to start.
- **CI build:** matrix of Windows / macOS / Linux in `.github/workflows/release.yml`; ~5–8 minutes per platform.
- **Maintenance:** re-bundle on every release; the PyInstaller spec is ~200 LOC.

**Pros**

- The smallest scope that meaningfully addresses U2/U3. One binary download is a one-line README change.
- API-only users (curl / scripts / programmatic use) can skip the Flutter client entirely.
- The bundle is just `python -m PyInstaller omniscribe_server.spec` — the build is reproducible from the existing `uv.lock`.

**Cons**

- Still 3 steps for the full experience: (1) download `omniscribe-server.exe`, (2) download the Flutter client release, (3) start LM Studio + load a model.
- The Flutter client install still requires `flutter pub get` + `flutter run` for development builds, or a separate Flutter release artifact. (A pre-built `client-windows.zip` would add one more CI job.)
- Torch + surya-ocr are large; the `.exe` is ~1 GB, which is a big download for a hobbyist on a slow connection.

### Option B — Flutter desktop build that embeds the server

A single Flutter desktop binary (Windows / macOS / Linux) that bundles the server as a side-car process. The Flutter app spawns the PyInstaller-bundled `omniscribe-server` on first launch, waits for `/api/health` to respond, then opens the Workstation screen.

- **Distribution:** one `.exe` / `.app` / `.AppImage` per platform that contains both the Flutter UI and the Python server.
- **First-run:** ~5–10 seconds (Python extraction + first VLM call).
- **CI build:** Flutter build + PyInstaller build + `flutter build windows --release` integration; ~10–15 minutes per platform.
- **Maintenance:** doubled — both Flutter and Python release artifacts must move in lockstep.

**Pros**

- One binary download. The 3-step install collapses to "download, double-click, point at LM Studio."
- The user-facing entry point matches the audit persona's mental model.
- The Flutter UI gets to surface server status (the static index.html stops being a dead end).

**Cons**

- Real engineering cost. Flutter can't run Python directly; the server is shipped as a PyInstaller-bundle side-car.
- Cross-platform packaging (codesigning on macOS, MSI on Windows, AppImage on Linux) is the hard part. A single misstep and the binary won't run on a fresh box.
- The combined binary is bigger than either A or C alone (~1.5–2 GB).

### Option C — Standalone CLI (`pip install omniscribe`)

A `pyproject.toml`-published package that gives the user `omniscribe <file.pdf>` and `omniscribe-server` console scripts. Programmatic and CI-friendly; the smallest distribution.

- **Distribution:** PyPI; users run `pip install omniscribe` (or `uv tool install omniscribe`).
- **First-run:** ~30 seconds (depends on whether torch is already installed).
- **CI build:** a `release.yml` job that runs `uv build` and `twine upload` to PyPI.
- **Maintenance:** minimal — the existing `pyproject.toml` is already close to ready.

**Pros**

- Trivially scriptable. CI / batch jobs / power users adopt this with zero friction.
- The smallest artifact and the fastest install for anyone who already has Python.
- The audit's QA persona (developer with a CI pipeline) prefers this.

**Cons**

- Does **not** address the U3 / U4 / C2 findings. The non-developer persona still needs a Flutter install + a `pip install`.
- Loses the Flutter UI entirely. The audit's stated persona doesn't have a way to *use* the product without a UI.
- The PyPI story is open (`omniscribe` is a common namespace; a real release would need to negotiate that).

## Recommendation

**v0.2.0 = source install (Phase 2's `TROUBLESHOOTING.md` + improved `make doctor` are the user-facing win). v0.3+ = Option A (PyInstaller bundle) when the anyio bundling issue is unblocked.** Option B (Flutter-embedded) is a v0.4+ stretch.

The reasoning:

1. **The audit's pain is "12–16 steps," not "I want a single .exe."** Most of those steps are now documented in `docs/TROUBLESHOOTING.md` (13 sections, Phase 2.1) and `make doctor` now prints remediation hints to the right doc anchor (Phase 2.6). The first-run friction is dominated by **"what went wrong and where do I look"** — not by the install steps themselves.
2. **The PyInstaller bundle is blocked on a real upstream issue.** 14 build attempts (anyio 3.x and 4.x, custom hooks, force-imports, lazy-import workarounds) all produce a `omniscribe-server.exe` that boots Python but exits immediately on the first `import anyio.abc` with `ModuleNotFoundError: No module named 'anyio'`. The PYZ archive has 0 anyio entries. This is independent of anyio version; PyInstaller's static analyzer cannot follow anyio's package layout. See `docs/deployment/windows-bundle.md` §"Known build issue" for the full failure record.
3. **Switching bundlers (Nuitka) is a 2-3 day bet, not a guarantee.** The audit's recommendation is a product call, not an engineering one; given the personal-project scope (per the user profile memo), the cost-vs-benefit for a single-user install doesn't favor another bundler attempt.
4. **The infrastructure is reusable.** `omniscribe_server.spec`, `scripts/build_windows.py`, `scripts/run_server.py`, and `hooks/hook-anyio.py` are kept in tree. (hook-anyio.py was removed 2026-09-06 — the shipped fix is `collect_submodules("anyio")` in the spec; the hook was orphaned.) The moment PyInstaller's analysis recognizes anyio (or a different bundler is chosen), the smoke test gate (`scripts/build_windows.py --smoke` must report `/api/health -> 200`) is the same.
5. **The source install is a known quantity.** The 12-step install is what shipped in v0.1.0; the v0.2.0 improvements are the first-run affordances (Phase 2), not the install steps. A user who hit v0.1.0 install errors and gave up has a real reason to retry v0.2.0.

## Concrete steps for v0.2.0 (source install — the supported path now)

v0.2.0 ships the source install as the supported end-user path. The
first-run affordances from Phase 2 are the user-facing improvement.

1. **README install section** — already in place. `uv sync --extra web
   --extra preprocessing` + `uv run omniscribe-server --port 8000` is
   the 3-step backend path; the Flutter client is a separate `flutter
   run` workflow. No changes needed.
2. **`docs/TROUBLESHOOTING.md`** — added in Phase 2.1. 13 sections
   cover the most-hit first-run errors (VLM not on `127.0.0.1:1234`,
   placeholder auth token on non-loopback, Windows Defender
   quarantining `arrow_substrait.dll`, etc.).
3. **`make doctor`** — added in Phase 2.6. Runs Python version check,
   `uv` on PATH, Redis reachability, and the VLM endpoint reachable
   check. On failure, prints `-> see docs/TROUBLESHOOTING.md#<anchor>`
   so the next click is obvious.
4. **`docs/DEPLOYMENT.md` Profile 1** — clarified to say the Flutter
   client (not the browser) is the supported UI. The "open the
   browser" copy is removed.
5. **`client/README.md`** — rewritten as a 1-page install + connect
   guide; the Flutter starter is gone.

The user-facing v0.2.0 install journey is: (1) clone, (2) `uv sync
--extra web --extra preprocessing`, (3) `uv run omniscribe-server
--port 8000`, (4) start LM Studio, (5) `cd client && flutter run`. The
TROUBLESHOOTING.md is the first place to look when any of those
fails.

## Concrete steps for v0.3+ (PyInstaller bundle — deferred)

The PyInstaller bundle ships in v0.3+ when the anyio bundling issue
is unblocked. The infrastructure is in place; the gate is the smoke
test passing.

1. **Unblock the build** — pick one of: (a) wait for upstream
   PyInstaller to recognize anyio (track
   [pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller/issues)),
   (b) switch to Nuitka, (c) re-test after a future anyio major
   release. The minimal reproducer is the `omniscribe_server.spec` +
   the `anyio>=3.7,<4` pin in `pyproject.toml` + a clean venv.
2. **`bundle.yml` CI workflow** — matrix of Windows / macOS / Linux;
   ~5–8 minutes per platform. Triggered on `v*` tags.
3. **GitHub release artifacts** — `omniscribe-server-windows.exe`,
   `omniscribe-server-macos.app`, `omniscribe-server-linux.AppImage`
   + a `client-windows.zip` (Flutter release pipeline already
   exists).
4. **Smoke test gate** — `scripts/build_windows.py --smoke` must
   report `/api/health -> 200` before a release tag can ship.
5. **README install collapse** — the install section becomes a
   3-line "download the binary, start LM Studio, run the .exe" once
   the bundle is signed (codesigning is a separate v0.3+ decision).

## Concrete steps for Option A (v0.2.0, superseded)

1. **CI: add a `bundle.yml` workflow** that runs on a `v*` tag and produces three artifacts:
   - `omniscribe-server-windows.exe` (PyInstaller onefile, codesigned if the secret is configured)
   - `omniscribe-server-macos.app` (PyInstaller onefile, notarized if the secret is configured)
   - `omniscribe-server-linux.AppImage` (PyInstaller onefile + AppImage toolkit)
   The first two weeks are the build-system work; the bundle itself is the easy part.

2. **Spec file: `omniscribe_server.spec`** with the torch / surya-ocr / pymupdf hidden imports. Modeled on the existing `client/build` patterns.

3. **Distribution: a GitHub release** that the existing `release.yml` already creates. Attach the three artifacts to the release. Auto-bump the README install section to point at the latest release.

4. **Flutter client distribution:** for v0.2.0, ship a pre-built `client-windows.zip` as a second release artifact, alongside the server binary. The Flutter release pipeline already exists (`make build-client`); we just need to attach the output to the release. (For macOS / Linux, the Flutter `flutter build macos` / `flutter build linux` flow is one CI job per platform.)

5. **README:** the install section collapses to:
   ```bash
   # Windows: download omniscribe-server-windows.exe and the matching client zip
   # macOS:   download omniscribe-server-macos.dmg
   # Linux:   download omniscribe-server-linux.AppImage
   ```
   And the "Before you start" section (already in place from Phase 1) covers LM Studio + model selection.

6. **Backwards compatibility:** the source install (`uv sync --extra web --extra preprocessing`) keeps working. The bundle is a packaging option, not a replacement. Existing developer installs don't change.

## Decision (2026-09-05)

**Ship source install as v0.2.0; defer the PyInstaller bundle to v0.3+ when the anyio bundling issue is unblocked.**

The 14 build attempts documented in `docs/deployment/windows-bundle.md`
§"Known build issue" did not crack the PyInstaller + anyio static
analysis interaction. The source install is a known quantity (it
shipped in v0.1.0); the v0.2.0 user-facing win is the Phase 2
first-run affordances (TROUBLESHOOTING.md, `make doctor` hints,
SQLite default, CONTRIBUTING.md), not the install steps themselves.

The previously-offered alternatives are now historical:

- **(a) Option A as v0.2.0, Option B as v0.3.0** — superseded by
  the anyio bundling failure. The bundle infrastructure is in place
  and will ship as v0.3+ when the upstream issue resolves.
- **(b) Option B as v0.2.0** — not pursued; the Flutter-embedded
  approach would also depend on a working PyInstaller bundle as
  the side-car, so the anyio issue is on the critical path either
  way.
- **(c) Option C only** — `pip install omniscribe` is a small
  follow-up if there's interest; not part of v0.2.0.
- **(d) Punt** — partially chosen. The source install remains the
  v0.2.0 path; a "supported platforms" table is added to the
  README (Phase 6 U11) to make the U2/U3 limitation explicit.

## Open questions for the user

1. **Codesigning budget.** Codesigning a Windows `.exe` and a macOS `.app` (notarization included) costs $200–500/year in certs and ~2 days of CI plumbing per platform. Worth it for a personal project, or skip and document the SmartScreen warning?
2. **PyPI namespace.** `omniscribe` is a common-enough name that the PyPI slot may be taken or contested. Worth checking before the Option C sub-task.
3. **What's the realistic audience size?** If this is genuinely a personal project, the 12-step install is fine — the persona in the audit is hypothetical. If the user has actual non-developer friends who'd use it, Option A matters.

## Cross-references

- [Five-Lens Audit §4.5 — End-User lens](../audits/2026-09-04-five-lens-audit.md#4-lens-findings-1)
- [Remediation Plan §5 — Phase 4](../audits/2026-09-04-remediation-plan.md#phase-4--end-user-install-path-26-weeks-owner--desktop--devx)
- [`docs/DEPLOYMENT.md`](../DEPLOYMENT.md) — the three deployment profiles; Option A only changes Profile 1.
- [`docs/TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) — install-step troubleshooting; needs a "the bundled server won't start" entry once A is in the wild.

_Last updated: 2026-09-05_
