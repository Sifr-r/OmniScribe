/// Provider catalog, preset, and discovery models matching OmniScribe backend contracts.
library;

class ProviderPreset {
  const ProviderPreset({
    required this.id,
    required this.name,
    required this.category,
    required this.description,
    required this.recommendedBaseUrl,
    required this.defaultModel,
    this.apiBase,
    this.models = const [],
    this.requiresKey = true,
    this.envKeys = const [],
    this.docUrl,
    this.getApiKeyUrl,
    this.isRecommended,
    this.isCustom,
    this.iconId,
    this.notes = '',
  });

  final String id;
  final String name;
  final String category;
  final String description;
  final String recommendedBaseUrl;
  final String? apiBase;
  final String defaultModel;
  final List<String> models;
  final bool requiresKey;
  final List<String> envKeys;
  final String? docUrl;
  final String? getApiKeyUrl;
  final bool? isRecommended;
  final bool? isCustom;
  final String? iconId;
  final String notes;

  factory ProviderPreset.fromJson(Map<String, dynamic> json) {
    final modelList = <String>[];
    if (json['models'] is List) {
      for (final m in json['models'] as List) {
        if (m != null) modelList.add(m.toString());
      }
    }
    final envList = <String>[];
    if (json['env_keys'] is List) {
      for (final k in json['env_keys'] as List) {
        if (k != null) envList.add(k.toString());
      }
    }

    return ProviderPreset(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      category: json['category']?.toString() ?? 'other',
      description: json['description']?.toString() ?? '',
      recommendedBaseUrl: json['recommended_base_url']?.toString() ??
          json['api_base']?.toString() ??
          '',
      apiBase: json['api_base']?.toString(),
      defaultModel: json['default_model']?.toString() ?? '',
      models: modelList,
      requiresKey: json['requires_key'] as bool? ?? true,
      envKeys: envList,
      docUrl: json['doc_url']?.toString(),
      getApiKeyUrl: json['get_api_key_url']?.toString(),
      isRecommended: json['is_recommended'] as bool?,
      isCustom: json['is_custom'] as bool?,
      iconId: json['icon_id']?.toString(),
      notes: json['notes']?.toString() ?? '',
    );
  }

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'id': id,
      'name': name,
      'category': category,
      'description': description,
      'recommended_base_url': recommendedBaseUrl,
      if (apiBase != null) 'api_base': apiBase,
      'default_model': defaultModel,
      'models': models,
      'requires_key': requiresKey,
      'env_keys': envKeys,
      if (docUrl != null) 'doc_url': docUrl,
      if (getApiKeyUrl != null) 'get_api_key_url': getApiKeyUrl,
      if (isRecommended != null) 'is_recommended': isRecommended,
      if (isCustom != null) 'is_custom': isCustom,
      if (iconId != null) 'icon_id': iconId,
      'notes': notes,
    };
  }
}

/// Response returned by GET /api/providers/{id}/models
class ProviderModelsResponse {
  const ProviderModelsResponse({
    required this.models,
    this.error,
  });

  final List<String> models;
  final String? error;

  factory ProviderModelsResponse.fromJson(Map<String, dynamic> json) {
    final list = <String>[];
    if (json['models'] is List) {
      for (final item in json['models'] as List) {
        if (item != null) list.add(item.toString());
      }
    }
    return ProviderModelsResponse(
      models: list,
      error: json['error']?.toString(),
    );
  }

  Map<String, dynamic> toJson() => {
        'models': models,
        if (error != null) 'error': error,
      };
}

/// Request to POST /api/providers/active
class SetActiveProviderRequest {
  const SetActiveProviderRequest({
    required this.providerId,
    this.apiBase,
    this.apiKey,
    this.model,
  });

  final String providerId;
  final String? apiBase;
  final String? apiKey;
  final String? model;

  Map<String, dynamic> toJson() => {
        'provider_id': providerId,
        if (apiBase != null) 'api_base': apiBase,
        if (apiKey != null) 'api_key': apiKey,
        if (model != null) 'model': model,
      };
}

/// Response from POST /api/providers/active
class SetActiveProviderResponse {
  const SetActiveProviderResponse({
    required this.apiBase,
    required this.model,
  });

  final String apiBase;
  final String model;

  factory SetActiveProviderResponse.fromJson(Map<String, dynamic> json) {
    return SetActiveProviderResponse(
      apiBase: json['api_base']?.toString() ?? '',
      model: json['model']?.toString() ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
        'api_base': apiBase,
        'model': model,
      };
}

/// Request to POST /api/providers/validate
class ValidateProviderRequest {
  const ValidateProviderRequest({
    required this.providerId,
    required this.apiBase,
    this.apiKey,
    this.model,
  });

  final String providerId;
  final String apiBase;
  final String? apiKey;
  final String? model;

  Map<String, dynamic> toJson() => {
        'provider_id': providerId,
        'api_base': apiBase,
        if (apiKey != null) 'api_key': apiKey,
        if (model != null) 'model': model,
      };
}

/// Response from POST /api/providers/validate
class ValidateProviderResponse {
  const ValidateProviderResponse({
    required this.valid,
    this.modelCount = 0,
    this.models = const [],
    this.error,
  });

  final bool valid;
  final int modelCount;
  final List<String> models;
  final String? error;

  factory ValidateProviderResponse.fromJson(Map<String, dynamic> json) {
    final modelList = <String>[];
    if (json['models'] is List) {
      for (final m in json['models'] as List) {
        if (m != null) modelList.add(m.toString());
      }
    }
    return ValidateProviderResponse(
      valid: json['valid'] as bool? ?? false,
      modelCount: (json['model_count'] as num?)?.toInt() ?? modelList.length,
      models: modelList,
      error: json['error']?.toString(),
    );
  }

  Map<String, dynamic> toJson() => {
        'valid': valid,
        'model_count': modelCount,
        'models': models,
        if (error != null) 'error': error,
      };
}
