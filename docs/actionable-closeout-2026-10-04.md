# Actionable document closeout — 2026-10-04

Scope: review the canonical `outstanding-work.md` against the current tree,
complete executable work, and retain explicit evidence for external blockers.
Preserve all existing uncommitted work. No publishing or Git mutations.

| Owner / paths | Single responsibility / acceptance |
| --- | --- |
| Dataset agent: `scripts/fetch_datasets.py`, matching `tests/scripts/`, `docs/benchmarks.md` | Verify upstream dataset identity, license and schema; implement only supportable download/conversion paths with honest provenance, validation and offline regression tests. No invented confidence scores or license approval. |
| Client agent: existing `client/test/` feature suites and necessary feature fixes | Cover meaningful remaining integration-only behavior; run format, analysis, unit/widget and ownership gates; attempt normal Windows/web builds without permission bypasses. |
| Packaging agent: `omniscribe_server.spec`, matching packaging tests/docs | Audit excluded modules against installed module-level imports and smoke the existing frozen bundle; make narrowly required corrections and report exact evidence. |
| Lead: this artifact, `docs/outstanding-work.md`, root and canonical architecture ledgers | Reconcile completion claims, run cross-area checks and document every new file's responsibility. Probe configured inference availability without exposing credentials. |

All agents enforce trust-boundary validation, contextual deterministic errors,
strict available typing and repeat-safe mutations. Shell commands use `rtk`.
Writes stay within `D:/OmniScribe`; Git is limited to status/log/diff.
Live deployment, model-backed accuracy, release decisions and screen recordings
are complete only when their actual results have been observed.

Concrete client scope: `client/lib/features/jobs/job_history_screen.dart`
owns job-history rendering and user feedback; its matching notifier owns fetch
and clear transitions. New `client/test/features/jobs/job_history_screen_test.dart`
owns widget regressions for the history list, refresh and clear/error journeys.

Concrete packaging scope: `scripts/smoke_existing.py` owns the common bounded,
workspace-isolated probe; `scripts/build_windows.py` delegates smoke checks to
it. New `tests/scripts/test_bundle_imports.py` pins runtime import reachability
under all spec exclusions; new `tests/scripts/test_bundle_smoke.py` pins timeout,
port ownership, storage isolation and owned-process shutdown.

## Observed acceptance

- Client: 447 unit/widget tests; fatal-info analysis; 171-file unchanged format;
  31-provider layout check and JSON contracts. Final Windows and web release
  builds pass. Web warns about an unavailable optional CupertinoIcons font.
- No-VLM Windows real-server integration passed without skipping: sample fetch,
  staging and server-rendered PDF preview, followed by clean teardown.
- Dataset tool: pinned source-only acquisition, original annotations/digests,
  research-only OmniDocBench acknowledgement and bounded page adapter. Real KIE
  annotations and one OmniDocBench image/page were acquired successfully. See
  [exact corpus/terms evidence](benchmarks.md#5-dataset-ingestion--license-review-status).
- Existing frozen binary: health and sample-PDF endpoints returned 200; all
  fourteen plugins mounted with workspace-isolated SQLite. Cleanup of one
  extracted library failed with WinError 5; that directory was retained and
  cleanup halted. The smoke helper now retains its workspace evidence by design.
  This probe does not attest to a freshly rebuilt source release artifact.
- Configured inference availability: OCR LAN endpoint refused the TCP connection,
  translation placeholder failed DNS resolution, and localhost VLM refused the
  connection. No credentials were printed or changed.
- Cross-area Ruff lint and formatting passed (467 files); typing passed across
  source, tests and the three changed scripts (449 files). Dataset-focused
  regression tests passed (19); combined dataset regressions passed with three
  expected missing-full-fixture skips. Test descriptions now distinguish
  synthetic smoke data from upstream datasets.
- Bundle smoke/import regressions: seven passed. Final existing-binary probe
  passed with its retained log at `build/bundle-smoke-nyxv07xe/boot.log`; the
  binary is 460.3 MB. Response validation requires a healthy JSON health payload
  and a PDF signature, rather than arbitrary body substrings.
- Full backend fast/coverage run: **2,848 passed, 21 skipped, 6 deselected,
  one failed**, 84.04% coverage, 771.63 seconds, exit 1. The failure was
  `test_sqlite_owner_excludes_other_process_and_recovers_after_death` timing out
  while launching its second child interpreter (45-second budget). The exact
  test subsequently passed alone in 32.72 seconds, without code changes.
  This is an observed intermittent timeout, not proof of a green full gate.
- Final diff whitespace and local Markdown file-target checks passed. Existing
  uncommitted work was preserved; no Git mutation or publication occurred.

Remaining acceptance requires a reachable model endpoint (live OCR, scanned
device export/search, Redis completion/recovery and recording), actual missing
calibration/regional annotations, deployment measurements, underlying-document
rights review and a release decision. Optional feature choices and compatibility
cleanups remain explicitly conditional in the canonical backlog.
