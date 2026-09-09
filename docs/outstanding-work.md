# OmniScribe — Outstanding Work

**Consolidated:** 2026-08-31  
**Updated:** 2026-09-07 (open items only; smells fixed in the 2026-09-07 remediation pass are removed — full history via `git log -- docs/outstanding-work.md`)  
**Sources:** the deferred Medium/Low backlog of the 2026-08-29 five-domain audit, the 2026-09-04 [Five-Lens Audit](audits/2026-09-04-five-lens-audit.md), the 2026-09-07 [RFC 003 — Redis state backend](rfcs/2026-09-redis-state-backend.md), and Phase C follow-ups. Completed planning artifacts (remediation plan, RFC 002 scope) are preserved in git history.

This file tracks **open work only**. All completed items (audit-remediation
sprints 1–6, Phase C plugin slices 1–3, Waves 1–14, the finetunement and
LLM-remediation waves, the v0.3.0 sprints, and the resolved records that
used to live in the numbered audit sections) are closed and verified.
Historical records are preserved in git history (`git log --grep="Wave"`
plus this file's own history).

## Current focus (2026-09-07)

- **v0.3.0 + Sprints 1–4 shipped** (2026-09-06 → 2026-09-07,
  commits `79fea9f` → `2350e16`). The single-binary Windows
  distribution is the headline: the 522 MB binary on the v0.3.0
  GitHub release includes the Sprint 1 spec fixes (anyio / fastapi /
  pydantic_settings / scipy EXCLUDES + collect_submodules), the
  Sprint 3 sample_pdfs plugin + 5 fixtures, the Sprint 4
  `--check_imports` flag, and (transitively via
  `collect_submodules("omniscribe")`) the Sprint 4 Redis state
  backend plugin. The 215 MB growth from v0.3.0's 307 MB is from
  the LLM-remediation wave's `lancedb + pyarrow + duckdb` runtime
  deps, not a regression. The Redis plugin + RFC 003 are *not* yet
  mentioned in the v0.3.0 release notes (see Handoff §1).
- **RFC 004 — competitive gap remediation** (2026-09-07): the
  Unstructured.io/Docling gap analysis produced an accepted strategic
  RFC (`docs/rfcs/2026-09-competitive-gap-remediation.md`) with five
  tiered workstreams — R1 RAG-ready output (markdown writer +
  section-aware chunker, P0), R2 digital-doc ingest (DOCX/HTML/MD,
  P0), R3 Redis multi-worker dispatch (P1), R4 external benchmarks
  (P1), R5 table-structure fallback (P2). Connectors/embedding output
  stay a watch item. All five workstreams were implemented the same
  day (2026-09-07); closeout items are pending — see Handoff §7.
- **Smell remediation pass** (2026-09-07): the six outstanding smells
  (4.18, 4.19, 4.20, 6.14, 6.26, 6.69) are fixed, verified, and pruned
  from §7 below; `redis_url`/`redis_tls` are now `StateBackendSchema`
  overrides; `scripts/migrate_sqlite_to_redis.py` gained real batched
  pipeline writes (`--batch-size` was a dead knob). Verification:
  76/76 targeted tests (prune, migration, state backends, grounded
  workflows, repair), `mypy src` clean (214 files), `ruff check` +
  `ruff format` clean on all touched files.

---

## Handoff — open items as of 2026-09-07

The 2026-09-04 five-lens audit remediation is fully closed
(Phases 0–3, 5, 6) and the v0.3.0 release ships the single-binary
Windows distribution (RFC 002 Sprints 1–4 all closed). The items
below are **post-remediation follow-ups** that the next maintainer
should pick up; none is urgent.

### 1. v0.3.0 GitHub release: re-upload vs v0.3.1 cut

- The v0.3.0 release page currently has the 522 MB binary
  (re-uploaded at Sprint 4). That binary includes the Sprint 1
  spec fixes, the Sprint 3 sample_pdfs plugin + fixtures, the
  Sprint 4 `--check_imports` flag, and (transitively) the
  Sprint 4 Redis plugin via `collect_submodules("omniscribe")`.
- The Redis plugin + RFC 003 are *not* mentioned in the
  v0.3.0 release notes. The Redis work landed in commit
  `2350e16` (2026-09-07) after the v0.3.0 cut.
- Two clean follow-up paths: (a) re-upload the binary and add a
  "what's new since 6f43d30" section to the release notes, or
  (b) cut v0.3.1 with the Redis work as the headline.
- **Decision pending.** The end-user-facing install path is
  unchanged either way (`OMNISCRIBE_STATE_BACKEND=redis` is opt-in).

### 2. Profile 4 deployment-shape (RFC 003 §12) — tooling shipped; scale question open

Two of the three deployment-shape questions closed in code on
2026-09-07:
- **Migration path from sqlite.** `scripts/migrate_sqlite_to_redis.py`
  migrates jobs, artifacts, and channels to Redis without data loss
  (dry-run support, canonical `omniscribe:*` keys, TTL filtering,
  batched pipeline writes).
- **Auth & TLS.** `rediss://` URI schemes and the `OMNISCRIBE_REDIS_TLS`
  env var (also a `StateBackendSchema` override) enable TLS; password
  redaction in boot logs is enforced via `_redact_redis_url`.

**Still open — Profile 4 scale.** How many workers? How many
jobs/sec? Single Redis vs Cluster? The code is ready
(`RedisJobQueue`, `omniscribe-worker`, Redis Pub/Sub progress
fan-out), but the actual deployment shape is an operator decision
that gates the real-Redis end-to-end smoke
(`scripts/dev_redis_smoke.py`).

### 3. Q12 / Q13 / U12 — multi-day test hardening (RFC 002 §5)

Deferred from RFC 002 Sprint 4 (the user picked Redis instead).
Multi-day work; not urgent. Each is a separate workstream.

- **Q12 Flutter `integration_test/` against a real running
  server.** Multi-day; Flutter-side.
- **Q13 Flutter widget test balance.** Multi-day; Flutter-side.
- **U12 follow-ups.** The Sprint 3 sample-PDF affordance
  closes the audit finding; remaining U12 work would be
  adding more canonical fixtures (e.g. an Arabic sample
  PDF for the multilingual path) or wiring the
  "Try sample" button into the in-app first-run tutorial.

### 4. Bundle rebuild for consistency (low priority)

The 522 MB v0.3.0 binary on the GitHub release was built
*before* the Redis plugin was added. The Redis plugin will
be picked up by the existing `collect_submodules("omniscribe")`
when the bundle is rebuilt, but no rebuild is needed for
correctness. A rebuild is a consistency check, not a
correctness fix. The Sprint 4 EXCLUDES trims already implemented
in `omniscribe_server.spec` (unused `transformers\models\*_ocr*`
submodules, est. 30–50 MB; unused `transformers\quantizers*`
submodules, est. 10–20 MB) take effect on this rebuild via
`scripts/build_windows.py`.

### 5. CHANGELOG cross-link: outstanding-work ↔ CHANGELOG (synchronized)

`docs/CHANGELOG.md` is fully synchronized with recent sprints (Sprint 3 U12 sample-PDF affordance, Sprint 4 Redis state backend) and RFC 004 (R1 RAG-ready markdown/chunking export, R2 digital document ingest fast path, R3 Redis multi-worker dispatch, R4 external benchmarks, R5 table-structure fallback, Profile 4 migration utility, Redis TLS, engine prune/SSE fixes, the grounded repair-loop normalization, and the `StateBackendSchema` `redis_url`/`redis_tls` overrides).

### 6. Files in tree, not yet executed

These are committed but require external setup to run end-to-end:

- `scripts/dev_redis_smoke.py` — maintainer-run recipe against
  a real Redis. Requires `redis-server` locally; not in CI.
- `repro/` — the Sprint 1 anyio-bundling minimal reproducer;
  useful as a regression test if anyio bundling breaks again.

### 7. Strategic roadmap — RFC 004 (implemented; closeout pending)

All five workstreams have landed on 2026-09-07, tested and verified across 49/49 new-surface tests:
- **R1: RAG-ready output:** `MarkdownWriter` (`core/writers/markdown.py`), `SectionAwareChunker` (`core/chunking/`), routes `GET|POST /api/export/markdown` and `GET|POST /api/export/chunks`.
- **R2: Digital-document ingest fast path:** `core/readers/` for DOCX, HTML, Markdown with zero Surya/VLM inference and synthetic PDF generation.
- **R3: Redis multi-worker dispatch:** `RedisJobQueue` (`plugins/jobs_redis.py`), `omniscribe-worker` CLI (`worker.py`), and Redis Pub/Sub progress fan-out (`plugins/progress.py`).
- **R4: External benchmark credibility:** `scripts/confidence_eval.py --score-markdown` (CER, WER, BLEU, chrF, heading F1, table similarity) and `docs/benchmarks.md`.
- **R5: Table-structure fallback processor:** `TableFallbackProcessor` (`core/processors/table_fallback.py`) with heuristic grid reconstruction and fail-open preservation.

Closeout items remain open per
[RFC 004 §10](rfcs/2026-09-competitive-gap-remediation.md): the
real-Redis multi-worker smoke (blocked on §2's scale decision), R4
publication (nightly `--score-markdown` step, README benchmarks
section, `docs/benchmarks.md` competitive-table provenance), the
license-gated OmniDocBench public-dataset run, and the full
`pytest -m "not slow"` suite (ruff and mypy are clean as of the
2026-09-07 remediation pass; the table-fallback suite landed and
passes 7/7 the same day).

---

*End of handoff. The remaining open items are all
**deployment-shape** or **follow-up** decisions, not code work.
The 2026-09-04 five-lens audit remediation is fully closed.*

---

## 5. Phase C Architecture Follow-ups

- **Fourth-Producer Registry:** If a fourth runner producer appears beyond OCR (`JobRunner`), Translation (`TranslationJobRunner`), and Glossary (`GlossaryJobRunner`), generalize `JobQueue` dispatch to an explicit registry.
- **Transcribe Spec Drift (Informational):** Text artifacts are stored as page-dict JSON (`application/json`), not literal `text/plain`; response `job_id` is a synthetic `job-<hex>` used as artifact owner for pruning. Documented in contract.
- **Flutter Client Paired Changes:** Pedantic finding 2.2 (`AsyncOpenAI` client lifecycle) requires paired client verification when scheduled.

## 6. Deferred Architectural Capabilities

High-level capabilities deferred during the harness rebuild and not yet
shipped. Each entry points at the unblocker.

1. **Full Regression Datasets (`slow_dataset`):** `scripts/fetch_datasets.py` execution once upstream licenses clear for OCR-Quality and KIE-HVQA benchmarks.

## 7. Low-Priority Naming, API & Style Smells

*Status verified against source on 2026-09-06 by the finetunement audit
(two agents re-read every item's target file). Closed entries were
pruned 2026-09-07; what follows is still open.*

### Still open — naming & API smells

- **4.1** `cors_origins_raw` property is referenced nowhere in src (the deprecated input field was removed in D10; the read-only property is test-pinned). Delete in a future breaking pass.
- **4.11** Four names for two concepts: `result_artifact_id` (`state_backend_types.py:61`) vs `artifact_id` (`jobs.py:66`) vs `text_artifact_id` / `translated_artifact_id` (plugin layers).
- **4.36** Per-candidate scan over existing boxes is O(n·m) on pathological box counts — inherent to the filter; the 2026-09-06 fused `geometry.is_duplicate` halved the constant.

### Still open — style nits

- **6.4** `HybridEngine.__init__` is now a 10-kwarg permanent API surface.
- **6.39** `preprocessing_enabled` property couples HTTP naming to behavior (`ocr/schemas.py`).
- **6.88** `OCRRequest` is 19 fields / 4 validators; consider a nested config object.
- **6.63-6.66** Hybrid re-injection wrappers are pass-throughs **kept deliberately**: tests drive the engine through these seams (~45 call sites), so inlining is churn without behavior change. Revisit only with a test-migration pass.

---

*End of outstanding work. Everything above is open; everything else
lives in git history.*
