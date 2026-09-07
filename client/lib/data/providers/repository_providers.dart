import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/core/websocket/ws_client.dart';
import 'package:omniscribe_client/data/repositories/config_repository.dart';
import 'package:omniscribe_client/data/repositories/feature_repository.dart';
import 'package:omniscribe_client/data/repositories/job_repository.dart';
import 'package:omniscribe_client/data/repositories/ocr_repository.dart';
import 'package:omniscribe_client/data/repositories/provider_repository.dart';
import 'package:omniscribe_client/data/repositories/sample_pdf_repository.dart';

/// Riverpod 3 [Notifier] holding the OmniScribe backend base URL.
///
/// Migrated from ``StateProvider<String>`` in Wave 16. Callers mutate the
/// value via ``ref.read(apiBaseUrlProvider.notifier).set(url)`` instead of
/// the removed ``.state =`` setter pattern.
class ApiBaseUrlNotifier extends Notifier<String> {
  @override
  String build() => ApiConstants.defaultBaseUrl;

  void set(String value) => state = value;
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
    onUnauthorized: () =>
        ref.read(authRequiredProvider.notifier).set(true),
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

/// OCR Repository provider.
final ocrRepositoryProvider = Provider<OcrRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  return OcrRepositoryImpl(apiClient);
});

/// Sample-PDF Repository provider (Sprint 3 / audit U12).
/// Fetches canonical fixture PDFs from the server's
/// ``/api/sample-pdf/{name}`` route so a new user can confirm
/// the install works without finding their own PDF.
final samplePdfRepositoryProvider = Provider<SamplePdfRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  return SamplePdfRepository(apiClient);
});

/// Provider Catalog & Discovery Repository provider.
final providerRepositoryProvider = Provider<ProviderRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  return ProviderRepositoryImpl(apiClient);
});

/// Config Repository provider.
final configRepositoryProvider = Provider<ConfigRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  return ConfigRepositoryImpl(apiClient);
});

/// Job History & Queue Repository provider.
final jobRepositoryProvider = Provider<JobRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  // 2026-08-29 audit C-3 / H-3: downloadResult now resolves the
  // result token via the ``job_completed`` SSE event (out-of-band
  // channel), so the job repo depends on the OCR repo's SSE helper.
  final ocrRepo = ref.watch(ocrRepositoryProvider);
  return JobRepositoryImpl(apiClient, ocrRepo);
});

/// Feature Repository provider (translation, transcription, extraction, glossary, export).
final featureRepositoryProvider = Provider<FeatureRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  return FeatureRepositoryImpl(apiClient);
});
