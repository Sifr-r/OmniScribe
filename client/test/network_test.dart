import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/core/network/api_exceptions.dart';

// Top-level counter so the onUnauthorized callback can record invocations
// from inside ApiClient catch blocks where the per-test `fired` capture
// isn't directly observable.
int unauthInvocations = 0;

void main() {
  group('ApiClient Error Translation Tests', () {
    late ApiClient apiClient;

    setUp(() {
      apiClient = ApiClient();
    });

    test('Translates 400 bad request to ValidationException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 400,
                  data: {'error': 'bad_request', 'detail': 'Missing parameter'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<ValidationException>()
              .having((e) => e.statusCode, 'statusCode', 400)
              .having((e) => e.error, 'error', 'bad_request'),
        ),
      );
    });

    test('Translates 401 unauthorized to UnauthorizedException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 401,
                  data: {'error': 'unauthorized', 'detail': 'Invalid API Key'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<UnauthorizedException>()
              .having((e) => e.statusCode, 'statusCode', 401)
              .having((e) => e.message, 'message', 'Invalid API Key'),
        ),
      );
    });

    test('Translates 503 circuit_open to CircuitOpenException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 503,
                  data: {
                    'error': 'circuit_open',
                    'detail': 'LLM endpoint circuit breaker is open',
                  },
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<CircuitOpenException>()
              .having((e) => e.statusCode, 'statusCode', 503)
              .having((e) => e.error, 'error', 'circuit_open'),
        ),
      );
    });

    test('Translates connection timeout to NetworkException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                type: DioExceptionType.connectionTimeout,
                message: 'Connection timed out',
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<NetworkException>()
              .having((e) => e.isTimeout, 'isTimeout', isTrue),
        ),
      );
    });

    test('Translates 403 forbidden to ForbiddenException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 403,
                  data: {'error': 'forbidden', 'detail': 'Access denied'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<ForbiddenException>()
              .having((e) => e.statusCode, 'statusCode', 403)
              .having((e) => e.error, 'error', 'forbidden'),
        ),
      );
    });

    test('Translates 404 not found to NotFoundException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 404,
                  data: {'error': 'not_found', 'detail': 'Resource missing'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<NotFoundException>()
              .having((e) => e.statusCode, 'statusCode', 404)
              .having((e) => e.error, 'error', 'not_found'),
        ),
      );
    });

    test('Translates 409 conflict to ConflictException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 409,
                  data: {'error': 'conflict', 'detail': 'State conflict'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<ConflictException>()
              .having((e) => e.statusCode, 'statusCode', 409)
              .having((e) => e.error, 'error', 'conflict'),
        ),
      );
    });

    test('Translates 413 payload too large to PayloadTooLargeException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 413,
                  data: {'error': 'payload_too_large', 'detail': 'File exceeds limit'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<PayloadTooLargeException>()
              .having((e) => e.statusCode, 'statusCode', 413)
              .having((e) => e.error, 'error', 'payload_too_large'),
        ),
      );
    });

    test('Translates 422 unprocessable to ValidationException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 422,
                  data: {'error': 'validation_error', 'detail': 'Invalid field'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<ValidationException>()
              .having((e) => e.statusCode, 'statusCode', 422)
              .having((e) => e.error, 'error', 'validation_error'),
        ),
      );
    });

    test('Translates 429 rate limit to RateLimitException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 429,
                  data: {'error': 'rate_limited', 'detail': 'Too many requests'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<RateLimitException>()
              .having((e) => e.statusCode, 'statusCode', 429)
              .having((e) => e.error, 'error', 'rate_limited'),
        ),
      );
    });

    test('Translates 502 bad gateway to ServerException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 502,
                  data: {'error': 'llm_call_failed', 'detail': 'LLM offline'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<ServerException>()
              .having((e) => e.statusCode, 'statusCode', 502)
              .having((e) => e.error, 'error', 'llm_call_failed'),
        ),
      );
    });

    test('Translates 503 non-circuit to ServiceUnavailableException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 503,
                  data: {'error': 'service_unavailable', 'detail': 'Starting up'},
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<ServiceUnavailableException>()
              .having((e) => e.statusCode, 'statusCode', 503)
              .having((e) => e.error, 'error', 'service_unavailable'),
        ),
      );
    });

    test('Translates default 500 server error to ServerException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 500,
                  data: 'Internal error string',
                ),
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<ServerException>()
              .having((e) => e.statusCode, 'statusCode', 500)
              .having((e) => e.message, 'message', 'Internal error string')
              .having((e) => e.error, 'error', 'server_error'),
        ),
      );
    });

    test('Translates DioExceptionType.cancel to JobCancelledException', () async {
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                type: DioExceptionType.cancel,
                message: 'Aborted',
              ),
            );
          },
        ),
      );

      expect(
        () => apiClient.get<dynamic>('/test'),
        throwsA(
          isA<JobCancelledException>(),
        ),
      );
    });

    test('onUnauthorized callback fires on 401 (flag UI without suppressing exception)',
        () async {
      unauthInvocations = 0;
      final flagged = ApiClient(onUnauthorized: () {
        unauthInvocations++;
      });
      final dio = flagged.rawDio;
      // The per-method catch-block pattern owns the onUnauthorized hook
      // (the centralized onError interceptor approach was tried but
      // reverted: Dio's onError chain only fires for transport-layer
      // failures, not for handler.reject() in onRequest). Clear and add
      // the test interceptor so the request fails with 401 the same way
      // it would for a real server response.
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                response: Response(
                  requestOptions: options,
                  statusCode: 401,
                  headers: Headers.fromMap(const <String, List<String>>{
                    'www-authenticate': <String>['Bearer realm="omniscribe"'],
                  }),
                  data: {'error': 'unauthorized', 'detail': 'Invalid API Key'},
                ),
              ),
            );
          },
        ),
      );

      try {
        await flagged.get<dynamic>('/test');
        fail('expected UnauthorizedException');
      } on UnauthorizedException {
        // Expected.
      }
      expect(unauthInvocations, equals(1),
          reason: 'onUnauthorized must fire on a bearer-challenged 401');
    });

    test('onUnauthorized callback does NOT fire on non-401 errors', () async {
      unauthInvocations = 0;
      final flagged = ApiClient(onUnauthorized: () {
        unauthInvocations++;
      });
      final dio = flagged.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            handler.reject(
              DioException(
                requestOptions: options,
                type: DioExceptionType.connectionTimeout,
                message: 'Connection timed out',
              ),
            );
          },
        ),
      );

      try {
        await flagged.get<dynamic>('/test');
        fail('expected NetworkException');
      } on NetworkException {
        // Expected.
      }
      expect(unauthInvocations, equals(0),
          reason: 'onUnauthorized must NOT fire on non-401');
    });

    test('postMultipartBytes passes receiveTimeout to RequestOptions', () async {
      Duration? capturedTimeout;
      final dio = apiClient.rawDio;
      dio.interceptors.clear();
      dio.interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) {
            capturedTimeout = options.receiveTimeout;
            handler.resolve(
              Response(
                requestOptions: options,
                statusCode: 200,
                data: Uint8List.fromList([1, 2, 3]),
              ),
            );
          },
        ),
      );

      final response = await apiClient.postMultipartBytes(
        '/test',
        formData: FormData.fromMap({'foo': 'bar'}),
        receiveTimeout: const Duration(minutes: 30),
      );

      expect(response.statusCode, 200);
      expect(capturedTimeout, const Duration(minutes: 30));
    });
  });
}
