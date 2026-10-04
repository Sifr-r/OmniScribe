# OmniScribe — Outstanding Work

**Updated:** 2026-09-30

**Purpose:** canonical list of open work only. Completed plans, audit details,
and earlier handoffs remain available in Git history or dated review artifacts.
Actions below were checked against source and documentation on this date;
that review does not constitute a fresh runtime or release verification.

## Release and deployment decisions

- Decide whether the Redis/distributed-worker changes belong in a rebuilt
  v0.3.0 artifact or a new patch release. Re-run the Windows bundle smoke gate
  before publishing either option.
- Establish and measure a production Redis deployment profile: worker count,
  throughput target, visibility timeout, Redis topology, and TLS/auth settings.
  Run `scripts/dev_redis_smoke.py --jobs 4 --verify-recovery --timeout 600`
  against that real deployment, restarting the identified worker at the printed
  cue. The opt-in smoke now submits concurrent translation jobs, checks
  token-bound HTTP/Redis results and requires the same job to move to a different
  worker before completion. The default remains a health/keyspace probe; offline
  regressions do not establish production throughput or restart recovery.
  Export the deployment's `OMNISCRIBE_AUTH_TOKEN` for an authenticated API;
  the script rejects redirects and accepts `rediss://` Redis URLs for TLS.

## Verification gaps

- Execute the opt-in live VLM OCR integration run documented in
  [client/README.md](../client/README.md#real-server-integration-verification).
  `OMNISCRIBE_LIVE_OCR=true` now exercises model-backed OCR on a rasterized
  sample through the Flutter workstation and checks recognized content and
  processed PDF output. Its live result remains unverified.
- Complete license review and implement the full-dataset download/conversion
  paths before running external OCR-Quality, KIE-HVQA, or OmniDocBench data.
  `scripts/fetch_datasets.py` currently provides gated OCR-Quality/KIE-HVQA
  stubs; it has no OmniDocBench downloader. Checked-in mini fixtures cover the
  offline regression path. Publish comparable public results after these gates
  are met; competitor numbers in [benchmarks.md](benchmarks.md#provenance)
  remain illustrative. Live `--score-markdown` runs remain manual because CI
  has no configured VLM endpoint.
- Capture `docs/screenshots/drop-to-result.gif` during a live VLM run.
- Complete normal Flutter analysis and device/browser integration verification
  in an environment with the required writable SDK/user state. The
  [2026-09-29 review](modularity-review-2026-09-29.md#verification)
  records compiler/native assertions, an unavailable standard analyzer, and
  two native suites that retained processes after successful assertions.
  The [2026-09-30 document-state review](document-state-review-2026-09-30.md#verification-and-limits)
  adds unexecuted state/export regressions; normal Dart formatting also failed
  on the locked telemetry directory. Run its targeted gate before considering
  those changes runtime-verified. This pass again encountered access denied
  creating the normal launcher's user telemetry directory; no bypass was used.
  Include the new format-specific readiness and document replacement
  assertions in `export_modal_smoke_test.dart` when rerunning the gate.

## Product follow-ups

- Decide whether to add a multilingual sample document and an in-app first-run
  walkthrough around the existing **Try sample PDF** action.
- Extend Flutter widget coverage for remaining behavior exercised only through
  integration tests. Export readiness and document replacement now have focused
  widget assertions, pending the Flutter runtime gate above.

## Low-priority maintenance

- Remove the test-pinned `RuntimeSettings.cors_origins_raw` compatibility
  property only in a breaking cleanup.
- Standardize the overlapping result/artifact identifier names across state,
  job, OCR, and translation response models.
- Revisit the O(n·m) recall-candidate deduplication scan only if profiling shows
  pathological box counts in real documents.
- Simplify `HybridEngine.__init__` and `OCRRequest` only alongside their broad
  caller/test migrations; avoid compatibility wrappers solely for aesthetics.
