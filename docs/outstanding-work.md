# OmniScribe — Outstanding Work

**Consolidated:** 2026-08-31  
**Updated:** 2026-09-06 (v0.3.0 shipped at `6f43d30`; finetunement remediation wave — see §7 status map)  
**Sources:** `docs/audits/2026-08-30-pedantic-review.md`, the deferred Medium/Low backlog of the 2026-08-29 five-domain audit, the 2026-09-04 [Five-Lens Audit](audits/2026-09-04-five-lens-audit.md) and [Remediation Plan](audits/2026-09-04-remediation-plan.md), the 2026-09-06 [v0.3.0 RFC 002](rfcs/2026-09-v0.3.0-scope.md), and Phase C follow-ups.

All completed items (audit-remediation sprints 1–6, Phase C plugin slices 1–3, and Waves 1–14) have been closed and verified. Historical records are preserved in git history (`git log --grep="Wave"`).

## Current focus (2026-09-06)

- **v0.3.0 shipped** (2026-09-06, `6f43d30`): the 307 MB single-binary
  Windows bundle boots and serves `/api/health -> 200`; the release is
  tagged and the changelog updated. RFC 002 Sprints 1–2 are complete.
- **Sprint 3 — U12 "try with sample PDF"** ✅ (closed 2026-09-07).
  New `omniscribe.plugins.sample_pdfs` Cordis plugin serves a
  fixed allowlist of 5 canonical fixture PDFs at
  `GET /api/sample-pdf/{name}`. Path traversal is a structural
  impossibility (user input never joins a filesystem path);
  the path is auth-exempt so the Profile 1 loopback Flutter
  client can hit it without a token. The Flutter Workstation
  empty-state header now has a "Try sample PDF" `AppButton`
  that fetches the default fixture and stages the bytes as
  the active document so the existing Run OCR flow takes
  over. 24 new tests across pytest (15) and Flutter (9).
  Bundle rebuild landed at 522 MB (was 307 MB at the v0.3.0
  cut) — `collect_submodules` drift across PyInstaller runs
  pulled in extra transitive deps; the size is functional
  but worth a Sprint 4 trim pass.
- **Next up (per RFC 002):** Sprint 4 — buffer / spillover
  (trim the bundle, re-upload the new binary vs cut v0.3.1,
  Redis state backend if Profile 4 in flight, Q11 chaos test
  first slice, additional mypy strict, or clean cut).
- **Finetunement remediation wave (2026-09-06):** four-domain polish
  audit (core / harness+plugins / Flutter client / repo hygiene) executed
  in one pass — SQLite `started_at` persistence, upload-cap fallback,
  SSE sequence cursors, lazy repair-page decode, plugin `PluginError` /
  `TrimmedModel` dedup, dead config knobs, Flutter UX fixes, and the
  §7 status-map prune below.
- **LLM-application remediation (2026-09-06):** the 2026-09-06
  LLM-app-quality audit was remediated in three subsystems
  (`docs/superpowers/specs/2026-09-06-llm-remediation-design.md`).
  Subsystem 1 (translation + lexicon: fail-safe judge, best-attempt
  tracking, script-aware length bands, RRF hybrid lexicon search,
  migration upsert) and subsystem 2 (OCR / trust layer / transcription:
  block confidence at the `from_pages_data` choke point, trust fields on
  the block-tree JSON + `X-Document-Trust` header + Flutter export-modal
  summary, `exp(avg_logprob)` transcription confidence, whisper
  robustness kwargs, hoisted audio-API client with Retry-After backoff,
  informed repair re-OCR with per-attempt temperature bump, text-layer
  agreement as a fluent-hallucination repair trigger, GLM parser bbox
  hardening with structured drop events, correction-pass fallback, and
  the §6.55 PROMPT_VERSION split) are complete and green. Subsystem 3
  (this doc + ARCHITECTURE.md refresh) closes the wave.

---

## 1. Pedantic Review — Medium-Priority Findings (all resolved)

*All items in this section have been resolved:*
- **2.6** `plugins/ocr/service.py` — Prune is the single source of truth for bounding per-job maps (closed in Wave 9).
- **2.8** `plugins/ocr/service.py` / `_OcrPayload` — Replaced in-memory upload bytes with streaming pipeline and per-job spooling (closed in Wave 9 & Wave 12).
- **3.6** `JobStatusResponse` — Reconciled documentation on SSE-delivered token vs polled-result design (closed in Wave 9).

---

## 2. Harness & Plugin Seams (Post-Phase-C)

*All items in this section have been resolved:*
- **9.8** `plugins/glossary/plugin.py` — Evaluated lazy initialization and reload handling (closed in Wave 9).
- **9.9** `plugins/translate/service.py` — Aligned empty-text semantics with route contract (closed in Wave 9 & Wave 13).
- **9.10** `plugins/translate/service.py` — Decoupled `TRANSLATION_SYSTEM_MESSAGE` via stable export from `omniscribe.core.translate` (closed in Wave 13).
- **9.11** `plugins/transcribe/service.py` — Flattened 4-step config fallback with helper (closed in Wave 9).
- **9.12** `plugins/transcribe/service.py` — Co-located `unpack_transcribe_options` helper next to `TranscribeRequest` schema (closed in Wave 13).
- **9.13** `plugins/transcribe/service.py` — Narrowed unused imports block (closed in Wave 9).
- **9.17** Audited new route modules for uniform envelope, union return types, and SSRF validation (closed in Wave 9 & Wave 12).

---

## 3. Test Gaps

*All items in this section have been resolved:*
- **5.1** Added test for `_OcrPayload` round-trip and eviction lookup miss (closed in Wave 9).
- **5.3** Python optimization (`-O`) assertion regression test covered (closed in Wave 7).
- **5.4** Added 200-event rapid burst test pinning per-job replay deque (closed in Wave 9).
- **5.5** Covered `plugins/jobs.py` paginated shutdown under 1500 queued jobs (closed in Wave 9).
- **5.7** Added frontend Flutter test asserting strict discrimination between `cancelled` and `error` status (closed in Wave 13).

---

## 4. Five-Domain Audit Deferred Backlog

*All actionable items in this section have been resolved across Waves 8–14:*

### Domain 1 — Core Pipeline (CLOSED)
- Refine stage decodes target pages on-demand using run-scoped cache (Wave 13).
- Fresh unclosed `AsyncOpenAI` client lifecycle resolved with lazy initialization and ephemeral probes (Wave 12).
- Grounded `ensure_model_loaded` uses ephemeral client closed in `finally` (Wave 12).
- First-use model loads offloaded to thread pool (Wave 8).
- Embedder batches page rasterization in bounded chunks of 16 (Wave 13) and applies `garbage=3, deflate=True` stream compression (Wave 14).
- Cancelled grounded tasks properly awaited and cleaned up (Wave 12).
- $O(1)$ block lookup in `grounded.py` repair loop (Wave 13).
- Single-pass image decode in layout stage (Wave 13).
- Dead `input_path` parameter removed (Wave 14).
- Defensive copying on `trust_images_dict` (Wave 14).
- Explicit `last_exc` invariants for `-O` execution (Wave 14).

### Domain 2 — API & Security (CLOSED)
- Byte-budget streaming upload parsing and size enforcement (Wave 12).
- Full ASGI Middleware Suite restored: Bearer Auth (`auth.py`), Rate Limiting (`rate_limit.py`), and Upload Size Limiting (`upload_limit.py`) (Waves 11, 13, 14).
- Startup validation in `create_app()` prevents uvicorn direct-bind bypass of non-loopback and placeholder tokens (Wave 13).
- `DELETE /api/jobs` protected with `confirm=true` requirement to prevent accidental wipes (Wave 14).
- WebSocket Origin validation against `cors_origins` (Wave 13).
- Constant-time token comparisons (`secrets.compare_digest`) across backends and progress channels (Wave 13).
- Provider API keys accepted via `X-Provider-Api-Key` and `Authorization` headers (Wave 13).
- `CircuitOpenError` mapped to HTTP 503 with standard `Retry-After` header (Wave 14).
- Sanitized `ValueError` detail responses (Wave 13).
- POSIX `0o700` permission enforcement on state directories (Wave 14).

### Domain 3 — Frontend / Flutter Client (CLOSED)
- Workstation async submit fallback polls `getJobStatus` on unexpected WebSocket disconnection and downloads results (Wave 14).
- Result token passed exclusively via Authorization header (Wave 13).
- Real `ServerHealthNotifier.checkHealth` pinging `/api/health` replaces simulated badge (Wave 12).
- File download persists to disk via `FilePicker.platform.saveFile` (Wave 13).
- Dead API constants removed (Wave 13).
- `isCancelled` status discrimination tested and verified (Wave 13).

### Domain 4 — Testing & QA (CLOSED)
- Dedicated unit tests for `page_preprocess.py` (`tests/core/imaging/test_page_preprocess.py`) (Wave 14).
- Dedicated unit tests for `routing.py` (`tests/core/ocr_quality/test_routing.py`) (Wave 14).
- Dedicated unit tests for `local_engine.py` (`tests/core/transcription/test_transcription.py`) (Wave 13).
- Dedicated unit tests for `embedder.py` (`tests/core/pdf/test_embedder.py`) (Wave 14).
- Dedicated unit tests for `config.py` (`tests/test_config.py`) (Wave 14).
- Dedicated unit tests for ASGI middleware triad (`tests/middleware/`) (Waves 11, 13, 14).
- OpenAPI snapshot drift contract test passes (Wave 13 & 14).
- Merged single-test `tests/ops/` directory into `tests/scripts/` (Q10 resolved).
- Under-tested modules wave (Q8 resolved):
  - Local and API audio transcription engine tests (`tests/core/transcription/test_transcription_engines.py`)
  - Grounded OCR prompt builder, chunking, coordinate clamping, reading order, and JSON repair tests (`tests/core/grounded/test_prompted_grounded_ocr.py`)
  - Glossary HTTP fetch, redirect limits, SSRF private IP blocking, body size guards (`tests/plugins/test_glossary_http_fetch.py`)
  - Glossary library routes, source toggle/reorder, query pagination, and LanceDB 503 fallback (`tests/routers/test_glossary_library_routes.py`)
  - Glossary source encoding auto-detection and XLIFF 1.2/2.0 parsing (`tests/core/glossary_sources/test_encoding_and_xliff.py`)

### Domain 5 — DevOps & Config (CLOSED)
- `.env.example` provides working default `REDIS_PASSWORD` allowing `cp .env.example .env && docker compose up` without failure (Wave 14).
- `compose.yaml` aligned and verified (Wave 14).
- Cleaned up stale `# force_run` comment in `nightly.yml` (Wave 14).
- Pinned toolchain versions and security workflows aligned (Wave 14).
- Security contact PGP policy documented in `docs/SECURITY.md` (sensitive reports request fingerprint out-of-band; static PGP key omitted to prevent unmanaged key rot; P13 closed / N/A).

---

## 5. Phase C Architecture Follow-ups

- **Fourth-Producer Registry:** If a fourth runner producer appears beyond OCR (`JobRunner`), Translation (`TranslationJobRunner`), and Glossary (`GlossaryJobRunner`), generalize `JobQueue` dispatch to an explicit registry.
- **Transcribe Spec Drift (Informational):** Text artifacts are stored as page-dict JSON (`application/json`), not literal `text/plain`; response `job_id` is a synthetic `job-<hex>` used as artifact owner for pruning. Documented in contract.
- **Flutter Client Paired Changes:** Pedantic finding 2.2 (`AsyncOpenAI` client lifecycle) requires paired client verification when scheduled.

---

## 6. Deferred Architectural Capabilities

High-level capabilities deferred during the harness rebuild and not yet
shipped. Each entry points at the unblocker.

> **Removed 2026-09-05:** the ASGI Middleware Suite
> (bearer auth + rate limit + upload size) was previously listed here.
> It shipped in Waves 11, 13, and 14 — see §4 Domain 2 closure record
> and [SECURITY.md](SECURITY.md) §Security Features for the current
> contract.

1. **Redis State Backend:** Complete `RedisStateBackend` for distributed deployments (`OMNISCRIBE_STATE_BACKEND=redis` currently crashes at plugin apply).
2. **Model Pre-flight Route:** Formal API endpoint for VLM pre-flight verification against silent fallback. `ensure_model_loaded()` exists in `core/ocr/processor.py`; the public route is unbuilt.
3. **Full Regression Datasets (`slow_dataset`):** `scripts/fetch_datasets.py` execution once upstream licenses clear for OCR-Quality and KIE-HVQA benchmarks.

---

## 7. Low-Priority Naming, API & Style Smells

*Status verified against source on 2026-09-06 by the finetunement audit
(two agents re-read every item's target file). Most entries were closed by
the Phase 6 long-tail batches and Waves 8-14 but were never pruned from
this list. What follows is what is actually still open.*

### Still open — naming & API smells

- **4.1** `cors_origins_raw` property is referenced nowhere in src (the deprecated input field was removed in D10; the read-only property is test-pinned). Delete in a future breaking pass.
- **4.7** `WhitespaceRecallOptions` / `TextLayerRecallOptions.from_env` twins — extract a shared base (`core/recall/whitespace.py:100`, `text_layer.py:63`).
- **4.9** `input_path: str = ""` dead default in `_detect_layout` (`hybrid.py:347`); sole caller always passes it.
- **4.11** Four names for two concepts: `result_artifact_id` (`state_backend_types.py:61`) vs `artifact_id` (`jobs.py:66`) vs `text_artifact_id` / `translated_artifact_id` (plugin layers).
- **4.18** SSE loop's clear-on-wake `asyncio.Event` can flap (lost wake, not lost data — the seq-stamped deque is authoritative since 2026-09-06).
- **4.19** `max_buffered_jobs` caps three structures with two eviction loops (`ocr/service.py` prune paths); fold.
- **4.20** `update_config` mutates shared `RuntimeSettings` mid-flight; document "applies to subsequent requests".
- **4.23** `_QUEUE_STATUS_TO_HTTP` should live next to the response schema.
- **4.27** `env_int` logs a warning on bad input; `env_bool` / `env_list_csv` silently default (`utils/env.py`).
- **4.28** `env_list_csv` vs `env_str` empty-value semantics differ (`""` → `[]` vs `None`).
- **4.31** Loader `row = replace(row, ...)` rebind shadows traceback context (`harness/loader.py:145,199`).
- **4.36** Per-candidate scan over existing boxes is O(n·m) on pathological box counts — inherent to the filter; the 2026-09-06 fused `geometry.is_duplicate` halved the constant.
- **4.42** Exponential backoff cumulative sleep budget undocumented (`core/ocr/chat_client.py`).
- **4.43** Context-length error message is LM Studio-specific on a generic client.
### Still open — style nits

- **6.4** `HybridEngine.__init__` is now a 10-kwarg permanent API surface.
- **6.6** `_KERNEL_W_RANGE` / `_KERNEL_H_RANGE` tuples; named MIN/MAX constants would read better.
- **6.8** Triple-`or` candidate filter (`whitespace.py`); three named predicates would scan better.
- **6.14** Default-arg closure binding — fixed in `hybrid_repair.py` (2026-09-06, lazy `get_page_image`); `grounded.py` arbitration still uses the pattern.
- **6.16** 0-based `range(max_retries + 1)` reads cryptic (`chat_client.py`, `grounded/prompted.py`).
- **6.17** Post-loop error translation duplicates `is_transient_error` context-length terms (`resilience.py` vs `chat_client.py`).
- **6.26** Memory backend caps blobs at 256 MB; sqlite backend is uncapped — clarify intent.
- **6.38** `_split_processors` only handles comma-joined form fields; repeated keys drop (`ocr/schemas.py`).
- **6.39** `preprocessing_enabled` property couples HTTP naming to behavior (`ocr/schemas.py`).
- **6.46** `_select_dense_pages` stage keeps a `str | DenseMode` union and coercion branch; the engine already enforces `DenseMode`.
- **6.50** 24-line rationale comment blocks in `processor.py` (F1.9); trim to pointers.
- **6.53** Substring scan over a frozenset buys nothing (`core/ocr/prompts.py:85`).
- **6.55** Three `PROMPT_VERSION` constants share `"2026-08-15.v1"` by coincidence (`prompts.py`, `grounded/prompted.py`, `translate/nodes.py`) — version-bump hazard.
- **6.69** `completed_box` list-counter in `hybrid_repair.py` vs `nonlocal` in `grounded.py` — two patterns for one concern.
- **6.78** Progress `frame_cap` is soft when done-callbacks never fire (`progress.py`).
- **6.79** `broadcast` returns submission count, not delivery successes; docstring says "fan-out count" (`progress.py`).
- **6.81** Extensionless upload filenames fall back to `.pdf` (`content_sniff.py`).
- **6.86** Masked `api_key == "******"` skip contract is subtle; document it.
- **6.88** `OCRRequest` is 19 fields / 4 validators; consider a nested config object.
- **6.89** `_coerce_bool` field list duplicates the model's field declarations.
- **6.63-6.66** Hybrid re-injection wrappers are pass-throughs **kept deliberately**: tests drive the engine through these seams (~45 call sites), so inlining is churn without behavior change. Revisit only with a test-migration pass.

### Resolved (verified 2026-09-06, pruned from the old list)

4.2, 4.3, 4.4, 4.5, 4.8, 4.10, 4.12, 4.13, 4.14, 4.16, 4.17, 4.21, 4.25, 4.26,
4.29, 4.30, 4.32, 4.37, 4.38, 4.39, 4.40, 4.41, 6.3 (dead field deleted),
6.7, 6.9, 6.13, 6.30, 6.31, 6.34, 6.35, 6.40, 6.42, 6.45, 6.47, 6.48, 6.49
(accept-always documented; superseded by the LLM-remediation wave — an
empty/fallback correction pass now *falls back to the first pass* instead of
erasing it, `31dee3b`), 6.51, 6.52, 6.55 (renamed to module-scoped constants:
`grounded/prompted.py::GROUNDED_PROMPT_VERSION`,
`translate/nodes.py::TRANSLATION_PROMPT_VERSION` — the OCR and documents
prompts keep their own `PROMPT_VERSION`), 6.56, 6.68, 6.70, 6.74, 6.75, 6.76,
6.77, 6.82, 6.83, 6.87.

### Not applicable

- **4.15** `omniscribe-migrate-lexicon` ships deliberately (AGENTS.md documents the exception).
- **6.71** No frozen dataclass carries unhashable fields; `GroundedBlock` is deliberately unfrozen.
- **6.94** The final re-sort makes text-layer grouping order harmless.
