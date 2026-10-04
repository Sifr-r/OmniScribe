import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/features/jobs/jobs_notifier.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/jobs/job_repository.dart';

class _MockJobRepository extends Mock implements JobRepository {}

JobRecord _makeJob(String id) => JobRecord(
      id: id,
      filename: '$id.pdf',
      model: 'qwen2-vl',
      pipelineMode: 'hybrid',
      durationS: 1.5,
      timestamp: '2026-08-26T10:00:00Z',
      status: 'completed',
    );

void main() {
  late _MockJobRepository repo;

  setUp(() {
    repo = _MockJobRepository();
  });

  ProviderContainer makeContainer() {
    return ProviderContainer(
      overrides: [
        jobRepositoryProvider.overrideWithValue(repo),
      ],
    );
  }

  group('JobsNotifier.build', () {
    test('returns empty JobsState with no error before any method call', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      final state = container.read(jobsProvider);

      expect(state.jobs, isEmpty);
      expect(state.isFetching, isFalse);
      expect(state.error, isNull);
    });
  });

  group('JobsNotifier.fetchJobs', () {
    test('populates state.jobs and clears isFetching on success', () async {
      final jobs = [_makeJob('a'), _makeJob('b')];
      when(() => repo.listJobs()).thenAnswer((_) async => jobs);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      await notifier.fetchJobs();

      final state = container.read(jobsProvider);
      expect(state.jobs, jobs);
      expect(state.isFetching, isFalse);
      expect(state.error, isNull);
      verify(() => repo.listJobs()).called(1);
    });

    test('sets isFetching true while listJobs() is pending', () async {
      final gate = Completer<List<JobRecord>>();
      when(() => repo.listJobs()).thenAnswer((_) => gate.future);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      final fetchFuture = notifier.fetchJobs();

      // While listJobs() is pending the notifier should be in isFetching=true.
      expect(
        container.read(jobsProvider).isFetching,
        isTrue,
        reason: 'should be in-flight while listJobs() is pending',
      );

      gate.complete([_makeJob('a')]);
      await fetchFuture;

      expect(container.read(jobsProvider).isFetching, isFalse);
    });

    test('sets state.error and clears isFetching on failure', () async {
      when(() => repo.listJobs()).thenThrow(Exception('boom'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      await notifier.fetchJobs();

      final state = container.read(jobsProvider);
      expect(state.isFetching, isFalse);
      expect(state.error, contains('boom'));
    });
  });

  group('JobsNotifier.clearJobs', () {
    test('resets jobs to empty and clears isFetching on success', () async {
      when(() => repo.clearJobs()).thenAnswer((_) async => 3);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      await notifier.clearJobs();

      final state = container.read(jobsProvider);
      expect(state.jobs, isEmpty);
      expect(state.isFetching, isFalse);
      verify(() => repo.clearJobs()).called(1);
    });

    test('rethrows on failure and stores error in state', () async {
      when(() => repo.clearJobs()).thenThrow(Exception('clear failed'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      await expectLater(notifier.clearJobs(), throwsA(isA<Exception>()));

      final state = container.read(jobsProvider);
      expect(state.error, contains('clear failed'));
      expect(state.isFetching, isFalse);
    });
  });

  group('JobsNotifier.cancelJob', () {
    test('calls repo.cancelJob then refetches jobs', () async {
      when(() => repo.cancelJob('job-7')).thenAnswer((_) async => true);
      when(() => repo.listJobs()).thenAnswer((_) async => <JobRecord>[]);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      await notifier.cancelJob('job-7');

      verify(() => repo.cancelJob('job-7')).called(1);
      verify(() => repo.listJobs()).called(1);
      expect(container.read(jobsProvider).jobs, isEmpty);
    });

    test('does not refetch jobs when cancelJob throws', () async {
      when(() => repo.cancelJob('job-7')).thenThrow(Exception('cancel failed'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      await expectLater(notifier.cancelJob('job-7'), throwsA(isA<Exception>()));

      final state = container.read(jobsProvider);
      expect(state.error, contains('cancel failed'));
      verifyNever(() => repo.listJobs());
    });
  });

  group('JobsNotifier.downloadResult', () {
    test('returns repo bytes on success', () async {
      final bytes = Uint8List.fromList([0x25, 0x50, 0x44, 0x46]); // %PDF
      when(() => repo.downloadResult('job-1')).thenAnswer((_) async => bytes);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      final result = await notifier.downloadResult('job-1');

      expect(result, bytes);
      final state = container.read(jobsProvider);
      expect(state.error, isNull);
    });

    test('sets state.error and rethrows on failure', () async {
      when(() => repo.downloadResult('job-1'))
          .thenThrow(Exception('download failed'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobsProvider.notifier);

      await expectLater(
        notifier.downloadResult('job-1'),
        throwsA(isA<Exception>()),
      );

      final state = container.read(jobsProvider);
      expect(state.error, contains('download failed'));
    });
  });
}
