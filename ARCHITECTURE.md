# Architecture Ledger

The canonical Architecture Ledger for OmniScribe is located at [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Please refer to `docs/ARCHITECTURE.md` for the complete system shape, pipeline topology, plugin tree, component catalog, extension points, and performance specifications.

## Latest Synchronizations
- 2026-10-04 GitHub preparation: current assessment evidence now precedes historical claims and local scratch output is excluded from Git. CI typing passes for 444 files after narrow script/test corrections; the fast suite passes with 84.07% coverage. Flutter and release acceptance remain open. No runtime ownership changed.
- 2026-10-04: implemented the reproduced goal-alignment defects across existing client/OCR/runtime owners, with native SQLite dispatch ownership and PDF ActualText preservation. [Closeout evidence](docs/goal-alignment-assessment-2026-10-03.md#implementation-closeout-2026-10-04) records passing backend checks and live-model journeys; SDK, real Redis, current bundle and satisfactory accuracy gates remain open. See the [blueprint](docs/goal-alignment-closeout-blueprint-2026-10-04.md).
- 2026-10-03 historical revalidation: several DONE claims were partial, with confirmed export/trust, glossary parsing and Redis boot/ownership defects. [The revalidation](docs/goal-alignment-assessment-2026-10-03.md#independent-revalidation-2026-10-03) changed documentation only; the 2026-10-04 implementation above addresses its code findings.

### 2026-10-03 — Goal-alignment remediation (all 8 findings)
Historical implementation claims: [the assessment](docs/goal-alignment-assessment-2026-10-03.md). The independent revalidation and 2026-10-04 closeout supersede this table as acceptance evidence.

**Verification after this pass:** `mypy src` — no issues in 222 source files. `ruff check .` — all checks passed. `ruff format --check .` — 507 files already formatted. Full offline fast suite (`pytest -m 'not slow and not slow_dataset'`) — **2796 passed, 18 skipped, 13 deselected, 2 xfailed, 0 failed** in 145.89 s. `flutter test` — 426 passed. `flutter analyze` — 24 issues, all pre-existing and in files this pass did not touch. Feature-layout check — 169 Dart files, 31 provider declarations, passed. The baseline this replaced was 2675 passed / 2 failed.

| Finding | Added paths / responsibilities | Public contract boundary | Dependency direction |
| --- | --- | --- | --- |
| 1 — rich document persistence | `plugins/documents/artifact.py` stores/loads the capability-bound rich `DocumentResult`; `core/document.py` gains lossless `to_dict`/`from_dict` | `ExportHtmlRequest` and subclasses gain `document_artifact_id`/`document_artifact_token`; `JobStatusResponse` gains `document_artifact_id` | `plugins/documents/routes` → `documents/artifact` → `core/document`. `core` never imports `plugins`. The legacy text artifact stays byte-identical, so old clients and old jobs keep working. |
| 2 — client export authority | `workstation_notifier.hydratePagesFromTextArtifact` reconciles only pages with no live blocks; `export_notifier` derives text from the completed artifact | No wire-format change; the searchable-PDF path is untouched | client feature → its own repository. Live `block_complete` coordinates still win over artifact placeholders. |
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
- Hardened security, SSRF, and authentication layers: extended `BearerAuthMiddleware` to gate ASGI `websocket` scopes (validating bearer token via `Authorization: Bearer` or `?auth_token=` / `?token=` query parameters and rejecting unauthenticated handshakes with WebSocket close code 4401); added `create_pinned_client` in `omniscribe.utils.security` to bind HTTP client transport connections to pre-validated SSRF target IPs; updated `complete_vlm_prompt`, `call_vlm`, and `call_llm` to accept an optional `http_client` instance; and wired pinned clients into `documents` and `translate` services upon successful SSRF validation to eliminate DNS rebinding TOCTOU vulnerabilities for both HTTP and HTTPS endpoints.
- Added `omniscribe-import-lanes-lexicon` CLI and `lanes_sqlite`/`lanes_xml` source parsers in `omniscribe.core.glossary_sources.lanes_lexicon` for ingesting Lane's Arabic-English Lexicon into LanceDB, with fail-closed schema protection in `open_terms_table`.
- Optimized OCR crop and pre-processing pipeline: switched blank-region detection to NumPy stddev (1.5x faster), parallelized `crop_many_for_ocr_from_image` with a persistent thread pool, switched upscale resampling to BICUBIC (1.55x faster), and memoized bbox crops across repair loop retries.

## Refactor Artifact

`docs/modularity-review-2026-09-29.md` consolidates this Flutter/Python refactor. The linked Flutter/backend artifacts contain concrete layouts, exact before/after changes and complete source responsibilities; `docs/contract-review-2026-09-29.md` records the independent contract review. `client/tool/check_feature_contracts.dart` and `client/tool/check_feature_layout.py` provide runnable boundary/ownership checks.

`docs/refactor-blueprint-2026-09-29.md` records this refactor's planned feature ownership, parallel implementation scope and preservation checks. The earlier `docs/refactor-blueprint-2026-09-27.md` records the preceding scoped refactor and its verification plan.
