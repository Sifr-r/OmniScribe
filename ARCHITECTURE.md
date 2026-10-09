# Architecture Ledger

The canonical Architecture Ledger for OmniScribe is located at [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Please refer to `docs/ARCHITECTURE.md` for the complete system shape, pipeline topology, plugin tree, component catalog, extension points, and performance specifications.

## Latest Synchronizations
- 2026-10-08 **GitHub failed-check repair**. Ownership and evidence are recorded in
  [docs/ci-checks-blueprint-2026-10-08.md](docs/ci-checks-blueprint-2026-10-08.md),
  whose single responsibility is repair scope and validation evidence.
  Dependabot now tracks Flutter `pub` manifests under `/client`, replacing the
  deleted npm `/frontend` target. The existing optional-PyArrow collection guard
  unblocks fast and nightly pytest collection. The OCR transport regression test
  now narrows its backend to the two concrete implementations, matching CI's
  `mypy src tests` scope. Both Docker stages upgrade the seven Debian packages
  identified by Trivy while retaining the digest-pinned Python base and scanner
  enforcement. AnyIO's web constraint is `>=4.14.2,<5`, locked to 4.15.1;
  existing bundle submodule collection and the explicit `anyio.abc` import were
  verified with a frozen runtime probe. The two obsolete AnyIO audit exceptions
  are removed from CI and Makefile. Runtime APIs and data storage are unchanged.
  `.trivyignore.yaml` owns the expiring, exact-version SDK advisory exception;
  its single responsibility is to keep container scanner acceptance scoped to
  the documented unreachable SDK authorization vector. Final local fast gate:
  2,779 passed, 30 skipped, 13 deselected, 80.26% coverage. Trivy reports zero
  unsuppressed fixable HIGH/CRITICAL findings; container health returns HTTP 200.
  The pinned scanner action's SARIF severity-limit option is explicitly enabled
  so the remote gate retains the same HIGH/CRITICAL policy verified locally.
- 2026-10-06 **CI optional extras coverage gap closed** in `.github/workflows/nightly.yml`.
  Added a dedicated `extras` job (`pytest (py${{ matrix.python }}, extras tier: lexicon + memory + glossary)`)
  running on Ubuntu Linux with Python 3.12. The job synchronizes
  `uv sync --extra web --extra async-translation --extra lexicon --extra memory --extra glossary`
  and executes `pytest -m "not slow and not slow_dataset"`. This provides scheduled and
  dispatch CI coverage for LanceDB, PyArrow, sentence-transformers, SQLAlchemy, openpyxl,
  and gitpython integration modules, closing the dark coverage gap while strictly preserving
  the lean fast-tier PR merge policy (`test.yml:67-77`).
  Validated with Python YAML parsing; all actions SHA-pinned to existing CI standards.
  Full gates re-verified clean: Flutter analyze (0 issues), Flutter test (447 passed, 0 failed),
  Dart format (171 files, 0 changed), layout/JSON contracts pass, Ruff check/format clean,
  Mypy clean (222 files), pip-audit clean (0 vulnerabilities, 3 ignored with reachability proofs).
- 2026-10-06 dependency-security closure and a **CI collection break** found while
  re-verifying it. `uv run pip-audit` previously exited 1 on six advisories; it now
  exits 0.
  1. **`urllib3` 2.7.0 → 2.8.0** in `uv.lock`, clearing PYSEC-2026-4177 / -4176 /
     -4175. `urllib3` is transitive only (`requests`, `lance-namespace-urllib3-client`)
     and neither caps it, so this needed no design decision.
  2. **Three advisories are risk-accepted with a written reachability proof**, in
     `.github/workflows/test.yml` and mirrored in the `Makefile` `audit` target so a
     local run and CI agree:
     - `PYSEC-2026-4024` / CVE-2026-64847 (anyio process-pool stderr deadlock) —
       OmniScribe has **zero** anyio usage in `src/`; every concurrency call site is
       stdlib `asyncio.to_thread`. No anyio process pool exists.
     - `PYSEC-2026-4025` / CVE-2026-63374 (anyio IDNA-2003 TLS spoofing, Critical) —
       requires a non-ASCII hostname; `utils/security.py::_PinnedNetworkBackend`
       hands `httpcore` a literal `resolved_ip`, so no hostname is ever IDNA-encoded.
     - `CVE-2026-104873` (langgraph-sdk `@auth.on.*` action bypass) — affects custom
       auth handlers for a self-hosted LangGraph Platform server. The only import site
       is `core/translate/workflow.py:87`, `from langgraph.graph import END, START,
       StateGraph`; `langgraph_sdk` is never imported. Reaching the fix (sdk 0.4.4)
       requires `websockets>=17.0.1` (`[web]` extra) → `<17`, moving a production
       runtime dependency backwards to silence an unreachable finding. Declined.
  3. **`tests/core/test_lexicon_schema.py` broke collection in *both* CI workflows.**
     `pyarrow` ships only in the `memory`/`lexicon` extras, but `test.yml` syncs
     `--extra web` and `nightly.yml` syncs `--extra web --extra async-translation` —
     neither installs it. The module's bare `import pyarrow as pa` therefore aborted
     the **entire** suite at collection, so no fast-tier pytest run could ever reach
     its assertions. Three sibling lexicon tests already `pytest.importorskip`
     (`test_recall_fixture.py`, `test_toggle_glossary_atomic.py`,
     `test_translation_lexicon_integration.py`); this one was simply missed. The guard
     is placed before the `omniscribe.core.lexicon.schema` import, which hard-imports
     `pyarrow` too. It was the only module-scope hard import of an extras-only package
     in `tests/` (verified by scanning all extras-only names). Local runs had masked
     it because the developer venv had the `lexicon` extra installed.
  4. Environment note: a `uv sync` interrupted by a locked `scipy` OpenBLAS DLL had
     left `.venv` without the `langgraph` tree, which would have turned real
     translation tests into silent `importorskip` skips. Restored to
     `uv sync --extra web --extra preprocessing --extra async-translation`, verified
     idempotent. Two stale `surya.detection.server` processes from 2026-10-03 were
     holding the DLL and had to be stopped to complete the sync.

  **Verification (2026-10-06):** `uv run pip-audit --ignore-vuln PYSEC-2026-4024
  --ignore-vuln PYSEC-2026-4025 --ignore-vuln CVE-2026-104873` → *No known
  vulnerabilities found, 3 ignored*, **exit 0** (was exit 1 on 6). The unignored
  baseline in the restored env is exactly 3 advisories, confirming each flag matches a
  real finding and none are stale. `pytest -m "not slow and not slow_dataset" --cov=
  src/omniscribe --cov-fail-under=80` → **2775 passed, 30 skipped, 13 deselected,
  0 failed, exit 0 in 236.49 s at 80.23% coverage**. `ruff check src tests` and
  `ruff format --check` clean over 446 files; `mypy src/omniscribe` clean over 222
  files; `uv lock --check` clean; `test.yml` parses and the folded `run:` scalar
  resolves to the exact command above. The count differs from the 2026-10-04
  "2866 passed / 84.04%" run because this environment carries a narrower extras set —
  no `lexicon`/`memory`/`glossary` extras — so the `pyarrow`- and `sqlalchemy`-backed
  tests skip instead of running. The collection fix is behaviour-preserving when
  `pyarrow` *is* installed: `pytest.importorskip` is a no-op there, so that module runs
  exactly as before.
- 2026-10-06 **SSRF rebinding fixed for HTTPS on both OCR engines** (was open when
  first raised earlier the same day). `plugins/ocr/pipeline_bridge.py` could only
  rewrite the URL to the resolved IP for plain `http` — a naive https URL→IP rewrite
  breaks SNI and certificate validation — so for an HTTPS `api_base` the
  SSRF-validated address was discarded and the bare hostname was re-resolved on
  connect, leaving exactly the TOCTOU window closed for `preflight_check` on
  2026-10-04. Both OCR engines were affected, not just the hybrid one. `resolved_ip`
  is now threaded end to end and each engine owns an IP-pinned transport:
  - `core/ocr/chat_client.py` — `ChatClient.__init__` gains `http_client` and passes
    it to every `call_llm` in the retry loop. `call_llm` never closes an injected
    client, so ownership stays with the caller.
  - `core/ocr/processor.py` — `OCRProcessor.__init__` gains `resolved_ip`, builds
    `create_pinned_client(self.api_base, resolved_ip)`, hands it to `ChatClient`,
    passes it as `http_client=` to the ephemeral `AsyncOpenAI` in
    `ensure_model_loaded`, and releases it in the existing `aclose()` hook (the
    hybrid teardown path `OCRPipeline.aclose` already routed through).
  - `core/grounded/prompted.py` — `PromptedGroundedOCR` gained the same
    `resolved_ip` parameter, pinned `AsyncOpenAI` + `call_llm`, plus a new
    `aclose()`; `pipeline.py` `OCRPipeline.aclose` now also releases the grounded
    backend (it previously only handled `ocr_processor`, on the assumption that the
    grounded path held no long-lived client — no longer true).
  - `pipeline_bridge.py` passes `resolved_ip` into both constructors.
  `create_pinned_client` is imported by `core`, matching the existing precedent in
  `core/transcription/api_engine.py`; `utils/security.py` depends only on stdlib,
  `httpx`/`httpcore` and `omniscribe.config`, so no import cycle is introduced.
  Regression tests in `tests/plugins/test_pipeline_bridge.py`
  (`test_build_pipeline_pins_transport_to_resolved_ip_for_https`,
  `test_ocr_pipeline_aclose_releases_pinned_transport`) are parametrised over both
  engines and verified red against the pre-fix source — `assert [] ==
  [('https://vlm.example.internal/v1', '203.0.113.10')]` — and green after. Both
  engines now match `documents`, `translate`, `transcribe` and the OCR pre-flight
  probe.

  **Post-fix gate (2026-10-06):** `pytest -m "not slow and not slow_dataset" --cov=
  src/omniscribe --cov-fail-under=80` → **2779 passed, 30 skipped, 13 deselected,
  0 failed, exit 0 in 237.17 s at 80.25% coverage** (+4 tests over the pre-fix
  2775, all four new). `ruff check src tests` and `ruff format --check` clean over
  446 files; `mypy src/omniscribe` clean over 222 files.
- 2026-10-04 SSRF gap closed in the OCR VLM pre-flight probe, plus a flaky-test
  diagnosis. `OCRServiceImpl.preflight_check` validated `api_base` with
  `check_ssrf_target_sync` and then discarded the resolved address;
  `_probe_vlm_server` handed the bare **hostname** to a plain
  `openai.AsyncOpenAI`, which re-resolved it on connect. A host answering with
  a public address during validation and a link-local/metadata address on the
  second lookup therefore walked past the guard. The probe now takes
  `check.resolved_ip` and builds `create_pinned_client(api_base, resolved_ip)`,
  passing it to the SDK as `http_client` and closing it in `finally` — the same
  contract `documents/service.py` and `translate/service.py` already use.
  Regression test `test_preflight_probe_pins_transport_to_resolved_ip` asserts
  the pin reaches the SDK and is closed; verified red against the pre-fix code
  (`assert [] == [('http://vlm.example.internal/v1', '203.0.113.10')]`) and
  green after. `ruff check` / `ruff format --check` / `mypy src` clean;
  `mypy src tests` clean on both changed test files; the two touched test
  files pass 25/25.
- 2026-10-04 flaky-test diagnosis, not a product defect:
  `test_sqlite_owner_excludes_other_process_and_recovers_after_death` timed out
  on its second child interpreter under the full coverage run while passing
  alone. The assertions under test (owner exclusion, then recovery after the
  owner dies) resolve in microseconds; measured cold start of one child
  interpreter plus `omniscribe` plugin imports is **~8.7 s** unloaded, so the
  45 s per-subprocess ceiling was only ~5x headroom and a loaded
  coverage-traced run exhausted it. Raised to `_CHILD_OWNER_TIMEOUT_S = 180`,
  which keeps a real deadlock guard without making the gate a machine-speed
  proxy. Isolated call time is 17.38 s for both children combined. The full
  gate is now clean: **2,866 passed, 18 skipped, 13 deselected, 0 failed,
  exit 0 in 420.21 s at 84.04% coverage** (the 18 skips are absent optional
  extras — `sqlalchemy` for the glossary extra, `pytesseract` — not deferred
  work), closing the "clean full backend fast/coverage gate" item that
  `docs/outstanding-work.md` had carried since the 2,848-passed run.
- 2026-10-04 **open, newly verified**: `uv run pip-audit` exits 1, so the
  `fast` job in `.github/workflows/test.yml` is currently red. Five real
  advisories: `anyio 3.7.1` PYSEC-2026-4025 / PYSEC-2026-4024 (fix 4.14.2) and
  `urllib3 2.7.0` PYSEC-2026-4177 / 4176 / 4175 (fix 2.8.0). `urllib3` is only
  pulled transitively by `requests` and `lance-namespace-urllib3-client`,
  neither of which caps it, so `uv lock --upgrade-package urllib3` is the
  clean fix. `anyio` is **not**: the `>=3.7,<4` pin is deliberate and
  documented at `pyproject.toml` (anyio 4.x uses `_lazyimport` for submodules
  that PyInstaller's static analysis cannot trace, so the onefile Windows
  bundle ships an incomplete `anyio` and dies on `import anyio.abc`; the
  frozen binary was already found dead on boot for this reason on 2026-10-04).
  Bumping anyio needs the PyInstaller hidden-import story fixed first, or a
  documented `--ignore-vuln` exception (precedent: PYSEC-2026-311, removed
  again in `test.yml` once `chromadb` left the tree). Not resolved here.
- 2026-10-04 actionable-document closeout: Job History fetch/clear failures now surface with recoverable widget coverage; 447 Flutter tests, static/ownership/contract gates, Windows/web release builds and the no-VLM Windows real-server integration pass. Dataset and packaging tooling retain their existing owners. See [the scoped artifact](docs/actionable-closeout-2026-10-04.md) and canonical ledger for new-file responsibilities and final evidence.
- 2026-10-04 audit of the uncommitted client working tree: the tree was already green (`flutter analyze --fatal-infos` exit 0, 439 tests, layout check pass), so the audit targeted what the gates could not see and found three defects, all now fixed.
  1. **Both layout fixes were unpinned, and the glossary one was incomplete.** `client/lib/features/documents/export_modal.dart` (scrollable body) and `client/lib/features/glossary/glossary_screen.dart` (flexible title) had no test constraining the dimension that caused the overflow, so either wrapper could be deleted with every gate still green. Worse, the glossary fix only made the *title* yield: the header's action cluster (view switcher + Import) is rigid at ~990px, so the header still overflowed horizontally at **every** width below 1000px — 449px of overflow at 420, 101px at 768 and 69px at 800x600, the narrow viewport `client/test/presentation/workstation_screen_test.dart` already pins for the dropzone. Fixed with a `LayoutBuilder` and `kGlossaryHeaderInlineBreakpoint = 1040`: above it the cluster stays inline exactly as before, below it drops to its own line inside a horizontal `SingleChildScrollView` — the same "scroll rather than overflow" treatment the dropzone uses, keeping `Import glossary` reachable. Measured clean at 420/600/768/800/850/900/1000/1039/1040/1400.
  2. **Neither client boundary gate ran in CI.** `client/tool/check_feature_layout.py` and `client/tool/check_feature_contracts.dart` were documentation-only, so the exact boundary this working tree was cleaning up was unenforced. The contract tool also `assert`s, which is a trap: run without `--enable-asserts` it prints `Feature JSON contracts passed.` and exits 0 *even when an assertion is false* (verified with a deliberately false assertion). Both are now steps in the `client-tests` job, renamed `flutter (analyze + test + boundary gates)`, with the contract invocation pinned to `--enable-asserts`; `docs/AGENTS.md` updated to the new job name.
  3. `client/integration_test/_test_helpers.dart` lost the comment explaining why `ConfigUpdate` needs a mocktail fallback when the now-redundant import was removed; the rationale is restored at the `registerFallbackValue` site.
  Regression tests added: `client/test/features/glossary/glossary_screen_test.dart` (narrow/wide header) and a short-height scroll test in `export_modal_smoke_test.dart`. Both are red against the pre-fix code — glossary `RenderFlex overflowed by 1221 pixels` at 420 and `241 pixels` at 1400; export modal `RenderFlex overflowed by 105 pixels on the bottom`. Verification: `flutter analyze --fatal-infos` → *No issues found* (exit 0); `flutter test` → **443 passed, 0 failed** (exit 0, +4 new); `check_feature_layout.py` → 170 Dart files, 31 providers (exit 0); `dart --enable-asserts tool/check_feature_contracts.dart` → passed (exit 0). No Python source, wire format or dependency changed.
- 2026-10-04 packaging: `PyInstaller omniscribe_server.spec` completes again (the `PermissionError` on `%APPDATA%\Python\Python313\site-packages` did not recur), but the first frozen binary was found **dead on boot** — `ModuleNotFoundError: No module named 'transformers.quantizers'`. Sprint 4's size optimisation had excluded that subpackage, but transformers 5.x reaches it by module-level import from `integrations.finegrained_fp8`. Removing the two `EXCLUDES` entries yields a binary that mounts all 14 plugins and serves `GET /api/health` → `200` with 57 routes. Dependency direction unchanged; no runtime code touched.
- 2026-10-04 gate execution: the previously unexecuted client gates now run in this environment and pass. `flutter analyze --fatal-infos` → *No issues found* (exit 0) after removing 24 findings; `flutter test` → **439 passed, 0 failed** (exit 0) after fixing 5 failures. Executing them exposed two real layout defects and one test-contract contradiction that the blocked runs had concealed. Backend re-verified: `ruff check` and `ruff format --check` clean over 465 files, `mypy src tests` clean over 444 files, fast suite **2845 passed / 18 skipped / 13 deselected** at **84.05%** coverage (exit 0). No runtime ownership, wire format or dependency changed.
- 2026-10-04 GitHub preparation: current assessment evidence now precedes historical claims and local scratch output is excluded from Git. CI typing passes for 444 files after narrow script/test corrections; the fast suite passes with 84.07% coverage. Flutter and release acceptance remain open. No runtime ownership changed.
- 2026-10-04: implemented the reproduced goal-alignment defects across existing client/OCR/runtime owners, with native SQLite dispatch ownership and PDF ActualText preservation. [Closeout evidence](docs/goal-alignment-assessment-2026-10-03.md#implementation-closeout-2026-10-04) records passing backend checks and live-model journeys; SDK, real Redis, current bundle and satisfactory accuracy gates remain open. See the [blueprint](docs/goal-alignment-closeout-blueprint-2026-10-04.md).
- 2026-10-03 historical revalidation: several DONE claims were partial, with confirmed export/trust, glossary parsing and Redis boot/ownership defects. [The revalidation](docs/goal-alignment-assessment-2026-10-03.md#independent-revalidation-2026-10-03) changed documentation only; the 2026-10-04 implementation above addresses its code findings.

### 2026-10-03 — Goal-alignment remediation (all 8 findings)
Historical implementation claims: [the assessment](docs/goal-alignment-assessment-2026-10-03.md). The independent revalidation and 2026-10-04 closeout supersede this table as acceptance evidence.

**Verification after this pass:** `mypy src` — no issues in 222 source files. `ruff check .` — all checks passed. `ruff format --check .` — 507 files already formatted. Full offline fast suite (`pytest -m 'not slow and not slow_dataset'`) — **2796 passed, 18 skipped, 13 deselected, 2 xfailed, 0 failed** in 145.89 s. `flutter test` — 426 passed. `flutter analyze` — 24 issues, all pre-existing and in files this pass did not touch. Feature-layout check — 169 Dart files, 31 provider declarations, passed. The baseline this replaced was 2675 passed / 2 failed.

| Finding | Added paths / responsibilities | Public contract boundary | Dependency direction |
| --- | --- | --- | --- |
| 1 — rich document persistence | `plugins/documents/artifact.py` stores/loads the capability-bound rich `DocumentResult`; `core/document.py` gains lossless `to_dict`/`from_dict` | `ExportHtmlRequest` and subclasses gain `document_artifact_id`/`document_artifact_token`; `JobStatusResponse` gains `document_artifact_id` | `plugins/documents/routes` → `documents/artifact` → `core/document`. `core` never imports `plugins`. The legacy text artifact stays byte-identical, so old clients and old jobs keep working. |
| 2 — client export authority | `workstation_notifier.hydratePagesFromTextArtifact` reconciles per line occurrence, appending only artifact lines no live block already represents; `export_notifier` derives text from the completed artifact | No wire-format change; the searchable-PDF path is untouched | client feature → its own repository. Live `block_complete` coordinates always win over artifact placeholders, which are tagged `hydrated-from-artifact`. |
| 3 — quality options wired | `OCRRequest.quality_options` (typed, validated); `ocr/pipeline_bridge.build_pipeline` injects a real `TrustOrchestrator` through the existing `build_trust_orchestrator()` factory; `table_fallback` added to the processor allowlist | `quality_options` form field; invalid values → 422 naming the field | `plugins/ocr` → `pipeline_bridge` → `core/ocr_quality`. No orchestrator is built unless the user opts in, so the no-orchestrator default is byte-identical. |
| 4 — NLLB language resolution | `core/translate/nllb.py` maps every client-offered label and raises `UnsupportedLanguageError` instead of silently falling back to English; `DEFAULT_SOURCE_LANGUAGE` makes the pinned source an explicit decision | `NllbRequest.target_language` validated at the edge → 422; the service re-checks before loading model weights → 400 | `core/translate` does not depend on `plugins`. Only the NLLB route is language-constrained; the LLM path accepts any name. |
| 5 — one broker per worker | `JobsPlugin`/`ProgressPlugin` `apply` prefer their injected `redis_url`/`mode` over a second `load_settings()`; startup marks interrupted non-terminal SQLite work terminal | No wire-format change; `OMNISCRIBE_ARTIFACT_DIR` / `--redis-url` precedence unchanged | `worker.boot_worker_context` is the single resolution point; plugins consume what they are handed. |
| 6 — real failure surfaces | OCR status/list carry the real `failed_pages` set (was hardcoded `[]`); `GlossaryJobStatus` + `GlossaryRepository.getImportJobStatus` let the client follow a queued import | `JobStatusResponse.failed_pages`, `JobListItemResponse.failed_pages`; glossary polls `GET /api/process/status/{job_id}` | the client glossary feature uses its own repository — it does not import `features/workstation`. |
| 7 — measurement matches output | `confidence_eval` scores the canonical exported Markdown instead of a bbox/text join; machine-readable JSON results and a failing exit status | CLI gains a strict-by-default exit rule for an incomplete requested run | `scripts/` → `omniscribe.confidence_eval` → the real export builders. |
| 8 — promises and storage | BMP/TIFF accepted by the upload allowlist and sniffer and offered by the dropzone; `_default_artifact_base_dir()` gives durable per-user state; the embedder warns on shaping-dependent scripts | `_SUPPORTED_UPLOAD_FORMATS` is now the single source of the 415 message | `core/pdf` never imports `plugins`. The artifact-dir default degrades to temp only when no per-user location exists at all. |

**Known limitations now pinned by tests, not silently carried**
- Arabic — and any mixed-script block containing it — does not round-trip through the PDF text layer: it extracts as presentation forms, so search and copy of the logical string fail. Pinned with `strict=True` xfail markers in `tests/core/pdf/test_embedder.py`; a fix needs a shaper (e.g. libraqm) in the embed path.
- The Windows server binary and Flutter client are still built and attached manually; the release workflow publishes wheel/sdist only. Documented under "Publishing release assets" in `docs/deployment/windows-bundle.md` rather than claiming automation that does not exist.
- Real-Redis concurrency and worker-restart recovery, real-OCR table reconstruction fidelity, and every accuracy number remain unverified in this environment.

- Extended backlog verification tools for concurrent Redis jobs/recovery and opt-in Flutter live OCR; added export readiness/replacement widget assertions. See [the scoped blueprint](docs/outstanding-work-blueprint-2026-09-30.md) for validation and remaining live/SDK gates.
- Fixed document identity and replacement/cancellation races, tracked processed PDF readiness, validated export prerequisites, and consolidated two redundant test compatibility barrels; see [the current review](docs/document-state-review-2026-09-30.md) for verification limits.
- Organized Flutter by feature ownership, split four-domain models/state/API adapters, isolated shared transport, and moved export preparation into documents state management; every moved/new source has a single responsibility in the canonical ledger.
- Preserved Python feature modules while removing 19 redundant error wrappers, decoupling OCR domain errors from HTTP exceptions, validating transcription outputs, and retaining JSON 422 validation responses.
- Shared tagged job-runner resolution between the in-process queue and Redis worker; missing tagged registrations now fail explicitly while untagged jobs retain the default OCR runner.
- Reused the in-process queue's trusted spool containment check in the Redis queue while retaining each queue's cleanup policy.
- Scoped optional TrOCR failure recovery to recognition, allowing errors from the subsequent VLM correction call to propagate.
- Consolidated client preview response parsing and sync/async OCR artifact hydration without changing their distinct request and run-guard behavior.
- Hardened Redis job queue payload deserialization with strict spool directory confinement and staged path cleanup on pre-execution cancel.
- Guaranteed persistent terminal state in `StateBackend` prior to worker queue lease acknowledgment / completion.
- Added atomic Lua script for lease-checked requeue during worker drain, eliminating TOCTOU claim races.
- Hardened OCR execution against recursive directory deletion via strict temp/spool root and prefix validation.
- Extended SSE stream progress event bus dispatching to observe worker completions across distributed processes.
- Guarded glossary route extension inference against non-path formats (`sqlite`, `db`, `xml`).
- Added `X-Job-Token` to CORS `allow_headers`.
- Enforced header-only text and metadata capability tokens for document export GET endpoints; POST credentials remain in request bodies.
- Enforced serial page rasterization over single PyMuPDF `fitz.Document` instances in `omniscribe.core.pdf.rasterizer` to eliminate thread-unsafe native MuPDF memory corruption and race conditions.
- Hardened client `JobOrchestrationNotifier.cancelOcr` to guarantee local cancellation (`isProcessing: false`, stage `'Cancelled'`) and teardown with warning attribution even if remote server cancellations fail, and optimized `WorkstationState` buffer comparison/hash to eliminate multi-megabyte byte traversal and hashing on state transitions.
- Bound claimed Redis OCR payload paths to the persisted `JobRecord.input_path` and claimed job ID; invalid payloads fail terminally and clean only the recorded input.
- Relayed worker terminal frames (`complete`, `failed`, `cancelled`) over Redis Pub/Sub progress channels and re-emitted them into the local in-process `Context` bus in `ProgressService`, unblocking distributed SSE event listeners.
- Hardened `OCRService.wait_for_events` against SSE polling deadlocks with bounded 2.0s timeouts and proactive `StateBackend` terminal state checks.
- Enforced staged OCR upload cleanup beneath spool roots on job cancellation and graceful shutdown in `InMemoryJobQueue`.
- Hardened PDF embedder and synthetic reader pipelines with zero-page PDF fallback pages, Unicode fast-path font selection preventing non-Latin script degradation (Arabic, CJK, Hebrew, Cyrillic), robust bbox boundary epsilon tolerance in intermediate representation (`_normalize_bbox`), and coordinate clamping in grounded workflow page grouping (`_group_blocks_by_page` / `_accumulate_pages`).
- Hardened security, SSRF, and authentication layers: extended `BearerAuthMiddleware` to gate ASGI `websocket` scopes (validating bearer token via `Authorization: Bearer` or `?auth_token=` / `?token=` query parameters and rejecting unauthenticated handshakes with WebSocket close code 4401); added `create_pinned_client` in `omniscribe.utils.security` to bind HTTP client transport connections to pre-validated SSRF target IPs; updated `complete_vlm_prompt`, `call_vlm`, and `call_llm` to accept an optional `http_client` instance; and wired pinned clients into `documents` and `translate` services upon successful SSRF validation to eliminate DNS rebinding TOCTOU vulnerabilities for both HTTP and HTTPS endpoints. The OCR VLM pre-flight probe received the same treatment on 2026-10-04 — see "Latest Synchronizations" above.
- Added `omniscribe-import-lanes-lexicon` CLI and `lanes_sqlite`/`lanes_xml` source parsers in `omniscribe.core.glossary_sources.lanes_lexicon` for ingesting Lane's Arabic-English Lexicon into LanceDB, with fail-closed schema protection in `open_terms_table`.
- Optimized OCR crop and pre-processing pipeline: switched blank-region detection to NumPy stddev (1.5x faster), parallelized `crop_many_for_ocr_from_image` with a persistent thread pool, switched upscale resampling to BICUBIC (1.55x faster), and memoized bbox crops across repair loop retries.

## Refactor Artifact

`docs/modularity-review-2026-09-29.md` consolidates this Flutter/Python refactor. The linked Flutter/backend artifacts contain concrete layouts, exact before/after changes and complete source responsibilities; `docs/contract-review-2026-09-29.md` records the independent contract review. `client/tool/check_feature_contracts.dart` and `client/tool/check_feature_layout.py` provide runnable boundary/ownership checks.

`docs/refactor-blueprint-2026-09-29.md` records this refactor's planned feature ownership, parallel implementation scope and preservation checks. The earlier `docs/refactor-blueprint-2026-09-27.md` records the preceding scoped refactor and its verification plan.
