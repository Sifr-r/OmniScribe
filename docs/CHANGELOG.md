# Changelog

All notable changes to OmniScribe are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### User-visible changes

_See [v0.3.0](#030--2026-09-06) for the most recent release and
[docs/RELEASE-NOTES-v0.3.0.md](RELEASE-NOTES-v0.3.0.md) for the
full v0.3.0 release report._

- **2026-09-06 — U12 in-UI "try with sample PDF" affordance
  (Sprint 3, RFC 002 §4 Option b).** A new user has no PDF of
  their own to upload; the Workstation screen's empty-state
  header now has a **Try sample PDF** button that fetches a
  canonical fixture from the server's
  `GET /api/sample-pdf/{name}` route and stages it as the
  active document. The five fixtures — `digital.pdf`
  (default), `handwritten.pdf`, `hybrid.pdf`, `dense.pdf`,
  `notes.pdf` — are bundled into the binary
  (`src/omniscribe/resources/sample_pdfs/` is copied wholesale
  by the existing `DATAS` block; the PyInstaller spec picks up
  the new `omniscribe.plugins.sample_pdfs` plugin via
  `_CORDIS_PLUGINS`). The route is path-prefix-exempt in
  `middleware/auth.py` (Profile 1 loopback has no token). Path
  traversal is a structural impossibility — user input is
  never joined with a filesystem path; an unknown name
  returns 404. See the new `## "I just installed this — does
  it work?" (no PDF handy)` entry in
  [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) and
  [`docs/rfcs/2026-09-v0.3.0-scope.md`](rfcs/2026-09-v0.3.0-scope.md)
  §4 for the product call.

### Maintenance

- **2026-09-06 — Sprint 3 bundle smoke gate extended.** The
  `scripts/smoke_existing.py` boot-test now hits both
  `/api/health` and `/api/sample-pdf/digital.pdf` (the
  default fixture); both must return 200 before a release tag
  ships. The gate catches regressions in the Cordis plugin
  loader, the resources bundling, or the sample-PDF
  allowlist.
- **2026-09-06 — 15 new Python tests**
  (`tests/plugins/test_sample_pdfs_plugin.py`) and **5 new
  Flutter tests** (`test/data/sample_pdf_repository_test.dart`)
  + **4 new WorkstationNotifier tests**
  (`tryWithSamplePdf` group in
  `test/data/workstation_notifier_test.dart`). The Python
  tests cover: allowlist <-> fixture-set lockstep (test side
  and resource side), byte-for-byte return of every fixture
  (parametrized), 404 on unknown name, 4xx on path-traversal
  attempts, auth-bypass verification, and plugin singleton
  shape. The Flutter tests cover: allowlist lockstep, default
  fixture, route call, 404 propagation, document staging on
  success, error state on failure, and the in-flight-OCR
  guard.

## [0.3.0] — 2026-09-06

> **Single-binary Windows distribution ships.** v0.3.0 closes
> the 2026-09-04 five-lens audit's Phase 4 (single-binary
> distribution) end-to-end. The bundle is a 307 MB
> `omniscribe-server.exe` that boots, serves
> `/api/health -> 200`, `/api/jobs -> 200`, and
> `/openapi.json -> 200` on Windows 11. Plus the Phase 6
> long-tail batch 5 closeout (D9 mypy strict for
> `omniscribe.plugins.*` + `omniscribe.harness.*`; Q9
> calibration script determinism test). The full release
> report is at [docs/RELEASE-NOTES-v0.3.0.md](RELEASE-NOTES-v0.3.0.md).
> The Sprint 1 root-cause analysis that fixed the bundling
> failure is at
> [docs/rfcs/2026-09-bundle-sprint-1-findings.md](rfcs/2026-09-bundle-sprint-1-findings.md).
> The v0.3.0 plan is at
> [docs/rfcs/2026-09-v0.3.0-scope.md](rfcs/2026-09-v0.3.0-scope.md).

### Bundle & Distribution (Phase 4 — closed)

- **2026-09-06 — Single-binary Windows distribution ships.**
  307 MB `omniscribe-server.exe` boots and serves
  `/api/health -> 200` on a Windows 11 dev box. The
  14-attempt failure record in `docs/deployment/windows-bundle.md`
  was the predictable outcome of four local spec
  misclassifications in `omniscribe_server.spec`, not an
  upstream PyInstaller bug. Fix is five lines:
  - Remove `"anyio"` from `EXCLUDES` (was actively
    fighting `collect_submodules("anyio")` on the same
    file; EXCLUDES wins).
  - Add `collect_submodules("fastapi")` to
    `_RUNTIME_SUBMODULES` (was missing —
    `fastapi.staticfiles` was "not installed" on boot).
  - Remove `"pydantic-settings"` from `EXCLUDES` and add
    `collect_submodules("pydantic_settings")` (paired bug;
    EXCLUDES would have masked even adding the collect
    call).
  - Add `import anyio.abc  # noqa: F401` to
    `scripts/run_server.py` so the static analyzer follows
    the import edge that FastAPI / Starlette / uvicorn
    normally carry internally.
  - Add `"scipy._external.array_api_compat.numpy.fft"` to
    the manual hiddenimports block (a private
    underscore-prefixed submodule that
    `collect_submodules` skips by default; discovered
    automatically by the new
    `scripts/iterative_bundle.py`).
  See [Sprint 1 findings](rfcs/2026-09-bundle-sprint-1-findings.md)
  for the full root-cause analysis, the minimal reproducer
  in `repro/`, and the chronological fix log. The minimal
  reproducer proves the anyio part is local; the other
  three are well-known PyInstaller static-analysis gaps
  on deep ML stacks. Closes audit findings **U2, U3, U4,
  U6, C2**.
- **2026-09-06 — New `scripts/iterative_bundle.py`** (110 LOC):
  test-driven "catch the next missing module and add it"
  tool. Boots the binary, parses the first
  `ModuleNotFoundError` / `ImportError`, adds the missing
  module to the spec, rebuilds. Exits when the binary
  boots successfully. Useful for future maintenance if a
  new dep tree has a similar gap.
- **2026-09-06 — New `scripts/smoke_existing.py`** (80 LOC):
  standalone smoke test for an already-built binary.
  Boots, hits `/api/health`, asserts 200. Doesn't re-run
  `uv sync` (which is the part that hits Windows file
  locks during dev).
- **2026-09-06 — New `repro/` directory**: minimal 30-line
  spec + 21-line entry script + 50-line smoke test that
  proves the anyio bundling bug is local, not upstream.
  Tracked alongside the main spec so the next maintainer
  can re-run the minimal build if a regression hits.

### Maintenance

- **2026-09-06 — D9 mypy strict enabled for `omniscribe.plugins.*`
  - Remove `"anyio"` from `EXCLUDES` in `omniscribe_server.spec`
    (was actively fighting `collect_submodules("anyio")` on the
    same file).
  - Add `collect_submodules("fastapi")` to `_RUNTIME_SUBMODULES`
    (was missing — `fastapi.staticfiles` was in the bundle as
    "not installed").
  - Remove `"pydantic-settings"` from `EXCLUDES` and add
    `collect_submodules("pydantic_settings")` to
    `_RUNTIME_SUBMODULES`.
  - Add `import anyio.abc  # noqa: F401` to
    `scripts/run_server.py` so the static analyzer follows the
    import edge.
  - Add `"scipy._external.array_api_compat.numpy.fft"` to the
    manual hiddenimports block (private submodule that
    `collect_submodules` skips by default).
  Verified 2026-09-06: 307 MB `omniscribe-server.exe` boots,
  serves `/api/health -> 200`, `/api/jobs -> 200 []`, and
  `/openapi.json -> 200` (45 KB) on a Windows 11 dev box. See
  [Sprint 1 findings](rfcs/2026-09-bundle-sprint-1-findings.md)
  for the full root-cause analysis. The minimal reproducer
  (`repro/minimal_anyio.spec` + `repro/run_minimal.py` +
  `repro/smoke.py`) proves the anyio part is local; the
  other three are well-known PyInstaller static-analysis gaps
  on deep ML stacks. New `scripts/iterative_bundle.py`
  automates "catch the next missing module and add it" for
  future maintenance.
- **2026-09-06 — D9 mypy strict enabled for `omniscribe.plugins.*`
  and `omniscribe.harness.*`.** `pyproject.toml` adds a per-module
  override with `disallow_untyped_defs = true`,
  `disallow_untyped_calls = true`, and `check_untyped_defs = true`
  (matching the `omniscribe.core.*` override). 14 errors were
  reported on first run: 4 missing return-type annotations
  (`_progress_adapter`, `_warning_adapter`, `_cancel_check` in
  `plugins/ocr/service.py`; the inner `stream()` in
  `plugins/ocr/plugin.py`) and 10 `# type: ignore[no-untyped-call]`
  sites against `pymupdf`. The pymupdf stubs ship partial coverage
  for `fitz.open`, `get_pixmap`, `page_count`, `__getitem__`, and
  `close()` — 3 of the 10 `type: ignore` lines turned out to be
  unused and were removed, leaving 9 active. Final tally: 0 errors
  in 56 source files. See
  [`pyproject.toml`](pyproject.toml) §`[tool.mypy.overrides]`.
- **2026-09-06 — Q9 calibration script determinism test.** New test
  `test_seed_actually_controls_the_platt_split` in
  `tests/scripts/test_calibrate_model_script.py` pins the
  `scripts/calibrate_model.py` `--seed` contract: different seeds
  produce different `a` / `b` / `n_train` (the seed is actually
  consumed somewhere on the path), and the same seed is byte-for-byte
  deterministic on `n_train`, `n_test`, `a`, and `b`. The test would
  have caught a regression where the script "uses the seed once,
  then drifts" via ambient numpy state.

## [0.2.0] — 2026-09-05

> **Six-week workstream.** Closing release of the 2026-09-04
> Five-Lens Audit remediation. Six phases shipped end-to-end
> (Phases 0-3, 5, 6) with the single-binary distribution
> (Phase 4) deferred to v0.3+ pending an upstream PyInstaller +
> anyio bundling resolution. The full release report is at
> [docs/RELEASE-NOTES-v0.2.0.md](RELEASE-NOTES-v0.2.0.md). The
> audit + plan that drove this work are at
> [docs/audits/2026-09-04-five-lens-audit.md](audits/2026-09-04-five-lens-audit.md)
> and [docs/audits/2026-09-04-remediation-plan.md](audits/2026-09-04-remediation-plan.md).

### Audit & Remediation (2026-09-05 Five-Lens Audit Wave)

- **Audit Completion & Verification**: Executed and validated all actions from the
  2026-09-04 Five-Lens Audit (`docs/audits/2026-09-04-five-lens-audit.md`) and
  Remediation Plan (`docs/audits/2026-09-04-remediation-plan.md`).
- **Regression Resolution**: Fixed `EngineBase._reset_run_state()` in-place clearing
  to preserve `HybridOcrRunner.last_failed_pages` state tracking, added `argparse`
  support to `scripts/run_server.py`, synchronized `REQUIRED_TARGETS` in
  `tests/scripts/test_dev_commands.py`, and guarded optional `langgraph` async
  translation tests with `pytest.importorskip`.
- **Core & Server Cleanups**: Removed `load_dotenv()` side-effect from `create_app()`
  in `server.py`, hoisted `_DEFAULTS` in `processor.py`, optimized `extract_json` to
  single-pass `raw_decode`, tightened token masking in `config_store.py` for $\le 12$
  character keys, and switched sensitive logging redaction to exact-key matching.
- **Test Hardening (Phase 5)**: Added 5 property-based test suites using `hypothesis`
  covering `json_parse`, `prompt_safety`, `page_range`, `whitespace` recall, and
  OCR filters; added 18 direct unit and property tests for `core/translate/workflow.py`;
  and re-homed canonical PDF fixtures to `tests/fixtures/pdfs/`.
- **First-run & Documentation (Phases 1-2)**: Shipped `docs/TROUBLESHOOTING.md`,
  `CONTRIBUTING.md`, issue/PR templates, `make doctor` remediation pointers, and
  reconciled `SECURITY.md`, `README.md`, `client/README.md`, and `DEPLOYMENT.md`.

### Configuration

- **2026-09-05 — `OMNISCRIBE_MAX_UPLOAD_MB` default lowered from 10 GB
  to 1 GB.** `src/omniscribe/config.py` Pydantic default and the
  compose `services.api.environment` override both flipped to `1024`.
  The 10 GB default was generous enough that a LAN caller with bearer
  auth could pin 10 GB of memory and disk per request. The cap is
  enforced at upload parse and by `MaxUploadSizeMiddleware`; raise it
  explicitly for batch hosts. See
  [`docs/SECURITY.md`](SECURITY.md) §Security Features for the
  current contract.
- **2026-09-05 — Default state backend flipped from `memory` to
  `sqlite`.** `OMNISCRIBE_STATE_BACKEND` now defaults to `sqlite` in
  both `src/omniscribe/config.py` (Pydantic `Field` default) and
  `src/omniscribe/resources/cordis.yml` (YAML interpolation). Job
  records, artifact metadata, and progress-channel state now survive
  a server restart. Operators who explicitly want the in-memory
  behaviour can set `OMNISCRIBE_STATE_BACKEND=memory`; the server
  prints a `WARN` log line at boot to that effect. The full recovery
  story is in
  [`docs/DEPLOYMENT.md`](DEPLOYMENT.md#backup--recovery) and
  [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md#async-translation-result-is-gone-after-restart).
  Closes the long-standing PM/End-user finding that a server restart
  silently dropped async translation results.

### Security

- **2026-08-29 audit remediation — Sprint 5/H-5: compose.yaml redis digest pin**
  `compose.yaml` now pins `redis:7-alpine` to a verified OCI
  image-index digest rather than the floating tag, matching the
  Dockerfile `python:3.14-slim` pin (F2-1 hardening). Refresh with
  `docker buildx imagetools inspect redis:7-alpine` when bumping.
- **2026-08-29 audit remediation — C-3 / H-3: result token no longer leaks
  from the unauthenticated status endpoint** `JobStatusResponse` no longer
  carries `text_artifact_token` or `text_artifact_url`. The unauthenticated
  `GET /api/process/status/{job_id}` + `GET /api/jobs` chain previously let
  any caller fetch another user's OCR'd PDF by walking
  `list → id → token → /api/jobs/{id}/result`, defeating the constant-time
  gate at `fetch_result`. The async client now obtains the token
  out-of-band via the `job_completed` SSE event payload
  (`/api/process/{job_id}/events`), parallel to the sync path's
  `X-Text-Artifact-Token` response header. The Flutter client now
  resolves the token via `OcrRepository.getJobArtifactToken(jobId)`
  (Dio stream consumer that parses the `job_completed` SSE block) and
  `JobRepository.downloadResult(jobId)` performs the fetch — the
  `token` parameter is gone. New regression test:
  `test_status_response_never_returns_artifact_token` (server) +
  updated `client/test/data/job_record_test.dart` (asserts the model
  no longer surfaces `text_artifact_token` / `text_artifact_url`).

### Removed

- **2026-08-29 audit remediation — Sprint 6 / dead code cleanup**
  Two dead-code items from the audit-residual catalog (each was
  verified live-by-absence in the working tree before deletion).
  Two more catalog items that *looked* dead turned out to be live
  (`get_default_store` / `reset_default_store` in
  `core/lexicon/__init__.py` are called by
  `core/translate/workflow.py:104,109` and four test sites;
  `run_trust_scored_blocks` in `core/ocr_quality/__init__.py` is
  called by
  `tests/core/ocr_quality/test_ocr_quality_integration.py:15,36,57`)
  and stay.
  - `HybridEngine._collect_batched_images` wrapper
    (`core/workflows/hybrid.py`, 17 LOC) — no internal callers;
    tests go through `converter.collect_batched_images` directly.
  - `RuntimeSettings.chroma_db` field + `OMNISCRIBE_CHROMA_DB`
    validation alias + `chroma_db_path` property (`config.py`) —
    post-LanceDB migration; nothing in `src/` reads them. The
    migration-time `chroma_db/` detection in
    `core/lexicon/migration.py` is unrelated and stays.

### Refactor

- **2026-08-29 audit remediation — Sprint 6 / god-function splits**
  Four long functions (94–177 LOC) split into per-phase helpers.
  Each refactor is its own commit and reviewable; behavior is
  byte-identical (all existing tests pass without modification).
  - `core/ocr_quality/orchestrator.run` 177 LOC → 5 helpers
    (`_watermark_bbox`, `_script_detect_hints`,
    `_hallucination_risks`, `_calibrated_confidences`,
    `_compose_blocks`). The per-block script-hint cache is now
    closure-scoped to `_script_detect_hints`; default-arg binding
    for the per-block `_eval_block` / `_calibrate` closures is
    preserved.
  - `core/workflows/HybridEngine.execute` 122 LOC: the two
    between-phase cancel gates (post-`_convert_pages`,
    post-`_ocr_pages`) relocated into the next-phase helper
    (`_detect_layout`, `_finalize`). `execute()` is now a clean
    phase driver; the 122-LOC framing was misleading (most of the
    body is kwarg pass-through to the 5 phase helpers; the real
    orchestration is ~25 LOC).
  - `core/workflows/GroundedEngine.execute` 112 LOC: the
    overlay-build + `_build_document_result` + `_apply_trust` +
    `_emit` tail extracted into a single `_finalize()` helper.
  - `core/workflows/HybridEngine._repair_pages` 94 LOC → 2
    helpers (`_count_repair_targets`, `_repair_single_page`). The
    third catalog-suggested helper (`_emit_repair_summary`) was
    not extracted — it's a single-call site and the grounded path
    has the same inline block, so the dedup would require a
    cross-class helper (out of scope). The shared `completed`
    counter is carried via a single-element list (same pattern
    the orchestrator uses for `fallback_used_box`).

### Fixed

- **2026-08-29 audit remediation — Sprint 6 / audit residuals**
  Four long-flagged items from the 2026-08-29 audit catalog that
  the original Sprint 1–5 plan didn't close.
  - `core/ocr/resilience.py` `CircuitBreaker.__init__` now
    resolves `failure_threshold` / `cooldown_seconds` via
    `load_settings()` instead of raw `os.getenv` for
    `OMNISCRIBE_CB_FAILURE_THRESHOLD` / `OMNISCRIBE_CB_COOLDOWN`
    (audit L-1).
  - `utils/security.py` `_local_ssrf_allowed()` now reads
    `RuntimeSettings.allow_ssrf_local` instead of raw
    `os.getenv("ALLOW_SSRF_LOCAL", ...)` (audit L-1).
  - `core/translate/nllb.py` `NLLBEngine.translate` replaces
    `asyncio.get_event_loop()` (deprecated inside a coroutine)
    with `asyncio.get_running_loop()` (audit M-domain 4).
  - `core/grounded/prompted.py`
    `PromptedGroundedOCR.ocr_document` now awaits
    `asyncio.gather(*tasks, return_exceptions=True)` in the
    `finally` block instead of a bare `cancel()` loop. Closes the
    "Task was destroyed but it is pending" warning on shutdown
    (audit M-domain 5).
- **2026-08-29 audit remediation — Sprint 6 / P2 cleanup**
  Six P2 items from the catalog: dead `DocumentSpan` class,
  TODOS.md doc-drift references, two LanceDB push-downs
  (column-projection + WHERE), and two long-file splits
  (state_backend, ocr plugin). All behavior-preserving; the
  state_backend and ocr-plugin splits were each its own commit
  with conftest updates where the test monkeypatch targets
  moved.
  - Removed `DocumentSpan` class + `DocumentBlock.spans` field
    (`core/document.py`); `block_tree.Span` (the live
    rich-text inline-run class) stays. The one test that
    round-tripped the field was updated to drop the no-op
    passthrough. (The catalog's "or wire
    `pages_structured → DocumentBlock.spans`" alternative is a
    feature, not tech debt; out of scope for this sweep.)
  - Fixed two stale `TODOS.md` references (the file doesn't
    exist; the T9 spec and the 2026-08-14 design doc are also
    gone) in `core/recall/whitespace.py:70` and
    `tests/core/recall/test_text_recall.py:392`. The T7 harness
    at `scripts/measure_recall_delta.py` is a real script and
    stays referenced.
  - `core/lexicon/lancedb_store.py` `list_glossaries` now uses
    `self._table.to_pandas(columns=...)` so the storage layer
    reads only the 10 metadata columns it needs; the
    `embedding` column (the largest in the table) is not loaded
    into memory. Older `lancedb` versions fall back to the
    legacy `to_arrow() + select` path via a `TypeError` guard.
  - `core/lexicon/lancedb_store.py` `_hybrid_via_arrow` (the
    NumPy-fallback path) now tries
    `self._table.search().where(_build_where(query))` before
    materialising the full table; the remaining predicates
    (`enabled_only`, `glossary_ids`, anything `_build_where`
    rejected) still apply in Python via `_matches_query`.
- **2026-08-29 audit remediation — Sprint 6 / long-file splits**
  Two of the eight 500+ LOC files were split; the remaining
  six are flagged for a follow-up sweep (the splits are
  mechanical, but each needs its own conftest/touch-up pass).
  - `plugins/state_backend.py` (703 LOC) → frontend (Protocol +
    dataclasses + plugin) + `state_backend_memory.py`
    (140 LOC) + `state_backend_sqlite.py` (300 LOC). The
    `_MEMORY_BLOB_CAP_BYTES` constant moved into
    `state_backend_memory.py` (impl-specific). Public surface
    preserved; one test that patched the constant via the old
    import path was updated to the new module.
  - `plugins/ocr/plugin.py` (679 LOC) → frontend (Protocol +
    routes + plugin, 370 LOC) + `plugins/ocr/service.py`
    (340 LOC: `OCRServiceImpl` + the SSE event-formatting
    helper + the queue/event-name lookup tables). The pipeline
    bridge call sites (`build_pipeline`, `run_pipeline`) moved
    to `service.py`, so the two `fake_pipeline` test fixtures
    now also patch the new module.
  - `core/lexicon/lancedb_store.py` (770+ LOC after the Phase 3.3/3.4
    push-downs) — 80 LOC of stateless helpers (`_to_utc_datetime`,
    `_opt_str`, `_entry_from_row`, `_sql_escape`) extracted to a
    new `lancedb_helpers.py`. Pure leaf split: the helpers depend
    only on `datetime` and `.store.LexiconEntry`, no cycle. The
    remaining 705 LOC is the class definition; further splits
    (search methods as a mixin, admin/CRUD extraction) are
    flagged for a follow-up — the audit catalog overstated the
    refactor's reach, and the original `save_glossary` body has
    a non-trivial normalize-and-validate pipeline that's hard to
    reproduce by hand without the original test fixtures as the
    oracle.
- **2026-08-29 audit remediation — Sprint 6 / P3 cleanup**
  The four P3 catalog items (all S effort) closed in this batch.
  - Dropped the `_decode_chunk_bytes = decode_chunk_bytes` alias
    shim in `core/workflows/hybrid.py`. The two test sites that
    imported the alias now import the real function from
    `omniscribe.core.workflows.stages` (where it has always
    lived). The unused `decode_chunk_bytes` import in
    `hybrid.py` is also dropped.
  - Removed 3 import-guarded tests that silently inflated the
    test count (they used `pytest.importorskip("omniscribe.api")`
    so every test collected but did not run, since the
    `omniscribe.api` namespace was removed in the API rebuild):
    - `test_ocr_cancellation.py::test_route_returns_503_when_engine_raises_ocrcancelled`
    - `test_ocr_cancellation.py::test_route_builds_cancel_check_from_websocket_manager`
    - `test_translation_boundary.py::test_translation_base_imports_do_not_require_async_extras`
    Kept the positive guard
    `test_workflows_callback_decoupling.py::test_core_file_does_not_import_from_api`
    (it scans the tree for any future `from omniscribe.api` /
    `import omniscribe.api` regression). Net effect on the
    fast gate: skipped count drops from 26 → 23.
  - Dropped the `live_llm` pytest marker (`pyproject.toml:201`)
    which was declared but applied to zero tests
    (`pytest -m live_llm` was a no-op). AGENTS.md updated to
    remove the `pytest -m live_llm` line from the full-gate
    example block and the `live_llm —` bullet from the marker
    legend. `pytest --strict-markers` now passes (any future
    use of `@pytest.mark.live_llm` would error rather than
    silently no-op).
- **2026-08-29 audit remediation — Sprint 6 / Phase 4 long-file splits**
  Closed the remaining five 500+ LOC files flagged in the P2
  long-file split entry. Each is its own commit; all behavior
  preserved (1379 passed / 23 skipped / 6 deselected on the
  fast gate, identical to pre-Phase-1 baseline).
  - `core/pdf/embedder.py` (578 LOC) → 105 LOC public entry
    (header + `embed_structured_text` + worker pool) +
    `embedder_helpers.py` (470 LOC: the 15 underscored helpers
    for font chain, drawing, image-input branch, and page
    rasterization + module-level state like `_UNICODE_CHAIN` /
    `_EMBED_FONT` / `_PROBE_CODEPOINTS`). Pure leaf split; tests
    that monkeypatched the moved module-level state
    (`test_pdf.py` 3 sites) or called the helpers directly
    (`test_ocr_processor.py` 1 site) now import
    `omniscribe.core.pdf.embedder_helpers`. Pre-commit ruff
    format swept up 2 pre-existing drifts in
    `lancedb_store.py` and `ocr_quality/orchestrator.py`.
  - `plugins/providers.py` (535 LOC) → `providers.py` (120 LOC:
    routes + plugin + module-level `plugin =` + public-surface
    re-exports) + `providers_service.py` (430 LOC: catalog,
    request/response Pydantic models, SSRF helpers, Protocol,
    `ProviderManagerImpl`, `ProvidersSchema`). The tests that
    patched `omniscribe.plugins.providers.is_ssrf_target` (2
    sites in `test_providers_resolved_ip_pin.py`) now patch
    `omniscribe.plugins.providers_service.is_ssrf_target` (the
    call site moved with the impl).
  - `core/translate/workflow.py` (603 LOC) → `workflow.py` (225
    LOC: `TranslationState` schema + `get_translation_app` +
    `_LazyTranslationApp` + `chunk_text` + `run_translation` +
    node re-exports) + `nodes.py` (430 LOC: the 3 node
    functions, `should_refine`, `_state_settings`, prompt
    builder, JSON parser, system message constants). Tests
    patching `omniscribe.core.translate.workflow.call_llm` (3
    sites in `test_translation_boundary.py`) and
    `_llm_evaluate_translation` (11 sites in
    `test_translation_evaluator.py`) now patch
    `omniscribe.core.translate.nodes.*` (the call sites moved).
  - `core/ocr/processor.py` (672 LOC) → `processor.py` (~590
    LOC: `OCRProcessor` orchestrator with the prompt / tesseract
    / adaptive-threshold helpers) + `chat_client.py` (~150 LOC:
    `ChatClient` class owning the per-call retry loop, the
    circuit-breaker integration, the transient-vs-permanent
    error classification, and the context-length error
    translation). `OCRProcessor.__init__` now instantiates a
    `ChatClient` alongside the existing circuit breaker; the 4
    call sites in `perform_ocr` / `perform_ocr_on_crop` /
    `_run_trocr_arbitration` still call `self._chat(...)` but
    the method is a 1-line delegate to
    `self._chat_client.chat(...)` that also re-syncs the client
    breaker from `self.circuit_breaker` (so tests that swap the
    breaker on the processor take effect without rebuilding
    the client). The `OCRProcessor.__new__` legacy test path
    still works: tests that do `ocr._chat = fake_chat` override
    the wrapper method on the instance, which is what those
    tests were always trying to do. Tests patching
    `omniscribe.core.ocr.processor.call_llm` (8 sites across
    `test_ocr_resilience.py` + `test_ocr_trocr_integration.py`)
    now patch `omniscribe.core.ocr.chat_client.call_llm`.
  - `core/workflows/hybrid.py` (633 LOC) → `hybrid.py` (~510
    LOC: `HybridEngine` with the 4 stage delegators
    `_convert_pages` / `_detect_layout` / `_ocr_pages` /
    `_refine_pages` / `_finalize` + `__init__` + the LRU
    decoded-image cache) + `hybrid_repair.py` (165 LOC: the
    Phase 4b repair logic — `run_repair_phase` driver +
    `repair_single_page` + `_count_repair_targets` helper). The
    repair phase is the only phase with substantial bespoke
    logic (loop orchestration, per-page re-OCR, progress
    emission, shared completed-box counter) so it's the only
    phase that moved. The other 4 phases are pure pass-throughs
    to the stage classes and had to stay on `HybridEngine`
    (tests call `engine._convert_pages`, `_detect_layout`,
    `_ocr_pages`, `_finalize` directly). The engine surface
    the repair module depends on (`ocr_processor`,
    `block_callbacks`) is typed as a Protocol
    (`_RepairEngineHost`) so the contract is explicit without
    coupling the module to `HybridEngine`.

### Added

- Documents plugin (`plugins/documents/`): rebuilt the deferred extraction and export HTTP surface — `POST /api/extract`, `POST /api/export/document`, `GET|POST /api/export/docx`, `POST /api/export/html`, `POST /api/export/docx-tree`, `POST /api/export/blocktree`, token-bound `GET /api/export/{id}`, `GET /api/text/{id}`, `GET /api/metadata/{id}`. The Flutter client's extraction/export screens and text display work again; no client changes.
- Translate plugin (`plugins/translate/`): rebuilt the deferred translation surface — `POST /api/translate` (sync single-shot), `POST /api/translate/async` (tree-aware, dispatched on the harness JobQueue), `GET /api/translate/status/{job_id}`, `POST /api/translate/nllb`. Translated documents are stored as token-bound text artifacts. The broken Celery worker service was retired from `compose.yaml` (async translation no longer uses Celery).
- Transcribe plugin (`plugins/transcribe/`): `POST /api/transcribe` (sync multipart transcription with token-bound text + metadata artifacts), `GET/POST /api/config/transcription` (masked keys, always-writable in-memory store), `GET /api/models/transcription` (endpoint discovery with whisper fallback list).
- Glossary plugin (`plugins/glossary/`): rebuilt the 9-route glossary import/library surface. Imports accept the legacy JSON source envelope AND the client's multipart/JSON-body shapes; imports above the 5,000-entry estimate dispatch on the harness JobQueue (`GlossaryJobRunner`). The LanceDB lexicon store loads lazily — routes 503 with an install hint when the `lexicon` extra is missing.
- Translate: `GET /api/translate/result/{job_id}?token=…` — token-redeeming async result fetch (wrong token → 404; C-3/H-3 preserved).
- **2026-08-29 audit remediation — Sprint 3/H-4: AppButton keyboard accessibility**
  `client/lib/presentation/common/app_button.dart` now wraps the
  press target in a `FocusableActionDetector` with an
  `ActivateIntent` handler so keyboard users can Tab + Enter/Space to
  activate, not just mouse/touch. Disabled / non-interactive buttons
  short-circuit the callback.

### Fixed

- **2026-08-29 audit remediation — cumulative audit status**
  Sprint 1 (Core Pipeline), Sprint 2 (API & Security), Sprint 4
  (Testing & QA), and Sprint 5 (DevOps & Config) audit findings are
  all closed at the Critical and High severity levels. Sprint 3
  (Frontend) audit items are closed at Critical and High severity
  levels, with the remaining medium and low items tracked in the
  deferred Flutter-client backlog (axe-playwright coverage, full
  48 dp touch-target sweep, all keyboard shortcut bindings). The
  `ruff check src tests` + `ruff format --check` + `mypy src` +
  `pytest -m "not slow"` gates all pass (1378 passed, 26 skipped,
  6 deselected).
- **2026-08-29 audit remediation — Sprint 4/M-6: SSRF safety test async/await**
  `tests/utils/test_ssrf.py::test_ssrf_fails_closed_and_requires_explicit_local_allowance`
  now uses `await` instead of nested `asyncio.run` calls so the
  SSRF guard runs on the same event loop as the rest of the
  suite (pytest-asyncio auto mode). The async conversion catches
  timing/loop issues that the sync wrapper could mask (e.g. a
  half-closed ``socket.getaddrinfo`` thread-pool task between
  successive ``asyncio.run`` boundaries).
- **2026-08-29 audit remediation — Sprint 3/M-2: AppButton 48 dp minimum tap target**
  `client/lib/presentation/common/app_button.dart` now wraps the
  visual button in a `_MinimumTapTarget` (private widget) that
  constrains the hit area to at least 48 dp via `BoxConstraints`.
  The visible button keeps its design height (32/36/44 dp for
  sm/md/lg); the touch area extends invisibly. Regression test:
  `client/test/presentation/common/app_button_tap_target_test.dart`
  (3 sizes).
- **2026-08-29 audit remediation — Sprint 4/M-6: OCR test async/await**
  `tests/core/ocr/test_ocr.py` and
  `tests/core/grounded/test_grounded.py` convert the
  `asyncio.run(...)` call sites in `TestHallucinationFilter`,
  `TestEnsureModelLoaded`, and `TestPromptedGroundedEnsureModelLoaded`
  to `async def test_*` so the OCR processor and the
  `PromptedGroundedOCR.ensure_model_loaded` runs on the suite's
  event loop instead of spawning a fresh loop per test. Net
  effect: 22 sync `asyncio.run` calls removed; all 1378 tests
  still pass.
- **2026-08-29 audit remediation — Sprint 4/M-6: full async test refactor**
  Sprint 4 / M-6 audit fix: converted `asyncio.run(...)` to
  `await` across `tests/core/ocr/test_ocr_resilience.py` (8
  tests), `tests/core/ocr/test_ocr.py` (11 tests),
  `tests/core/grounded/test_grounded.py` (5 tests),
  `tests/core/translate/test_translation_tree.py` (4 tests),
  `tests/core/translate/test_dual_translator.py` (2 tests),
  `tests/core/processors/test_reading_order.py` (1 test).
  All 31 conversion sites use `pytest-asyncio` auto mode (no
  per-test decorator) so they share the suite's event loop. Two
  remaining `asyncio.run` sites are intentional: the subprocess
  script literal in `test_translation_boundary.py` exercises
  import isolation, and the slow-marked recall test is
  deselected by the fast gate.

### Rebuilt API on Cordis-style plugin harness
  `src/omniscribe/api/` package is replaced by a plugin-harness
  architecture: `src/omniscribe/harness/` (Context, Loader, Plugin
  base, LIFO effect disposal) plus nine boot plugins under
  `src/omniscribe/plugins/` (runtime, logging, state_backend,
  artifacts, jobs, progress, providers, health, ocr) declared in the
  shipped `resources/cordis.yml` tree. `server.py` loads the tree
  inside the FastAPI lifespan; patch files (`OMNISCRIBE_CORDIS_PATCH`
  or `<artifact_dir>/cordis.patch.yml`) and
  `OMNISCRIBE_PLUGIN_<ID>__<FIELD>` env overrides layer on top, and a
  malformed tree fails boot loud (`PluginLoadError`).

  Routes restored: `POST /api/process` (sync, PDF blob +
  `X-Text-Artifact-Id/Token` headers), `POST /api/process/async` +
  `GET /api/process/status/{job_id}` + SSE
  `GET /api/process/{job_id}/events`, `GET/DELETE /api/jobs` +
  `GET /api/jobs/{job_id}/result` + `POST /api/jobs/{job_id}/cancel`,
  `POST /api/progress/session` + `POST /api/progress/cancel/{channel_id}`
  + `WS /ws/{channel_id}` (first-frame auth, NDJSON),
  `GET /api/providers*`, `GET/POST /api/config` (+ `/api/config/ocr`
  aliases), and the health/readiness probes. The route surface is
  pinned by the regenerated `tests/openapi.json` snapshot.

  State-backend env contract: `OMNISCRIBE_STATE_BACKEND` accepts
  `memory` (default) or `sqlite` only; any other value fails boot.
  The SQLite backend persists jobs + artifacts across restarts
  (single WAL-mode file, default `<artifact_dir>/omniscribe-state.db`,
  override `OMNISCRIBE_STATE_DB_PATH`).

  Deferred to follow-up specs: translation / transcription /
  glossary-import / extraction+export routes, the auth / rate-limit /
  upload-size ASGI middlewares, Celery async dispatch, the Redis
  state backend, and model pre-flight (`GET /v1/models`). The route
  surface is currently unauthenticated — local trusted use only.

- **LanceDB-backed lexicon store (replaces JSON + ChromaDB)** — the
  canonical glossary / translation lexicon is now a single embedded
  columnar vector database (`omniscribe.core.lexicon.LanceDBLexiconStore`)
  with native hybrid (vector + SQL filter) queries. Replaces the prior
  two-system pair of `glossary_library/library.json` (JSON-on-disk) +
  `chroma_db/lanes_lexicon` (ChromaDB PersistentClient). The new
  `LexiconStore` Protocol is the single read/write surface; legacy
  callers route through `GlossaryLibraryAdapter`. Translation lookup
  is now a one-query hybrid (similar terms, in this language pair, in
  this domain, in this user's enabled glossaries) instead of a
  ChromaDB semantic search + a JSON side-lookup. Spec:
  `docs/lexicon-migration-spec.md`.

- **`omniscribe-migrate-lexicon` CLI** — explicit one-shot migration
  script for users who prefer a manual upgrade path. Supports
  `--dry-run` (plan without writing), `--verify-only` (read-only
  check of an existing migration), and `--artifact-dir <path>`. The
  server also auto-migrates on first run after the upgrade
  (fail-open — a broken migration never blocks boot; the user can
  retry with the explicit CLI).

- **Optional `source_lang` / `target_lang` on `TranslationState`** —
  the LangGraph translation flow now carries language pair hints
  through to the lexicon query so a glossary scoped to `en→fr` does
  not bleed into a `de→es` request. Populated by the translation
  route when known (request field, OCR document metadata, or
  inference); missing is fine — the store just skips the filter.

- **Whitespace recall booster (default ON)** — the hybrid pipeline now
  runs a secondary whitespace-masking discovery pass
  (`core/text_recall.py`) after Surya layout detection: binarize +
  invert, horizontal dilation, connected-component filtering, dedup
  against Surya boxes. Recovered line boxes join detection before
  dense selection, OCR, and DP alignment. Disable per process with
  `OMNISCRIBE_WHITESPACE_RECALL=0` (also `false`/`no`/`off`).
  Requires the `preprocessing` extra (`opencv-python-headless`);
  without it the pass logs one warning and stays inert.

- **`omniscribe-migrate-lexicon` exit-code fix** — the CLI no longer
  returns exit code 2 for a valid empty `lexicon.lance` after
  `--verify-only` (a fresh install or one with no glossaries is a
  successful verification, not a problem). Exit 2 is now reserved for
  `--strict` mode when the live store is empty but a backup manifest
  reports glossaries. Operators scripting `if
  omniscribe-migrate-lexicon --verify-only; then …` no longer see
  false-positive failures.

- **Plugin context infrastructure (Cordis-style container)** —
  `src/omniscribe/api/plugin/` introduces a Protocol-based plugin
  container with five seams (`JobQueue`, `SessionLog`, `ConfigStore`,
  `ProgressService`, `TextArtifactStore`), a runtime `get_<name>()`
  helper, a "look up by Protocol, fall back to singleton" migration
  window, and dual-write projections for `JobHistory` and the artifact
  stores. `OMNISCRIBE_PLUGIN_CONTEXT=1` enables it (default off; import-
  time-only toggle, no runtime flip). Two of the five seams are wired at
  boot (`JobQueue` → `local`, `SessionLog` → `memory`); the other three
  fall through to the legacy `api/routers/state.py` singletons by
  design. See `AGENTS.md` §"Plugin Context Migration Status" for the
  current state.

- **`document_exporters/` package** — the `core/document_exporters/`
  package is a thin `DocumentExportProtocol` + `BaseDocumentExporter`
  ABC. The three real exporters (DOCX, tree-DOCX, HTML) are
  co-located with the writers they wrap
  (`core/docx_writer.py`, `core/docx_tree_writer.py`,
  `core/html_writer.py`); the package ships only the abstraction.

- **A11y test additions (frontend)** — `frontend/src/__tests__/a11y.test.ts`
  plus the new `frontend/src/lib/utils/download.ts` / `__tests__/download.test.ts`
  cover the Svelte 5 component layer for accessible-name regressions
  and the new browser download lifecycle. `vitest-axe` /
  `@axe-core/playwright` integration is still pending (tracked
  separately).

- **Celery task unit tests** — `tests/test_distributed_ocr_tasks.py`
  covers the Celery worker side of the OCR pipeline for crash-safety
  and queue draining. Multi-worker / crash-safe dispatch remains a
  follow-up.

- **Document exporter tests** — `tests/test_document_exporters.py`
  pins the new `document_exporters/` abstraction's contract.

- **Phase-2 remediation tests** — `tests/test_phase2_remediations.py`
  bundles 7 fixes from the 2026-08-17 audit's Domain 1 / Domain 2
  close-out into one regression file (splitting per-finding is a
  follow-up; see `audits/2026-08-19-secondary-validation-pass.md` §F26).

- **Misc 2026-08-17 → 2026-08-19** — `tests/test_security_middleware.py`,
  `tests/test_token_deprecation.py`,
  `frontend/src/__tests__/auditMediumD3.test.ts`, and the
  `_PinnedIPTransport` regression test (D2-06 partial close-out) all
  landed in this window.

### Changed

- **`[memory]` extra renamed to `[lexicon]`** — the `chromadb` dependency
  is removed; the new extra installs `lancedb + pyarrow + pandas +
  sentence-transformers` instead. The `[memory]` name is kept as a
  one-release deprecation alias that installs the same set, so existing
  `omniscribe[memory]` users upgrade transparently. After this release
  the alias is dropped.

- **Install paths now include the `preprocessing` extra** —

- **Windows quick-start robustness (install.ps1 + start_app.vbs)** —
  the Windows one-click launcher no longer silently fails on
  re-launch. `start_app.vbs` now writes a timestamped log to
  `start_app.log` next to itself, pre-checks that `uv` is on PATH
  (pops a clear "log out so PATH updates" dialog if not),
  reuses the existing `redis-local-ocr` container via
  `docker start` or creates a new one with `--rm`, skips
  Redis + Celery gracefully if the Docker daemon is not
  reachable (async translation is the only thing that
  breaks), and polls `http://localhost:8000` until uvicorn
  actually responds (max 60 s) before opening the browser.
  `install.ps1` now wraps the `uv` installer in a try/catch,
  fails fast on `uv sync` errors via `$LASTEXITCODE`, runs
  `uv run python --version` to verify the venv is usable, and
  prints a clear "log out so PATH updates" callout at the end.
- **Speech transcription endpoint** — new
  `POST /api/transcribe` route plus
  `GET/POST /api/config/transcription` and
  `GET /api/models/transcription` for the transcription
  provider; gated by the new `OMNISCRIBE_TRANSCRIPTION_AUTH_TOKEN`
  env var (falls back to the global `OMNISCRIBE_AUTH_TOKEN`).
  Bypasses bearer auth on `/health`, `/healthz`, `/ready`,
  `/readyz` regardless of token configuration.
- **Quality repair loop (automatic low-confidence retry)** —
  engine-agnostic block-level quality retry in
  `core/workflows/repair.py` (`QualityRepairLoop` +
  `RepairOptions`). Blocks whose estimated confidence falls
  below the target are re-OCR'd crop-scoped (hybrid reuses
  refine's crop → `perform_ocr_on_crop` primitive; grounded
  goes through the backend's `ocr_crop`) up to
  `max_retries` times; a retry is accepted only while
  confidence strictly improves (stall guard), and any
  unexpected error fails open with the original text.
  `CircuitOpenError` is re-raised so the circuit breaker
  stays authoritative. Repair runs sequentially after block
  emission in both engines, before post-processing and
  embedding, so downstream stages always see the repaired
  text.
  - `OCRPipeline.run` accepts `repair_options=`; engines
    default **off** (`repair_options=None`) for in-process
    callers, while `/api/process` defaults **on** — upgrade
    note: expect up to `quality_max_retries` extra VLM
    passes per low-confidence block unless disabled.
  - Per-request form fields `quality_loop_enabled`,
    `quality_target` (0.5–1.0, default 0.98) and
    `quality_max_retries` (0–5, default 2) on
    `/api/process`; out-of-range values return 422.
  - Env seeds `OMNISCRIBE_QUALITY_LOOP` /
    `OMNISCRIBE_QUALITY_TARGET` /
    `OMNISCRIBE_QUALITY_MAX_RETRIES` (out-of-range values
    fall back to the defaults).
  - New WebSocket frames: `block_retry`, `block_revised` and
    `quality_summary` (job-level repaired-block count);
    progress accounting reuses the `refine` stage band.
- **System / user role split in OCR + translation prompts** —
  the canonical OLMOCR page prompt stays a pure user message
  (it was RL-trained on that exact string, so a system role
  would shift the distribution). For other code paths we now
  emit the role identity in a system message so the model
  doesn't have to compete with task content. New constants
  in `omniscribe.core.ocr.prompts`:
  - `OCR_SYSTEM_MESSAGE`, `HANDWRITING_OCR_SYSTEM_MESSAGE`,
    `DUAL_ENGINE_OCR_SYSTEM_MESSAGE`,
    `GROUNDED_OCR_SYSTEM_MESSAGE` — identity + diacritics
    emphasis + "no invent / emit empty on blank" guards.
  - `TRANSLATION_SYSTEM_MESSAGE` (sync + async paths) and
    `EVALUATION_SYSTEM_MESSAGE` (LLM-as-judge step) — both
    pin the "preserve URLs / identifiers / brand names"
    rule that local models otherwise helpfully mistranslate.
  - `EXTRACTION_SYSTEM_MESSAGE` — pins the
    "use `null` for missing fields, no markdown fences" rules
    so the model doesn't invent plausible values for absent
    fields.
  - `model_supports_system_role(model_name)` — the narrow
    OlmOCR exclusion list (see also the bug fix above).
  - `select_system_message(...)` and the new
    `_resolve_page_system` / `_resolve_crop_system` helpers
    on `OCRProcessor` are the single source of truth for
    "which system message goes with which call site".
  - `PROMPT_VERSION = "2026-08-15.v1"` per file. Bump on any
    user-visible prompt body change so log / runtime
    telemetry can correlate regressions with a known version.
  - The OlmOCR-2 canonical page-prompt body is **unchanged**
    and is locked by
    `test_olmocr_prompt_is_canonical` — the model was
    RL-trained on that exact string and any drift would cost
    OCR quality. The system-role plumbing is wired around
    it, never into it.
- **`scripts/debug_websocket_frames.py`** — Python WebSocket
  diagnostic that opens a real progress session, prints every
  incoming text frame as hex + UTF-8 + parse result, and
  writes a JSONL log. Use when a future regression looks
  like "mangled JSON in the browser console": run this
  alongside a real OCR job, and if every frame arrives with
  `parse_ok=true` the corruption is browser-side; if frames
  are already mangled on the wire, the issue is uvicorn /
  websockets.
- **Centralized LLM temperature constants**
  (`omniscribe.core.llm_temperatures`) — six named
  constants (`TEMPERATURE_OCR`, `TEMPERATURE_GROUNDED`,
  `TEMPERATURE_EXTRACTION`, `TEMPERATURE_EVALUATION`,
  `TEMPERATURE_TRANSLATION`,
  `TEMPERATURE_TRANSLATION_TREE`) replace the literal
  floats previously scattered across `core/ocr/processor.py`,
  `core/grounded/prompted.py`, `core/translation.py`,
  `core/translation_tree.py`, and `api/services/ai.py`. Each
  constant has a per-call-site rationale (e.g. OCR=0.1 lets
  the model escape degenerate-token traps without injecting
  real randomness; TRANSLATION_TREE=0.2 because the sliding
  window already constrains per-chunk variation). The
  values are deliberately **not** env-overridable — they
  are deployment shape, not user preference. Adding a new
  call site should pick an existing constant that matches
  the tolerance rather than invent a new float. (Issue 7)
- **Translation evaluator rubric + failure-mode block** —
  `build_evaluation_prompt` in `core/translation.py` now
  ships a 0–10 rubric (meaning preservation, terminology
  fidelity, fluency, format) and an explicit failure-mode
  checklist ("do not reward code-switching mid-sentence",
  "do not award a pass when brand names are silently
  translated") so the LLM-as-judge step stops rewarding the
  exact behaviors the rubric was supposed to penalize. (Issue 9)
- **Prompt input sanitization at the LLM boundary** —
  `sanitize_prompt_input` from `omniscribe.utils.prompt_safety`
  is now applied to every user-controlled text segment that
  reaches a prompt body: translation source chunks, structured
  extraction document text + custom prompt, evaluation
  source + translation, dual-engine / correction OCR draft
  text, and the translation tree chunk input. The helper
  neutralizes control characters and the prompt-injection
  markers most likely to make the model ignore the system
  message; it is applied at the prompt-builder level so a
  future call site can't forget. (Issue 11)
- **`_extract_prompt_and_image` simplified to a 2-tuple**
  — `core.ocr.multi_format_client._extract_prompt_and_image`
  dropped the legacy `(prompt, system_prompt, image)`
  3-tuple shape and the system-from-`messages` branch. The
  single source of truth for the system role is now the
  explicit `system_prompt` parameter on `call_llm` (routed
  through `model_supports_system_role` for OlmOCR family
  models). The previous dual path was not exercised by any
  production caller. (Issue 12)
- **SQLite-backed `StateBackend` (opt-in persistent
  state)** — `OMNISCRIBE_STATE_BACKEND=sqlite` activates
  :class:`SQLiteStateBackend` in
  `omniscribe.api.services.state_backend_sqlite`. Sits
  alongside the existing :class:`LocalStateBackend`
  (`memory`, the default — no behaviour change) and
  :class:`RedisStateBackend` (`redis`, requires a Redis
  server). The SQLite backend writes the three artifact
  tables (`omniscribe_artifact_text`,
  `omniscribe_artifact_meta`, `omniscribe_artifact_export`)
  and the jobs table (`omniscribe_jobs`) to a single
  SQLite file (default
  ``$OMNISCRIBE_ARTIFACT_DIR/omniscribe-state.db``;
  override with `OMNISCRIBE_STATE_DB_PATH`); artifact
  files themselves still live on disk in the existing
  artifact directory. WAL mode is enabled for concurrent
  readers + crash safety; the cap on
  `max_jobs` / `max_entries` is enforced via SQL on every
  write. `ProgressService`, `GlossaryLibrary`, and
  `OCRJobQueue` remain in-memory because they reference
  live WebSocket channels / RAG index state — see the
  module docstring for the "recovery boundary"
  explanation. The backend is the persistent opt-in for
  the local-first deployment shape; the Redis backend
  remains the answer when you need horizontal scaling
  across multiple uvicorn workers. New test module
  `tests/test_state_backend_sqlite.py` covers
  round-trip persistence, TTL/overflow enforcement, the
  per-instance monotonic counter for job ordering, and
  factory wiring.
- **`GET /api/jobs/{job_id}/result` — async OCR result
  download** — completes the existing async path. The
  route streams the searchable PDF produced by
  `POST /api/process/async` once the job reaches
  `status: "complete"`, gated by the per-job
  `text_artifact_token` from
  `GET /api/process/status/{job_id}` (constant-time
  compared via `secrets.compare_digest`; the token is
  passed via `?token=`, `Authorization: Bearer`, or
  `X-Artifact-Token` — matching the legacy artifact
  convention). 404 when the job is unknown, 409 when
  it exists but is not yet complete (PENDING /
  PROCESSING / ERROR), 403 when the token is missing
  or wrong, 410 when the on-disk PDF has been swept
  but the record is still in memory. The
  Content-Disposition header is `<stem>.ocr.pdf` (the
  trailing `.pdf` is stripped from the source filename
  to avoid `report.pdf.ocr.pdf`). The existing async
  endpoint was already shipping but lacked a
  result-download path; this is the user-visible
  completion of the async loop. (Phase D2.1)
- **Async OCR mode toggle in the workstation UI** —
  `ProcessSettings.svelte` gains an "Async processing"
  toggle (off by default — no behaviour change for
  existing users). When on, `WorkstationView` submits
  to `POST /api/process/async` and polls
  `GET /api/process/status/{job_id}` every 2 seconds
  (max 1000 attempts ≈ 33 min, well under the 24h
  record retention) until the job reaches a terminal
  state, then fetches the result PDF via
  `GET /api/jobs/{job_id}/result`. The toggle is
  purely UI state (`configStore.use_async`) — it is
  not synced to the server config because it is a
  deployment preference, not a runtime knob. The
  frontend `ocrApi` gains `processAsync` and
  `getResult`; the `apiClient` route-bearer table
  learns `/api/jobs` so the per-route OCR bearer is
  attached. (Phase D2.2 + D2.3)
- **P1 #4 (type `Any` escapes in `postprocess.py` /
  `handwriting_preprocessor.py`) resolved by venv refresh —
  no code change** — the §2 #4 finding flagged pyspellchecker's
  `candidates()` and `cv2.cvtColor` as untyped at the domain
  boundary, propagating `Any` through the return paths and
  tripping mypy's `warn_return_any = true`. An interim
  `typing.cast(...)` was applied in `glossary_imports.py`
  mid-investigation, then reverted once the
  `uv.lock` reconciliation (commit `829cd3b`) refreshed the
  venv (numpy 2.2.6 → 2.4.6, websockets 13.1 → 17.0.1). The
  mypy violations were symptoms of a stale venv, not actual
  code defects. Logged here for future auditors so the
  finding is not re-opened against a green baseline.

- **Phase C — service layers + typed API surface** —
  - **API**: canonical `ErrorEnvelope`
    (`{"error": "<stable_code>", "detail": "<human string>"}`) +
    `APIError` hierarchy (`SSRFBlocked`, `BackendUnavailable`,
    `ValidationFailed`, `NotFound`, `RateLimited`, `BadRequest`)
    wired via `register_envelope_handlers(app)`. Sweeps
    `routers/{config, transcription, providers, translation,
    extraction}.py` from ad-hoc `HTTPException` /
    `JSONResponse({...})` to the envelope; the duplicate
    `_ai_error_response` is deleted from `translation.py` +
    `extraction.py`. `services/security.py` keeps
    `api_error_response` as a one-release alias. New
    `tests/api/services/test_envelope.py` + parametrized
    `tests/api/routers/test_envelope_sweep.py` pin the
    contract.
  - **API**: glossary imports are now fully async —
    `import_glossary` awaits `is_ssrf_target(...)` directly.
    The `ThreadPoolExecutor` + `asyncio.run` sync shim
    (`_sync_ssrf_blocked` + `_validate_ssrf`) is deleted; the
    function no longer hops the threadpool to call the async
    SSRF validator.
    `tests/api/routers/test_glossary_imports_async.py`
    pins the async path.
  - **API**: per-route config helpers (`_load_config_from_store`,
    `_persist_config`, `_mask_api_key`,
    `_ConfigBackendIncompatible`) lifted from
    `routers/config.py` into `services/config_helpers.py`; the
    router re-exports the names for back-compat. The four
    `/api/models*` routes (3 from `routers/config.py` + 1
    `get_transcription_models` from `routers/transcription.py`)
    are consolidated in the new `routers/models.py`.
    `tests/api/services/test_config_helpers.py` +
    `tests/api/routers/test_models_router.py` cover the moved
    code.
  - **Frontend**: `FetchOptions` (`{ signal, ... }`) propagates
    through every wrapper in `endpoints.ts`; the new
    `frontend/src/lib/api/fetchOptions.ts` is the single
    source for the type + `createAbortController()` helper.
    Five free-function service modules
    (`translationService`, `extractionService`,
    `transcriptionService`, `glossaryService`, `jobsService`)
    replace every raw `fetchApi<...>('/...')` /
    `fetchFile('/...')` call across the 5 Svelte views
    (`TranslationView`, `ExtractionView`,
    `TranscriptionView`, `GlossaryView`, `JobHistoryView`).
    `TabRibbon.pingHealth` aborts its in-flight `/health`
    probe on unmount via `pingAbort.abort()` (and at the
    start of each new call so a superseded ping does not
    flip the badge to "backend down").
  - **Frontend**: `frontend/src/lib/__tests__/appHarness.ts`
    enables isolated `<App>` mounting in component tests;
    `mountApp()` returns a harness with the canonical
    `activeTab` writable, and `cleanupApp()` is idempotent
    and tears down the `AbortController` + intervals in
    `onDestroy`. New isolation tests in `appStore.test.ts`
    exercise the harness against happy-path + failure-
    injection stubs.

### Fixed

- **Documentation drift**:
  `/api/health` is not a real route; the liveness probe is
  `GET /health` (alias `/healthz`), with `GET /ready` (alias
  `/readyz`) for readiness. `DEPLOYMENT.md` and the
  `Dockerfile` healthcheck block now point at `/health`.
  `ARCHITECTURE.md` listed `/api/models/all`; the real
  combined route is `GET /api/models` (with
  `/api/models/ocr` and `/api/models/translation` siblings).
  `SECURITY.md` referenced a non-existent
  `OMNISCRIBE_CANCEL_SECRET` env var; cancel is an
  in-process `asyncio.Event` per `channel_id`, no signature.
  `DEPLOYMENT.md` documented third-party VLMs under
  `LLM_API_BASE` / `LLM_API_KEY`; the actual env vars are
  `OMNISCRIBE_LLM_API_BASE` / `OMNISCRIBE_LLM_API_KEY`
  (with `OMNISCRIBE_LLM_MODEL`).

- **OCR quality trust layer (Phase 1, foundation)** — new
  `omniscribe.core.ocr_quality` package ships six sub-modules
  (`watermark`, `script_detector`, `hallucination`, `calibration`,
  `trust_scorer`, `orchestrator`) plus an `events` log channel. Every
  sub-module defaults to **off** and fails open — no behavioural change
  for existing callers. `DocumentBlock` gains optional
  `trust_score: float | None` and `trust_flags: tuple[str, ...] | None`
  fields (always `None` until the layer is enabled).
  - New `OCrQualitySettings` Pydantic config (`extra="forbid"`).
  - `pyproject.toml` gains `[tool.omniscribe.ocr_quality]` workspace
    defaults, a `slow_dataset` pytest marker, and a `hypothesis` dev
    dependency for property tests on the pure trust formula.
  - New user-facing docs at `docs/ocr_quality.md`. Phase 2 (defaults on,
    Web UI Trust panel) and Phase 3 (calibration training, dataset
    regression) are planned but not yet shipped.
- **OCR quality trust layer (Phase 2, defaults on)** — wires the trust
  orchestrator into both engines and the `/api/process` route.
  - `OCRPipeline.__init__` accepts `trust_orchestrator=`; the
    `TrustOrchestrator` runtime-checkable Protocol in
    `omniscribe.core.ocr_quality.orchestrator` documents the
    `(blocks, page_image, *, model_id, page_size=None)` contract.
  - `EngineBase` gains `trust_orchestrator` and a no-op default
    `_apply_trust`; `HybridEngine` and `GroundedEngine` override it
    per page (Hybrid decodes the page image from base64; Grounded
    passes `None` because it has no page image in scope). Failures
    in the orchestrator log at DEBUG and fall back to the input
    blocks (design §7 fail-open contract).
  - `ProcessSettings.quality_options: OCrQualitySettings | None` with
    a `field_validator(mode="before")` that accepts `None`, a dict, a
    JSON-encoded string (multipart form), or an existing
    `OCrQualitySettings` instance.
  - `_form_param_keys()` and `process_pdf` / `process_pdf_async` carry
    the new `quality_options` form field through `resolve_process_settings`.
  - `ocr_pipeline_factory.build_pipeline` instantiates the
    orchestrator via `build_trust_orchestrator(settings.quality_options)`
    (returns `None` when every sub-module is off). Both pipeline
    branches pass it to `OCRPipeline(trust_orchestrator=...)`.
  - `/api/process` forwards `trust_model_id=settings.model` to
    `pipeline.run(...)` so calibration picks the right per-model JSON.
  - New `X-Document-Trust` response header carries a compact JSON
    summary (`block_count`, `scored_count`, `flagged_count`,
    `average`, 5-bin `histogram`, `flag_counts`) — emitted only when
    at least one block has a `trust_score`. The header is omitted
    entirely when the layer is off, keeping the no-orchestrator
    default byte-identical.
  - Phase 2 / Phase 3 keep the new defaults behind per-workspace
    toggles (`phase2_default: bool = False`,
    `phase3_default: bool = False`) so existing setups see no
    behaviour change.
- **OCR quality trust layer (Phase 3, calibration + dataset regression)**.
  - `scripts/calibrate_model.py` — CLI that fits Platt scaling
    `sigmoid(a * raw + b)` from an OCR-Quality-format JSON fixture
    via pure-numpy bounded gradient descent with backtracking
    line-search (`omniscribe.core.ocr_quality.calibration_fit.fit_platt`).
    Default `--train-fraction 0.8`, `--min-records 50`, `--seed 42`.
    Reports ECE (Expected Calibration Error, 10-bin weighted) on the
    held-out 20%; the acceptance criterion is ≥ 20% drop vs. raw.
  - `scripts/fetch_datasets.py` — downloads OCR-Quality and KIE-HVQA
    fixtures under `tests/fixtures/datasets/`. Datasets are not
    bundled in the repo (license review pending); the
    `slow_dataset` regression tests skip cleanly when absent.
  - `src/omniscribe/resources/calibration/qwen2_5_vl_72b.json` —
    shipped pre-trained calibration file fit on
    `tests/fixtures/datasets/ocr_quality_synthetic_qwen.json` (500
    records). ECE drop: 0.0999 → 0.0783 (21.6%, exceeds the ≥ 20%
    acceptance).
  - `tests/test_ocr_quality_calibration_regression.py`,
    `tests/test_kie_hvqa_hallucination_regression.py`,
    `tests/test_calibrate_model_script.py`,
    `tests/test_fetch_datasets_script.py`,
    `tests/test_ocr_quality_calibration_fit.py` — dataset-driven
    regression tests (12 Platt-fit, 6 calibration, 3 dataset-script,
    7 calibrate-script tests). Full-fixture paths are `slow_dataset`-
    gated; the `slow_dataset` mini-fixture smoke tests run with the
    fast suite.
  - `.github/workflows/nightly.yml` gains the calibration regression
    job (03:00 UTC) that runs `pytest -m slow_dataset` against the
    fetched datasets with cached HF Hub snapshots.
- **SECURITY.md** — vulnerability disclosure policy, threat model,
  hardening checklist. (D1)
- **DEPLOYMENT.md** — three deployment profiles (local, LAN, public)
  with Caddy + docker-compose reference. (D1)
- **CHANGELOG.md** — this file. (D1)
- `OMNISCRIBE_AUTH_TOKEN`, `OMNISCRIBE_OCR_AUTH_TOKEN`,
  `OMNISCRIBE_TRANSLATION_AUTH_TOKEN` reject well-known placeholder
  values at startup (e.g. `change-me-in-prod`). (M10)
- `AuthTokenUpdate.auth_token` field carries `min_length=32` and a
  custom weak-pattern check. (M1)
- `urllib` redirect handler validates every `Location` hop through
  `is_ssrf_target` (no more silent walk to `169.254.169.254`).
  (M2)
- `OMNISCRIBE_MAX_UPLOAD_MB` default bumped to 10 GB; absolute
  ceiling 100 GB.
- `MaxUploadSizeMiddleware` rejects oversized chunked uploads
  (cumulative byte accounting; was per-chunk before). (T2 / H2)
- `MaxUploadSizeMiddleware` is now wrapped around `send()` so a
  detected overflow actually emits a 413, not the inner app's
  empty-body 422. (T2 / H2)
- `BearerAuthMiddleware` accepts per-service tokens
  (`OMNISCRIBE_OCR_AUTH_TOKEN`, `OMNISCRIBE_TRANSLATION_AUTH_TOKEN`)
  for OCR- and translation-only routes.
- Dockerfile base image is digest-pinned. (M7)
- Dockerfile uv install is version-pinned. (M8)
- Dockerfile HEALTHCHECK against `/api/health`. (M11)
- `compose.yaml` binds the API + Redis to `127.0.0.1` only. (M9)
- `_emit` writes a terminal error progress frame if the output
  writer raises, so the UI does not appear stuck. (E3)
- `test_size_limits.py` covers chunked-upload overflow (single chunk
  and cumulative). (T2)
- `test_http_fetch.py` covers urllib SSRF redirect blocking. (T2)
- `test_websocket_handler.py` covers `/api/progress/cancel`
  session-token binding (missing header, wrong token, unbound
  channel, success). (T2)
- **WebSocket byte-level corruption on the progress channel** —
  block-level senders (`block_complete`, `block_retry`,
  `block_revised`, `quality_summary`) are awaited on the
  `/api/process` worker's own event loop while progress and
  warning frames are emitted on the main uvicorn loop. uvicorn's
  wsproto state machine is not safe to drive from two threads
  at once, so writes interleaved byte-by-byte on the wire and
  the browser saw mangled JSON fragments ("pairge" where the
  real text was "progress", "4tage" instead of "stage"),
  truncated frames (`{"status":"OCR (1/1)","percent` cut off
  mid-string), and ultimately `Invalid frame header` as the
  wsproto receiver gave up. `ConnectionManager.send` now records
  each channel's accept loop on `connect` and marshals any
  foreign-loop send back onto it via
  `asyncio.run_coroutine_threadsafe` + `asyncio.wrap_future`,
  so all socket writes are serialized through the loop that
  accepted the socket. The fix preserves caller ordering and
  backpressure. Regression-locked by
  `test_ws_send_from_foreign_event_loop_is_marshaled_to_accept_loop`,
  which fails against the old single-loop send path because
  `send_threads[0]` would no longer match `accept_thread["id"]`.
- **OCR fail on LM Studio + OlmOCR-2** — adding a system-role
  message on top of the canonical OlmOCR page prompt shifted
  the model's input distribution (OlmOCR-2 was RL-trained on
  the prompt as a single user turn). Symptom was
  `LLMCallError: ...` for every crop / handwriting / dual-engine
  call. `omniscribe.core.ocr.prompts` now exports
  `model_supports_system_role(model_name)`, which returns
  `False` for any model whose name contains `olmocr` (case-
  insensitive) — the only family we have direct field evidence
  for. `OCRProcessor._resolve_page_system` /
  `_resolve_crop_system` and the grounded backend's
  `_call_with_retry` gate on this helper, so the canonical
  page prompt stays a pure user message for OlmOCR-2 *and*
  every crop / handwriting / dual-engine call also drops the
  system role. Other models (Qwen, future additions) keep
  the system role. The list is intentionally narrow — see the
  helper's docstring for the "extend cautiously" rationale.
- **OCR fallback paths now log warnings** — three sites
  (`src/omniscribe/core/ocr/processor.py:487, 566` and
  `src/omniscribe/core/pdf/embedder.py:121`) used to swallow
  all exceptions with bare `except Exception:`, returning safe
  defaults without any log line. OCR quality degradation was
  invisible to operators. The except clauses are now narrowed
  to the specific exception types (pytesseract errors, cv2
  errors, font-probe errors) and each site emits a
  `logger.warning` with the underlying exception before the
  safe-default return. Tests cover the three sites.
- **Form primitives now associate errors and hints via
  ARIA** — `Input.svelte` and `Select.svelte` rendered an
  error/hint `<p>` below the form element but didn't link it
  via `aria-describedby` or set `aria-invalid`. Screen readers
  could not announce the error or hint on focus. The
  `ariaLabel` prop (added in the prior audit-fix 2bec3bf) is
  unchanged; the missing describedby + invalid wiring is
  added in this commit. The `Select.svelte` `ariaLabel` prop
  binding stays as-is.
- **TabRibbon now follows the WAI-ARIA tab pattern** — the
  container was a plain `<nav>` with `<button>` children. The
  container now has `role="tablist"`, each tab has
  `role="tab"`, `aria-selected`, and roving `tabindex`
  (active=0, others=-1).
- **Docker image is now multi-stage** — the Dockerfile was a
  single `FROM python:3.14-slim AS runtime-base` (dependabot PR
  #22 had bumped the base from 3.12 to 3.14 just before P1
  started) that ran `uv sync` of transformers, torch, surya-ocr,
  and chromadb in the production image, with the `uv` toolchain
  and `curl` build deps landing in the final image and
  enlarging the attack surface. A `builder` stage now does the
  `uv sync`; the runtime stage copies only `/app/.venv` from
  the builder, leaving the final image with no `uv` toolchain,
  no `curl`, and no build cache. The pre-change image did not
  build (it was missing a `COPY LICENSE README.md` for
  hatchling's project install — a pre-existing gap, incidentally
  fixed in this commit), so no pre-change size baseline exists.
  The 17.4 GB virtual / 11.4 GB unique final image is dominated
  by `torch` + `transformers` + `chromadb` + `surya-ocr`; the
  main win is the absence of build tools and build cache from
  runtime, verified by `docker run` smoke (no `uv`/`curl`
  in the container) and import test (`transformers`, `surya`,
  `omniscribe` all import).

### Security

- **scripts/ingest_lexicon.py is now XXE-safe** — the script
  parses external GitHub-hosted TEI XML with `defusedxml.ElementTree`
  instead of the stdlib `xml.etree.ElementTree`. The previous parser
  silently accepted `<!DOCTYPE>` declarations and external entity
  references, allowing XXE-driven local file read, SSRF, or
  billion-laughs DoS via a malicious payload. The parse step is
  extracted into `_parse_xml()` and unit-tested for plain XML,
  external-entity XXE, and billion-laughs rejection.

- **Sprint 1 / M-2**: `core/ocr_quality/orchestrator.run` now
  pre-computes per-block `script_detector.detect` results into a
  single-pass cache (keyed on `hash(text)`). A 200-block page
  with all-Latin text collapses from 200 per-character
  classification passes to 1. The test file
  `tests/core/ocr_quality/test_ocr_quality_orchestrator.py`
  still passes (the per-block loop now reads the cache instead
  of re-running the classifier).
- **Resume session (2026-08-29)** — closed the remaining audit
  follow-ups after the prior 5-sprint sweep.
  - **Sprint 3 / H-2**: `client/lib/presentation/shell/tab_ribbon.dart`
    now wraps the tab buttons and the theme toggle in explicit
    `Semantics(button: true, selected: ..., toggled: ...)` nodes so
    screen readers announce both the role and the selection state. New
    regression test:
    `client/test/presentation/shell/tab_ribbon_a11y_test.dart`.
  - **Sprint 4 / H-2**: `tests/routers/test_progress_ws_e2e.py` drives
    a full `POST /api/progress/session` → WS auth → `POST
    /api/process/async?progress_channel=...` → WS progress frame →
    status=complete lifecycle. Catches regressions where
    `OCRService._progress_adapter` drops the channel arg.
  - **Sprint 5 / M-10**: `src/omniscribe/server.py` refuses to start
    bound to a non-loopback host when `OMNISCRIBE_AUTH_TOKEN` is one
    of the documented placeholder values (`change-me-in-prod`,
    `placeholder`, `example-token-replace-me`,
    `replace-this-with-a-real-secret`). The guard is opt-out via
    `--allow-placeholder-token`. New regression test:
    `tests/test_server_placeholder_token.py` (4 cases).
  - **DEPLOYMENT.md**: the post-upgrade "Visit `/health`" instruction
    is now `http://localhost:8000/api/health` (the actual endpoint).
  - **Pre-batch (already in main)**: `src/omniscribe/utils/security.py`
    adds the AWS EC2 IPv6 metadata network (`fd00:ec2::/64`) and the
    empty `getaddrinfo` guard; `core/pdf/rasterizer.py` uses PIL
    `seek`/`n_frames` instead of `ImageSequence.Iterator`; the
    `client/` package is now in `.dockerignore`; `auth_required_banner`
    navigates to Settings on click; the export modal HTML-escapes
    user-controlled filenames and bbox text (XSS hardening).
  - Plan: `docs/superpowers/plans/2026-08-28-audit-remediation.md`.

- **Sprint 2 + Sprint 3 + Sprint 4 follow-ups (2026-08-28)** —
  closes the most material remaining items from the 5-domain audit
  beyond the initial sprint close.
  - **Sprint 2 / C-3**: `StateBackendPlugin.apply` now rejects
    `sqlite_path` values that resolve outside the artifact base
    directory (path-traversal guard for operator-supplied SQLite
    overrides).
  - **Sprint 2 / H-2**: `ProgressServiceImpl` foreign-loop sends
    now register a `Future.add_done_callback` that detaches the
    connection on exception. The previous code's
    `asyncio.run_coroutine_threadsafe` future was silently
    swallowed on failure.
  - **Sprint 2 / M-3**: `create_app` registers a catch-all
    `Exception` handler that logs the traceback but returns a
    stable 500 envelope to the client. The previous default
    leaked internal stack traces.
  - **Sprint 2 / H-5**: `OCRPlugin._parse_upload` now validates
    `content_type` against an allowlist (PDF, PNG, JPEG, WebP, AVIF)
    AND a magic-byte header check, rejecting mismatches with 415.
  - **Sprint 3 / H-4**: `client/lib/core/websocket/ws_client.dart`
    now runs an application-level keep-alive (20s ping + 5s
    pong watchdog). Half-open sockets are detected in <30 s
    instead of relying on the OS's 2-hour TCP keep-alive.
  - **Sprint 4 / H-1**: `tests/core/workflows/test_pipeline_repair_integration.py`
    wires `OCRPipeline -> HybridEngine -> _repair_blocks -> QualityRepairLoop`
    end-to-end and asserts the repair loop fires for low-confidence
    blocks and skips when `repair_options=None`. The existing
    unit tests covered the loop in isolation; a regression
    that drops the call site would have passed them silently.
  - **Windows Defender false positive on
    `arrow_substrait.dll`** (lancedb transitive dep, optional
    `[lexicon]` extra): documented in `SECURITY.md` §"Platform
    Notes" with three mitigations (update Defender, folder
    exclusion via `install.ps1`, or run in a container).
    `install.ps1` now offers an opt-in Defender exclusion
    scoped to the venv site-packages dir. New regression test
    `tests/ops/test_arrow_substrait_present.py` asserts the
    DLL is shipped as a real file when the extra is installed.

  Plan: `docs/superpowers/plans/2026-08-28-audit-remediation.md`.

- **Sprint 5 audit remediation (DevOps & Config, 2026-08-28)** — the
  2026-08-28 5-domain audit's DevOps & Config findings are
  partially closed (license posture in `pyproject.toml`, the
  `Dockerfile curl | sh` switch to download-to-disk, the
  `start_app.vbs` shell-injection analysis, and the per-service
  `.env.example` annotations are addressed).
  - **C-1**: workspace `.env` no longer has the
    `OCR_API_BASE=http://192.168.1.75:1234/ v1` URL typo (stray
    space inside the URL). The value is now well-formed. The
    audit's pre-existing `my-real-secret-key-xyz123` and
    `translate-key-1234` placeholders were left as documented
    test fixtures; operators on a public deploy must rotate
    them at the provider before the first request.
  - **C-3**: `AGENTS.md` now lists the exact CI jobs that must
    be green before merge (fast tier on 3.11 / 3.13 / windows +
    Trivy container scan) and the nightly tier that is
    intentionally **not** a merge gate.
  - **H-1**: `Dockerfile` now downloads the `uv` release tarball
    to disk and installs from disk instead of piping
    `curl | sh`. A `test -s` guard rejects an empty payload, and
    a non-2xx response (where the redirect chain fails) propagates
    as a non-zero exit. Same supply-chain provenance as
    `install.sh`, but no half-fetched payload can reach `sh`.
  - **H-2**: `Dockerfile` installs `tini` and uses it as
    `ENTRYPOINT`. Without tini, the Python process is PID 1
    inside the container and `SIGTERM` exits abruptly without
    draining the FastAPI lifespan / WebSocket clients.
    `compose.yaml` adds `--maxmemory 256mb --maxmemory-policy
    allkeys-lru` to the Redis service so a chatty broker cannot
    OOM the host.

  Plan: `docs/superpowers/plans/2026-08-28-audit-remediation.md`.

- **Sprint 4 audit remediation (Testing & QA, 2026-08-28)** — the
  2026-08-28 5-domain audit's Testing & QA findings are partially
  closed (the `QualityRepairLoop` integration test and the WS
  end-to-end progress test remain follow-ups).
  - **C-1 / C-2**: reconciled coverage threshold. Local
    `pyproject.toml` now sets `fail_under = 85` to match the CI
    `test.yml` threshold (`--cov-fail-under=85`). Before this
    change, `pytest` ran locally at 80 % and CI rejected the same
    diff at 85 %, producing green local runs that turned red in
    CI. Stale `.coverage` artifacts are now also gitignored.
  - **M-4**: the fast-tier CI workflow now runs
    `coverage html -d htmlcov` and uploads the report as a
    30-day artifact so developers can drill into uncovered
    branches without running coverage locally.

  Plan: `docs/superpowers/plans/2026-08-28-audit-remediation.md`.

- **Sprint 3 audit remediation (Frontend, 2026-08-28)** — the
  2026-08-28 5-domain audit's Frontend findings are partially
  closed (a11y test coverage remains a follow-up; the Flutter
  test target compiles and exercises a real `MaterialApp` shell
  smoke).
  - **C-1**: deleted the stale Svelte/Vite bundle under
    `src/omniscribe/static/assets/`; the
    `index.html` referenced hashed bundles (`index-DQshMyx3.js`,
    `index-MlaBq5fV.css`, …) that no longer exist and would 404
    in the browser. The placeholder `index.html` now renders a
    Flutter-client landing page that points at
    `GET /health` and `GET /ready`.
  - **C-2 / C-3**: `client/lib/core/network/api_client.dart` now
    refuses plaintext `http://` for non-loopback hosts at the
    `set baseUrl` boundary. Loopback (`127.0.0.1`, `::1`,
    `localhost`) is the documented local-trusted mode and remains
    allowed in plaintext. The check is a runtime guard so a user
    pasting a public IP into the settings screen sees a clear
    `ArgumentError` rather than silently leaking the bearer token.
    New tests:
    `client/test/core/network/test_api_client_url_safety_test.dart`.
  - **H-1**: `BottomProgressDock` now wraps its content in a
    `Semantics(liveRegion: true, label: …)` node so screen
    readers announce stage + percent changes without forcing the
    user to focus the dock. The label is rebuilt on every
    announcement.
  - **H-3**: replaced the 13-line `expect(true, isTrue)` placeholder
    in `client/test/widget_test.dart` with a real
    `testWidgets` smoke that mounts `OmniScribeApp` and asserts
    the `MaterialApp` shell is present.

  Plan: `docs/superpowers/plans/2026-08-28-audit-remediation.md`.

- **Sprint 2 audit remediation (API & Security, 2026-08-28)** —
  the 2026-08-28 5-domain audit's API & Security findings are
  partially closed (the deferred-capability items remain on the
  follow-up roadmap per AGENTS.md).
  - **C-1**: `server.py:main()` refuses to start bound to a
    non-loopback host with no `OMNISCRIBE_AUTH_TOKEN` (raises
    `SystemExit`). Loopback binds (`127.0.0.1`, `::1`, `localhost`)
    remain allowed without a token — the documented local-trusted
    mode. New tests: `tests/api/test_server_startup_guard.py`.
  - **C-2**: `server.py:main()` emits a loud WARNING when
    `ALLOW_SSRF_LOCAL=true` AND the bind host is non-loopback, so
    a LAN-deploy operator sees the SSRF-disabled-by-default risk
    explicitly. Default still true for local dev; operators who
    expose the server to a LAN should set
    `ALLOW_SSRF_LOCAL=false`.
  - **C-4 / H-4**: `ProviderManagerImpl.set_active` now refuses an
    `api_base` that the SSRF guard rejects (private / loopback /
    metadata range). Closes the deferred-capability gap where an
    unauthenticated caller could redirect the OCR pipeline at an
    attacker-controlled VLM. Operators can still point at loopback
    via `ALLOW_SSRF_LOCAL=true`.
  - **H-1**: `ProviderManagerImpl.discover_models` and `validate`
    now rewrite the request URL host to the SSRF-validated IP via
    `_rewrite_url_with_resolved_ip`, preserving the original hostname
    in the `Host` header so HTTPS SNI / virtual hosting still match.
    Closes the DNS-rebinding TOCTOU window the audit flagged.
    IPv6 literals are wrapped in `[ ]`. Existing
    `tests/plugins/test_providers_plugin.py` updated to the new
    pinned-URL contract. New tests:
    `tests/api/test_providers_resolved_ip_pin.py`.
  - **H-3**: `MemoryStateBackend.consume_channel` and
    `SQLiteStateBackend.consume_channel` compare
    `session_token` with `secrets.compare_digest` (timing-safe).
    New tests: `tests/api/test_channel_token_compare.py`.
  - **M-1**: `server.py:create_app` mounts `fastapi.middleware.cors.CORSMiddleware`
    driven by `OMNISCRIBE_CORS_ORIGINS` (parsed but never wired
    before the audit). Empty default denies browser cross-origin
    requests but allows the Flutter desktop client (no Origin
    header).

  Plan: `docs/superpowers/plans/2026-08-28-audit-remediation.md`,
  Sprint 2 file `2026-08-28-audit-remediation-sprint2-api.md`.

- **Sprint 1 audit remediation (Core Pipeline, 2026-08-28)** — the 2026-08-28 5-domain audit's Core Pipeline findings are
  closed. All fixes land a regression test before the production
  change.
  - **C1**: `core/ocr/multi_format_client.py` now logs a WARNING
    with provider id + missing key on every malformed upstream
    response (was a silent `return ""` in all three branches).
  - **C2**: `core/lexicon/lancedb_store.toggle_glossary` fallback
    now performs `add`-before-`delete` (via `pa.Table.from_pylist`)
    so a write failure preserves the original rows.
  - **H1**: PIL `Image.open` calls in
    `core/imaging/utils.py`, `core/ocr/processor.py:549,635`,
    `core/aligner.py:166`, and `core/ocr/trocr.py` now use
    `with` blocks so the underlying buffer is closed before the
    helper returns.
  - **H2/H4**: `OCRProcessor.__init__` and
    `PromptedGroundedOCR.__init__` now read LLM coordinates
    (`api_base`, `api_key`, `model`) from `load_settings()`
    rather than `os.getenv`. The F1.9 fix already covered the
    timeout/retry knobs; this closes the residual gap.
  - **H3**: `core/recall/text_layer.PdfTextLayerRecall.supplement`
    now wraps per-page extraction in try/except so a single
    corrupted PDF page degrades to "no extra boxes" instead of
    aborting the per-page loop. Mirrors `whitespace.py`.
  - **M3**: removed the redundant unconditional pre-loop
    `await self.circuit_breaker.check()` in
    `OCRProcessor._chat`; the in-loop check now runs on every
    attempt (not just `attempt > 0`) so the first attempt also
    consults the breaker and an already-OPEN breaker fails fast.
  - **M5**: `_MAX_SAFE_PIXELS_CEILING` in
    `core/pdf/rasterization_settings.py` tightened from 10 GPixels
    to 500 MPixels (~20x the default). Accidental 10x typos that
    would allocate ~100 GB are now rejected.
  - **M6/M7**: extracted `_FULL_PAGE_FALLBACK_EPSILON`,
    `_MIN_FONT_SIZE`, `_MAX_FONT_SIZE`, and `_FALLBACK_BOX_INSET`
    module-level constants in `core/pdf/embedder.py`; the literal
    `0.001 / 0.999 / 10` thresholds in `_handle_fullpage_fallback`
    are gone.
  - **M9**: `core/workflows/grounded.py` repair loop now uses
    `dataclasses.replace(obj, text=text)` instead of in-place
    mutation; the new object is written back into both the local
    per-page list and `response.blocks` so downstream stages see
    the repaired text. The block can be made `frozen=True` later
    without rewriting this site.
  - **M10**: `core/evaluation._valid_bbox` now accepts degenerate
    single-point boxes (area = 0), aligning with
    `confidence_eval.iou()` semantics. The previous `< x1`
    rejected them, creating metric divergence between the two
    modules.

  Plan: `docs/superpowers/plans/2026-08-28-audit-remediation.md`.

- **Redis password is now CSPRNG-generated** — `start_app.vbs`
  generates the password via a PowerShell one-liner using
  `[System.Security.Cryptography.RandomNumberGenerator]` instead of
  the previous VBScript `Randomize` + `Rnd()` LCG. The consumer-side
  `--requirepass` plumbing added in a77b77a is unchanged; only the
  entropy source moved. A hygiene test asserts the VBS no longer
  references `Rnd` / `Randomize` and now references the CSPRNG type.

### Changed

- `_extract_prompt_and_image` now returns a 2-tuple
  `(prompt, image)`; the legacy `system_prompt` slot and the
  system-from-`messages` branch are removed. Callers must
  pass the system role via the explicit `system_prompt`
  parameter on `call_llm` (which routes through
  `model_supports_system_role` for OlmOCR family models).
  (Issue 12)
- `process_pdf` / `process_pdf_async` share a single
  `_prepare_process_request` helper (was duplicated ~60 lines of
  validation/upload). (Q1)
- `Any`-typed `manager_send_block` / `manager_send_page_complete`
  callbacks replaced by a `ConnectionManagerLike` Protocol. (Q2)
- Runner dependencies lifted from routers to a factory module
  (`ocr_pipeline_factory.py`). (Q3)
- Synchronous `json.load` / `open` calls inside async handlers are
  wrapped in `asyncio.to_thread`. (Q4)
- `_convert_pages` tautology guard simplified to `if pages:`. (Q5)
- **Progress WebSocket wire format is now line-delimited JSON
  (NDJSON)** — every frame the server sends is one JSON object
  followed by a single `\n`. The frontend's `socket.onmessage`
  in `frontend/src/lib/api/websocket.ts` splits on `\n` and
  parses each line independently. Belt-and-suspenders: even
  if a future bug ever concatenates two ASGI frames into one
  text payload, the client can split and recover. The
  previous single-JSON-per-frame path is still valid (a
  trailing `\n` is harmless to `JSON.parse`).
- **`/api/process` warning text now includes the underlying
  exception message**, capped at 500 chars. Old format was
  `OCR failed for page N: LLMCallError`; new format is
  `OCR failed for page N: LLMCallError: <underlying message>`.
  Saves a round-trip to the server log when a warning fires.
- **OCR + grounded prompt bodies slimmed** — the diacritics
  emphasis and "no invent" guard text moved out of the user
  prompt and into the system message (which the model
  processes separately from the per-task instructions).
  Visible side effects: the OlmOCR-2 page prompt and the
  ground-truth OlmOCR page body are unchanged, but the crop
  / handwriting / dual-engine / correction crops no longer
  carry the long diacritics preamble in their user turn.
- **Grounded default prompt gained two extra lines**:
  "for multi-column layouts, read each column top-to-bottom
  before moving to the next column" and "if the page
  contains no readable text, emit an empty JSON array `[]`".
  Both are belt-and-suspenders against the historical
  line-collapsing and "single placeholder element" failure
  modes on dense / blank pages.
- `_ai_error_response` deduplicated to one definition in
  `common.py`. (Q6)
- TrOCR dual-engine fallback catches `LLMCallError` separately so
  the page-isolation boundary sees secondary-VLM failures as
  engine-down signals instead of swallowing them. (E2)
- `OCRProcessor` no longer uses `getattr(self, "handwriting_mode",
  False)` — the attribute is unconditionally set in `__init__`.
  (E4)
- `_PYMUPDF_AGPL_NOTICE_EMITTED` race documented as acceptable
  (logging-only idempotent). (E5)
- Dependency upper pins tightened across `pyproject.toml` and
  `frontend/package.json`:
  - `pillow>=11.3,<13`
  - `httpx>=0.27.2,<0.29` (CVE-2025-43859 floor)
  - `requests>=2.32.0` (CVE-2024-35195 floor)
  - `openai>=2.11.0,<3`
  - `fastapi>=0.124,<1.0`
  - `pymupdf>=1.27,<2`
  - `torch>=2.0,<3`
  - `redis>=5.0,<9`
  - `langgraph>=0.1,<2`
  - `chromadb>=0.5,<2`
- `block_metadata_overlays` typed as `Mapping[...]` so the
  `_cross_page_merge` cast is gone. (A5)
- `ARCHITECTURE.md` adds the missing `/api/config/ocr`,
  `/api/config/translation`, `/api/models/ocr`,
  `/api/models/translation`, `/api/models/all`, and
  `/api/glossary/library/*` routes. (D1)

### Removed

- `markdown-it` frontend dependency (no imports — dead). (Deps)
- `@types/markdown-it` frontend dev dependency. (Deps)
- Vite `manualChunks` branch for `markdown-it`. (Deps)

### Deferred

- A1 — ASGI middleware is intentional for pre-routing enforcement;
  per-router `dependencies=[Depends(...)]` is no safer in practice.
- A2 — module-level state singletons are the right shape until the
  Redis backend ships.
- A3 — frontend store consolidation is out of scope for the backend
  audit.
- A4 — lazy imports are intentional for cold-start perf.

## [0.1.0] — Initial public release

- Hybrid OCR pipeline (Surya detection + VLM OCR + DP align + refine).
- Grounded OCR path (`grounded_backend=`).
- WebSocket-bound progress with token-bound channels.
- Glossary RAG for translation (`async-translation` + `memory` extras).
- Svelte 5 + Tailwind CSS v4 workstation UI.
- Single-worker FastAPI server with optional Celery background jobs.

[Unreleased]: https://github.com/Sifr-r/OmniScribe/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Sifr-r/OmniScribe/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Sifr-r/OmniScribe/releases/tag/v0.1.0