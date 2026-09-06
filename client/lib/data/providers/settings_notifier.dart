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
      final ocrModels = await _repo.getModelsForProvider(activeProviderId);

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

  Future<void> updateOcr(ProcessSettings next) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      await _repo.updateConfig(
        ConfigUpdate(
          apiBase: next.apiBase,
          apiKey: next.apiKey.isNotEmpty ? next.apiKey : null,
          model: next.model,
          pipelineMode: next.pipelineMode,
          denseMode: next.denseMode,
          denseThreshold: next.denseThreshold,
          dpi: next.dpi,
          concurrency: next.concurrency,
          refine: next.refine,
          maxImageDim: next.maxImageDim,
          selfCorrection: next.selfCorrection,
          binarize: next.binarize,
          dualEngine: next.dualEngine,
          spellcheck: next.spellcheck,
          crossPage: next.crossPage,
          preprocessPages: next.preprocessPages,
          orientationDetection: next.orientationDetection,
          deskew: next.deskew,
          denoise: next.denoise,
          normalizeContrast: next.normalizeContrast,
          cropCleanup: next.cropCleanup,
          qualityRouting: next.qualityRouting,
          documentProcessors: next.documentProcessors,
        ),
      );
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
