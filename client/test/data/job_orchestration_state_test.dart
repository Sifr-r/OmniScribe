// JobOrchestrationState's constructor intentionally wraps collection arguments
// with `List.unmodifiable` / `Map.unmodifiable` to enforce the immutability
// contract proven by the tests in the `Immutability (unmodifiable getters)`
// group below. That defensive copy means the constructor cannot be `const`,
// which trips `prefer_const_constructors` and `prefer_const_literals_to_
// create_immutables` for every test instantiation in this file. Silencing
// these two lints here is intentional and scoped.
// ignore_for_file: prefer_const_constructors, prefer_const_literals_to_create_immutables

import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/data/models/document_result.dart';
import 'package:omniscribe_client/data/providers/job_orchestration_notifier.dart';

void main() {
  group('JobOrchestrationState Construction & Defaults', () {
    test('default constructor provides sane initial defaults', () {
      final state = JobOrchestrationState();

      expect(state.isProcessing, isFalse);
      expect(state.activeJobId, isNull);
      expect(state.channelId, isNull);
      expect(state.percent, 0);
      expect(state.stage, 'Idle');
      expect(state.statusMessage, '');
      expect(state.warnings, isEmpty);
      expect(state.blockRetryCounts, isEmpty);
      expect(state.qualitySummary, isNull);
      expect(state.avgConfidence, isNull);
      expect(state.processedBlocks, 0);
      expect(state.totalBlocks, 0);
      expect(state.scoredBlocks, 0);
      expect(state.trustSummary, isNull);
      expect(state.error, isNull);
      expect(state.textArtifactId, isNull);
      expect(state.textArtifactToken, isNull);
      expect(state.effectiveJobId, isNull);
    });
  });

  group('JobOrchestrationState Getters', () {
    test('totalRetriesAttempted folds retry counts', () {
      final state = JobOrchestrationState(
        blockRetryCounts: {'p0_b0': 2, 'p0_b1': 1},
      );
      expect(state.totalRetriesAttempted, 3);
    });

    test('repairedCount prefers the quality summary over the document count', () {
      final withSummary = JobOrchestrationState(
        qualitySummary: const QualitySummary(
          scope: 'document',
          target: 0.85,
          avgConfidence: 0.92,
          repairedCount: 3,
          belowTargetCount: 1,
        ),
      );
      expect(withSummary.repairedCount(7), 3);

      final withoutSummary = JobOrchestrationState();
      expect(withoutSummary.repairedCount(7), 7);
    });

    test('percentInt mirrors percent', () {
      expect(JobOrchestrationState(percent: 42).percentInt, 42);
    });

    test('currentStageIndex maps stage name correctly', () {
      expect(JobOrchestrationState(stage: 'Conversion').currentStageIndex, 0);
      expect(JobOrchestrationState(stage: 'Detection').currentStageIndex, 1);
      expect(JobOrchestrationState(stage: 'OCR').currentStageIndex, 2);
      expect(
        JobOrchestrationState(stage: 'Refine / Quality Repair')
            .currentStageIndex,
        3,
      );
      expect(JobOrchestrationState(stage: 'Postprocess').currentStageIndex, 4);
      expect(JobOrchestrationState(stage: 'Embedding').currentStageIndex, 5);
    });

    test('currentStageIndex returns -1 for non-pipeline stages', () {
      // Sentinel states outside the pipelineStages list should return -1
      expect(JobOrchestrationState(stage: 'Idle').currentStageIndex, -1);
      expect(JobOrchestrationState(stage: 'Complete').currentStageIndex, -1);
      expect(JobOrchestrationState(stage: 'Warning').currentStageIndex, -1);
      expect(JobOrchestrationState(stage: 'Error').currentStageIndex, -1);
      expect(JobOrchestrationState(stage: 'Cancelled').currentStageIndex, -1);
      expect(JobOrchestrationState(stage: 'UnknownStage').currentStageIndex, -1);
    });

    test('effectiveJobId prefers the active job over the last submitted', () {
      expect(
        JobOrchestrationState(
          activeJobId: 'job-active',
          lastSubmittedJobId: 'job-old',
        ).effectiveJobId,
        'job-active',
      );
      expect(
        JobOrchestrationState(lastSubmittedJobId: 'job-old').effectiveJobId,
        'job-old',
      );
      expect(
        JobOrchestrationState(activeJobId: '').effectiveJobId,
        isNull,
      );
    });
  });

  group('JobOrchestrationState Immutability (unmodifiable getters)', () {
    test('warnings getter returns an unmodifiable list', () {
      final state = JobOrchestrationState(warnings: ['warn-1', 'warn-2']);

      expect(state.warnings.length, 2);
      expect(state.warnings.first, 'warn-1');

      expect(
        () => state.warnings.add('warn-3'),
        throwsUnsupportedError,
      );
      expect(
        () => state.warnings.clear(),
        throwsUnsupportedError,
      );
    });

    test('blockRetryCounts getter returns an unmodifiable map', () {
      final state = JobOrchestrationState(blockRetryCounts: {'p0_b0': 2});

      expect(state.blockRetryCounts['p0_b0'], 2);

      expect(
        () => state.blockRetryCounts['p1_b1'] = 1,
        throwsUnsupportedError,
      );
      expect(
        () => state.blockRetryCounts.remove('p0_b0'),
        throwsUnsupportedError,
      );
      expect(
        () => state.blockRetryCounts.clear(),
        throwsUnsupportedError,
      );
    });

    test('copyWith produces unmodifiable collections', () {
      final original = JobOrchestrationState();

      // Pass new mutable collections into copyWith; they must be wrapped.
      final next = original.copyWith(
        warnings: ['warn-2'],
        blockRetryCounts: {'p1_b0': 3},
      );

      expect(() => next.warnings.add('warn-3'), throwsUnsupportedError);
      expect(
        () => next.blockRetryCounts['k'] = 0,
        throwsUnsupportedError,
      );
    });
  });

  group('JobOrchestrationState copyWith & Clear Flags', () {
    test('preserves untouched fields and overwrites specified values', () {
      final initial = JobOrchestrationState(
        percent: 50,
        stage: 'OCR',
      );

      final updated = initial.copyWith(
        percent: 75,
        stage: 'Postprocess',
      );

      expect(updated.percent, 75);
      expect(updated.stage, 'Postprocess');
    });

    test('clear flags explicitly reset nullable fields to null', () {
      final initial = JobOrchestrationState(
        activeJobId: 'job-1',
        channelId: 'ch-1',
        qualitySummary: const QualitySummary(
          scope: 'document',
          target: 0.85,
          avgConfidence: 0.9,
          repairedCount: 1,
          belowTargetCount: 0,
        ),
        avgConfidence: 0.85,
        trustSummary: const TrustSummary(
          blockCount: 10,
          scoredCount: 10,
          flaggedCount: 0,
          average: 0.95,
        ),
        error: 'Some error',
        textArtifactId: 'art-1',
        textArtifactToken: 'tok-1',
      );

      final cleared = initial.copyWith(
        clearActiveJobId: true,
        clearChannelId: true,
        clearQualitySummary: true,
        clearAvgConfidence: true,
        clearTrustSummary: true,
        clearError: true,
        clearTextArtifactId: true,
        clearTextArtifactToken: true,
      );

      expect(cleared.activeJobId, isNull);
      expect(cleared.channelId, isNull);
      expect(cleared.qualitySummary, isNull);
      expect(cleared.avgConfidence, isNull);
      expect(cleared.trustSummary, isNull);
      expect(cleared.error, isNull);
      expect(cleared.textArtifactId, isNull);
      expect(cleared.textArtifactToken, isNull);
    });
  });

  group('JobOrchestrationState Equality & HashCode', () {
    test('instances with identical fields are equal and have matching hashCodes', () {
      final s1 = JobOrchestrationState(
        stage: 'OCR',
        warnings: const ['warn1'],
        blockRetryCounts: const {'p0_b0': 1},
      );

      final s2 = JobOrchestrationState(
        stage: 'OCR',
        warnings: const ['warn1'],
        blockRetryCounts: const {'p0_b0': 1},
      );

      expect(s1, equals(s2));
      expect(s1.hashCode, equals(s2.hashCode));
    });

    test('instances with different fields are not equal', () {
      final s1 = JobOrchestrationState(percent: 10);
      final s2 = JobOrchestrationState(percent: 20);

      expect(s1, isNot(equals(s2)));
    });

    test(
        'instances with equal blockRetryCounts but different insertion order are equal and have equal hashCodes',
        () {
      // Same entries, different insertion order
      final s1 = JobOrchestrationState(
        blockRetryCounts: {'p0_b0': 1, 'p0_b1': 2, 'p0_b2': 3},
      );
      final s2 = JobOrchestrationState(
        blockRetryCounts: {'p0_b2': 3, 'p0_b0': 1, 'p0_b1': 2},
      );

      expect(s1, equals(s2));
      expect(s1.hashCode, equals(s2.hashCode));

      // Mutating the original input map after construction must not affect equality
      final baseMap = <String, int>{'p0_b0': 1, 'p0_b1': 2};
      final s3 = JobOrchestrationState(blockRetryCounts: baseMap);
      baseMap['p0_b2'] = 3;
      final s4 = JobOrchestrationState(blockRetryCounts: {'p0_b0': 1, 'p0_b1': 2});
      expect(s3, equals(s4));
      expect(s3.hashCode, equals(s4.hashCode));
    });
  });
}
