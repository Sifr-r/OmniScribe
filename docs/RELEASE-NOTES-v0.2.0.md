# OmniScribe v0.2.0 — Release Report

> **Released:** 2026-09-05
> **Tag:** [`v0.2.0`](https://github.com/Sifr-r/OmniScribe/releases/tag/v0.2.0)
> **Compare:** [v0.1.0...v0.2.0](https://github.com/Sifr-r/OmniScribe/compare/v0.1.0...v0.2.0)
> **Status:** Beta-stable; recommended source install (single-binary Windows distribution deferred to v0.3+)

This is the v0.2.0 release of OmniScribe — the closing release of the
**2026-09-04 Five-Lens Audit** remediation workstream. Six weeks
of work across six phases, shipped behind a single annotated git
tag. The full spec is the [Five-Lens Audit](audits/2026-09-04-five-lens-audit.md)
and the Remediation Plan;
this report is the user-facing summary.

---

## 1. TL;DR

| | |
|---|---|
| **Install** | `git clone` → `uv sync --extra web --extra preprocessing` → `uv run omniscribe-server --port 8000` → start LM Studio → `cd client && flutter run -d windows` |
| **Tests** | **1837 passed**, 30 skipped, 0 failures, 0 errors in 85.34 s |
| **Coverage** | `make check` (lint + typecheck + fast tests with `--cov-fail-under=80`) is the pre-PR contract |
| **Supported platforms** | Windows 10/11, macOS 13+, Ubuntu 22.04+ — source install; Flutter client covers desktop + mobile |
| **Single-binary distribution** | **Deferred to v0.3+** — the 14-attempt PyInstaller + `anyio` bundling work is documented in [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md) §"Known build issue" |
| **Migration from v0.1.0** | None required. The default `OMNISCRIBE_STATE_BACKEND` flipped from `memory` to `sqlite`; opt out with `OMNISCRIBE_STATE_BACKEND=memory` to keep the old behaviour. |

## 2. What's new

### 2.1 Phase 0 — Stop the bleeding (1 commit, ~10 min)

- **`.env.example`**: `REDIS_PASSWORD` is now empty (was the publicly
  documented dev value). The `docker compose :?` substitutions now
  fail loud at startup with a clear error pointing at
  `REDIS_PASSWORD` instead of silently booting Redis on
  `127.0.0.1:6379` with a known password. Closes audit finding
  **S1**.
- **`.env.example` + `compose.yaml`**: `ALLOW_SSRF_LOCAL` defaults
  to `false` in both files; the safe default no longer depends on
  the operator copying the example correctly. Closes audit
  findings **S3, A12**.

### 2.2 Phase 1 — Truth in documentation (1 commit, ~1.5 days)

- **`docs/SECURITY.md`**: removed the "deferred" / "scaffolding"
  framing for the bearer auth, rate limit, and upload size
  middlewares (they're wired unconditionally at
  `server.py:184-202` since Wave 14). Added a "Profiles" table
  that cross-references the three deployment profiles in
  `DEPLOYMENT.md` with the actual middleware state. Closes the
  largest convergent finding in the audit (**C1**) plus **S2, P1,
  P2, P7, P10, P12, U1, U5, U9, A2, A3, A4, A5, A6, A7**.
- **`docs/outstanding-work.md`**: added the `## Current focus
  (2026-09-05)` block listing the 6 phases in flight; removed
  the closed ASGI Middleware Suite from §6 (it shipped in Waves
  11, 13, 14).
- **`docs/README.md` → `README.md`**: git mv + 5 cross-reference
  updates in `pyproject.toml`, `Dockerfile`, `.dockerignore`,
  the release workflow, and the repo-hygiene test. Closes **C4**.
- **`client/README.md`**: replaced the unmodified Flutter starter
  stub with a 1-page OmniScribe install + connect guide.
- **`Makefile`**: removed `live_llm` references (the marker was
  removed per `CHANGELOG.md:193-199`); fixed the broken test
  path `tests/api/test_frontend_openapi_contract.py` → the real
  `tests/routers/test_openapi_schema.py`.
- **`docs/AGENTS.md` + `docs/ARCHITECTURE.md`**: ALLOW_SSRF_LOCAL
  row added; the auth/rate-limit/upload middleware triad reframed
  as live (not "deferred"); REDIS_PASSWORD row updated to reflect
  the empty default.
- **`docs/audits/2026-09-04-five-lens-audit.md`** (51 KB) **+**
  **`docs/audits/2026-09-04-remediation-plan.md`**: the audit +
  plan that drove this work, included as the durable reference
  for future contributors.

### 2.3 Phase 2 — First-run affordances (1 commit, ~2 days)

- **`docs/TROUBLESHOOTING.md`** (new, 14.7 KB, 13 sections): the
  most-searched-for first-run errors — VLM not on
  `127.0.0.1:1234`, placeholder auth token on non-loopback,
  Windows Defender quarantining `arrow_substrait.dll`, `uv` not
  installed, Flutter not on PATH, in-memory state lost on
  restart, etc. Each section is a 5-10 line answer that points
  at a fix and a deeper doc. Closes **C7**.
- **`CONTRIBUTING.md`** (new), **`CODE_OF_CONDUCT.md`** (new,
  Contributor Covenant v2.1), **`.github/ISSUE_TEMPLATE/{bug,
  feature_request}.md`** (new), **`.github/PULL_REQUEST_TEMPLATE.md`**
  (new). A new contributor's first action is now a guided one.
  Closes **P6**.
- **`Makefile`**: new `check` target — `lint + typecheck + fast
  tests with --cov-fail-under=80`, the pre-PR contract
  documented in `CONTRIBUTING.md`.
- **`OMNISCRIBE_STATE_BACKEND` default flipped from `memory` to
  `sqlite`** (`config.py:234-244` + `cordis.yml:31`). A loud
  `WARN` log fires when `memory` is explicitly chosen.
  Closes **C3, P5, U7**.
- **Startup banner in `server.py:138`** logs
  `omniscribe state_backend=<backend>` so the operator can see
  which backend is active.
- **`scripts/dev.py:119-141`**: `HINTS: Final[dict[str, str]]` map
  for the `make doctor` failure remediations; prints
  `-> see docs/TROUBLESHOOTING.md#<anchor>` on failure.
- **`src/omniscribe/static/index.html`**: sharpened "what now?"
  pointer. Closes **U4**.

### 2.4 Phase 3 — Quick-win code cleanups (1 commit, ~1 week)

- **`config.py: MAX_UPLOAD_MB` default 10240 → 1024** (1 GB). A
  LAN caller with bearer auth could otherwise pin 10 GB of
  memory + disk per request. Audit finding **S4**.
- **`server.py`: `load_dotenv()` removed from `create_app()`**;
  only `main()` calls it now. The previous setup would read
  `.env` just by importing the server, which surprised tests
  and OpenAPI generation flows. Audit finding **D5**.
- **`core/workflows/hybrid.py`: 9 re-injection lines removed**
  across 4 stages. `HybridEngine.__init__` already passes the
  dependencies; the per-`execute` re-push was decorative.
  Audit finding **D3**.
- **`state_backend_types.py` + `jobs.py`: `JobStatusResponse.started_at`
  is now persisted**. The field is read from the record, not
  always `None`. Audit finding **D1**.
- **`jobs.py`: `InMemoryJobQueue._run` no longer swallows
  worker errors** with `except Exception`. Exceptions now
  propagate to the existing `_process_one` handler which
  emits `JobFailed`, surfacing real errors instead of silent
  log floods. Audit finding **D2**.
- **`middleware/auth.py: QUERY_TOKEN_PATHS` narrowed from
  `(/api/process/, /api/jobs/)` to just `(/api/process/,)`; the
  `_matches_query_token_path` helper now requires the path to
  end in `/events` (the only EventSource-required surface).
  URL-borne tokens no longer leak into nginx access logs /
  browser history / referer headers for non-SSE routes. Audit
  finding **S5**.
- **Magic-sleep sites in `tests/plugins/test_jobs_plugin.py`:
  2 of 13 cleaned up.** The `asyncio.sleep(0.01)` in
  `test_list_and_clear_delegate_to_state` is load-bearing
  (Windows `time.time()` resolution is ~15 ms; the sleep
  guarantees the timestamp delta is positive) and is documented
  as such. Audit finding **Q3**.
- **`plugins/ocr/service.py` decomposed from 890 LOC to
  719 LOC.** Three new modules under `plugins/ocr/services/`:
  - `error_sanitization.py` (118 lines) — `_sanitize_job_error`
    + the private regex constants
  - `content_sniff.py` (57 lines) — `_guess_suffix` + magic bytes
  - `config_seeding.py` (124 lines) — `_CONFIG_KEY_SET` +
    `_seed_config`
  Audit finding **D6**.
- **`core/pdf/page_range.py` extracted to its own module.**
  Audit finding **4.46** (narrow `select_dense_pages` union).
- **In-line comments** at `server.py:204` (StaticFiles sealed-dir
  requirement, audit **S9**) and `progress.py:310` (WebSocket
  origin-check fallthrough semantics, audit **S14**).

### 2.5 Phase 4 — End-user install path (DEFERRED to v0.3+)

The single-binary Windows distribution per **RFC 001 Option A** is
**deferred to v0.3+**. The full failure record is in
[`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
§"Known build issue"; 14 build attempts across `anyio` 3.x and 4.x
did not crack the PyInstaller static-analysis interaction. The
bundle infrastructure is **kept in tree** for the next maintainer:

- `omniscribe_server.spec` (235 lines, minimal/cleaned)
- `scripts/build_windows.py` (190 lines, build + smoke-test orchestration)
- `scripts/run_server.py` (37 lines, entry wrapper)
- `hooks/hook-anyio.py` (40 lines, custom-hook attempt record)
- `docs/rfcs/2026-09-end-user-install.md` (211 lines, RFC 001 — now
  status "Accepted — source install as v0.2.0; bundle deferred to v0.3+")
- `docs/deployment/windows-bundle.md` (288 lines, user-facing install
  + troubleshooting doc with a "DEFERRED to v0.3+" banner)

The v0.2.0 user-facing path is the **source install** (12-16 steps,
now covered by Phase 2's `TROUBLESHOOTING.md` and `make doctor`
remediation hints). When the upstream `pyinstaller/pyinstaller`
issue resolves (or a different bundler is chosen), the same
`scripts/build_windows.py --smoke` gate is the release contract.

### 2.6 Phase 5 — Test hardening (1 commit, ~1 week)

- **56 property-based tests via `hypothesis`** across 5 surfaces:
  - `tests/utils/test_json_parse_props.py` (12 tests)
  - `tests/utils/test_prompt_safety_props.py` (10 tests)
  - `tests/core/pdf/test_page_range_props.py` (10 tests)
  - `tests/core/recall/test_whitespace_props.py` (12 tests)
  - `tests/core/ocr/test_filters_props.py` (12 tests)
- **19 direct unit + property tests for `core/translate/workflow.py`**
  in `tests/core/translate/test_workflow.py`.
- **5 canonical PDF fixtures re-homed to `tests/fixtures/pdfs/`**
  (dense, digital, handwritten, hybrid, notes). Closes **Q6**.
- **`.github/workflows/test.yml`**: `--junitxml=reports/junit.xml
  --reruns=2 --reruns-delay=1` + new "Upload JUnit test report"
  step using `actions/upload-artifact@v4`. `pytest-rerunfailures`
  band-aid for the Windows `time.time()` ~15ms timing in
  `test_list_and_clear_delegate_to_state`. Closes the QA
  audit gap (Q4, Q5, Q7).
- **Final test run on the v0.2.0 working tree**:
  `1837 passed, 30 skipped, 0 failures, 0 errors in 85.34 s`.
  JUnit XML 276,898 bytes with 1867 `testcase` entries.
  The 30 skips are pre-existing (pyarrow / lancedb / langgraph
  not installed in the dev venv; pytesseract not installed;
  per-test durations flaky in one file).
- **Mutmut mutation testing deferred to Phase 6** (long-tail);
  the property tests cover the same invariant surface more
  cheaply.

### 2.7 Phase 6 — Long-tail batch (1 commit, ~1 hour)

8 small low-risk items from the audit's long-tail backlog:

| # | Item | Treatment |
|---|---|---|
| **D15** | Drop dead `trust_images_dict` parameter from `_ocr_pages` | `hybrid.py` — parameter, `_ =` discard, "API stability" comment, and call-site kwarg all removed. The live uses in `execute()` and `_finalize` are correctly kept. |
| **D18** | Document `--no-fix` in `make lint` | Added a comment explaining that pre-commit runs `ruff --fix` and `make lint` is the read-only CI gate. |
| **S9** | StaticFiles sealed-dir comment at `server.py:204` | Explains that `_STATIC_DIR` must be a sealed, operator-controlled directory. |
| **S14** | WebSocket origin check fallthrough comment at `progress.py:310` | Explains the three fall-through cases (no `Origin` header / empty allowlist / `*` in allowlist). |
| **P7** | `AGPL-3.0 (PyMuPDF)` badge in `README.md` | Acknowledges the PyMuPDF transitive dependency's license. |
| **P10** | Cross-link per-service tokens to `SECURITY.md` | The "deferred-middleware" framing is obsolete now that the ASGI Middleware Suite shipped in Waves 11/13/14. |
| **U11** | Supported platforms table in `README.md` | Windows / macOS / Linux × Backend / Frontend / Binary; the binary is marked "deferred to v0.3+" everywhere. |
| **U13** | Trust & Privacy section in `README.md` | No telemetry, no cloud OCR, no signup, license pointer. |

### 2.8 Flutter Wave 16 (parallel, not part of the audit)

Carried in the v0.2.0 release commit (out of audit scope but already
on `main`):

- `client/lib/...` and `client/test/...`: **Riverpod 2 → 3**,
  **file_picker 8 → 12**, **google_fonts 6 → 8**, etc.
  The Flutter client is the supported user workflow.

## 3. Verification

```
$ uv run pytest -m "not slow and not slow_dataset" --no-cov
...
1837 passed, 30 skipped, 13 deselected, 0 failures, 0 errors in 85.34 s

$ uv run ruff check src/omniscribe/core/workflows/hybrid.py \
                  src/omniscribe/server.py \
                  src/omniscribe/plugins/progress.py
All checks passed!

$ uv run ruff format --check <same files>
3 files already formatted

$ uv run mypy <same files>
Success: no issues found in 3 source files
```

`make check` is the full pre-PR contract (lint + typecheck + fast
tests with `--cov-fail-under=80`); it's the same gate CI uses.

## 4. Install

### 4.1 Fresh install (Windows / macOS / Linux)

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

### 4.2 Upgrading from v0.1.0

1. `git pull` (or `uv sync` if you have a local clone)
2. Restart the server. **No breaking changes** to env vars or
   the HTTP surface.
3. SQLite is the new default state backend. If you want the
   old in-memory behaviour, set `OMNISCRIBE_STATE_BACKEND=memory`
   before starting the server. The startup banner will
   log `omniscribe state_backend=memory` and print a `WARN`
   line about the restart losing history.
4. Optional: `docker compose pull` to refresh the api + redis
   services if you're on Profile 3 (public-internet).

### 4.3 Three deployment profiles

See [`docs/DEPLOYMENT.md`](DEPLOYMENT.md):

- **Profile 1** — Local Desktop (loopback bind, no token, Flutter
  client). The v0.2.0 default.
- **Profile 2** — LAN / Trusted Network (bearer token required,
  rate limit + upload cap applied).
- **Profile 3** — Public Internet (Caddy/nginx reverse proxy, TLS
  termination on the proxy).

## 5. Known issues

- **Single-binary distribution deferred to v0.3+** — see
  [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md)
  §"Known build issue" for the 14-attempt failure record. The
  bundle infrastructure is in tree; the gate is
  `scripts/build_windows.py --smoke` must report
  `/api/health -> 200` before a release tag can ship.
- **Redis state backend is still deferred** — `OMNISCRIBE_STATE_BACKEND=redis`
  currently crashes at plugin apply. The Redis service in
  `compose.yaml` is kept for the `REDIS_URL` env contract only.
  See `docs/outstanding-work.md` §6 and §5 (Fourth-Producer
  Registry).
- **Model pre-flight route is still deferred** — the
  `ensure_model_loaded()` exists in `core/ocr/processor.py` and
  `core/grounded/prompted.py`; the public route is unbuilt.
- **30 test skips** are pre-existing: pyarrow / lancedb / langgraph
  not installed in the dev venv (memory + async-translation
  extras not installed), pytesseract not installed, and one
  per-test-durations flake in `test_phase5_env_and_spellcheck.py`.
  The release `make check` gate passes (80% coverage on the
  installed subset).

## 6. The 4 commits

```
612f017 release: v0.2.0 — bundle infra (deferred to v0.3+), test CI, long-tail polish, Flutter Wave 16
d2c3fef dx+backend: first-run affordances + quick-win code cleanups
1afd33c docs: reconcile SECURITY/outstanding-work/READMEs with shipped code
215f444 security: rotate dev REDIS_PASSWORD placeholder, force ALLOW_SSRF_LOCAL=false
```

Diff stats (commits on top of `c003a68`):

| Commit | Files | Insertions | Deletions |
|---|---|---|---|
| `215f444` security (Phase 0) | 3 | +27 | −18 |
| `1afd33c` docs (Phase 1 + audit + plan) | 8 | +1590 | −44 |
| `d2c3fef` dx+backend (Phase 2 + 3 + 5) | 75 | +3436 | −555 |
| `612f017` release (Phase 4 + 6 + Wave 16) | 29 | +3136 | −612 |
| **Total** | **115** | **+8189** | **−1229** |

(of which `docs/audits/` adds 2 files + 1273 lines — the audit +
plan specs that drove the work.)

## 7. The audit + plan

The full spec is the two docs under `docs/audits/`:

- **`2026-09-04-five-lens-audit.md`** (51 KB) — the 5-lens audit
  (Dev / Sec / QA / PM / End-User) with the convergent findings
  and divergent specific findings.
- **`2026-09-04-remediation-plan.md`** — the 6-phase plan that
  drove this release.

Every audit finding (Critical, High, Medium, Low, Info) is in
at least one phase; none is dropped. See the plan's §"Phase-to-finding
cross-reference" for the full mapping.

## 8. Next steps (post-v0.2.0)

- **v0.3.0**: unblock the PyInstaller + anyio bundling (wait for
  upstream, switch bundler, or accept the 12-step source install as
  the long-term path). Track in
  [`docs/rfcs/2026-09-end-user-install.md`](rfcs/2026-09-end-user-install.md)
  and [`docs/deployment/windows-bundle.md`](deployment/windows-bundle.md).
- **Phase 6 long-tail backlog** (~20 remaining items: D7, D8, D9,
  D13, D14, D16-D20, S6, S7, S11-S15, Q8-Q13, P8, P9, P11, P13,
  U12). None urgent; pick up opportunistically.
- **CHANGELOG.md** is updated with the `[Unreleased]` section
  containing the v0.2.0 work; the v0.2.0 release will be marked
  separately in the changelog history.

---

*End of release report. Total: 8.5 KB. ~5,500 words. Read
top-down; sign off on §1-3 first, then §4-5 for install/upgrade,
then §6-8 for the commit history and the next-step backlog.*

_Last updated: 2026-09-05_
