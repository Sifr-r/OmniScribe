import 'package:flutter/foundation.dart';
import 'package:omniscribe_client/features/providers/provider_preset.dart';

@immutable
class ProviderBrowserState {
  const ProviderBrowserState({
    this.providers = const <ProviderPreset>[],
    this.activeProvider,
    this.validationStatus = const <String, String>{},
    this.modelsMap = const <String, List<String>>{},
    this.loadingModelIds = const <String>{},
    this.isFetching = false,
    this.isValidating = false,
    this.searchQuery = '',
    this.error,
  });

  /// Initial empty state — no providers fetched, no errors.
  const ProviderBrowserState.initial()
      : providers = const <ProviderPreset>[],
        activeProvider = null,
        validationStatus = const <String, String>{},
        modelsMap = const <String, List<String>>{},
        loadingModelIds = const <String>{},
        isFetching = false,
        isValidating = false,
        searchQuery = '',
        error = null;

  final List<ProviderPreset> providers;
  final ProviderPreset? activeProvider;
  final Map<String, String> validationStatus;
  final Map<String, List<String>> modelsMap;
  final Set<String> loadingModelIds;
  final bool isFetching;
  final bool isValidating;
  final String searchQuery;
  final String? error;

  /// Providers filtered by `searchQuery` (case-insensitive across name,
  /// description, id, category, and embedded model ids).
  List<ProviderPreset> get filteredProviders {
    if (searchQuery.trim().isEmpty) return providers;
    final q = searchQuery.toLowerCase().trim();
    return providers.where((p) {
      return p.name.toLowerCase().contains(q) ||
          p.description.toLowerCase().contains(q) ||
          p.id.toLowerCase().contains(q) ||
          p.category.toLowerCase().contains(q) ||
          p.models.any((m) => m.toLowerCase().contains(q));
    }).toList();
  }

  List<ProviderPreset> get popularProviders =>
      filteredProviders.where((p) => p.category == 'popular').toList();

  List<ProviderPreset> get otherProviders =>
      filteredProviders.where((p) => p.category != 'popular').toList();

  ProviderBrowserState copyWith({
    List<ProviderPreset>? providers,
    ProviderPreset? activeProvider,
    bool clearActiveProvider = false,
    Map<String, String>? validationStatus,
    Map<String, List<String>>? modelsMap,
    Set<String>? loadingModelIds,
    bool? isFetching,
    bool? isValidating,
    String? searchQuery,
    String? error,
    bool clearError = false,
  }) {
    return ProviderBrowserState(
      providers: providers ?? this.providers,
      activeProvider:
          clearActiveProvider ? null : (activeProvider ?? this.activeProvider),
      validationStatus: validationStatus ?? this.validationStatus,
      modelsMap: modelsMap ?? this.modelsMap,
      loadingModelIds: loadingModelIds ?? this.loadingModelIds,
      isFetching: isFetching ?? this.isFetching,
      isValidating: isValidating ?? this.isValidating,
      searchQuery: searchQuery ?? this.searchQuery,
      error: clearError ? null : (error ?? this.error),
    );
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is ProviderBrowserState &&
        listEquals(other.providers, providers) &&
        other.activeProvider == activeProvider &&
        mapEquals(other.validationStatus, validationStatus) &&
        mapEquals(other.modelsMap, modelsMap) &&
        setEquals(other.loadingModelIds, loadingModelIds) &&
        other.isFetching == isFetching &&
        other.isValidating == isValidating &&
        other.searchQuery == searchQuery &&
        other.error == error;
  }

  @override
  int get hashCode => Object.hash(
        Object.hashAll(providers),
        activeProvider,
        Object.hashAll(
            validationStatus.entries.map((e) => '${e.key}=${e.value}')),
        Object.hashAll(
            modelsMap.entries.map((e) => '${e.key}=${e.value.join(",")}')),
        Object.hashAll(loadingModelIds),
        isFetching,
        isValidating,
        searchQuery,
        error,
      );
}
