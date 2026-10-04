# Flutter and Python modularity refactor

## Scope and preservation boundary

Preserve the current working-tree changes, endpoint URLs, status codes, token rules, queue behavior, Riverpod provider identities, and public model fields. Do not add dependencies or replace the working plugin runtime. Use feature ownership rather than a new architecture framework.

## Planned ownership

```text
client/lib/
  core/                         shared transport, theme, application enums
  app/                          shell and composition
  shared/                       reusable presentation and provider wiring
  features/
    workstation/                OCR state, models, API adapters, canvas and controls
    settings/                   runtime configuration state, API and UI
    providers/                  model/provider discovery state, API and UI
    jobs/                       history and job orchestration
    translation/                translation models, state, notifier and screen
    transcription/              transcription models, state, notifier and screen
    glossary/                   glossary models, state, notifier and screen
    documents/                  extraction/export models, state, notifier and UI
src/omniscribe/plugins/
  <existing domain>/routes.py    HTTP validation, status and response mapping
  <existing domain>/schemas.py   typed request/response contracts
  <existing domain>/service.py   business orchestration and domain errors
  _http.py, errors.py            existing shared error envelope and HTTP mapping
```

Actual additions and moves must be listed individually in the final architecture ledger. Small import-only compatibility barrels are allowed where existing tests or public imports depend on the old location; production callers should use the feature path. Shared transport/provider wiring remains shared. Split the four-domain notifier/state/model files into domain-owned files. Keep existing repository test seams unless removing them improves the real dependency flow without broad behavior changes.

## Parallel work

1. Flutter owner: migrate source ownership, split combined feature files, update imports, preserve provider identity and serialization behavior; verify with analyzer and current unit/widget tests.
2. Python owner: inspect route/service boundaries, remove redundant catch-and-reraise wrappers, strengthen already-existing response contracts where compatible, and run focused route/schema proofs. Preserve response payloads and existing working-tree edits.
3. Contract reviewer: independently trace payloads, errors, async status, capability tokens, widget side effects and rebuilds; provide exact source references and before/after snippets for actionable findings. Coordinate any contract fix with its owner.

Every implementation must retain input validation, contextual errors, language typing, and repeat-safe state mutations. No speculative interfaces, empty services, generated-model dependencies, destructive Git commands or writes outside this workspace.

## Verification and deliverables

Run a baseline before edits and repeat the relevant proof after edits. Run Flutter analysis/tests and Python Ruff, formatting, mypy, and the fast pytest suite when practical; record environmental failures separately from regressions. Deliver a review report with the final file layout, exact before/after snippets for every recommended or implemented change, verification results, and remaining limitations. Sync both architecture ledgers without replacing unrelated edits.
