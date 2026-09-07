import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/repositories/config_repository.dart';
import 'package:omniscribe_client/presentation/settings/settings_screen.dart';

class _MockConfigRepository extends Mock implements ConfigRepository {}

/// Mirrors a real LM Studio deployment: a local model is active and several
/// quality flags are switched on server-side that Settings never displays.
RuntimeConfig _serverConfig() => RuntimeConfig.fromJson(<String, dynamic>{
      'api_base': 'http://localhost:1234/v1',
      'api_key': 'lm-studio',
      'model': 'deepseek-ocr-2',
      'dpi': 300,
      'concurrency': 7,
      'dense_threshold': 42,
      'max_image_dim': 1536,
      'refine': true,
      'dual_engine': true,
      'self_correction': true,
      'spellcheck': 'basic',
    });

void main() {
  late _MockConfigRepository repo;
  late ProviderContainer container;

  setUpAll(() => registerFallbackValue(const ConfigUpdate()));

  setUp(() {
    repo = _MockConfigRepository();
    when(() => repo.getConfig()).thenAnswer((_) async => _serverConfig());
    when(() => repo.updateConfig(any()))
        .thenAnswer((_) async => _serverConfig());
    when(() => repo.getModelsForProvider(any(), apiBase: any(named: 'apiBase')))
        .thenAnswer(
      (_) async => ['deepseek-ocr-2', 'qwen3.6-35b-a3b', 'gemma-4-12b'],
    );
  });

  Future<void> pumpSettings(WidgetTester tester) async {
    // The default 800x600 surface is narrower than this desktop layout
    // expects; the tab strip overflows and the model row falls below the fold.
    await tester.binding.setSurfaceSize(const Size(1600, 1200));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    container = ProviderContainer(overrides: [
      configRepositoryProvider.overrideWithValue(repo),
    ]);
    addTearDown(container.dispose);
    await container.read(settingsStateProvider.notifier).load();

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: ThemeData.dark().copyWith(extensions: [AppColorScheme.dark()]),
          home: const Scaffold(body: SettingsScreen()),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  ConfigUpdate savedPayload(WidgetTester tester) {
    return verify(() => repo.updateConfig(captureAny())).captured.single
        as ConfigUpdate;
  }

  Finder modelField() => find.descendant(
        of: find.byKey(const ValueKey('settings-model-id')),
        matching: find.byType(EditableText),
      );

  Finder apiBaseField() => find.descendant(
        of: find.byKey(const ValueKey('settings-api-base')),
        matching: find.byType(EditableText),
      );

  Finder apiKeyField() => find.descendant(
        of: find.byKey(const ValueKey('settings-api-key')),
        matching: find.byType(EditableText),
      );

  testWidgets('shows the active model, api base, and api key in editable fields',
      (tester) async {
    await pumpSettings(tester);

    expect(find.text('Model ID'), findsOneWidget);
    expect(find.text('deepseek-ocr-2'), findsOneWidget);
    expect(find.text('API Base URL'), findsOneWidget);
    expect(find.text('API Key (optional for local)'), findsOneWidget);
    expect(find.text('http://localhost:1234/v1'), findsWidgets);
  });

  testWidgets('offers the discovered models in a picker', (tester) async {
    await pumpSettings(tester);

    await tester.tap(find.textContaining('Select model'));
    await tester.pumpAndSettle();

    expect(find.text('qwen3.6-35b-a3b'), findsOneWidget);
  });

  testWidgets('saving sends the model the user typed', (tester) async {
    await pumpSettings(tester);

    await tester.enterText(modelField(), 'my-model');
    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();

    expect(savedPayload(tester).model, 'my-model');
  });

  testWidgets('saving sends the edited apiBase and apiKey', (tester) async {
    await pumpSettings(tester);

    await tester.enterText(apiBaseField(), 'http://custom-host:8080/v1');
    await tester.enterText(apiKeyField(), 'sk-custom-secret');
    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();

    final update = savedPayload(tester);
    expect(update.apiBase, 'http://custom-host:8080/v1');
    expect(update.apiKey, 'sk-custom-secret');
  });

  testWidgets('saving leaves the model untouched when it is not edited',
      (tester) async {
    await pumpSettings(tester);

    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();

    expect(savedPayload(tester).model, 'deepseek-ocr-2');
  });

  testWidgets('saving does not reset pipeline flags the screen never shows',
      (tester) async {
    await pumpSettings(tester);

    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();

    final update = savedPayload(tester);
    // Regression: the payload used to be built from
    // ProcessSettings.defaultSettings(), so every field the screen does not
    // edit went back to the wire as a hardcoded default and silently
    // overwrote the server value.
    expect(update.refine, isNull);
    expect(update.dualEngine, isNull);
    expect(update.selfCorrection, isNull);
    expect(update.spellcheck, isNull);
    expect(update.pipelineMode, isNull);
    expect(update.denseMode, isNull);
    expect(update.documentProcessors, isNull);
  });

  testWidgets('saving does not repoint the LLM endpoint at the backend URL',
      (tester) async {
    await pumpSettings(tester);

    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();

    // "Backend Base URL" is the client -> OmniScribe address. ConfigUpdate
    // .apiBase is the server -> LLM address. Saving sends the LLM endpoint
    // configured in _apiBaseController, not the backend URL.
    final update = savedPayload(tester);
    expect(update.apiBase, 'http://localhost:1234/v1');
    expect(update.apiBase, isNot(equals('http://127.0.0.1:8000')));
    expect(container.read(settingsStateProvider).runtimeConfig?.apiBase,
        'http://localhost:1234/v1');
  });

  testWidgets('reactively updates model and apiBase when runtimeConfig changes',
      (tester) async {
    await pumpSettings(tester);

    when(() => repo.getConfig()).thenAnswer(
      (_) async => RuntimeConfig.fromJson(<String, dynamic>{
        'api_base': 'http://reactive-base:9000/v1',
        'api_key': 'key-2',
        'model': 'new-reactive-model',
      }),
    );
    await container.read(settingsStateProvider.notifier).load();
    await tester.pumpAndSettle();

    expect(find.text('new-reactive-model'), findsOneWidget);
    expect(find.text('http://reactive-base:9000/v1'), findsWidgets);
  });

  testWidgets('saving keeps the numeric fields the screen does show',
      (tester) async {
    await pumpSettings(tester);

    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();

    final update = savedPayload(tester);
    expect(update.dpi, 300);
    expect(update.concurrency, 7);
    expect(update.denseThreshold, 42);
    expect(update.maxImageDim, 1536);
  });

  testWidgets('a bearer token is masked and reaches the transport layer',
      (tester) async {
    await pumpSettings(tester);

    final bearerField = find.descendant(
      of: find.byKey(const ValueKey('settings-bearer-token')),
      matching: find.byType(EditableText),
    );
    expect(tester.widget<EditableText>(bearerField).obscureText, isTrue);

    await tester.enterText(bearerField, 'server-secret');
    await tester.tap(find.text('Apply token'));
    await tester.pumpAndSettle();

    expect(container.read(authTokenProvider), 'server-secret');
  });
}
