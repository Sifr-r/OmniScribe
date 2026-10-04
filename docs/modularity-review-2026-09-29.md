# Flutter and Python modularity review

This report records the working tree reviewed on 2026-09-29, including pre-existing local changes. Its counts and test results are historical evidence. Current document-state fixes and compatibility-barrel consolidation are recorded in [the 2026-09-30 review](document-state-review-2026-09-30.md). The original refactor preserved the existing Python plugin composition runtime and Flutter Riverpod data flow. It added no runtime dependencies, code generation, generic service layer, or new architecture framework.

The implementation blueprint is [refactor-blueprint-2026-09-29.md](refactor-blueprint-2026-09-29.md). Exact file moves, before/after code, and ownership are recorded in the Flutter and backend detail artifacts linked below; the independent contract review records each observed failure and its narrow correction.

## Deliverables

- [Flutter file layout and exact refactored snippets](flutter-refactor-details-2026-09-29.md)
- [Python boundary changes and exact refactored snippets](backend-refactor-details-2026-09-29.md)
- [Independent API and lifecycle review](contract-review-2026-09-29.md)
- [Canonical architecture ledger](ARCHITECTURE.md)

## Decisions

Flutter features own their models, state, network adapters, and UI. Shared HTTP/WebSocket transport, theme, reusable widgets, and application composition keep shared ownership. A feature reads a Riverpod state owner; the state owner invokes its domain adapter; only that adapter accesses the shared API client. Existing test injection points are preserved where useful, rather than adding interfaces for each new file. Old imports can remain thin export-only compatibility barrels; they must not instantiate duplicate providers or contain business logic.

Python already groups documents, glossary, transcription, translation, and OCR by domain. Its existing route/schema/service arrangement is retained. External IO remains in the actual clients/stores used by those services; it does not need an additional pass-through repository. Services raise contextual domain errors, and the HTTP layer maps those errors through the existing stable envelope. Successful responses retain their existing fields, statuses, and capability-token rules.

Models remain hand-written immutable Dart classes with explicit JSON factories and Pydantic Python schemas. Adding `freezed`, `json_serializable`, and build generation solely for this refactor would grow the toolchain without removing the need for boundary validation.

## Verification

All results in this section were recorded on 2026-09-29. They do not establish
that subsequent edits pass the same checks. The standard analyzer and
device/browser execution limitations remain verification follow-ups in the
[canonical backlog](outstanding-work.md#verification-gaps).

Baseline Python Ruff check failed on the pre-existing `B010` at `tests/test_security.py:71`. Formatting check found six pre-existing files: `tests/core/pdf/test_embedder.py`, `tests/core/readers/test_readers.py`, `tests/core/test_document.py`, `tests/plugins/test_jobs_redis_worker_security.py`, `tests/test_security.py`, and `tests/test_server_boot.py`. Those unrelated changes are preserved.

The normal Flutter/Dart launchers attempted access to locked user telemetry directories (`C:\Users\rahin\AppData\Roaming\.dart-tool` and `.flutter_tool_state`). No outside-workspace directories, SDK caches, or lock files were modified to work around those errors. Read-only verification and workspace-local checks are reported in the final results below.

Final Python fast suite: **2,658 passed, 21 skipped, 6 deselected**, including the OpenAPI snapshot and all added boundary regressions. Full mypy passes across **221 source files**. Ruff and formatting pass on all ten Python files changed by this refactor; global Ruff/format retain only the baseline issues above.

Direct Flutter frontend compilation passed for the application entry point and all **56 unit/widget/integration test entry points**, with installed dependency access approved for reads and all compiler output confined to `.agent-tmp/`. The dependency-free JSON contract executable and source-layout checker pass: **168 Dart files, 31 unique provider declarations**. The installed Dart formatter formatted 73 touched sources; its final pass reported zero further changes. `git diff --check` passes.

Native Flutter execution verified successful assertions in all **43 unit/widget test entry points**, including all 13 presentation/widget suites and the six focused modularity regressions. The runner used `FLUTTER_TEST=true`, `--flutter-assets-dir` with the existing compiled project assets, and workspace-local output. **41 suites exited cleanly**; the feature-notifier and provider-notifier suites reported `All tests passed!` without test errors but retained an engine process until the runner's shutdown timeout. This is an explicit execution limitation rather than a clean-exit claim for those two suites. The independent review contains per-suite evidence. Device/browser integration scenarios were compiled but were not executed.

The standard analyzer remains unverified because its launcher attempts writes to locked user telemetry directories. No SDK, user-cache or telemetry files were changed. The direct compiler and native test engine supplied type and runtime verification without changing those directories. Generated temporary compiler binaries were removed after verification; text evidence remains under `.agent-tmp/native-review/`.

The final Flutter ownership inventory contains **71 relocated sources and 20 added/split sources**. The canonical ledger documents all 91 resulting paths and their single responsibilities. The root ledger and client README link to the updated structure.
