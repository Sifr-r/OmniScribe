# Document state, consolidation and action review

## Blueprint

Preserve the existing feature-owned layout and all unrelated in-progress edits.
No new production directory, dependency or service layer is needed.

| Owner/files | Responsibility and planned change |
| --- | --- |
| `client/lib/features/workstation/workstation_state.dart` | Document identity and explicit processed PDF readiness; distinguish different byte buffers without scanning their contents. |
| `client/lib/features/workstation/document_result.dart` | Page preview identity participates in equality/hash so loaded preview changes publish to canvas consumers. |
| `client/lib/features/workstation/workstation_notifier.dart` | Document replacement/clear/sample transitions; invalidate old work and clear document-owned state before asynchronous cleanup. |
| `client/lib/features/jobs/job_orchestration_notifier.dart` | Sample/cancel request lifecycle; prevent late replies from mutating replacement jobs/documents. |
| `client/lib/features/documents/export_notifier.dart`, `export_modal.dart` | Validate selected export against current document data and save processed PDFs with a PDF extension. |
| `client/lib/data/models/models.dart`, `client/lib/data/repositories/repositories.dart` | Retain aggregate test imports while absorbing redundant feature-only export barrels; update their callers. |
| `client/lib/features/documents/extraction_state.dart` | Reuse installed collection equality rather than duplicate recursive comparison. |
| Existing `client/test/data/` and export modal suites | Regression proof for identity, replacement races, cancellation and export preconditions. |
| `docs/outstanding-work.md`, relevant `docs/rfcs/`, architecture ledgers | Separate current open actions from shipped changes and historical verification. |
| This document | Verifiable scoped blueprint, current findings and final verification. |

Parallel ownership: lifecycle agent owns workstation/job changes and their tests;
consolidation agent owns barrels/equality and affected callers; documentation agent
owns backlog/RFC corrections. Lead owns export validation, integration review and
architecture synchronization. Changes must retain validation at entry points,
contextual errors, strict available typing and repeat-safe state transitions.

## Preservation checks

- Baseline feature layout check passed: 168 Dart files, 31 unique providers.
- Run the same ownership/import check after consolidation.
- Run targeted Flutter state, job, model and export tests with the normal launcher.
- Verify local Markdown links and every current architecture-ledger file entry.
- Inspect final changes for duplicate providers, broad file merges and stale actions.

Previous 2026-09-29 test totals are historical evidence, not verification of this
change. Do not bypass locked SDK/user directories with helper scripts.

## Implemented findings

- Document buffers now compare and hash by identity, so replacing a document
  with different bytes of the same length publishes the change without a
  multi-megabyte content scan.
  Page preview buffers likewise participate in page equality/hash by identity.
- Clearing/replacing a document invalidates old OCR, sample and preview work
  immediately. Cancellation captures the old job/channel before cleanup and
  guards later warnings. Progress attachment and async failure cleanup recheck
  ownership after awaits. Preview preload ownership follows document generation.
- Sample adoption uses the normal loading path, clearing previous pages,
  selection, preview cache and artifact handles. Both OCR entry points clear old
  recognition before reruns, allowing text-artifact recovery to supply new text.
- Successful OCR records explicit processed PDF bytes and a `.pdf` filename.
  Export rejects source-only PDFs, empty recognized content and processing jobs;
  it also rejects an API export completed after document replacement. Readiness
  in the modal follows the selected format. DOCX no longer substitutes a filename
  for missing recognized text.
- Combined `data/models/feature_models.dart` into `data/models/models.dart` and
  `data/repositories/feature_repository.dart` into `data/repositories/repositories.dart`.
  Their callers use the parent barrels. Four aggregate compatibility files
  remain. Selection, viewport, extraction and export keep distinct owners.
- Extraction equality and hashing share the installed `DeepCollectionEquality`.

## Action document validity

`docs/outstanding-work.md` remains the single current backlog. RFC 001's upstream
anyio wait is superseded by the shipped local bundle fix. RFC 003's missing
distributed queue/Pub/Sub/TLS assumptions are historical; its real worker/recovery
smoke is still open. The existing SQLite-to-Redis migration script needs deployment
verification, not reimplementation. RFC 004's README/provenance tasks are complete;
nightly live markdown scoring is superseded by the documented manual workflow.
Release decisions, live VLM/device verification and external dataset license gates
remain open. Dated review reports retain historical evidence and original snippets.

## Verification and limits

- Feature layout/import/provider check passed after consolidation: **166 Dart
  files, 31 unique providers** (baseline: 168 files, 31 providers).
- `git diff --check` passed. Local Markdown targets in **12 documents** and
  **272 architecture catalog paths** passed checks, excluding historical source
  snippets and API URLs from filesystem checks.
- Added/updated regression cases in existing state/job/export suites for buffer
  and preview identity, empty-source validation, processed PDF readiness, replacement races,
  rerun text recovery, preload ownership, whitespace-only DOCX and stale exports.
  These assertions have **not been executed** for this change.
- Normal `flutter test --no-pub` produced no test results and was interrupted.
  Normal `dart format` failed before formatting with `PathAccessException`
  creating `C:\Users\rahin\AppData\Roaming\.dart-tool` (access denied). Per the
  workspace safety instruction, no user/SDK directory was modified and no
  helper/native runner was used to bypass the denied access. Formatting, analysis
  and runtime tests remain unverified.

Run the following in an environment where the normal launcher can initialize:

```powershell
cd client
rtk dart format lib/features/documents lib/features/workstation/workstation_state.dart lib/features/workstation/workstation_notifier.dart lib/features/workstation/document_result.dart lib/features/jobs/job_orchestration_notifier.dart lib/data/models/models.dart lib/data/repositories/repositories.dart test/data test/models_test.dart test/presentation/workstation/modals integration_test/_test_helpers.dart
rtk flutter analyze --no-pub
rtk flutter test --no-pub test/models_test.dart test/data/workstation_state_test.dart test/data/workstation_notifier_test.dart test/data/workstation_hydration_test.dart test/data/job_orchestration_notifier_test.dart test/data/job_orchestration_state_test.dart test/data/features_state_test.dart test/data/features_notifier_test.dart test/data/modularity_regression_test.dart test/presentation/workstation/modals/export_modal_test.dart test/presentation/workstation/modals/export_modal_smoke_test.dart
```
