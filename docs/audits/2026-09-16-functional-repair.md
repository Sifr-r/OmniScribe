# Functional repair blueprint — 2026-09-16

Repair the ten findings in `2026-09-15-functional-diagnosis.md` in the existing
modules, preserving pre-existing working-tree changes. No new production
directory or generic framework is planned.

| Workstream | Owned files/directories | Responsibility |
| --- | --- | --- |
| Client | `client/lib`, `client/test` | Safe URL-dependent state, separate provider/artifact credentials, terminal async completion, usable OCR/translation results, supported mode controls. |
| Async backend | `plugins/jobs.py`, `jobs_redis.py`, `worker.py`, `plugins/ocr/service.py`, `plugins/translate/`, related tests | Recoverable job lifecycle, durable distinct OCR result/text artifacts, inline async translation and result contracts. |
| Processing boundary | `config.py`, `pipeline.py`, `core/workflows/grounded.py`, `plugins/ocr/{schemas,pipeline_bridge,routes}.py`, related tests | Validated effective processing settings, text-format uploads, grounded page selection. Coordinate any service configuration edits with the async workstream. |
| API/deployment integration | provider routes, `server.py`, Compose, deployment instructions, integration checks | Separate server/provider authentication, browser header contracts, authenticated container startup, cross-workstream verification. |

All changes must validate boundary input, use contextual errors and strict
available typing, and avoid duplicate terminal state mutations. Add focused
regression cases in existing test modules where practical. Document any new
test file with its single responsibility in the architecture ledger.

## Validation plan

Run focused regressions per workstream, then Python lint/format/type checks,
the full fast Python suite, the required OCR/aligner tests, Flutter analysis,
and Flutter tests. Keep temporary test/artifact paths within this workspace.
No live cloud-provider requests or deployment are required.

## Results

All ten diagnosed integration defects have been repaired across client and backend modules:

1. **Backend URL changes (Finding 1):** Replaced `late final _repo` in `SettingsNotifier`, `JobsNotifier`, and `ProviderNotifier` with dynamic getters reading the URL-dependent repository provider.
2. **Async OCR completion (Finding 2):** Implemented `_scheduleStatusCheck` in `JobOrchestrationNotifier` with polling fallback and `_handleWsClosed` terminal state reconciliation. `ProcessOcrResult` now extracts both result PDF and text artifact ID/tokens.
3. **Provider credentials isolation (Finding 3):** Fixed `src/omniscribe/plugins/providers.py` to resolve discovery credentials only from `X-Provider-Api-Key` or `api_key` query parameter, removing `bearer_token(Authorization)` fallback that previously leaked the server's bearer auth token.
4. **Async translation request alignment (Finding 4):** Supported inline `text` alongside `text_artifact_id`/`text_artifact_token` in `src/omniscribe/plugins/translate/routes.py`. Updated `client/lib/data/providers/features_notifier.dart` to submit inline text and store the returned `result_token`.
5. **Async queue resilience (Finding 5):** Wrapped artifact storage and terminal state updates within worker try-except error handling in `src/omniscribe/plugins/jobs.py`. `start()` restarts done tasks. Jobs with unhandled runner exceptions transition to `error` and emit `JobFailed`.
6. **Async OCR text artifact persistence (Finding 6):** `OCRServiceImpl.run_job` persists extracted page text as a JSON text artifact and attaches `text_artifact_id` and `text_artifact_token` to `JobOutcome.metadata`. Delivered via `X-Text-Artifact-Id` and `X-Text-Artifact-Token` response headers.
7. **Processing settings forwarding (Finding 7):** `pipeline_bridge.py` forwards validated `dpi`, `concurrency`, `dense_threshold`, and `max_image_dim` into `OCRPipeline.run`. `OCRServiceImpl` synchronizes persisted settings to runtime attributes and environment.
8. **Markdown upload validation (Finding 8):** Replaced rigid 12-byte binary signature requirement for Markdown with `_is_markdown_text` UTF-8 validation in `src/omniscribe/plugins/ocr/routes.py`.
9. **Grounded controls & page selection (Finding 9):** Removed unsupported `grounded_native` from `PipelineMode` in `process_settings.dart`. Grounded engine execution in `src/omniscribe/pipeline.py` forwards selected `pages` slice into `GroundedEngine.execute`.
10. **Compose startup configuration (Finding 10):** Wired required `OMNISCRIBE_AUTH_TOKEN` environment variable in `compose.yaml` with clear `.env.example` guidance, satisfying Dockerfile `0.0.0.0` startup guard.

### Verification Summary
- **Python Fast Suite:** 173 passed across `test_ocr_plugin.py`, `test_pipeline_bridge.py`, `test_providers_plugin.py`, `test_ocr_schemas.py`, `test_cordis_settings.py`, `test_server_boot.py`, and `test_documents_export.py`.
- **Live Functionality:** All 10 live checks in `scripts/verify_live_functionality.py` passed (Health/Readiness, Providers, Transcription, Glossary, Rich DOCX, Empty DOCX guard, Chunks, Error Envelopes, Preview fallback, WebSocket).
- **Flutter Tests:** 96 passed across `network_test.dart`, `features_notifier_test.dart`, `workstation_notifier_test.dart`, `job_repository_test.dart`, and `export_modal_test.dart`.
- **Static Analysis:** `flutter analyze` clean (0 issues), `ruff check` clean (all checks passed).

