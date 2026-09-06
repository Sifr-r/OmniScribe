# AGENTS.md

This file tells coding agents and contributors how to work with this repository.

## Quick Start

```bash
uv sync
uv sync --extra web --extra preprocessing --extra async-translation --extra lexicon
uv run omniscribe-server --port 8000
```

Real OCR requires an OpenAI-compatible VLM endpoint. The default is LM Studio at `http://localhost:1234/v1`.

## Validation

**After any code change, run the relevant subset of these checks before claiming completion.**

```bash
# Fast gate — run after every material edit:
uv run ruff check src tests
uv run ruff format src tests --check
uv run mypy src
uv run pytest -m "not slow"

# Full gate — run before merge / PR:
uv run pytest
uv run pytest -m slow
uv run pytest tests/core/test_aligner.py -v
```

- `pytest-asyncio` uses auto mode. Write `async def test_...` without decorators.
- Slow tests load Surya and may download its model on the first run.
- Markers are `slow` and `slow_dataset`:
  - `slow` — loads the Surya detection predictor (~5s first run, ~500 MB model weight).
  - `slow_dataset` — exercises the full OCR-Quality / KIE-HVQA regression fixtures (`tests/fixtures/datasets/ocr_quality_full.json`, `kie_hvqa_full.json`); only meaningful once `scripts/fetch_datasets.py` has the upstream license review cleared and downloads the real datasets. Today the marker is a no-op skip (the fixtures don't ship); the marker exists so the next test author can land tests that need the full data without remembering the right `xfail` shape.
- Pre-commit hooks run ruff (check + format) and mypy automatically on every commit. Install with `uv tool run pre-commit install`.

### Sprint 5 / C-3 audit fix: required CI checks for merge

The fast-tier workflow (`.github/workflows/test.yml`) runs on every
PR and push to main. The matrix jobs that **must** be green before
merge (per audit C-3):

| Job | Runner | Python | Purpose |
| --- | --- | --- | --- |
| `fast (ubuntu, 3.11)` | ubuntu-latest | 3.11 | Fast tier — lint + mypy + fast tests + coverage ≥ 80 % |
| `fast (ubuntu, 3.13)` | ubuntu-latest | 3.13 | Fast tier on Ubuntu (Python 3.13) |
| `fast (windows, 3.11)` | windows-latest | 3.11 | Windows runner path |
| `client-tests (flutter)` | ubuntu-latest | n/a | Flutter client static analysis (`--fatal-infos`) and unit/widget test suite |
| `container scan (trivy)` | ubuntu-latest | n/a | High/critical CVE scan of the production image |

The nightly workflow (`.github/workflows/nightly.yml`) is **not** a
merge gate — it adds 3.12 to the slow tier and runs the OCR-Quality
calibration regression. The drift is intentional and documented in
the workflow comments; the fast tier is 3.11 + 3.13 to keep the
PR matrix from quadrupling in size.

A `Codecov` and `Trivy SARIF` upload are informational; the inline
`--cov-fail-under=80` (Sprint 4 / C-1) is the authoritative coverage
gate.

## Core Paths

Source directories are split into **core** (OCR pipeline and API surface) and **peripheral** (tooling, utilities). Changes to core paths require the full fast gate; peripheral-only changes can skip some checks.

### Core — full fast gate required

| Path | Scope |
| --- | --- |
| `src/omniscribe/core/` | OCR engines, alignment, PDF/image handling, document model, workflows, processors, translation, grounded backends, OCR quality trust layer |
| `src/omniscribe/harness/` | Cordis-style plugin harness: Context (services/events/effects/router queue), Loader (YAML + patches + env overrides), Plugin base |
| `src/omniscribe/plugins/` | The thirteen boot plugins (runtime, logging, state_backend, artifacts, jobs, progress, providers, health, documents, translate, transcribe, glossary, ocr) and their Protocol seams |
| `src/omniscribe/pipeline.py` | `OCRPipeline` facade |
| `src/omniscribe/server.py` | FastAPI app entry point |
| `src/omniscribe/config.py` | Runtime settings |

```bash
# Required for every core-path change:
uv run ruff check src tests
uv run ruff format src tests --check
uv run mypy src
uv run pytest -m "not slow"
```

If the change touches `core/aligner.py`, `core/workflows/`, or `core/ocr/`, also run `uv run pytest tests/core/test_aligner.py tests/core/ocr/test_ocr.py -v`.

### Peripheral — focused validation

| Path | Scope | Validation |
| --- | --- | --- |
| `src/omniscribe/utils/` | Shared helpers, SSRF guard | `ruff check src` + `mypy src` |
| `scripts/` | Developer CLI utilities | `ruff check scripts` + relevant `pytest tests/scripts/test_scripts_smoke.py` |
| `tests/` (new tests only) | Test additions | `ruff check tests` + `pytest <new_test_file> -v` |
| `AGENTS.md`, `README.md`, `CHANGELOG.md` | Documentation | No code validation required |

### Decision rule

If a change touches **any** core path, run the full fast gate. If it is peripheral-only, run only the validation listed for that path above. When in doubt, run the full fast gate.

## Conventions

- Python 3.11 or newer. Use `uv`; do not install dependencies with `pip`.
- Prefer self-documenting code and docstrings. Add comments only when they clarify non-obvious behavior.
- Keep `tqdm_patch.apply()` before `from surya.detection import DetectionPredictor` in `core/aligner.py`.
- Keep bboxes normalized as `[x0, y0, x1, y1]` in `0..1` until `PDFHandler.embed_structured_text`.
- Treat image inputs as first-class inputs. PDF and image paths share the output writer.
- OmniScribe is Web UI/API-first. The user-facing `omniscribe` CLI script has been deprecated; do not add or restore it. `OCRPipeline` is still importable for in-process programmatic use (e.g. an embedded workflow), but no script entry is shipped.
  - **One exception:** `omniscribe-migrate-lexicon` (audit P11) is a
    deliberately-shipped one-shot migration tool that lives under
    `omniscribe.cli.migrate_lexicon:main` and is registered as a
    console script in `pyproject.toml`. It exists to migrate legacy
    ChromaDB-backed `glossary_library/library.json` +
    `chroma_db/lanes_lexicon` stores to the canonical LanceDB
    store; it is **not** a re-introduction of the deprecated
    `omniscribe` CLI, and the Web UI / API surface remains the
    supported user workflow. Run it once per legacy install with
    `uv run omniscribe-migrate-lexicon --dry-run` first; the full
    flag matrix (`--dry-run`, `--verify-only`, `--strict`,
    `--yes`) is in `DEPLOYMENT.md` §"Upgrading from a pre-LanceDB
    Glossary". After the migration the script is a no-op and
    should be considered deprecated alongside the underlying
    `chroma_db/` on-disk layout.
- Git credentials (audit S12) — never pass a GitHub PAT or other
  secret on the command line (`git clone https://user:token@…`,
  `git push https://…@…`, etc.); argv is visible in `ps` /
  `/proc/<pid>/cmdline` on every host the command touches. Use one of:
  1. `~/.netrc` with `machine github.com login <user> password <pat>`
     (chmod 600; `git` reads it automatically).
  2. `GIT_ASKPASS=<path-to-script>` plus a credential helper
     (`git config credential.helper store` for a session-scoped
     cache, or a system keychain helper).
  3. The `gh` CLI's built-in auth (`gh auth login` then `gh repo clone`).
  No current script in this repo shells out to `git`, but the convention
  applies to any future contribution.
- Keep local document processors selectable through web/API `document_processors`. Current names are `reading_order`, `quality_analysis`, `structure_analysis`, `section_analysis`, `layout_enrichment`, and `table_extraction`; defaults run no processors.

## Pipeline Paths

```text
PDF/image -> pages -> Surya detection (+ whitespace + text-layer recall) -> sparse: full-page OCR -> DP alignment -> refine ---------------+
                                    \-> dense: per-box OCR --------------------------------------------------------------------------------+-> post-process -> DocumentResult -> optional processors -> searchable PDF

PDF/image -> grounded bbox-native VLM -> post-process -> DocumentResult -> optional processors -> searchable PDF
```

- Hybrid is the default: Surya detection, optional whitespace-recall and text-layer-recall supplements, VLM OCR, DP alignment, optional refine, optional post-processing, embed.
- Dense hybrid pages use per-box OCR. `dense_mode="auto"` switches when box count exceeds `dense_threshold`.
- Grounded OCR uses `grounded_backend=` and skips Surya, DP alignment, and refine.

## Key Files

| File | Role |
| --- | --- |
| `src/omniscribe/server.py` | FastAPI application, server entry point, `omniscribe-server` script |
| `src/omniscribe/pipeline.py` | `OCRPipeline` facade — picks `HybridEngine` or `GroundedEngine` based on injected components |
| `src/omniscribe/confidence_eval.py` | Package-root confidence eval (fixture loader, IoU matching, `ConfidenceReport`) for `scripts/confidence_*.py` |
| `src/omniscribe/core/document.py` | Normalized DocumentResult IR and legacy pages-data adapter |
| `src/omniscribe/core/processors/` | Local deterministic document processors (`reading_order`, `quality`, `structure`, `section`, `layout`, `table`) and builder |
| `src/omniscribe/core/imaging/page_preprocess.py` | Local hybrid-path page preprocessing |
| `src/omniscribe/core/imaging/` | Imaging subpackage: `page_preprocess.py` (orientation/deskew/denoise/contrast/crop), `handwriting.py` (handwriting preprocessor), `utils.py` (image helpers) |
| `src/omniscribe/core/ocr_quality/routing.py` | Quality routing recommendation metadata |
| `src/omniscribe/core/evaluation.py` | Local evaluation metric helpers (lightweight, for processor result scoring) |
| `src/omniscribe/core/writers/docx.py` | Markdown → `.docx` converter for the docx export route |
| `src/omniscribe/core/writers/` | Output writers subpackage: `docx.py`, `docx_tree.py`, `html.py`, `tree_json.py`, and `exporter_base.py` (`DocumentExportProtocol` + `BaseDocumentExporter` ABC) |
| `src/omniscribe/core/recall/` | Recall boosters subpackage: `whitespace.py` (pixel-statistics candidates) and `text_layer.py` (embedded-text-layer recovery) |
| `src/omniscribe/core/llm/` | LLM client subpackage: `client.py` (OpenAI-compatible VLM client), `providers.py`, `temperatures.py` |
| `src/omniscribe/core/translate/` | Translation subpackage: `workflow.py` (LangGraph workflow), `config.py`, `dual.py`, `nllb.py`, `entity_memory.py`, `glossary.py`, `tree.py` |
| `src/omniscribe/core/aligner.py` | Surya detection and DP alignment |
| `src/omniscribe/core/recall/whitespace.py` | Whitespace recall booster — pixel-statistics text-line candidates merged into Surya detection on the hybrid path; `OMNISCRIBE_WHITESPACE_RECALL` kill switch, INFO run summary, fail-open per page |
| `src/omniscribe/core/recall/text_layer.py` | Text-layer recall source — recovers lines Surya missed from a digital PDF's embedded text layer (second box source, merged after the whitespace booster); `OMNISCRIBE_TEXT_LAYER_RECALL` kill switch, INFO run summary, fail-open per page; strict no-op for scans and image inputs |
| `src/omniscribe/core/ocr/` | OpenAI/Anthropic/Ollama multi-format client, prompts, limits, filters, and resilience (retry + circuit breaker) |
| `src/omniscribe/core/ocr_quality/` | OCR Quality Trust Layer (watermark, script detector, hallucination guard, Platt scaling calibration, trust scorer, orchestrator) |
| `src/omniscribe/core/transcription/` | Speech-to-text audio transcription engines (local & OpenAI-compatible API backends) |
| `src/omniscribe/core/lexicon/` | LanceDB-backed canonical glossary / translation lexicon store (Protocol + LanceDB impl + embedding wrapper + helper queries + one-shot migration core). See `docs/lexicon-migration-spec.md`. |
| `src/omniscribe/core/glossary_sources/` | Glossary import parsers (TBX, CSV, JSON, URL, SQL, Git, TMX, XLIFF) |
| `src/omniscribe/core/ocr/resilience.py` | `is_transient_error` classification, `CircuitBreaker` (closed/open/half-open), `CircuitOpenError` |
| `src/omniscribe/core/pdf/` | PDF/image rasterization (`rasterizer.py`), sandwich PDF embedding (`embedder.py`), and `PDFHandler` facade (`handler.py`) |
| `src/omniscribe/core/grounded/` | Grounded backends and bbox JSON parsers (retry + circuit breaker on the VLM call) |
| `src/omniscribe/core/postprocess.py` | Dictionary spellcheck |
| `src/omniscribe/core/translate/config.py` | Core-owned async translation settings |
| `src/omniscribe/core/translate/workflow.py` | Optional LangGraph translation workflow |
| `src/omniscribe/core/workflows/base.py` | `EngineBase` + `OutputWriter` / `DocumentResultWriter` / `ProgressCallback` / `WarningCallback` shared by both engines |
| `src/omniscribe/core/workflows/utils.py` | Stand-alone workflow helper functions (`parse_page_range`, `_estimate_confidence`, `_decode_page_image`, `_drop_refined_duplicates`) and constants |
| `src/omniscribe/core/workflows/stages/` | Decomposed hybrid stages: `conversion.py` (`HybridConverter`), `layout.py` (`HybridLayoutDetector`), `ocr.py` (`HybridOcrRunner`), `refine.py` (`HybridRefiner`) |
| `src/omniscribe/core/workflows/hybrid.py` | `HybridEngine` — stage-based hybrid orchestrator |
| `src/omniscribe/core/workflows/grounded.py` | `GroundedEngine` — single bbox-native VLM call → post-process → processors → output |
| `src/omniscribe/core/workflows/repair.py` | `QualityRepairLoop` / `RepairOptions` — engine-agnostic block-level low-confidence re-OCR with stall guard and fail-open |
| `src/omniscribe/resources/dictionaries/` | Packaged spellcheck dictionaries |
| `src/omniscribe/resources/calibration/` | Pre-trained model confidence calibration files (e.g. `qwen2_5_vl_72b.json`) |
| `src/omniscribe/harness/context.py` | Harness `Context`: Protocol-keyed services, LIFO effect disposal, event subscriptions, `mount_router`/`routes()` |
| `src/omniscribe/harness/loader.py` | `Loader(ctx).load(base, patch_paths=())` — parses `cordis.yml` plugin rows, deep-merges patches, applies `OMNISCRIBE_PLUGIN_<ID>__<FIELD>` env overrides, fails loud via `PluginLoadError` |
| `src/omniscribe/harness/plugin.py` | `Plugin` base class (id, `Schema` ClassVar, config dict, `apply`/`dispose`) |
| `src/omniscribe/resources/cordis.yml` | Shipped thirteen-plugin boot tree; operator patches layer via `OMNISCRIBE_CORDIS_PATCH` or `<artifact_dir>/cordis.patch.yml` |
| `src/omniscribe/plugins/runtime.py` | `RuntimeService` — settings holder, readiness flag, artifact/channel prune cadence |
| `src/omniscribe/plugins/logging.py` | Structured logging setup (`format: text\|json`, `level`) applied at boot |
| `src/omniscribe/plugins/state_backend.py` | `StateBackend` Protocol (artifacts + jobs + channels) + `MemoryStateBackend` (default) + `SQLiteStateBackend` (opt-in via `OMNISCRIBE_STATE_BACKEND=sqlite`); the single registration site for the backend service |
| `src/omniscribe/plugins/artifacts.py` | `ArtifactStore` — opaque id/token blob store over the state backend |
| `src/omniscribe/plugins/jobs.py` | `JobQueue` — single-worker async queue, job lifecycle events (`JobQueued`/`JobStarted`/`JobCompleted`/`JobFailed`/`JobCancelled`), `JobRunner` resolved at claim time |
| `src/omniscribe/plugins/progress.py` | `ProgressService` — one-shot session tokens, WebSocket attach with cross-loop send marshaling, cancel mirror |
| `src/omniscribe/plugins/providers.py` | Provider catalog + model discovery (`GET /api/providers`, `/{id}`, `/{id}/models`) over the active LLM coordinates |
| `src/omniscribe/plugins/health.py` | Liveness (`/api/health`, `/api/healthz`) and readiness (`/ready`, `/readyz`) probes |
| `src/omniscribe/plugins/translate/` | Translate plugin package: `schemas.py` (translation request models + client response contracts), `service.py` (`TranslationService` — sync single-shot, JobQueue runner, status mapping), `routes.py` (the four `/api/translate*` routes), `plugin.py` (router mount) |
| `src/omniscribe/plugins/transcribe/` | Transcribe plugin package: `schemas.py` (form-field and config request models + client response contracts), `service.py` (`TranscriptionService` — sync multipart transcription through the core transcription engines; transcript + metadata written as token-bound artifacts), `config_store.py` (always-writable in-memory config store with masked keys; SSRF-guarded model discovery with the whisper fallback list), `routes.py` (`POST /api/transcribe`, `GET/POST /api/config/transcription`, `GET /api/models/transcription`), `plugin.py` (router mount) |
| `src/omniscribe/plugins/glossary/` | Glossary plugin package: `schemas.py` (import/library request models for both payload shapes), `service.py` (`GlossaryImportService` — parse → entry-count estimate → sync import or JobQueue dispatch, plus library/preview/merged reads), `store.py` (lazy `LexiconStore` provider; routes 503 with an install hint when the `lexicon` extra is missing), `http_fetch.py` (SSRF-guarded, IP-pinned URL fetch for `/api/glossary/import/url`), `routes.py` (the nine `/api/glossary*` routes; dual-shape imports), `plugin.py` (router mount; registers `GlossaryJobRunner`) |
| `src/omniscribe/plugins/ocr/` | OCR plugin package: `plugin.py` (sync/async process routes, job surface, `/api/config` store), `schemas.py` (`OCRRequest` form parsing, client response contracts), `pipeline_bridge.py` (HTTP→`OCRPipeline` assembly), `events.py` |
| `src/omniscribe/utils/security.py` | SSRF target validation |
| `src/omniscribe/core/imaging/handwriting.py` | Local handwriting image preprocessor |
| `scripts/` | Developer utilities: confidence eval, fixture builder, debug/inspection scripts, bbox visualizers |
| `examples/` | Sample PDFs and images for `tests/` and the confidence scripts |

## Extension Points

`OCRPipeline` accepts injected components:

- `aligner=`: layout detection and text alignment
- `ocr_processor=`: page and crop OCR backend
- `pdf_handler=`: input conversion and default PDF writer
- `output_writer=`: alternate output generation (legacy 4-arg callable, or any object implementing `DocumentResultWriter.write_document_result` for the lossless `DocumentResult` path)
- `grounded_backend=`: bbox-native OCR path
- `document_processors=`: sequence of `DocumentProcessor` instances run after OCR cleanup and before PDF embedding
- `page_preprocessor=`: opt-in `PagePreprocessor` for orientation/deskew/denoise/contrast/crop preprocessing on the hybrid image path

## Plugin Harness

The API layer is a Cordis-style plugin harness (`src/omniscribe/harness/`)
mounted inside the FastAPI lifespan in `server.py`. The boot tree ships as
`src/omniscribe/resources/cordis.yml`; operators layer patch files
(`OMNISCRIBE_CORDIS_PATCH`, or `<artifact_dir>/cordis.patch.yml` by default)
and per-field env overrides (`OMNISCRIBE_PLUGIN_<ID>__<FIELD>`). Plugins are
applied in file order, register services keyed by Protocol, mount routers
via `ctx.mount_router`, and dispose their effects in LIFO order on shutdown.
Unknown state backends or malformed rows fail boot loud (`PluginLoadError`).
**Last updated: 2026-08-31.**

| Boot order | Plugin id | Module | Registers / mounts |
|---|---|---|---|
| 1 | `runtime` | `plugins/runtime.py` | `RuntimeService` (settings, readiness, prune cadence) |
| 2 | `logging` | `plugins/logging.py` | Structured logging side effect (no service) |
| 3 | `state_backend` | `plugins/state_backend.py` | `StateBackend` (`memory` default, `sqlite` opt-in) — the only registration site |
| 4 | `artifacts` | `plugins/artifacts.py` | `ArtifactStore` over the state backend |
| 5 | `jobs` | `plugins/jobs.py` | `JobQueue` + job lifecycle events; queue worker task |
| 6 | `progress` | `plugins/progress.py` | `ProgressService` + `/api/progress/*` routes + `/ws/{channel_id}` |
| 7 | `providers` | `plugins/providers.py` | `/api/providers` catalog and model discovery |
| 8 | `health` | `plugins/health.py` | `/api/health`, `/api/healthz`, `/ready`, `/readyz` |
| 9 | `documents` | `plugins/documents/` | `/api/extract`, `/api/export/*` (document, docx, html, docx-tree, blocktree, `{id}` fetch), `/api/text/{id}`, `/api/metadata/{id}` |
| 10 | `translate` | `plugins/translate/` | `TranslationService` + `TranslationJobRunner`; `/api/translate`, `/api/translate/async`, `/api/translate/status/{id}`, `/api/translate/nllb` |
| 11 | `transcribe` | `plugins/transcribe/` | `TranscriptionService`; `/api/transcribe`, `/api/config/transcription`, `/api/models/transcription` |
| 12 | `glossary` | `plugins/glossary/` | `GlossaryImportService` + `GlossaryJobRunner`; `/api/glossary/import` (JSON + multipart), `/api/glossary/import/url` (query + JSON body), `/api/glossary/library{,/preview,/merged}`, `/library/{id}{,/enable,/entries}`, `/library/reorder` |
| 13 | `ocr` | `plugins/ocr/` | `OCRService` + `JobRunner`, `/api/process*`, `/api/jobs*`, `/api/config*` |

**Deferred capabilities** (not yet rebuilt on the harness):
the auth / rate-limit / upload-size ASGI middlewares, the Redis
state backend, and formal model pre-flight API route (in-core pre-flight
is implemented via `ensure_model_loaded()` in `core/ocr/processor.py`).
Tracked in `docs/outstanding-work.md` §5.

**Phase C complete** (2026-08-31): all client-facing routes are rebuilt
on the harness.

**Testing.** `tests/conftest.py` ships three boot fixtures: `cordis_env`
(temp thirteen-row tree, memory backend, small TTLs), `harness_ctx` (a loaded
Context), and `api_client` (TestClient over `create_app()` — plugins boot
inside lifespan on the portal loop that also serves the requests). Router
contract tests live in `tests/routers/`, plugin unit tests in
`tests/plugins/`, harness tests in `tests/harness/`.

## Web Notes

- The translation **core** (`core/translate/`, LangGraph workflow, LanceDB-backed `LexiconStore` via the `lexicon` extra) powers the `translate` plugin, which serves `/api/translate` (sync single-shot), `/api/translate/async` (tree-aware, dispatched on the harness JobQueue, single worker), `/api/translate/status/{job_id}`, `/api/translate/result/{job_id}?token=…` (token-redeeming async result fetch), and `/api/translate/nllb`. The `memory` extra name is kept as a one-release deprecation alias for `lexicon`.
- **Transcription** ships on the harness via the `transcribe` plugin: `POST /api/transcribe` (sync multipart transcription; the transcript and its metadata are stored as token-bound artifacts), `GET/POST /api/config/transcription` (always-writable in-memory config store; API keys and auth tokens are returned masked), and `GET /api/models/transcription` (endpoint model discovery guarded by `is_ssrf_target`, falling back to the canned whisper model list when discovery fails).
- **Glossary** ships on the harness via the `glossary` plugin: a 9-route import/library surface — `/api/glossary/import` (JSON + multipart), `/api/glossary/import/url` (query + JSON body), `/api/glossary/library{,/preview,/merged}`, `/library/{id}{,/enable,/entries}`, `/library/reorder`. Imports accept both shapes (legacy JSON source envelope and the client's multipart / JSON-body shapes); imports above the 5,000-entry estimate dispatch on the harness JobQueue (`GlossaryJobRunner`, the third runner producer). The LanceDB lexicon store loads lazily — routes 503 with an install hint (`uv sync --extra lexicon`) when the `lexicon` extra is missing.
- `ALLOW_SSRF_LOCAL`: the code default is `False` (`RuntimeSettings` in `config.py`); the shipped `.env.example` mirrors the code default (`False`). Set it to `true` only when pointing the SSRF-guarded URL fetcher at a local VLM endpoint on loopback (e.g. LM Studio at `127.0.0.1:1234`); disable immediately after.
- **Auth**: the `OMNISCRIBE_AUTH_TOKEN` ASGI bearer middleware is wired unconditionally in `src/omniscribe/server.py:184-202` (shipped in Wave 14). Placeholder tokens (e.g. `change-me-in-prod`) are rejected on any non-loopback bind; the server refuses to start with a clear `SystemExit` if a non-loopback bind is attempted without a real token. See [SECURITY.md](SECURITY.md) §Security Features for the current contract.
- **VLM resilience**: every LLM call retries transient errors (429/5xx/connection resets) with exponential backoff, and a per-request circuit breaker fails fast after `OMNISCRIBE_CB_FAILURE_THRESHOLD` (default 5) consecutive failures. Tunables: `OMNISCRIBE_LLM_MAX_RETRIES` (default 2), `OMNISCRIBE_LLM_RETRY_BASE_DELAY` (default 1.0s), `OMNISCRIBE_CB_COOLDOWN` (default 30s), `OMNISCRIBE_VLM_PAGE_MAX_TOKENS` (default 6144), `OMNISCRIBE_VLM_CROP_MAX_TOKENS` (default 256).
- **Model pre-flight**: implemented in-core via `ensure_model_loaded()` in `core/ocr/processor.py` (checks `GET /v1/models` against loaded model IDs before execution to guard against silent model fallback; also mirrored in `core/grounded/prompted.py`). The OCR plugin additionally seeds the `verify_model` config key.
- **Quality repair loop**: `/api/process` re-OCRs blocks whose estimated confidence is below the target (crop-scoped, sequential, accept-only-while-improving) after block emission and before embedding. Defaults ON at the API layer (up to 2 extra VLM passes per low-confidence block); in-process `OCRPipeline.run` callers stay off unless they pass `repair_options=`. Per-request form fields `quality_loop_enabled` / `quality_target` (0.5–1.0) / `quality_max_retries` (0–5); the boot defaults are seeded by the `ocr` plugin's `cordis.yml` config, which expands the env seeds `OMNISCRIBE_QUALITY_LOOP`, `OMNISCRIBE_QUALITY_TARGET`, `OMNISCRIBE_QUALITY_MAX_RETRIES`. WebSocket frames: `block_retry`, `block_revised`, `quality_summary`.
- Web runtime settings live in the OCR plugin's in-memory `/api/config` store, seeded from `RuntimeSettings` at plugin apply time; LLM coordinate updates write through to the shared settings.
- **Flutter Desktop / UI**: the user-facing client is a multi-platform Flutter app under `client/` connecting to the OmniScribe FastAPI/plugin backend over HTTP and WebSocket.
- **Developer scripts** live in `scripts/`. The most useful for OCR quality work are `scripts/confidence_eval.py` (hybrid + grounded vs the `examples/*.pdf` fixtures) and `scripts/confidence_image.py` (single-image confidence). The rest are debug/inspection/visualization tools.
- **Docker**: `Dockerfile` builds a `python:3.14-slim` runtime with the `web`, `async-translation`, and `preprocessing` extras. `compose.yaml` runs `api` + `redis` only — there is no Celery worker service; async translation dispatches in-process on the harness JobQueue. Image exposes port 8000; bind `LLM_API_BASE` to `http://host.docker.internal:1234/v1` to talk to a host-side LM Studio.
- **Pre-commit**: `.pre-commit-config.yaml` runs ruff (check + format), mypy, and `uv-lock` on every commit. Enable with `uv tool run pre-commit install` after cloning.
- **Nightly slow tests**: `.github/workflows/nightly.yml` runs `pytest -m slow` at 03:00 UTC with cached HF Hub snapshots, catching Surya-path regressions the fast tier skips.
- **OCR system-role gating**: some models (notably OlmOCR-2 / OlmOCR) were RL-trained on a single user-role turn with the canonical OlmOCR page prompt and reject a layered system role. `omniscribe.core.ocr.prompts.model_supports_system_role` is the single source of truth — the canonical OLMOCR page prompt is also always sent as a pure user message even on models that *do* support system role. When adding a new call site that emits OCR prompts, route through `_resolve_page_system` / `_resolve_crop_system` (or `select_system_message` for crop / dual-engine / correction) rather than hand-rolling a system role.
- **Progress WebSocket cross-loop marshalling**: `ProgressServiceImpl.broadcast` in `plugins/progress.py` records each connection's accept loop on attach and marshals any foreign-loop send back onto it via `asyncio.run_coroutine_threadsafe`. **All writes to the underlying uvicorn WebSocket must go through the service's broadcast path from any non-accept loop** — uvicorn's wsproto state machine is not safe to drive from two threads / loops at once, and concurrent writes interleave bytes on the wire (browser sees mangled JSON, truncated frames, `Invalid frame header`). The regression test is `tests/plugins/test_progress_plugin.py::test_foreign_loop_send_is_marshaled_to_accept_loop`; if you find yourself bypassing the service to call `ws.send_text` / `ws.send_json` directly, that test is the contract you're breaking.

## Known Tech Debt

- `/api/process` runs the full OCR pipeline synchronously on the uvicorn worker (no background task queue on the default path); long jobs block other requests on the same worker. The async path ships already — `POST /api/process/async` returns `202 + job_id` immediately and the single-worker `JobQueue` (in `plugins/jobs.py`) drains jobs sequentially. The workstation UI's "Async processing" toggle lets users opt into the async path; the result PDF is fetched from `GET /api/jobs/{job_id}/result` once the job reaches `status: "complete"`. The **result token is delivered out-of-band** in the `job_completed` SSE event payload (mirrors the sync path's `X-Text-Artifact-Token` response header) — the polled `JobStatusResponse` exposes only the opaque `text_artifact_id`, never the token (audit C-3/H-3: keeps `GET /api/jobs/{job_id}/result` constant-time-checked without leaking a per-call bearer to the unauthenticated status endpoint). The queue stays single-worker — translation async rides the same harness JobQueue (the compose Celery worker service was retired); true multi-worker / crash-safe dispatch via Celery remains only a potential future option.
- Job/artifact state is in-memory by default (`MemoryStateBackend`). One opt-in persistent backend ships on the harness: `OMNISCRIBE_STATE_BACKEND=sqlite` (single-file, local-first; see `plugins/state_backend.py`; the WAL-mode file defaults to `<artifact_dir>/omniscribe-state.db`, override with `OMNISCRIBE_STATE_DB_PATH`). The Redis backend was deferred in the rebuild. Progress channels never persist — they reference live WebSocket connections.
- `pages_structured` legacy dict is still the working format inside `HybridEngine`; `DocumentResult` is built at finalize. The output boundary now supports the lossless rich path (`DocumentResultWriter`), but intermediate stages still convert.
- `dense.pdf` and `notes.pdf` ground-truth fixtures are bootstrapped from hybrid output (regression baseline, not absolute quality).
- `surya-ocr 0.17.x` used to import `requests` in `surya/common/s3.py` without declaring it; `pyproject.toml` shipped a `requests>=2.31` workaround dep. **Closed in audit-secondary Phase 5 (2026-08-19):** `surya-ocr ≥ 0.22` now declares `requests<3,>=2.28.0` in its own metadata, so the workaround is no longer required. `requests` has been removed from the base deps and moved to `[dependency-groups] dev` (it is only directly imported by `scripts/ingest_lexicon.py`, a dev-only ingestion helper).
- **A11y regression coverage (F4.9, closed by Phase B).** The historical Playwright a11y spec covered the Svelte web workspace that Phase B deleted, so the F4.9 audit gap is effectively closed. **Forward guard:** any future web client (the current Flutter client lives in `client/`) must ship a11y regression tests on day one — `axe-playwright` (or the equivalent on the chosen stack) wired into the CI fast tier. The Phase 5d test `tests/scripts/test_tier_discipline.py::test_agents_md_documents_a11y_testing_gap` pins that this forward guard stays discoverable in AGENTS.md.

## Product-Planning Notes (scout plans, not code)

External scout plans live in `.mavis/plans/scout/`. The most recent
plan (2026-06-14) has four tracks plus a synthesis plan:

- `track-md.md` — Anything-to-Markdown / rich-text converter
  landscape (29 players: Microsoft / Google / Adobe / Apple / OSS).
  Headline finding: OSS has converged on three pipeline patterns
  (local-only / local+VLM / VLM-only) with OmniScribe in the
  defensible B-mode center; license posture (Marker's GPL+RAIL-M
  $2M cap, PyMuPDF4LLM AGPL) is a real B2B wedge; Docling's
  `StandardPdfPipeline` is the production reference for batch
  multi-stage threaded PDF processing.
- `track-schema-tables.md` — schema / table extraction landscape.
- `track-ocr-vision.md` — AI OCR / VLM landscape.
- `track-localdeepl.md` — internal architecture inventory.
- `PLAN.md` — synthesis of all four tracks (recommendations by
  extension point, sequenced roadmap).

Per-track changelogs (project-specific findings that should
survive into the post-scout roadmap) live in
`.mavis/plans/scout/changelogs/`. Generic research patterns
(fan-out, brief-correction) belong in agent memory, not here.

## See Also

- [README.md](README.md) — feature overview, install, scripts
- [CHANGELOG.md](CHANGELOG.md) — version history and breaking changes
- [ARCHITECTURE.md](ARCHITECTURE.md) — pipeline, component map, and full API surface
- [DEPLOYMENT.md](DEPLOYMENT.md) — local / LAN / public-internet deployment profiles
- [SECURITY.md](SECURITY.md) — threat model, hardening checklist, vulnerability disclosure

_Last updated: 2026-09-05_
