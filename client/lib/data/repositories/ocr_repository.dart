import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/models/document_result.dart';
import 'package:omniscribe_client/data/models/job_record.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';

/// Progress channel session credentials.
class ProgressSessionHandle {
  const ProgressSessionHandle({
    required this.channelId,
    required this.sessionToken,
  });

  final String channelId;
  final String sessionToken;
}

abstract class OcrRepository {
  /// Execute synchronous OCR, returning the resulting searchable PDF bytes and trust headers.
  Future<ProcessOcrResult> processOcrSync({
    required Uint8List fileBytes,
    required String filename,
    ProcessSettings? settings,
    String? progressChannel,
    String? progressToken,
    void Function(int sent, int total)? onSendProgress,
    Duration? receiveTimeout,
  });

  /// Submit an asynchronous OCR job to the worker queue.
  Future<AsyncSubmitResponse> processOcrAsync({
    required Uint8List fileBytes,
    required String filename,
    ProcessSettings? settings,
    String? progressChannel,
    String? progressToken,
    void Function(int sent, int total)? onSendProgress,
  });

  /// Query the status of an active or completed OCR job.
  Future<OcrJobStatusResponse> getOcrStatus(String jobId);

  /// Query the status of an active or completed OCR job (alias for [getOcrStatus]).
  Future<OcrJobStatusResponse> getJobStatus(String jobId);

  /// Download the PDF output for a completed asynchronous OCR job.
  Future<Uint8List> getOcrResultBytes(String jobId, String token);

  /// Download the PDF output for a completed asynchronous OCR job by resolving
  /// its artifact token via SSE and fetching the result bytes.
  Future<Uint8List> downloadResult(String jobId);

  /// Cancel a running or queued job.
  Future<bool> cancelJob(String jobId);

  /// Request a fresh WebSocket progress channel session handle.
  Future<ProgressSessionHandle> openProgressSession({String? clientId});

  /// Cancel an active progress channel. The server rejects a tokenless cancel
  /// with 401, so [sessionToken] must be the handle from [openProgressSession].
  Future<bool> cancelProgressChannel(String channelId,
      {required String sessionToken});

  /// Fetch text artifact content by ID with artifact bearer token.
  Future<String> getTextArtifact(String artifactId, String token);

  /// Resolve the result download token for a completed async job by
  /// replaying the ``/api/process/{jobId}/events`` SSE stream until
  /// the ``job_completed`` event fires. This is the out-of-band
  /// channel that pairs with the sync path's ``X-Text-Artifact-Token``
  Future<String> getJobArtifactToken(String jobId, {Duration? timeout});

  /// Render any page of an uploaded document as PNG preview bytes.
  Future<PagePreviewResult?> renderDocumentPagePreview({
    Uint8List? fileBytes,
    required String filename,
    int pageIndex = 0,
    int dpi = 150,
    String? docId,
  });
}

class OcrRepositoryImpl implements OcrRepository {
  const OcrRepositoryImpl(this._apiClient);

  final ApiClient _apiClient;

  FormData _buildOcrFormData({
    required Uint8List fileBytes,
    required String filename,
    ProcessSettings? settings,
    String? progressChannel,
    String? progressToken,
  }) {
    final s = settings ?? ProcessSettings.defaultSettings();
    final map = <String, dynamic>{
      'file': MultipartFile.fromBytes(fileBytes, filename: filename),
      'model': s.model,
      'api_base': s.apiBase,
      if (s.apiKey.isNotEmpty) 'api_key': s.apiKey,
      'pipeline_mode': s.pipelineMode.value,
      'dense_mode': s.denseMode.value,
      'spellcheck': s.spellcheck.value,
      'preprocess_pages': s.preprocessPages.toString(),
      'orientation_detection': s.orientationDetection.toString(),
      'deskew': s.deskew.toString(),
      'denoise': s.denoise.toString(),
      'normalize_contrast': s.normalizeContrast.toString(),
      'crop_cleanup': s.cropCleanup.toString(),
    };

    if (s.pages != null && s.pages!.isNotEmpty) {
      map['pages'] = s.pages;
    }
    if (s.documentProcessors.isNotEmpty) {
      map['document_processors'] =
          s.documentProcessors.map((p) => p.value).join(',');
    }
    if (progressChannel != null && progressChannel.isNotEmpty) {
      map['progress_channel'] = progressChannel;
    }
    if (progressToken != null && progressToken.isNotEmpty) {
      map['progress_token'] = progressToken;
    }
    if (s.qualityLoopEnabled != null) {
      map['quality_loop_enabled'] = s.qualityLoopEnabled.toString();
    }
    if (s.qualityTarget != null) {
      map['quality_target'] = s.qualityTarget.toString();
    }
    if (s.qualityMaxRetries != null) {
      map['quality_max_retries'] = s.qualityMaxRetries.toString();
    }

    return FormData.fromMap(map);
  }

  @override
  Future<ProcessOcrResult> processOcrSync({
    required Uint8List fileBytes,
    required String filename,
    ProcessSettings? settings,
    String? progressChannel,
    String? progressToken,
    void Function(int sent, int total)? onSendProgress,
    Duration? receiveTimeout,
  }) async {
    final formData = _buildOcrFormData(
      fileBytes: fileBytes,
      filename: filename,
      settings: settings,
      progressChannel: progressChannel,
      progressToken: progressToken,
    );

    final response = await _apiClient.postMultipartBytes(
      ApiConstants.processSync,
      formData: formData,
      onSendProgress: onSendProgress,
      receiveTimeout: receiveTimeout ?? ApiConstants.defaultOcrReceiveTimeout,
    );

    final headers = response.headers;
    final trustRaw = headers[ApiConstants.headerDocumentTrust];
    final trustSummary = TrustSummary.tryParseHeader(trustRaw);

    return ProcessOcrResult(
      pdfBytes: response.data,
      headers: headers,
      trustSummary: trustSummary,
      textArtifactId: headers[ApiConstants.headerTextArtifactId],
      textArtifactToken: headers[ApiConstants.headerTextArtifactToken],
    );
  }

  @override
  Future<AsyncSubmitResponse> processOcrAsync({
    required Uint8List fileBytes,
    required String filename,
    ProcessSettings? settings,
    String? progressChannel,
    String? progressToken,
    void Function(int sent, int total)? onSendProgress,
  }) async {
    final formData = _buildOcrFormData(
      fileBytes: fileBytes,
      filename: filename,
      settings: settings,
      progressChannel: progressChannel,
      progressToken: progressToken,
    );

    final response = await _apiClient.postMultipart<Map<String, dynamic>>(
      ApiConstants.processAsync,
      formData: formData,
      onSendProgress: onSendProgress,
    );

    return AsyncSubmitResponse.fromJson(response.data);
  }

  @override
  Future<OcrJobStatusResponse> getOcrStatus(String jobId) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.processStatus(jobId),
    );
    return OcrJobStatusResponse.fromJson(json);
  }

  @override
  Future<OcrJobStatusResponse> getJobStatus(String jobId) => getOcrStatus(jobId);

  @override
  Future<Uint8List> getOcrResultBytes(String jobId, String token) async {
    return _apiClient.getBytes(
      ApiConstants.jobResult(jobId),
      queryParameters: {'token': token},
      headers: {'Authorization': 'Bearer $token'},
    );
  }

  @override
  Future<Uint8List> downloadResult(String jobId) async {
    final token = await getJobArtifactToken(jobId);
    return getOcrResultBytes(jobId, token);
  }

  @override
  Future<bool> cancelJob(String jobId) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.cancelJob(jobId),
    );
    return json['cancelled'] as bool? ?? false;
  }

  @override
  Future<ProgressSessionHandle> openProgressSession({String? clientId}) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.progressSession,
      data: <String, dynamic>{'client_id': clientId ?? ''},
    );
    return ProgressSessionHandle(
      channelId: json['channel_id']?.toString() ?? '',
      sessionToken: json['session_token']?.toString() ?? '',
    );
  }

  @override
  Future<bool> cancelProgressChannel(String channelId,
      {required String sessionToken}) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.cancelProgress(channelId),
      headers: <String, dynamic>{'X-Session-Token': sessionToken},
    );
    return json['cancelled'] as bool? ?? false;
  }

  @override
  Future<String> getTextArtifact(String artifactId, String token) async {
    final bytes = await _apiClient.getBytes(
      ApiConstants.textArtifact(artifactId),
      headers: {'Authorization': 'Bearer $token'},
    );
    return utf8.decode(bytes);
  }

  @override
  Future<String> getJobArtifactToken(
    String jobId, {
    Duration? timeout,
  }) async {
    // Open the SSE event stream for the job. Per-byte receive timeout
    // is zero (long-lived stream); an overall deadline is enforced
    // via the optional ``timeout`` so the 15s keep-alive does not
    // accidentally cause dio to abort early.
    final response = await _apiClient.rawDio.get<ResponseBody>(
      ApiConstants.processEvents(jobId),
      options: Options(
        responseType: ResponseType.stream,
        headers: const {'Accept': 'text/event-stream'},
        receiveTimeout: Duration.zero,
      ),
    );
    final body = response.data;
    if (body == null) {
      throw const FormatException(
        'Empty SSE body when reading job_completed event',
      );
    }
    final stream = body.stream;
    final completer = Completer<String>();
    StreamSubscription<String>? subscription;
    Timer? deadlineTimer;
    var currentEvent = '';
    subscription = stream
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter())
        .listen(
      (line) {
        // SSE block format:
        //   event: <name>\n
        //   data: <json>\n
        //   \n
        // A blank line terminates the current event block and resets
        // the current-event cursor. Lines starting with ``:`` are
        // keep-alive comments and are ignored.
        if (line.isEmpty) {
          currentEvent = '';
          return;
        }
        if (line.startsWith(':')) {
          return;
        }
        if (line.startsWith('event:')) {
          currentEvent = line.substring(6).trim();
          return;
        }
        if (line.startsWith('data:') && currentEvent == 'job_completed') {
          try {
            final payload = jsonDecode(line.substring(5).trim()) as Object?;
            if (payload is Map && payload['artifact_token'] is String) {
              completer.complete(payload['artifact_token'] as String);
            }
          } on FormatException {
            // Malformed data line; the server never sends these.
          }
        }
      },
      onError: completer.completeError,
      onDone: () {
        if (!completer.isCompleted) {
          completer.completeError(
            StateError(
              'SSE stream for job $jobId closed before job_completed event',
            ),
          );
        }
      },
      cancelOnError: true,
    );
    if (timeout != null) {
      deadlineTimer = Timer(timeout, () {
        if (!completer.isCompleted) {
          completer.completeError(
            TimeoutException(
              'Timed out waiting for job_completed event for $jobId',
              timeout,
            ),
          );
        }
      });
    }
    try {
      return await completer.future;
    } finally {
      deadlineTimer?.cancel();
      await subscription.cancel();
    }
  }

  @override
  Future<PagePreviewResult?> renderDocumentPagePreview({
    Uint8List? fileBytes,
    required String filename,
    int pageIndex = 0,
    int dpi = 150,
    String? docId,
  }) async {
    if (fileBytes == null && (docId == null || docId.isEmpty)) {
      return null;
    }

    // 1. Lightweight path: if docId is present, try fast rasterization without re-uploading file
    if (docId != null && docId.isNotEmpty) {
      try {
        final map = <String, dynamic>{
          'page': pageIndex.toString(),
          'dpi': dpi.toString(),
          'doc_id': docId,
        };
        final formData = FormData.fromMap(map);

        final response = await _apiClient.postMultipartBytes(
          ApiConstants.documentPreview,
          formData: formData,
          receiveTimeout: const Duration(seconds: 30),
        );

        final totalPages =
            int.tryParse(response.getHeader(ApiConstants.headerTotalPages) ?? '') ?? 1;
        final width =
            double.tryParse(response.getHeader(ApiConstants.headerPageWidth) ?? '');
        final height =
            double.tryParse(response.getHeader(ApiConstants.headerPageHeight) ?? '');
        final responseDocId = response.getHeader(ApiConstants.headerDocumentId) ??
            response.getHeader('x-document-id') ??
            response.getHeader('X-Document-Id') ??
            docId;

        return PagePreviewResult(
          bytes: response.data,
          totalPages: totalPages,
          width: width,
          height: height,
          docId: responseDocId,
        );
      } catch (_) {
        // Stale docId or server restarted — fall through to upload fileBytes if available
        if (fileBytes == null) {
          return null;
        }
      }
    }

    // 2. Upload path: send file bytes and acquire new docId
    if (fileBytes != null) {
      try {
        final map = <String, dynamic>{
          'page': pageIndex.toString(),
          'dpi': dpi.toString(),
          'file': MultipartFile.fromBytes(fileBytes, filename: filename),
        };
        final formData = FormData.fromMap(map);

        final response = await _apiClient.postMultipartBytes(
          ApiConstants.documentPreview,
          formData: formData,
          receiveTimeout: const Duration(seconds: 30),
        );

        final totalPages =
            int.tryParse(response.getHeader(ApiConstants.headerTotalPages) ?? '') ?? 1;
        final width =
            double.tryParse(response.getHeader(ApiConstants.headerPageWidth) ?? '');
        final height =
            double.tryParse(response.getHeader(ApiConstants.headerPageHeight) ?? '');
        final responseDocId = response.getHeader(ApiConstants.headerDocumentId) ??
            response.getHeader('x-document-id') ??
            response.getHeader('X-Document-Id');

        return PagePreviewResult(
          bytes: response.data,
          totalPages: totalPages,
          width: width,
          height: height,
          docId: responseDocId,
        );
      } catch (_) {
        return null;
      }
    }

    return null;
  }
}
