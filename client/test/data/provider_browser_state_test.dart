import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/providers/provider_preset.dart';
import 'package:omniscribe_client/features/providers/provider_browser_state.dart';

void main() {
  const presetA = ProviderPreset(
    id: 'openai',
    name: 'OpenAI',
    category: 'popular',
    description: 'GPT models',
    recommendedBaseUrl: 'https://api.openai.com/v1',
    defaultModel: 'gpt-4o',
  );
  const presetB = ProviderPreset(
    id: 'ollama',
    name: 'Ollama',
    category: 'local',
    description: 'Local models',
    recommendedBaseUrl: 'http://localhost:11434/v1',
    defaultModel: 'llama3',
  );

  group('ProviderBrowserState.initial', () {
    test('returns sane defaults', () {
      const state = ProviderBrowserState.initial();
      expect(state.providers, isEmpty);
      expect(state.activeProvider, isNull);
      expect(state.validationStatus, isEmpty);
      expect(state.modelsMap, isEmpty);
      expect(state.loadingModelIds, isEmpty);
      expect(state.isFetching, isFalse);
      expect(state.isValidating, isFalse);
      expect(state.searchQuery, isEmpty);
      expect(state.error, isNull);
    });
  });

  group('ProviderBrowserState.copyWith', () {
    test('preserves untouched fields', () {
      const before = ProviderBrowserState.initial();
      final after = before.copyWith(isFetching: true);
      expect(after.isFetching, isTrue);
      expect(after.providers, before.providers);
      expect(after.error, before.error);
      expect(after.searchQuery, before.searchQuery);
    });

    test('clearError: null error is preserved when explicit null passed', () {
      const before = ProviderBrowserState.initial();
      final after = before.copyWith(clearError: true);
      expect(after.error, isNull);
    });

    test('clearActiveProvider forces activeProvider to null', () {
      final before = const ProviderBrowserState.initial()
          .copyWith(activeProvider: presetA);
      final after = before.copyWith(clearActiveProvider: true);
      expect(after.activeProvider, isNull);
    });
  });

  group('ProviderBrowserState.filteredProviders', () {
    test('returns all providers when search query is empty', () {
      final state = const ProviderBrowserState.initial()
          .copyWith(providers: [presetA, presetB]);
      expect(state.filteredProviders, hasLength(2));
    });

    test('matches against name, description, id, category, and models', () {
      final state = const ProviderBrowserState.initial()
          .copyWith(providers: [presetA, presetB], searchQuery: 'GPT');
      expect(state.filteredProviders.map((p) => p.id), ['openai']);
    });

    test('popularProviders / otherProviders split on category', () {
      final state = const ProviderBrowserState.initial()
          .copyWith(providers: [presetA, presetB]);
      expect(state.popularProviders.map((p) => p.id), ['openai']);
      expect(state.otherProviders.map((p) => p.id), ['ollama']);
    });
  });
}
