# Goal alignment closeout blueprint (2026-10-04)

Scope: close the independently reproduced defects in the 2026-10-03 assessment, preserve existing changes, and record fresh acceptance evidence. Existing feature directories retain ownership; no new runtime layer is planned.

| Owner | Existing files / responsibility | Required proof |
| --- | --- | --- |
| Client agent | Workstation OCR models/repository/state, jobs orchestration, documents export models/notifier, glossary parsing/polling/UI | Rich handles reach exports; unavailable completed artifacts fail visibly; partial previews reconcile; nullable healthy glossary responses and failed URL imports work. |
| OCR agent | Core trust workflow, OCR service/routes, server CORS, NLLB resolver | Scored trust survives rich structure/artifact/export; all-page failure is terminal; document/failed-page headers exposed; invalid language codes rejected before model load. |
| Runtime agent | Broker config/schema, in-process job ownership/reconciliation, boot tests, Windows bundle instructions | Shipped Loader uses one Redis mode; live peer jobs survive startup; abandoned work handled safely; tests isolate storage; asset names/checksums consistent. |
| Lead | PDF invisible text helpers/tests, integration review, assessment and architecture ledgers | Arabic logical extraction/search preserved; focused and full offline gates; live/build gates reported according to observed evidence. |

Regression tests stay in their corresponding existing test domains. Any new test file has one responsibility: exercise its combined acceptance boundary. All agents enforce entry-point validation, contextual errors, strict typing and idempotent state changes. No writes outside the workspace, permission bypasses, destructive Git, or publishing.

Acceptance: reproduced defects have meaningful passing regressions; full backend and available client gates are checked; no unexecuted live measurement or release journey is described as passed.

GitHub preparation: the lead runs the existing CI lint, format, typing and fast-test coverage gates, puts current assessment evidence before historical findings, and excludes local scratch output via `.gitignore`. Client and OCR agents independently review readiness and document clarity. Existing assessment and architecture files retain their responsibilities; no new source files are planned.

The broader `mypy src tests` gate exposed annotation and narrowing defects. OCR owns its three existing OCR/artifact test files; runtime owns the confidence/Redis scripts and Redis script tests; the lead owns security/export test narrowing. Fixes retain runtime contracts and test assertions, then rerun the affected checks.
