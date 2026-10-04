# Flutter modularity refactor — 2026-09-29

This is the dated refactor record. Before snippets, move tables and verification
counts describe 2026-09-29; they are not fresh checks of the current tree.
Current compatibility descriptions below include the 2026-09-30 barrel
consolidation; its verification belongs to the
[document-state review](document-state-review-2026-09-30.md).

Implemented against the starting working tree, preserving endpoint paths, payload field names, capability-token handling, OCR/queue behavior and existing feature state-provider identities. No dependencies added. The removed `featureRepositoryProvider` is replaced by four independently overridable domain adapter providers. Existing repository interfaces for OCR/config/provider/history remain because current tests override them; new adapters are concrete classes.

## Concrete final layout

```text
client/lib/
  main.dart                         bootstrap; selects only theme flag
  app/                              shell, navigation, health and view composition
  core/
    constants/, enums/, theme/      existing shared definitions
    network/                       existing HTTP transport/errors
    serialization/json_fields.dart required wire-field decoding
    websocket/                     transport plus typed event envelopes
  shared/
    widgets/                       reusable presentation
    providers/api_providers.dart   auth, URL, HTTP and websocket wiring
    providers/repository_providers.dart export-only test composition
  features/
    workstation/                   OCR models/options/state/API/canvas/controls
    settings/                      runtime DTOs/state/API/form
    providers/                     provider DTOs/state/API/setup UI
    jobs/                          job DTOs/state/API/execution/history
    translation/                   models/state/notifier/API/screen
    transcription/                 models/state/notifier/API/screen
    glossary/                      models/state/notifier/API/screen
    documents/                     extraction/export models/state/API/UI
  data/                            four aggregate compatibility exports only
client/test/data/modularity_regression_test.dart
client/tool/check_feature_contracts.dart
client/tool/check_feature_layout.py
```

## Exact before/after changes

### Feature ownership and strict data flow

The complete relocation table below applies this import-only change to source, unit/widget and integration consumers. No implementation was replaced during moves.

Before (`presentation/workstation/workstation_screen.dart`):
```dart
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
```
After (`features/workstation/workstation_screen.dart`):
```dart
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';
```

Before (`data/providers/features_notifier.dart`, four unrelated domains):
```dart
class TranslationNotifier extends Notifier<TranslationState> {
  FeatureRepository get _repo => ref.read(featureRepositoryProvider);
```
After (`features/translation/translation_notifier.dart`; equivalent concrete adapter ownership for transcription/glossary/documents):
```dart
class TranslationNotifier extends Notifier<TranslationState> {
  TranslationRepository get _repo => ref.read(translationRepositoryProvider);
```

Before (`data/repositories/feature_repository.dart`):
```dart
abstract class FeatureRepository {
  Future<TranslationResponse> translate(TranslationRequest request);
  Future<ExtractionResponse> extractStructuredData(ExtractionRequest request);
  Future<List<GlossaryListItem>> getGlossaryLibraries();
```
After (`features/translation/translation_repository.dart`; each domain owns its existing operations):
```dart
final translationRepositoryProvider = Provider<TranslationRepository>(
  (ref) => TranslationRepository(ref.watch(apiClientProvider)),
);

class TranslationRepository {
  const TranslationRepository(this._apiClient);

  final ApiClient _apiClient;

  Future<TranslationResponse> translate(TranslationRequest request) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.translate,
      data: request.toJson(),
    );
    return TranslationResponse.fromJson(json);
  }
```

Provider transport/auth/WebSocket declarations moved intact from the combined repository wiring to `shared/providers/api_providers.dart`. OCR/config/provider/job/sample-PDF adapter provider declarations moved intact beside their existing adapters. Production callers import only those owners. The shared composition barrel contains exports only and is consumed by cross-feature tests, avoiding feature→shared→all-feature implementation dependencies.

Before (runtime config shared a file with OCR processing options):
```dart
import 'package:omniscribe_client/data/models/process_settings.dart';
```
After (configuration consumers):
```dart
import 'package:omniscribe_client/features/settings/runtime_config.dart';
```
OCR processing consumers import `features/workstation/process_settings.dart`. The old aggregate `data/models/models.dart` still exports both sets of types for existing multi-domain tests.

### Export orchestration leaves the widget

Before (`presentation/workstation/modals/export_modal.dart`):
```dart
final repo = ref.read(featureRepositoryProvider);
final bytes = await repo.exportDocxTree(
  ExportBlockTreeRequest(
    textArtifactId: jobState.textArtifactId!,
    textArtifactToken: jobState.textArtifactToken!,
  ),
);
```
After (`features/documents/export_modal.dart`):
```dart
final prepared = await exporter.prepare(_selectedFormat);
if (!mounted) return;
await _saveWithPicker(
  fileName: prepared.filename,
  bytes: prepared.bytes,
  formatLabel: prepared.label,
);
```
`DocumentExportNotifier.prepare` owns the existing format switch, whitespace filtering, DOCX filename fallback, escaped HTML, JSON/text generation and artifact validation. The view owns the platform save dialog. It rejects repeated preparation and restores busy state in `finally`, guarded against disposal.

Before (picker cancellation and exception were both presented as success):
```dart
} catch (_) {
  _statusMessage = '$formatLabel ready ($kb KB).';
}
_isSuccess = true;
```
After:
```dart
} else {
  _statusMessage = '$formatLabel save cancelled ($kb KB ready).';
  _isSuccess = false;
}
} catch (error) {
  _statusMessage = '$formatLabel save failed: $error ($kb KB ready).';
  _isSuccess = false;
}
```

### Strict response decoding, without code generation

Before (`feature_models.dart`):
```dart
translatedText: json['translated_text']?.toString() ?? '',
start: (json['start'] as num?)?.toDouble() ?? 0.0,
id: (json['id'] as num?)?.toInt(),
extractedData: json['extracted_data'] ?? json,
```
After (respective domain models):
```dart
translatedText: jsonString(json, 'translated_text'),
start: jsonDouble(json, 'start'),
id: jsonInt(json, 'id'),
```
```dart
if (!json.containsKey('extracted_data')) {
  throw const FormatException('Extraction response is missing extracted_data.');
}
return ExtractionResponse(
  extractedData: json['extracted_data'],
);
```

`jsonString`, `jsonDouble` and `jsonInt` reject the wrong type with the field name; finite timing/duration rejects NaN/infinity. Translation state still accepts either the existing `state` or `status` spelling, but requires one to be a string. Optional strings now use `as String?`, preserving absent/null optional values and rejecting coercion. Parsed segment/extra collections and glossary repository results are unmodifiable. Required glossary entry fields and document export handles cannot become empty fallback fields. Malformed glossary list/entry envelopes now throw contextual errors instead of silently returning an empty list or dropping bad items.

Async submission and stored glossary library decoding also enforce the backend's required fields.

Before (`ProcessResponse.fromJson` and `GlossaryListItem.fromJson`):
```dart
jobId: json['job_id']?.toString() ?? '',
status: json['status']?.toString() ?? 'pending',
entryCount: (json['entry_count'] as num?)?.toInt() ?? 0,
```
After (`features/jobs/job_record.dart` and `features/glossary/glossary_models.dart`):
```dart
jobId: jsonString(json, 'job_id'),
status: jsonString(json, 'status'),
entryCount: jsonInt(json, 'entry_count'),
```

Before (glossary list silently skipped a malformed payload):
```dart
if (response is List) {
  for (final item in response) {
    if (item is Map<String, dynamic>) {
      list.add(GlossaryListItem.fromJson(item));
    }
  }
}
return list;
```
After (`features/glossary/glossary_repository.dart`):
```dart
if (response is! List || response.any((item) => item is! Map<String, dynamic>)) {
  throw const FormatException('Malformed glossary library response.');
}
return List.unmodifiable(response.map((item) =>
    GlossaryListItem.fromJson(item as Map<String, dynamic>)));
```

### Two evidenced payload fixes

Before (the UI-selected `faster-whisper` fell through to `auto`):
```dart
whisperLocal('whisper_local'),
auto('auto');
```
After (`features/transcription/transcription_models.dart`):
```dart
whisperLocal('whisper_local'),
fasterWhisper('faster-whisper'),
fasterWhisperUnderscore('faster_whisper'),
auto('auto');
```
Unknown non-null engine values now throw `FormatException`; null still means auto. Glossary enum roundtrips also include the backend's existing `lanes_sqlite` and `lanes_xml` values, and unknown non-null formats throw instead of labeling them CSV. The import UI choices are unchanged.

Before (`importGlossaryJson`):
```dart
filename: '${name ?? "glossary"}.${format.value}',
```
After (`features/glossary/glossary_notifier.dart`):
```dart
filename: '${name ?? "glossary"}.${format == GlossaryFormat.jsonPairs ? "json" : format.value}',
```
The backend infers JSON pairs from `.json`, so the synthesized `.json_pairs` filename no longer fails format inference.

### Async state safety

Before (`loadEntries`):
```dart
final entries = await _repo.getGlossaryEntries(lib.id);
state = state.copyWith(
  entries: entries,
  activeViewIndex: 1,
  isLoading: false,
);
```
After:
```dart
final runId = ++_entriesEpoch;
```
```dart
final entries = await _repo.getGlossaryEntries(lib.id);
if (!ref.mounted || runId != _entriesEpoch) return;
state = state.copyWith(
  entries: entries,
  activeViewIndex: 1,
  isLoading: false,
);
```
Selection changes and deletion of the selected library invalidate pending entry requests; obsolete failures are also ignored. All glossary continuations check disposal, and import error paths retain their original rethrow behavior.

Before (`transcribe` / `extract` permitted duplicate submissions and wrote after disposal):
```dart
final res = await _repo.extractStructuredData(req);
state = state.copyWith(
```
After (`extraction_notifier.dart`):
```dart
if (state.isExtracting) return;
```
```dart
final res = await _repo.extractStructuredData(req);
if (!ref.mounted) return;
state = state.copyWith(
```
Transcription uses the existing translation-style run epoch: changing/clearing selected audio invalidates pending results, resets result/busy state, repeated submit returns early, and both success/failure continuations verify mounted plus the epoch.

The native engine regression run exposed an additional existing lifecycle defect: a document-preview request failed after the workstation provider was disposed, and its catch block attempted to write state. All callers of the shared preview helpers were traced. Success/failure paths now check the existing document generation and mounted state; an obsolete completion also leaves the current document's busy state untouched. The page-preview fallback, first-preview callback, background preloader and async document clear continuation use the same boundary. Background preload errors now produce a contextual preview error for the current document rather than disappearing silently.

Before (`_loadDocumentPreview`):
```dart
} catch (e) {
  state = state.copyWith(
    isPreviewLoading: false,
    previewError: 'Failed to generate page preview: ${e.toString()}',
  );
  return null;
}
```
After (`features/workstation/workstation_notifier.dart`):
```dart
} catch (e) {
  if (!ref.mounted || targetGeneration != _preloadGeneration) return null;
  state = state.copyWith(
    isPreviewLoading: false,
    previewError: 'Failed to generate page preview: ${e.toString()}',
  );
  return null;
}
```

`modularity_regression_test.dart` contains a delayed preview-error/disposal test. Its export test overrides the OCR adapter, keeping the regression independent of a running backend.

### Narrow subscriptions remove unnecessary rebuilds

Before (`main.dart`):
```dart
final settings = ref.watch(settingsStateProvider);
themeMode: settings.isDarkMode ? ThemeMode.dark : ThemeMode.light,
```
After:
```dart
final isDarkMode = ref.watch(settingsStateProvider.select((s) => s.isDarkMode));
themeMode: isDarkMode ? ThemeMode.dark : ThemeMode.light,
```

Before (`quality_repair_dock.dart`):
```dart
final job = ref.watch(jobOrchestrationProvider);
final repairedCount =
    job.repairedCount(ref.watch(workstationProvider).documentRevisedCount);
final retriesAttempted = job.totalRetriesAttempted;
final avgConf = job.avgConfidence;
```
After:
```dart
final revisedCount = ref.watch(
  workstationProvider.select((state) => state.documentRevisedCount),
);
final (repairedCount, retriesAttempted, avgConf) = ref.watch(
  jobOrchestrationProvider.select((job) => (
    job.repairedCount(revisedCount),
    job.totalRetriesAttempted,
    job.avgConfidence,
  )),
);
```

## Every relocated production file

All paths in this table are relative to `client/lib/`; each destination is the canonical implementation owner.

| Before | After | Single responsibility |
|---|---|---|
| `data/models/bbox_item.dart` | `features/workstation/bbox_item.dart` | Immutable OCR bounding-box wire model. |
| `data/models/document_result.dart` | `features/workstation/document_result.dart` | OCR page, trust, quality, document and text-artifact response models. |
| `data/models/job_record.dart` | `features/jobs/job_record.dart` | Job history, OCR status and asynchronous submission wire models. |
| `data/models/process_settings.dart` | `features/workstation/process_settings.dart` | OCR processing options and processing enums. |
| `data/models/provider_preset.dart` | `features/providers/provider_preset.dart` | Provider catalog, discovery, validation and activation wire models. |
| `data/models/smart_preset.dart` | `features/workstation/smart_preset.dart` | Named OCR setting presets. |
| `data/models/ws_frames.dart` | `core/websocket/ws_frames.dart` | Typed WebSocket event envelope parsing shared by processing features. |
| `data/providers/document_selection_notifier.dart` | `features/workstation/document_selection_notifier.dart` | Selected OCR block state. |
| `data/providers/document_viewport_notifier.dart` | `features/workstation/document_viewport_notifier.dart` | Canvas zoom, pan and fit state. |
| `data/providers/job_orchestration_notifier.dart` | `features/jobs/job_orchestration_notifier.dart` | OCR execution, streaming, queue polling and artifact lifecycle. |
| `data/providers/jobs_notifier.dart` | `features/jobs/jobs_notifier.dart` | History loading, cancellation and result download state. |
| `data/providers/jobs_state.dart` | `features/jobs/jobs_state.dart` | Immutable job history/filter state. |
| `data/providers/provider_browser_state.dart` | `features/providers/provider_browser_state.dart` | Immutable provider discovery and validation UI state. |
| `data/providers/provider_notifier.dart` | `features/providers/provider_notifier.dart` | Provider discovery, activation and validation orchestration. |
| `data/providers/repository_providers.dart` | `shared/providers/repository_providers.dart` | Export-only composition barrel retained for multi-feature tests; production uses precise owner imports. |
| `data/providers/settings_notifier.dart` | `features/settings/settings_notifier.dart` | Configuration load/save and settings state transitions. |
| `data/providers/settings_state.dart` | `features/settings/settings_state.dart` | Immutable runtime configuration, theme and settings form state. |
| `data/providers/workstation_notifier.dart` | `features/workstation/workstation_notifier.dart` | Loaded document, OCR options and bounding-box state transitions. |
| `data/providers/workstation_state.dart` | `features/workstation/workstation_state.dart` | Immutable loaded document and OCR canvas state. |
| `data/repositories/config_repository.dart` | `features/settings/config_repository.dart` | Runtime configuration HTTP operations and existing test seam; owns config adapter provider. |
| `data/repositories/job_repository.dart` | `features/jobs/job_repository.dart` | History/result HTTP operations and existing test seam; owns job adapter provider. |
| `data/repositories/ocr_repository.dart` | `features/workstation/ocr_repository.dart` | OCR HTTP/SSE operations and its existing test seam; owns OCR adapter provider. |
| `data/repositories/provider_repository.dart` | `features/providers/provider_repository.dart` | Provider catalog/discovery HTTP operations and existing test seam; owns provider adapter provider. |
| `data/repositories/sample_pdf_repository.dart` | `features/workstation/sample_pdf_repository.dart` | Server sample-PDF download; owns its adapter provider. |
| `presentation/common/app_badge.dart` | `shared/widgets/app_badge.dart` | Shared status badge. |
| `presentation/common/app_button.dart` | `shared/widgets/app_button.dart` | Shared accessible application button. |
| `presentation/common/app_card.dart` | `shared/widgets/app_card.dart` | Shared card surface. |
| `presentation/common/app_input.dart` | `shared/widgets/app_input.dart` | Shared text input. |
| `presentation/common/app_modal.dart` | `shared/widgets/app_modal.dart` | Shared dialog frame. |
| `presentation/common/app_select.dart` | `shared/widgets/app_select.dart` | Shared typed dropdown. |
| `presentation/common/app_toggle.dart` | `shared/widgets/app_toggle.dart` | Shared switch control. |
| `presentation/common/auth_required_banner.dart` | `shared/widgets/auth_required_banner.dart` | Authentication-required banner. |
| `presentation/common/common.dart` | `shared/widgets/common.dart` | Shared widget public exports. |
| `presentation/common/error_banner.dart` | `shared/widgets/error_banner.dart` | Shared dismissible error presentation. |
| `presentation/common/feature_screen_scaffold.dart` | `shared/widgets/feature_screen_scaffold.dart` | Shared feature-view chrome and pane layout. |
| `presentation/common/section_header.dart` | `shared/widgets/section_header.dart` | Shared section title/action UI. |
| `presentation/common/toast_overlay.dart` | `shared/widgets/toast_overlay.dart` | Shared toast overlay rendering. |
| `presentation/common/toast_service.dart` | `shared/widgets/toast_service.dart` | Shared toast message state and timing. |
| `presentation/features/extraction_screen.dart` | `features/documents/extraction_screen.dart` | Structured extraction input/template/result UI. |
| `presentation/features/glossary_screen.dart` | `features/glossary/glossary_screen.dart` | Glossary library/import/entry UI. |
| `presentation/features/transcription_screen.dart` | `features/transcription/transcription_screen.dart` | Audio selection, transcription controls and segment presentation. |
| `presentation/features/translation_screen.dart` | `features/translation/translation_screen.dart` | Translation controls and result presentation. |
| `presentation/jobs/job_history_screen.dart` | `features/jobs/job_history_screen.dart` | Job history table and history actions UI. |
| `presentation/providers/ai_setup_wizard_modal.dart` | `features/providers/ai_setup_wizard_modal.dart` | AI provider setup wizard UI. |
| `presentation/providers/provider_card.dart` | `features/providers/provider_card.dart` | Provider catalog item UI. |
| `presentation/providers/provider_modal.dart` | `features/providers/provider_modal.dart` | Provider catalog/discovery modal UI. |
| `presentation/settings/settings_screen.dart` | `features/settings/settings_screen.dart` | Runtime configuration form UI. |
| `presentation/shell/app_shell.dart` | `app/app_shell.dart` | Top-level application chrome, navigation and authentication banner. |
| `presentation/shell/server_health_badge.dart` | `app/server_health_badge.dart` | Backend connection status presentation. |
| `presentation/shell/shell.dart` | `app/shell.dart` | Application-shell public exports. |
| `presentation/shell/shell_state.dart` | `app/shell_state.dart` | Navigation, selected provider/theme and server-health state owners. |
| `presentation/shell/tab_ribbon.dart` | `app/tab_ribbon.dart` | Accessible top-level navigation ribbon. |
| `presentation/shell/workspace_view.dart` | `app/workspace_view.dart` | Active feature view composition. |
| `presentation/workstation/canvas/bbox_inspector.dart` | `features/workstation/canvas/bbox_inspector.dart` | Selected OCR block detail UI. |
| `presentation/workstation/canvas/bbox_painter.dart` | `features/workstation/canvas/bbox_painter.dart` | OCR bounding-box canvas painting. |
| `presentation/workstation/canvas/document_viewport.dart` | `features/workstation/canvas/document_viewport.dart` | OCR page canvas UI and gestures. |
| `presentation/workstation/controls/components/ai_engine_status_card.dart` | `features/workstation/controls/components/ai_engine_status_card.dart` | Active OCR AI engine status presentation. |
| `presentation/workstation/controls/components/document_processors_card.dart` | `features/workstation/controls/components/document_processors_card.dart` | Optional document processor controls. |
| `presentation/workstation/controls/components/execution_options_section.dart` | `features/workstation/controls/components/execution_options_section.dart` | OCR execution/queue options controls. |
| `presentation/workstation/controls/components/image_preprocessing_card.dart` | `features/workstation/controls/components/image_preprocessing_card.dart` | Image preprocessing controls. |
| `presentation/workstation/controls/components/smart_preset_section.dart` | `features/workstation/controls/components/smart_preset_section.dart` | OCR preset selection section. |
| `presentation/workstation/controls/components/workstation_action_buttons.dart` | `features/workstation/controls/components/workstation_action_buttons.dart` | OCR execution, cancellation and export action buttons. |
| `presentation/workstation/controls/page_strip.dart` | `features/workstation/controls/page_strip.dart` | OCR page thumbnail/navigation UI. |
| `presentation/workstation/controls/quality_repair_dock.dart` | `features/workstation/controls/quality_repair_dock.dart` | Repair settings and live repair metrics UI; watches only consumed metrics. |
| `presentation/workstation/controls/right_control_dock.dart` | `features/workstation/controls/right_control_dock.dart` | OCR processing-control composition. |
| `presentation/workstation/controls/smart_preset_selector.dart` | `features/workstation/controls/smart_preset_selector.dart` | OCR preset selector UI. |
| `presentation/workstation/controls/trust_breakdown_panel.dart` | `features/workstation/controls/trust_breakdown_panel.dart` | Trust and confidence breakdown UI. |
| `presentation/workstation/controls/upload_dropzone.dart` | `features/workstation/controls/upload_dropzone.dart` | Document picker/drop UI. |
| `presentation/workstation/modals/export_modal.dart` | `features/documents/export_modal.dart` | Export format selection and platform save-dialog UI; delegates export preparation to documentExportProvider. |
| `presentation/workstation/progress/bottom_progress_dock.dart` | `features/workstation/progress/bottom_progress_dock.dart` | Processing progress and execution log UI. |
| `presentation/workstation/workstation_screen.dart` | `features/workstation/workstation_screen.dart` | OCR workstation UI composition. |

## Every newly split/added production file

| File under `client/lib/` | Single responsibility |
|---|---|
| `core/serialization/json_fields.dart` | Three dependency-free required wire-field readers (string, finite number, integer) with contextual FormatException errors. |
| `features/documents/document_repository.dart` | Concrete documents API adapter and its domain-owned Riverpod provider; no new interface or pass-through service. |
| `features/documents/documents_models.dart` | Documents request/response models and applicable enums. |
| `features/documents/export_notifier.dart` | Export format/prepared bytes models and state owner for representation generation, artifact checks and DOCX API calls. |
| `features/documents/extraction_notifier.dart` | Extraction state transitions and async side effects using its domain API adapter. |
| `features/documents/extraction_state.dart` | Immutable extraction UI state; extracted unchanged except ownership. |
| `features/glossary/glossary_models.dart` | Glossary request/response models and applicable enums. |
| `features/glossary/glossary_notifier.dart` | Glossary state transitions and async side effects using its domain API adapter. |
| `features/glossary/glossary_repository.dart` | Concrete glossary API adapter and its domain-owned Riverpod provider; no new interface or pass-through service. |
| `features/glossary/glossary_state.dart` | Immutable glossary UI state; extracted unchanged except ownership. |
| `features/settings/runtime_config.dart` | Runtime configuration read/update DTOs, extracted from processing settings. |
| `features/transcription/transcription_models.dart` | Transcription request/response models and applicable enums. |
| `features/transcription/transcription_notifier.dart` | Transcription state transitions and async side effects using its domain API adapter. |
| `features/transcription/transcription_repository.dart` | Concrete transcription API adapter and its domain-owned Riverpod provider; no new interface or pass-through service. |
| `features/transcription/transcription_state.dart` | Immutable transcription UI state; extracted unchanged except ownership. |
| `features/translation/translation_models.dart` | Translation request/response models and applicable enums. |
| `features/translation/translation_notifier.dart` | Translation state transitions and async side effects using its domain API adapter. |
| `features/translation/translation_repository.dart` | Concrete translation API adapter and its domain-owned Riverpod provider; no new interface or pass-through service. |
| `features/translation/translation_state.dart` | Immutable translation UI state; extracted unchanged except ownership. |
| `shared/providers/api_providers.dart` | Transport base URL, auth flags, HTTP client and WebSocket lifetime provider wiring; contains no feature adapter imports. |

## Compatibility and verification artifacts

The current `data/models/models.dart`, `data/providers/{features_state,features_notifier}.dart`, and `data/repositories/repositories.dart` are four import-only aggregate exports consumed by existing multi-domain tests. On 2026-09-30, the nested `feature_models.dart` and `feature_repository.dart` exports were combined into their parent barrels and their consumers migrated. They contain no implementation, provider state, or old all-feature API interface. Historical before snippets and the 2026-09-29 move table retain their original paths. A redundant temporary `data/models/process_settings.dart` export was removed during the original refactor because all consumers migrated.

`client/test/data/modularity_regression_test.dart` adds six focused Flutter tests: out-of-order glossary selection, JSON filename inference, transcription engine/repeated/stale submission, extraction duplicate/disposal, escaped HTML/missing export artifacts, and delayed document-preview failure after disposal. Existing mock implementations now implement the concrete domain adapters and override the four providers independently; optional integration mock wiring uses a guarded collection spread so absent feature mocks do not become nullable override arguments.

`client/tool/check_feature_contracts.dart` is an assertion-based, dependency-free executable proof of strict response parsing and enum roundtrips. `client/tool/check_feature_layout.py` checks local import targets, all eight feature roots, UI→state ownership, unique provider declarations, and absence of old production layer imports or shared transport→feature adapter dependencies.

## Verification evidence and practical limits

The following results were recorded on 2026-09-29. Later file consolidation and
state repairs require their own checks; the historical 168-file count is not
the current ownership count.

- Passed: `rtk proxy C:\src\flutter\bin\cache\dart-sdk\bin\dart.exe --enable-asserts tool/check_feature_contracts.dart` (from `client`), output `Feature JSON contracts passed.`
- Passed: `rtk proxy .venv/Scripts/python.exe client/tool/check_feature_layout.py` (from the workspace root), output `Feature layout passed: 168 Dart files, 31 unique provider declarations.`
- Passed: `rtk git diff --check`.
- Passed: direct `dart_style` formatting using the installed Flutter tools dependency configuration, with workspace-only writes; final rerun reported zero formatting changes.
- Parent verification: direct Flutter frontend compiler compiled `lib/main.dart` and all 56 unit/widget/integration entrypoints, including the new focused regressions, with zero compilation errors, using read-only access to installed dependencies and output files confined to the workspace.
- Native runtime verification: all 43 unit/widget entrypoints reported successful assertions; 41 engines exited cleanly. The feature-notifier and provider-notifier suites retained an engine process until shutdown timeout after reporting `All tests passed!` without test errors. All 13 presentation/widget suites and both final focused reruns exited cleanly. Per-file evidence is `.agent-tmp/native-review/summary-final.json` and the linked independent review.
- Baseline and conventional analyzer/runtime attempts were blocked: `dart analyze` cannot create `C:\Users\rahin\AppData\Roaming\.dart-tool`; direct `flutter_tools.snapshot test --no-pub` cannot read/write `Roaming\.flutter_tool_state`; analysis-server initialization also requires denied user cache state. No locked directories, SDK caches, telemetry paths or user configuration were modified or redirected. Conventional launcher-based tests remain blocked; parent subsequently ran native FlutterTester engines directly with workspace-only compiler/runtime artifacts. Final runtime totals are recorded in the parent report.

The source move has import checks, direct Flutter kernel compilation, and native test-engine execution; interactive device/browser execution is separate. Existing handwritten final-field models and const constructors were retained; generated model dependencies were unnecessary. Constructors accepting externally supplied collections retain their existing semantics; parsed collections are hardened where touched. The simulated transcription player still rebuilds its feature screen on playback ticks; splitting its UI was outside the evidenced minimal repair controls/theme subscriptions. OCR execution remains in its existing cohesive orchestrator rather than being split into extra forwarding layers.
