import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/data/models/provider_preset.dart';
import 'package:omniscribe_client/data/providers/provider_notifier.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/repositories/config_repository.dart';
import 'package:omniscribe_client/data/repositories/provider_repository.dart';

class _MockProviderRepository extends Mock implements ProviderRepository {}

class _MockConfigRepository extends Mock implements ConfigRepository {}

void main() {
  late _MockProviderRepository repo;
  late _MockConfigRepository configRepo;

  setUpAll(() {
    // mocktail needs a registered fallback for every custom type that
    // appears as an `any()` argument (it can't synthesize complex types
    // under sound null safety). Task 4's validateProvider +
    // setActiveProvider tests reach into the repo with any() against
    // these request types.
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
    // Default stubs for the Settings notifier's load() path. Task 4's
    // setActiveProvider test reaches into settingsStateProvider.load()
    // which calls configRepo.getConfig() + getModels(...). The Settings
    // notifier swallows those errors into state.error, so a throw is fine
    // here — we only need the call to NOT crash the test container.
    when(() => configRepo.getConfig())
        .thenThrow(StateError('test stub: configRepo not configured'));
    when(() => configRepo.getModels(namespace: any(named: 'namespace')))
        .thenThrow(StateError('test stub: configRepo not configured'));
  });

  ProviderContainer makeContainer() {
    return ProviderContainer(
      overrides: [
        providerRepositoryProvider.overrideWithValue(repo),
        configRepositoryProvider.overrideWithValue(configRepo),
      ],
    );
  }

  group('ProviderBrowserNotifier.build', () {
    test('returns ProviderBrowserState.initial() before any method call', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      final state = container.read(providerBrowserProvider);
      expect(state.providers, isEmpty);
      expect(state.isFetching, isFalse);
      expect(state.error, isNull);
    });
  });

  group('ProviderBrowserNotifier.fetchProviders', () {
    test('populates providers list', () async {
      const presetA = ProviderPreset(
        id: 'openai',
        name: 'OpenAI',
        category: 'popular',
        description: 'GPT models',
        recommendedBaseUrl: 'https://api.openai.com/v1',
        defaultModel: 'gpt-4o',
      );
      const presetB = ProviderPreset(
        id: 'template-provider',
        name: 'Template',
        category: 'popular',
        description: 'Uses placeholder',
        recommendedBaseUrl:
            'http://{host}/v1', // contains '{' -> skip auto-fetch
        defaultModel: 'm',
      );
      when(() => repo.getProviders())
          .thenAnswer((_) async => [presetA, presetB]);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      await notifier.fetchProviders();

      final state = container.read(providerBrowserProvider);
      expect(state.providers, [presetA, presetB]);
      expect(state.isFetching, isFalse);
      expect(state.error, isNull);
    });

    test('on failure populates error and clears isFetching', () async {
      when(() => repo.getProviders()).thenThrow(Exception('boom'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      await notifier.fetchProviders();

      final state = container.read(providerBrowserProvider);
      expect(state.isFetching, isFalse);
      expect(state.providers, isEmpty);
      expect(state.error, contains('boom'));
    });
  });

  group('ProviderBrowserNotifier.fetchModelsForProvider', () {
    test(
        'populates modelsMap and removes the id from loadingModelIds on success',
        () async {
      when(() => repo.getProviderModels(
            any(),
            apiBase: any(named: 'apiBase'),
            apiKey: any(named: 'apiKey'),
          )).thenAnswer(
        (_) async => const ProviderModelsResponse(models: ['m1', 'm2']),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      await notifier.fetchModelsForProvider(
        'openai',
        apiBase: 'https://api.openai.com/v1',
        apiKey: 'key-123',
      );

      final state = container.read(providerBrowserProvider);
      expect(state.modelsMap['openai'], ['m1', 'm2']);
      expect(state.loadingModelIds.contains('openai'), isFalse);
      verify(() => repo.getProviderModels(
            'openai',
            apiBase: 'https://api.openai.com/v1',
            apiKey: 'key-123',
          )).called(1);
    });

    test(
        'is a no-op when the id is already in loadingModelIds (deduplicates concurrent calls)',
        () async {
      var callCount = 0;
      when(() => repo.getProviderModels(
            any(),
            apiBase: any(named: 'apiBase'),
            apiKey: any(named: 'apiKey'),
          )).thenAnswer((_) async {
        callCount += 1;
        // Hold the future open so we can observe the in-flight flag.
        await Future<void>.delayed(const Duration(milliseconds: 20));
        return const ProviderModelsResponse(models: ['m']);
      });

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      // Kick off two concurrent calls; the second should be deduped.
      final f1 = notifier.fetchModelsForProvider('openai');
      final f2 = notifier.fetchModelsForProvider('openai');
      await Future.wait([f1, f2]);

      expect(callCount, 1);
      expect(
          container
              .read(providerBrowserProvider)
              .loadingModelIds
              .contains('openai'),
          isFalse);
    });

    test('removes the id from loadingModelIds even when the repo throws',
        () async {
      when(() => repo.getProviderModels(
            any(),
            apiBase: any(named: 'apiBase'),
            apiKey: any(named: 'apiKey'),
          )).thenThrow(Exception('boom'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      await notifier.fetchModelsForProvider('openai');

      final state = container.read(providerBrowserProvider);
      expect(state.loadingModelIds.contains('openai'), isFalse);
      expect(state.modelsMap['openai'], isNull);
    });
  });

  group('ValidateProviderResponse', () {
    test('parses models from JSON and falls back to empty list', () {
      final resWithModels = ValidateProviderResponse.fromJson({
        'valid': true,
        'model_count': 2,
        'models': ['gpt-4o', 'gpt-4o-mini'],
      });
      expect(resWithModels.valid, isTrue);
      expect(resWithModels.modelCount, 2);
      expect(resWithModels.models, ['gpt-4o', 'gpt-4o-mini']);

      final resEmpty = ValidateProviderResponse.fromJson({
        'valid': false,
        'error': 'connection failed',
      });
      expect(resEmpty.valid, isFalse);
      expect(resEmpty.models, isEmpty);
      expect(resEmpty.modelCount, 0);
      expect(resEmpty.error, 'connection failed');
    });

    test('serializes models to JSON', () {
      const res = ValidateProviderResponse(
        valid: true,
        modelCount: 1,
        models: ['llama-3.3-70b'],
      );
      final json = res.toJson();
      expect(json['valid'], isTrue);
      expect(json['model_count'], 1);
      expect(json['models'], ['llama-3.3-70b']);
    });
  });

  group('ProviderBrowserNotifier.validateProvider', () {
    test(
        'on success immediately populates modelsMap with res.models and triggers credentials sync',
        () async {
      when(() => repo.validateProvider(any())).thenAnswer(
        (_) async => const ValidateProviderResponse(
          valid: true,
          modelCount: 2,
          models: ['model-alpha', 'model-beta'],
        ),
      );
      when(() => repo.getProviderModels(
            any(),
            apiBase: any(named: 'apiBase'),
            apiKey: any(named: 'apiKey'),
          )).thenAnswer(
        (_) async =>
            const ProviderModelsResponse(models: ['model-alpha', 'model-beta']),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      final res = await notifier.validateProvider(
        'lmstudio',
        'http://localhost:1234/v1',
        'test-key',
      );

      expect(res.valid, isTrue);
      expect(res.models, ['model-alpha', 'model-beta']);
      final state = container.read(providerBrowserProvider);
      expect(state.validationStatus['lmstudio'], contains('2 models'));
      // modelsMap must be populated immediately without waiting for fetchModelsForProvider:
      expect(state.modelsMap['lmstudio'], ['model-alpha', 'model-beta']);
      expect(state.isValidating, isFalse);

      verify(() => repo.getProviderModels(
            'lmstudio',
            apiBase: 'http://localhost:1234/v1',
            apiKey: 'test-key',
          )).called(1);
    });

    test('on success populates validationStatus and triggers model refetch',
        () async {
      when(() => repo.validateProvider(any())).thenAnswer(
        (_) async => const ValidateProviderResponse(valid: true, modelCount: 3),
      );
      when(() => repo.getProviderModels(
            any(),
            apiBase: any(named: 'apiBase'),
            apiKey: any(named: 'apiKey'),
          )).thenAnswer(
        (_) async => const ProviderModelsResponse(models: ['m1']),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      final res = await notifier.validateProvider(
        'openai',
        'https://api.openai.com/v1',
        null,
      );

      expect(res.valid, isTrue);
      final state = container.read(providerBrowserProvider);
      expect(state.validationStatus['openai'], contains('3'));
      expect(state.isValidating, isFalse);
    });

    test(
        'on failure populates validationStatus with error and clears isValidating',
        () async {
      when(() => repo.validateProvider(any())).thenAnswer(
        (_) async =>
            const ValidateProviderResponse(valid: false, error: 'bad creds'),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      final res = await notifier.validateProvider(
        'openai',
        'https://api.openai.com/v1',
        'k',
      );

      expect(res.valid, isFalse);
      final state = container.read(providerBrowserProvider);
      expect(state.validationStatus['openai'], 'bad creds');
      expect(state.isValidating, isFalse);
    });
  });

  group('ProviderBrowserNotifier.setActiveProvider', () {
    test(
        'on success calls repo.setActiveProvider, mirrors activeProviderId into settingsStateProvider, and re-fetches settings',
        () async {
      when(() => repo.setActiveProvider(any())).thenAnswer(
        (_) async => const SetActiveProviderResponse(
          apiBase: 'https://api.openai.com/v1',
          model: 'gpt-4o',
        ),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      await notifier.setActiveProvider(
        'openai',
        'https://api.openai.com/v1',
        'k',
        'gpt-4o',
      );

      final state = container.read(providerBrowserProvider);
      expect(state.isFetching, isFalse);
      expect(state.error, isNull);
      verify(() => repo.setActiveProvider(any())).called(1);

      // Cross-notifier coordination: settingsStateProvider.activeProviderId
      // should now be 'openai' (mirrored by SettingsNotifier.setActiveProvider).
      expect(container.read(settingsStateProvider).activeProviderId, 'openai');
      // settings.load() was awaited, so configRepo.getConfig was hit.
      verify(() => configRepo.getConfig()).called(1);
    });

    test('on failure populates error and rethrows without touching settings',
        () async {
      when(() => repo.setActiveProvider(any())).thenThrow(Exception('reject'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      await expectLater(
        () => notifier.setActiveProvider('openai', null, null, null),
        throwsA(isA<Exception>()),
      );

      final state = container.read(providerBrowserProvider);
      expect(state.isFetching, isFalse);
      expect(state.error, contains('reject'));
      // settings.load() must NOT have been called when the repo throws
      // (otherwise a downstream error would mask the original failure).
      verifyNever(() => configRepo.getConfig());
    });
  });

  group('ProviderBrowserNotifier.setSearchQuery', () {
    test('mirrors the query string into state', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      notifier.setSearchQuery('open');
      expect(container.read(providerBrowserProvider).searchQuery, 'open');
    });
  });

  group('ProviderBrowserNotifier.selectActiveProvider', () {
    test('mirrors the provider into state', () async {
      const preset = ProviderPreset(
        id: 'openai',
        name: 'OpenAI',
        category: 'popular',
        description: '',
        recommendedBaseUrl: '',
        defaultModel: '',
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(providerBrowserProvider.notifier);

      notifier.selectActiveProvider(preset);
      expect(container.read(providerBrowserProvider).activeProvider, preset);

      notifier.selectActiveProvider(null);
      expect(container.read(providerBrowserProvider).activeProvider, isNull);
    });
  });
}
