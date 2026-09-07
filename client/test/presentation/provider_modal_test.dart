import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/models/provider_preset.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/repositories/config_repository.dart';
import 'package:omniscribe_client/data/repositories/provider_repository.dart';
import 'package:omniscribe_client/presentation/providers/provider_modal.dart';

class _MockProviderRepository extends Mock implements ProviderRepository {}

class _MockConfigRepository extends Mock implements ConfigRepository {}

const _openAI = ProviderPreset(
  id: 'openai',
  name: 'OpenAI',
  category: 'cloud',
  description: 'OpenAI hosted models.',
  recommendedBaseUrl: 'https://api.openai.com/v1',
  defaultModel: 'gpt-4o',
);

const _lmStudio = ProviderPreset(
  id: 'lmstudio',
  name: 'LM Studio',
  category: 'local',
  description: 'Local OpenAI-compatible server.',
  recommendedBaseUrl: 'http://localhost:1234/v1',
  defaultModel: 'allenai/olmocr-2-7b',
);

Widget _wrap(
    Widget child, ProviderRepository repo, ConfigRepository configRepo) {
  return ProviderScope(
    overrides: [
      providerRepositoryProvider.overrideWithValue(repo),
      configRepositoryProvider.overrideWithValue(configRepo),
    ],
    child: MaterialApp(
      theme: ThemeData.dark().copyWith(extensions: [AppColorScheme.dark()]),
      home: Scaffold(body: SingleChildScrollView(child: child)),
    ),
  );
}

void main() {
  late _MockProviderRepository repo;
  late _MockConfigRepository configRepo;

  setUpAll(() {
    registerFallbackValue(
      const ValidateProviderRequest(
        providerId: '',
        apiBase: '',
      ),
    );
    registerFallbackValue(
      const SetActiveProviderRequest(providerId: ''),
    );
  });

  setUp(() {
    repo = _MockProviderRepository();
    configRepo = _MockConfigRepository();
    when(() => repo.getProviderModels(
          any(),
          apiBase: any(named: 'apiBase'),
          apiKey: any(named: 'apiKey'),
        )).thenAnswer(
      (_) async => const ProviderModelsResponse(models: []),
    );
    when(() => repo.setActiveProvider(any())).thenAnswer(
      (_) async => const SetActiveProviderResponse(
        apiBase: 'http://localhost:1234/v1',
        model: 'allenai/olmocr-2-7b',
      ),
    );
    when(() => configRepo.getConfig()).thenAnswer(
      (_) async => RuntimeConfig.fromJson(<String, dynamic>{
        'api_base': 'http://localhost:1234/v1',
        'model': 'allenai/olmocr-2-7b',
      }),
    );
    when(() => configRepo.getModelsForProvider(any(),
            apiBase: any(named: 'apiBase')))
        .thenAnswer((_) async => []);
  });

  testWidgets('opening the modal loads the provider catalog', (tester) async {
    when(() => repo.getProviders())
        .thenAnswer((_) async => [_openAI, _lmStudio]);

    await tester.pumpWidget(_wrap(const ProviderModal(), repo, configRepo));
    await tester.pumpAndSettle();

    verify(() => repo.getProviders()).called(1);
    expect(find.text('OpenAI'), findsOneWidget);
    expect(find.text('LM Studio'), findsOneWidget);
    expect(find.textContaining('No providers found'), findsNothing);
  });

  testWidgets('a catalog fetch failure is reported, not shown as no matches',
      (tester) async {
    when(() => repo.getProviders()).thenThrow(Exception('401 unauthorized'));

    await tester.pumpWidget(_wrap(const ProviderModal(), repo, configRepo));
    await tester.pumpAndSettle();

    expect(find.textContaining('No providers found'), findsNothing);
    expect(find.textContaining('401 unauthorized'), findsOneWidget);
    expect(find.text('Retry'), findsOneWidget);
  });

  testWidgets('Retry re-attempts the catalog fetch', (tester) async {
    when(() => repo.getProviders()).thenThrow(Exception('boom'));
    await tester.pumpWidget(_wrap(const ProviderModal(), repo, configRepo));
    await tester.pumpAndSettle();

    reset(repo);
    when(() => repo.getProviders()).thenAnswer((_) async => [_openAI]);
    when(() => repo.getProviderModels(
          any(),
          apiBase: any(named: 'apiBase'),
          apiKey: any(named: 'apiKey'),
        )).thenAnswer(
      (_) async => const ProviderModelsResponse(models: []),
    );

    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();

    verify(() => repo.getProviders()).called(1);
    expect(find.text('OpenAI'), findsOneWidget);
  });

  testWidgets(
      'connection test success immediately displays discovered models and updates model controller',
      (tester) async {
    when(() => repo.getProviders()).thenAnswer((_) async => [_lmStudio]);
    when(() => repo.validateProvider(any())).thenAnswer(
      (_) async => const ValidateProviderResponse(
        valid: true,
        modelCount: 2,
        models: ['mistral-7b', 'phi-3'],
      ),
    );

    await tester.pumpWidget(_wrap(
      const ProviderModal(initialProvider: _lmStudio),
      repo,
      configRepo,
    ));
    await tester.pumpAndSettle();

    // Initially shows LM Studio config form with defaultModel 'allenai/olmocr-2-7b'
    expect(find.text('Configuring LM Studio'), findsOneWidget);
    expect(find.text('allenai/olmocr-2-7b'), findsOneWidget);
    expect(find.textContaining('Select model'), findsNothing);

    // Tap 'Test connection'
    await tester.tap(find.text('Test connection'));
    await tester.pumpAndSettle();

    // Verification: models discovered and reflected in UI
    expect(
      find.text('Endpoint reachable! Found 2 models.'),
      findsOneWidget,
    );
    expect(find.text('Select model (2)'), findsOneWidget);
    // Since 'allenai/olmocr-2-7b' was not in discovered models, auto-selects first discovered model:
    expect(find.text('mistral-7b'), findsOneWidget);

    // Selecting another model from the popup updates the model controller
    await tester.tap(find.text('Select model (2)'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('phi-3').last);
    await tester.pumpAndSettle();
    expect(find.text('phi-3'), findsOneWidget);
  });

  testWidgets('ProviderCard refresh button triggers fetchModelsForProvider',
      (tester) async {
    when(() => repo.getProviders()).thenAnswer((_) async => [_openAI]);

    await tester.pumpWidget(_wrap(const ProviderModal(), repo, configRepo));
    await tester.pumpAndSettle();

    expect(find.byTooltip('Refresh models'), findsOneWidget);
    await tester.tap(find.byTooltip('Refresh models'));
    await tester.pumpAndSettle();

    verify(() => repo.getProviderModels(
          'openai',
          apiBase: any(named: 'apiBase'),
          apiKey: any(named: 'apiKey'),
        )).called(greaterThanOrEqualTo(1));
  });

  testWidgets('Apply as active passes model if non-empty and reloads settings',
      (tester) async {
    when(() => repo.getProviders()).thenAnswer((_) async => [_lmStudio]);

    await tester.pumpWidget(_wrap(
      const ProviderModal(initialProvider: _lmStudio),
      repo,
      configRepo,
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Apply as active'));
    await tester.pumpAndSettle();

    final captured = verify(() => repo.setActiveProvider(captureAny()))
        .captured
        .single as SetActiveProviderRequest;
    expect(captured.providerId, 'lmstudio');
    expect(captured.model, 'allenai/olmocr-2-7b');
    verify(() => configRepo.getConfig()).called(greaterThanOrEqualTo(1));
  });

  testWidgets('Apply as active passes null model if model field is empty',
      (tester) async {
    when(() => repo.getProviders()).thenAnswer((_) async => [_lmStudio]);

    await tester.pumpWidget(_wrap(
      const ProviderModal(initialProvider: _lmStudio),
      repo,
      configRepo,
    ));
    await tester.pumpAndSettle();

    // Clear model input
    await tester.enterText(find.byType(EditableText).last, '');
    await tester.pumpAndSettle();

    await tester.tap(find.text('Apply as active'));
    await tester.pumpAndSettle();

    final captured = verify(() => repo.setActiveProvider(captureAny()))
        .captured
        .single as SetActiveProviderRequest;
    expect(captured.providerId, 'lmstudio');
    expect(captured.model, isNull);
    verify(() => configRepo.getConfig()).called(greaterThanOrEqualTo(1));
  });
}
