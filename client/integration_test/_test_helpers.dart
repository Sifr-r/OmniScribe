import 'dart:typed_data';

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/data/models/models.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
// ConfigUpdate is a transitive type used by ConfigRepository.updateConfig
// when mocktail matchers are applied with `any()`; without registering a
// fallback the matcher library raises a `Bad state: registerFallbackValue`.
import 'package:omniscribe_client/data/repositories/repositories.dart';
import 'package:omniscribe_client/main.dart';
import 'package:riverpod/misc.dart' show Override;

import 'stub_omniscribe_server.dart';

/// Cross-platform viewport the workstation layout was tuned against; matches
/// the [tester.view.physicalSize] the widget-test suite uses for AppShell.
const Size kDesktopSurface = Size(1920, 1080);

/// Platform tags understood by [Tags]/`flutter test --platform`.
const String kPlatformWindows = 'windows';
const String kPlatformWeb = 'web';
const String kPlatformDesktop = 'desktop';

/// Initialize mocktail fallback values used by the integration test suite.
///
/// `mocktail` requires a registered fallback for every non-primitive type that
/// appears in `any()` / `captureAny()` matchers; this is a no-op once per
/// process but is called from `setUpAll` in every test that touches the
/// mocked repositories.
void registerOmniscribeFallbacks() {
  registerFallbackValue(const ProcessSettings());
  registerFallbackValue(const ConfigUpdate());
  registerFallbackValue(Uint8List(0));
  registerFallbackValue(_FakeExportDocxRequest());
  registerFallbackValue(_FakeExportBlockTreeRequest());
  registerFallbackValue(_FakeTranslationRequest());
  registerFallbackValue(_FakeTranscriptionRequest());
  registerFallbackValue(_FakeExtractionRequest());
}

/// Boots the real [OmniScribeApp] against [stub] with the optional repository
/// overrides layered on top. Returns the live container so tests can read
/// provider state.
Future<ProviderContainer> bootAppForIntegration(
  WidgetTester tester,
  StubOmniscribeServer stub, {
  List<Override> extraOverrides = const [],
  ConfigRepository? configRepo,
  ProviderRepository? providerRepo,
  MockFeatureRepository? featureRepo,
  JobRepository? jobRepo,
  OcrRepository? ocrRepo,
  SamplePdfRepository? samplePdfRepo,
}) async {
  registerOmniscribeFallbacks();

  tester.view.physicalSize = kDesktopSurface;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  final overrides = <Override>[
    apiBaseUrlProvider.overrideWith(() => _FixedBaseUrl(stub.baseUrl)),
    if (configRepo != null)
      configRepositoryProvider.overrideWithValue(configRepo),
    if (providerRepo != null)
      providerRepositoryProvider.overrideWithValue(providerRepo),
    if (featureRepo != null) ...[
      translationRepositoryProvider.overrideWithValue(featureRepo),
      transcriptionRepositoryProvider.overrideWithValue(featureRepo),
      glossaryRepositoryProvider.overrideWithValue(featureRepo),
      documentRepositoryProvider.overrideWithValue(featureRepo),
    ],
    if (jobRepo != null) jobRepositoryProvider.overrideWithValue(jobRepo),
    if (ocrRepo != null) ocrRepositoryProvider.overrideWithValue(ocrRepo),
    if (samplePdfRepo != null)
      samplePdfRepositoryProvider.overrideWithValue(samplePdfRepo),
    ...extraOverrides,
  ];

  await tester.pumpWidget(
    ProviderScope(
      overrides: overrides,
      child: const OmniScribeApp(),
    ),
  );
  // The initState of OmniScribeApp kicks off a microtask for settings + health;
  // pumpAndSettle drives both to completion so the first tab is stable.
  await tester.pumpAndSettle(const Duration(seconds: 2));

  return ProviderScope.containerOf(tester.element(find.byType(OmniScribeApp)));
}

/// Standard boot for tests that don't need the stub server but still need the
/// real provider graph (e.g. modals that only read providers from state).
Future<ProviderContainer> bootAppWithMockedProviders(
  WidgetTester tester, {
  required ConfigRepository configRepo,
  ProviderRepository? providerRepo,
  MockFeatureRepository? featureRepo,
  JobRepository? jobRepo,
  OcrRepository? ocrRepo,
  SamplePdfRepository? samplePdfRepo,
  List<Override> extraOverrides = const [],
}) async {
  registerOmniscribeFallbacks();

  tester.view.physicalSize = kDesktopSurface;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  final overrides = <Override>[
    configRepositoryProvider.overrideWithValue(configRepo),
    if (providerRepo != null)
      providerRepositoryProvider.overrideWithValue(providerRepo),
    if (featureRepo != null) ...[
      translationRepositoryProvider.overrideWithValue(featureRepo),
      transcriptionRepositoryProvider.overrideWithValue(featureRepo),
      glossaryRepositoryProvider.overrideWithValue(featureRepo),
      documentRepositoryProvider.overrideWithValue(featureRepo),
    ],
    if (jobRepo != null) jobRepositoryProvider.overrideWithValue(jobRepo),
    if (ocrRepo != null) ocrRepositoryProvider.overrideWithValue(ocrRepo),
    if (samplePdfRepo != null)
      samplePdfRepositoryProvider.overrideWithValue(samplePdfRepo),
    ...extraOverrides,
  ];

  await tester.pumpWidget(
    ProviderScope(
      overrides: overrides,
      child: const OmniScribeApp(),
    ),
  );
  await tester.pumpAndSettle(const Duration(seconds: 2));

  return ProviderScope.containerOf(tester.element(find.byType(OmniScribeApp)));
}

/// Initializes the [IntegrationTestWidgetsFlutterBinding] with the fully-live
/// frame policy used by the workstation golden-path tests. Calling this is
/// required once per `main()` — every integration test file does so at the
/// top of its `main`.
IntegrationTestWidgetsFlutterBinding initBinding() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;
  return binding;
}

/// Mock implementations of every repository the integration suite replaces.
/// Each test that mocks a repo picks what it needs and ignores the rest; the
/// unused mocks are still useful for type-safe `overrideWithValue` calls.
class MockConfigRepository extends Mock implements ConfigRepository {}

class MockProviderRepository extends Mock implements ProviderRepository {}

class MockFeatureRepository extends Mock
    implements
        TranslationRepository,
        TranscriptionRepository,
        GlossaryRepository,
        DocumentRepository {}

class MockJobRepository extends Mock implements JobRepository {}

class MockOcrRepository extends Mock implements OcrRepository {}

class MockSamplePdfRepository extends Mock implements SamplePdfRepository {}

/// Standard "boot a config repo with realistic defaults" used by settings,
/// provider modal, and AI wizard integration tests.
RuntimeConfig defaultTestRuntimeConfig({Map<String, dynamic>? overrides}) {
  return RuntimeConfig.fromJson(<String, dynamic>{
    'api_base': 'http://localhost:1234/v1',
    'api_key': 'lm-studio',
    'model': 'deepseek-ocr-2',
    'dpi': 300,
    'concurrency': 4,
    'dense_threshold': 10,
    'max_image_dim': 2048,
    'sliding_window_words': 32,
    ...?overrides,
  });
}

/// A no-network `ApiBaseUrlNotifier` override that pins the URL for the test.
class _FixedBaseUrl extends ApiBaseUrlNotifier {
  _FixedBaseUrl(this.url);
  final String url;

  @override
  String build() => url;
}

/// Fake request DTOs — mocktail needs registered fallbacks for the
/// ``any()`` matcher to handle non-primitive parameter types.
class _FakeExportDocxRequest extends Fake implements ExportDocxRequest {}

class _FakeExportBlockTreeRequest extends Fake
    implements ExportBlockTreeRequest {}

class _FakeTranslationRequest extends Fake implements TranslationRequest {}

class _FakeTranscriptionRequest extends Fake implements TranscriptionRequest {}

class _FakeExtractionRequest extends Fake implements ExtractionRequest {}

/// A tiny "wait for predicate" helper. The stub server + provider pipeline
/// have unpredictable latency under load, so fixed `pumpAndSettle` durations
/// are insufficient for state-dependent assertions.
Future<void> waitForCondition(
  WidgetTester tester,
  bool Function() condition, {
  Duration timeout = const Duration(seconds: 10),
  Duration step = const Duration(milliseconds: 100),
  String? context,
}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    if (condition()) return;
    await tester.pump(step);
  }
  if (context != null) {
    throw StateError(
      'waitForCondition timed out after ${timeout.inSeconds}s: $context',
    );
  }
  throw StateError('waitForCondition timed out after ${timeout.inSeconds}s');
}
