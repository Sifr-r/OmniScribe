import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/data/models/provider_preset.dart';
import 'package:omniscribe_client/data/providers/provider_browser_state.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/repositories/provider_repository.dart';

/// Riverpod entry-point for the Provider Browser feature.
///
/// Wires `data/repositories/provider_repository.dart` to a
/// `ProviderBrowserState` and exposes the same verb set the legacy
/// `state/provider_browser_provider.dart` did:
/// `fetchProviders`, `fetchModelsForProvider`, `validateProvider`,
/// `setActiveProvider`, `setSearchQuery`, `selectActiveProvider`.
final providerBrowserProvider =
    NotifierProvider<ProviderBrowserNotifier, ProviderBrowserState>(
  ProviderBrowserNotifier.new,
);

class ProviderBrowserNotifier extends Notifier<ProviderBrowserState> {
  late final ProviderRepository _repo;

  @override
  ProviderBrowserState build() {
    _repo = ref.watch(providerRepositoryProvider);
    return const ProviderBrowserState.initial();
  }

  Future<void> fetchProviders() async {
    state = state.copyWith(isFetching: true, clearError: true);
    try {
      final providers = await _repo.getProviders();
      state = state.copyWith(providers: providers, isFetching: false);
      // Auto-fetch models for providers whose `recommendedBaseUrl` is
      // a concrete URL (no template placeholders like `{host}`).
      for (final provider in providers) {
        if (provider.recommendedBaseUrl.isNotEmpty &&
            !provider.recommendedBaseUrl.contains('<') &&
            !provider.recommendedBaseUrl.contains('{')) {
          // Intentionally not awaited — fire-and-forget; per-provider
          // state updates land via `fetchModelsForProvider`. The legacy
          // behaviour was the same.
          // ignore: unawaited_futures
          fetchModelsForProvider(provider.id);
        }
      }
    } catch (e) {
      state = state.copyWith(isFetching: false, error: e.toString());
    }
  }

  Future<void> fetchModelsForProvider(
    String id, {
    String? apiBase,
    String? apiKey,
  }) async {
    if (state.loadingModelIds.contains(id)) return;

    state = state.copyWith(
      loadingModelIds: {...state.loadingModelIds, id},
    );

    try {
      final response = await _repo.getProviderModels(
        id,
        apiBase: apiBase,
        apiKey: apiKey,
      );
      final next = Map<String, List<String>>.from(state.modelsMap);
      if (response.models.isNotEmpty) {
        next[id] = response.models;
      }
      state = state.copyWith(
        modelsMap: next,
        loadingModelIds: state.loadingModelIds.difference({id}),
      );
    } catch (_) {
      state = state.copyWith(
        loadingModelIds: state.loadingModelIds.difference({id}),
      );
    }
  }

  Future<ValidateProviderResponse> validateProvider(
    String id,
    String base,
    String? key, {
    String? model,
  }) async {
    state = state.copyWith(isValidating: true);
    try {
      final res = await _repo.validateProvider(
        ValidateProviderRequest(
          providerId: id,
          apiBase: base,
          apiKey: key,
          model: model,
        ),
      );
      final newStatus = Map<String, String>.from(state.validationStatus);
      newStatus[id] = res.valid
          ? 'Connected successfully (${res.modelCount} models)'
          : (res.error ?? 'Validation failed');

      var nextModelsMap = state.modelsMap;
      if (res.valid && res.models.isNotEmpty) {
        nextModelsMap = Map<String, List<String>>.from(state.modelsMap);
        nextModelsMap[id] = res.models;
      }

      state = state.copyWith(
        validationStatus: newStatus,
        modelsMap: nextModelsMap,
        isValidating: false,
      );
      if (res.valid) {
        // ignore: unawaited_futures
        fetchModelsForProvider(id, apiBase: base, apiKey: key);
      }
      return res;
    } catch (e) {
      final newStatus = Map<String, String>.from(state.validationStatus);
      newStatus[id] = e.toString();
      state = state.copyWith(validationStatus: newStatus, isValidating: false);
      return ValidateProviderResponse(valid: false, error: e.toString());
    }
  }

  Future<void> setActiveProvider(
    String id,
    String? apiBase,
    String? apiKey,
    String? model,
  ) async {
    state = state.copyWith(isFetching: true, clearError: true);
    try {
      await _repo.setActiveProvider(
        SetActiveProviderRequest(
          providerId: id,
          apiBase: apiBase,
          apiKey: apiKey,
          model: model,
        ),
      );
      // Cross-notifier coordination: mirror the active provider id into
      // the Settings slice so the Settings tab badge / dropdown follow.
      ref.read(settingsStateProvider.notifier).setActiveProvider(id);
      await ref.read(settingsStateProvider.notifier).load();
      state = state.copyWith(isFetching: false);
    } catch (e) {
      state = state.copyWith(isFetching: false, error: e.toString());
      rethrow;
    }
  }

  void setSearchQuery(String query) {
    state = state.copyWith(searchQuery: query);
  }

  void selectActiveProvider(ProviderPreset? provider) {
    state = state.copyWith(
      activeProvider: provider,
      clearActiveProvider: provider == null,
    );
  }
}
