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
    required this.serverBearerToken,
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
        serverBearerToken = null,
        useAsync = false,
        error = null,
        isDarkMode = false;

  final bool isLoading;
  final RuntimeConfig? runtimeConfig;
  final String activeProviderId;
  final List<String> ocrModels;
  final String serverBaseUrl;

  /// Bearer sent to the OmniScribe backend when it arms `OMNISCRIBE_AUTH_TOKEN`.
  /// Session-only, like [serverBaseUrl], and unrelated to a provider API key.
  final String? serverBearerToken;
  final bool useAsync;
  final String? error;
  final bool isDarkMode;

  SettingsState copyWith({
    bool? isLoading,
    RuntimeConfig? runtimeConfig,
    String? activeProviderId,
    List<String>? ocrModels,
    String? serverBaseUrl,
    String? serverBearerToken,
    bool? useAsync,
    String? error,
    bool? isDarkMode,
    bool clearError = false,
    bool clearRuntimeConfig = false,
    bool clearServerBearerToken = false,
  }) {
    return SettingsState(
      isLoading: isLoading ?? this.isLoading,
      runtimeConfig:
          clearRuntimeConfig ? null : (runtimeConfig ?? this.runtimeConfig),
      activeProviderId: activeProviderId ?? this.activeProviderId,
      ocrModels: ocrModels ?? this.ocrModels,
      serverBaseUrl: serverBaseUrl ?? this.serverBaseUrl,
      serverBearerToken: clearServerBearerToken
          ? null
          : (serverBearerToken ?? this.serverBearerToken),
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
        other.serverBearerToken == serverBearerToken &&
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
        serverBearerToken,
        useAsync,
        error,
        isDarkMode,
      );
}
