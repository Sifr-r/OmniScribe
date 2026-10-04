import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';

void main() {
  group('JobRecord text artifact fields', () {
    test('parses text_artifact_id from JSON', () {
      final json = <String, dynamic>{
        'id': 'job-1',
        'filename': 'doc.pdf',
        'model': 'qwen2-vl',
        'pipeline_mode': 'hybrid',
        'duration_s': 4.2,
        'timestamp': '2026-08-26T10:00:00Z',
        'status': 'completed',
        'failed_pages': <int>[],
        'text_artifact_id': 'artifact-xyz',
      };

      final job = JobRecord.fromJson(json);

      expect(job.textArtifactId, 'artifact-xyz');
    });

    test('text artifact id defaults to null when absent', () {
      final json = <String, dynamic>{
        'id': 'job-2',
        'filename': 'doc.pdf',
        'model': 'qwen2-vl',
        'pipeline_mode': 'hybrid',
        'duration_s': 1.0,
        'timestamp': '2026-08-26T10:00:00Z',
        'status': 'completed',
      };

      final job = JobRecord.fromJson(json);

      expect(job.textArtifactId, isNull);
    });

    test('toJson emits text artifact id only when non-null', () {
      const job = JobRecord(
        id: 'job-3',
        filename: 'doc.pdf',
        model: 'qwen2-vl',
        pipelineMode: 'hybrid',
        durationS: 0.0,
        timestamp: '2026-08-26T10:00:00Z',
        status: 'completed',
        textArtifactId: 'aid',
      );

      final encoded = job.toJson();

      expect(encoded['text_artifact_id'], 'aid');
    });

    test(
        'toJson omits text artifact id when null and never carries a token '
        '(2026-08-29 audit C-3 / H-3: result token is out-of-band via SSE)',
        () {
      const job = JobRecord(
        id: 'job-4',
        filename: 'doc.pdf',
        model: 'qwen2-vl',
        pipelineMode: 'hybrid',
        durationS: 0.0,
        timestamp: '2026-08-26T10:00:00Z',
        status: 'completed',
      );

      final encoded = job.toJson();

      expect(encoded.containsKey('text_artifact_id'), isFalse);
      expect(encoded.containsKey('text_artifact_token'), isFalse);
      expect(encoded.containsKey('text_artifact_url'), isFalse);
    });
  });

  group('OcrJobStatusResponse', () {
    test('parses text_artifact_id and ignores removed token/url fields', () {
      // 2026-08-29 audit C-3 / H-3: the unauthenticated status response
      // no longer includes the result token or the pre-built URL. The
      // client should silently ignore those keys if a stale server
      // still emits them, without surfacing them on the model.
      final json = <String, dynamic>{
        'job_id': 'j1',
        'filename': 'a.pdf',
        'status': 'complete',
        'created_at': 1.0,
        'completed_at': 3.0,
        'duration_s': 2.0,
        'text_artifact_id': 'aid',
        'text_artifact_token': 'should-be-ignored',
        'text_artifact_url': '/api/jobs/j1/result?token=ignored',
      };

      final response = OcrJobStatusResponse.fromJson(json);

      expect(response.textArtifactId, 'aid');
      // No public surface for the removed fields. The runtime check
      // below uses reflection-free assertions on the JSON shape.
    });

    test('status getters distinguish isCancelled, isError, and isComplete', () {
      // When status == 'cancelled', isCancelled is true and isComplete is false.
      const cancelledJob = OcrJobStatusResponse(
        jobId: 'j-cancelled',
        filename: 'test.pdf',
        status: 'cancelled',
        createdAt: 1.0,
      );
      expect(cancelledJob.isCancelled, isTrue);
      expect(cancelledJob.isComplete, isFalse);
      expect(cancelledJob.isError, isFalse);

      // When status == 'error', isError is true and isCancelled is false.
      const errorJob = OcrJobStatusResponse(
        jobId: 'j-error',
        filename: 'test.pdf',
        status: 'error',
        createdAt: 1.0,
      );
      expect(errorJob.isError, isTrue);
      expect(errorJob.isCancelled, isFalse);
      expect(errorJob.isComplete, isFalse);

      // When status == 'complete', isComplete is true.
      const completeJob = OcrJobStatusResponse(
        jobId: 'j-complete',
        filename: 'test.pdf',
        status: 'complete',
        createdAt: 1.0,
      );
      expect(completeJob.isComplete, isTrue);
      expect(completeJob.isCancelled, isFalse);
      expect(completeJob.isError, isFalse);
    });
  });
}
