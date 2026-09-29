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

Digital (.docx/.html/.md) -> Reader Fast Path (core/readers/) -> synthetic DocumentResult (confidence=1.0, trust_flags=("source:digital",)) -> synthetic PDF renderer -> searchable PDF (bypasses Surya and VLM)
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
├─ state_backend  StateBackend service: sqlite (default, durable persistence),
│                 memory (ephemeral), or redis (Profile 4 multi-worker LAN); single registration site
├─ artifacts      ArtifactStore: opaque id/token blob store over the backend
├─ jobs           JobQueue: single-worker async queue (inprocess mode) or
│                 distributed Redis queue (redis mode via jobs_redis.py) +
│                 JobQueued/Started/Completed/Failed/Cancelled events;
│                 resolves the JobRunner registered for the job type
├─ progress       ProgressService: one-shot session tokens, WS attach with
│                 cross-loop send marshaling; /api/progress/* + /ws/{channel_id}
├─ providers      provider catalog + model discovery (/api/providers*)
├─ health         liveness (/api/health, /api/healthz) and readiness (/ready, /readyz)
├─ documents      extraction + export routes over the token-bound ArtifactStore:
│                 POST /api/extract (extraction prompts re-homed verbatim,
│                 PROMPT_VERSION 2026-08-15.v1), /api/export/* builders
│                 (text/markdown/json/docling/mineru), GET|POST /api/export/markdown (GET header tokens),
│                 GET|POST /api/export/chunks (GET header tokens), and the token-bound
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
├─ ocr            OCRService + JobRunner; /api/process*, /api/jobs*, /api/config*,
│                 SSE /api/process/{job_id}/events; wires BlockCallbackSet
│                 (block_complete, page_complete, repair frames) to ProgressService;
│                 seeds the quality-loop defaults
├─ sample_pdfs    SamplePdfsPlugin: GET /api/sample-pdf/{name} allowlisted test fixture distribution
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
| `src/omniscribe/confidence_eval.py` | Package-root confidence evaluator: GLM-OCR fixture loader, greedy IoU matching, per-document `ConfidenceReport`, and end-to-end PDF-to-Markdown scoring metrics (CER, WER, BLEU, chrF, heading hierarchy F1, table similarity) |
| `src/omniscribe/core/document.py` | Normalized `DocumentResult` IR, pages, blocks, spans, text aggregation, and legacy pages-data adapter |
| `src/omniscribe/core/errors.py` | Shared domain exceptions plus safe job-error and exception redaction before failures reach persistent state, logs, or clients |
| `src/omniscribe/core/processors/__init__.py` | Package-level re-exports for backward-compatible import of `DocumentProcessor`, `DocumentProcessorRegistry`, built-in processors, and helper functions |
| `src/omniscribe/core/processors/base.py` | Core `DocumentProcessor` protocol, `DocumentProcessorFactory`, `DocumentProcessorRegistry`, processor name lists, shared regexes, helper functions (`_structure_kind`, `_normalize_space`, `_page_region`, `_bbox_area`), `build_document_processors`, and `run_document_processors` |
| `src/omniscribe/core/processors/reading_order.py` | `ReadingOrderProcessor` — row-major block ordering based on normalized bounding box coordinates |
| `src/omniscribe/core/processors/quality.py` | `QualityAnalysisProcessor` — page-level OCR quality findings (empty pages, sparse text, large empty blocks) |
| `src/omniscribe/core/processors/structure.py` | `StructureAnalysisProcessor` — deterministic block structure hints (headings, list items, key-values, table candidates) |
| `src/omniscribe/core/processors/section.py` | `SectionAnalysisProcessor` — section heading detection and block grouping across page boundaries |
| `src/omniscribe/core/processors/layout.py` | `LayoutEnrichmentProcessor` — page region and layout role labeling (headers, footers, page numbers, figures, captions) |
| `src/omniscribe/core/processors/table.py` | `TableExtractionProcessor` — table grid structure extraction from aligned text blocks |
| `src/omniscribe/core/processors/table_fallback.py` | `TableFallbackProcessor` — dedicated grid reconstruction, alignment heuristics, and fail-open table repair for low-confidence tables (RFC 004 R5) |
| `src/omniscribe/core/aligner.py` | Surya detection and DP text-to-box alignment |
| `src/omniscribe/core/recall/base.py` | `BaseRecallOptions` — shared frozen dataclass with `_from_env` helper for secondary text-recall passes |
| `src/omniscribe/core/recall/whitespace.py` | Whitespace recall booster — pixel-statistics text-line candidates merged into Surya detection on the hybrid path (`OMNISCRIBE_WHITESPACE_RECALL` kill switch, INFO run summary) |
| `src/omniscribe/core/recall/text_layer.py` | Text-layer recall source — lines Surya missed recovered from a digital PDF's embedded text layer; second box source merged after the whitespace booster (`OMNISCRIBE_TEXT_LAYER_RECALL` kill switch, INFO run summary, no-op for scans/images); also exposes `token_agreement` + `PdfTextLayerRecall.page_text` used by the repair phase's fluent-hallucination trigger |
| `src/omniscribe/core/ocr/` | OpenAI/Anthropic/Ollama multi-format VLM client, prompts, response filters, limits, exceptions, retry, and circuit-breaker resilience; `__init__.py` preserves the public import surface |
| `src/omniscribe/core/ocr_quality/` | OCR Quality Trust Layer — watermark detection, script detection, hallucination guard, Platt scaling calibration fit/eval, trust scorer, orchestrator, `summary.py` (document-level `X-Document-Trust` header payload), and the `events.py` structured log channel (sub-modules incl. `parsers` drop events) |
| `src/omniscribe/core/transcription/` | Speech-to-text audio transcription engines (local Whisper & OpenAI-compatible API backends); `logprob_to_confidence` keeps per-segment confidence in `[0,1]`, the local engine runs whisper with beam/VAD/temperature-fallback robustness kwargs, and the API engine reuses one HTTP client with exponential backoff + `Retry-After` support |
| `src/omniscribe/core/lexicon/` | LanceDB-backed canonical glossary / translation lexicon store (Protocol + LanceDB implementation + embedding wrapper + helper queries + one-shot migration core). `schema.py` implements fail-closed table open and compatibility checks (`open_terms_table`, `ensure_meta_and_compat`) preventing destructive overwrites. Migration operations are documented in `docs/DEPLOYMENT.md`. |
| `src/omniscribe/core/glossary_sources/` | Terminology import parsers for XLIFF (1.2 / 2.0), TBX, TMX, CSV, TSV, JSON pairs, SQL tables, and Git repositories with encoding auto-detection (BOMs, UTF-8/16/32, Windows-1252, and ISO-8859-1 fallbacks). `lanes_lexicon.py` provides recursive in-order XML document traversal and Arabic headword prioritization for Lane's Lexicon (XML and SQLite). |
| `src/omniscribe/cli/import_lanes_lexicon.py` | CLI utility for importing Lane's Arabic-English Lexicon (XML/TEI or SQLite) into LanceDB lexicon tables |
| `src/omniscribe/core/writers/__init__.py` | Package-level re-exports for `MarkdownWriter`, `MarkdownExporter` (backward compatibility alias with deprecation notice), `export_json`, `convert_tree_to_docx`, and writer interfaces |
| `src/omniscribe/core/writers/markdown.py` | `MarkdownWriter` & `MarkdownExporter` — RAG-ready markdown rendering from `DocumentTree` and `DocumentResult` (headings with hierarchy levels, GFM pipe tables, equation blocks/inlines, figure caption/bbox references, page break markers `<!-- PageBreak: <page_idx> -->`, text normalization) |
| `src/omniscribe/core/writers/tree_json.py` | Hierarchical block-tree export builder |
| `src/omniscribe/core/writers/exporter_base.py` | `BaseDocumentExporter` ABC. **Implementations are co-located with the writers they wrap** (DOCX in `core/writers/docx.py`, tree-DOCX in `core/writers/docx_tree.py`, HTML in `core/writers/html.py`) — the module ships only the abstraction, not the exporters. To add a new format, subclass `BaseDocumentExporter` in the same file as the existing writer, then register it on `PDFHandler` (or the relevant writer) |
| `src/omniscribe/core/writers/docx_tree.py` | Hierarchical block-tree to `.docx` converter |
| `src/omniscribe/core/writers/html.py` | Semantic HTML document writer from `DocumentResult` |
| `src/omniscribe/core/chunking/` | RAG-ready semantic chunking sub-package: `taxonomy.py` (`RAGElementCategory` deterministic element mapping), `chunker.py` (`DocumentChunk` dataclass with boundary hierarchy, atomic table preservation, intra-section overlap, and provenance metadata; `SectionAwareChunker`), `__init__.py` |
| `src/omniscribe/core/block_tree.py` | Hierarchical block-tree data structure and tree nodes; `BlockNode` serializes `trust_score`/`trust_flags` so the block-tree JSON carries trust-layer output |
| `src/omniscribe/core/pdf/__init__.py` | Package re-exports for `PDFHandler`, `DocumentResultWriter`, `IMAGE_EXTENSIONS`, `_emit_pymupdf_agpl_notice`, and public PDF symbols |
| `src/omniscribe/core/pdf/rasterizer.py` | PyMuPDF AGPL warning emission, safe DPI calculation, image extension validation, and PDF/image rasterization to JPEG/PNG base64 |
| `src/omniscribe/core/pdf/embedder.py` | Invisible text layer PDF rendering over rasterized backgrounds, normalized bbox coordinate transformations, and font sizing calculation |
| `src/omniscribe/core/pdf/handler.py` | `PDFHandler` class facade implementing `DocumentResultWriter` protocol for high-level workflow orchestration |
| `src/omniscribe/core/pdf/page_range.py` | Parse and serialize validated page selections shared by the HTTP and workflow boundaries |
| `src/omniscribe/core/pdf/rasterization_settings.py` | Typed rasterization settings and bounded DPI/image-size policy |
| `src/omniscribe/core/pdf/embedder_helpers.py` | Small font, glyph, and logging helpers used by the PDF embedder |
| `src/omniscribe/core/grounded/` | Grounded OCR models, prompted backend, rasterization, and bbox-native response parsers; `__init__.py` preserves the public import surface |
| `src/omniscribe/core/postprocess.py` | Dictionary-based spellcheck post-processing |
| `src/omniscribe/core/imaging/page_preprocess.py` | Local hybrid-path page preprocessing (orientation detection, deskew, denoise, contrast normalization, crop cleanup) |
| `src/omniscribe/core/imaging/handwriting.py` | Local handwriting image preprocessor for specialized handwriting pipeline paths |
| `src/omniscribe/core/ocr_quality/routing.py` | Quality routing recommendation metadata and policy recorder |
| `src/omniscribe/core/evaluation.py` | Lightweight `EvaluationMetrics` dataclass and `evaluate_document` helper for in-process processor result scoring |
| `src/omniscribe/core/lexicon/schema.py` | LanceDB PyArrow schema definitions, dataset versioning, metadata table compatibility, and corrupted table self-healing recreation |
| `src/omniscribe/core/writers/docx.py` | Markdown → `.docx` converter used by the docx export route with native GFM pipe table rendering, cell alignment, and heading hierarchy |
| `src/omniscribe/core/translate/config.py` | Core-owned typed settings (judge toggle, lexicon result count / min score, max tokens, entity-memory cap) and optional-feature errors for async translation |
| `src/omniscribe/core/translate/workflow.py` | Optional LangGraph translation workflow — retrieve → translate → evaluate loop with LLM-as-judge, best-attempt tracking, deterministic adequacy checks (URL/acronym/number preservation), script-aware length bands, and fail-safe acceptance on unparseable judge output |
| `src/omniscribe/core/workflows/base.py` | `EngineBase`, `OutputWriter`, `ProgressCallback`, `WarningCallback` shared by both engines |
| `src/omniscribe/core/workflows/hybrid.py` | `HybridEngine` — orchestrator delegating to specialized workflow stages |
| `src/omniscribe/core/workflows/hybrid_repair.py` | Hybrid-engine adapter for the shared quality-repair loop and block re-OCR callbacks |
| `src/omniscribe/core/workflows/stages/` | Decomposed hybrid workflow stages: `conversion.py` (`HybridConverter`), `layout.py` (`HybridLayoutDetector`), `ocr.py` (`HybridOcrRunner`), `refine.py` (`HybridRefiner`) |
| `src/omniscribe/core/workflows/grounded.py` | `GroundedEngine` — single bbox-native VLM call → post-process → processors → output |
| `src/omniscribe/core/workflows/repair.py` | `QualityRepairLoop` and `RepairOptions` — engine-agnostic block-level low-confidence re-OCR (stall guard, fail-open, `CircuitOpenError` re-raise, informed `re_ocr` contract passing `previous_text`/`attempt`, text-layer agreement trigger for fluent hallucinations) plus the job-level `quality_summary` aggregator |
| `src/omniscribe/core/workflows/utils.py` | Stand-alone workflow helper functions (`parse_page_range`, `_estimate_confidence`, `_decode_page_image`, `_normalize_for_dedup`, `_drop_refined_duplicates`, `_is_refinable`) and workflow constants (`REFINABLE_MIN_WIDTH`, `REFINABLE_MIN_HEIGHT`, `DETECT_CHUNK_SIZE`) |
| `src/omniscribe/core/workflows/__init__.py` | Re-exports `EngineBase`, `HybridEngine`, `GroundedEngine`, public helper `parse_page_range`, constants, and callback type aliases |
| `src/omniscribe/resources/dictionaries/` | Packaged compiled spellcheck dictionaries loaded before legacy repository-root dictionaries |
| `src/omniscribe/resources/calibration/` | Pre-trained model confidence calibration files (e.g. `qwen2_5_vl_72b.json`) |
| `src/omniscribe/core/ocr/multi_format_client.py` | Multi-format LLM completion dispatcher (`openai_compatible`, `anthropic_compatible`, `ollama_compatible`), vision base64 payloads, exponential backoff resilience retries, and timeout boundaries |
| `src/omniscribe/harness/` | Cordis-style plugin harness: `context.py` (Protocol-keyed services, LIFO effects, event bus, router queue, duplicate protection, and non-shadowing rollback), `loader.py` (YAML tree + patches + env overrides, fails loud), `plugin.py` (Plugin base), plus `errors.py` (hierarchical domain exceptions: `DuplicateServiceError`, `DuplicatePluginError`, `ContextDisposedError`, `ServiceNotFoundError`, `PluginLoadError`), `events.py`, `effects.py`, `config.py` |
| `src/omniscribe/plugins/` | The fourteen boot plugins (runtime, logging, state_backend, artifacts, jobs, progress, providers, health, documents, translate, transcribe, glossary, ocr, sample_pdfs) that register services and mount every `/api` router; see the Plugin Tree section |
| `src/omniscribe/plugins/_http.py` | Shared capability-token extraction from query, headers, and Bearer authentication |
| `src/omniscribe/plugins/_schemas.py` | Small schema helpers shared across plugin request and response models |
| `src/omniscribe/plugins/errors.py` | Plugin-layer domain errors and HTTP-safe failure mapping |
| `src/omniscribe/plugins/state_backend_types.py` | Isolated state backend domain dataclasses (ArtifactBlob, ChannelRecord, JobRecord, ArtifactRecord) and StateBackend protocol |
| `src/omniscribe/plugins/state_backend.py` | Select and register the configured state backend |
| `src/omniscribe/plugins/state_backend_memory.py` | Ephemeral in-process state backend for tests and disposable runs |
| `src/omniscribe/plugins/state_backend_sqlite.py` | Durable single-node SQLite state backend and blob persistence |
| `src/omniscribe/plugins/state_backend_redis.py` | Distributed Redis state backend with TTL-aware artifacts and channels |
| `src/omniscribe/plugins/providers_service.py` | Provider discovery, validation, credential binding, and persisted active-provider settings |
| `src/omniscribe/plugins/documents/` | Documents plugin: `schemas.py` (extraction/export request models reproducing the pre-harness contract, plus `ExportMarkdownRequest`, `ExportChunksRequest`, `DocumentChunkPayload`, `ExportChunksResponse`), `prompts.py` (extraction prompts re-homed verbatim from the pre-harness `api/services/ai.py`; `PROMPT_VERSION 2026-08-15.v1`; invoice/resume/academic/table/table_extraction/custom templates), `service.py` (LLM extraction runner, text/markdown/json/docling-compatible/mineru-compatible export builders, `build_markdown_export`, `build_chunks_export`, and on-demand block-tree building from the stored text artifact — no tree sidecars), `routes.py` (`POST /api/extract`, `POST /api/export/document`, `GET|POST /api/export/docx`, `POST /api/export/html`, `POST /api/export/docx-tree`, `POST /api/export/blocktree`, `GET|POST /api/export/markdown`, `GET|POST /api/export/chunks`, token-bound `GET /api/export/{artifact_id}`, `GET /api/text/{artifact_id}`, `GET /api/metadata/{artifact_id}`), and `plugin.py` (mounts the router; no configurable fields) |
| `src/omniscribe/plugins/translate/` | Translate plugin: `schemas.py` (translation request models + client response contracts), `service.py` (`TranslationService` — sync single-shot `translate_text` re-home with service-level judge loop and LRU translation cache, JobQueue runner (`TranslationJobRunner` seam) that walks the stored text artifact's tree with `translate_tree`, and client status mapping PENDING/PROGRESS/SUCCESS/FAILURE), `routes.py` (`POST /api/translate`, `POST /api/translate/async`, `GET /api/translate/status/{job_id}`, `GET /api/translate/result/{job_id}` token-redeeming fetch, `POST /api/translate/nllb`), and `plugin.py` (mounts the router; no configurable fields) |
| `src/omniscribe/plugins/transcribe/` | Transcribe plugin: `schemas.py` (form-field and config request models + client response contracts), `service.py` (`TranscriptionService` — sync multipart transcription through the core transcription engines; transcript and metadata serialized JSON using the text-artifact convention and stored as token-bound artifacts), `config_store.py` (always-writable in-memory transcription config store with masked keys; SSRF-guarded endpoint model discovery falling back to the canned whisper list), `routes.py` (`POST /api/transcribe`, `GET|POST /api/config/transcription`, `GET /api/models/transcription`), and `plugin.py` (mounts the router; no configurable fields) |
| `src/omniscribe/plugins/glossary/` | Glossary plugin: `schemas.py` (import/library request models covering dual-shape imports and toggle bodies), `service.py` (`GlossaryImportService` — parse → entry-count estimate → sync import or JobQueue dispatch above the 5,000-entry estimate, plus `ensure_store_ready`, library/sources CRUD, toggle flipping, reorder, and query-filtered/paginated entries), `store.py` (lazy `LexiconStore` provider — routes 503 with the `uv sync --extra lexicon` install hint when the `lexicon` extra is missing), `http_fetch.py` (SSRF-guarded, IP-pinned URL fetch with manual redirect following for `/api/glossary/import/url`), `routes.py` (the /api/glossary* routes; dual-shape imports, sources CRUD, toggle, entries search/pagination, preview/merged), and `plugin.py` (mounts the router; registers `GlossaryJobRunner`) |
| `src/omniscribe/plugins/sample_pdfs.py` | `SamplePdfsPlugin`: mounts allowlisted `GET /api/sample-pdf/{name}` test fixture distribution to verify fresh installs without authentication |
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
| `src/omniscribe/utils/env.py` | Typed environment-variable access helpers and robust atomic key persistence (`persist_env_key`) to `.env` |
| `src/omniscribe/static/` | Static asset directory served by FastAPI |
| `scripts/` | Repo-root developer utilities: confidence eval, fixture builder, debug/inspection scripts, bbox visualizers |
| `examples/` | Sample PDFs and images used by `tests/` and the confidence scripts |
| `tests/` | Unit, integration, security, and slow-path validation |
| `tests/middleware/test_rate_limit.py` | Unit tests for `RateLimitMiddleware` (limits, window expiration, IP isolation, exemptions) |
| `tests/middleware/test_upload_limit.py` | Unit tests for `UploadSizeLimitMiddleware` (Content-Length limits, streaming chunk accumulation, exemptions, 413 responses) |
| `tests/utils/test_json_parse.py` | Unit tests for `extract_json` utility |
| `tests/utils/test_env.py` | Unit and boundary tests for typed environment variable parsing and `.env` file persistence (`persist_env_key`) |
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
| `src/omniscribe/plugins/ocr/routes.py` | FastAPI route definitions, multipart upload parsing, full-content-addressed preview caching, and HTTP endpoint handlers for the OCR plugin (`POST /api/process`, `POST /api/process/async`, job status/listing/cancelling/deletion, SSE event streaming, config GET/POST, preflight, and page previews) |
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
| `tests/plugins/test_jobs_chaos.py` | Q11 chaos and fault-injection test suite for JobQueue and worker lifecycle |
| `tests/plugins/test_ocr_preflight.py` | Unit and route tests for OCR model pre-flight verification endpoint |
| `src/omniscribe/core/readers/` | Digital-document ingest subsystem (RFC 004 R2 Fast Path) |
| `src/omniscribe/core/readers/base.py` | Abstract BaseDocumentReader protocol, ReaderError domain exceptions, and synthetic DocumentBlock/DocumentPage builders |
| `src/omniscribe/core/readers/docx_reader.py` | Word (.docx) document reader preserving headings, tables, lists, and runs into DocumentResult and DocumentTree |
| `src/omniscribe/core/readers/html_reader.py` | HTML reader parsing headings, paragraphs, lists, tables, and code with selectolax and standard library fallback |
| `src/omniscribe/core/readers/markdown_reader.py` | Markdown reader parsing headings, paragraphs, tables, lists, blockquotes, and code fences into DocumentResult |
| `src/omniscribe/core/readers/dispatch.py` | Reader registry and factory mapping file extensions (.docx, .html, .htm, .md, .markdown) |
| `src/omniscribe/core/readers/pdf_renderer.py` | PyMuPDF synthetic PDF renderer creating searchable PDF output from DocumentResult with Unicode fast-path font selection (Arabic, CJK, Hebrew, Cyrillic) |
| `src/omniscribe/plugins/jobs_redis.py` | `RedisJobQueue` distributed job queue implementing `JobQueue` protocol with owner-bound atomic claims, renewable visibility leases, atomic lease-checked requeue Lua script, OCR payload paths checked against the claimed job's persisted input path, stale-lease-safe recovery/finish, worker heartbeats, and Redis Pub/Sub integration (RFC 004 R3) |
| `src/omniscribe/worker.py` | Standalone `omniscribe-worker` multi-worker process dispatching OCR, Translation, and Glossary jobs from Redis with terminal StateBackend persistence before lease acknowledgement, graceful shutdown, owner-bound lease renewal, and heartbeat monitoring (RFC 004 R3) |
| `scripts/migrate_sqlite_to_redis.py` | Standalone migration utility transferring jobs, artifacts, and channels from SQLite state database to Redis (RFC 003 / RFC 004) |
| `tests/core/chunking/test_chunker_props.py` | Hypothesis property-based tests for `SectionAwareChunker` invariants, element boundaries, table preservation, chunk sizing |
| `tests/core/processors/test_table_fallback.py` | Unit and heuristic tests for `TableFallbackProcessor` grid reconstruction, row/col detection, and fail-open table repair |
| `tests/core/readers/test_readers.py` | Unit tests for DOCX, HTML, Markdown readers, dispatch registry, and synthetic PDF renderer |
| `tests/core/test_rasterizer.py` | Unit tests for PyMuPDF serial page rasterization and sequential order preservation |
| `tests/core/glossary_sources/test_lanes_lexicon.py` | Unit tests for Lane's Lexicon XML and SQLite source parsers, mixed-content text ordering, and Arabic headword prioritization |
| `tests/core/test_lexicon_schema.py` | Unit tests for LanceDB lexicon schema fail-closed table open and compatibility checks |
| `tests/test_security.py` | Unit tests for URL origin validation, malformed port resilience, and security utility functions |
| `tests/core/writers/test_markdown_writer.py` | Unit tests for `MarkdownWriter` rendering, heading hierarchy, pipe tables, math fences, page-break comments |
| `tests/plugins/test_digital_ingest_fastpath.py` | End-to-end fast path integration tests asserting zero Surya/VLM calls and DOCX text parity |
| `tests/plugins/test_jobs_redis.py` | Unit, race condition, visibility recovery, retry exhaustion, heartbeat, pubsub fanout, and runner integration tests for `RedisJobQueue` |
| `tests/plugins/test_jobs_redis_worker_security.py` | Unit tests for Redis spool path containment, atomic lease-checked requeue, and worker terminal persistence order contracts |
| `tests/plugins/test_ocr_service_prune.py` | Unit tests for OCR service event buffer eviction, prune loop consolidation, and SSE event backlog bounding |
| `tests/plugins/test_ocr_block_callbacks.py` | Unit and integration tests for OCR block/page callbacks, WebSocket frame schema compliance, pipeline integration, and digital reader fast path emission |
| `tests/routers/test_export_markdown_chunks.py` | FastAPI route tests for `/api/export/markdown` and `/api/export/chunks` endpoints |
| `tests/scripts/test_benchmark_scoring.py` | Unit tests for `scripts/confidence_eval.py --score-markdown` metrics (CER, WER, BLEU, chrF, heading F1, table similarity) |
| `tests/scripts/test_migrate_sqlite_to_redis.py` | Unit and integration tests for Profile 4 SQLite-to-Redis migration utility |
| `docs/rfcs/2026-09-end-user-install.md` | RFC 001 evaluating end-user distribution architectures (Option A PyInstaller bundle, Option B Flutter desktop embed, Option C standalone CLI) |
| `docs/rfcs/2026-09-bundle-sprint-1-findings.md` | RFC 002 Sprint 1 root-cause analysis and PyInstaller packaging findings for the Windows single-file bundle |
| `docs/rfcs/2026-09-redis-state-backend.md` | RFC 003 design and operational specification for the Redis distributed state backend and multi-worker deployment |
| `docs/rfcs/2026-09-competitive-gap-remediation.md` | RFC 004 strategic plan closing the competitive gap against Docling/Unstructured (R1–R5 workstreams) |
| `docs/benchmarks.md` | External comparative benchmark documentation (OmniDocBench, Docling, Marker, Unstructured, Nougat) with evaluation protocol and reproduction commands |
| `docs/deployment/windows-bundle.md` | Operator and user guide for running the standalone Windows PyInstaller bundle |
| `docs/TROUBLESHOOTING.md` | Central first-run troubleshooting guide covering top 10 failure modes, cross-linked from `make doctor` and README |
| `scripts/verify_live_functionality.py` | Live non-mocked functional verification harness validating FastAPI runtime lifecycle, HTTP status envelopes, rich DOCX export with native tables, artifact previews, and WebSocket connectivity |
| `tests/core/ocr/test_llm_balance_error.py` | Unit tests validating `LLMBalanceError` status code mapping (402 Payment Required) and error propagation across VLM client tiers |
| `tests/core/workflows/test_llm_balance_propagation.py` | Integration tests verifying `LLMBalanceError` propagation through GroundedEngine, HybridEngine, and OCR workflow stages |
| `client/test/data/job_orchestration_notifier_test.dart` | Unit tests for job orchestration notifier, cancellation, retry bounding, and terminal status handling |
| `client/test/data/workstation_hydration_test.dart` | Unit tests for workstation notifier artifact hydration preserving dimensions, preview images, and page indexing |
| `client/test/data/workstation_notifier_test.dart` | Unit tests for workstation notifier state transitions and page updates |
| `client/test/presentation/settings_screen_test.dart` | Widget tests for settings screen server URL error notifications |

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
- PyMuPDF rendering and embedding are serialized per document because sharing
  one document across worker threads can corrupt native state. Independent
  jobs can still run concurrently in separate workers.

## Shared State and Artifacts

All persistent and process-local state flows through the `StateBackend`
service registered by the `state_backend` plugin — no router touches a
module singleton. Three backends ship: `SQLiteStateBackend` (the durable
default), `MemoryStateBackend` (ephemeral), and `RedisStateBackend`
(`OMNISCRIBE_STATE_BACKEND=redis`) for distributed workers. The backend
covers three domains: artifacts, jobs, and progress channels.

The `artifacts` plugin layers an `ArtifactStore` on top: every artifact is
an opaque id + bearer token pair; sync `/api/process` returns them as
`X-Text-Artifact-Id` / `X-Text-Artifact-Token` headers. Async submission
returns the result capability token once; status and SSE payloads expose
artifact ids but never repeat that secret. The `documents` plugin
serves the metadata/export artifact surfaces on the same store:
`POST /api/export/document` writes a new token-bound export artifact, and
`GET /api/text/{id}` / `GET /api/metadata/{id}` / `GET /api/export/{id}`
fetch stored ones. The `translate` plugin writes translated text to the
same token-bound store; its async status summary carries the artifact ids
and never the bearer tokens.

### Background OCR lifecycle

`POST /api/process/async` validates and persists the upload before submitting
a payload to the configured `JobQueue`. In-process mode runs a single worker
from `plugins/jobs.py`; Redis mode uses `plugins/jobs_redis.py` plus one or
more `omniscribe-worker` processes. Observable HTTP
states are `pending`, `processing`, `complete`, `error`, and `cancelled`; status is
available at `GET /api/process/status/{job_id}` and as an SSE replay at
`GET /api/process/{job_id}/events`. `POST /api/jobs/{job_id}/cancel` removes a
pending job or marks an in-flight job as `cancelled` without
letting the runner's eventual return overwrite the cancellation. With the
memory backend queue and artifact indexes are lost on restart; SQLite persists
state for one server process, while Redis coordinates multiple workers.

#### Concurrency, Spool Safety & Lifecycle Invariants

1. **Spool Path Job Binding & Safe Deletion Lifecycle**: OCR job payloads deserialized from Redis require a non-empty `input_path` beneath a trusted spool root (`tempfile.gettempdir()`, `OMNISCRIBE_SPOOL_DIR`, or `OMNISCRIBE_ARTIFACT_DIR`). Both claim paths compare the payload job ID and resolved input path with the claimed job's persisted `JobRecord`; a forged payload fails terminally and cleanup uses only the recorded path. `InMemoryJobQueue` and `RedisJobQueue` purge staged spool uploads on queued cancellation. Working directory creation and cleanup in `ocr/service.py` use `work_dir = spool_dir / f"omniscribe-ocr-{id}"` and `_is_safe_ocr_work_dir` to confine recursive deletion, while preserving `work_dir` on `asyncio.CancelledError` so requeued jobs during worker drain maintain valid input files.
2. **Worker Terminal Persistence Ordering Contract**: In `worker.py`, the terminal `JobRecord` (`complete`, `error`, or `cancelled`) is persisted in `StateBackend` *before* the worker releases or acknowledges the queue lease (`queue.complete`, `queue.fail`). Completion checks verify that jobs are not already in terminal status, preventing worker race conditions from overwriting user cancellations with `complete`.
3. **Atomic Lease-Checked Requeue**: During graceful worker drain (`SIGINT`/`SIGTERM`), in-flight jobs requeued to Redis pass `lease_owner` to `RedisJobQueue.requeue`. Ownership is atomically verified in Redis via Lua script (`_REQUEUE_JOB_LUA`) and WATCH before moving jobs from `omniscribe:jobs:active` to `omniscribe:jobs:queue`.
4. **Cross-Pod Redis Pub/Sub Terminal Event Relay & SSE Unblock**: In distributed Redis mode, workers broadcast terminal frames (`complete`, `failed`, `cancelled`) over `omniscribe:progress:{job_id}` and `progress_channel`. `ProgressServiceImpl._run_redis_pubsub()` ingests these frames and re-emits corresponding terminal events (`JobCompleted`, `JobFailed`, `JobCancelled`) into the local `Context` bus (deduplicated against local worker emissions via `_service_id` and tracking sets). This guarantees that distributed API processes unblock SSE event loops (`/api/process/{job_id}/events` via `OCRService.wait_for_events`), which is additionally hardened against deadlocks by checking terminal status in `StateBackend` upfront/post-wait and bounding wait intervals to 2.0s.
5. **Lexicon Fail-Closed Error Policy**: `open_terms_table` and `ensure_meta_and_compat` in `core.lexicon.schema` fail closed by raising exceptions on table read/open errors instead of destructively recreating or overwriting existing lexicon databases with `mode="overwrite"`.
6. **Client Bounded Polling & Artifact Hydration**: The Flutter client (`job_orchestration_notifier.dart`) implements bounded retry tracking (`_maxConsecutiveStatusFailures = 3`) before declaring status check failures, guards against stale artifact hydration overwrites (`_isCurrentRun`), preserves existing `PageResult` previews, dimensions, and image URLs without collapsing sparse page indices, and unconditionally clears processing state upon cancellation even when server-side cancel endpoints fail.
7. **Unicode Fast-Path & Zero-Page PDF Embedding**: PyMuPDF embedding and synthetic document rendering automatically resolve Unicode font chains (supporting Arabic, CJK, Hebrew, and Cyrillic) and inject default blank pages for empty page collections, preventing WinAnsi encoding degradation and zero-page PDF fatal crashes.
8. **SSRF IP-Pinned Transports & WebSocket Bearer Auth**: Outbound LLM API calls in extraction and translation bind HTTP transports directly to pre-resolved, SSRF-validated IP addresses to prevent DNS rebinding TOCTOU attacks. In addition, the ASGI `BearerAuthMiddleware` gates both `http` and `websocket` connection scopes on protected server instances.

### Multi-producer job runner dispatch

The `JobQueue` supports multiple producer plugins (OCR,
translate, glossary) through runtime-checkable `JobPayload` protocol
conformance. Payloads tag their class with `runner_protocol = <RunnerProtocol>`.
At claim time, `InMemoryJobQueue._resolve_runner` inspects the payload: if it
conforms to `JobPayload` (or defines `runner_protocol`), its declared runner
protocol key is injected from the application `Context`; otherwise, it defaults
to injecting `JobRunner`.

### Authentication and runtime security

The live ASGI boundary provides bearer authentication, optional per-IP rate
limiting, and upload-size enforcement. Loopback use can run without an auth
token; non-loopback binds require `OMNISCRIBE_AUTH_TOKEN`. Health/readiness,
static assets, the root page, sample PDFs, and CORS preflight remain public.
Rate limiting is disabled unless `OMNISCRIBE_RATE_LIMIT_PER_MINUTE` is set
(the Compose profile sets 60). Artifact reads remain independently
capability-token-bound.

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
| `GET` / `POST` | `/api/process/preflight` | `ocr` | Verify model availability and connectivity against configured LLM/VLM backend before processing |
| `GET` | `/api/process/status/{job_id}` | `ocr` | Background OCR lifecycle status |
| `GET` | `/api/process/{job_id}/events` | `ocr` | SSE replay of the job's lifecycle events |
| `GET` / `DELETE` | `/api/jobs` | `ocr` | Job list; `DELETE` clears terminal jobs while preserving queued/running work |
| `GET` | `/api/jobs/{job_id}/result` | `ocr` | Token-bound result PDF download |
| `POST` | `/api/jobs/{job_id}/cancel` | `ocr` | Cancel pending/running job; terminal jobs are idempotent |
| `POST` | `/api/extract` | `documents` | Structured data extraction against OCR text; templates `invoice`, `resume`, `academic`, `table`, `table_extraction`, or `custom` prompt |
| `POST` | `/api/export/document` | `documents` | Build a token-bound export artifact (`text`/`markdown`/`json`/`docling`/`mineru`); returns `{artifact_id, token, format}` |
| `GET` / `POST` | `/api/export/docx` | `documents` | `.docx` from Markdown page text; the GET form takes `?text=` (Flutter ExportModal) |
| `POST` | `/api/export/html` | `documents` | Semantic HTML built from the stored text artifact's block tree |
| `POST` | `/api/export/docx-tree` | `documents` | `.docx` built from the stored text artifact's block tree |
| `POST` | `/api/export/blocktree` | `documents` | Hierarchical block-tree JSON built from the stored text artifact |
| `GET` / `POST` | `/api/export/markdown` | `documents` | Render clean GFM Markdown; GET takes the text token in `X-Artifact-Token` or `Authorization: Bearer <token>` and an optional metadata token in `X-Metadata-Artifact-Token`; POST takes tokens in its JSON body |
| `GET` / `POST` | `/api/export/chunks` | `documents` | Provenance-preserving section-aware RAG chunks; GET uses the same token headers and POST uses JSON body tokens |
| `GET` | `/api/export/{artifact_id}` | `documents` | Token-bound (Bearer) export artifact download |
| `GET` | `/api/text/{artifact_id}` | `documents` | Token-bound (Bearer) OCR text artifact fetch |
| `GET` | `/api/metadata/{artifact_id}` | `documents` | Token-bound (Bearer) document metadata artifact fetch |
| `POST` | `/api/translate` | `translate` | Synchronous single-shot translation; returns `{translated_text}` |
| `POST` | `/api/translate/async` | `translate` | Tree-aware translation dispatched on the harness JobQueue; translated text stored as a token-bound artifact |
| `GET` / `POST` | `/api/translate/status/{job_id}` | `translate` | Client status vocabulary (`PENDING`/`PROGRESS`/`SUCCESS`/`FAILURE`); the result summary references artifact ids, never tokens |
| `POST` | `/api/translate/nllb` | `translate` | Local NLLB translation (lazy module-level engine); 503 when the `nllb` extra is missing |
| `GET` | `/api/translate/result/{job_id}` | `translate` | Token-redeeming async result fetch (`?token=…`); wrong token → uniform 404 |
| `POST` | `/api/transcribe` | `transcribe` | Synchronous multipart transcription; token-bound text + metadata artifacts |
| `GET` / `POST` | `/api/config/transcription` | `transcribe` | Transcription config store; masked keys, always writable |
| `GET` | `/api/models/transcription` | `transcribe` | Endpoint model discovery; SSRF guard + whisper fallback list |
| `POST` | `/api/glossary/import` | `glossary` | Dual-shape import: legacy JSON source envelope or the client's multipart upload; formats: CSV, TSV, TMX, TBX, XLIFF, Git glossary, SQL table, JSON pairs, Lane's Lexicon SQLite (`lanes_sqlite`), Lane's Lexicon XML (`lanes_xml`); above the 5,000-entry estimate dispatches on the JobQueue |
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
| `WS` | `/ws/{channel_id}`, `/api/progress/ws/{channel_id}` | `progress` | Token-bound progress stream with Origin validation; bearer auth via query (`?auth_token=` or `?token=`), `Authorization: Bearer` header, or first `{"type":"auth",...}` frame, then accepts `{"type":"cancel"}` (unauthorized handshakes close with 4401) |

Deferred in the harness rebuild (routes not mounted): the remaining
`/api/models*` discovery aliases (the transcribe plugin ships
`GET /api/models/transcription`) and
provider mutation routes (`POST/DELETE /api/providers*`) — see the design
spec's out-of-scope list.

## Documentation Maintenance

This ledger describes the current system only. Release history belongs in
[CHANGELOG.md](CHANGELOG.md), the active backlog in
[outstanding-work.md](outstanding-work.md), and completed audit details in Git history.

## See Also

- [README.md](../README.md) — feature overview, install, and client workflow
- [CHANGELOG.md](CHANGELOG.md) — version history and breaking changes
- [DEPLOYMENT.md](DEPLOYMENT.md) — local / LAN / public-internet deployment profiles
- [SECURITY.md](SECURITY.md) — threat model, hardening checklist, vulnerability disclosure
- [AGENTS.md](AGENTS.md) — contributor guide and full env-var reference
- [outstanding-work.md](outstanding-work.md) — canonical open backlog
- [COMPREHENSIVE-AUDIT.md](audits/COMPREHENSIVE-AUDIT.md) — historical audit snapshot; detailed predecessor reports remain in Git history

_Last updated: 2026-09-27_
