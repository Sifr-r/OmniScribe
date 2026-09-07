// The AuthRequiredBanner means exactly one thing: the OmniScribe server
// refused this client's bearer token. Only BearerAuthMiddleware answers with
// ``WWW-Authenticate: Bearer``; other 401s in the app (e.g. /api/progress/
// cancel/{id} rejecting a missing per-channel session token) are a different
// credential entirely and must not flip the banner.

import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/core/network/api_exceptions.dart';

class _StubAdapter implements HttpClientAdapter {
  _StubAdapter({required this.statusCode, this.wwwAuthenticate});

  final int statusCode;
  final String? wwwAuthenticate;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final body = utf8.encode(jsonEncode(<String, String>{
      'detail': statusCode == 401 && wwwAuthenticate == null
          ? 'session token required (X-Session-Token header or session_token '
              'query param)'
          : 'valid bearer token required',
    }));
    return ResponseBody(
      Stream.value(Uint8List.fromList(body)),
      statusCode,
      headers: <String, List<String>>{
        'content-type': <String>['application/json'],
        if (wwwAuthenticate != null)
          'www-authenticate': <String>[wwwAuthenticate!],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  group('ApiClient onUnauthorized 401 discrimination', () {
    late int fired;

    setUp(() => fired = 0);

    Future<void> postWith(_StubAdapter adapter) async {
      final client = ApiClient(
        baseUrl: 'http://127.0.0.1:8000',
        onUnauthorized: () => fired++,
      );
      client.rawDio.httpClientAdapter = adapter;
      try {
        await client.post<Map<String, dynamic>>('/api/progress/cancel/ch-1');
      } on ApiException catch (_) {
        // ApiClient still translates and rethrows; this test only observes
        // whether the UI flag was raised on the way out.
      }
    }

    test('fires for a bearer challenge from BearerAuthMiddleware', () async {
      await postWith(_StubAdapter(
        statusCode: 401,
        wwwAuthenticate: 'Bearer realm="omniscribe"',
      ));
      expect(fired, 1);
    });

    test('stays silent for a 401 that is not a bearer challenge', () async {
      await postWith(_StubAdapter(statusCode: 401));
      expect(fired, 0);
    });

    test('stays silent for a 200', () async {
      await postWith(_StubAdapter(statusCode: 200));
      expect(fired, 0);
    });
  });
}
