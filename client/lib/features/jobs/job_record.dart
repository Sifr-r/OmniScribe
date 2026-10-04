/// Job lifecycle, history records, and asynchronous submission models.
library;

import 'package:omniscribe_client/core/serialization/json_fields.dart';

class JobRecord {
  const JobRecord({
    required this.id,
    required this.filename,
    required this.model,
    required this.pipelineMode,
    required this.durationS,
    required this.timestamp,
    required this.status,
    this.pages,
    this.failedPages = const <int>[],
    this.textArtifactId,
  });

  final String id;
  final String filename;
  final String model;
  final String pipelineMode;
  final String? pages;
  final double durationS;
  final String timestamp;
  final String status;
  final List<int> failedPages;
  final String? textArtifactId;

  factory JobRecord.fromJson(Map<String, dynamic> json) {
    final failed = <int>[];
    if (json['failed_pages'] is List) {
      for (final item in json['failed_pages'] as List) {
        if (item is num) failed.add(item.toInt());
      }
    }

    return JobRecord(
      id: json['id']?.toString() ?? '',
      filename: json['filename']?.toString() ?? '',
      model: json['model']?.toString() ?? '',
      pipelineMode: json['pipeline_mode']?.toString() ?? '',
      pages: json['pages']?.toString(),
      durationS: (json['duration_s'] as num?)?.toDouble() ?? 0.0,
      timestamp: json['timestamp']?.toString() ?? '',
      status: json['status']?.toString() ?? 'unknown',
      failedPages: failed,
      textArtifactId: json['text_artifact_id']?.toString(),
    );
  }

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'id': id,
      'filename': filename,
      'model': model,
      'pipeline_mode': pipelineMode,
      if (pages != null) 'pages': pages,
      'duration_s': durationS,
      'timestamp': timestamp,
      'status': status,
      'failed_pages': failedPages,
      if (textArtifactId != null) 'text_artifact_id': textArtifactId,
    };
  }
}

/// Response returned from GET /api/process/status/{job_id}.
///
/// Security note (2026-08-29 audit C-3 / H-3): the result ``token`` and
/// the pre-built ``/api/jobs/{id}/result?token=...`` URL are
/// intentionally NOT fields here. The unauthenticated
/// ``/api/process/status/{job_id}`` + ``/api/jobs`` chain would
/// otherwise let any caller fetch another user's OCR'd PDF by walking
/// ``list → id → token → /api/jobs/{id}/result``, defeating the
/// constant-time gate at the server's ``fetch_result`` route. The
/// async client obtains the token out-of-band via the
/// ``job_completed`` SSE event payload
/// (see ``OcrRepository.getJobArtifactToken``), parallel to the sync
/// path's ``X-Text-Artifact-Token`` response header. Only
/// ``textArtifactId`` is safe to expose — it's the opaque handle, not
/// the secret.
class OcrJobStatusResponse {
  const OcrJobStatusResponse({
    required this.jobId,
    required this.filename,
    required this.status,
    required this.createdAt,
    this.startedAt,
    this.completedAt,
    this.durationS,
    this.error,
    this.textArtifactId,
    this.failedPages = const [],
  });

  final String jobId;
  final String filename;
  final String status;
  final double createdAt;
  final double? startedAt;
  final double? completedAt;
  final double? durationS;
  final String? error;
  final String? textArtifactId;
  final List<int> failedPages;

  bool get isPending => status == 'pending' || status == 'queued';
  bool get isProcessing => status == 'processing' || status == 'running';
  bool get isComplete => status == 'complete';
  bool get isCancelled => status == 'cancelled';
  bool get isError => status == 'error';

  factory OcrJobStatusResponse.fromJson(Map<String, dynamic> json) {
    final failed = <int>[];
    if (json['failed_pages'] is List) {
      for (final item in json['failed_pages'] as List) {
        if (item is num) failed.add(item.toInt());
      }
    }

    return OcrJobStatusResponse(
      jobId: json['job_id']?.toString() ?? '',
      filename: json['filename']?.toString() ?? '',
      status: json['status']?.toString() ?? 'pending',
      createdAt: (json['created_at'] as num?)?.toDouble() ?? 0.0,
      startedAt: (json['started_at'] as num?)?.toDouble(),
      completedAt: (json['completed_at'] as num?)?.toDouble(),
      durationS: (json['duration_s'] as num?)?.toDouble(),
      error: json['error']?.toString(),
      textArtifactId: json['text_artifact_id']?.toString(),
      failedPages: failed,
    );
  }

  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'job_id': jobId,
      'filename': filename,
      'status': status,
      'created_at': createdAt,
      if (startedAt != null) 'started_at': startedAt,
      if (completedAt != null) 'completed_at': completedAt,
      if (durationS != null) 'duration_s': durationS,
      if (error != null) 'error': error,
      if (textArtifactId != null) 'text_artifact_id': textArtifactId,
      'failed_pages': failedPages,
    };
  }
}

/// Response returned from POST /api/process/async
class ProcessResponse {
  const ProcessResponse({
    required this.jobId,
    required this.status,
    this.statusUrl,
    this.resultToken,
  });

  final String jobId;
  final String status;
  final String? statusUrl;
  final String? resultToken;

  factory ProcessResponse.fromJson(Map<String, dynamic> json) {
    return ProcessResponse(
      jobId: jsonString(json, 'job_id'),
      status: jsonString(json, 'status'),
      statusUrl: json['status_url'] as String?,
      resultToken: json['result_token'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'job_id': jobId,
        'status': status,
        if (statusUrl != null) 'status_url': statusUrl,
        if (resultToken != null) 'result_token': resultToken,
      };
}

/// Type alias for async submission response matching backend AsyncSubmitResponse schema.
typedef AsyncSubmitResponse = ProcessResponse;
