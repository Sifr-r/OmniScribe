# LLM-Application Remediation — Design

Date: 2026-09-06
Status: Approved
Source: 2026-09-06 LLM-application audit (memory: `2026-09-06-llm-application-dev-audit.md`)

## Decisions

- **Scope:** full findings list (8 High + ~15 Medium + ~8 Low), one pass.
- **Judge loop:** wire into production, default ON (`OMNISCRIBE_TRANSLATION_EVALUATE`).
- **Git:** finetunement wave committed first (`d5520c6`); this wave lands as ~3 logical commits (translate+lexicon / OCR+transcription+client / docs), confirmed before committing.
- **Execution:** sequential by subsystem with TDD; no parallel agent streams (translation and lexicon share files).

## Subsystem 1 — Translation pipeline + Lexicon

### Translation (`core/translate/`, `plugins/translate/`)

1. **Judge wiring, default ON.** `translate_text`/`translate_tree` route through the evaluate/retry graph. `TranslationSettings.evaluate_enabled` from `OMNISCRIBE_TRANSLATION_EVALUATE` (default true). Judge unparseable/errored → logged warning, no silent 1.0. Best-scoring attempt tracked in state; after max attempts the best is returned. If every attempt errored, raise domain `TranslationError` — the `[Translation Error: …]` marker never reaches output.
2. **Sanitization at every injection site** — glossary blocks, RAG lines, entity memory, judge feedback pass through `sanitize_prompt_input`; glossary import sanitizes entries as defense in depth.
3. **Token budget.** Entity-memory lists capped (top-K by frequency, default 20/category, configurable); glossary block capped; explicit `max_tokens` passed on all translation/evaluate LLM calls from settings.
4. **Script-aware length bands.** Local `_length_band(source_script, target_script)` (CJK-aware char-ratio ranges) replaces the flat 0.1–2.5 band in nodes/dual/tree.
5. **Config.** Add `lexicon_result_count` (default 3, env-overridable); `_int_env`/`_float_env` warn with the offending key and default used.
6. **Unified prompt builder.** One builder used by graph and tree paths (single temperature source). Never-populated dead state fields (`glossary_prompt_block`, `entity_memory_prompt_block`, `sliding_window`) deleted after caller verification.
7. **Process-local LRU translation cache** keyed (chunk hash, target, model, lexicon fingerprint). `LexiconStore.fingerprint()` = cached hash of glossary names+entry counts, invalidated on save/toggle.
8. **Offline eval:** chrF utility + fixture harness test (`tests/core/translate/`); not a CI gate.

### Lexicon (`core/lexicon/`)

9. **Real hybrid search.** Keyword leg = deterministic scoring against a persisted `term_normalized` column (NFC + casefold): exact > prefix > substring, computed SQL-side via `.where()`/`.contains()`; fused with vector results via RRF (k=60; weights via env, default 0.6 vector / 0.4 keyword). No FTS/ tantivy dependency — deliberate, to keep the embedded store dependency-free and CJK-safe. Both leg scores on `LexiconHit`.
10. **Truncation fix.** `_candidate_terms()` extracts acronyms, capitalized runs, non-Latin spans, quoted phrases (cap 8); entry vector score = max over term embeddings + in-window chunk embedding; warn when input exceeds the model window.
11. **Vector index.** `_ensure_index()` creates the HNSW index per `VECTOR_INDEX_SPEC` at table create and after bulk imports (idempotent).
12. **Model guard.** `_meta` table persists model name+dim; on open, mismatch raises `LexiconError` with remediation guidance.
13. **Prefilter + floor.** `.where(..., prefilter=True)` replaces ×4 over-fetch; `min_score` via `OMNISCRIBE_LEXICON_MIN_SCORE` (default 0.35) wired into `retrieve_lexicon_context`; score doc clamped to 0..1.
14. **Migration idempotency.** Fix None-vs-raise (`get_glossary` returns None) — look up by name+group; legacy `glossary_id` threaded through `save_glossary`.
15. **Upsert on re-import.** `save_glossary(..., upsert=True)` replaces same (name, source_uri) keeping glossary_id; `entry_hash` column lets unchanged entries reuse stored embeddings.
16. **Normalization.** Single `normalize_term()` (NFC + casefold) for merge/preview/exact_lookup; `case_sensitive` honored.
17. **Observability.** Debug query→hit logging; recall@k fixture test with deterministic fake embedder.

## Subsystem 2 — OCR / trust / transcription (summary)

Block-builders populate `confidence`; trust_score/flags surface in API + export-modal warning summary (Flutter); transcription stores `exp(avg_logprob)`; repair prompt carries previous attempt + reason with retry temperature bump; hybrid trigger adds text-layer agreement; whisper beam/VAD/temperature-fallback config; segment newline joins; API engine client hoisted + exponential backoff + Retry-After; correction-pass fallback to first pass; GLM parser aliases/guards + drop-count events; PROMPT_VERSION constants split (closes outstanding-work 6.55).

## Subsystem 3 — Docs (summary)

outstanding-work.md updates (6.55 closed; deferred list), ARCHITECTURE.md translation/trust sections.

## Deferred explicitly

Chunked audio upload; cross-engine agreement repair trigger; CI recall@k gate; cache beyond process-local LRU.

## Verification

TDD per item; fast-tier pytest (pyarrow imports guarded); mypy on touched modules; Flutter widget tests for export-modal change; full suite green before completion.
