# Flutter/Python contract and boundary review

Reviewed against the working tree on 2026-09-29. Findings and verification below are historical refactor evidence, not new failures or fresh test results. Initial references name files before the feature migration. The ownership section maps these references to feature-owned implementations; its compatibility paths include the 2026-09-30 consolidation. Follow-up document-state fixes and their proof are in [the 2026-09-30 review](document-state-review-2026-09-30.md).

## Evidence-ranked findings

### 1. Transcription engine selection changes silently on the wire

**High confidence, functional contract defect.** `client/lib/presentation/features/transcription_screen.dart:171` offers `faster-whisper`. `client/lib/data/models/feature_models.dart:75-87` does not contain that value, and its parser defaults to `auto`. `client/lib/data/providers/features_notifier.dart:446` passes the parsed value to the repository. Python accepts both spellings in `src/omniscribe/plugins/transcribe/schemas.py:39-40`. Choosing local Faster-Whisper therefore submits `auto`, which can select a different engine.

Before:

```dart
enum TranscriptionEngineType {
  api('api'),
  whisperApi('whisper_api'),
  local('local'),
  whisperLocal('whisper_local'),
  auto('auto');
}
```

After, in `client/lib/features/transcription/transcription_models.dart`, preserve exact Python wire values:

```dart
  fasterWhisper('faster-whisper'),
  fasterWhisperUnderscore('faster_whisper'),
```

Unknown non-null engine names now throw `FormatException` rather than selecting `auto`. Python's actual factory (`src/omniscribe/core/transcription/factory.py`) normalizes both spellings to `whisper_local`; `auto` can fall back to API when local dependencies are absent.

Proof: deserialize each spelling, serialize a `TranscriptionRequest`, and assert `engine` is unchanged. No engine strategy interface is needed.

### 2. Default inline glossary import creates an unrecognised extension

**High confidence, functional contract defect.** The inline modal defaults to `json_pairs` (`client/lib/presentation/features/glossary_screen.dart:561`). `importGlossaryJson` names its multipart upload `${name ?? "glossary"}.${format.value}` (`client/lib/data/providers/features_notifier.dart:660`). Python infers formats by suffix and accepts `json`, not `json_pairs` (`src/omniscribe/plugins/glossary/routes.py:39-46`). Default inline JSON import therefore receives 422 before parsing its content.

Before:

```dart
filename: '${name ?? "glossary"}.${format.value}',
```

After, in `client/lib/features/glossary/glossary_notifier.dart`:

```dart
filename: '${name ?? "glossary"}.${format == GlossaryFormat.jsonPairs ? "json" : format.value}',
```

Proof: capture the uploaded filename in the existing fake repository; assert JSON pairs uses `.json`, and CSV retains `.csv`. The alternative is to carry the selected format explicitly in the existing multipart method, which is useful if more non-file formats are exposed later.

The same contract pass adds Python's existing Lane's Lexicon formats to the Dart enum, so libraries returned by the server retain their actual format:

```dart
// Before: enum stopped at jsonPairs('json_pairs').
lanesSqlite('lanes_sqlite'),
lanesXml('lanes_xml');
// Before: unknown non-null strings returned GlossaryFormat.csv.
throw FormatException('Unsupported glossary format: $value');
```

### 3. Validation responses fail JSON serialization

**Runtime confirmed, boundary defect.** `src/omniscribe/plugins/transcribe/routes.py:66-69` puts `ValidationError.errors()` directly into `JSONResponse`. Custom validators store `ValueError` in `ctx.error`. Starlette cannot JSON-encode that object; malformed engine/temperature input can return a server failure instead of the intended 422. `src/omniscribe/plugins/glossary/routes.py` repeats the response pattern.

Before:

```python
return JSONResponse(
    status_code=422,
    content={"detail": exc.errors(include_url=False)},
)
```

After, retain the existing FastAPI-style detail array:

```python
from fastapi.encoders import jsonable_encoder

return JSONResponse(
    status_code=422,
    content={"detail": jsonable_encoder(exc.errors(include_url=False))},
)
```

Runtime reproduction with the project's `.venv/Scripts/python.exe`: validate `{'engine': 'invalid'}` through `TranscribeRequest`, then build the current JSON response. Result: `TypeError: Object of type ValueError is not JSON serializable`.

Proof: route requests containing an invalid engine and a non-float temperature must return 422 with a JSON detail list. Do not replace schema validation errors with a string envelope; Flutter's shared transport already retains structured `detail` and supplies the missing `validation_error` tag.

### 4. Response factories hide malformed success payloads

**High confidence, contract validation gap.** `TranslationResponse.fromJson` turns absent `translated_text` into an empty result and numbers into strings (`client/lib/data/models/feature_models.dart:187-190`). `TranscriptionResponse.fromJson` skips invalid segment elements and defaults missing text (`:379-393`). `DocumentExportResult.fromJson` substitutes empty artifact credentials (`:785-791`). This lets malformed HTTP 200 responses reach successful UI state.

Before:

```dart
translatedText: json['translated_text']?.toString() ?? '',
```

After, the feature-owned models use the small shared `client/lib/core/serialization/json_fields.dart` boundary readers; HTTP repository methods add contextual format errors for malformed translation/transcription payloads:

```dart
factory TranslationResponse.fromJson(Map<String, dynamic> json) {
  return TranslationResponse(
    translatedText: jsonString(json, 'translated_text'),
  );
}
```

The shared readers produce contextual `FormatException`s for missing or wrongly typed strings/integers and reject non-finite numbers. Decoded transcription segments and glossary lists are unmodifiable. The same required-field rule applies to Python-required response fields; preserve documented optional fields and empty strings when the server permits them. Transcription's optional segment list may be absent, but a present list must contain objects with integer IDs, finite numeric timestamps and string text. Keep dynamic extraction results where the server intentionally accepts arbitrary JSON; require the `extracted_data` key instead of silently substituting the entire response.

Transcription segment fields before:

```dart
id: (json['id'] as num?)?.toInt(),
start: (json['start'] as num?)?.toDouble() ?? 0.0,
end: (json['end'] as num?)?.toDouble() ?? 0.0,
text: json['text']?.toString() ?? '',
extra: json,
```

After:

```dart
id: jsonInt(json, 'id'),
start: jsonDouble(json, 'start'),
end: jsonDouble(json, 'end'),
text: jsonString(json, 'text'),
extra: Map.unmodifiable(json),
```

Export result fields before:

```dart
artifactId: json['artifact_id']?.toString() ?? '',
token: json['token']?.toString() ?? '',
format: json['format']?.toString() ?? 'markdown',
```

After:

```dart
artifactId: jsonString(json, 'artifact_id'),
token: jsonString(json, 'token'),
format: jsonString(json, 'format'),
```

Actual shared numeric reader:

```dart
double jsonDouble(Map<String, dynamic> json, String field) {
  final value = json[field];
  if (value is! num || !value.isFinite) {
    throw FormatException('Expected a finite number for "$field".');
  }
  return value.toDouble();
}
```

Before for extraction:

```dart
extractedData: json['extracted_data'] ?? json,
```

After:

```dart
if (!json.containsKey('extracted_data')) {
  throw const FormatException('Extraction response is missing extracted_data.');
}
return ExtractionResponse(extractedData: json['extracted_data']);
```

Proof: one table-driven model test with missing/wrongly typed fields plus valid empty strings, absent optional segments, and arbitrary valid extraction data. Standard immutable Dart models suffice; generated serialization packages are unnecessary for this correction.

### 5. Async glossary requests can attach entries to the wrong library

**High confidence, ordering and lifecycle defect.** `loadEntries` changes the selected library, awaits IO, and commits entries without checking which request is current (`client/lib/data/providers/features_notifier.dart:522-541`). The UI calls it without awaiting when a library is selected (`client/lib/presentation/features/glossary_screen.dart:341`). A slow A response arriving after a fast B response leaves B selected with A's entries. Transcription/extraction also access state after awaits without checking `ref.mounted`. Translation already has the repository's local epoch-plus-mounted pattern.

Before:

```dart
final entries = await _repo.getGlossaryEntries(lib.id);
state = state.copyWith(entries: entries, activeViewIndex: 1, isLoading: false);
```

After, in the glossary notifier, reuse the existing pattern:

```dart
int _entriesEpoch = 0;

Future<void> loadEntries(GlossaryListItem lib) async {
  final epoch = ++_entriesEpoch;
  state = state.copyWith(selectedLibrary: lib, isLoading: true, clearError: true);
  try {
    final entries = await _repo.getGlossaryEntries(lib.id);
    if (!ref.mounted || epoch != _entriesEpoch) return;
    state = state.copyWith(entries: entries, activeViewIndex: 1, isLoading: false);
  } catch (error) {
    if (!ref.mounted || epoch != _entriesEpoch) return;
    state = state.copyWith(isLoading: false, error: error.toString());
  }
}
```

Invalidate pending entry loads when deleting/changing the selected library. Keep timer disposal callbacks free of state mutations. Proof: use two completers, select A then B, finish B then A, and assert selected library and entries both remain B. Dispose the provider while an operation is pending and complete it without an unmounted-state exception.

### 6. Repair controls subscribe to unrelated document/queue changes

**High confidence, rebuild scope improvement; no benchmark claim.** `client/lib/presentation/workstation/controls/quality_repair_dock.dart:27-37` watches whole workstation/job states but displays only revised count, repair count, retry count, and confidence. Page selection and queue log changes needlessly invalidate these controls.

Before:

```dart
final job = ref.watch(jobOrchestrationProvider);
final repairedCount =
    job.repairedCount(ref.watch(workstationProvider).documentRevisedCount);
final retriesAttempted = job.totalRetriesAttempted;
final avgConf = job.avgConfidence;
```

After, select the values the dock actually displays:

```dart
final revised = ref.watch(
  workstationProvider.select((state) => state.documentRevisedCount),
);
final (repairedCount, retriesAttempted, avgConf) = ref.watch(
  jobOrchestrationProvider.select((job) => (
    job.repairedCount(revised),
    job.totalRetriesAttempted,
    job.avgConfidence,
  )),
);
```

No new file is needed. Native widget tests verify the integration of these selectors; no measured performance improvement is claimed. Transcription playback profiling was outside this refactor's scope.

### 7. Export widget owns API orchestration and hides save failures

**High confidence, flow and error-reporting gap.** `client/lib/presentation/workstation/modals/export_modal.dart:101` reads the repository directly and calls export API methods from the widget (`:157`, `:176`). `_saveWithPicker` treats picker exceptions as ready/success (`:91-95`), even though no saved file/download is available. This violates the requested UI → state → API flow and masks external IO failure.

Before:

```dart
final repo = ref.read(featureRepositoryProvider);
final bytes = await repo.exportDocx(ExportDocxRequest(text: docText));
// ...
} catch (_) {
  _statusMessage = '$formatLabel ready ($kb KB).';
}
_isSuccess = true;
```

After: `client/lib/features/documents/export_notifier.dart` owns format selection, content generation, artifact validation and repository calls. Its `prepare` method returns `PreparedExport`; the widget retains the platform save dialog. Picker exceptions and user cancellation now produce distinct messages and a false success flag.

```dart
final prepared = await exporter.prepare(_selectedFormat);
if (!mounted) return;
await _saveWithPicker(
  fileName: prepared.filename,
  bytes: prepared.bytes,
  formatLabel: prepared.label,
);
```

Actual picker status handling:

```dart
if (savePath != null) {
  _statusMessage = '$formatLabel saved to $savePath.';
  _isSuccess = true;
} else {
  _statusMessage = '$formatLabel save cancelled ($kb KB ready).';
  _isSuccess = false;
}
// ...catch (error)...
_statusMessage = '$formatLabel save failed: $error ($kb KB ready).';
_isSuccess = false;
```

Verification: the native export-modal and smoke suites pass; the smoke suite stubs a cancelled picker. The new state regression checks escaped HTML and missing artifact capabilities. The picker-exception message and false-success assignments above were reviewed in source.

## Contracts already aligned

- Shared `ApiClient` maps 400/401/402/403/404/409/413/422/429/502/503 into typed exceptions and keeps server `error`/`detail`. Byte export errors are decoded before mapping. Do not duplicate HTTP error handling in each feature.
- Async translation submissions retain `result_token`. The client redeems it with `X-Artifact-Token`; completed status responses intentionally omit translated text and capability secrets. Missing/wrong tokens return the same 404 as unavailable results. Preserve this capability boundary.
- Translation polling suppresses overlapping status checks and stale/disposed completions. Timer disposal only cancels timers. Reuse this pattern where other notifiers need it.
- Python already groups translate/transcribe/glossary/documents into routes/schemas/service/plugin files. Preserve that working structure; it does not need a second domain/application/infrastructure hierarchy.
- Existing repository fake overrides remain useful test seams. The combined four-domain interface is replaced with concrete feature-owned repositories; tests can implement those concrete types. Preserve useful fake seams rather than adding an interface for every new operation.

## Final ownership and verification

Actual feature layout:

```text
client/lib/
  app/                         app_shell, tab_ribbon, workspace_view, shell_state
  core/
    network/                   ApiClient and typed HTTP exceptions
    serialization/json_fields.dart
    websocket/                 WsClient and wire frame models
  shared/
    providers/api_providers.dart
    providers/repository_providers.dart  export-only compatibility/composition barrel
    widgets/                   shared controls and feedback
  features/
    translation/               translation_models, translation_state,
                               translation_notifier, translation_repository, translation_screen
    transcription/             transcription_models, transcription_state,
                               transcription_notifier, transcription_repository, transcription_screen
    glossary/                  glossary_models, glossary_state,
                               glossary_notifier, glossary_repository, glossary_screen
    documents/                 documents_models, extraction_state,
                               extraction_notifier, document_repository, extraction_screen,
                               export_notifier, export_modal
    settings/                  runtime_config, config_repository, settings_state,
                               settings_notifier, settings_screen
    providers/                 provider_preset, provider_repository, provider_browser_state,
                               provider_notifier, provider UI
    jobs/                      job_record, job_repository, jobs_state, jobs_notifier,
                               job_orchestration_notifier, job_history_screen
    workstation/               document/bbox/process models, OCR and sample-PDF adapters,
                               workstation and document-view state, canvas, controls, progress
  data/models/models.dart
  data/providers/{features_state,features_notifier}.dart
  data/repositories/repositories.dart
                               export-only legacy imports; no duplicated provider definitions
src/omniscribe/plugins/
  translate/{routes,schemas,service,plugin}.py
  transcribe/{routes,schemas,service,config_store,plugin}.py
  glossary/{routes,schemas,service,store,http_fetch,plugin}.py
  documents/{routes,schemas,service,prompts,plugin}.py
  ocr/{routes,schemas,service,pipeline_bridge,plugin}.py
  _http.py, errors.py           existing shared HTTP error translation
```

`json_fields.dart` validates wire primitives. `api_providers.dart` owns shared transport/auth state without feature imports. Each concrete feature repository owns that feature's HTTP calls and decoding. Each notifier owns that feature's asynchronous state. `export_notifier.dart` prepares document representations; `export_modal.dart` owns the platform save interaction. Import-only barrels re-export the same declarations rather than creating provider instances. The architecture ledger carries the full file-by-file responsibility inventory.

| Finding | Implemented location | Source review result |
| --- | --- | --- |
| Engine wire values | `features/transcription/transcription_models.dart` | Both Python Faster-Whisper spellings retained; unknown names rejected. |
| Inline glossary filename | `features/glossary/glossary_notifier.dart` | JSON pairs uses `.json`; other formats preserve their suffix. |
| Serializable validation errors | Python transcribe/glossary `routes.py` | Existing 422 detail arrays passed through `jsonable_encoder`. |
| Strict successful responses | `core/serialization/json_fields.dart` and feature models/repositories | Required fields no longer become empty successful results; finite numeric checks and immutable decoded lists added. |
| Async ordering/lifecycle | Feature notifiers | Glossary entries use an epoch; transcription invalidates requests when changing audio; post-await mounted checks added. |
| Repair dock rebuild scope | `features/workstation/controls/quality_repair_dock.dart` | Record selectors cover only displayed counters/confidence. |
| Export state and save failures | `features/documents/export_notifier.dart`, `export_modal.dart` | Widget calls notifier; platform failure/cancellation is distinguished from saved success. |

Verification recorded by this reviewer on 2026-09-29:

- Reproduced the original Pydantic error serialization failure with project Python. Repeated with `jsonable_encoder`: both invalid engine and malformed temperature produce serializable 422 response bodies.
- Resolved local import/export/part URIs throughout Flutter production, unit-test and integration-test sources: zero missing paths.
- Traced project-defined class/enum/typedef/mixin visibility through local export barrels: zero unresolved project-type candidates.
- Checked directive ordering and duplicate Riverpod provider declarations: zero late directives and zero duplicate provider definitions.
- Scanned non-void `Future` notifier methods and manually verified both glossary import catch branches return their response or rethrow; no bare non-void return remains.
- Confirmed feature screens and the export modal no longer read repository/API providers directly; production sources no longer import legacy layer paths.

Backend owner's frozen verification: **237 focused tests passed**, including all router/OpenAPI checks, OCR plugin/pipeline tests, transcription service/schema tests and harness errors. The transcription-specific subset passed **14 tests**; changed-file Ruff/format checks, full mypy (**221 files**), final incremental mypy and diff whitespace checks passed. Exact backend snippets and preservation proof are in `docs/backend-refactor-details-2026-09-29.md`.

Final whole-backend fast suite: **2,658 passed, 21 skipped, 6 deselected**. The application and all **56 unit/widget/integration test entry points** compiled successfully through the installed Flutter frontend compiler. The source-layout check passes for **168 Dart files and 31 unique providers**; the JSON boundary self-check and final formatting pass also succeed.

The native runner exercised every **43 unit/widget test entry point**, including all 13 presentation/widget suites and six new modularity regressions. Final logs contain `All tests passed!` without `[E]` or `Some tests failed`: **41 engines exited cleanly**, while `features_notifier_test.dart` and `provider_notifier_test.dart` completed their assertions but remained alive until the runner shutdown timeout. The latest focused reruns of job-record parsing and modularity regressions both exited successfully. Consolidated per-file evidence is `.agent-tmp/native-review/summary-final.json`; earlier summaries include superseded attempts with incomplete runner settings.

The runner used `FLUTTER_TEST=true` and `--flutter-assets-dir` pointing to the existing `client/build/unit_test_assets`, with generated outputs inside the workspace. No asset regeneration or application change was needed for the initial runner failures. Generated compiler binaries were removed after verification; text logs remain.

Standard analyzer execution remains unverified because its launcher attempts writes to locked user telemetry directories. Those directories and SDK files were not modified. Device/browser integration scenarios were compiled but were not executed. See `docs/modularity-review-2026-09-29.md` for the complete verification scope and pre-existing repository-wide lint issues.
