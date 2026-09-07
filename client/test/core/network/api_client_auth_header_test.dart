// A caller-supplied Authorization header must win over the global server
// bearer. Artifact downloads (getTextArtifact / getOcrResultBytes) send their
// own short-lived bearer; if the interceptor overwrites it, configuring a
// server token turns every valid download into a 403.

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omniscribe_client/core/network/api_client.dart';

class _CaptureAdapter implements HttpClientAdapter {
  RequestOptions? lastRequest;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    lastRequest = options;
    return ResponseBody(
      Stream.value(Uint8List.fromList(utf8.encode('{"ok":true}'))),
      200,
      headers: <String, List<String>>{
        'content-type': <String>['application/json'],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

/// Dio keeps header names as supplied by callers but lowercases the ones it
/// merges itself, so lookup must not depend on case.
String? header(RequestOptions? options, String name) {
  if (options == null) return null;
  for (final entry in options.headers.entries) {
    if (entry.key.toLowerCase() == name.toLowerCase()) {
      return entry.value.toString();
    }
  }
  return null;
}

void main() {
  group('ApiClient Authorization precedence', () {
    late _CaptureAdapter adapter;
    late ApiClient client;

    setUp(() {
      adapter = _CaptureAdapter();
      client = ApiClient(
        baseUrl: 'http://127.0.0.1:8000',
        authTokenProvider: () => 'server-secret',
      );
      client.rawDio.httpClientAdapter = adapter;
    });

    test('keeps a caller-supplied Authorization over the server bearer',
        () async {
      await client.get<Map<String, dynamic>>(
        '/api/artifacts/a-1',
        headers: <String, dynamic>{'Authorization': 'Bearer artifact-token'},
      );

      expect(header(adapter.lastRequest, 'Authorization'),
          'Bearer artifact-token');
    });

    test('applies the server bearer when the caller supplied none', () async {
      await client.get<Map<String, dynamic>>('/api/config');

      expect(header(adapter.lastRequest, 'Authorization'), 'Bearer server-secret');
    });

    test('sends no Authorization when no server bearer is configured',
        () async {
      final anonymous = ApiClient(baseUrl: 'http://127.0.0.1:8000');
      final anonymousAdapter = _CaptureAdapter();
      anonymous.rawDio.httpClientAdapter = anonymousAdapter;

      await anonymous.get<Map<String, dynamic>>('/api/config');

      expect(header(anonymousAdapter.lastRequest, 'Authorization'), isNull);
    });
  });
}
