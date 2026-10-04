import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/features/jobs/jobs_state.dart';

void main() {
  group('JobsState', () {
    test('default constructor returns empty list, not fetching, no error', () {
      const state = JobsState();

      expect(state.jobs, isEmpty);
      expect(state.isFetching, isFalse);
      expect(state.error, isNull);
    });

    test('copyWith preserves untouched fields', () {
      const before = JobsState();
      final after = before.copyWith(isFetching: true);

      expect(after.isFetching, isTrue);
      expect(after.jobs, before.jobs);
      expect(after.error, before.error);
    });

    test(
        'copyWith clearError: true resets error to null even when error param is null',
        () {
      const before = JobsState(error: 'boom');
      final after = before.copyWith(clearError: true);

      expect(after.error, isNull);
    });

    test('copyWith replaces jobs list wholesale (no merge)', () {
      const a = JobRecord(
        id: 'a',
        filename: 'a.pdf',
        model: 'm',
        pipelineMode: 'hybrid',
        durationS: 0,
        timestamp: 't',
        status: 'completed',
      );
      const b = JobRecord(
        id: 'b',
        filename: 'b.pdf',
        model: 'm',
        pipelineMode: 'hybrid',
        durationS: 0,
        timestamp: 't',
        status: 'completed',
      );

      const before = JobsState();
      final after = before.copyWith(jobs: [a, b]);

      expect(after.jobs, [a, b]);
    });

    test('copyWith with explicit error overrides previous error', () {
      const before = JobsState(error: 'old');
      final after = before.copyWith(error: 'new');

      expect(after.error, 'new');
    });
  });
}
