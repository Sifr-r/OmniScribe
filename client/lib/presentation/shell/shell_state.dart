import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';

/// Riverpod 3 [Notifier] holding the currently-active top-level navigation tab.
///
/// Migrated from the Riverpod 2 ``StateProvider<AppTab>``. We keep a tiny
/// ``set()`` shim so call sites read ``ref.read(activeTabProvider.notifier)
/// .set(tab)`` instead of poking the removed ``.state =`` setter.
class ActiveTabNotifier extends Notifier<AppTab> {
  @override
  AppTab build() => AppTab.workstation;

  void set(AppTab value) => state = value;
}

/// Active navigation tab in OmniScribe.
final activeTabProvider =
    NotifierProvider<ActiveTabNotifier, AppTab>(ActiveTabNotifier.new);

/// Riverpod 3 [Notifier] wrapping the current [ThemeMode] (Dark is default in
/// OmniScribe). See [ActiveTabNotifier] for the migration rationale.
class ThemeModeNotifier extends Notifier<ThemeMode> {
  @override
  ThemeMode build() => ThemeMode.dark;

  void set(ThemeMode value) => state = value;
}

/// Current theme mode (Dark is default in OmniScribe).
final themeModeProvider =
    NotifierProvider<ThemeModeNotifier, ThemeMode>(ThemeModeNotifier.new);

/// Riverpod 3 [Notifier] holding the selected LLM / OCR provider preset name.
class ActiveProviderPresetNotifier extends Notifier<String> {
  @override
  String build() => 'Ollama (Local)';

  void set(String value) => state = value;
}

/// Selected LLM / OCR provider preset name.
final activeProviderPresetProvider =
    NotifierProvider<ActiveProviderPresetNotifier, String>(
  ActiveProviderPresetNotifier.new,
);

/// Server health model.
@immutable
class ServerHealthState {
  const ServerHealthState({
    required this.status,
    this.latencyMs,
    this.endpoint = 'http://localhost:8000',
    this.lastChecked,
    this.error,
  });

  final ServerHealth status;
  final int? latencyMs;
  final String endpoint;
  final DateTime? lastChecked;
  final String? error;

  ServerHealthState copyWith({
    ServerHealth? status,
    int? latencyMs,
    bool clearLatencyMs = false,
    String? endpoint,
    DateTime? lastChecked,
    String? error,
    bool clearError = false,
  }) {
    return ServerHealthState(
      status: status ?? this.status,
      latencyMs: clearLatencyMs ? null : (latencyMs ?? this.latencyMs),
      endpoint: endpoint ?? this.endpoint,
      lastChecked: lastChecked ?? this.lastChecked,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

/// Riverpod 3 [Notifier] for Server Health monitoring.
///
/// Migrated from ``StateNotifier<ServerHealthState>`` in Wave 16. The
/// internal ``state =`` setter still works on the new ``Notifier`` class, so
/// the body of [checkHealth] / [setChecking] / [setOnline] / [setOffline]
/// is unchanged.
class ServerHealthNotifier extends Notifier<ServerHealthState> {
  ApiClient? _apiClient;

  @override
  ServerHealthState build() {
    _apiClient = ref.watch(apiClientProvider);
    return const ServerHealthState(status: ServerHealth.checking);
  }

  Future<void> checkHealth() async {
    if (_apiClient == null) return;
    setChecking();
    final stopwatch = Stopwatch()..start();
    try {
      await _apiClient!.get<dynamic>(ApiConstants.apiHealth);
      stopwatch.stop();
      setOnline(latencyMs: stopwatch.elapsedMilliseconds);
    } catch (e) {
      setOffline(error: e.toString());
    }
  }

  void setChecking() {
    state = state.copyWith(status: ServerHealth.checking);
  }

  void setOnline({int? latencyMs}) {
    state = state.copyWith(
      status: ServerHealth.online,
      latencyMs: latencyMs ?? state.latencyMs,
      lastChecked: DateTime.now(),
      error: null,
    );
  }

  void setOffline({String? error}) {
    state = state.copyWith(
      status: ServerHealth.offline,
      clearLatencyMs: true,
      lastChecked: DateTime.now(),
      error: error,
    );
  }
}

/// Provider for server health state.
final serverHealthProvider =
    NotifierProvider<ServerHealthNotifier, ServerHealthState>(
  ServerHealthNotifier.new,
);
