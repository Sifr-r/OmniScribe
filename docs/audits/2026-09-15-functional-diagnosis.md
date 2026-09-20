# Functional diagnosis — 2026-09-15

## Investigation blueprint

Review the current working tree, preserving all pre-existing edits. Trace user
flows across the Flutter client, HTTP/plugin layer, and document pipeline;
reproduce concrete failures before attributing a cause.

| Area | Existing directories | Responsibility |
| --- | --- | --- |
| Client review | `client/lib`, `client/test` | Verify endpoint contracts, configuration, upload, job progress, and result retrieval. |
| Backend review | `src/omniscribe/plugins`, `src/omniscribe/harness` | Verify job lifecycle, artifact ownership, and API error/result contracts. |
| Pipeline review | `src/omniscribe/core`, `src/omniscribe/pipeline.py` | Verify OCR selection, provider errors, document conversion, and output correctness. |
| Runtime validation | server/configuration, test suite, local logs | Distinguish code defects from environment or deployment failures. |

New artifact: this file records reproducible findings and validation limits.
No new production directory or abstraction is planned for this diagnosis.

## Outcome (completed 2026-09-16)

The current application has several independent integration defects. Basic
server liveness is not the main problem: the local server returned HTTP 200
from both `/api/health` and `/ready`. Successful boot and passing unit tests
do not establish that a complete client workflow works.

No production code was changed by this investigation. Existing working-tree
edits were preserved. Findings describe that working tree, not just HEAD.

### 1. Backend URL changes crash client state — high priority

`client/lib/data/providers/settings_notifier.dart:12-17` declares `_repo` as
`late final` and assigns it inside `build()`, which watches the URL-dependent
repository. Changing the URL rebuilds the same notifier and assigns the final
field again. Other notifiers use the same pattern and need a caller audit.

**Executed proof:** create a ProviderContainer, read `settingsStateProvider`,
change `apiBaseUrlProvider` to `http://localhost:9000`, then read settings again.
Observed `ProviderException` wrapping `LateInitializationError: Field '_repo'
has already been initialized` at `SettingsNotifier.build:16`.

**Fix direction:** make dependency resolution safe across rebuilds; preserve
the selected URL/state and refresh against the new repository.

### 2. Async OCR completion never reaches the client — high priority

`client/lib/data/providers/job_orchestration_notifier.dart:498-500` hooks
completion checking to WebSocket closure. Its `ProgressFrame` handler at
line 740 only updates progress, and the server's progress socket remains in
its receive loop (`src/omniscribe/plugins/progress.py:544-546`). Normal
completion does not close that socket. No ongoing status polling bridges the
gap on this client path.

**Executed proof:** submit a mocked async job, emit a 100% completion progress
frame, and leave the socket open. Observed `percent=100`, `isProcessing=true`,
zero status checks, and zero result downloads.

**Fix direction:** observe a terminal job event or poll to terminal status,
then redeem the result and finalize exactly once. Do not infer job completion
from a percentage alone: processing and artifact storage finish separately.

### 3. Backend authentication overrides provider credentials — high priority

`src/omniscribe/plugins/providers.py:112` resolves discovery credentials as
`X-Provider-Api-Key or bearer_token(Authorization) or api_key`.
The Flutter repository sends the provider key as the `api_key` query parameter
(`client/lib/data/repositories/provider_repository.dart:69-75`), while the API
client automatically adds the OmniScribe server bearer token
(`client/lib/core/network/api_client.dart:134-140`).

**Source-confirmed trigger:** enable server authentication and discover cloud
provider models through the client. The backend bearer wins over the supplied
provider key, causing upstream authentication failures and potentially sending
the server's credential to the provider. No real credential was sent during
this investigation.

**Fix direction:** keep server authentication and upstream provider credentials
separate; use the dedicated provider header consistently and remove the server
bearer fallback.

### 4. Async translation sends the wrong request — high priority

`client/lib/data/providers/features_notifier.dart:168-177` sends inline `text`
to `/api/translate/async`. `src/omniscribe/plugins/translate/routes.py:81-87`
requires `text_artifact_id` and `text_artifact_token`, neither of which that
client path supplies.

**Source-confirmed result:** a nonempty-text submission from the Async button
is rejected with HTTP 400 and the missing-artifact-pair message before any
translation starts.

**Fix direction:** agree on a single async input contract: support inline text
at the server boundary, or create and pass a token-bound text artifact.

### 5. One artifact-storage failure kills the async queue — high priority

`src/omniscribe/plugins/jobs.py:428` stores the result outside the runner's
error handler. An exception escapes `_process_one` and terminates the sole
worker. `start()` at line 295 also does not restart an already-completed task.

**Executed proof:** inject `OSError('No space left on device')` from artifact
storage and submit two jobs. Observed `worker_done=True`, first job still
`running`, second job still `queued`.

**Fix direction:** handle failure across the complete job lifecycle, mark a
failed job terminal where storage permits, keep/recover the worker, and expose
unhealthy queue state when recovery is impossible.

### 6. Async OCR loses the text artifact — high priority

`src/omniscribe/plugins/ocr/service.py:395-400` discards extracted page text and
returns only PDF bytes from `run_job`. `_status_response` at line 559 then
labels the PDF result's id `text_artifact_id`. Sync OCR creates an actual JSON
text artifact; async OCR does not provide the same contract.

The client completion fallback also retains only the id
(`job_orchestration_notifier.dart:581`), whereas structured export requires an
id/token pair. Thus repairing the stuck progress state alone will not repair
the downstream text/structured-export flow.

**Evidence:** traced producer, queue artifact storage, status mapping, and
consumer; no live model was used. **Fix direction:** store distinct PDF/text
artifacts and deliver each usable id/token pair through the authorized result
channel, preserving tokens outside public status responses.

### 7. Processing settings save successfully but have no effect

`src/omniscribe/plugins/ocr/service.py:870-877` saves DPI, concurrency, image
size, and dense threshold. `pipeline_bridge.py:113-148` does not forward those
values into `OCRPipeline.run`; the grounded constructor additionally hardcodes
image size and concurrency.

**Executed proof:** with persistence mocked, save DPI 300, concurrency 6, and
image size 2048. Configuration reads reflect those values, but execution still
uses the pipeline defaults DPI 200 and concurrency 1.

**Fix direction:** pass validated effective configuration into construction
and execution, and test the values received by the actual engine boundary.

### 8. Valid Markdown uploads fail with HTTP 415

`src/omniscribe/plugins/ocr/routes.py:209-222` treats the first 12 bytes of
Markdown as a required file signature. A plain paragraph is valid Markdown
but does not match the recognized opening punctuation.

**Executed router proof:** upload `example.md`, content type `text/markdown`,
contents `An ordinary paragraph of valid Markdown.` to `/api/process/async`.
Observed HTTP 415: `file contents do not match declared content type
'text/markdown'`.

**Fix direction:** validate text formats through decoding/parser rules rather
than mandatory binary-style signatures.

### 9. Grounded controls do not match backend capabilities

- `client/lib/data/models/process_settings.dart:8` exposes `grounded_native`,
  but `src/omniscribe/plugins/ocr/schemas.py:19` permits only `hybrid` and
  `grounded`. Selecting that UI option produces request validation failure
  (HTTP 422).
- `src/omniscribe/pipeline.py:234-246` does not forward the selected `pages`
  into the grounded engine, unlike the hybrid branch at line 258. A grounded
  request's page selection is silently ignored.

**Evidence:** direct schema/serialization and dispatch traces. **Fix direction:**
align the supported modes and either implement grounded page selection or
reject/disable it explicitly.

### 10. Default Compose configuration cannot start the current image

`Dockerfile:165` launches with `--host 0.0.0.0`. The server startup guard
(`src/omniscribe/server.py:522-528`) requires an auth token for that bind.
`compose.yaml` only contains a commented token example and does not forward
the host `.env` token into the API container. The image excludes `.env`.

**Source-confirmed result:** default Compose startup reaches the guard and
exits when no explicit container token override is supplied. Host loopback
port publishing does not change the address inspected by this guard.

**Fix direction:** wire an explicit required token into Compose and document
client authentication. Keep the startup protection intact.

## Validation and limits

- Broad Python fast suite: **2,442 passed, 2 failed, 2 skipped, 13 deselected**
  in 148.12 seconds. Output: `reports/diagnosis-pytest.txt`.
- Focused rerun: **both failed tests passed**. The SQLite test needed its
  temporary directory within the configured artifact base. The cancellation
  test did not reproduce its earlier `page_calls=3` failure in isolation;
  timing/order sensitivity remains unresolved. Output:
  `reports/diagnosis-focused.txt`. The initial broad run is not claimed green.
- Ruff: **all checks passed**, `reports/diagnosis-ruff.txt`.
- Two temporary Flutter diagnostic tests **reproduced** the URL crash and
  stuck completion state. Their assertions described the current bugs, not
  desired behavior, so the temporary test was removed after execution.
  Output: `reports/diagnosis-flutter-repro.txt`.
- Flutter static analysis: **no issues found** after removing the temporary
  diagnostic test. Output: `reports/diagnosis-flutter-analyze.txt`.
- Local `/api/health` and `/ready` both returned HTTP 200 during the initial
  investigation. This says nothing about external provider availability.
- No paid/cloud model requests, model downloads, full live OCR benchmark,
  container deployment, or full Flutter suite were performed. Findings marked
  source-confirmed have a traced deterministic path rather than a live UI or
  provider reproduction.

## Recommended repair order

Separate provider credentials first, then repair URL rebuilds and async job
completion. Align async translation and OCR artifact contracts next. Finally
wire processing settings, upload/mode validation, queue failure recovery, and
Compose configuration. Add focused cross-boundary regression checks for these
flows; isolated mocks currently allow the mismatched interfaces to pass.
