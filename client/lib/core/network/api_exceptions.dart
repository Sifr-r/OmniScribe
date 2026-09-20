/// Domain exceptions for OmniScribe network and API operations.
library;

/// Base exception class for all OmniScribe API errors.
abstract class ApiException implements Exception {
  const ApiException({
    required this.message,
    this.statusCode,
    this.error,
    this.detail,
  });

  final String message;
  final int? statusCode;
  final String? error;
  final dynamic detail;

  @override
  String toString() {
    final buffer = StringBuffer(runtimeType.toString())
      ..write(': ')
      ..write(message);
    if (statusCode != null) {
      buffer.write(' (HTTP $statusCode)');
    }
    if (error != null) {
      buffer.write(' [error: $error]');
    }
    if (detail != null) {
      buffer.write(' [detail: $detail]');
    }
    return buffer.toString();
  }
}

/// Thrown when network connectivity fails (connection refused, timeouts, DNS errors).
class NetworkException extends ApiException {
  const NetworkException({
    required super.message,
    super.statusCode,
    super.error = 'network_error',
    super.detail,
    this.isTimeout = false,
  });

  final bool isTimeout;
}

/// Thrown when request payload validation fails on client or server (HTTP 400, 422).
class ValidationException extends ApiException {
  const ValidationException({
    required super.message,
    super.statusCode = 422,
    super.error = 'validation_error',
    super.detail,
    this.errors = const {},
  });

  final Map<String, List<String>> errors;
}

/// Thrown when authentication fails or API key is invalid/missing (HTTP 401).
class UnauthorizedException extends ApiException {
  const UnauthorizedException({
    required super.message,
    super.statusCode = 401,
    super.error = 'unauthorized',
    super.detail,
  });
}

/// Thrown when access is forbidden or an artifact token is rejected (HTTP 403).
class ForbiddenException extends ApiException {
  const ForbiddenException({
    required super.message,
    super.statusCode = 403,
    super.error = 'forbidden',
    super.detail,
  });
}

/// Thrown when the requested resource, route, job, or artifact is not found (HTTP 404).
class NotFoundException extends ApiException {
  const NotFoundException({
    required super.message,
    super.statusCode = 404,
    super.error = 'not_found',
    super.detail,
  });
}

/// Thrown when there is a state conflict (e.g. fetching job result before completion) (HTTP 409).
class ConflictException extends ApiException {
  const ConflictException({
    required super.message,
    super.statusCode = 409,
    super.error = 'conflict',
    super.detail,
  });
}

/// Thrown when upload file exceeds server configured size limit (HTTP 413).
class PayloadTooLargeException extends ApiException {
  const PayloadTooLargeException({
    required super.message,
    super.statusCode = 413,
    super.error = 'payload_too_large',
    super.detail,
    this.maxBytes,
  });

  final int? maxBytes;
}

/// Thrown when the LLM provider account cannot pay for the request
/// (HTTP 402, e.g. cloud provider out of credits).
class PaymentRequiredException extends ApiException {
  const PaymentRequiredException({
    required super.message,
    super.statusCode = 402,
    super.error = 'payment_required',
    super.detail,
  });
}

/// Thrown when upstream LLM or backend rate limit is encountered (HTTP 429).
class RateLimitException extends ApiException {
  const RateLimitException({
    required super.message,
    super.statusCode = 429,
    super.error = 'rate_limited',
    super.detail,
    this.retryAfterSeconds,
  });

  final double? retryAfterSeconds;
}

/// Thrown when upstream LLM call fails or internal server error occurs (HTTP 500, 502).
class ServerException extends ApiException {
  const ServerException({
    required super.message,
    super.statusCode = 500,
    super.error = 'server_error',
    super.detail,
  });
}

/// Thrown when service is starting or model is not loaded (HTTP 503).
class ServiceUnavailableException extends ApiException {
  const ServiceUnavailableException({
    required super.message,
    super.statusCode = 503,
    super.error = 'service_unavailable',
    super.detail,
  });
}

/// Thrown when the LLM circuit breaker is currently open (HTTP 503 circuit_open).
class CircuitOpenException extends ApiException {
  const CircuitOpenException({
    required super.message,
    super.statusCode = 503,
    super.error = 'circuit_open',
    super.detail,
    this.failures = 0,
    this.retryAfterSeconds,
  });

  final int failures;
  final double? retryAfterSeconds;
}

/// Thrown when an asynchronous or pipeline job was cancelled.
class JobCancelledException extends ApiException {
  const JobCancelledException({
    required super.message,
    super.error = 'job_cancelled',
    super.detail,
  });
}
