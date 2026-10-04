# Goal alignment assessment — 2026-10-03

> **Latest implementation closeout: 2026-10-04.** The independently reproduced
> code defects have been addressed. Backend and live-model evidence, client
> changes, and remaining environment/product gates are recorded in
> [Implementation closeout](#implementation-closeout-2026-10-04).
> Earlier findings and DONE claims below are historical, not current signoff.

## GitHub precheck (2026-10-04)

**Ready for review; full merge and release signoff remains open.** The backend
checks below pass locally on Windows/Python 3.13.9. This does not prove the full
GitHub operating-system/Python matrix or the unexecuted Flutter gates.

| Check | Result |
| --- | --- |
| Ruff lint: `src tests scripts` | Pass |
| Ruff format: `src tests scripts` | Pass; 465 files |
| CI typing: `mypy src tests` | Pass; 444 files after fixing 43 typing errors across eight files |
| Fast backend suite with `--cov-fail-under=80` | 2,841 passed, 18 skipped, 13 deselected; **84.07% coverage**; exit 0 |
| Client feature layout | Pass; 169 Dart files, 31 providers |
| Assessment links, evidence JSON and whitespace | Pass |
| Flutter `analyze --fatal-infos` and `test` | Blocked locally by the previously recorded SDK directory denial |

The coverage run took 486.39 seconds and emitted 20 warnings, including SQLite
resource warnings and dependency/fixture deprecations. Its collection preceded
the final typing cleanup and added Redis smoke regressions. Subsequent affected
checks passed: 106 OCR tests, 68 security/export tests, 16 confidence-reporting
tests and 25 Redis smoke tests. Counts overlap and are not a combined suite total.
The final full typing, lint and formatting checks cover the resulting files.

Reproduce the backend gate using the installed environment:

```powershell
rtk .venv/Scripts/python.exe -m ruff check src tests scripts --no-cache
rtk .venv/Scripts/python.exe -m ruff format src tests scripts --check --no-cache
rtk .venv/Scripts/python.exe -m mypy src tests --cache-dir .assessment-tmp/github-precheck-mypy
rtk .venv/Scripts/python.exe -m pytest -m 'not slow and not slow_dataset' --cov=src/omniscribe --cov-fail-under=80 --cov-report=xml:reports/github-precheck-coverage.xml --junitxml=reports/github-precheck-junit.xml -q --basetemp=.assessment-tmp/github-precheck-tests -o cache_dir=.assessment-tmp/github-precheck-cache
rtk .venv/Scripts/python.exe client/tool/check_feature_layout.py
```

GitHub preparation excludes `.agent-tmp/` and `.assessment-tmp/` without deleting
local evidence. The existing `reports/` exclusion covers JUnit and coverage XML.
Current findings lead this document; earlier claims remain as historical records.
Personal paths are replaced with environment-variable or repository-relative paths.

When preparing the change, include the replacement files under `client/lib/app/`,
`client/lib/features/` and `client/lib/shared/` alongside the removed legacy files.
The existing [client CI job](../.github/workflows/test.yml) requires fatal-info
analysis and client tests; the layout check does not substitute for them.
The [release workflow](../.github/workflows/release.yml) can trigger on a
`pyproject.toml` change on `main` independently of successful Tests. It publishes
Python packages, not the Windows/Flutter bundle. Release approval must therefore
check the test outcome and remaining packaging gates explicitly.

## Implementation closeout (2026-10-04)

The [closeout blueprint](goal-alignment-closeout-blueprint-2026-10-04.md)
split client, OCR and runtime ownership, followed by independent review. The
reproduced code defects in the historical assessment have been fixed without another runtime layer or
dependency. **This is implementation closeout, not unconditional product or
release acceptance:** client SDK verification and deployment gates remain
blocked, and the first measured OCR baseline is not satisfactory quality proof.

### Current status of all eight findings

| Finding | Implemented outcome | Acceptance evidence / remaining gate |
| --- | --- | --- |
| 1. Rich document persistence | Trust scores/flags synchronize into a copied existing tree, including split table cells, preserving sections, headings, IDs and geometry. Flutter carries rich handles through sync/async results, state and structured exports. Supplied rich artifacts fail visibly when missing; only legacy requests without rich handles reconstruct text. | Combined real trust scoring → OCR persistence → HTTP structured export passes. Native DOCX/table regressions pass. Client handle regressions are added but SDK execution is blocked. |
| 2. Authoritative exports | Missing, unreadable, malformed, incomplete and empty completed text artifacts fail visibly. Canonical server Markdown and structured exports receive rich handles. Preview hydration restores missing line occurrences within pages, preserving live coordinates and repeated lines; replacement guards remain. | Backend deleted-artifact checks and live rich-artifact HTTP exports pass. Client authority, replacement and hydration regressions are added; device/widget gates remain blocked. |
| 3. Quality controls | Typed HTTP quality controls and trust construction remain wired; scores now reach canonical exports rather than remaining only in page blocks. | API/processor/trust/export regressions pass. Quality controls remain explicitly API-only; no Flutter toggle is claimed. Model-backed repair quality is not established by these tests. |
| 4. NLLB targets | UI aliases and declared supported model codes resolve explicitly; unsupported names and code-shaped strings fail before loading weights. | Language/request/service regressions pass. English source remains the documented scope. |
| 5. Broker/restart behavior | Omitted progress mode inherits runtime configuration through the shipped Loader. SQLite in-process queues claim a native exclusive owner lock before reconciliation/dispatch; a live peer fails clearly, while process death releases ownership. Redis state requires Redis dispatch. | Actual Loader, broker override, live-peer input protection, abrupt process-death release and idempotent shutdown regressions pass on Windows. Real Redis concurrency/recovery/throughput remains unavailable. |
| 6. Failure/import feedback | All-page OCR failure produces sync HTTP 502 or terminal async failure, without successful empty result artifacts. Partial failed-page metadata remains exposed. Glossary accepts nullable errors, validates status envelopes, retries bounded transient polling failures and retains URL worker errors in the modal. | Sync/async page-failure regressions pass. Actual glossary JSON/polling/modal regressions are added but client execution is blocked. |
| 7. Honest measurement | Existing canonical-export/provenance/failing-exit harness has now completed three requested live grounded fixture runs. | Machine-readable evidence and raw outputs exist. Scores below expose substantial misses; no independently reviewed multilingual corpus, second engine or measured competitor comparison has established the quality goal. |
| 8. Formats/storage/distribution | BMP/TIFF and durable storage remain supported; boot tests isolate their storage. Arabic/mixed-script search and logical extraction now use PDF ActualText over an invisible carrier, preserving scan appearance and explicit multiline positions. Manual assets use explicit versioned names and SHA-256 manifests. | Exact extraction, search, unchanged raster, font-independent Arabic and line-geometry tests pass. Current Windows bundle build was attempted and stopped on a denied user directory; Flutter Windows/web and device acceptance remain blocked. |

### Arabic text-layer implementation

The scanned raster supplies visible glyphs. For scripts with shaping/cmap
ambiguity, an invisible, evenly positioned carrier spans the OCR bounding box;
PDF `ActualText` records the logical UTF-16 source string. This avoids dependence
on the host's Arabic font mappings and preserves mixed-script text without
dropping characters. Explicit lines retain separate selection rectangles.
The two former Arabic expected failures are now passing assertions. Tests also
compare output pixels against the same raster without the text layer.

This proves extraction/search with PyMuPDF and image preservation. It does not
claim character-level alignment inside an OCR block or independent verification
of every PDF viewer's copy behavior. The carrier is intentionally uniform within
each block; scanned visual appearance is unchanged.

### Live evidence and observed quality

The retained [machine-readable evidence](goal-alignment-live-evidence-2026-10-04.json)
contains prompt hashes, model/settings, host information, fixture hashes, raw
output locations, failures and scores. Execution used the already available
local `allenai/olmocr-2-7b` endpoint; no model was downloaded.

Command:
`rtk .venv/Scripts/python.exe scripts/confidence_eval.py --path grounded --grounded-model allenai/olmocr-2-7b --fixtures digital.pdf,hybrid.pdf,handwritten.pdf --out-dir .assessment-tmp/live-grounded-20261004`.

| Fixture | Latency (seconds) | Block recall | Export CER | Export WER |
| --- | ---: | ---: | ---: | ---: |
| digital.pdf | 46.7 | 0.12 | 0.896 | 0.941 |
| hybrid.pdf | 28.5 | 0.39 | 0.618 | 0.922 |
| handwritten.pdf | 22.8 | 0.64 | 1.370 | 1.637 |

The run exited zero with **3/3 requested combinations scored**, which means
measurement completed, not that recognition passed a quality threshold. These
references use the existing synthesized Markdown fallback; CER/WER include
structural formatting and insertion differences. Weak block recall independently
shows missed content. The selected model/settings require further evaluation
before an accuracy claim; heading F1 of 1.0 is not meaningful evidence when both
sides contain no Markdown headings. Competitor rows remain illustrative.

A separate actual HTTP upload of the scanned handwriting fixture through the
shipped SQLite plugin tree returned HTTP 200 for OCR, Markdown and block-tree
exports using the published rich handles. The resulting one-page PDF contained
474 extracted characters; Markdown contained 568 characters. Artifacts and
token-free raw evidence remain locally under `.assessment-tmp/live-api-20261004/`
and are excluded from Git. The linked JSON retains the summary and provenance;
raw output paths are local references, not downloadable GitHub artifacts. This closes
a backend live-model journey only, not the Flutter Windows/browser journey.

### Verification and environment gates

- Ruff lint and format pass for `src`, `tests` and `scripts` (465 Python files).
- Mypy passes for 222 source files with a workspace cache.
- Client feature boundaries pass: 169 Dart files, 31 unique providers.
- Latest PDF acceptance suite: **44 passed**, including the review-driven
  multiline selection check.
- Full offline backend suite: **2,840 passed, 18 skipped, 13 deselected, zero
  failures and zero expected failures** in 255.78 seconds. Its collection
  preceded the final multiline PDF regression; the subsequent 44-test PDF gate
  covers that last source adjustment and added regression. Focused counts
  overlap and must not be summed.

Full-suite command:
`rtk .venv/Scripts/python.exe -m pytest -m 'not slow and not slow_dataset' -q --basetemp=.assessment-tmp/closeout-final-full-20261004 -o cache_dir=.assessment-tmp/closeout-final-full-cache`.
The three previous boot-storage failures now pass using their isolated fixtures.

**Flutter:** normal Dart formatting failed initializing analytics at
`%APPDATA%\.dart-tool`. SDK formatting, analysis, tests,
Windows/web builds and device journeys are therefore unexecuted. No alternate
wrapper, telemetry relocation or outside-directory write bypass was used.
Client regressions are implementation evidence until executed.

**Windows bundle:**
`rtk .venv/Scripts/python.exe -m PyInstaller --noconfirm omniscribe_server.spec`
reached dependency analysis, then failed with `PermissionError [WinError 5]`
reading `%APPDATA%\Python\Python313\site-packages`.
The build was halted under the directory-safety rule. No current executable,
bundle smoke, checksum or release upload was produced. Optional/obsolete hidden
import warnings also require review in a permitted build environment.

**Redis:** no live Redis service was available. Docker reported no running daemon
and denied access to its user config. No installation, daemon control, config
relocation or permission bypass was attempted. Offline broker/ownership proof
does not substitute for live worker concurrency and recovery.

### Remaining acceptance work

1. In an environment where normal SDK commands can access their required user
   directories, execute the added client tests, Dart formatting/analysis,
   Flutter Windows/web builds and scanned upload → PDF search/copy → complete
   text/structured export journeys. Resolve any resulting failures before
   signing off the client changes.
2. Run the documented paired Redis API/worker recipe against real Redis,
   including concurrent jobs and abrupt worker restart with the same job
   completing under a new lease owner.
3. Build and smoke the current Windows server bundle, archive the complete
   Flutter client directory, produce the documented SHA-256 manifest and verify
   matching versioned asset names. Publishing remains a separate action.
4. Review multilingual/columns/table reference content independently, evaluate
   the intended engines/models on that corpus and improve the observed misses
   before accepting accuracy claims. A completed harness run alone cannot close
   this product-quality gate.

## Historical assessment and remediation (2026-10-03)

The following checkpoints preserve the original findings and implementation
claims. They describe earlier repository states; use the closeout above for
current acceptance and remaining work. Source line references are historical.

## Review blueprint

Assess the current working tree against README and project goals without changing application code.

| Review area | Responsibility | Evidence |
| --- | --- | --- |
| OCR and document pipeline | Trace ingestion, OCR engines, searchable PDF and structured exports | Source, focused regressions, benchmark provenance |
| Flutter workflow | Trace upload, processing, progress, cancellation, hydration and export | Source, state/widget tests, client verification tools |
| Runtime and distribution | Assess local-first defaults, jobs, security, packaging and release gates | Source, CI definitions, focused runtime checks |

This file records the assessment and prioritized next steps. Existing source ownership remains documented in `docs/ARCHITECTURE.md`.

## Results

**Verdict:** the architecture and core processing are substantially aligned with the local-first OCR workstation goal. The current product is not yet proven to meet its full quality, structured-export, and distributed-operation claims. The main gaps are existing components disconnected at API/client boundaries, rather than missing engines or a need for another architectural rewrite.

Assessment covers the current, heavily modified working tree, including untracked feature modules. It is not an assessment of a published release binary. Application code was not changed.

## Goals and current behavior

| Goal | Assessment |
| --- | --- |
| Local-first scanned PDF/image to searchable PDF | Implemented core pipeline, hybrid/grounded engines, PDF embedding, and API/client submission. A fresh model-backed client-to-result run remains necessary. |
| Reliable workstation processing and exports | Submission, cancellation, stale-run guards and result downloads exist. Partial progress can silently leave local exports incomplete. |
| Quality/trust controls and repair | Core trust and repair modules exist. Repair is wired; API trust options and quality-routing controls are not fully wired. These are distinct features. |
| Structured, provenance-preserving exports | Writers and chunkers exist, but OCR persistence loses the rich document representation and exports reconstruct heuristic structure. |
| Translation, transcription and glossary | Working service/client flows exist, with specific language-resolution and queued-import gaps. Audio playback is explicitly simulated. |
| Distributed processing | Redis queue/worker/recovery machinery exists; worker broker override propagation is inconsistent and deployment behavior still needs real-environment proof. |
| Demonstrated OCR accuracy and easy distribution | Evaluation and packaging tools exist. Current scoring does not exercise canonical exported Markdown, comparative rows are illustrative, and current live/bundle gates remain open. |

## Findings and acceptance checks

### 1. Preserve the document result across the OCR/artifact/export boundary (P1)

`src/omniscribe/plugins/ocr/service.py:492` stores page strings, and `_execute` returns PDF bytes, strings and a trust summary (`:599`), discarding the available `last_document_result`. The digital reader path similarly reduces blocks to strings (`:570`). The sync path uses the same reduction (`:387`). Neither persists the metadata artifact promised by README.

`src/omniscribe/plugins/documents/service.py:75` reconstructs the export tree using zero bboxes and text classification heuristics. Geometry, original block kinds, tables, section metadata and individual trust scores cannot survive this boundary. This undermines structured DOCX/HTML and RAG provenance even when core processors succeeded.

**Next:** persist the existing rich document representation as a capability-bound artifact and use it for canonical exports. Retain page-text artifacts for compatible clients. Avoid creating a second intermediate document model.

**Acceptance:** sync and async OCR of a multi-page table/heading fixture preserve geometry, block types, section paths and trust metadata through stored artifacts and exports. Digital DOCX ingestion retains native table structure through export.

### 2. Make completed artifacts authoritative for exports (P1)

`client/lib/features/jobs/job_orchestration_notifier.dart:377` skips recovery if any live bbox exists; `client/lib/features/workstation/workstation_notifier.dart:448` repeats that condition. `client/lib/features/documents/export_notifier.dart:86` then exports the received boxes. Receiving page one before a socket disconnect is enough to prevent recovery of missing pages. Searchable PDF uses separate result bytes and is not subject to this particular text-loss path.

**Next:** use completed artifacts for export content and reconcile missing preview blocks without overwriting real coordinates. Route structured exports through the existing document repository; expose chunks/Docling/MinerU only after their retained structure is verified.

**Acceptance:** deliver only some WebSocket blocks, complete a two-page job, and assert every text export contains both pages. Repeat with no live frames and a replaced document.

### 3. Wire or withdraw advertised quality controls (P1)

`src/omniscribe/plugins/ocr/schemas.py:90` has no `quality_options` field. Pydantic silently drops an input with that name. `src/omniscribe/plugins/ocr/pipeline_bridge.py:46` does not construct/inject a trust orchestrator. Passing `trust_model_id` alone does not enable one. The request processor allowlist (`schemas.py:46`) also excludes the implemented `table_fallback` processor, and the bridge has no quality-routing options.

**Next:** validate and forward the existing options through the shared sync/async request and bridge. Correct README/UI claims until those paths are connected.

**Acceptance:** enable each advertised control via a real HTTP request, assert the intended core component runs, and verify scored block metadata survives export. Invalid options must produce an explicit validation response.

### 4. Correct NLLB language resolution (P1)

The UI offers Chinese (Simplified) and Korean (`client/lib/features/translation/translation_screen.dart:36`). NLLB receives those labels unchanged. `src/omniscribe/core/translate/nllb.py:43` resolves both to English. This affects the NLLB mode, not LLM translation. Source language is also fixed to English.

**Next:** map every offered target explicitly and reject unsupported language names rather than silently translating into English. Add source selection only if multilingual source translation is in the intended supported scope.

**Acceptance:** all selectable NLLB targets resolve to the expected model code; unknown targets fail validation. A direct resolver probe confirmed both affected labels currently produce `eng_Latn`.

### 5. Use one resolved Redis configuration in each worker (P1 for Redis deployments)

`src/omniscribe/worker.py:87` passes its resolved broker URL to state, jobs and progress plugins. `src/omniscribe/plugins/jobs.py:597` and `src/omniscribe/plugins/progress.py:642` reload settings and use the environment/default URL instead. A worker launched with a differing `--redis-url` can read its queue and progress broker from one instance while persisting state to another.

**Next:** consume the resolved plugin/runtime broker configuration consistently.

**Acceptance:** give the worker a CLI broker URL differing from its environment and assert state, queue and progress use the same URL. Then run concurrent jobs and worker-restart recovery against real Redis. Default SQLite persists records, not in-process queue execution; define restart recovery or explicitly mark interrupted work terminal on startup.

### 6. Preserve partial-failure and queued-job feedback (P2)

OCR status/list constructors hardcode `failed_pages=[]` (`src/omniscribe/plugins/ocr/service.py:827`, `:842`), hiding recorded core page failures. The glossary client refreshes immediately after a queued import (`client/lib/features/glossary/glossary_notifier.dart:191`) and closes the modal without following its job handle.

**Next:** propagate real failed-page outcomes and follow glossary imports through the existing job infrastructure.

**Acceptance:** a failed OCR page is visible in completion/history; a delayed glossary import refreshes on success and reports worker failure.

### 7. Make measurement match the advertised output (P2, before accuracy claims)

`scripts/confidence_eval.py:79` calls the grounded backend directly; the hybrid path captures raw output blocks. Scoring at `:240` and `:260` calls `blocks_to_markdown`, which simply orders and joins bbox/text pairs (`src/omniscribe/confidence_eval.py:716`). It does not measure the canonical exported Markdown, rich document processors, or client export workflow. Per-path exceptions are printed and swallowed (`scripts/confidence_eval.py:245`, `:265`), so a run with failures can exit successfully.

**Next:** score actual canonical exports, emit machine-readable results including failures, and return a failing status for incomplete requested evaluations. Record model, prompts, settings, hardware, latency, corpus and raw outputs. Treat existing competitor rows as illustrative until measured on a shared corpus/protocol. Full dataset downloaders remain stubs pending their license review.

**Acceptance:** a missing endpoint fails the evaluation; a complete live run produces scored exports and provenance for every requested fixture/path. Include scans, handwriting, columns, tables and non-Latin text with independently checked reference content.

### 8. Close format and release promises (P2)

README advertises BMP/TIFF, while API MIME/signature allowlists and the client dropzone omit them. Either support them throughout or narrow the promise. Re-run the Windows bundle smoke and Flutter Windows/web gates for this working tree before claiming a deployable release. Choose a new patch release or explicitly rebuilt artifact; source changes do not update an existing binary.

The checked-in release workflow publishes wheel/sdist assets (`.github/workflows/release.yml:125`), not the Windows server/client binaries described in the install guide. Existing remote release assets were not inspected. Add reproducible Windows asset builds and smoke checks or clearly document the manual release step. Default state/artifact storage is temporary (`src/omniscribe/config.py:138`); choose a durable per-user location for users expecting history after system cleanup.

Multilingual search/copy also needs stronger proof. `src/omniscribe/core/pdf/embedder_helpers.py:258` filters characters missing from the selected font. The multilingual embedding test (`tests/core/pdf/test_embedder.py:186`) does not assert its Arabic input survives extraction. Assert exact supported-script round trips, including Arabic and mixed-script blocks, and make incomplete text-layer coverage visible to users rather than treating a successful PDF write as complete recognition.

## Recommended sequence

1. Restore a green lint/format baseline, then fix canonical document persistence and partial-stream exports.
2. Wire quality options, correct NLLB targets, and expose real failed-page/import outcomes with small boundary regressions.
3. Prove one real scanned upload-to-searchable-PDF-to-text/structured-export journey on Windows and web. Check PDF search/copy, complete text and layout fidelity.
4. Make benchmark scoring exercise that same export path and record reproducible accuracy/latency results.
5. Fix and measure Redis deployment only for a deployment that needs it; then verify the release bundle and synchronize feature claims.

No new plugin framework, client rewrite, connector ecosystem or exporter abstraction is needed to address these findings.

## Initial verification (2026-10-03)

- Python source typing: `mypy src` passes for 221 source files.
- Ruff lint: fails with B010 at `tests/test_security.py:71`.
- Ruff format: six test files require formatting: `tests/core/pdf/test_embedder.py`, `tests/core/readers/test_readers.py`, `tests/core/test_document.py`, `tests/plugins/test_jobs_redis_worker_security.py`, `tests/test_security.py`, and `tests/test_server_boot.py`.
- Direct probes: `quality_options` is discarded by `OCRRequest`; Chinese (Simplified) and Korean resolve to English in NLLB.
- Parallel runtime reviewer: 61 focused server/config/in-process/Redis queue tests passed.
- Full offline fast suite: **2,675 passed, 18 skipped, 13 deselected** in 203.22 seconds, using `pytest -m 'not slow and not slow_dataset'`. Skips cover missing optional SQLAlchemy and pytesseract dependencies. Four warnings include a permission-denied pytest cache write; that locked cache was not modified or bypassed. This run did not measure coverage or establish the complete CI release gate.
- Flutter analysis/device tests, real model-backed OCR, model accuracy, real Redis recovery/throughput, and current Windows binary were not verified in this assessment. No model downloads or locked-directory bypasses were used.

---

# Resolution (2026-10-03)

The earlier remediation pass marked all eight findings implemented, and
`ARCHITECTURE.md` carries its table as a repository-ledger entry. Independent
revalidation below found remaining defects and regressions, so the DONE labels
must not be used as acceptance sign-off. No complete real-model, real Redis,
or release-build verification was recorded by that implementation pass.

## 1. Persist the rich document representation — historical implementation claim

`core/document.py` gained lossless `to_dict`/`from_dict` on `DocumentResult`,
`DocumentTree` and `DocumentBlock` (geometry, block `kind`, `confidence`,
`source_processor`, `reading_order`, trust score/flags, page size and
metadata). `plugins/documents/artifact.py` stores and loads that structure
behind an opaque, capability-bound handle.

* The legacy text artifact is unchanged — still `{page: lines}` — so old
  clients and pre-existing jobs keep working.
* The handle travels in a parallel channel: `X-Document-Artifact-Id`/`-Token`
  on the sync path, `document_artifact_id`/`document_artifact_token` in
  `JobOutcome.metadata` on the async path; authenticated result headers deliver the handles to clients.
* `ExportHtmlRequest` (and therefore the markdown, chunks, block-tree, JSON,
  Docling, MinerU and docx-tree requests) accepts the handle;
  `_load_tree_or_none` prefers the rich document and falls back to the
  text-reconstruction path, so older payloads still export.

## 2. Make completed artifacts authoritative for exports — historical implementation claim

`hydratePagesFromTextArtifact` no longer bails out when any live bbox exists.
It reconciles **only pages with no live blocks**, so real coordinates arriving
over the socket still win and a partial stream cannot drop pages. The old
`-1` sentinel now reports truthfully. `export_notifier` builds export content
from the completed artifact when one exists, falling back to live bboxes only
when there is none, and keeps the "document changed while preparing" guard. The
searchable-PDF path is untouched and still uses separate result bytes.

## 3. Wire the advertised quality options — historical implementation claim (API surface)

`OCRRequest.quality_options` is a typed, validated model covering the options
that actually exist in `core/ocr_quality`. Bad JSON, an unknown field or an
unparseable boolean is a **422 naming the field**, not a silent drop.
`ocr/pipeline_bridge.build_pipeline` builds a real `TrustOrchestrator` through
the existing `build_trust_orchestrator()` factory for both the grounded and
hybrid branches, only when the user opts in. `table_fallback` is now accepted
by the processor allowlist and verified to build.

**Scope note:** the Flutter client has no control that emits this field, so
the trust layer is reachable over the API only. The README says so explicitly
rather than implying a UI toggle. Adding the UI control was not in scope.

## 4. Correct NLLB language resolution — historical implementation claim

Every label the client offers now maps explicitly; `Chinese (Simplified)` →
`zho_Hans` and `Korean` →`kor_Hang` were the two silently-English cases. The
silent English fallback is gone: an unknown target raises
`UnsupportedLanguageError` (a `ValueError`, so it surfaces as a 422 at the edge)
and the service re-checks **before loading model weights** and returns a 400.
`DEFAULT_SOURCE_LANGUAGE` records the pinned English source as a deliberate
scope decision. Only the NLLB route is constrained — the LLM path still accepts
any language name. A test parses the Dart `_languages` list and fails if the
client offers a label the server does not support.

## 5. One resolved broker per worker — historical implementation claim

`JobsPlugin.apply` and `ProgressPlugin.apply` now prefer the `redis_url`/`mode`
handed to them by `worker.boot_worker_context` over a second `load_settings()`,
so the CLI override reaches the queue, the state backend and progress. The
explicit-config-over-environment precedence used by `StateBackendPlugin` is the
pattern both now follow. Redis URLs are still redacted in logs, and the default
non-Redis path never opens a connection.

On the open question, **option (b) was chosen**: startup explicitly marks
interrupted non-terminal work terminal. It is idempotent and safe on every
start, unlike restart recovery, which would have been a distributed-coordination
project.

## 6. Real failed-page and import outcomes — historical implementation claim

`failed_pages` is propagated from the core run into `JobStatusResponse`,
`JobListItemResponse` and the `X-Failed-Pages` header instead of the hardcoded
`[]`. The glossary client no longer refreshes blindly: a queued import is
followed through `GET /api/process/status/{job_id}` via a new
`GlossaryRepository.getImportJobStatus`, refreshing on success and surfacing the
worker's error on failure — including `cancelled` and a poll timeout, which is
reported as still-running rather than as success.

## 7. Make benchmark scoring exercise the real output — historical implementation claim

`confidence_eval` now runs the real pipeline and scores the canonical exported
Markdown (`build_document_export(export_format="markdown", document=...)` →
`render_markdown`) rather than joining bbox/text pairs. `blocks_to_markdown` is
retained only as the ground-truth fallback and is labelled as such per record.
Results are emitted as machine-readable JSON with per-run records, failures,
provenance (model, settings, prompts, hardware, latency, corpus identity and
raw-output locations) and a `status` of `complete`/`partial`. The process exits
**non-zero** for an incomplete requested run; `--allow-partial` is the only
opt-out. Competitor rows are marked `illustrative`; dataset downloaders stay
stubbed and are reported unavailable.

## 8. Close format and release promises — historical implementation claim for formats, DOCUMENTED for release

* **BMP/TIFF are now supported throughout** rather than the promise being
  narrowed: added to the content-type allowlist, the magic-byte table
  (little/big-endian TIFF, BigTIFF, BMP) and the client dropzone, with a single
  `_SUPPORTED_UPLOAD_FORMATS` constant feeding the 415 message so the three
  sides cannot drift again. Covered at the sniffer level, the allowlist level
  and the real HTTP route.
* **Storage is durable.** `artifact_base_dir` defaults to a per-user data
  directory (`%LOCALAPPDATA%\OmniScribe`, `~/Library/Application Support/OmniScribe`,
  `$XDG_DATA_HOME/omniscribe`) instead of the system temp dir, and degrades to
  temp only when no per-user location exists at all. This is a location change,
  **not** a migration: state written by an older build is left on disk untouched.
* **Multilingual proof.** The embedder test now asserts exact round trips per
  script. Latin, CJK, Cyrillic and mixed blocks of those pass. **Arabic does
  not round-trip** — it extracts as presentation forms, so search/copy of the
  logical string fails. That is pinned by two `strict=True` xfail markers (a
  future fix must remove them deliberately) and the embedder now emits a
  once-per-process warning instead of letting a green write read as complete
  recognition. Fixing it needs a shaper (e.g. libraqm) in the embed path.
* **Release assets are documented, not faked.** The release workflow really
  does publish wheel/sdist only, so "add reproducible Windows asset builds" was
  answered with the other option the finding allows: `docs/deployment/windows-bundle.md`
  gained a **Publishing release assets** section with the exact manual build,
  smoke (`scripts/build_windows.py --smoke`), client build and
  `gh release upload` steps, the release gate, and an explicit rule that source
  changes do not update an existing binary. The README and the install path
  both now warn that a release page without an `.exe` shipped without the
  Windows assets. No unverifiable CI pipeline was added.

## Contract snapshots updated

`JobStatusResponse` gained `document_artifact_id` and the export request models
gained the two document handle fields, so the three contract tests that pin
these shapes were updated deliberately and `tests/openapi.json` was regenerated
per that test's own instructions.

## Verification after remediation

| Check | Command | Result |
| --- | --- | --- |
| Type checking | `mypy src` | no issues in 222 source files |
| Lint | `ruff check .` | all checks passed |
| Format | `ruff format --check .` | 507 files already formatted |
| Fast suite | `pytest -m 'not slow and not slow_dataset'` | **2796 passed, 18 skipped, 13 deselected, 2 xfailed, 0 failed** in 145.89 s |
| Client tests | `flutter test` | 426 passed |
| Client analysis | `flutter analyze` | 24 issues, all pre-existing, none in files this pass touched |
| Feature boundaries | `python client/tool/check_feature_layout.py` | passed — 169 Dart files, 31 provider declarations |
| Harness fails correctly | `confidence_eval.py --api-base http://127.0.0.1:1/v1` | `status: partial`, all runs recorded as failures, **exit code 1** |

The initial test baseline was 2,675 passed. Separate lint and format checks
failed (`tests/test_security.py` B010 and six unformatted test files); those
checks were fixed in this historical remediation pass.
The 2 xfails are the deliberate Arabic text-layer markers described above.

## Gates open at the historical remediation checkpoint
- **Real Redis**: concurrent jobs, throughput, and worker-restart recovery
  against a live Redis. The resolution rule is proven with fakes; the
  deployment behaviour is not.
- **Real OCR accuracy**: no model downloads, so no table-reconstruction or
  quality claim was measured. The benchmark harness is fixed to measure the
  right artifact and to fail honestly, but it has **never produced a complete
  measured run** here.
- **Release build**: no Windows bundle build, no Flutter Windows/web build, and
  no signed or checksummed asset was produced. No release was cut and no
  existing remote asset was inspected.
- **Arabic text layer**: the gap is now visible and pinned, not fixed.
- **All-pages-failed runs** still return a successful empty PDF. Failed pages
  are visible via `failed_pages` and the harness treats them as failures, but
  the success contract was deliberately left alone rather than changed without
  a decision about partial-success semantics.
- **End-to-end scanned upload → searchable PDF → export journey** on Windows and
  web (sequence step 3) was not exercised on real hardware.

## Independent revalidation (2026-10-03)

**Verdict: not satisfactory as a complete closeout.** The remediation adds useful
implementation and regression coverage, but several original acceptance
conditions remain unmet and two runtime regressions were reproduced. This
validation changed documentation only; it did not fix product code.

### Status of every finding

| Finding | Current status | Acceptance assessment |
| --- | --- | --- |
| 1. Rich document persistence | Partial | Serialization, sync/async storage, capability checks and native DOCX table retention pass. Scored trust does not propagate into an existing tree, and Flutter does not carry the rich artifact handles into export requests. |
| 2. Artifact-authoritative exports | Partial | Successful text-artifact reads include missing text. Failed/unreadable artifacts silently fall back to potentially partial or empty previews; existing pages with some blocks are not reconciled. Structured client exports still use text-only reconstruction. |
| 3. Quality controls | Partial | Validated API options, trust construction and table fallback pass focused tests. The processor → trust → stored artifact → export contract loses scored trust on existing trees. API-only UI scope is documented accurately. |
| 4. NLLB target language | Mostly satisfactory | All client labels map correctly, and unknown ordinary names fail. Arbitrary code-shaped strings such as `Fake_Target` bypass supported-language validation. English source remains explicitly scoped. |
| 5. Redis configuration/restart | Not satisfactory | Direct worker CLI URL override passes. Shipped Loader configuration leaves API progress in in-process mode while jobs use Redis; startup reconciliation can cancel a still-live peer's jobs and delete its staged inputs. Real Redis recovery/throughput remains unverified. |
| 6. Partial failure/import feedback | Partial | Failed page IDs reach status/history. Healthy glossary polling responses fail client JSON parsing because nullable `error` is treated as required. All-pages-failed OCR can still return successful empty output. |
| 7. Benchmark alignment | Harness satisfactory; measurement incomplete | Canonical export scoring, machine-readable provenance and nonzero incomplete-run exit pass. No complete live measurement, independently reviewed multilingual reference corpus, public dataset downloads or comparable competitor run proves the quality goal. |
| 8. Format/release promises | Partial | BMP/TIFF, durable storage defaults and manual release disclosure pass their relevant checks. Arabic logical search/copy remains broken; current Windows/web build, bundle smoke and full scanned end-to-end journey are unverified. |

### Remaining defects and minimum closing checks

1. **P1 — Scored trust remains absent from canonical structure.**
   `core/workflows/base.py:227-232` replaces scored page blocks while retaining
   `document_result.tree`. Processors create that tree before scoring, and
   `plugins/documents/service.py:84` prefers it for exports. A direct real-core
   probe produced page `trust_score=0.2` but exported structural
   `trust_score=None`. Synchronize scored fields into existing tree nodes
   without discarding tables/sections, then verify processors → scoring →
   persisted artifact → export together rather than as separate unit checks.

2. **P1 — Flutter does not use the rich artifact.**
   The client has no `document_artifact_*`/`X-Document-Artifact-*` fields in its
   OCR result, orchestration state or export models. Requests at
   `client/lib/features/documents/export_notifier.dart:268`, `:279` and `:292`
   pass only text handles, so `plugins/documents/routes.py:132` reconstructs
   the old heuristic tree. Markdown remains a joined-string export. Add the
   handle to the existing flow and assert real geometry/tables survive a
   client request. `server.py:208` also omits the new document/failed-page
   headers from CORS exposure, blocking their use by cross-origin web clients.

3. **P1 — Artifact-read failure silently revives incomplete exports.**
   `_resolveExportText` at `export_notifier.dart:137` catches all errors and
   falls back to live boxes. Validation accepts a handle even with no live
   text, and preparation never checks the resulting content. Test a missing
   artifact with a partially delivered document and with no blocks; fail
   visibly unless a complete alternative representation is established.
   `workstation_notifier.dart:465` also skips pages with any live block, so
   partial delivery within a page remains incomplete in the preview.

4. **P1 — Glossary polling rejects healthy responses.**
   `client/lib/features/glossary/glossary_models.dart:271` reads optional
   `error` with required `jsonString`, which throws on null
   (`core/serialization/json_fields.dart:4-5`). Backend `JobStatusResponse`
   legitimately returns `error: null` for pending/complete jobs. Current
   polling tests construct `GlossaryJobStatus` directly and bypass JSON
   parsing. Add actual pending/complete/null-error repository/parser tests.
   The URL-import modal also closes without checking recorded errors
   (`glossary_screen.dart:594-630`); test that branch with a worker failure.

5. **P1 — Real Loader boot disagrees about Redis mode.**
   `ProgressSchema.mode="inprocess"` (`plugins/progress.py:632`) is inserted
   by `harness/loader.py:437` for the shipped progress row that omits mode.
   `resolve_broker_config` (`plugins/state_backend.py:129-131`) treats this
   default as an explicit override of Redis runtime settings. An offline
   `create_app`/`TestClient` reproduction using the shipped tree and mocked
   Redis `open` calls confirmed `RedisJobQueue` alongside
   `ProgressService._redis_mode=False`. Preserve omitted-mode semantics and
   test the actual Loader tree, not only direct `Context.plugin` calls.

6. **P1 — Startup reconciliation assumes every peer is dead.**
   `plugins/jobs.py:450-460` transitions every nonterminal record and deletes
   queued inputs without proving ownership/death. A two-queue/shared-backend
   probe confirmed queue B cancels queue A's live queued job and deletes its
   input while A retains the executable payload. Enforce a single in-process
   owner or reconcile only proven abandoned work. Verify a live peer is
   unaffected as well as an interrupted process being terminalized.

7. **P2 — Code-shaped invalid NLLB targets bypass validation.**
   `core/translate/nllb.py:94` accepts any underscore/uppercase string.
   `NllbRequest(target_language="Fake_Target")` and the resolver both accept
   it, allowing model loading before a later failure. Validate against
   supported model codes while preserving the now-correct UI aliases.

8. **Remaining product/release gates are not closed by documentation.**
   Arabic and mixed Arabic PDF extraction are strict expected failures, not
   fixes. No complete live OCR/export/accuracy run, real Redis concurrency/
   restart test, or current bundle/device smoke run was verified. The manual
   release procedure also uses different server asset filenames from the
   build script; make renaming/checksum steps explicit before executing it.

### Initial verification (2026-10-03) and limits

| Check | Result |
| --- | --- |
| `ruff check src tests scripts --no-cache` | Passed |
| `ruff format src tests scripts --check --no-cache` | Passed; 465 files already formatted |
| `mypy src` with workspace cache | Passed; 222 source files |
| `python client/tool/check_feature_layout.py` | Passed; 169 Dart files, 31 unique providers |
| Main focused backend acceptance suite | 165 passed, 2 expected Arabic failures |
| Full offline fast suite | **2,793 passed, 3 failed, 18 skipped, 13 deselected, 2 xfailed** in 152.88 seconds |

The three full-suite failures are
`tests/plugins/test_boot_config.py::{test_shipped_cordis_yml_mounts_full_service_tree,test_patch_file_overrides_row_config,test_env_override_seeds_quality_defaults}`.
They attempt SQLite startup using the new default
`%LOCALAPPDATA%\OmniScribe`, outside this writable workspace,
and fail with `sqlite3.OperationalError: unable to open database file`.
This is an environment-dependent storage/isolation failure, not proof of the
separately reproduced Redis defects. No permission bypass or outside-directory
modification was attempted to repair it. Isolate those tests' storage in their
own workspace fixture and rerun them before claiming the entire gate passes.

Full-suite command:
`rtk .venv/Scripts/python.exe -m pytest -m 'not slow and not slow_dataset' -q --basetemp=.assessment-tmp/revalidation-full-20261003 -o cache_dir=.assessment-tmp/revalidation-full-cache-20261003`.
Independent focused reviewer counts overlap with the main suites and must not
be added to the totals above.

Flutter's earlier recorded 426 passing tests and 24 analysis issues were not
rerun in this validation; they do not cover the broken actual glossary JSON
contract or artifact-read completeness. A local model discovery endpoint was
reachable, but the backend on port 8000 and Redis on port 6379 were not running.
Model discovery alone establishes no OCR, device or release acceptance.

### Closeout order

Fix the P1 export/trust/glossary and Redis boot/ownership defects first, with
the combined boundary checks listed above. Then close the sandbox-independent
boot test gate, run Flutter contract/device checks, and execute the live OCR,
Redis and release acceptance journeys. Keep items marked partial/unverified
until their stated acceptance conditions actually pass.

