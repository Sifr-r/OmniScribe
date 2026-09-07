import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/settings_state.dart';
import 'package:omniscribe_client/data/repositories/config_repository.dart';

final settingsStateProvider = NotifierProvider<SettingsNotifier, SettingsState>(
  SettingsNotifier.new,
);

class SettingsNotifier extends Notifier<SettingsState> {
  late final ConfigRepository _repo;

  @override
  SettingsState build() {
    _repo = ref.watch(configRepositoryProvider);
    return const SettingsState.initial();
  }

  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final config = await _repo.getConfig();
      // Resolve the active provider from the freshly-fetched config BEFORE
      // the model call so the first load() doesn't use the previous
      // (initial-default) activeProviderId.
      final activeProviderId = config.ocrProvider ?? state.activeProviderId;
      // api_base, not the provider id, decides which endpoint is probed:
      // /api/config never reports a provider, so activeProviderId can be a
      // stale client-side default.
      final ocrModels = await _repo.getModelsForProvider(activeProviderId,
          apiBase: config.apiBase);

      state = state.copyWith(
        isLoading: false,
        runtimeConfig: config,
        activeProviderId: activeProviderId,
        ocrModels: ocrModels,
      );
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }

  Future<void> updateOcr(ConfigUpdate updates) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      await _repo.updateConfig(updates);
      await load();
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
      rethrow;
    }
  }

  void setServerBaseUrl(String url) {
    state = state.copyWith(serverBaseUrl: url);
    ref.read(apiBaseUrlProvider.notifier).set(url);
    // Trigger a config refresh against the new URL.
    load();
  }

  /// Applies the bearer the backend expects once `OMNISCRIBE_AUTH_TOKEN` is
  /// armed. Session-only, like the base URL; an empty [token] unsets it, for
  /// switching back to a tokenless loopback server.
  Future<void> setServerBearerToken(String? token) async {
    final trimmed = (token ?? '').trim();
    final hasToken = trimmed.isNotEmpty;
    state = state.copyWith(
      serverBearerToken: hasToken ? trimmed : null,
      clearServerBearerToken: !hasToken,
    );
    ref.read(authTokenProvider.notifier).set(hasToken ? trimmed : null);
    // authRequiredProvider never clears itself on a later success, so applying
    // a token has to dismiss it; a still-wrong one re-arms it via load().
    ref.read(authRequiredProvider.notifier).set(false);
    await load();
  }

  void setActiveProvider(String id) {
    state = state.copyWith(activeProviderId: id);
  }

  void setUseAsync(bool useAsync) {
    // Optimistic local-only update; the server is updated the next time
    // updateOcr/updateTranslation/etc. are called.
    state = state.copyWith(useAsync: useAsync);
  }

  void toggleDarkMode([bool? forceValue]) {
    state = state.copyWith(isDarkMode: forceValue ?? !state.isDarkMode);
  }
}
