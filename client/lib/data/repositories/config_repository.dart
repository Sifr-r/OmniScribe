import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';

abstract class ConfigRepository {
  /// Fetch the active server runtime configuration.
  Future<RuntimeConfig> getConfig();

  /// Update server runtime configuration options.
  Future<RuntimeConfig> updateConfig(ConfigUpdate updates);

  /// Fetch the supported models exposed by a specific provider.
  ///
  /// Hits `/api/providers/{providerId}/models` and parses the `models` array.
  /// When [apiBase] is supplied the server probes that endpoint instead of the
  /// provider template's default URL. Returns an empty list when the response
  /// shape is unexpected (matches the resilient Svelte fallback).
  Future<List<String>> getModelsForProvider(String providerId,
      {String? apiBase});

  /// Fetch list of supported models under the given namespace.
  ///
  /// Kept for back-compat with existing call sites; delegates to
  /// [getModelsForProvider] for `general` / `ocr` and returns an empty list
  /// for namespaces whose routes are deferred per the harness rebuild spec.
  Future<List<String>> getModels({String namespace = 'general'});
}

class ConfigRepositoryImpl implements ConfigRepository {
  const ConfigRepositoryImpl(this._apiClient);

  final ApiClient _apiClient;

  @override
  Future<RuntimeConfig> getConfig() async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.config,
    );
    return RuntimeConfig.fromJson(json);
  }

  @override
  Future<RuntimeConfig> updateConfig(ConfigUpdate updates) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.config,
      data: updates.toJson(),
    );
    return RuntimeConfig.fromJson(json);
  }

  @override
  Future<List<String>> getModelsForProvider(String providerId,
      {String? apiBase}) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      '/api/providers/$providerId/models',
      queryParameters: apiBase == null || apiBase.isEmpty
          ? null
          : <String, dynamic>{'api_base': apiBase},
    );
    final list = <String>[];
    if (json['models'] is List) {
      for (final item in json['models'] as List) {
        if (item != null) list.add(item.toString());
      }
    }
    return list;
  }

  @override
  Future<List<String>> getModels({String namespace = 'general'}) async {
    switch (namespace) {
      case 'translation':
      case 'transcription':
        // Deferred per harness rebuild spec.
        return const <String>[];
      case 'general':
      case 'ocr':
      default:
        // Phase A: hardcode 'lmstudio' as the default OCR/general provider
        // until the SettingsNotifier-driven override is wired.
        return getModelsForProvider('lmstudio');
    }
  }
}
