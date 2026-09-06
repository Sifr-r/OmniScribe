import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/repositories/config_repository.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';

class _MockConfigRepository extends Mock implements ConfigRepository {}

void main() {
  late _MockConfigRepository repo;

  setUpAll(() {
    registerFallbackValue(ConfigUpdate.fromJson(<String, dynamic>{}));
  });

  setUp(() {
    repo = _MockConfigRepository();
  });

  ProviderContainer makeContainer() {
    return ProviderContainer(
      overrides: [
        configRepositoryProvider.overrideWithValue(repo),
      ],
    );
  }

  group('SettingsNotifier.build', () {
    test('returns SettingsState.initial() before any method call', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      final state = container.read(settingsStateProvider);
      expect(state.isLoading, isFalse);
      expect(state.runtimeConfig, isNull);
    });
  });

  group('SettingsNotifier.load', () {
    test('fetches config + models and updates state', () async {
      final config = RuntimeConfig.fromJson(<String, dynamic>{
        'api_base': 'http://example.test/v1',
        'api_key': '',
        'model': 'allenai/olmocr-2-7b',
        'ocr_provider': 'openai',
      });
      when(() => repo.getConfig()).thenAnswer((_) async => config);
      when(() => repo.getModelsForProvider('openai'))
          .thenAnswer((_) async => ['allenai/olmocr-2-7b', 'qwen2-vl']);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(settingsStateProvider.notifier);

      await notifier.load();

      final state = container.read(settingsStateProvider);
      expect(state.isLoading, isFalse);
      expect(state.runtimeConfig, config);
      expect(state.activeProviderId, 'openai');
      expect(state.ocrModels, ['allenai/olmocr-2-7b', 'qwen2-vl']);
      expect(state.error, isNull);
      // Phase A: translation/transcription routes are deferred — the
      // deprecated namespace API must not be called anymore.
      verifyNever(() => repo.getModels(namespace: any(named: 'namespace')));
    });

    test('load() routes ocr models through active provider', () async {
      final config = RuntimeConfig.fromJson(<String, dynamic>{
        'api_base': 'http://example.test/v1',
        'api_key': '',
        'model': 'gpt-4o',
        'ocr_provider': 'openai',
      });
      when(() => repo.getConfig()).thenAnswer((_) async => config);
      when(() => repo.getModelsForProvider('openai'))
          .thenAnswer((_) async => ['gpt-4o']);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(settingsStateProvider.notifier);

      await notifier.load();

      final state = container.read(settingsStateProvider);
      expect(state.activeProviderId, 'openai');
      expect(state.ocrModels, ['gpt-4o']);
      verify(() => repo.getModelsForProvider('openai')).called(1);
    });

    test('on failure populates error and clears isLoading', () async {
      when(() => repo.getConfig()).thenThrow(Exception('boom'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(settingsStateProvider.notifier);

      await notifier.load();

      final state = container.read(settingsStateProvider);
      expect(state.isLoading, isFalse);
      expect(state.runtimeConfig, isNull);
      expect(state.error, contains('boom'));
    });
  });

  group('SettingsNotifier.updateOcr', () {
    test('posts ConfigUpdate via repo and re-fetches config', () async {
      final initial = RuntimeConfig.fromJson(<String, dynamic>{
        'api_base': 'http://example.test/v1',
        'model': 'allenai/olmocr-2-7b',
        'ocr_provider': 'openai',
      });
      final updated = RuntimeConfig.fromJson(<String, dynamic>{
        'api_base': 'http://example.test/v1',
        'model': 'qwen2-vl',
        'ocr_provider': 'openai',
      });
      // First load() sees initial; the subsequent load() (after updateConfig)
      // sees the updated config.
      var configCallCount = 0;
      when(() => repo.getConfig()).thenAnswer((_) async {
        configCallCount += 1;
        return configCallCount == 1 ? initial : updated;
      });
      when(() => repo.updateConfig(any())).thenAnswer((_) async => updated);
      when(() => repo.getModelsForProvider('openai'))
          .thenAnswer((_) async => ['allenai/olmocr-2-7b']);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(settingsStateProvider.notifier);

      await notifier.load();
      await notifier.updateOcr(
        ProcessSettings.defaultSettings().copyWith(model: 'qwen2-vl'),
      );

      final captured = verify(() => repo.updateConfig(captureAny()))
          .captured
          .single as ConfigUpdate;
      expect(captured.model, 'qwen2-vl');

      final state = container.read(settingsStateProvider);
      expect(state.runtimeConfig?.model, 'qwen2-vl');
    });
  });

  group('SettingsNotifier.toggleDarkMode', () {
    test('flips isDarkMode without touching repo', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(settingsStateProvider.notifier);

      expect(container.read(settingsStateProvider).isDarkMode, isFalse);
      notifier.toggleDarkMode();
      expect(container.read(settingsStateProvider).isDarkMode, isTrue);
      notifier.toggleDarkMode();
      expect(container.read(settingsStateProvider).isDarkMode, isFalse);
      verifyNever(() => repo.getConfig());
    });
  });

  group('SettingsNotifier.setServerBaseUrl', () {
    test('updates serverBaseUrl state', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(settingsStateProvider.notifier);

      notifier.setServerBaseUrl('http://localhost:9000');
      expect(container.read(settingsStateProvider).serverBaseUrl,
          'http://localhost:9000');
    });
  });

  group('SettingsNotifier.setUseAsync', () {
    test('updates useAsync locally without round-tripping', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(settingsStateProvider.notifier);

      expect(container.read(settingsStateProvider).useAsync, isFalse);
      notifier.setUseAsync(true);
      expect(container.read(settingsStateProvider).useAsync, isTrue);
      verifyNever(() => repo.updateConfig(any()));
    });
  });
}
