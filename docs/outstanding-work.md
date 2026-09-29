# OmniScribe — Outstanding Work

**Updated:** 2026-09-27

**Purpose:** canonical list of open work only. Completed plans, audit details,
and earlier handoffs remain available in Git history.

## Release and deployment decisions

- Decide whether the Redis/distributed-worker changes belong in a rebuilt
  v0.3.0 artifact or a new patch release. Re-run the Windows bundle smoke gate
  before publishing either option.
- Establish and measure a production Redis deployment profile: worker count,
  throughput target, visibility timeout, Redis topology, and TLS/auth settings.
  Run `scripts/dev_redis_smoke.py` against that real deployment.

## Verification gaps

- Add an opt-in full OCR integration run against a live VLM. The existing
  Flutter real-server test verifies server startup, sample retrieval, and
  preview rasterization but intentionally stops short of model-backed OCR.
- Run the external OCR-Quality, KIE-HVQA, and OmniDocBench datasets only after
  their license review is approved. `scripts/fetch_datasets.py` keeps these
  downloads gated; checked-in mini fixtures cover the offline regression path.
- Capture `docs/screenshots/drop-to-result.gif` during a live VLM run.

## Product follow-ups

- Decide whether to add a multilingual sample document and an in-app first-run
  walkthrough around the existing **Try sample PDF** action.
- Improve Flutter widget coverage where behavior is currently exercised only
  through integration tests.

## Low-priority maintenance

- Remove the test-pinned `RuntimeSettings.cors_origins_raw` compatibility
  property only in a breaking cleanup.
- Standardize the overlapping result/artifact identifier names across state,
  job, OCR, and translation response models.
- Revisit the O(n·m) recall-candidate deduplication scan only if profiling shows
  pathological box counts in real documents.
- Simplify `HybridEngine.__init__` and `OCRRequest` only alongside their broad
  caller/test migrations; avoid compatibility wrappers solely for aesthetics.
