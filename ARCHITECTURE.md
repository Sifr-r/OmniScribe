# Architecture Ledger

The canonical Architecture Ledger for OmniScribe is located at [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Please refer to `docs/ARCHITECTURE.md` for the complete system shape, pipeline topology, plugin tree, component catalog, extension points, and performance specifications.

## Latest Synchronizations
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

`docs/refactor-blueprint-2026-09-27.md` has one responsibility: record the scoped file ownership and verification plan for this refactor. It introduces no new production modules.
