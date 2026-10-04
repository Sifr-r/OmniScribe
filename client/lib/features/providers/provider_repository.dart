import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/features/providers/provider_preset.dart';
import 'package:omniscribe_client/shared/providers/api_providers.dart';

abstract class ProviderRepository {
  /// Fetch list of all configured and discovered LLM providers.
  Future<List<ProviderPreset>> getProviders();

  /// Fetch specific provider preset configuration details.
  Future<ProviderPreset> getProviderDetails(String providerId);

  /// Fetch live available models for a given provider.
  Future<ProviderModelsResponse> getProviderModels(
    String providerId, {
    String? apiBase,
    String? apiKey,
  });

  /// Set the server active LLM provider.
  Future<SetActiveProviderResponse> setActiveProvider(
    SetActiveProviderRequest request,
  );

  /// Validate provider credentials and endpoint connectivity.
  Future<ValidateProviderResponse> validateProvider(
    ValidateProviderRequest request,
  );
}

class ProviderRepositoryImpl implements ProviderRepository {
  const ProviderRepositoryImpl(this._apiClient);

  final ApiClient _apiClient;

  @override
  Future<List<ProviderPreset>> getProviders() async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.providers,
    );
    final list = <ProviderPreset>[];
    if (json['providers'] is List) {
      for (final item in json['providers'] as List) {
        if (item is Map<String, dynamic>) {
          list.add(ProviderPreset.fromJson(item));
        }
      }
    }
    return list;
  }

  @override
  Future<ProviderPreset> getProviderDetails(String providerId) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.providerDetails(providerId),
    );
    return ProviderPreset.fromJson(json);
  }

  @override
  Future<ProviderModelsResponse> getProviderModels(
    String providerId, {
    String? apiBase,
    String? apiKey,
  }) async {
    final queryParams = <String, dynamic>{};
    if (apiBase != null && apiBase.isNotEmpty) {
      queryParams['api_base'] = apiBase;
    }

    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.providerModels(providerId),
      queryParameters: queryParams.isNotEmpty ? queryParams : null,
      headers: apiKey == null || apiKey.trim().isEmpty
          ? null
          : {'X-Provider-Api-Key': apiKey.trim()},
    );
    return ProviderModelsResponse.fromJson(json);
  }

  @override
  Future<SetActiveProviderResponse> setActiveProvider(
    SetActiveProviderRequest request,
  ) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.setActiveProvider,
      data: request.toJson(),
    );
    return SetActiveProviderResponse.fromJson(json);
  }

  @override
  Future<ValidateProviderResponse> validateProvider(
    ValidateProviderRequest request,
  ) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.validateProvider,
      data: request.toJson(),
    );
    return ValidateProviderResponse.fromJson(json);
  }
}

final providerRepositoryProvider = Provider<ProviderRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  return ProviderRepositoryImpl(apiClient);
});
