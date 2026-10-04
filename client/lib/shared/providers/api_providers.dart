import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/core/websocket/ws_client.dart';

/// Riverpod 3 [Notifier] holding the OmniScribe backend base URL.
///
/// Migrated from ``StateProvider<String>`` in Wave 16. Callers mutate the
/// value via ``ref.read(apiBaseUrlProvider.notifier).set(url)`` instead of
/// the removed ``.state =`` setter pattern.
class ApiBaseUrlNotifier extends Notifier<String> {
  @override
  String build() => ApiConstants.defaultBaseUrl;

  void set(String value) => state = ApiClient.validateBaseUrl(value.trim());
}

/// Base URL provider for the OmniScribe backend server.
final apiBaseUrlProvider =
    NotifierProvider<ApiBaseUrlNotifier, String>(ApiBaseUrlNotifier.new);

/// Riverpod 3 [Notifier] holding the active bearer auth token.
class AuthTokenNotifier extends Notifier<String?> {
  @override
  String? build() => null;

  void set(String? value) => state = value;
}

/// Global/active auth token provider.
final authTokenProvider =
    NotifierProvider<AuthTokenNotifier, String?>(AuthTokenNotifier.new);

/// Riverpod 3 [Notifier] holding the "auth required" banner flag.
///
/// True when the API client has observed a 401 since the last dismiss.
/// Mounted as a banner by AppShell; flipping it does not auto-clear.
class AuthRequiredNotifier extends Notifier<bool> {
  @override
  bool build() => false;

  void set(bool value) => state = value;
}

/// True when the API client has observed a 401 since the last dismiss.
final authRequiredProvider =
    NotifierProvider<AuthRequiredNotifier, bool>(AuthRequiredNotifier.new);

/// Core ApiClient provider.
final apiClientProvider = Provider<ApiClient>((ref) {
  final baseUrl = ref.watch<String>(apiBaseUrlProvider);
  final client = ApiClient(
    baseUrl: baseUrl,
    authTokenProvider: () => ref.read<String?>(authTokenProvider),
    onUnauthorized: () => ref.read(authRequiredProvider.notifier).set(true),
  );
  return client;
});

/// WebSocket Client provider.
final wsClientProvider = Provider<WsClient>((ref) {
  final baseUrl = ref.watch(apiBaseUrlProvider);
  // Sprint 3 / H-6 audit fix: derive the WebSocket scheme from the
  // API scheme. The previous ``baseUrl.replaceFirst(RegExp(r'^http'),
  // 'ws')`` only handled ``http://`` (mapping to ``ws://``); a public
  // ``https://`` server produced a ``https://...`` WS URL that the
  // server's WebSocket router would reject. Build the WS URL via
  // ``Uri.parse`` so the scheme mapping is exact and we don't
  // accidentally rewrite ``http``-like substrings inside the
  // host or path.
  final parsed = Uri.tryParse(baseUrl);
  String wsUrl;
  if (parsed == null) {
    // Best-effort fallback: leave the legacy ``http->ws`` rewrite in
    // place so an unparseable URL still produces something WsClient
    // can try. ``Uri.parse`` would have raised.
    wsUrl = baseUrl.replaceFirst(RegExp(r'^http'), 'ws');
  } else {
    final wsScheme = switch (parsed.scheme) {
      'https' => 'wss',
      'http' => 'ws',
      'wss' => 'wss',
      'ws' => 'ws',
      _ => 'ws',
    };
    wsUrl = parsed.replace(scheme: wsScheme).toString();
  }
  final ws = WsClient(defaultWsBaseUrl: wsUrl);
  ref.onDispose(ws.dispose);
  return ws;
});
