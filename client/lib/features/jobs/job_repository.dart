import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/core/network/api_exceptions.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/features/workstation/ocr_repository.dart';
import 'package:omniscribe_client/shared/providers/api_providers.dart';

abstract class JobRepository {
  /// Retrieve list of all past and current OCR jobs.
  Future<List<JobRecord>> listJobs();

  /// Clear all completed and failed jobs from queue/history.
  Future<int> clearJobs();

  /// Cancel a running or queued job by ID.
  Future<bool> cancelJob(String jobId);

  /// Download the per-job result PDF bytes. The result token is
  /// fetched out-of-band via the ``job_completed`` SSE event (parallel
  /// to the sync path's ``X-Text-Artifact-Token`` response header);
  /// the unauthenticated ``/api/process/status/{jobId}`` route no
  /// longer returns it (2026-08-29 audit C-3 / H-3).
  Future<Uint8List> downloadResult(String jobId);

  /// Render one page of the original upload as PNG bytes for the
  /// workstation viewport. Returns ``null`` when the server has no
  /// recorded source for the job (e.g. submitted before the preview
  /// route landed, or whose source was cleaned up after completion).
  Future<Uint8List?> fetchPagePreview(String jobId, int pageIndex);
}

class JobRepositoryImpl implements JobRepository {
  const JobRepositoryImpl(this._apiClient, this._ocrRepository);

  final ApiClient _apiClient;
  final OcrRepository _ocrRepository;

  @override
  Future<List<JobRecord>> listJobs() async {
    final dynamic response = await _apiClient.get<dynamic>(
      ApiConstants.jobs,
    );

    final list = <JobRecord>[];
    if (response is List) {
      for (final item in response) {
        if (item is Map<String, dynamic>) {
          list.add(JobRecord.fromJson(item));
        }
      }
    }
    return list;
  }

  @override
  Future<int> clearJobs() async {
    final json = await _apiClient.delete<Map<String, dynamic>>(
      ApiConstants.jobs,
      queryParameters: const {'confirm': true},
    );
    return (json['cleared'] as num?)?.toInt() ?? 0;
  }

  @override
  Future<bool> cancelJob(String jobId) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.cancelJob(jobId),
    );
    return json['cancelled'] as bool? ?? false;
  }

  @override
  Future<Uint8List> downloadResult(String jobId) async {
    final token = await _ocrRepository.getJobArtifactToken(jobId);
    return _apiClient.getBytes(
      ApiConstants.jobResult(jobId),
      headers: {'X-Artifact-Token': token},
    );
  }

  @override
  Future<Uint8List?> fetchPagePreview(String jobId, int pageIndex) async {
    try {
      return await _apiClient.getBytes(
        ApiConstants.jobPagePreview(jobId, pageIndex),
      );
    } on ApiException catch (e) {
      // 404 is the documented "no preview available" path (older job,
      // missing input path, or out-of-range page). Surface null so the
      // viewport falls back to its placeholder.
      if (e.statusCode == 404) return null;
      rethrow;
    }
  }
}

final jobRepositoryProvider = Provider<JobRepository>((ref) {
  final apiClient = ref.watch(apiClientProvider);
  // 2026-08-29 audit C-3 / H-3: downloadResult now resolves the
  // result token via the ``job_completed`` SSE event (out-of-band
  // channel), so the job repo depends on the OCR repo's SSE helper.
  final ocrRepo = ref.watch(ocrRepositoryProvider);
  return JobRepositoryImpl(apiClient, ocrRepo);
});
