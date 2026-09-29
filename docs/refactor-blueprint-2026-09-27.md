# Scoped refactor blueprint — 2026-09-27

The architecture ledger is `ARCHITECTURE.md` at the repository root, with the detailed catalog in `docs/ARCHITECTURE.md`. The current boundaries already fit these fixes. No new production directory or file is planned.

| Existing path | Responsibility after this change | Verification |
| --- | --- | --- |
| `src/omniscribe/plugins/jobs.py` | Own the shared queue runner selection rule and trusted spool containment check; retain in-process queue and cleanup policy. | Tagged and untagged queue dispatch, cancellation tests. |
| `src/omniscribe/plugins/jobs_redis.py` | Reuse shared containment check; retain Redis claim, payload validation, and authoritative cleanup policy. | Redis payload path and cancellation tests. |
| `src/omniscribe/worker.py` | Use the shared runner selection rule for claimed jobs. | Worker dispatch tests. |
| `src/omniscribe/core/ocr/processor.py` | Treat optional TrOCR recognition as best effort while propagating second VLM call failures. | TrOCR integration tests. |
| `client/lib/data/providers/job_orchestration_notifier.dart` | Share sync and async artifact hydration while preserving run and job guards. | Job completion and hydration tests. |
| `client/lib/data/repositories/ocr_repository.dart` | Parse preview response headers in one place while retaining separate cached-ID and upload request paths. | Preview repository tests. |

Existing test files receive focused regression cases where the current tests do not cover the changed behavior. No dependencies or public API changes are planned.
