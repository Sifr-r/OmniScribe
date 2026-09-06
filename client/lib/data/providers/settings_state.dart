import 'package:flutter/foundation.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';

@immutable
class SettingsState {
  const SettingsState({
    required this.isLoading,
    required this.runtimeConfig,
    required this.activeProviderId,
    required this.ocrModels,
    required this.serverBaseUrl,
    required this.useAsync,
    required this.error,
    required this.isDarkMode,
  });

  /// Initial empty state — no config fetched, no errors, default provider.
  const SettingsState.initial()
      : isLoading = false,
        runtimeConfig = null,
        activeProviderId = 'openai',
        ocrModels = const <String>[],
        serverBaseUrl = 'http://127.0.0.1:8000',
        useAsync = false,
        error = null,
        isDarkMode = false;

  final bool isLoading;
  final RuntimeConfig? runtimeConfig;
  final String activeProviderId;
  final List<String> ocrModels;
  final String serverBaseUrl;
  final bool useAsync;
  final String? error;
  final bool isDarkMode;

  SettingsState copyWith({
    bool? isLoading,
    RuntimeConfig? runtimeConfig,
    String? activeProviderId,
    List<String>? ocrModels,
    String? serverBaseUrl,
    bool? useAsync,
    String? error,
    bool? isDarkMode,
    bool clearError = false,
    bool clearRuntimeConfig = false,
  }) {
    return SettingsState(
      isLoading: isLoading ?? this.isLoading,
      runtimeConfig:
          clearRuntimeConfig ? null : (runtimeConfig ?? this.runtimeConfig),
      activeProviderId: activeProviderId ?? this.activeProviderId,
      ocrModels: ocrModels ?? this.ocrModels,
      serverBaseUrl: serverBaseUrl ?? this.serverBaseUrl,
      useAsync: useAsync ?? this.useAsync,
      error: clearError ? null : (error ?? this.error),
      isDarkMode: isDarkMode ?? this.isDarkMode,
    );
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is SettingsState &&
        other.isLoading == isLoading &&
        other.runtimeConfig == runtimeConfig &&
        other.activeProviderId == activeProviderId &&
        listEquals(other.ocrModels, ocrModels) &&
        other.serverBaseUrl == serverBaseUrl &&
        other.useAsync == useAsync &&
        other.error == error &&
        other.isDarkMode == isDarkMode;
  }

  @override
  int get hashCode => Object.hash(
        isLoading,
        runtimeConfig,
        activeProviderId,
        Object.hashAll(ocrModels),
        serverBaseUrl,
        useAsync,
        error,
        isDarkMode,
      );
}
