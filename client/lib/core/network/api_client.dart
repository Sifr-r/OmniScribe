import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'api_exceptions.dart';

/// Typed wrapper for API responses carrying data, status code, and headers.
class ApiResponse<T> {
  const ApiResponse({
    required this.data,
    required this.statusCode,
    required this.headers,
  });

  final T data;
  final int statusCode;
  final Map<String, String> headers;

  /// Convenience getter for lowercase header lookup.
  String? getHeader(String name) => headers[name.toLowerCase()];
}

/// Core API Client wrapping Dio with typed domain error handling,
/// token management, multipart helpers, and JSON serialization.
class ApiClient {
  ApiClient({
    String baseUrl = ApiConstants.defaultBaseUrl,
    Duration connectTimeout = ApiConstants.defaultConnectTimeout,
    Duration receiveTimeout = ApiConstants.defaultReceiveTimeout,
    Duration sendTimeout = ApiConstants.defaultSendTimeout,
    String? Function()? authTokenProvider,
    Dio? dioOverride,
    this.onUnauthorized,
  })  : _authTokenProvider = authTokenProvider,
        _dio = dioOverride ??
            Dio(
              BaseOptions(
                baseUrl: _assertBaseUrlIsTransportSafe(baseUrl),
                connectTimeout: connectTimeout,
                receiveTimeout: receiveTimeout,
                sendTimeout: sendTimeout,
                headers: <String, dynamic>{
                  'Accept': 'application/json',
                },
                responseType: ResponseType.json,
              ),
            ) {
    _initInterceptors();
  }

  final Dio _dio;
  final String? Function()? _authTokenProvider;
  String? _staticAuthToken;

  /// Invoked synchronously when the server refuses this client's bearer
  /// credential — a 401 answering with ``WWW-Authenticate: Bearer``.
  /// Used by `repository_providers.dart` to flip `authRequiredProvider` so the
  /// UI can surface an `AuthRequiredBanner`. The exception is still translated
  /// and re-thrown — this hook is purely for flagging the UI.
  final void Function()? onUnauthorized;

  Dio get rawDio => _dio;
  String get baseUrl => _dio.options.baseUrl;

  set baseUrl(String newBaseUrl) {
    // Sprint 3 / C-2 audit fix: refuse non-loopback plaintext HTTP.
    // Loopback (127.0.0.1, ::1, localhost) is the documented
    // local-trusted mode and remains allowed in plaintext. Any other
    // host must use HTTPS — otherwise the bearer token is sent
    // over the wire unencrypted. The check is a runtime guard, not
    // a build-time flag, so a user pasting a public IP into the
    // settings screen gets a clear error.
    _assertBaseUrlIsTransportSafe(newBaseUrl);
    _dio.options.baseUrl = newBaseUrl;
  }

  /// True for loopback hosts where plaintext HTTP/WS is documented safe.
  static bool _isLoopbackHost(String host) {
    final lower = host.toLowerCase();
    return lower == '127.0.0.1'
        || lower == '::1'
        || lower == 'localhost'
        || lower == '[::1]';
  }

  static String _assertBaseUrlIsTransportSafe(String url) {
    final parsed = Uri.tryParse(url);
    if (parsed == null || (parsed.scheme != 'http' && parsed.scheme != 'https')) {
      throw ArgumentError(
        'api_base must be http(s); got $url',
      );
    }
    final host = parsed.host;
    if (parsed.scheme == 'http' && !_isLoopbackHost(host)) {
      throw ArgumentError(
        "Refusing plaintext HTTP for non-loopback host '$host'. "
        'Use https:// for any server reachable from a network.',
      );
    }
    return url;
  }

  void setAuthToken(String? token) {
    _staticAuthToken = token;
  }

  /// Flags the UI only for a real bearer-auth refusal, which
  /// ``BearerAuthMiddleware`` marks with ``WWW-Authenticate: Bearer``. A 401
  /// from a route guarding another credential — the per-channel progress
  /// session token, an artifact token — is not a missing bearer token, and the
  /// banner's advice would send the user to Settings for nothing.
  void _flagUnauthorizedOnBearerChallenge(DioException e) {
    if (e.response?.statusCode != 401) return;
    final challenges = e.response?.headers['www-authenticate'];
    if (challenges == null) return;
    if (challenges.any(
      (c) => c.trim().toLowerCase().startsWith('bearer'),
    )) {
      onUnauthorized?.call();
    }
  }

  /// Dio keeps the casing callers supply, so presence cannot be tested with a
  /// literal key.
  static bool _hasAuthorization(Map<String, dynamic> headers) =>
      headers.keys.any((k) => k.toLowerCase() == 'authorization');

  void _initInterceptors() {
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          final token = _staticAuthToken ?? _authTokenProvider?.call();
          // A per-request bearer (an artifact or result token) wins over the
          // server-wide one; overwriting it would 403 those downloads.
          if (token != null &&
              token.isNotEmpty &&
              !_hasAuthorization(options.headers)) {
            options.headers['Authorization'] = 'Bearer $token';
          }
          return handler.next(options);
        },
      ),
    );
  }

  /// Perform a GET request returning deserialized data of type [T].
  Future<T> get<T>(
    String path, {
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.get<T>(
        path,
        queryParameters: queryParameters,
        options: _mergeOptions(options, headers: headers),
        cancelToken: cancelToken,
      );
      return response.data as T;
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(message: 'Unexpected error during GET $path: $e');
    }
  }

  /// Perform a GET request returning an [ApiResponse<T>] containing headers.
  Future<ApiResponse<T>> getWithHeaders<T>(
    String path, {
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.get<T>(
        path,
        queryParameters: queryParameters,
        options: _mergeOptions(options, headers: headers),
        cancelToken: cancelToken,
      );
      return ApiResponse<T>(
        data: response.data as T,
        statusCode: response.statusCode ?? 200,
        headers: _extractHeaders(response.headers),
      );
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(
        message: 'Unexpected error during GET (headers) $path: $e',
      );
    }
  }

  /// Perform a POST request returning deserialized data of type [T].
  Future<T> post<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.post<T>(
        path,
        data: data,
        queryParameters: queryParameters,
        options: _mergeOptions(options, headers: headers),
        cancelToken: cancelToken,
      );
      return response.data as T;
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(message: 'Unexpected error during POST $path: $e');
    }
  }

  /// Perform a POST request returning an [ApiResponse<T>] containing headers.
  Future<ApiResponse<T>> postWithHeaders<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.post<T>(
        path,
        data: data,
        queryParameters: queryParameters,
        options: _mergeOptions(options, headers: headers),
        cancelToken: cancelToken,
      );
      return ApiResponse<T>(
        data: response.data as T,
        statusCode: response.statusCode ?? 200,
        headers: _extractHeaders(response.headers),
      );
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(
        message: 'Unexpected error during POST (headers) $path: $e',
      );
    }
  }

  /// Perform a PUT request returning deserialized data of type [T].
  Future<T> put<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.put<T>(
        path,
        data: data,
        queryParameters: queryParameters,
        options: _mergeOptions(options, headers: headers),
        cancelToken: cancelToken,
      );
      return response.data as T;
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(message: 'Unexpected error during PUT $path: $e');
    }
  }

  /// Perform a DELETE request returning deserialized data of type [T].
  Future<T> delete<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.delete<T>(
        path,
        data: data,
        queryParameters: queryParameters,
        options: _mergeOptions(options, headers: headers),
        cancelToken: cancelToken,
      );
      return response.data as T;
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(
        message: 'Unexpected error during DELETE $path: $e',
      );
    }
  }

  /// Download raw bytes (for PDFs, DOCX, binary blobs).
  Future<Uint8List> getBytes(
    String path, {
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.get<List<int>>(
        path,
        queryParameters: queryParameters,
        options: _mergeOptions(
          Options(responseType: ResponseType.bytes),
          headers: headers,
        ),
        cancelToken: cancelToken,
      );
      final rawData = response.data;
      if (rawData == null) {
        return Uint8List(0);
      }
      return Uint8List.fromList(rawData);
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(
        message: 'Unexpected error downloading bytes from $path: $e',
      );
    }
  }

  /// Download raw bytes with response headers (e.g. sync OCR returning PDF bytes + trust headers).
  Future<ApiResponse<Uint8List>> getBytesWithHeaders(
    String path, {
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    CancelToken? cancelToken,
  }) async {
    try {
      final response = await _dio.get<List<int>>(
        path,
        queryParameters: queryParameters,
        options: _mergeOptions(
          Options(responseType: ResponseType.bytes),
          headers: headers,
        ),
        cancelToken: cancelToken,
      );
      final rawData = response.data;
      return ApiResponse<Uint8List>(
        data: rawData != null ? Uint8List.fromList(rawData) : Uint8List(0),
        statusCode: response.statusCode ?? 200,
        headers: _extractHeaders(response.headers),
      );
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(
        message:
            'Unexpected error downloading bytes with headers from $path: $e',
      );
    }
  }

  /// Perform a multipart upload returning an [ApiResponse<T>] with data and headers.
  Future<ApiResponse<T>> postMultipart<T>(
    String path, {
    required FormData formData,
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
    void Function(int sent, int total)? onSendProgress,
  }) async {
    try {
      final response = await _dio.post<T>(
        path,
        data: formData,
        queryParameters: queryParameters,
        options: _mergeOptions(options, headers: headers),
        cancelToken: cancelToken,
        onSendProgress: onSendProgress,
      );
      return ApiResponse<T>(
        data: response.data as T,
        statusCode: response.statusCode ?? 200,
        headers: _extractHeaders(response.headers),
      );
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(
        message: 'Unexpected error during multipart POST $path: $e',
      );
    }
  }

  /// Perform a multipart upload returning raw bytes (used for sync OCR POST /api/process).
  Future<ApiResponse<Uint8List>> postMultipartBytes(
    String path, {
    required FormData formData,
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic>? headers,
    Options? options,
    CancelToken? cancelToken,
    Duration? receiveTimeout,
    Duration? sendTimeout,
    void Function(int sent, int total)? onSendProgress,
  }) async {
    try {
      final baseOptions = options ?? Options();
      final effectiveOptions = _mergeOptions(
        baseOptions.copyWith(
          responseType: ResponseType.bytes,
          receiveTimeout: receiveTimeout ?? baseOptions.receiveTimeout,
          sendTimeout: sendTimeout ?? baseOptions.sendTimeout,
        ),
        headers: headers,
      );
      final response = await _dio.post<List<int>>(
        path,
        data: formData,
        queryParameters: queryParameters,
        options: effectiveOptions,
        cancelToken: cancelToken,
        onSendProgress: onSendProgress,
      );
      final rawData = response.data;
      return ApiResponse<Uint8List>(
        data: rawData != null ? Uint8List.fromList(rawData) : Uint8List(0),
        statusCode: response.statusCode ?? 200,
        headers: _extractHeaders(response.headers),
      );
    } on DioException catch (e) {
      _flagUnauthorizedOnBearerChallenge(e);
      throw _translateDioError(e);
    } catch (e) {
      if (e is ApiException) rethrow;
      throw NetworkException(
        message: 'Unexpected error during multipart byte POST $path: $e',
      );
    }
  }

  Options _mergeOptions(Options? options, {Map<String, dynamic>? headers}) {
    final merged = options ?? Options();
    if (headers != null && headers.isNotEmpty) {
      merged.headers = <String, dynamic>{
        ...?merged.headers,
        ...headers,
      };
    }
    return merged;
  }

  Map<String, String> _extractHeaders(Headers headers) {
    final result = <String, String>{};
    headers.forEach((key, values) {
      if (values.isNotEmpty) {
        result[key.toLowerCase()] = values.first;
      }
    });
    return result;
  }

  ApiException _translateDioError(DioException error) {
    if (error.type == DioExceptionType.connectionTimeout ||
        error.type == DioExceptionType.sendTimeout ||
        error.type == DioExceptionType.receiveTimeout) {
      return NetworkException(
        message: 'Request timed out: ${error.message}',
        isTimeout: true,
        detail: error.error,
      );
    }

    if (error.type == DioExceptionType.connectionError) {
      return NetworkException(
        message:
            'Unable to connect to OmniScribe server at $baseUrl: ${error.message}',
        detail: error.error,
      );
    }

    if (error.type == DioExceptionType.cancel) {
      return const JobCancelledException(message: 'Request was cancelled.');
    }

    final response = error.response;
    if (response == null) {
      return NetworkException(
        message: error.message ?? 'Unknown network communication failure',
        detail: error.error,
      );
    }

    final statusCode = response.statusCode ?? 500;
    final dynamic data = response.data;

    String? errorType;
    String? detailMessage;
    dynamic rawDetail;

    if (data is Map<String, dynamic>) {
      errorType = data['error']?.toString();
      rawDetail = data['detail'];
      detailMessage = rawDetail is String ? rawDetail : jsonEncode(rawDetail);
    } else if (data is String) {
      detailMessage = data;
    }

    final message = detailMessage ?? error.message ?? 'HTTP $statusCode Error';

    switch (statusCode) {
      case 400:
        return ValidationException(
          message: message,
          statusCode: 400,
          error: errorType ?? 'bad_request',
          detail: rawDetail,
        );
      case 401:
        return UnauthorizedException(
          message: message,
          error: errorType ?? 'unauthorized',
          detail: rawDetail,
        );
      case 403:
        return ForbiddenException(
          message: message,
          error: errorType ?? 'forbidden',
          detail: rawDetail,
        );
      case 404:
        return NotFoundException(
          message: message,
          error: errorType ?? 'not_found',
          detail: rawDetail,
        );
      case 409:
        return ConflictException(
          message: message,
          error: errorType ?? 'conflict',
          detail: rawDetail,
        );
      case 413:
        return PayloadTooLargeException(
          message: message,
          error: errorType ?? 'payload_too_large',
          detail: rawDetail,
        );
      case 422:
        return ValidationException(
          message: message,
          statusCode: 422,
          error: errorType ?? 'validation_error',
          detail: rawDetail,
        );
      case 429:
        return RateLimitException(
          message: message,
          error: errorType ?? 'rate_limited',
          detail: rawDetail,
        );
      case 502:
        return ServerException(
          message: message,
          statusCode: 502,
          error: errorType ?? 'llm_call_failed',
          detail: rawDetail,
        );
      case 503:
        if (errorType == 'circuit_open') {
          return CircuitOpenException(
            message: message,
            error: errorType,
            detail: rawDetail,
          );
        }
        return ServiceUnavailableException(
          message: message,
          error: errorType ?? 'service_unavailable',
          detail: rawDetail,
        );
      default:
        return ServerException(
          message: message,
          statusCode: statusCode,
          error: errorType ?? 'server_error',
          detail: rawDetail,
        );
    }
  }
}
