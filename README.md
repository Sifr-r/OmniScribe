# OmniScribe

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Web_UI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-purple?style=for-the-badge)](LICENSE)
[![AGPL--3.0 (PyMuPDF)](https://img.shields.io/badge/AGPL--3.0-PyMuPDF-orange?style=for-the-badge)](https://artifex.com/licensing/)

OmniScribe turns scanned PDFs and photos into searchable, selectable PDFs. Everything runs on your machine — no cloud OCR, no signup, no API keys. The local vision model is yours to choose (LM Studio, Ollama, or any OpenAI-compatible server).

## Trust & Privacy

OmniScribe is local-first. By design:

- **No telemetry, no analytics, no phone-home.** The server makes no outbound calls except to the VLM endpoint you configure in `LLM_API_BASE`.
- **No cloud OCR.** OCR runs against a local VLM (LM Studio, Ollama, or any OpenAI-compatible server) you bring. If you point `OMNISCRIBE_LLM_API_BASE` at a hosted provider, your documents and extracted text leave your machine — see [DEPLOYMENT.md](docs/DEPLOYMENT.md) §"Third-party VLM" for the privacy warning.
- **No upload, no signup, no API keys from us.** Bearer tokens for the LAN / public-internet profiles are tokens you generate yourself with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`; the server ships with no default.
- **Open-source under MIT** (this project) + **AGPL-3.0** (the bundled PyMuPDF). See [SECURITY.md](SECURITY.md) for the full threat model and the [Third-Party Software Notices](#third-party-software-notices) below for the PyMuPDF license details.

## Before you start

OmniScribe needs a local **OpenAI-compatible vision model server** — by default, [LM Studio](https://lmstudio.ai). Install it, open the **Search** tab, pick a vision model, then start the local server in the **Developer** tab (it binds to `http://localhost:1234/v1`).

A starting-point model table:

| Hardware tier | Suggested model | Approx. VRAM |
| --- | --- | --- |
| 8 GB GPU, no CPU fallback | Qwen2.5-VL-7B-Instruct (Q4) | ~6 GB |
| 16 GB GPU | Qwen2.5-VL-7B-Instruct (Q8) | ~9 GB |
| 32 GB GPU | Qwen2.5-VL-72B-Instruct (Q4) | ~24 GB |
| CPU only (slow) | Qwen2.5-VL-3B-Instruct | ~4 GB RAM |

If you already run an Ollama or other OpenAI-compatible server, set `LLM_API_BASE` in your environment to point at it — OmniScribe will discover the model list automatically.

## Features

- **Format Support**: PDFs and images, including JPEG, PNG, BMP, WebP, TIFF, and AVIF.
- **Searchable Output**: Sandwich PDFs with the original page image plus hidden searchable text.
- **Hybrid OCR**: Surya layout detection, VLM OCR, DP alignment, optional refine, and searchable PDF embedding.
- **Grounded OCR**: Bbox-native VLM path for models that return positioned text directly.
- **Local Document Intelligence**: Optional web/API processors for preprocessing (including page cleanup and handwriting preprocessing), reading order, quality analysis, structure, sections, layout enrichment, table extraction, quality routing, metadata reports, and structured exports.
- **Provider Management**: Multi-format provider configuration (OpenAI, Anthropic, Ollama compatible), automatic env-var discovery, and runtime switching.
- **Voice Transcription**: Local and API-based speech-to-text audio transcription via `/api/transcribe`.
- **Flutter Client**: Cross-platform desktop / mobile client built with Flutter + Riverpod (light/dark themes, Material 3, animated transitions), page selection, WebSocket progress, preview, translation, extraction, transcription, glossary browsing, and export to the OmniScribe FastAPI server.

## Installation

```bash
git clone https://github.com/Sifr-r/OmniScribe.git
cd OmniScribe
uv sync --extra web --extra preprocessing
```

If anything goes wrong, run `make doctor` — it reports Python version, `uv` on PATH, Redis reachability, and whether your VLM endpoint is actually responding on `127.0.0.1:1234`.

For asynchronous translation:

```bash
uv sync --extra web --extra preprocessing --extra async-translation
```

If you also want the translation lexicon (LanceDB-backed vector store
for domain terminology), add the `memory` extra. The async-translation
extra alone does **not** install ChromaDB or sentence-transformers,
so it stays light (no torch / no multi-GB ML stack):

```bash
uv sync --extra web --extra preprocessing --extra async-translation --extra lexicon
```

> **Upgrading from a pre-LanceDB version?** Migrate the legacy
> `glossary_library/library.json` + `chroma_db/lanes_lexicon`
> pair to the new LanceDB store with the `omniscribe-migrate-lexicon`
> console script (the server itself does not auto-migrate on boot):
>
> ```bash
> uv run omniscribe-migrate-lexicon --dry-run      # preview the plan
> uv run omniscribe-migrate-lexicon               # run (idempotent)
> uv run omniscribe-migrate-lexicon --verify-only # check the result
> uv run omniscribe-migrate-lexicon --strict      # exit 2 on empty store
> ```
>
> Exit codes: `0` = success (including a valid empty `lexicon.lance`
> after `--verify-only`); `1` = migration error; `2` = `--strict` only
> — empty live store when a backup manifest reports glossaries.

Real OCR requires an OpenAI-compatible VLM endpoint. The local-development default is LM Studio at `http://localhost:1234/v1`.

## Supported platforms

| Platform | Backend (Python) | Frontend (Flutter) | Binary install |
| --- | --- | --- | --- |
| Windows 10/11 | ✅ | ✅ Windows desktop | ❌ *deferred to v0.3+* (see [RFC 001](rfcs/2026-09-end-user-install.md)) |
| macOS 13+ | ✅ | ✅ macOS desktop | ❌ *deferred to v0.3+* |
| Ubuntu 22.04+ / Debian 12+ | ✅ | ✅ Linux desktop | ❌ *deferred to v0.3+* |

The **source install** above is the supported v0.2.0 path on every platform
— a single-binary distribution was being scoped for v0.2.0 but is
deferred to v0.3+ (blocked on a PyInstaller + `anyio` bundling
issue). The 12-step source install is documented in
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) and is the path
[CONTRIBUTING.md](CONTRIBUTING.md) recommends for new contributors.
Docker is supported on all three (see [DEPLOYMENT.md](docs/DEPLOYMENT.md)
Profile 3).

## Flutter Client

Start the backend (it serves the FastAPI surface that the Flutter client talks to):

```bash
uv run omniscribe-server --port 8000
```

Then, in another terminal, run the Flutter client:

```bash
cd client
flutter pub get
flutter run
```

The Flutter Client is the supported user workflow. Advanced document intelligence is exposed through the client's Advanced Configuration panel and FastAPI request fields; the user-facing CLI script has been deprecated.

The Advanced Configuration panel includes:

- **Preprocess Pages** with orientation detection, deskew, denoise, contrast normalization, and crop cleanup.
- **Reading Order** for deterministic top-to-bottom, left-to-right block ordering.
- **Quality Analysis** for page-level density, block counts, and advisory findings.
- **Structure Analysis** for headings, paragraphs, list items, key-values, table candidates, and empty blocks.
- **Section Analysis** for grouping content under detected headings across pages.
- **Layout Enrichment** for headers, footers, captions, page numbers, figures, title blocks, and body regions.
- **Table Extraction** for deterministic table reconstruction from OCR boxes.
- **Quality Routing** for recording local routing recommendations from quality findings.

The **OCR Quality Trust Layer** (`omniscribe.core.ocr_quality`) ships in
Phase 1 + Phase 2 + Phase 3 as an additive layer over both engines and
the `/api/process` route. Every sub-module (watermark, script detection,
hallucination guard, confidence calibration) is **off** by default and
fails open, so existing callers see no behavioural change. Each
`DocumentBlock` carries optional `trust_score` and `trust_flags` fields
(always `None` until the layer is enabled). The runtime orchestrator is
plumbed through `OCRPipeline(trust_orchestrator=...)`; engines apply
it per page (HybridEngine decodes the page image from base64,
GroundedEngine passes `None`). The `/api/process` route accepts a
JSON-encoded `quality_options` form field and forwards
`trust_model_id=settings.model` to calibration. Responses include a
compact `X-Document-Trust` JSON header (block count, score histogram,
flagged count, per-flag counts) emitted only when at least one block
carries a `trust_score` — keeping the no-orchestrator default
byte-identical. Phase 3 ships `scripts/calibrate_model.py` (Platt
scaling via pure-numpy gradient descent with backtracking line-search)
and a pre-trained `qwen2_5_vl_72b.json` that drops ECE by 21.6% vs.
raw confidence on the synthetic fixture. The `slow_dataset`-gated
regression tests run on OCR-Quality and KIE-HVQA fixtures via the
nightly workflow. See [ARCHITECTURE.md](ARCHITECTURE.md) for the flag
reference, fallback semantics, and dataset attribution.

OCR responses include token-bound text artifact headers. When processor metadata exists, responses also include `X-Document-Metadata-Artifact-Id` and `X-Document-Metadata-Artifact-Token`; fetch `GET /api/metadata/{artifact_id}` with the token to retrieve compact page/block metadata. Use `POST /api/export/document` to create token-bound JSON, Markdown, plain text, Docling-compatible, or MinerU-compatible export artifacts. `POST /api/export/docx` produces a `.docx` from Markdown page text. `POST /api/extract` runs structured data extraction against OCR text using a built-in template (`invoice`, `resume`, `academic`, `table`, `table_extraction`, `custom`) or a custom prompt.

### Confidence scripts

`scripts/confidence_eval.py` runs the hybrid and grounded paths against the `examples/` PDFs and reports per-document block recall, IoU, and text similarity against hand-built ground-truth fixtures. `scripts/confidence_image.py` does the same for a single image (defaults to `examples/image.avif`). Both assume LM Studio / Ollama is serving the target model at `--api-base`.

## Async Translation

`POST /api/translate/async` dispatches tree-aware translation on the
server's in-process harness JobQueue (single worker); poll
`GET /api/translate/status/{job_id}` for the client status vocabulary.
Translated output is stored as a token-bound text artifact and fetched
via `GET /api/text/{artifact_id}` — no Celery worker is needed; the
compose stack runs `api` + `redis` only.

## Validation

```bash
uv run pytest
uv run pytest -m "not slow"
uv run pytest -m slow
uv run ruff check src tests
uv run ruff format src tests --check
uv run mypy src
cd client && flutter pub get && flutter analyze && flutter test && flutter build web --release
```

Slow tests load Surya and may download its model on the first run.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for pipeline details, extension points, and staged document-intelligence notes.

## Third-Party Software Notices

OmniScribe is released under the [MIT License](LICENSE). It depends on
PyMuPDF (Artifex Software) for PDF rendering and sandwich-PDF embedding.
PyMuPDF is dual-licensed under AGPL-3.0 and a commercial license; the
upstream library itself is AGPL-3.0. The bundled PyMuPDF is for use by
end users — internal OCR, personal use, and AGPL-compatible use cases.

**If you distribute OmniScribe (or a derived product) outside your
organization in a way that is *not* AGPL-3.0-compatible, you are
responsible for obtaining a commercial PyMuPDF license from Artifex
Software.** A one-time warning is also logged the first time this
package processes a PDF, as an in-product reminder. See
<https://artifex.com/licensing/> for license details.

If you want a license-clean default for closed-source distribution,
swap to the Apache-2.0 `pypdfium2` backend and stop importing
`pymupdf`; the OCR pipeline's render-and-embed call sites use a small
PDF-handling surface that pypdfium2 covers with feature parity.

## For developers

- The supported user workflow is the **Flutter client** + **FastAPI server**. The previous in-browser workstation is deprecated.
- The `OCRPipeline` class is importable from `omniscribe.core.pipeline` for in-process programmatic use. No `omniscribe` CLI script is shipped — programmatic use is the supported path.
- See [ARCHITECTURE.md](ARCHITECTURE.md) for the component map and full API surface.

## See Also

- [CHANGELOG.md](CHANGELOG.md) — version history and breaking changes
- [ARCHITECTURE.md](ARCHITECTURE.md) — pipeline, component map, and full API surface
- [DEPLOYMENT.md](DEPLOYMENT.md) — local / LAN / public-internet deployment profiles
- [SECURITY.md](SECURITY.md) — threat model, hardening checklist, vulnerability disclosure
- [AGENTS.md](AGENTS.md) — contributor guide and full env-var reference
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — first-run error guide
- [rfcs/2026-09-end-user-install.md](rfcs/2026-09-end-user-install.md) — proposed path to a single-binary distribution (Phase 4)

_Last updated: 2026-09-05_
