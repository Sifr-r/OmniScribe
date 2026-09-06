# Architecture Ledger

## System Shape

`omniscribe` is a Python 3.11+ Web UI/API OCR application with a shared
pipeline behind the FastAPI server. Inputs are PDFs or images. Outputs are
searchable sandwich PDFs with normalized OCR bounding boxes embedded as an
invisible text layer.

## Pipeline

```text
PDF/image -> raster pages -> Surya detection (+ optional whitespace + text-layer recall) -> sparse: full-page VLM OCR -> DP alignment --+
                                      \-> dense: per-box VLM OCR -----------------------------------------------------------------------+-> optional refine -> optional quality repair -> optional post-process -> DocumentResult -> optional document processors -> searchable PDF

PDF/image -> grounded bbox-native VLM OCR -> optional quality repair -> optional post-process -> DocumentResult -> optional document processors -> searchable PDF
```

The optional whitespace-recall pass (`core/recall/whitespace.py`, hybrid path only,
default on, kill switch `OMNISCRIBE_WHITESPACE_RECALL`) merges conservative
pixel-statistics text-line candidates into the Surya boxes before dense
selection, OCR, and alignment. It fails open: any per-page error degrades to
the original Surya boxes.

The optional text-layer-recall pass (`core/recall/text_layer.py`, hybrid path
only, default on, kill switch `OMNISCRIBE_TEXT_LAYER_RECALL`) is the second
recall source: on digital PDFs it recovers lines Surya missed straight from
the embedded text layer (`page.get_text("words")`), merged after the
whitespace booster so its dedup sees both sources' extras. Scanned pages and
image inputs have no text layer, making the pass a strict no-op there. Same
fail-open contract: any per-page error degrades to the boxes merged so far,
and each pass logs one INFO run summary per job.

The HTTP layer mounts this pipeline through the plugin harness: `server.py`
loads `resources/cordis.yml` inside the FastAPI lifespan, and the `ocr`
plugin's `pipeline_bridge.py` assembles one `OCRPipeline` per upload
(shared Surya aligner singleton, request-scoped LLM coordinates).

## Plugin Tree

Boot order (from `resources/cordis.yml`; plugins apply top-to-bottom and
dispose LIFO on shutdown):

```text
cordis.yml
├─ runtime        RuntimeService: RuntimeSettings holder, readiness flag,
│                 artifact/channel prune cadence (HarnessReady event)
├─ logging        structured logging (text|json format, level) — side effect only
├─ state_backend  StateBackend service: sqlite (default, durable persistence)
│                 or memory (OMNISCRIBE_STATE_BACKEND); single registration site
├─ artifacts      ArtifactStore: opaque id/token blob store over the backend
├─ jobs           JobQueue: single-worker async queue + JobQueued/Started/
│                 Completed/Failed/Cancelled events; resolves the JobRunner
│                 the ocr plugin registers at claim time
├─ progress       ProgressService: one-shot session tokens, WS attach with
│                 cross-loop send marshaling; /api/progress/* + /ws/{channel_id}
├─ providers      provider catalog + model discovery (/api/providers*)
├─ health         liveness (/api/health, /api/healthz) and readiness (/ready, /readyz)
├─ documents      extraction + export routes over the token-bound ArtifactStore:
│                 POST /api/extract (extraction prompts re-homed verbatim,
│                 PROMPT_VERSION 2026-08-15.v1), /api/export/* builders
│                 (text/markdown/json/docling/mineru), and the token-bound
│                 /api/export/{id}, /api/text/{id}, /api/metadata/{id} fetches
├─ translate      TranslationService + TranslationJobRunner: POST /api/translate
│                 (sync single-shot), POST /api/translate/async (tree-aware,
│                 dispatched on the harness JobQueue; the translated text is
│                 stored as a token-bound artifact — the status summary carries
│                 artifact ids, never tokens), GET /api/translate/status/{job_id},
│                 GET /api/translate/result/{job_id} (token-redeeming async
│                 result fetch; wrong token → uniform 404), and
│                 POST /api/translate/nllb
├─ transcribe     TranscriptionService: POST /api/transcribe (sync multipart
│                 transcription; the transcript and its metadata are stored
│                 as token-bound artifacts), GET/POST /api/config/transcription
│                 (masked keys, always-writable in-memory store), and
│                 GET /api/models/transcription (SSRF-guarded endpoint
│                 discovery with a whisper fallback list)
├─ glossary       GlossaryImportService + GlossaryJobRunner (the JobQueue's
│                 third runner producer): /api/glossary* routes —
│                 dual-shape imports (legacy JSON source envelope or the
│                 client's multipart / JSON-body shapes), library / sources
│                 CRUD and listing (/api/glossary/sources, /api/glossary/sources/{id},
│                 /api/glossary/library, /api/glossary/library/{id}), toggling
│                 (/api/glossary/library/{id}/toggle with optional state flipping,
│                 /api/glossary/library/{id}/enable), reorder, entries querying
│                 with case-insensitive filtering and pagination (/api/glossary/library/entries,
│                 /api/glossary/library/{id}/entries), and preview/merged. Imports above
│                 the 5,000-entry estimate dispatch on the harness JobQueue;
│                 the LanceDB lexicon store loads lazily — routes 503 with
│                 an install hint when the `lexicon` extra is missing
└─ ocr            OCRService + JobRunner; /api/process*, /api/jobs*, /api/config*,
                  SSE /api/process/{job_id}/events; seeds the quality-loop defaults
```

Every plugin declares a pydantic `Schema` for its config row; the Loader
validates the merged config (YAML row ← patch files ←
`OMNISCRIBE_PLUGIN_<ID>__<FIELD>` env overrides) before `apply`, so a bad
tree fails boot loud with `PluginLoadError`. Services are injected by
Protocol (`ctx.inject(JobQueue)`), never by module singleton.

## Directory Responsibilities

| Path | Single Responsibility |
| --- | --- |
| `src/omniscribe/__init__.py` | Lazy package-level public exports that avoid loading OCR or web dependencies during unrelated submodule imports |
| `src/omniscribe/server.py` | Lazy optional-web dependency loading, FastAPI application setup, CLI argument parsing for `--host/--port/--reload`, and `omniscribe-server` script entry point |
| `src/omniscribe/pipeline.py` | `OCRPipeline` facade — thin orchestration layer that delegates to `HybridEngine` or `GroundedEngine` based on injected components |
| `src/omniscribe/confidence_eval.py` | Package-root confidence evaluator: GLM-OCR fixture loader, greedy IoU matching, and per-document `ConfidenceReport` for the `scripts/confidence_*.py` tooling |
| `src/omniscribe/core/document.py` | Normalized `DocumentResult` IR, pages, blocks, spans, text aggregation, and legacy pages-data adapter |
| `src/omniscribe/core/processors/__init__.py` | Package-level re-exports for backward-compatible import of `DocumentProcessor`, `DocumentProcessorRegistry`, built-in processors, and helper functions |
| `src/omniscribe/core/processors/base.py` | Core `DocumentProcessor` protocol, `DocumentProcessorFactory`, `DocumentProcessorRegistry`, processor name lists, shared regexes, helper functions (`_structure_kind`, `_normalize_space`, `_page_region`, `_bbox_area`), `build_document_processors`, and `run_document_processors` |
| `src/omniscribe/core/processors/reading_order.py` | `ReadingOrderProcessor` — row-major block ordering based on normalized bounding box coordinates |
| `src/omniscribe/core/processors/quality.py` | `QualityAnalysisProcessor` — page-level OCR quality findings (empty pages, sparse text, large empty blocks) |
| `src/omniscribe/core/processors/structure.py` | `StructureAnalysisProcessor` — deterministic block structure hints (headings, list items, key-values, table candidates) |
| `src/omniscribe/core/processors/section.py` | `SectionAnalysisProcessor` — section heading detection and block grouping across page boundaries |
| `src/omniscribe/core/processors/layout.py` | `LayoutEnrichmentProcessor` — page region and layout role labeling (headers, footers, page numbers, figures, captions) |
| `src/omniscribe/core/processors/table.py` | `TableExtractionProcessor` — table grid structure extraction from aligned text blocks |
| `src/omniscribe/core/aligner.py` | Surya detection and DP text-to-box alignment |
| `src/omniscribe/core/recall/whitespace.py` | Whitespace recall booster — pixel-statistics text-line candidates merged into Surya detection on the hybrid path (`OMNISCRIBE_WHITESPACE_RECALL` kill switch, INFO run summary) |
| `src/omniscribe/core/recall/text_layer.py` | Text-layer recall source — lines Surya missed recovered from a digital PDF's embedded text layer; second box source merged after the whitespace booster (`OMNISCRIBE_TEXT_LAYER_RECALL` kill switch, INFO run summary, no-op for scans/images) |
| `src/omniscribe/core/ocr/` | OpenAI/Anthropic/Ollama multi-format VLM client, prompts, response filters, limits, exceptions, retry, and circuit-breaker resilience; `__init__.py` preserves the public import surface |
| `src/omniscribe/core/ocr_quality/` | OCR Quality Trust Layer — watermark detection, script detection, hallucination guard, Platt scaling calibration fit/eval, trust scorer, and orchestrator |
| `src/omniscribe/core/transcription/` | Speech-to-text audio transcription engines (local Whisper & OpenAI-compatible API backends) |
| `src/omniscribe/core/lexicon/` | LanceDB-backed canonical glossary / translation lexicon store (Protocol + LanceDB impl + embedding wrapper + helper queries + one-shot migration core). See `docs/lexicon-migration-spec.md`. |
| `src/omniscribe/core/glossary_sources/` | Terminology import parsers for XLIFF (1.2 / 2.0), TBX, TMX, CSV, TSV, JSON pairs, SQL tables, and Git repositories with encoding auto-detection (BOMs, UTF-8/16/32, Windows-1252, and ISO-8859-1 fallbacks) |
| `src/omniscribe/core/writers/tree_json.py` | Hierarchical block-tree export builder |
| `src/omniscribe/core/writers/exporter_base.py` | Thin `DocumentExportProtocol` + `BaseDocumentExporter` ABC. **Implementations are co-located with the writers they wrap** (DOCX in `core/writers/docx.py`, tree-DOCX in `core/writers/docx_tree.py`, HTML in `core/writers/html.py`) — the module ships only the abstraction, not the exporters. To add a new format, subclass `BaseDocumentExporter` in the same file as the existing writer, then register it on `PDFHandler` (or the relevant writer) |
| `src/omniscribe/core/writers/docx_tree.py` | Hierarchical block-tree to `.docx` converter |
| `src/omniscribe/core/writers/html.py` | Semantic HTML document writer from `DocumentResult` |
| `src/omniscribe/core/block_tree.py` | Hierarchical block-tree data structure and tree nodes |
| `src/omniscribe/core/pdf/__init__.py` | Package re-exports for `PDFHandler`, `DocumentResultWriter`, `IMAGE_EXTENSIONS`, `_emit_pymupdf_agpl_notice`, and public PDF symbols |
| `src/omniscribe/core/pdf/rasterizer.py` | PyMuPDF AGPL warning emission, safe DPI calculation, image extension validation, and PDF/image rasterization to JPEG/PNG base64 |
| `src/omniscribe/core/pdf/embedder.py` | Invisible text layer PDF rendering over rasterized backgrounds, normalized bbox coordinate transformations, and font sizing calculation |
| `src/omniscribe/core/pdf/handler.py` | `PDFHandler` class facade implementing `DocumentResultWriter` protocol for high-level workflow orchestration |
| `src/omniscribe/core/grounded/` | Grounded OCR models, prompted backend, rasterization, and bbox-native response parsers; `__init__.py` preserves the public import surface |
| `src/omniscribe/core/postprocess.py` | Dictionary-based spellcheck post-processing |
| `src/omniscribe/core/imaging/page_preprocess.py` | Local hybrid-path page preprocessing (orientation detection, deskew, denoise, contrast normalization, crop cleanup) |
| `src/omniscribe/core/imaging/handwriting.py` | Local handwriting image preprocessor for specialized handwriting pipeline paths |
| `src/omniscribe/core/ocr_quality/routing.py` | Quality routing recommendation metadata and policy recorder |
| `src/omniscribe/core/evaluation.py` | Lightweight `EvaluationMetrics` dataclass and `evaluate_document` helper for in-process processor result scoring |
| `src/omniscribe/core/writers/docx.py` | Markdown → `.docx` converter used by the docx export route |
| `src/omniscribe/core/translate/config.py` | Core-owned typed settings and optional-feature errors for async translation |
| `src/omniscribe/core/translate/workflow.py` | Optional LangGraph translation workflow |
| `src/omniscribe/core/workflows/base.py` | `EngineBase`, `OutputWriter`, `ProgressCallback`, `WarningCallback` shared by both engines |
| `src/omniscribe/core/workflows/hybrid.py` | `HybridEngine` — orchestrator delegating to specialized workflow stages |
| `src/omniscribe/core/workflows/stages/` | Decomposed hybrid workflow stages: `conversion.py` (`HybridConverter`), `layout.py` (`HybridLayoutDetector`), `ocr.py` (`HybridOcrRunner`), `refine.py` (`HybridRefiner`) |
| `src/omniscribe/core/workflows/grounded.py` | `GroundedEngine` — single bbox-native VLM call → post-process → processors → output |
| `src/omniscribe/core/workflows/repair.py` | `QualityRepairLoop` and `RepairOptions` — engine-agnostic block-level low-confidence re-OCR (stall guard, fail-open, `CircuitOpenError` re-raise) plus the job-level `quality_summary` aggregator |
| `src/omniscribe/core/workflows/utils.py` | Stand-alone workflow helper functions (`parse_page_range`, `_estimate_confidence`, `_decode_page_image`, `_normalize_for_dedup`, `_drop_refined_duplicates`, `_is_refinable`) and workflow constants (`REFINABLE_MIN_WIDTH`, `REFINABLE_MIN_HEIGHT`, `DETECT_CHUNK_SIZE`) |
| `src/omniscribe/core/workflows/__init__.py` | Re-exports `EngineBase`, `HybridEngine`, `GroundedEngine`, public helper `parse_page_range`, constants, and callback type aliases |
| `src/omniscribe/resources/dictionaries/` | Packaged compiled spellcheck dictionaries loaded before legacy repository-root dictionaries |
| `src/omniscribe/resources/calibration/` | Pre-trained model confidence calibration files (e.g. `qwen2_5_vl_72b.json`) |
| `src/omniscribe/core/ocr/multi_format_client.py` | Multi-format LLM completion dispatcher (`openai_compatible`, `anthropic_compatible`, `ollama_compatible`), vision base64 payloads, exponential backoff resilience retries, and timeout boundaries |
| `src/omniscribe/harness/` | Cordis-style plugin harness: `context.py` (Protocol-keyed services, LIFO effects, event bus, router queue, duplicate protection, and non-shadowing rollback), `loader.py` (YAML tree + patches + env overrides, fails loud), `plugin.py` (Plugin base), plus `errors.py` (hierarchical domain exceptions: `DuplicateServiceError`, `DuplicatePluginError`, `ContextDisposedError`, `ServiceNotFoundError`, `PluginLoadError`), `events.py`, `effects.py`, `service.py`, `config.py` |
| `src/omniscribe/plugins/` | The thirteen boot plugins (runtime, logging, state_backend, artifacts, jobs, progress, providers, health, documents, translate, transcribe, glossary, ocr) that register services and mount every `/api` router; see the Plugin Tree section |
| `src/omniscribe/plugins/state_backend_types.py` | Isolated state backend domain dataclasses (ArtifactBlob, ChannelRecord, JobRecord, ArtifactRecord) and StateBackend protocol |
| `src/omniscribe/plugins/documents/` | Documents plugin: `schemas.py` (extraction/export request models reproducing the pre-harness contract), `prompts.py` (extraction prompts re-homed verbatim from the pre-harness `api/services/ai.py`; `PROMPT_VERSION 2026-08-15.v1`; invoice/resume/academic/table/table_extraction/custom templates), `service.py` (LLM extraction runner, text/markdown/json/docling-compatible/mineru-compatible export builders, and on-demand block-tree building from the stored text artifact — no tree sidecars), `routes.py` (`POST /api/extract`, `POST /api/export/document`, `GET|POST /api/export/docx`, `POST /api/export/html`, `POST /api/export/docx-tree`, `POST /api/export/blocktree`, token-bound `GET /api/export/{artifact_id}`, `GET /api/text/{artifact_id}`, `GET /api/metadata/{artifact_id}`), and `plugin.py` (mounts the router; no configurable fields) |
| `src/omniscribe/plugins/translate/` | Translate plugin: `schemas.py` (translation request models + client response contracts), `service.py` (`TranslationService` — sync single-shot `translate_text` re-home, JobQueue runner (`TranslationJobRunner` seam) that walks the stored text artifact's tree with `translate_tree`, and client status mapping PENDING/PROGRESS/SUCCESS/FAILURE), `routes.py` (`POST /api/translate`, `POST /api/translate/async`, `GET /api/translate/status/{job_id}`, `GET /api/translate/result/{job_id}` token-redeeming fetch, `POST /api/translate/nllb`), and `plugin.py` (mounts the router; no configurable fields) |
| `src/omniscribe/plugins/transcribe/` | Transcribe plugin: `schemas.py` (form-field and config request models + client response contracts), `service.py` (`TranscriptionService` — sync multipart transcription through the core transcription engines; transcript and metadata serialized JSON using the text-artifact convention and stored as token-bound artifacts), `config_store.py` (always-writable in-memory transcription config store with masked keys; SSRF-guarded endpoint model discovery falling back to the canned whisper list), `routes.py` (`POST /api/transcribe`, `GET|POST /api/config/transcription`, `GET /api/models/transcription`), and `plugin.py` (mounts the router; no configurable fields) |
| `src/omniscribe/plugins/glossary/` | Glossary plugin: `schemas.py` (import/library request models covering dual-shape imports and toggle bodies), `service.py` (`GlossaryImportService` — parse → entry-count estimate → sync import or JobQueue dispatch above the 5,000-entry estimate, plus `ensure_store_ready`, library/sources CRUD, toggle flipping, reorder, and query-filtered/paginated entries), `store.py` (lazy `LexiconStore` provider — routes 503 with the `uv sync --extra lexicon` install hint when the `lexicon` extra is missing), `http_fetch.py` (SSRF-guarded, IP-pinned URL fetch with manual redirect following for `/api/glossary/import/url`), `routes.py` (the /api/glossary* routes; dual-shape imports, sources CRUD, toggle, entries search/pagination, preview/merged), and `plugin.py` (mounts the router; registers `GlossaryJobRunner`) |
| `src/omniscribe/middleware/auth.py` | ASGI 3.0 bearer authentication middleware enforcing `OMNISCRIBE_AUTH_TOKEN` constant-time verification |
| `src/omniscribe/middleware/rate_limit.py` | ASGI 3.0 sliding-window rate-limiting middleware enforcing request limits per client IP with Retry-After responses |
| `src/omniscribe/middleware/upload_limit.py` | ASGI 3.0 request body size limiting middleware enforcing payload limits via Content-Length inspection and streaming accumulation |
| `src/omniscribe/resources/cordis.yml` | Shipped plugin boot tree; patched via `OMNISCRIBE_CORDIS_PATCH` or `<artifact_dir>/cordis.patch.yml` |
| `src/omniscribe/utils/structured_logging.py` | Structured JSON logging formatter and handlers |
| `src/omniscribe/utils/prompt_safety.py` | Prompt injection detection and input sanitization |
| `src/omniscribe/utils/image.py` | Image crop, blank-region detection, and crop encoding helpers |
| `src/omniscribe/utils/security.py` | SSRF target validation |
| `src/omniscribe/utils/tqdm_patch.py` | Surya progress-bar suppression |
| `src/omniscribe/utils/json_parse.py` | Robust extraction of first parseable JSON object or array from LLM/VLM text outputs using single-pass raw_decode |
| `src/omniscribe/static/` | Static asset directory served by FastAPI |
| `scripts/` | Repo-root developer utilities: confidence eval, fixture builder, debug/inspection scripts, bbox visualizers |
| `examples/` | Sample PDFs and images used by `tests/` and the confidence scripts |
| `tests/` | Unit, integration, security, and slow-path validation |
| `tests/middleware/test_rate_limit.py` | Unit tests for `RateLimitMiddleware` (limits, window expiration, IP isolation, exemptions) |
| `tests/middleware/test_upload_limit.py` | Unit tests for `UploadSizeLimitMiddleware` (Content-Length limits, streaming chunk accumulation, exemptions, 413 responses) |
| `tests/utils/test_json_parse.py` | Unit tests for `extract_json` utility |
| `tests/core/llm/test_client.py` | Direct unit tests for `core/llm/client.py` (provider config resolution, prompt and image extraction, and VLM/LLM invocation) |
| `tests/core/imaging/test_page_preprocess.py` | Unit tests for `PagePreprocessingOptions`, `PagePreprocessingResult`, and `CompositePagePreprocessor` (orientation, deskew, contrast, crop cleanup) |
| `tests/core/ocr_quality/test_routing.py` | Unit tests for `QualityRoutingPolicy.apply` covering `empty_page`, `sparse_text`, and `empty_large_block` findings and decisions |
| `tests/core/pdf/test_embedder.py` | Unit tests for searchable PDF embedding (deflation, garbage collection, page indexing, bounds) |
| `tests/test_config.py` | Unit tests for runtime settings, model inheritance, rate limiting, and CORS normalization |
| `tests/plugins/test_job_error_sanitization.py` | Unit tests for OCR job error sanitization (`_sanitize_job_error` and `OCRService._status_response`) |
| `tests/core/transcription/test_transcription_engines.py` | Comprehensive unit and mock tests for `WhisperLocalEngine` and `GenericAudioAPIEngine` (device resolution, missing extras, thread-safe lazy loading, word timestamps, error mappings, transient retries, empty audio) |
| `tests/core/grounded/test_prompted_grounded_ocr.py` | Comprehensive unit and mock tests for `PromptedGroundedOCR` (prompt building across architectures, multi-page chunking, coordinate normalization and clamping, reading order, JSON repair loop, cancellation handling) |
| `tests/plugins/test_glossary_http_fetch.py` | Unit and mock tests for glossary HTTP fetching, SSRF blocking, redirect limits, body size guards, and network timeouts |
| `tests/routers/test_glossary_library_routes.py` | FastAPI route tests for glossary library management (sources listing, deletion, toggle, reordering, entry pagination, merged/preview routes, and 503 fallback when LanceDB lexicon is absent) |
| `tests/core/glossary_sources/test_encoding_and_xliff.py` | Unit tests for BOM detection, fallback text encodings (UTF-8/16/32, Windows-1252, ISO-8859-1), and robust XLIFF 1.2 / 2.0 parsing |
| `tests/scripts/test_arrow_substrait_present.py` | Integration test validating `arrow_substrait.dll` presence on Windows when the LanceDB lexicon extra is installed (migrated from `tests/ops/`) |
| `client/lib/data/providers/features_state.dart` | Immutable state models (`TranslationState`, `TranscriptionState`, `GlossaryState`, `ExtractionState`) with copyWith, equality, and clearError support |
| `client/lib/data/providers/features_notifier.dart` | Riverpod 2.x `Notifier` controllers (`translationProvider`, `transcriptionProvider`, `glossaryProvider`, `extractionProvider`) for feature operations |
| `client/lib/data/models/smart_preset.dart` | Immutable `SmartPreset` models, presets catalog (Standard, Receipt, Handwriting, Historical, Fast, Deep), filename heuristics, and ProcessSettings bidirectional mapping |
| `client/lib/presentation/workstation/controls/smart_preset_selector.dart` | Visual 1-click smart preset selector cards, active preset highlight, and filename auto-detect suggestion banner |
| `client/lib/presentation/workstation/controls/page_strip.dart` | Interactive multi-page thumbnail rail with vertical/horizontal orientation, auto-scrolling, and bounded flex layout |
| `client/lib/presentation/workstation/canvas/document_viewport.dart` | Full-height GPU interactive document canvas with zoom, pan, spatial grid, bounding box overlays, and floating viewport controls |
| `client/lib/presentation/workstation/workstation_screen.dart` | Primary OCR workstation screen orchestrating unified top header bar, left vertical page strip rail, viewport canvas, BBox inspector, right controls dock, and progress dock |
| `client/lib/presentation/providers/ai_setup_wizard_modal.dart` | 3-step beginner-friendly guided AI setup wizard for local (Ollama/LM Studio) and cloud (OpenAI/Gemini/Claude/Groq) engine configuration |
| `src/omniscribe/plugins/ocr/services/` | Modular OCR service sub-components (`error_sanitization.py`, `content_sniff.py`, `config_seeding.py`) extracted from the former monolithic `service.py` to isolate concerns |
| `scripts/build_windows.py` | PyInstaller build orchestrator generating standalone Windows server bundle with icon and spec integration |
| `scripts/run_server.py` | Argument-parsing executable entrypoint wrapper for the server binary and source execution |
| `tests/fixtures/pdfs/` | Canonical on-disk PDF test fixtures (`digital.pdf`, `hybrid.pdf`, `handwritten.pdf`, `dense.pdf`, `notes.pdf`) segregated from user-facing examples |
| `tests/utils/test_json_parse_props.py` | Hypothesis property-based fuzzing tests for `extract_json` |
| `tests/utils/test_prompt_safety_props.py` | Hypothesis property-based fuzzing tests for prompt sanitization |
| `tests/core/pdf/test_page_range_props.py` | Hypothesis property-based tests for PDF page range parsing and `serialize_page_range` |
| `tests/core/recall/test_whitespace_props.py` | Hypothesis property-based tests for `WhitespaceRecallBooster` invariants |
| `tests/core/ocr/test_filters_props.py` | Hypothesis property-based tests for OCR output filters and repetition deduplication |
| `tests/core/translate/test_workflow.py` | Direct unit and property tests for LangGraph translation workflow, chunker, and node transitions |
| `tests/fixtures/test_pdf_fixtures.py` | Regression tests asserting integrity and accessibility of canonical PDF fixtures |
| `docs/rfcs/2026-09-end-user-install.md` | RFC 001 evaluating end-user distribution architectures (Option A PyInstaller bundle, Option B Flutter desktop embed, Option C standalone CLI) |
| `docs/deployment/windows-bundle.md` | Operator and user guide for running the standalone Windows PyInstaller bundle |
| `docs/TROUBLESHOOTING.md` | Central first-run troubleshooting guide covering top 10 failure modes, cross-linked from `make doctor` and README |

## Extension Points

`OCRPipeline` accepts injected `aligner`, `ocr_processor`, `pdf_handler`,
`output_writer`, `grounded_backend`, and `document_processors` components. Keep
PDF and image inputs on the same output-writer path, and keep normalized bboxes
in `[x0, y0, x1, y1]` form until embedding.

Document processors receive a mutable `DocumentResult` after OCR cleanup,
spellcheck, and cross-page merge but before PDF embedding. The web/API surface
can select built-in local processors by name through `document_processors`.
The current six built-ins (in registration order) are `reading_order`,
`quality_analysis`, `structure_analysis`, `section_analysis`,
`layout_enrichment`, and `table_extraction`. Selection is off by default; the
list can be passed via `ConfigUpdate.document_processors` or the multipart OCR
`document_processors` field.

## Performance Notes

- Dense-mode and refine crop paths decode a page image once and reuse the PIL
  image across boxes.
- Grounded PDF rasterization converts PyMuPDF pixmaps directly into Pillow
  images before producing the final thumbnail JPEG.

## Shared State and Artifacts

All persistent and process-local state flows through the `StateBackend`
service registered by the `state_backend` plugin — no router touches a
module singleton. Two backends ship: `MemoryStateBackend` (default) and
`SQLiteStateBackend` (`OMNISCRIBE_STATE_BACKEND=sqlite`). The backend
covers three domains: artifacts, jobs, and progress channels.

The `artifacts` plugin layers an `ArtifactStore` on top: every artifact is
an opaque id + bearer token pair; sync `/api/process` returns them as
`X-Text-Artifact-Id` / `X-Text-Artifact-Token` headers, and async jobs
expose `text_artifact_id` in `JobStatusResponse` (with the secret token
delivered out-of-band via the `job_completed` SSE event). The `documents` plugin
serves the metadata/export artifact surfaces on the same store:
`POST /api/export/document` writes a new token-bound export artifact, and
`GET /api/text/{id}` / `GET /api/metadata/{id}` / `GET /api/export/{id}`
fetch stored ones. The `translate` plugin writes translated text to the
same token-bound store; its async status summary carries the artifact ids
and never the bearer tokens.

### Background OCR lifecycle

`POST /api/process/async` validates and persists the upload before submitting
a payload to the single-worker `JobQueue` (`plugins/jobs.py`). The plugin
starts the worker at apply time and stops it during dispose. Observable HTTP
states are `pending`, `processing`, `complete`, `error`, and `cancelled`; status is
available at `GET /api/process/status/{job_id}` and as an SSE replay at
`GET /api/process/{job_id}/events`. `POST /api/jobs/{job_id}/cancel` removes a
pending job or marks an in-flight job as `cancelled` without
letting the runner's eventual return overwrite the cancellation. With the
memory backend queue and artifact indexes are lost on restart;
`OMNISCRIBE_STATE_BACKEND=sqlite` persists them.

### Multi-producer job runner dispatch

The single-worker `JobQueue` supports multiple producer plugins (OCR,
translate, glossary) through runtime-checkable `JobPayload` protocol
conformance. Payloads tag their class with `runner_protocol = <RunnerProtocol>`.
At claim time, `InMemoryJobQueue._resolve_runner` inspects the payload: if it
conforms to `JobPayload` (or defines `runner_protocol`), its declared runner
protocol key is injected from the application `Context`; otherwise, it defaults
to injecting `JobRunner`.

### Authentication and runtime security

The historical ASGI security boundary (bearer auth via
`OMNISCRIBE_AUTH_TOKEN`, per-IP rate limiting, `Content-Length` upload
guard) was part of the removed `api/middleware/` package and is deferred in
the harness rebuild — the current route surface is unauthenticated and
intended for local trusted use only. Upload size is still enforced per
request by the `ocr` plugin (`max_upload_mb` plugin config, falling back to
`OMNISCRIBE_MAX_UPLOAD_MB`). Artifact reads remain token-bound.

## Web API Surface (non-exhaustive)

Rebuilt surface (pinned by `tests/openapi.json`):

| Method | Path | Plugin | Notes |
| --- | --- | --- | --- |
| `GET` / `POST` | `/api/config` | `ocr` | Read or update the shared runtime config store |
| `GET` / `PUT` | `/api/config/ocr` | `ocr` | OCR alias of the same store |
| `GET` | `/api/providers`, `/api/providers/{provider_id}`, `/api/providers/{provider_id}/models` | `providers` | Provider catalog and model discovery |
| `GET` | `/api/health`, `/api/healthz` | `health` | Liveness probes |
| `GET` | `/ready`, `/readyz` | `health` | Readiness probes (503 until the harness is ready) |
| `POST` | `/api/process` | `ocr` | Synchronous multipart OCR; PDF blob + artifact headers |
| `POST` | `/api/process/async` | `ocr` | Queue background OCR, returns `202` + job id |
| `GET` | `/api/process/status/{job_id}` | `ocr` | Background OCR lifecycle status |
| `GET` | `/api/process/{job_id}/events` | `ocr` | SSE replay of the job's lifecycle events |
| `GET` / `DELETE` | `/api/jobs` | `ocr` | Job list; `DELETE` clears all jobs |
| `GET` | `/api/jobs/{job_id}/result` | `ocr` | Token-bound result PDF download |
| `POST` | `/api/jobs/{job_id}/cancel` | `ocr` | Cancel pending/running job; terminal jobs are idempotent |
| `POST` | `/api/extract` | `documents` | Structured data extraction against OCR text; templates `invoice`, `resume`, `academic`, `table`, `table_extraction`, or `custom` prompt |
| `POST` | `/api/export/document` | `documents` | Build a token-bound export artifact (`text`/`markdown`/`json`/`docling`/`mineru`); returns `{artifact_id, token, format}` |
| `GET` / `POST` | `/api/export/docx` | `documents` | `.docx` from Markdown page text; the GET form takes `?text=` (Flutter ExportModal) |
| `POST` | `/api/export/html` | `documents` | Semantic HTML built from the stored text artifact's block tree |
| `POST` | `/api/export/docx-tree` | `documents` | `.docx` built from the stored text artifact's block tree |
| `POST` | `/api/export/blocktree` | `documents` | Hierarchical block-tree JSON built from the stored text artifact |
| `GET` | `/api/export/{artifact_id}` | `documents` | Token-bound (Bearer) export artifact download |
| `GET` | `/api/text/{artifact_id}` | `documents` | Token-bound (Bearer) OCR text artifact fetch |
| `GET` | `/api/metadata/{artifact_id}` | `documents` | Token-bound (Bearer) document metadata artifact fetch |
| `POST` | `/api/translate` | `translate` | Synchronous single-shot translation; returns `{translated_text}` |
| `POST` | `/api/translate/async` | `translate` | Tree-aware translation dispatched on the harness JobQueue; translated text stored as a token-bound artifact |
| `GET` | `/api/translate/status/{job_id}` | `translate` | Client status vocabulary (`PENDING`/`PROGRESS`/`SUCCESS`/`FAILURE`); the result summary references artifact ids, never tokens |
| `POST` | `/api/translate/nllb` | `translate` | Local NLLB translation (lazy module-level engine); 503 when the `nllb` extra is missing |
| `GET` | `/api/translate/result/{job_id}` | `translate` | Token-redeeming async result fetch (`?token=…`); wrong token → uniform 404 |
| `POST` | `/api/transcribe` | `transcribe` | Synchronous multipart transcription; token-bound text + metadata artifacts |
| `GET` / `POST` | `/api/config/transcription` | `transcribe` | Transcription config store; masked keys, always writable |
| `GET` | `/api/models/transcription` | `transcribe` | Endpoint model discovery; SSRF guard + whisper fallback list |
| `POST` | `/api/glossary/import` | `glossary` | Dual-shape import: legacy JSON source envelope or the client's multipart upload; above the 5,000-entry estimate dispatches on the JobQueue |
| `POST` | `/api/glossary/import/url` | `glossary` | Dual-shape URL import: query params or JSON body; SSRF-guarded fetch |
| `GET` | `/api/glossary/library` | `glossary` | List imported glossaries |
| `POST` | `/api/glossary/library/{id}/enable` | `glossary` | Enable/disable a glossary |
| `POST` | `/api/glossary/library/reorder` | `glossary` | Reorder the glossary library |
| `DELETE` | `/api/glossary/library/{id}` | `glossary` | Delete a glossary |
| `GET` | `/api/glossary/library/preview` | `glossary` | Preview of enabled entries |
| `GET` | `/api/glossary/library/{id}/entries` | `glossary` | Entries of one glossary |
| `GET` | `/api/glossary/library/merged` | `glossary` | Merged enabled entries; 503 with an install hint when the `lexicon` extra is missing |
| `POST` | `/api/progress/session` | `progress` | Issue an opaque progress channel + one-shot session token |
| `POST` | `/api/progress/cancel/{channel_id}` | `progress` | Request cancellation for a progress channel; token verification via `?session_token=` or `X-Session-Token` (403 on mismatch) |
| `WS` | `/ws/{channel_id}`, `/api/progress/ws/{channel_id}` | `progress` | Token-bound progress stream with Origin validation; auth via first `{"type":"auth",...}` frame (or `?token=`), then accepts `{"type":"cancel"}` |

Deferred in the harness rebuild (routes not mounted): the remaining
`/api/models*` discovery aliases (the transcribe plugin ships
`GET /api/models/transcription`) and
provider mutation routes (`POST/DELETE /api/providers*`) — see the design
spec's out-of-scope list.

## Change Blueprint

> Ledger entries are dated history: file paths reference the tree as it
> was at the entry's date. `src/omniscribe/api/**` paths (and
> `api/celery_app.py`) predate the 2026-08 harness rebuild — that code
> now lives under `src/omniscribe/plugins/` and `src/omniscribe/core/`.

### 2026-09-06: Workstation UI/UX Consolidation & Left Page Strip Rail (Phase 2 Domain 1)

Consolidated redundant document information and controls across the workstation interface:
- **Unified Header Bar**: Centralized document metadata, multi-page chevron navigation, and layer toggles (`Boxes`, `Heatmap`) into a single 52px top bar in `WorkstationScreen`. Added responsive horizontal scrolling for action controls on narrow viewports (< 768px).
- **Document Viewport Expansion**: Removed duplicate `_buildTopRibbon` header row from `DocumentViewport`, allowing the GPU-accelerated interactive canvas to occupy 100% of the viewport container height while retaining floating zoom/fit controls in the bottom-right corner.
- **Left Vertical Page Strip Rail**: Refactored `PageStrip` into a `ConsumerStatefulWidget` supporting `Axis.vertical` with bounded width (`116px`) and explicit item heights (`116px`), eliminating unbounded flex layout exceptions. Added automatic thumbnail scrolling via `ScrollController` on `selectedPageIndex` change. Positioned `PageStrip` as a left vertical rail in wide desktop layouts (`maxWidth >= 768px`).
- **Comprehensive Test Coverage**: Added dedicated widget tests in `workstation_screen_test.dart` for header navigation, layer toggling, vertical orientation mounting, and narrow viewport responsiveness.

### 2026-08-30: Codebase Hardening, SSRF Protection, WebSocket Stability & Multi-Domain Resilience

Addressed edge-case errors, security vulnerabilities, memory bounds, and testing gaps across five domains:
- **Security & SSRF Guarding**: Added `check_ssrf_target_sync` utility in `src/omniscribe/utils/security.py`. Enforced SSRF validation on `request.api_base` in `plugins/ocr/pipeline_bridge.py` (`build_pipeline`) and on `api_base` in `plugins/ocr/service.py` (`update_config`). Bounded and pruned `_event_buffers`, `_event_notify`, and `_done_jobs` tracking sets to eliminate unbounded memory growth.
- **WebSocket Keep-Alive & Ping/Pong**: Added server-side `{"type": "pong"}` response in `plugins/progress.py` and updated Flutter `ws_client.dart` to reset `_pongWatchdog` on any inbound message or pong frame, eliminating false keep-alive timeouts and reconnect storms.
- **Core Pipeline Resilience & Offloading**: Converted `CircuitBreaker._lock` in `core/ocr/resilience.py` from `asyncio.Lock` to `threading.Lock` for multi-loop / cross-thread safety. Offloaded Hugging Face model loading in `local_engine.py` (Whisper), `trocr.py` (TrOCR), and `nllb.py` (NLLB) to `asyncio.to_thread`. Added deterministic client cleanup (`await client.close()`) in `processor.py` and `prompted.py`. Cleaned unicode docstring/comment errors (RUF002/RUF003) and sorted exports in `hybrid_repair.py`.
- **Frontend Trust & Error States**: Removed fabricated mock invoices, fake speech transcripts, and placeholder terms from `client/lib/data/providers/features_notifier.dart` error catch blocks so the UI faithfully surfaces failure states.
- **DevOps, Tooling & Test Hygiene**: Fixed `Dockerfile` rootless execution by moving `tini` package installation before `USER app`, configured `ENV HF_HOME=/app/data/hf`, and removed crashing `f.Close` in `start_app.vbs` log rotation. Added `pytest.importorskip("pyarrow")` and `importorskip("lancedb")` across lexicon test fixtures, and added direct unit test suite `tests/core/llm/test_client.py`.

### 2026-08-20: Robust Multi-Format Model Discovery & 422 Request Resilience

Enhanced model discovery across `src/omniscribe/api/services/provider_manager.py`,
`src/omniscribe/api/routers/config.py`, and `src/omniscribe/api/routers/transcription.py`.
Introduced `extract_model_ids_from_response` supporting OpenAI standard, Ollama native
(`/api/tags`), Anthropic, OpenRouter, Together, top-level arrays, and custom formats.
Added candidate URL fallbacks (`/v1/models`, `/models`, `/api/tags`) for robust
compatibility with local servers (LM Studio, Ollama, vLLM, LocalAI) and remote endpoints.
Resolved HTTP 422 validation errors by:
- Allowing empty `api_key` in `ConfigUpdate` and defaulting empty `api_key` to `"lm-studio"` in `ProcessSettings` for local model backends.
- Accepting `document_processors` in `OcrConfigUpdate` (`POST /api/config/ocr`).
- Expanding `TranscriptionEngineType` to support `"faster-whisper"` and `"faster_whisper"`.
- Accepting nested namespace update objects in `ConfigUpdate` (`POST /api/config`).
- Aligning legacy web UI namespace update calls with dedicated `/api/providers/*` routes.
Added bidirectional `.env` preset synchronization:
- Implemented `update_dotenv` in `src/omniscribe/utils/env.py` to atomically update or insert `.env` variables while preserving comments and structure.
- Connected `ProviderManager.set_active_provider` and `_persist_config` to automatically sync `LLM_API_BASE`, `LLM_MODEL`, `LLM_API_KEY`, and OCR/translation settings to `.env`, `os.environ`, and `_config`.

### 2026-08-13: Quality repair loop (automatic low-confidence block retry)

`core/workflows/repair.py` adds an engine-agnostic `QualityRepairLoop`:
blocks whose estimated confidence is below `RepairOptions.target` are
re-OCR'd crop-scoped (hybrid reuses refine's crop primitive; grounded goes
through the backend's `ocr_crop`) up to `max_retries` times, accepting a
retry only while confidence strictly improves. Unexpected errors fail open
with the original text; `CircuitOpenError` is re-raised so the circuit
breaker stays authoritative. Both engines run repair sequentially after
block emission and before post-processing/embedding, so every downstream
stage sees the repaired text. `OCRPipeline.run` accepts `repair_options=`
(engines default off); `/api/process` defaults on with form fields
`quality_loop_enabled` / `quality_target` / `quality_max_retries` and env
seeds `OMNISCRIBE_QUALITY_LOOP` / `_TARGET` / `_MAX_RETRIES`. New
WebSocket frames: `block_retry`, `block_revised`, `quality_summary`;
progress accounting reuses the `refine` stage band.

### 2026-08-02: Canonical `/api` aliases and background OCR reliability

The web UI uses `/api/...` as its canonical HTTP contract. Legacy
prefix-less OCR and artifact paths remain registered against the same handler
objects so existing integrations continue to work without maintaining duplicate
implementations. The obsolete `api/routers/ai.py` module is removed; translation
and extraction routers use the single-purpose `api/services/ai.py` service.
Added the single-worker OCR queue to `LocalStateBackend`, wired
its start/stop lifecycle to FastAPI lifespan, exposed async submit/status/cancel
routes, and preserved cancellation as a terminal state when a runner winds down.
The WebSocket contract is `/ws/{channel_id}`: the session token is
presented in the first inbound frame (`{"type":"auth","session_token":...}`),
never in the URL. Progress sessions are
issued by `POST /api/progress/session`.

| Area | Canonical route | Compatibility route |
| --- | --- | --- |
| Synchronous OCR | `POST /api/process` | `POST /process` |
| Background OCR | `POST /api/process/async` | `POST /process/async` |
| OCR status | `GET /api/process/status/{job_id}` | `GET /process/status/{job_id}` |
| Text artifact | `GET /api/text/{artifact_id}` | `GET /text/{artifact_id}` |
| Metadata artifact | `GET /api/metadata/{artifact_id}` | `GET /metadata/{artifact_id}` |
| Export artifact | `GET /api/export/{artifact_id}` | `GET /export/{artifact_id}` |

### 2026-07-25: Core PDF Decomposition into `src/omniscribe/core/pdf/` Package

Refactored `src/omniscribe/core/pdf.py` (~18 KB) into a clean, single-responsibility subpackage `src/omniscribe/core/pdf/`. Separated PyMuPDF/image rasterization, safe DPI calculations, and image extension handling into `rasterizer.py`, invisible text layer rendering, font sizing, and coordinate transformation into `embedder.py`, and high-level workflow orchestration into `handler.py`. Preserved 100% backward compatibility via `__init__.py` re-exports for `PDFHandler`, `DocumentResultWriter`, `IMAGE_EXTENSIONS`, `_emit_pymupdf_agpl_notice`, and all public/internal symbols.

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/pdf/rasterizer.py` | PyMuPDF AGPL warning emission, safe DPI calculation, image extension validation, and PDF/image rasterization to JPEG/PNG base64 |
| `src/omniscribe/core/pdf/embedder.py` | Invisible text layer PDF rendering over rasterized backgrounds, normalized bbox coordinate transformations, and font sizing calculation |
| `src/omniscribe/core/pdf/handler.py` | `PDFHandler` class facade implementing `DocumentResultWriter` protocol for high-level workflow orchestration |
| `src/omniscribe/core/pdf/__init__.py` | Re-exports `PDFHandler`, `DocumentResultWriter`, `IMAGE_EXTENSIONS`, `_emit_pymupdf_agpl_notice`, and public PDF symbols |

### 2026-07-25: Refactor stand-alone workflow helpers into `core/workflows/utils.py`

Extracted stand-alone helper functions (`parse_page_range`, `_estimate_confidence`, `_decode_page_image`, `_normalize_for_dedup`, `_drop_refined_duplicates`, `_is_refinable`) and constants (`REFINABLE_MIN_WIDTH`, `REFINABLE_MIN_HEIGHT`, `DETECT_CHUNK_SIZE`) out of `hybrid.py` into `omniscribe.core.workflows.utils`. Re-exported public helpers in `omniscribe.core.workflows.__init__.py` and maintained backward compatibility in `hybrid.py`.

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/workflows/utils.py` | Stand-alone workflow helper functions and constants |
| `src/omniscribe/core/workflows/hybrid.py` | Imports and uses `omniscribe.core.workflows.utils` while re-exporting helpers |
| `src/omniscribe/core/workflows/__init__.py` | Re-exports public workflow helpers (`parse_page_range`, constants) |

### 2026-07-25: LiteLLM Cleanup and Handwriting Preprocessing

Streamlined provider selection by replacing `litellm_provider.py` with direct OpenAI-compatible client integration in `llm/client.py` and `ocr/processor.py`. Added dedicated `handwriting_preprocessor.py` module.

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/imaging/handwriting.py` | Local handwriting image preprocessor |
| `src/omniscribe/core/llm/client.py` | Direct OpenAI-compatible VLM client integration and resilience handlers |

### 2026-07-13: God-module decomposition

A four-phase decomposition targeted the two largest god-modules in the
codebase (`core/ocr.py` and `core/grounded.py`) and the
~1000-line `api/routers/ocr.py` that was accumulating responsibilities.

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/ocr/__init__.py` | Re-exports the public OCR surface (`OCRProcessor`, helpers, prompts) for backwards compatibility |
| `src/omniscribe/core/ocr/processor.py` | LiteLLM-backed `OCRProcessor.run` and per-page retry/filter orchestration |
| `src/omniscribe/core/ocr/prompts.py` | System + user prompt templates, OCR-specific limits, response filters |
| `src/omniscribe/core/grounded/__init__.py` | Re-exports the grounded OCR backend, models, parsers, and hosted adapters |
| `src/omniscribe/core/grounded/models.py` | Grounded block/response models and backend protocol |
| `src/omniscribe/core/grounded/prompted.py` | Prompted and hosted grounded OCR backends |
| `src/omniscribe/core/grounded/parsers.py` | Bbox-native JSON response parsers and axis-order normalization |
| `src/omniscribe/core/grounded/rasterize.py` | Grounded PDF/image rasterization helpers |
| `src/omniscribe/api/services/ocr/settings.py` | Form-parameter resolution for `POST /api/process` |
| `src/omniscribe/api/services/ocr/pipeline_factory.py` | Pipeline construction and backend-model verification for `POST /api/process` |
| `src/omniscribe/api/services/ocr/response.py` | Response assembly, validation-error envelopes, and `FileResponse` construction with token-bound headers |
| `src/omniscribe/api/routers/ocr.py` | Shrunk to a thin orchestrator that just chains the services above |
| `tests/api/routers/test_ocr_thread_bridge.py` | Patches updated to point at `api.services.ocr.pipeline_factory.*` instead of `api.routers.ocr.*` (formerly the monolithic API-safety suite) |
| `ARCHITECTURE.md` | Directory table updated to reflect the four new service modules and the corrected `ai.py` role |

Why a service module per concern (vs. expanding the router): each new
service has a single responsibility (resolve → assemble → respond),
maps to a single source-of-truth, and is independently testable. The
router stays declarative — the route body only orchestrates calls into
the three services.

### 2026-06-14: Engine split — `core/workflows/` package

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/workflows/base.py` | New `EngineBase` plus `OutputWriter`, `ProgressCallback`, `WarningCallback`, and `_notify` helpers shared by both engines |
| `src/omniscribe/core/workflows/hybrid.py` | New `HybridEngine` — extract the existing hybrid orchestration from `pipeline.py` (Surya detect → VLM OCR → DP align → refine → post-process → processors → output) |
| `src/omniscribe/core/workflows/grounded.py` | New `GroundedEngine` — single bbox-native VLM call → post-process → processors → output |
| `src/omniscribe/core/workflows/__init__.py` | Re-export the engines and callback aliases |
| `src/omniscribe/pipeline.py` | Shrink `OCRPipeline` to a facade that picks `HybridEngine` or `GroundedEngine` based on injected components |
| `ARCHITECTURE.md` | Document the new sub-package and the facade pattern in `pipeline.py` |

### 2026-06-14: DOCX export route + `core/writers/docx.py`

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/writers/docx.py` | New `convert_markdown_to_docx(markdown_text: str) -> io.BytesIO` helper |
| `src/omniscribe/api/schemas/requests.py` | New `ExportDocxRequest` typed schema |
| `src/omniscribe/api/routers/extraction.py` | New `POST /api/export/docx` route that streams the generated `.docx` |
| `pyproject.toml` | Already lists `python-docx>=1.1.0` (no change required) |
| `ARCHITECTURE.md` | Document the docx export in the directory table and the Web API surface |

### 2026-06-14: Confidence evaluation scripts and root-level `confidence_eval.py`

| File | Responsibility |
| --- | --- |
| `src/omniscribe/confidence_eval.py` | New package-root module: `GTBlock`, `BlockMatch`, `ConfidenceReport`, `load_ground_truth`, `text_similarity`, `compute_report`, `iou` (auto-detects `[x0,y0,x1,y1]` vs `[y0,x0,y1,x1]` fixture axis order) |
| `scripts/confidence_eval.py` | New developer script — runs hybrid and grounded paths against `examples/*.pdf` and reports per-document block recall, IoU, and text similarity |
| `scripts/confidence_image.py` | New developer script — same comparison on a single image, defaults to `examples/image.avif` |
| `examples/` | New sample inputs (`dense.pdf`, `digital.pdf`, `handwritten.pdf`, `hybrid.pdf`, `image.png`, `image.avif`, `notes.pdf`) |
| `tests/core/test_evaluation.py` | Cover fixture loading, axis-order detection, and `ConfidenceReport` aggregation |
| `ARCHITECTURE.md` | Document the root-level confidence eval vs the lightweight `core/evaluation.py` |

### 2026-06-14: `POST /api/extract` and `ExtractionTemplate` enum

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/schemas/requests.py` | New `ExtractionTemplate` StrEnum (`invoice`, `resume`, `academic`, `custom`) and the `ExtractionRequest` model with `template` and `custom_prompt` fields |
| `src/omniscribe/api/routers/ai.py` | New `extract_structured_data` service with fenced-JSON parsing, retry, and stable error mapping |
| `src/omniscribe/api/routers/extraction.py` | New router that wires the schema, the AI service, and the SSRF guard for `api_base` |
| `tests/api/routers/test_extraction_translation_routers.py` | Cover template dispatch, custom-prompt fallback, and SSRF fail-closed behavior |
| `ARCHITECTURE.md` | Document the new router and the four extraction templates in the Web API surface |

### 2026-06-09: Local document processors exposed to web/API

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/document.py` | Provide the normalized `DocumentResult` handoff used by post-OCR document processors |
| `src/omniscribe/core/processors.py` | Define built-in local processors and map user-facing names to deterministic processor instances |
| `src/omniscribe/api/schemas/requests.py` | Validate `document_processors` for config JSON and multipart OCR requests |
| `src/omniscribe/api/routers/ocr.py` | Instantiate selected processors, pass them into `OCRPipeline`, and expose quality metadata through `X-Document-Quality` when available |
| `src/omniscribe/static/js/state_and_api.js` | Persist and submit web-selected document processors |
| `src/omniscribe/static/index.html` | Expose Reading Order, Quality Analysis, Structure Analysis, and Section Analysis toggles in Advanced Configuration |
| `tests/api/services/test_document_processor_selection.py` | Cover processor selection parsing, validation, and factory mapping |

### 2026-06-09: Stage 2 local structure analysis processor

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/processors.py` | Add `structure_analysis`, a deterministic local processor that classifies blocks as headings, paragraphs, list items, key-values, table candidates, or empty blocks |
| `src/omniscribe/api/routers/ocr.py` | Expose page-level structure summaries through `X-Document-Structure` when structure metadata is present |
| `src/omniscribe/static/index.html` | Add the Structure Analysis opt-in control |
| `tests/core/test_document.py` | Cover block classification without rewriting output text |

### 2026-06-09: Stage 3 local section analysis processor

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/processors.py` | Add `section_analysis`, a deterministic local processor that assigns blocks to detected heading sections across page boundaries |
| `src/omniscribe/api/routers/ocr.py` | Expose page-level section summaries through `X-Document-Sections` when section metadata is present |
| `src/omniscribe/static/index.html` | Add the Section Analysis opt-in control |
| `tests/core/test_document.py` | Cover section grouping while preserving original block text |

### 2026-06-09: Stage 4 document metadata artifact surface

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/services/document_metadata.py` | Build compact JSON-safe metadata reports from `DocumentResult` page/block processor annotations and write them atomically as temporary artifacts |
| `src/omniscribe/api/routers/ocr.py` | Issue `X-Document-Metadata-Artifact-Id` and `X-Document-Metadata-Artifact-Token` only when report content exists, and serve protected `GET /metadata/{artifact_id}` |
| `tests/api/routers/test_artifacts.py` | Cover token-bound metadata artifact access and payload shape without changing text artifact behavior (formerly the monolithic API-safety suite) |

### 2026-06-09: Stage 5-12 Web/API document intelligence

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Deprecate the user-facing `omniscribe` CLI script and drop the CLI-only `rich` dependency; keep `omniscribe-server`. `OCRPipeline` is still importable for in-process programmatic use. |
| `src/omniscribe/core/imaging/page_preprocess.py` | Add opt-in local page preprocessing diagnostics for the hybrid image path |
| `src/omniscribe/core/processors.py` | Add `layout_enrichment` and `table_extraction` deterministic processors |
| `src/omniscribe/api/services/document_exports.py` | Add token-bound JSON, Markdown, text, Docling-compatible, and MinerU-compatible exports |
| `src/omniscribe/core/ocr_quality/routing.py` | Record default-off quality routing recommendations in document metadata |
| `src/omniscribe/api/services/workflow.py` | Expose deterministic Web/API workflow summaries |
| `src/omniscribe/core/evaluation.py` | Add local evaluation metrics for text, bbox, reading-order, and table coverage |

### 2026-06-02: Direct grounded PDF pixmap conversion

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/grounded/rasterize.py` | Convert PDF pixmaps directly into Pillow images before emitting the final grounded OCR thumbnail JPEG |
| `tests/core/grounded/test_grounded.py` | Guard against restoring the redundant intermediate JPEG decode |
| `ARCHITECTURE.md` | Record the existing module layout and the direct pixmap conversion invariant |

### 2026-06-02: Stage 1 API and browser safety hardening

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/schemas/requests.py` | Validate config JSON, OCR multipart settings, translation requests, and extraction requests with explicit enums, booleans, and numeric ranges |
| `src/omniscribe/api/services/uploads.py` | Enforce streaming upload byte limits, content-signature upload type detection, stable API error messages, and server-issued text artifact IDs |
| `src/omniscribe/api/routers/config.py` | Apply typed config validation, SSRF checks, safe environment parsing, and non-leaking model discovery errors |
| `src/omniscribe/api/routers/ocr.py` | Apply typed OCR/AI boundary validation, hardened upload dispatch, opaque text artifact retrieval, SSRF checks, and stable client-facing errors |
| `src/omniscribe/utils/security.py` | Fail closed for malformed, unsupported, or unresolvable URLs and only allow local/private endpoints when `ALLOW_SSRF_LOCAL=true` is explicitly set |
| `src/omniscribe/static/js/app.js` | Use server-issued text artifact IDs and render extraction status/errors/cards without HTML injection |
| `src/omniscribe/static/js/state_and_api.js` | Build model select placeholder with DOM APIs before appending model-controlled option text |
| `src/omniscribe/static/js/workspace_ui.js` | Provide safe DOM helpers for clearing elements and rendering extraction status cards |
| `tests/utils/test_ssrf.py`, `tests/api/services/test_uploads.py`, `tests/api/routers/test_artifacts.py`, `tests/api/routers/test_process_routes.py` | Cover config validation, SSRF fail-closed behavior, streaming upload validation, opaque text artifacts, stable API errors, and static JS sink removal (formerly the monolithic API-safety suite) |
| `tests/api/middleware/test_security_qa.py` | Keep extraction JSON parsing deterministic under fail-closed SSRF validation |

### 2026-06-03: Optional async translation boundary

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/translate/config.py` | Own typed translation settings and the deterministic optional-feature error used by core and API boundaries |
| `src/omniscribe/core/translate/workflow.py` | Keep chunking and evaluation helpers importable without async extras, lazily build the LangGraph workflow, and accept injected translation settings |
| `src/omniscribe/api/routers/config.py` | Adapt the mutable web runtime config into core-owned translation settings without exposing `_config` to core modules |
| `src/omniscribe/api/celery_app.py` | (since deleted) Guard Celery imports and provide an import-safe fallback task facade when async extras are not installed |
| `src/omniscribe/api/tasks.py` | Validate async translation task inputs and pass explicit translation settings into the core workflow |
| `src/omniscribe/api/routers/ocr.py` | Validate async translation route inputs and return deterministic 503 responses when optional async extras are unavailable |
| `pyproject.toml` | Move Celery, Redis, LangGraph, ChromaDB, and sentence-transformers into the `async-translation` extra with `translation` as an alias extra |
| `tests/core/translate/test_translation_boundary.py` | Cover guarded imports without async extras and explicit translation settings injection |

### 2026-06-03: Spellcheck resource package cleanup

| File | Responsibility |
| --- | --- |
| `src/omniscribe/resources/dictionaries/ara.json.gz` | Packaged Arabic compiled spellcheck dictionary for installed distributions |
| `src/omniscribe/resources/dictionaries/eng.json.gz` | Packaged English compiled spellcheck dictionary for installed distributions |
| `src/omniscribe/core/postprocess.py` | Load packaged dictionaries first while retaining legacy repository-root and user-cache fallbacks |
| `pyproject.toml` | Exclude bytecode cache artifacts from Hatch package builds |
| `tests/core/test_dictionary_postprocess.py` | Cover packaged dictionary lookup and legacy repository-root fallback |

### 2026-06-03: Lazy web server imports

| File | Responsibility |
| --- | --- |
| `src/omniscribe/__init__.py` | Preserve package-level OCR exports through lazy lookups so `import omniscribe.server` does not load OCR core dependencies first |
| `src/omniscribe/server.py` | Preserve `omniscribe.server:app` and `omniscribe.server:main` while deferring FastAPI, router, static-file, and uvicorn imports until the web app is created or run |
| `tests/api/test_server_lazy_imports.py` | Verify base-install-safe `omniscribe.server` imports and deterministic missing-web-extra errors without uninstalling FastAPI |
| `ARCHITECTURE.md` | Record the optional-web lazy import boundary for the server module |

### 2026-08-02: Quality Audit & YAGNI Improvements

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/workflows/hybrid.py` | Re-raise `CircuitOpenError` explicitly in crop/box OCR exception handlers to prevent swallowing endpoint failures |
| `src/omniscribe/core/grounded/prompted.py` | Offload grounded PIL crop and PNG buffer generation to thread pool via `asyncio.to_thread` |
| `src/omniscribe/api/routers/ocr.py` | Handle `asyncio.CancelledError` on client disconnect without logging 500 stack traces, and wrap file cleanup calls in `asyncio.to_thread` |
| `src/omniscribe/api/services/uploads.py` | Add parent directory confinement check in `cleanup_files` to ensure deleted paths reside in temporary storage |

### 2026-08-11: Industry-Standards Audit Implementation (P1 & Quick Wins)

| File | Responsibility |
| --- | --- |
| `.github/dependabot.yml` | Dependabot configuration for `pip` and `github-actions` ecosystems with weekly schedule |
| `.github/workflows/test.yml` | Add `pip-audit` vulnerability scan, `pytest-cov` test coverage reporting, and CycloneDX SBOM artifact generation |
| `pyproject.toml` | Add `pytest-cov`, `pip-audit`, and `cyclonedx-python-lib` to `dependency-groups.dev` |
| `.pre-commit-config.yaml` | Sync `ruff-pre-commit` version to `v0.9.0` |
| `AGENTS.md` | Document `surya-ocr` `requests>=2.31` workaround follow-up and `live_llm` manual test run instructions (workaround closed in audit-secondary Phase 5 — see Known Tech Debt) |

### 2026-08-11: Goose-Style Multi-Provider API Handling Architecture

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/services/provider_manager.py` | `ProviderManager` service with 11-provider catalog templates, system environment variable auto-discovery (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_HOST`, etc.), disk persistence to `~/.config/omniscribe/providers.yaml`, active provider switching, and model listing dispatch |
| `src/omniscribe/core/ocr/multi_format_client.py` | Multi-format LLM completion dispatcher supporting `openai_compatible`, `anthropic_compatible`, and `ollama_compatible` formats with exponential backoff retries and timeout boundaries |
| `src/omniscribe/api/routers/providers.py` | Goose-style provider management API routes (`/api/providers`, `/api/providers/templates`, `/api/providers/active`, `/api/providers/{provider_id}/models`) |
| `src/omniscribe/api/schemas/requests.py` | `ProviderFormatEnum`, `ProviderConfig`, `ProviderTemplate`, `ActiveProviderUpdate`, `ProviderCreateRequest` schemas |
| `src/omniscribe/core/llm/client.py` | Directs VLM/LLM completion calls through `ocr/multi_format_client.py` based on active provider configuration |
| `src/omniscribe/api/routers/config.py` | Connects `/api/models` discovery endpoints to `ProviderManager` |
| `tests/api/services/test_provider_manager.py` | Unit tests for provider configuration manager, env-var discovery, and persistence |
| `tests/api/test_multi_format_client.py` | Unit tests for OpenAI, Anthropic, and Ollama multi-format completion execution |
| `tests/api/routers/test_provider_api_routes.py` | Unit tests for provider REST management API routes |


### 2026-08-14: Multi-Domain Architecture, Security & Quality Audit

Conducted a comprehensive 3-domain audit (Core Pipeline, Backend API/Security, and QA/DevOps):
1. **Core Pipeline**: Confirmed normalized `[0..1]` bounding box invariant, monotonic DP alignment, cooperative cancellation via `OCRCancelled` (`BaseException`), bounded 16-entry image LRU cache, and quality repair loop stall guards. Identified `complete_vlm_prompt` export omission in `core/ocr/__init__.py` and `DocumentTree` child index desync on reading order sort.
2. **API & Security**: Identified and cataloged readiness probe fix (`OCRJobQueue.running` property), third-party provider API key response masking, artifact token separation from server bearer authentication, and uniform SSRF validation on tree translation and transcription endpoints.
3. **QA & DevOps**: Executed full test and lint suites (1,230 fast tests passing in 37.9s, 0 Ruff errors, 0 format issues, 144 source files clean in Mypy strict mode). Cataloged missing dev CI dependencies in `pyproject.toml` (`pytest-cov`, `pip-audit`, `cyclonedx-python-lib`, `rich`) and CI integration gaps.

### 2026-08-14: Core Dependencies Update (Redis & ChromaDB)

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Promoted `redis>=5.0.0` and `chromadb>=0.5.0` to core `[project.dependencies]` so Celery distributed backend state and vector lexicon RAG support are packaged out-of-the-box |

### 2026-08-14: Full Dependency Modernization & Security Audit Resolution

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Upgraded `surya-ocr>=0.22.1`, bounded `openai>=2.11.0,<3`, pinned `numpy<2.3.0` for Python 3.11 typing stub compatibility, removed unmaintained `comet` (`unbabel-comet`) extra to unblock modern `transformers 5.x` and `huggingface-hub>=1.5.0`, and locked `redis>=5.0.0` and `chromadb>=0.5.0` |
| `uv.lock` | Updated 220 resolved packages across runtime, upgrading `transformers` (v4.57.6 -> v5.15.0), `protobuf` (v4.25.9 -> v7.35.1), `huggingface-hub` (v0.36.2 -> v1.27.0), `pypdfium2` (v4.30.0 -> v5.13.0), resolving 45 of 46 known `pip-audit` security advisories |
| `src/omniscribe/core/translate/nllb.py` | Adapted HuggingFace pipeline and tokenizer typing for `transformers` 5.x |

### 2026-08-18: Comprehensive 5-Domain Multi-Agent Codebase Audit

| `src/omniscribe/api/routers/extraction.py` | New router that wires the schema, the AI service, and the SSRF guard for `api_base` |
| `tests/api/routers/test_extraction_translation_routers.py` | Cover template dispatch, custom-prompt fallback, and SSRF fail-closed behavior |
| `ARCHITECTURE.md` | Document the new router and the four extraction templates in the Web API surface |

### 2026-06-09: Local document processors exposed to web/API

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/document.py` | Provide the normalized `DocumentResult` handoff used by post-OCR document processors |
| `src/omniscribe/core/processors.py` | Define built-in local processors and map user-facing names to deterministic processor instances |
| `src/omniscribe/api/schemas/requests.py` | Validate `document_processors` for config JSON and multipart OCR requests |
| `src/omniscribe/api/routers/ocr.py` | Instantiate selected processors, pass them into `OCRPipeline`, and expose quality metadata through `X-Document-Quality` when available |
| `src/omniscribe/static/js/state_and_api.js` | Persist and submit web-selected document processors |
| `src/omniscribe/static/index.html` | Expose Reading Order, Quality Analysis, Structure Analysis, and Section Analysis toggles in Advanced Configuration |
| `tests/api/services/test_document_processor_selection.py` | Cover processor selection parsing, validation, and factory mapping |

### 2026-06-09: Stage 2 local structure analysis processor

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/processors.py` | Add `structure_analysis`, a deterministic local processor that classifies blocks as headings, paragraphs, list items, key-values, table candidates, or empty blocks |
| `src/omniscribe/api/routers/ocr.py` | Expose page-level structure summaries through `X-Document-Structure` when structure metadata is present |
| `src/omniscribe/static/index.html` | Add the Structure Analysis opt-in control |
| `tests/core/test_document.py` | Cover block classification without rewriting output text |

### 2026-06-09: Stage 3 local section analysis processor

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/processors.py` | Add `section_analysis`, a deterministic local processor that assigns blocks to detected heading sections across page boundaries |
| `src/omniscribe/api/routers/ocr.py` | Expose page-level section summaries through `X-Document-Sections` when section metadata is present |
| `src/omniscribe/static/index.html` | Add the Section Analysis opt-in control |
| `tests/core/test_document.py` | Cover section grouping while preserving original block text |

### 2026-06-09: Stage 4 document metadata artifact surface

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/services/document_metadata.py` | Build compact JSON-safe metadata reports from `DocumentResult` page/block processor annotations and write them atomically as temporary artifacts |
| `src/omniscribe/api/routers/ocr.py` | Issue `X-Document-Metadata-Artifact-Id` and `X-Document-Metadata-Artifact-Token` only when report content exists, and serve protected `GET /metadata/{artifact_id}` |
| `tests/api/routers/test_artifacts.py` | Cover token-bound metadata artifact access and payload shape without changing text artifact behavior (formerly the monolithic API-safety suite) |

### 2026-06-09: Stage 5-12 Web/API document intelligence

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Deprecate the user-facing `omniscribe` CLI script and drop the CLI-only `rich` dependency; keep `omniscribe-server`. `OCRPipeline` is still importable for in-process programmatic use. |
| `src/omniscribe/core/imaging/page_preprocess.py` | Add opt-in local page preprocessing diagnostics for the hybrid image path |
| `src/omniscribe/core/processors.py` | Add `layout_enrichment` and `table_extraction` deterministic processors |
| `src/omniscribe/api/services/document_exports.py` | Add token-bound JSON, Markdown, text, Docling-compatible, and MinerU-compatible exports |
| `src/omniscribe/core/ocr_quality/routing.py` | Record default-off quality routing recommendations in document metadata |
| `src/omniscribe/api/services/workflow.py` | Expose deterministic Web/API workflow summaries |
| `src/omniscribe/core/evaluation.py` | Add local evaluation metrics for text, bbox, reading-order, and table coverage |

### 2026-06-02: Direct grounded PDF pixmap conversion

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/grounded/rasterize.py` | Convert PDF pixmaps directly into Pillow images before emitting the final grounded OCR thumbnail JPEG |
| `tests/core/grounded/test_grounded.py` | Guard against restoring the redundant intermediate JPEG decode |
| `ARCHITECTURE.md` | Record the existing module layout and the direct pixmap conversion invariant |

### 2026-06-02: Stage 1 API and browser safety hardening

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/schemas/requests.py` | Validate config JSON, OCR multipart settings, translation requests, and extraction requests with explicit enums, booleans, and numeric ranges |
| `src/omniscribe/api/services/uploads.py` | Enforce streaming upload byte limits, content-signature upload type detection, stable API error messages, and server-issued text artifact IDs |
| `src/omniscribe/api/routers/config.py` | Apply typed config validation, SSRF checks, safe environment parsing, and non-leaking model discovery errors |
| `src/omniscribe/api/routers/ocr.py` | Apply typed OCR/AI boundary validation, hardened upload dispatch, opaque text artifact retrieval, SSRF checks, and stable client-facing errors |
| `src/omniscribe/utils/security.py` | Fail closed for malformed, unsupported, or unresolvable URLs and only allow local/private endpoints when `ALLOW_SSRF_LOCAL=true` is explicitly set |
| `src/omniscribe/static/js/app.js` | Use server-issued text artifact IDs and render extraction status/errors/cards without HTML injection |
| `src/omniscribe/static/js/state_and_api.js` | Build model select placeholder with DOM APIs before appending model-controlled option text |
| `src/omniscribe/static/js/workspace_ui.js` | Provide safe DOM helpers for clearing elements and rendering extraction status cards |
| `tests/utils/test_ssrf.py`, `tests/api/services/test_uploads.py`, `tests/api/routers/test_artifacts.py`, `tests/api/routers/test_process_routes.py` | Cover config validation, SSRF fail-closed behavior, streaming upload validation, opaque text artifacts, stable API errors, and static JS sink removal (formerly the monolithic API-safety suite) |
| `tests/api/middleware/test_security_qa.py` | Keep extraction JSON parsing deterministic under fail-closed SSRF validation |

### 2026-08-02: Quality Audit & YAGNI Improvements

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/workflows/hybrid.py` | Re-raise `CircuitOpenError` explicitly in crop/box OCR exception handlers to prevent swallowing endpoint failures |
| `src/omniscribe/core/grounded/prompted.py` | Offload grounded PIL crop and PNG buffer generation to thread pool via `asyncio.to_thread` |
| `src/omniscribe/api/routers/ocr.py` | Handle `asyncio.CancelledError` on client disconnect without logging 500 stack traces, and wrap file cleanup calls in `asyncio.to_thread` |
| `src/omniscribe/api/services/uploads.py` | Add parent directory confinement check in `cleanup_files` to ensure deleted paths reside in temporary storage |

### 2026-08-11: Industry-Standards Audit Implementation (P1 & Quick Wins)

| File | Responsibility |
| --- | --- |
| `.github/dependabot.yml` | Dependabot configuration for `pip` and `github-actions` ecosystems with weekly schedule |
| `.github/workflows/test.yml` | Add `pip-audit` vulnerability scan, `pytest-cov` test coverage reporting, and CycloneDX SBOM artifact generation |
| `pyproject.toml` | Add `pytest-cov`, `pip-audit`, and `cyclonedx-python-lib` to `dependency-groups.dev` |
| `.pre-commit-config.yaml` | Sync `ruff-pre-commit` version to `v0.9.0` |
| `AGENTS.md` | Document `surya-ocr` `requests>=2.31` workaround follow-up and `live_llm` manual test run instructions (workaround closed in audit-secondary Phase 5 — see Known Tech Debt) |

### 2026-08-11: Goose-Style Multi-Provider API Handling Architecture

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/services/provider_manager.py` | `ProviderManager` service with 11-provider catalog templates, system environment variable auto-discovery (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_HOST`, etc.), disk persistence to `~/.config/omniscribe/providers.yaml`, active provider switching, and model listing dispatch |
| `src/omniscribe/core/ocr/multi_format_client.py` | Multi-format LLM completion dispatcher supporting `openai_compatible`, `anthropic_compatible`, and `ollama_compatible` formats with exponential backoff retries and timeout boundaries |
| `src/omniscribe/api/routers/providers.py` | Goose-style provider management API routes (`/api/providers`, `/api/providers/templates`, `/api/providers/active`, `/api/providers/{provider_id}/models`) |
| `src/omniscribe/api/schemas/requests.py` | `ProviderFormatEnum`, `ProviderConfig`, `ProviderTemplate`, `ActiveProviderUpdate`, `ProviderCreateRequest` schemas |
| `src/omniscribe/core/llm/client.py` | Directs VLM/LLM completion calls through `ocr/multi_format_client.py` based on active provider configuration |
| `src/omniscribe/api/routers/config.py` | Connects `/api/models` discovery endpoints to `ProviderManager` |
| `tests/api/services/test_provider_manager.py` | Unit tests for provider configuration manager, env-var discovery, and persistence |
| `tests/api/test_multi_format_client.py` | Unit tests for OpenAI, Anthropic, and Ollama multi-format completion execution |
| `tests/api/routers/test_provider_api_routes.py` | Unit tests for provider REST management API routes |


### 2026-08-14: Multi-Domain Architecture, Security & Quality Audit

Conducted a comprehensive 3-domain audit (Core Pipeline, Backend API/Security, and QA/DevOps):
1. **Core Pipeline**: Confirmed normalized `[0..1]` bounding box invariant, monotonic DP alignment, cooperative cancellation via `OCRCancelled` (`BaseException`), bounded 16-entry image LRU cache, and quality repair loop stall guards. Identified `complete_vlm_prompt` export omission in `core/ocr/__init__.py` and `DocumentTree` child index desync on reading order sort.
2. **API & Security**: Identified and cataloged readiness probe fix (`OCRJobQueue.running` property), third-party provider API key response masking, artifact token separation from server bearer authentication, and uniform SSRF validation on tree translation and transcription endpoints.
3. **QA & DevOps**: Executed full test and lint suites (1,230 fast tests passing in 37.9s, 0 Ruff errors, 0 format issues, 144 source files clean in Mypy strict mode). Cataloged missing dev CI dependencies in `pyproject.toml` (`pytest-cov`, `pip-audit`, `cyclonedx-python-lib`, `rich`) and CI integration gaps.

### 2026-08-14: Core Dependencies Update (Redis & ChromaDB)

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Promoted `redis>=5.0.0` and `chromadb>=0.5.0` to core `[project.dependencies]` so Celery distributed backend state and vector lexicon RAG support are packaged out-of-the-box |

### 2026-08-14: Full Dependency Modernization & Security Audit Resolution

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Upgraded `surya-ocr>=0.22.1`, bounded `openai>=2.11.0,<3`, pinned `numpy<2.3.0` for Python 3.11 typing stub compatibility, removed unmaintained `comet` (`unbabel-comet`) extra to unblock modern `transformers 5.x` and `huggingface-hub>=1.5.0`, and locked `redis>=5.0.0` and `chromadb>=0.5.0` |
| `uv.lock` | Updated 220 resolved packages across runtime, upgrading `transformers` (v4.57.6 -> v5.15.0), `protobuf` (v4.25.9 -> v7.35.1), `huggingface-hub` (v0.36.2 -> v1.27.0), `pypdfium2` (v4.30.0 -> v5.13.0), resolving 45 of 46 known `pip-audit` security advisories |
| `src/omniscribe/core/translate/nllb.py` | Adapted HuggingFace pipeline and tokenizer typing for `transformers` 5.x |

### 2026-08-18: Comprehensive 4-Domain Multi-Agent Codebase Audit

Conducted an exhaustive 4-domain audit (49 findings across Core Pipeline, API & Security, Testing & QA, and DevOps & Configuration; the original 5-domain UI tier was retired alongside the legacy web UI in Phase B):
1. **Core Pipeline (10 findings)**: Identified `run_document_processors` strict aggregate assertion bug rejecting valid `MAY_DELETE` contract processors (`D1-01`); `convert_tree_to_docx` crash on `BlockNode(TABLE)` and duplicate table emissions (`D1-02`); unmanaged background task leak on `CircuitOpenError` in `PromptedGroundedOCR` (`D1-03`); `translate_tree` bypassing `TableNode` instances in page children (`D1-04`); and `_Chunker.add` delimiter overwrite formatting bug (`D1-05`).
2. **API & Security (13 findings)**: Identified management route auth bypass when global token is unset but subsystem tokens exist (`D2-01`); `JobHistory.record()` signature mismatch crashing OCR pipeline completion on SQLite or Redis backends (`D2-02`); plaintext token exposure via URL query parameters (`D2-03`); unbounded memory leak and $O(N)$ event loop blocking in `RateLimitMiddleware` (`D2-04`); missing SSRF check on `sql_dsn` in SQL glossary importer (`D2-05`); and flawed chunked/gzip byte parsing in `_PinnedIPTransport` (`D2-06`).
3. **Testing & QA (14 findings)**: Identified silent `pytest.skip` calls on empty pipeline outputs hiding regressions in recall and integration gates (`D4-01`); untested Redis/SQLite connection outage handling (`D4-02`); absence of mypy typechecking on `tests/` in CI and pre-commit (`D4-11`); missing `--cov-fail-under` coverage floor in CI (`D4-12`); and vacuous assertions in live VLM tests (`D4-05`).
4. **DevOps & Config (12 findings)**: Identified Celery worker inheriting Dockerfile HTTP healthcheck causing container restart loops (`D5-01`); `RUN chown` duplicating `.venv` layer by 1.5–2.0 GB in Docker image (`D5-02`); CLI flag password exposure in `compose.yaml` and `start_app.vbs` (`D5-03`); release workflow README sed regex typo (`D5-04`); and unverified curl execution in `install.sh` (`D5-05`).

### 2026-08-18: Phase 0 Critical Blocker Fixes Implementation

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/services/state/sqlite.py` | Added `text_artifact_id: str | None = None` to `SQLiteJobHistory.record()` to match `JobHistory` protocol and persist artifact linkage |
| `src/omniscribe/api/services/state/redis.py` | Added `text_artifact_id: str | None = None` to `RedisJobHistory.record()` to match `JobHistory` protocol and persist artifact linkage |
| `src/omniscribe/api/middleware/auth.py` | Hardened `BearerAuthMiddleware` to protect management routes (`/api/config`, `/api/providers`, `/api/jobs`) with active subsystem tokens when global token is unset |
| `tests/core/test_pipeline_recall.py` | Replaced `pytest.skip` on empty pipeline results with strict `assert doc_result is not None` and `assert len(captured) > 0` |
| `tests/api/test_integration.py` | Replaced `pytest.skip` on empty boxes with strict `assert len(boxes) > 0` and `assert len(boxes) >= 3` |
| `compose.yaml` | Overrode container healthcheck for Celery `worker` service with native `celery inspect ping` |
| `tests/api/middleware/test_security_middleware.py` | Added regression test `test_management_routes_protected_when_only_subsystem_token_set` (formerly the separate-auth suite) |

### 2026-08-18: Phase 1 High-Priority Reliability & Security Remediations

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/processors/base.py` | Honor `MAY_DELETE` contract in `run_document_processors` strict mode aggregate checks without false positives on deletions |
| `src/omniscribe/core/writers/docx_tree.py` | Safely handle `BlockNode(TABLE)` instances and de-duplicate rendered table instances between pages and document roots |
| `src/omniscribe/api/routers/common.py` | Prioritize `X-Artifact-Token` and `Authorization: Bearer` headers in `get_access_token()` over query params |
| `src/omniscribe/api/middleware/rate_limit.py` | Bound `RateLimitMiddleware` memory footprint with `MAX_TRACKED_IPS = 10_000` ceiling and clean eviction |
| `src/omniscribe/utils/security.py` | Provide synchronous `is_blocked_host()` check for SSRF validation |
| `src/omniscribe/core/glossary_sources/sql_table.py` | Block private / local host connections in `parse_sql_table()` with SSRF validation |
| `pyproject.toml` | Set `mypy_path = "src"` for consistent import resolution |
| `Dockerfile` | Use `COPY --chown=app:app` and remove redundant `RUN chown -R` layer, reducing image size by ~1.5 GB |
| `tests/core/glossary_sources/test_glossary_sources_sql_git.py` | Added regression test `test_ssrf_blocked_dsn_rejected` |

### 2026-08-18: Comprehensive Audit Phase 2 Remediations (Polish & Maintainability)

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/translate/tree.py` | Recursively translate `TableNode.cells` `BlockNode` instances in `translate_tree()` and emit chunk events |
| `src/omniscribe/core/translate/workflow.py` | Preserve multi-granularity delimiters (`\n\n`, `\n`, ` `) in `_Chunker` via formatted string accumulation |
| `src/omniscribe/core/grounded/prompted.py` | Guarantee background `asyncio.create_task` cancellation on `CircuitOpenError` or error in `PromptedGroundedOCR` |
| `src/omniscribe/core/processors/table.py` | Safeguard table cell bounding box calculation against non-finite float coordinates |
| `src/omniscribe/core/glossary_sources/git_repo.py` | Validate and sanitize `ref` arguments in `parse_git_glossary()` against CLI option injection |
| `src/omniscribe/api/services/provider_manager.py` | Prevent masked API key previews (`"***"`, `"..."`) from overwriting real secrets in `save_provider()` |
| `src/omniscribe/utils/security.py` | Unconditionally block cloud instance metadata endpoints (`169.254.169.254` / `169.254.0.0/16`) even under `ALLOW_SSRF_LOCAL=true` |
| `src/omniscribe/api/schemas/requests.py` | Accept `text_artifact_id` and `text_artifact_token` in `TranslationRequest` schema |
| `src/omniscribe/api/services/ai.py` | Resolve source text from token-bound artifact store in `translate_text()` when `request.text` is empty |
| `.github/workflows/release.yml` | Correct repository sed substitution regex to match `(OmniScribe\.git\|local-deepl\.git)` |
| `Dockerfile`, `install.ps1`, `install.sh`, `AGENTS.md` | Include `--extra lexicon` in standard `uv sync` commands to provide LanceDB vectorized glossary out-of-the-box |
### 2026-08-19: Distributed Tasks, Real-Time Progress Fanout, Security Hardening & State Parity

| File | Responsibility |
| --- | --- |
| `src/omniscribe/api/tasks.py` | Implement Celery background task `process_ocr_task` with `_OCRTask` base mixin for distributed OCR pipeline execution, progress emissions, and `JobHistory` tracking |
| `src/omniscribe/api/routers/ocr.py` | Wire `POST /api/process/async` to dispatch to Celery `process_ocr_task` when running in `RedisStateBackend` mode, falling back to standalone `OCRJobQueue` in memory/sqlite mode; update `process_status` to query queue, job history, and Celery status |
| `src/omniscribe/api/services/progress.py` | Add Redis Pub/Sub broadcast support (`publish`, `publish_async`) in `ProgressService` publishing progress frames to `omniscribe:progress:{channel_id}` |
| `src/omniscribe/api/routers/websocket.py` | Wire `ConnectionManager.send` to broadcast via Redis Pub/Sub, and spawn async background pubsub listener in `websocket_endpoint` for multi-worker WebSocket event fanout |
| `src/omniscribe/api/services/state/redis.py` | Initialize `ProgressService(redis_url=redis_url)`, standardize `RedisJobHistory` default `max_jobs` to 1000, and implement accurate active key counting in `RedisTextArtifactStore.__len__` |
| `src/omniscribe/api/middleware/rate_limit.py` | Implement `OrderedDict` sliding window with LRU eviction and strict 10,000 active IP bound in `RateLimitMiddleware` to prevent unbounded memory growth |
| `src/omniscribe/utils/security.py` | Unconditionally block IMDS (`169.254.0.0/16`, `fe80::/10`), CGNAT (`100.64.0.0/10`), and `0.0.0.0/8` regardless of `ALLOW_SSRF_LOCAL` setting in `is_ssrf_target` and `is_blocked_host` |
| `src/omniscribe/api/routers/common.py` | Emit `DeprecationWarning` and warning log when `?token=` query param is used in `get_access_token`, prioritizing `Authorization: Bearer` and `X-Artifact-Token` headers |
| `tests/api/services/test_distributed_ocr_tasks.py` | Unit tests for Celery `process_ocr_task` execution, error handling, Redis-mode dispatch, and status resolution |
| `tests/api/middleware/test_security_middleware.py` | Unit tests for `RateLimitMiddleware` LRU bounds (10,000 cap, LRU eviction), `BearerAuthMiddleware`, and `MaxUploadSizeMiddleware` |
| `tests/api/middleware/test_token_deprecation.py` | Unit tests for token sunset deprecation warning emission, log warning, and header precedence |

### 2026-08-23: Core Workflow & Engine Decomposition (Phase 3)

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/workflows/stages/conversion.py` | `HybridConverter` — batched page rasterization streaming through `PDFHandler.convert_batches` with page-range filtering and optional preprocessing |
| `src/omniscribe/core/workflows/stages/layout.py` | `HybridLayoutDetector` & `decode_chunk_bytes` — batched Surya layout detection (`DETECT_CHUNK_SIZE`), whitespace recall booster merging, PDF text-layer recall merging, and dense page classification |
| `src/omniscribe/core/workflows/stages/ocr.py` | `HybridOcrRunner` — concurrent sparse page OCR dispatching with DP alignment, dense per-box OCR dispatching, observer callback emission, and resilient exception unwrapping |
| `src/omniscribe/core/workflows/stages/refine.py` | `HybridRefiner` — crop-and-re-OCR for empty sparse boxes and nearby duplicate deduplication |
| `src/omniscribe/core/workflows/stages/__init__.py` | Stage package re-exports for `HybridConverter`, `HybridLayoutDetector`, `HybridOcrRunner`, `HybridRefiner`, and `decode_chunk_bytes` |
| `src/omniscribe/core/workflows/hybrid.py` | Streamlined `HybridEngine` coordinating the 5 execution phases with 100% backward-compatible delegators |
### 2026-08-27: Flutter Client Consolidation (Slice 5: Workstation & Canvas Migration)

| File | Responsibility |
| --- | --- |
| `client/lib/data/providers/workstation_state.dart` | Immutable Riverpod state model unifying document bytes, viewport transformations, bounding boxes, live OCR status, and confidence statistics |
| `client/lib/data/providers/workstation_notifier.dart` | `WorkstationNotifier` managing document lifecycle, GPU-accelerated canvas pan/zoom, interactive bounding box manipulation, sync/async OCR dispatch, and real-time WebSocket progress ingestion |
| `client/lib/presentation/workstation/workstation_screen.dart` | Riverpod 2.x `ConsumerStatefulWidget` rendering dropzone empty state and wide split-pane workstation layout |
| `client/lib/presentation/workstation/canvas/bbox_painter.dart` | CustomPainter rendering normalized bounding boxes, confidence badges, heatmap tinting, and selection handles |
| `client/lib/presentation/workstation/canvas/bbox_inspector.dart` | Inspect and edit OCR text, confidence scores, and normalized coordinates for selected bounding boxes |
| `client/lib/presentation/workstation/canvas/document_viewport.dart` | GPU-accelerated canvas viewport with smooth pan/zoom, drag/drop handling, and floating zoom controls |
| `client/lib/presentation/workstation/controls/page_strip.dart` | Page thumbnail navigation strip with per-page OCR bounding box indicators |
| `client/lib/presentation/workstation/controls/quality_repair_dock.dart` | Real-time OCR quality metrics, confidence distribution, and low-confidence block repair controls |
| `client/lib/presentation/workstation/controls/right_control_dock.dart` | Parameter controls for pipeline mode, spellcheck, layout enrichment, and document processors |
| `client/lib/presentation/workstation/controls/upload_dropzone.dart` | Drag-and-drop document upload target supporting PDF and image formats |
| `client/lib/presentation/workstation/progress/bottom_progress_dock.dart` | Multi-stage pipeline progress indicator with live stage percentage and cancel triggers |
| `client/lib/presentation/shell/app_shell.dart` | Main application shell with dynamic screen tab routing and DocuVerse theme toggle |
| `client/test/data/workstation_state_test.dart` | Unit tests for `WorkstationState` defaults, immutability, getters, `copyWith`, and equality |
| `client/test/data/workstation_notifier_test.dart` | Unit tests for `WorkstationNotifier` document loading, bounding box editing, viewport manipulation, and OCR processing |
| `client/test/presentation/workstation_screen_test.dart` | Widget tests for `WorkstationScreen` dropzone rendering and full split-pane viewport mode |
| `client/test/presentation/app_shell_test.dart` | Navigation and screen mounting tests for `AppShell` and individual feature screens |

### 2026-08-27: Flutter Client Consolidation (Slice 6: Legacy Purge, Zero-Issue Fast Gate & Windows Desktop Build)

| File | Responsibility |
| --- | --- |
| `client/lib/presentation/widgets/docuverse_select.dart` | Accessible `DocuVerseSelect` dropdown with custom `selectedItemBuilder` to prevent vertical layout overflow |
| `client/lib/presentation/widgets/docuverse_button.dart` | Flexible `DocuVerseButton` with ellipsis truncation preventing `RenderFlex` row overflow |
| `client/lib/presentation/widgets/docuverse_slider.dart` | Constrained `DocuVerseSlider` with `Flexible` label preventing dock overflow |
| `client/lib/presentation/features/translation_screen.dart` | Dual-pane `TranslationScreen` with bounded flex toggles and direct clipboard integration |
| `client/lib/presentation/workstation/controls/page_strip.dart` | Scaled thumbnail caption container preventing overflow during multi-page navigation |
| `client/build/windows/x64/runner/Debug/omniscribe_client.exe` | Verified native Windows x64 desktop executable build artifact |

### 2026-08-27: Flutter Architecture Unification & Feature Parity (Phases 1 - 7)

| File | Responsibility |
| --- | --- |
| `client/lib/main.dart` | Migrated application entrypoint to canonical `AppTheme.lightTheme` / `AppTheme.darkTheme` |
| `client/lib/presentation/shell/app_shell.dart` | Unified shell with top `TabRibbon`, `activeTabProvider` tab routing, and global desktop keyboard shortcuts (`Ctrl+1..7`, `Ctrl+S`) |
| `client/lib/presentation/shell/tab_ribbon.dart` | Navigation ribbon with canonical `App*` components, active indicator, provider modal trigger, and theme toggle |
| `client/lib/presentation/shell/server_health_badge.dart` | Discrete latency badge with controlled status animations that avoid blocking test harnesses |
| `client/lib/presentation/shell/workspace_view.dart` | Split workspace manager view utilizing canonical design system tokens |
| `client/lib/presentation/features/translation_screen.dart` | Dual-pane translation interface with target selector, NLLB mode toggle, and decoupled async job polling |
| `client/lib/presentation/features/transcription_screen.dart` | Speech-to-text transcription studio with interactive audio timeline scrubbing and pure notifier playback management |
| `client/lib/presentation/features/extraction_screen.dart` | Structured JSON entity extractor with preset templates, custom prompts, and live schema validation |
| `client/lib/presentation/features/glossary_screen.dart` | Dual-view terminology management supporting file imports (TBX, CSV, JSON), URL feeds, and real-time term search |
| `client/lib/presentation/settings/settings_screen.dart` | Global settings view with runtime configuration forms, provider catalog, and health probe dashboard |
| `client/lib/presentation/workstation/workstation_screen.dart` | Central OCR workstation integrating document dropzone, GPU canvas, controls, and multi-format export triggers |
| `client/lib/presentation/workstation/canvas/bbox_inspector.dart` | Selected bounding box editor with text correction, Platt calibrated confidence gauge, and normalized coordinate display |
| `client/lib/presentation/workstation/canvas/bbox_painter.dart` | CustomPainter rendering normalized bounding boxes, confidence badges, heatmap tinting, and selection handles |
| `client/lib/presentation/workstation/canvas/document_viewport.dart` | GPU-accelerated canvas viewport with smooth pan/zoom, drag/drop handling, and floating zoom controls |
| `client/lib/presentation/workstation/controls/page_strip.dart` | Multi-page thumbnail navigation strip with responsive bounding box density indicators |
| `client/lib/presentation/workstation/controls/quality_repair_dock.dart` | Real-time OCR quality metrics, confidence threshold slider, retry limits, and self-healing repair statistics |
| `client/lib/presentation/workstation/controls/right_control_dock.dart` | OCR pipeline parameters, spellcheck, layout enrichment, quality repair dock, and trust breakdown panel |
| `client/lib/presentation/workstation/controls/upload_dropzone.dart` | Drag-and-drop document upload target supporting PDF and image formats |
| `client/lib/presentation/workstation/controls/trust_breakdown_panel.dart` | Surfaces Platt calibrated trust scores, OCR confidence distribution, anomaly flag counts, and self-healing repair metrics |
| `client/lib/presentation/workstation/modals/export_modal.dart` | Multi-format document export dialog supporting Searchable PDF, DOCX, DOCX Tree, HTML, Tree JSON, Markdown, and Text |
| `client/lib/presentation/workstation/progress/bottom_progress_dock.dart` | Multi-stage pipeline progress indicator with live stage percentage and cancel triggers |
| `client/lib/presentation/common/section_header.dart` | Responsive section header primitive with flexible truncation preventing `RenderFlex` overflow in narrow docks |
| `client/lib/data/providers/features_notifier.dart` | Feature view-models encapsulating polling timers and audio playback timers with automatic lifecycle disposal |
| `client/build/windows/x64/runner/Debug/omniscribe_client.exe` | Verified native Windows x64 desktop executable build artifact (0 errors, 0 warnings, 175/175 tests passing) |

### 2026-08-27: Flutter Takeover — Phase A (Provider-Config Routes + Auth Banner + Shortcuts + Web Build)

The Flutter client is the canonical UI surface; Phase B has since retired the previous web UI. Provider-config routes (`POST /api/providers/active`, `POST /api/providers/validate`) were added in Phase A; the translation and extraction/export endpoints were then unimplemented (mock fallback notifiers only) — translation shipped 2026-08-30 via the `translate` plugin, extraction/export via the `documents` plugin, and transcription and glossary shipped 2026-08-31 via the `transcribe` and `glossary` plugins.

| File | Responsibility |
| --- | --- |
| `src/omniscribe/plugins/providers.py` | New `SetActiveProviderRequest` / `SetActiveProviderResponse` / `ValidateProviderRequest` / `ValidateProviderResponse` Pydantic models; `ProviderManager.set_active(api_key=…)` extension; new `ProviderManager.validate(...)` async probe method; new `POST /api/providers/active` and `POST /api/providers/validate` routes |
| `client/lib/presentation/common/auth_required_banner.dart` | Dismissible `AuthRequiredBanner` widget wrapped in `Semantics` with `role="status"` + `aria-live="polite"` for screen-reader announcement on auth failures |
| `client/lib/data/providers/repository_providers.dart` | `authRequiredProvider = StateProvider<bool>`; `apiClientProvider` factory wires `onUnauthorized` callback that flips the banner flag |
| `client/lib/core/network/api_client.dart` | New `onUnauthorized` constructor param; called from every Dio 401 catch block (10 methods) for UI flagging (does not suppress exception propagation) |
| `client/lib/data/repositories/config_repository.dart` | Split `getModels(namespace:)` into `getModelsForProvider(providerId)` + back-compat delegator; namespaces `translation` / `transcription` return `const []` (deferred) |
| `client/lib/data/providers/settings_notifier.dart` | `load()` resolves `activeProviderId` from the freshly-fetched config BEFORE the model call (avoids initial-load wrong-provider bug); translation/transcription hard-empty |
| `client/lib/data/providers/workstation_state.dart` | New `filePickSignal` int field for keyboard-shortcut signal plumbing |
| `client/lib/data/providers/workstation_notifier.dart` | New `incrementFilePick()` + `processCurrentDocument()` methods |
| `client/lib/presentation/workstation/controls/upload_dropzone.dart` | `ref.listen<int>` on `filePickSignal` to react to `Ctrl+O` shortcut by opening the file picker |
| `client/lib/presentation/shell/app_shell.dart` | Mount `AuthRequiredBanner` above `TabRibbon`; bind `Ctrl+O` (workstation-only) and `Ctrl+Enter` (workstation-only with loaded document) shortcuts |
| `client/lib/presentation/settings/settings_screen.dart` | Replaced "Auth token UI deferred to slice 5" badge with honest "Auth middleware deferred" copy |
| `client/web/` | New Flutter web platform assets (`index.html`, `manifest.json`, icons); `client/build/web/index.html` builds cleanly via `flutter build web --release` |
| `client/scripts/build_web.sh` | Manual web bundle build helper |
| `tests/openapi.json` | Regenerated snapshot for the two new routes + four new schemas |

### 2026-08-28: Comprehensive 5-Domain Architecture & Code Quality Audit

Conducted a full-repository parallel audit covering Core Pipeline, API & Security, Frontend (Flutter Client), Testing & QA, and DevOps & Configuration. Key architectural findings and prioritized tech debt logged:

| Domain | Key Findings & Risks | Planned Action |
| --- | --- | --- |
| **Core Pipeline** | - Page-range subset filter bug in `HybridConverter` drops pages 4+ (`conversion.py`).<br>- Anthropic VLM payload inserts `role: system` inside `messages` instead of top-level key (`multi_format_client.py`).<br>- `OCRProcessor` instantiates isolated circuit breaker registries by default rather than process shared singleton (`processor.py`).<br>- Multi-frame TIFF/image rasterizer loop duplicates final frame across all pages (`rasterizer.py`).<br>- Geometry preprocessors (crop/deskew) misalign sandwich PDF text layer coordinates (`page_preprocess.py`). | Fix page-range filter, move Anthropic system prompt to top-level payload, unify circuit breaker registries, seek frames per iteration in rasterizer. |
| **API & Security** | - `server.py` lacks active ASGI Auth Middleware while `SECURITY.md` documents enforcement.<br>- Unauthenticated blind SSRF in `providers.py` (`/api/providers/validate` and `discover_models`).<br>- `GET /api/config` leaks LLM `api_key` in plaintext.<br>- Unbounded `await upload.read()` memory buffering before upload size cap check (`ocr/plugin.py`).<br>- `is_blocked_host` fails open on DNS resolution exceptions (`security.py`).<br>- Multi-worker partitioning when using `MemoryStateBackend`. | Rebuild Auth Middleware plugin, mask secrets in config responses, validate `is_ssrf_target` on provider discovery, stream multipart uploads, fail-closed on DNS errors. |
| **Frontend Client** | - Raw HTML injection in document export modal (`export_modal.dart`).<br>- Server base URL changes in Settings do not propagate to `ApiClient`/`WsClient` (`settings_notifier.dart`).<br>- Premature WebSocket teardown in `processOcrAsync` terminates progress channels.<br>- `AppButton` lacks keyboard Tab focus and fails 48x48 dp minimum touch targets. | Sanitize HTML export with `htmlEscape.convert`, bind `apiBaseUrlProvider` reactively, remove premature cleanup on 202` status, implement `FocusableActionDetector` on buttons. |
| **Testing & QA** | - Zero Flutter CI in GitHub Actions workflows (`test.yml`).<br>- Mypy suppresses all type errors in `tests/` (`ignore_errors = true`).<br>- `core/transcription/` has 0% unit test coverage.<br>- `QualityRepairLoop` lacks standalone unit test suite and is untested on hybrid path.<br>- Synchronous tests calling `asyncio.run()` instead of `async def test_`. | Add Flutter CI job, remove Mypy test ignore flag, add transcription and repair loop test suites, migrate sync `asyncio.run` tests. |
| **DevOps & Config** | - Docker/Compose healthchecks fail with 404 (probing `/health` instead of `/api/health`).<br>- Stale Celery worker command crashes on startup (`ModuleNotFoundError: No module named 'omniscribe.api'`).<br>- `.dockerignore` misses `client/` causing context bloat.<br>- `.env.example` lists unread `OCR_*` variables not parsed by server. | Point healthcheck to `/api/health`, disable deferred Celery worker in compose, add `client/` to `.dockerignore`, sync `.env.example`. |

### 2026-08-30: Flutter Smart Preset Domain Models & Preset Detection Logic

| File | Responsibility |
| --- | --- |
| `client/lib/data/models/smart_preset.dart` | Immutable `SmartPreset` domain model defining 6 specialized OCR profiles (`standard`, `receipt`, `handwriting`, `historical`, `fast`, `deep`), filename suggestion heuristics (`suggestForFilename`), settings application (`applyToSettings`), and active preset reverse detection (`detectActivePreset`) |
| `client/lib/data/models/models.dart` | Barrel export exposing `smart_preset.dart` to the client application |
| `client/test/data/models/smart_preset_test.dart` | Unit test suite verifying preset integrity, metadata, filename suggestions, settings application, and preset matching logic |

### 2026-09-01: Wave 7 — Domain D: Harness & Engine Polish

Hardening, observability improvements, and polish across harness loading, invisible PDF text layer embedding, and progress event handling:

| File | Responsibility |
| --- | --- |
| `src/omniscribe/harness/loader.py` | `Loader.load()` validates explicit `patch_paths` (including `OMNISCRIBE_CORDIS_PATCH`) and warns if missing; simplified exception propagation preserving `PluginLoadError`; logged plugin mount count; enhanced `_instantiate()` error reporting with target row plugin use and ID. |
| `src/omniscribe/core/pdf/embedder_helpers.py` | Implemented `_log_once` helper replacing `_UNICODE_GLYPH_MISS_LOGGED` boolean flag; eliminated `exc_info=True` noise from expected font probe failures; added Persian `peh` (`\u067e` / `0x067E`) to `_PROBE_CODEPOINTS`. |
| `src/omniscribe/core/pdf/embedder.py` | Re-exported `_PROBE_CODEPOINTS` and `_log_once` in `__all__` for module surface parity. |
| `src/omniscribe/plugins/progress.py` | Narrowed `_on_foreign_send_done` exception handling to catch `KeyError` cleanly on concurrent channel disconnects while logging unexpected failures via `_LOGGER.exception`. |

### 2026-09-01: Wave 7 — Domain A: Security & Recall Hygiene

Security fail-closed hardening, recall constant deduplication, and observable lifecycle logging across core recall and network security:

| File | Responsibility |
| --- | --- |
| `src/omniscribe/core/recall/__init__.py` | Export shared recall constants (`MAX_RECALL_BOXES_PER_PAGE = 10`, `STRADDLE_MIN_OVERLAP = 0.15`) and backward-compatible aliases for recall passes. |
| `src/omniscribe/core/recall/text_layer.py` | Add debug logging when skipping non-PDF inputs or failing document open; consume `DISABLE_STRINGS` from `omniscribe.utils.env` and promoted constants from `omniscribe.core.recall`. |
| `src/omniscribe/core/recall/whitespace.py` | Fix stale plan docstring reference to point to `docs/ARCHITECTURE.md`; consume `DISABLE_STRINGS` from `omniscribe.utils.env`; align `_MAX_WHITESPACE_BOXES_PER_PAGE` and `_STRADDLE_MIN_OVERLAP` with standard recall constants. |
| `src/omniscribe/utils/security.py` | Document blocking `socket.getaddrinfo` DNS hazard in `is_blocked_host` docstring warning against async thread use; document intentional non-public blocking stance for `normalized.is_reserved` (240.0.0.0/4 and CGNAT 100.64.0.0/10). |
| `src/omniscribe/utils/env.py` | Export canonical `DISABLE_STRINGS: Final[frozenset[str]]` for standardized falsy environment variable parsing. |
| `tests/core/recall/test_text_layer_recall.py` | Regression tests for non-PDF/corrupted-PDF debug logging and recall constant parity. |
| `### 2026-09-01: Wave 7 — Domain B: Environment, Booleans & Configuration

Standardized boolean parsing vocabularies, config seed observability, and state-backend allowlist validation:

| File | Responsibility |
| --- | --- |
| `src/omniscribe/utils/env.py` | Declare canonical `ENABLE_STRINGS` and `DISABLE_STRINGS` sets; implement `parse_bool` and `env_bool` unifying truthy/falsy evaluation across env vars and form data. |
| `src/omniscribe/plugins/ocr/schemas.py` | Delegate `_parse_bool` to `omniscribe.utils.env.parse_bool` supporting extended booleans (`"enabled"`, `"disabled"`, etc.) uniformly. |
| `src/omniscribe/config.py` | Validate `state_backend` strictly against `{"memory", "sqlite"}` with explicit early guidance when unbuilt backends like `redis` are requested. |
| `src/omniscribe/plugins/ocr/service.py` | Document canonical 24 exposed keys in `_CONFIG_KEY_SET` for the `/api/config` endpoint. |
| `.env.example` | Document `OMNISCRIBE_LLM_*` aliases alongside canonical `LLM_*` variables. |
| `tests/utils/test_env.py` | Cover canonical boolean sets, `parse_bool`, and `env_bool` truthy/falsy parsing. |
| `tests/plugins/test_ocr_schemas.py` | Cover uniform boolean parsing across form requests. |
| `tests/test_cordis_settings.py` | Validate state backend default, SQLite acceptance, and Redis rejection. |

### 2026-09-01: Wave 7 — Domain C: Server & VLM Client Lifecycle

Clean server module logging and settings instantiation, preflight client isolation, and developer documentation reconciliation:

| File | Responsibility |
| --- | --- |
| `src/omniscribe/server.py` | Remove redundant module-level `_LOGGER`, eliminate inner `import logging` inside `_unhandled_exception_handler`, deduplicate `load_settings()` calls in `create_app()`, add `_load_attr` helper, and standardize divider comment styles. |
| `src/omniscribe/core/ocr/processor.py` | Isolate `ensure_model_loaded()` client lifecycle using an ephemeral client closed in `finally` without mutating or closing `self.client`; update references to `TestPromptConstants`. |
| `docs/AGENTS.md` | Reconcile model pre-flight documentation with in-core `ensure_model_loaded()`; document `OMNISCRIBE_VLM_PAGE_MAX_TOKENS` and `OMNISCRIBE_VLM_CROP_MAX_TOKENS` tunables; list `tests/core/ocr/test_ocr.py` in test inventory. |

### 2026-09-02: Wave 12 — Comprehensive Bug Fixes & Architectural Hardening

Comprehensive remediation across authentication, upload streaming, SSRF prevention, network isolation, core pipeline accuracy, and client export/health monitoring:

| File | Responsibility |
| --- | --- |
| `src/omniscribe/middleware/auth.py` | Add `/ready`, `/readyz`, and `/api/healthz` to `EXEMPT_EXACT_PATHS` to prevent Kubernetes container readiness/liveness lockout under `OMNISCRIBE_AUTH_TOKEN`. |
| `src/omniscribe/plugins/ocr/service.py` | Enforce SSRF validation on user-supplied `api_base` in `preflight_check`; safely manage ephemeral client in model listing. |
| `src/omniscribe/plugins/ocr/plugin.py` | Return HTTP 403 `ssrf_blocked` on blocked preflight requests; stream uploads in 1 MB chunks to bound memory against limits; enforce format sniffing on empty or octet-stream `Content-Type`. |
| `src/omniscribe/plugins/glossary/http_fetch.py` | Replace process-wide `socket.getaddrinfo` mutation with an isolated `_PinnedNetworkBackend` (`httpcore.AsyncNetworkBackend`) and `_PinnedIPTransport`. |
| `src/omniscribe/core/workflows/utils.py` | Replace naive substring containment with token boundary word matching in `_drop_refined_duplicates` to prevent erroneous deletion of short tokens. |
| `src/omniscribe/core/grounded/prompted.py` | Explicitly cancel uncompleted background tasks in `finally` before `asyncio.gather` in `PromptedGroundedOCR.ocr_document`. |
| `src/omniscribe/core/ocr/processor.py` | Initialize `self.client = None` to avoid creating an unused `AsyncOpenAI` connection pool on each request. |
| `src/omniscribe/core/ocr/chat_client.py` | Align data URI MIME scheme to `data:image/jpeg;base64,{image_base64}` matching rasterizer output. |
| `client/lib/presentation/workstation/modals/export_modal.dart` | Decouple `ExportFormat.docxTree` from `ExportFormat.html` and wire to `repo.exportDocxTree`; support saving for `ExportFormat.searchablePdf`. |
| `client/lib/presentation/shell/shell_state.dart` | Implement `checkHealth()` on `ServerHealthNotifier` to ping `/api/health` via `ApiClient` and measure latency. |
| `client/lib/presentation/shell/server_health_badge.dart` | Replace simulated timer in badge `onTap` with real `checkHealth()` trigger. |
| `tests/middleware/test_auth.py` | Add regression tests for container health probe exemptions. |
| `tests/plugins/test_ocr_schemas.py` | Add tests for preflight SSRF blocking and empty content-type format sniffing. |
| `tests/core/grounded/test_grounded.py` | Add unit test for task cancellation on grounded failure. |
| `tests/core/ocr/test_ocr_processor.py` | Add test verifying lazy client initialization and safe `aclose()`. |
| `tests/core/ocr/test_ocr.py` | Add test verifying JPEG data URI in chat client. |

### 2026-09-02: Wave 13 — Architectural Hardening, Rate Limiting & Performance Optimization

Wave 13 comprehensively remediates outstanding backlog findings across middleware rate limiting, startup security, state backends, core pipeline algorithms, and client status discrimination:

| File | Responsibility |
| --- | --- |
| `src/omniscribe/middleware/rate_limit.py` | ASGI 3.0 `RateLimitMiddleware` enforcing sliding-window request limits per client IP (or forwarded header from trusted proxies) with 429 Retry-After response and health/readiness/static exemptions (§6.1b). |
| `src/omniscribe/server.py` | Hardened startup validation by moving bind-host and placeholder auth token checks into `_validate_runtime_settings` to prevent direct uvicorn launch bypass; wired `RateLimitMiddleware` into `create_app`; sanitized `ValueError` details in `value_error_handler` against system path and traceback leaks. |
| `tests/middleware/test_rate_limit.py` | Comprehensive test suite for `RateLimitMiddleware`, verifying pass-through below limit, 429 + Retry-After when exceeding limit, probe and static exemptions, per-IP isolation, sliding-window expiration, memory eviction bounds, and TestClient integration. |
| `src/omniscribe/plugins/state_backend_memory.py` | Constant-time `secrets.compare_digest` artifact token verification in `get_artifact`. |
| `src/omniscribe/plugins/state_backend_sqlite.py` | Set `conn.row_factory = sqlite3.Row`; verify `PRAGMA journal_mode=WAL` with warning if not `"wal"` (§4.25); adopt named row/dict access in `_job_from_row`, `_channel_from_row`, and `_artifact_from_row` (§6.40); extract `_rowcount` helper (§6.42); use constant-time `secrets.compare_digest` in `get_artifact`. |
| `src/omniscribe/plugins/progress.py` | Enforce WebSocket Origin header validation against `settings.cors_origins` in `_handle_ws` (closes 4403 if rejected); accept optional `session_token` via query param or `X-Session-Token` header in `cancel_channel` and verify with `secrets.compare_digest` (403 on mismatch). |
| `src/omniscribe/plugins/providers.py` | Accept `X-Provider-Api-Key` and `Authorization: Bearer <key>` headers in `provider_models` (`GET /{provider_id}/models`) resolving API key to prevent access-log leakage. |
| `src/omniscribe/plugins/ocr/schemas.py` | Harden `AsyncSubmitResponse.status` from unconstrained `str` to `Literal["pending", "processing", "complete", "error", "cancelled"]` (§6.35). |
| `tests/plugins/test_state_backend_sqlite.py` | Tests for WAL mode non-WAL warning, named row mapping with `Row` and `dict`, constant-time token comparison, and `_rowcount`. |
| `tests/plugins/test_progress_plugin.py` | Tests for WebSocket origin checks (disallowed, allowed, wildcard, absent) and `cancel_channel` session token validation. |
| `tests/plugins/test_providers_plugin.py` | Tests for `X-Provider-Api-Key`, `Authorization: Bearer`, and query param precedence in `provider_models`. |
| `src/omniscribe/core/workflows/grounded.py` | $O(1)$ block lookup in `_repair_blocks` via pre-computed block identity dictionary, eliminating $O(\text{repaired} \times \text{blocks})$ scan. |
| `src/omniscribe/core/pdf/embedder.py` | Bounded-batch page rasterization (`batch_size = max(parallelism * 2, 8)`) in `embed_structured_text` reusing thread pool across batches to prevent multi-hundred-page heap spikes. |
| `src/omniscribe/core/workflows/stages/layout.py` | Single-pass image decoding in `decode_chunk_bytes` feeding decoded raw bytes to Pillow to eliminate double base64 decode per page. |
| `src/omniscribe/core/workflows/hybrid.py` | Run-scoped `(run_id, page_num)` key schema in `_decoded_cache` preventing cross-run page collision in concurrent hybrid executions (§4.39). |
| `src/omniscribe/core/transcription/local_engine.py` | Thread-safe model loading in `WhisperLocalEngine._get_model` using double-checked locking with `threading.Lock`. |
| `src/omniscribe/utils/json_parse.py` | Index-based raw decode `decoder.raw_decode(stripped, idx=start)` eliminating $O(n^2)$ substring slice allocations on large responses (§4.29). |
| `src/omniscribe/core/translate/__init__.py` | Re-export `TRANSLATION_SYSTEM_MESSAGE` preserving modular boundary between core translate engine and plugin (item 9.10). |
| `src/omniscribe/plugins/translate/service.py` | Import `TRANSLATION_SYSTEM_MESSAGE` from stable `omniscribe.core.translate` boundary. |
| `src/omniscribe/plugins/transcribe/schemas.py` | Co-locate `unpack_transcribe_options` helper next to `TranscribeRequest` schema (item 9.12). |
| `src/omniscribe/harness/loader.py` | Informational log record on cordis patch file application. |
| `tests/utils/test_json_parse.py` | Comprehensive test suite for index-based JSON extractor across direct, fenced, and embedded structures. |
| `client/lib/data/models/job_record.dart` | Distinguish `isCancelled` from `isError` on `OcrJobStatusResponse` with dedicated `bool get isCancelled => status == 'cancelled'`. |
| `client/lib/data/repositories/job_repository.dart` | Remove `queryParameters: {'token': token}` from `JobRepositoryImpl.downloadResult` so artifact token is supplied exclusively via Authorization header. |
| `client/lib/core/constants/api_constants.dart` | Remove dead endpoint constants `health`, `healthz`, and `apiReady`. |
| `client/lib/presentation/jobs/job_history_screen.dart` | Save downloaded PDF bytes to disk via `FilePicker.platform.saveFile` in `_handleDownload`. |
| `client/test/data/job_record_test.dart` | Unit tests asserting strict discrimination between `isCancelled`, `isError`, and `isComplete`. |

### 2026-09-03: Wave 14 — Final Backlog Sweep & Architecture Finalization

Wave 14 completes the remaining architectural, security, performance, and testing backlog from `docs/outstanding-work.md`:

| File | Responsibility |
| --- | --- |
| `src/omniscribe/middleware/upload_limit.py` | ASGI 3.0 `UploadSizeLimitMiddleware` enforcing payload limits via Content-Length inspection and streaming chunk accumulation with 413 responses. |
| `src/omniscribe/server.py` | Wired `UploadSizeLimitMiddleware` into `create_app()`; added HTTP 503 `Retry-After` exception handler for `CircuitOpenError`; removed module-level `load_dotenv()`. |
| `src/omniscribe/plugins/ocr/plugin.py` | Protected `DELETE /api/jobs` with `confirm=true` requirement to prevent accidental total wipe; clarified terminal status in `cancel_job`; passed MIME `content_type` into service runners. |
| `src/omniscribe/plugins/ocr/service.py` | Added `_guess_suffix` format sniffing for extensionless uploads; removed redundant progress assertions; added change detection in `update_config`. |
| `src/omniscribe/plugins/state_backend_types.py` | Extracted state backend domain records (`ArtifactBlob`, `ChannelRecord`, `JobRecord`, `ArtifactRecord`) and `StateBackend` protocol; documented `JobRecord` hashability contract. |
| `src/omniscribe/plugins/state_backend.py` | Eliminated circular import workarounds; cleanly imports domain types and protocols from `state_backend_types.py`. |
| `src/omniscribe/plugins/state_backend_sqlite.py` | Enforced `0o700` directory permissions on POSIX systems in `_open_sync`. |
| `src/omniscribe/config.py` | Replaced magic strings with `DEFAULT_GROUNDED_MODEL` constant; normalized non-positive rate limits; supported typed `cors_origins: list[str]`. |
| `src/omniscribe/core/ocr/processor.py` | Hoisted `import base64` to module top-level; moved `load_dotenv()` into `__init__`. |
| `src/omniscribe/core/pdf/embedder.py` | Added `garbage=3, deflate=True` stream compression on searchable PDF saves (§6.30); unified `page_nums` initialization (§6.31). |
| `src/omniscribe/core/pdf/embedder_helpers.py` | Trimmed outdated 470-LOC docstrings (§4.26). |
| `src/omniscribe/core/workflows/stages/layout.py` | Removed unused `input_path` parameter from `detect_layout` (§4.9). |
| `src/omniscribe/core/workflows/hybrid.py` | Defensive copy on `trust_images_dict` to prevent aliased cross-stage mutation (§4.40); documented stage run state resets (§4.38). |
| `src/omniscribe/core/grounded/prompted.py` | Explicit `last_exc` invariant raising `RuntimeError` rather than relying on `assert` under `python -O` (§4.4). |
| `client/lib/data/providers/workstation_notifier.dart` | Handled unexpected WebSocket closure in `processOcrAsync` via fallback `_handleWsClosed()`, polling `getJobStatus` and downloading result artifacts. |
| `client/lib/data/repositories/ocr_repository.dart` | Exposed `getJobStatus` and `downloadResult` on `OcrRepository`. |
| `client/test/data/workstation_notifier_test.dart` | Unit tests for workstation notifier WebSocket disconnection fallback. |
| `tests/middleware/test_upload_limit.py` | Unit tests for `UploadSizeLimitMiddleware` (limits, streaming, exemptions, 413). |
| `tests/test_config.py` | Unit tests for runtime configuration, model inheritance, and CORS normalization. |
| `tests/core/pdf/test_embedder.py` | Unit tests for searchable PDF embedding with compression and unified page bounds. |
| `tests/core/imaging/test_page_preprocess.py` | Comprehensive unit tests for `PagePreprocessingOptions`, `PagePreprocessingResult`, and `CompositePagePreprocessor` (orientation, deskew, contrast, crop cleanup). |
| `tests/core/ocr_quality/test_routing.py` | Comprehensive unit tests for `QualityRoutingPolicy.apply` covering `empty_page`, `sparse_text`, and `empty_large_block` findings and decisions. |
| `.env.example` | Active default `REDIS_PASSWORD=` (empty) so `cp .env.example .env && docker compose up` fails fast with Compose's `:?` substitution until the operator sets a real password. Active default `ALLOW_SSRF_LOCAL=false` mirroring the code default; compose.yaml pins the same value so the safe default holds without an `.env`. |
| `compose.yaml` | Aligned commentary on the fail-fast env-var contract; `REDIS_PASSWORD:?` substitution in three sites (`REDIS_URL`, `requirepass`, redis healthcheck). |
| `.github/workflows/nightly.yml` | Cleaned up stale `# force_run: slow overrides default skip` comment above `uv run pytest -m slow`. |

### 2026-09-05: Cooperative OCR Cancellation ASGI Boundary Fix

| File | Responsibility |
| --- | --- |
| `src/omniscribe/plugins/ocr/plugin.py` | Caught `OCRCancelled` in `process_sync` to return a structured HTTP 503 `JSONResponse` (`cancelled: true`, `error: "cancelled"`, `detail`) preventing `BaseException` escape to uvicorn. |
| `src/omniscribe/server.py` | Added defense-in-depth cancellation `BaseException` handling in `LazyASGIApp.__call__` and aligned ASGI type signatures with `MutableMapping[str, Any]`. |
| `tests/plugins/test_ocr_plugin.py` | Added `test_process_sync_returns_503_when_cancelled` verifying sync cancellation translation to HTTP 503. |
| `tests/core/workflows/test_ocr_cancellation.py` | Reimplemented `test_route_returns_503_when_engine_raises_ocrcancelled` in `TestProcessRouteCancel` to test cancellation translation against the modern plugin harness. |

### 2026-09-06: Client OCR Timeout Extension & Unhandled Exception Containment

| File | Responsibility |
| --- | --- |
| `client/lib/core/constants/api_constants.dart` | Declared `defaultOcrReceiveTimeout = Duration(minutes: 30)` for long-running multi-page OCR jobs. |
| `client/lib/core/network/api_client.dart` | Added `receiveTimeout`, `sendTimeout`, and `options` forwarding in `postMultipartBytes` merging with effective `RequestOptions`. |
| `client/lib/data/repositories/ocr_repository.dart` | Updated `OcrRepository` interface and `OcrRepositoryImpl.processOcrSync` to default to 30-minute `defaultOcrReceiveTimeout`. |
| `client/lib/data/providers/workstation_notifier.dart` | Added `receiveTimeout` support in `processOcrSync` and safe exception handling in `processCurrentDocument`. |
| `client/lib/presentation/workstation/workstation_screen.dart` | Wrapped `_handleProcessDocument` in `try/catch` with floating error `SnackBar` and integrated `settingsStateProvider.useAsync`. |
| `client/lib/presentation/workstation/controls/right_control_dock.dart` | Added `Background Queue (Async)` toggle in the advanced pipeline tuning card. |
### 2026-09-06: Document Viewport Rendering, Auto-Fit Canvas & Responsive Workstation Layout

| File | Responsibility |
| --- | --- |
| `src/omniscribe/plugins/ocr/plugin.py` | Added `POST /api/documents/preview` endpoint using PyMuPDF stream rendering via `asyncio.to_thread` returning image/png with `X-Total-Pages`, `X-Page-Width`, `X-Page-Height` headers. |
| `tests/plugins/test_ocr_plugin.py` | Added `test_document_page_preview` verifying status 200, PNG byte response, and page geometry headers. |
| `client/lib/core/constants/api_constants.dart` | Added `documentPreview = '/api/documents/preview'` and preview header constants. |
| `client/lib/data/models/document_result.dart` | Added `PagePreviewResult` model, `previewBytes` property to `PageResult`, synchronous `parseImageDimensions` binary header parser for PNG/JPEG, and dynamic intrinsic aspect ratio fallback. |
| `client/lib/data/repositories/ocr_repository.dart` | Added `renderDocumentPagePreview` to `OcrRepository` interface and implementation using `response.getHeader`. |
| `client/lib/data/providers/workstation_state.dart` | Added `isPreviewLoading` and `previewError` fields to `WorkstationState` for deterministic UI state tracking. |
| `client/lib/data/providers/workstation_notifier.dart` | Populated instant image preview bytes and dimensions in `loadDocument`, added asynchronous PyMuPDF preview rasterization with `isPreviewLoading` state transitions, error recording, dimension sniffing, and `retryPagePreview`. |
| `client/lib/presentation/workstation/controls/page_strip.dart` | Rendered live page preview thumbnails in `PageStrip` with document icon fallback. |
| `client/lib/presentation/workstation/canvas/document_viewport.dart` | Added `LayoutBuilder` viewport tracking, unconstrained canvas in `InteractiveViewer` (`constrained: false`) to prevent viewport height clamping distortion, dynamic aspect ratio auto-refit, centered zoom/reset translation math, `FittedBox` title row constraints, auto-fetch for missing previews, and high-fidelity fallback card with retry button. |
| `client/lib/presentation/workstation/workstation_screen.dart` | Lowered `isWide` split-pane breakpoint from 1080px to 768px with adaptive dock and inspector sizing to preserve side-by-side workstation layout on desktop windows. |
| `client/test/presentation/workstation_screen_test.dart` | Updated widget test suite with mock `renderDocumentPagePreview` stubbing and verified narrow layout branch at 600px width. |
| `client/test/data/workstation_notifier_test.dart` | Added unit tests verifying binary PNG header dimension sniffing, `PageResult.aspectRatio` calculation, and image upload dimension inference. |

### 2026-09-06: Workstation UI/UX Consolidation & Left Page Strip Rail (Phase 2 Domain 1)

| File | Responsibility |
| --- | --- |
| `client/lib/presentation/workstation/workstation_screen.dart` | Consolidated the workstation header bar: unified document title/scanner icon, page navigation (`< Page X of Y >`), layer toggles (`Boxes`, `Heatmap`), export/clear buttons, and status badge into a single 52px top bar with overflow protection. Moved `PageStrip` (`Axis.vertical`) to the left rail of the workstation row layout, removing bottom horizontal strip and giving full vertical canvas space. |
| `client/lib/presentation/workstation/canvas/document_viewport.dart` | Removed redundant inner ribbon `_buildTopRibbon` row from viewport, expanding GPU canvas to 100% of container height while preserving floating zoom/fit controls. |
| `client/lib/presentation/workstation/controls/page_strip.dart` | Converted to `ConsumerStatefulWidget` managing `ScrollController`. Added bounded height (`116px`) per card in vertical `ListView.separated` to eliminate `RenderFlex` unbounded height exceptions, and implemented auto-scrolling to active page upon index change. |
| `client/test/presentation/workstation_screen_test.dart` | Added tests for unified header controls, page navigation, layer toggle callbacks, and vertical `PageStrip` orientation. |

### 2026-09-06: Workstation Preview Caching, Client Repository Updates & Progressive Background Preloader (Phase 2 Domain 2)

| File | Responsibility |
| --- | --- |
| `src/omniscribe/plugins/ocr/plugin.py` | In-memory bounded LRU cache (`_preview_doc_cache`, capacity 10) for uploaded document blobs, `doc_id` reuse via form field or `X-Document-Id` header without requiring file re-upload, strict boundary validation (`page >= 0`, clamped `dpi` [50, 300]), deterministic SHA-256 16-hex `doc_id` generation, and `X-Document-Id` response header propagation. |
| `src/omniscribe/server.py` | Exposes `X-Document-Id`, `X-Total-Pages`, `X-Page-Width`, `X-Page-Height` in CORS `expose_headers` list. |
| `tests/plugins/test_ocr_plugin.py` | Added unit test coverage for preview caching, doc_id reuse without file upload, boundary validation, and LRU cache eviction at capacity. |
| `client/lib/core/constants/api_constants.dart` | Added `headerDocumentId = 'x-document-id'` constant. |
| `client/lib/data/models/document_result.dart` | Extended `PagePreviewResult` model with immutable `final String? docId` field and updated constructor. |
| `client/lib/data/repositories/ocr_repository.dart` | Updated `renderDocumentPagePreview` interface and implementation to accept optional `Uint8List? fileBytes` and `String? docId`, conditionally omitting `'file'` multipart field when `fileBytes == null`, and parsing `X-Document-Id` response header. |
| `client/test/data/ocr_repository_test.dart` | Unit tests verifying `OcrRepositoryImpl.renderDocumentPagePreview` form data construction, docId header parsing, file omission, and input validation. |
| `client/lib/data/providers/workstation_notifier.dart` | Implemented progressive background preloader in `WorkstationNotifier`: tracking `_previewDocId` and `_preloadGeneration`, non-blocking queue prioritized by page distance with forward bias (`current + 1, current + 2, current - 1, current + 3...`), in-flight cancellation guards on `clearDocument` and new loads, 25ms event loop yields, and complete isolation of `isPreviewLoading` and `previewError` from background operations. |
### 2026-09-06: Preview Cache Fallback & Preloader Concurrency Hardening

| File | Responsibility |
| --- | --- |
| `client/lib/data/repositories/ocr_repository.dart` | Hardened `renderDocumentPagePreview` with two-tier transport: attempts lightweight `doc_id` form payload first without sending file bytes, and automatically catches failures (e.g. server restart / cache eviction) to fall back to `fileBytes` multipart upload and re-acquire session `docId`. |
| `client/lib/data/providers/workstation_notifier.dart` | Protected `_loadDocumentPreview` race guard by resetting `isPreviewLoading: false` when generation changes, and prevented background preloader from duplicating requests for the active page while ensuring `isPreviewLoading` is cleared if preloader fulfills active page. |
| `src/omniscribe/server.py` & `src/omniscribe/plugins/ocr/plugin.py` | Stabilized long-running uvicorn server daemon process for PyMuPDF rasterization endpoint on `http://127.0.0.1:8000`. |

## See Also


- [README.md](README.md) — feature overview, install, web workspace
- [CHANGELOG.md](CHANGELOG.md) — version history and breaking changes
- [DEPLOYMENT.md](DEPLOYMENT.md) — local / LAN / public-internet deployment profiles
- [SECURITY.md](SECURITY.md) — threat model, hardening checklist, vulnerability disclosure
- [AGENTS.md](AGENTS.md) — contributor guide and full env-var reference
- `audits/` — historical and comprehensive domain audit logs

_Last updated: 2026-09-06_


