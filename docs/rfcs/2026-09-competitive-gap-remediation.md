# RFC 004 — Competitive gap remediation (RAG output, ingest, scale, benchmarks, tables)

| Field | Value |
| --- | --- |
| **Author** | OmniScribe gap analysis session (2026-09-07), benchmarked vs Unstructured.io / Docling / MinerU / Marker |
| **Status** | **Implemented** (2026-09-07) — closeout pending (see §10) |
| **Target** | v0.3.1+ strategic roadmap |
| **Refs** | RFC 003 (Redis state backend), `docs/audits/2026-09-04-five-lens-audit.md` (audit remediation — distinct from this doc) |

## 1. Background

The 2026-09-07 gap analysis (vs Unstructured.io / Docling / MinerU / Marker) found OmniScribe wins on quality infrastructure (trust layer, repair loop, translation with glossary RAG, sandwich PDF, Flutter client) but loses on five dimensions that define the "document ETL" market RAG builders buy into. Root cause is positioning, not architecture: OmniScribe grew as a quality-first OCR workstation while the reference competitors evolved as RAG-pipeline libraries. The IR is already element-rich (`DocumentBlock{bbox, kind, confidence, reading_order, trust_score}`), so most gaps are **output-shaping**, not re-architecture.

**Who is affected:** RAG builders evaluating OmniScribe against Unstructured/Docling (currently excluded by ingest + output), Profile 4 LAN deployers needing throughput, and the maintainer's positioning story.

## 2. Pre-implementation state (as of 2026-09-07, before R1–R5)

| Capability | State (before) | Evidence |
| --- | --- | --- |
| Ingest | PDF + 8 image MIMEs only | `plugins/ocr/services/content_sniff.py:23-62` |
| Exports | DOCX, HTML, JSON block-tree, sandwich PDF — no markdown, no chunks | `core/writers/` (5 modules), `plugins/documents/routes.py:76-205` |
| Chunking | None; `core/recall/` is OCR box recall, not text chunking; only splitter was the translate `_Chunker` | `core/recall/*`, `core/translate/workflow.py` |
| Element taxonomy | `kind` + section metadata exist, but labels were not RAG-conventional | `core/document.py:43-56`, `core/processors/section.py:19` |
| Scale | Single-worker in-process `JobQueue`; Redis state backend ships (RFC 003), workers did not scale horizontally | `plugins/jobs.py:1-5` |
| Benchmarks | Homegrown fixtures only; public datasets license-blocked (`slow_dataset` no-op) | `docs/outstanding-work.md` §6 |
| Tables | VLM/processor output only; no dedicated table-structure model fallback | `core/processors/table.py:32` |

**Post-implementation (2026-09-07):** All five workstreams (R1–R5) are
implemented. Markdown writer + section-aware chunker (R1), digital-doc
readers for DOCX/HTML/MD (R2), Redis multi-worker dispatch via
`RedisJobQueue` (R3), `--score-markdown` benchmark metrics (R4), and
`TableFallbackProcessor` (R5) all ship. See §4 for details and §10 for
remaining closeout items.

## 3. Goals

Close all five gaps as tiered workstreams, each independently shippable and opt-in by default.

**Non-goals:** connectors ecosystem (S3/SharePoint/vector-DB destinations — watch item, see §4.6), embedding/vector-store output layer, serverless hosting, Celery, client rewrite.

## 4. Workstreams

### R1 (P0) — RAG-ready output layer: markdown writer + section-aware chunker

The entry ticket. Unstructured's entire value prop is LLM-ready chunks; OmniScribe cannot compete for that use case without this.

- **`core/writers/markdown.py`** — `MarkdownWriter` implementing `DocumentExportProtocol` (`exporter_base.py`). Sections → `#`/`##` via section metadata; tree tables → GFM tables; figures → `![alt](artifact-ref)`; equations → `$$…$$` when LaTeX/MathML is available.
- **`core/chunking/chunker.py`** — `SectionAwareChunker` over the block tree. Output chunk: `{chunk_id, element_type, text, section_path, page_span, bbox[], block_ids[], trust_score(min)}` — provenance-preserving chunks, which Unstructured does not offer. Knobs: `max_chars=1200`, `overlap_chars=120`, `min_chars=200`; split preference: section boundary > paragraph > block; tables stay atomic unless oversized.
- **Taxonomy mapping** — `element_type` ∈ `title | narrative | table | figure | formula | list_item`, mapped from `DocumentBlock.kind` + section/structure processor metadata.
- **Routes** (documents plugin, artifact-backed like the existing exports): `GET /api/export/markdown`, `GET /api/export/chunks?max_chars=&overlap=`.
- **Tests:** chunker property tests (no chunk > `max_chars` unless a single block exceeds it; overlap only within a section; boundaries at block edges), writer tests, router contract tests, OpenAPI snapshot update.
- **Effort:** human ~3 days / CC ~1 day.

### R2 (P0) — Digital-document ingest fast path (DOCX, HTML, MD first)

- **`core/readers/`** — per-format readers producing `DocumentResult` with synthetic pages (one per logical unit: section/slide/sheet); `confidence=1.0`, `trust_flags={"source":"digital"}`; zero Surya/VLM calls.
- **`content_sniff.py`** — allowlist + MIME map extension; the OCR plugin branches to readers before engine dispatch.
- Wave 1: DOCX (`python-docx`), HTML (`selectolax`), Markdown (`mistune`). Wave 2 (separate RFC): PPTX, XLSX, EML.
- **Tests:** per-format fixtures, export parity (DOCX in → DOCX out), engine-not-called assertion.
- **Effort:** human ~4 days / CC ~1.5 days.

### R3 (P1) — Multi-worker dispatch on Redis (completes RFC 003 §12 "scale")

- `OMNISCRIBE_JOBS_MODE=inprocess|redis` (default `inprocess`). Redis mode: `omniscribe:jobs:queue` ZSET + atomic claim Lua (ZPOPMIN + status guard, mirroring `consume_channel`), visibility timeout + heartbeat, `omniscribe-worker` console script (N asyncio workers), worker→Redis progress frames fanned out to WebSockets by the API pod's `ProgressService`.
- Sized as two 1–3 day units: **R3a** queue data model + atomic claim; **R3b** worker script + progress fan-out.
- **Tests:** fakeredis multi-worker race tests; chaos extension of `tests/plugins/test_jobs_chaos.py`.
- **Effort:** human ~5 days total / CC ~2 days.

### R4 (P1) — External benchmark credibility (depends on R1)

- Extend `scripts/confidence_eval.py` + `scripts/fetch_datasets.py` to run OmniDocBench-class end-to-end PDF→markdown scoring; publish a results table (`docs/benchmarks.md` + README), nightly workflow artifact. Gated on the same upstream license review as `slow_dataset`.
- **Effort:** human ~3 days / CC ~1 day.

### R5 (P2) — Table-structure fallback

- Dedicated detector (Table Transformer-class) as fallback when VLM table confidence < threshold; selectable via `document_processors` + repair-loop trigger. Model choice pinned at execution time.
- **Effort:** human ~4 days / CC ~1.5 days.

### Watch (not planned): connectors + embedding output

Revisit when a deployment asks; R3 is the technical unblocker. Deliberately out of scope: competing with a funded platform on connectors is not the local-first wedge.

## 5. Dependency graph

```
R1 (RAG output) ──> R4 (benchmarks score markdown)
R2 (ingest)      (independent)
R3 (scale)       (independent)
R5 (tables)      (after R1+R2 ideally)
```

**Rationale:** R1/R2 first — they change what the product *is* to RAG buyers. R4 needs R1's markdown. R3 ships when Profile 4 scale demand materializes (RFC 003 groundwork exists). R5 last: VLM tables work today; this is robustness, not capability.

## 6. Definition of done

1. `GET /api/export/markdown` + `GET /api/export/chunks` return valid payloads for every `examples/` fixture (route contract tests).
2. Chunker properties hold under hypothesis testing (bounds, overlap, boundaries).
3. DOCX/HTML/MD upload processes end-to-end with zero Surya/VLM calls (asserted via monkeypatched engine).
4. `OMNISCRIBE_JOBS_MODE=redis`: 2 workers × 10 concurrent jobs, zero loss (fakeredis + real-Redis smoke).
5. One published benchmark run on a public dataset subset.
6. Table fallback ships default-off and triggers only below the confidence threshold.
7. Fast gate stays green (`ruff`, `mypy`, `pytest -m "not slow"`, coverage ≥ 80).

## 7. Do not touch

Trust-orchestrator fail-open contract; recall kill switches; Cordis boot order; ASGI middleware triad; `progress.py` cross-loop marshalling path (`test_foreign_loop_send_is_marshaled_to_accept_loop` is the contract).

## 8. Rollback

Doc-only RFC. Each workstream is additive and opt-in (new routes/readers join the allowlist only after fixtures pass; R3 behind an env knob like the Redis backend; R5 default-off processor). Revert = remove the module + route row.

**Total effort:** ~19 human-days / ~7 CC-days across 5 shippable units.

## 9. Related

- RFC 003 — Redis state backend (R3 builds on its key layout and `consume_channel` Lua pattern)
- `docs/audits/2026-09-04-five-lens-audit.md` — audit remediation (code-quality track, distinct from this competitive track)

## 10. Closeout status (2026-09-07 audit)

All five workstreams are implemented and the new-surface test suites
pass (49/49: chunking, readers, markdown writer, Redis jobs, digital
ingest, export routes). Remaining closeout items:

1. **Real-Redis multi-worker smoke** (DoD 4 second half): extend
   `scripts/dev_redis_smoke.py` with `jobs_mode=redis` plus 2 workers ×
   10 concurrent jobs; blocked on the RFC 003 §12 deployment-shape
   decisions (see `docs/outstanding-work.md` §2).
2. **R4 publication half**: add a `--score-markdown` step to
   `.github/workflows/nightly.yml`; add a README benchmarks section;
   annotate `docs/benchmarks.md`'s competitive-positioning table with
   provenance (baselines are in-repo fixtures; competitor numbers are
   illustrative, not measured runs).
3. **OmniDocBench public-dataset run** (DoD 5 as written): gated on the
   upstream license review (`scripts/fetch_datasets.py` exit-77
   protocol).
4. ~~**Table-fallback tests**~~ ✅ closed 2026-09-07:
   `tests/core/processors/test_table_fallback.py` (7 tests) covers the
   confidence-gated fallback behavior.
5. **Full fast gate** (`ruff`, `mypy`, `pytest -m "not slow"`) to
   confirm DoD 7; ruff and mypy are clean as of the 2026-09-07
   remediation pass — only the full test-suite run remains.
