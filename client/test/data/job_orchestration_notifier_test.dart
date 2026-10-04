import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/websocket/ws_client.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/core/websocket/ws_frames.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/features/workstation/workstation_state.dart';

class _MockOcrRepository extends Mock implements OcrRepository {}

class _MockWsClient extends Mock implements WsClient {}

class _MockSamplePdfRepository extends Mock implements SamplePdfRepository {}

void main() {
  late _MockOcrRepository ocrRepo;
  late _MockWsClient wsClient;
  late _MockSamplePdfRepository samplePdfRepo;
  late StreamController<WsEnvelope> wsStreamController;
  late StreamController<void> wsClosedController;

  setUpAll(() {
    registerFallbackValue(Uint8List(0));
    registerFallbackValue(const ProcessSettings());
  });

  setUp(() {
    ocrRepo = _MockOcrRepository();
    wsClient = _MockWsClient();
    samplePdfRepo = _MockSamplePdfRepository();
    wsStreamController = StreamController<WsEnvelope>.broadcast();
    wsClosedController = StreamController<void>.broadcast();

    when(() => wsClient.stream).thenAnswer((_) => wsStreamController.stream);
    when(() => wsClient.closedStream)
        .thenAnswer((_) => wsClosedController.stream);
    when(() => wsClient.connect(
          channelId: any(named: 'channelId'),
          sessionToken: any(named: 'sessionToken'),
        )).thenAnswer((_) async {});
    when(() => wsClient.cancelChannel()).thenReturn(null);
    when(() => wsClient.disconnect()).thenAnswer((_) async {});
    when(() => ocrRepo.cancelProgressChannel(any(),
            sessionToken: any(named: 'sessionToken')))
        .thenAnswer((_) async => true);
    when(() => ocrRepo.cancelJob(any())).thenAnswer((_) async => true);
  });

  tearDown(() {
    wsStreamController.close();
    wsClosedController.close();
  });

  ProviderContainer makeContainer() {
    return ProviderContainer(
      overrides: [
        ocrRepositoryProvider.overrideWithValue(ocrRepo),
        wsClientProvider.overrideWithValue(wsClient),
        samplePdfRepositoryProvider.overrideWithValue(samplePdfRepo),
      ],
    );
  }

  group('JobOrchestrationNotifier artifact recovery', () {
    const completeStatus = OcrJobStatusResponse(
      jobId: 'job-1',
      filename: 'doc.pdf',
      status: 'complete',
      createdAt: 0.0,
    );

    setUp(() {
      when(() => ocrRepo.getJobStatus('job-1'))
          .thenAnswer((_) async => completeStatus);
      when(() => ocrRepo
              .downloadProcessedResult('job-1', token: any(named: 'token')))
          .thenAnswer((_) async => ProcessOcrResult(
                pdfBytes: Uint8List.fromList([2]),
                headers: const {},
                textArtifactId: 'artifact-1',
                textArtifactToken: 'token-1',
                documentArtifactId: 'rich-1',
                documentArtifactToken: 'rich-token-1',
              ));
    });

    test('async completion recovers text and reports degraded progress',
        () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);
      notifier.state = JobOrchestrationState(
        isProcessing: true,
        activeJobId: 'job-1',
      );
      when(() => ocrRepo.getTextArtifact('artifact-1', 'token-1'))
          .thenAnswer((_) async => '{"0":"Recovered line"}');

      await notifier.handleWsClosed();

      expect(container.read(jobOrchestrationProvider).stage, 'Complete');
      expect(container.read(jobOrchestrationProvider).documentArtifactId, 'rich-1');
      expect(container.read(jobOrchestrationProvider).documentArtifactToken,
          'rich-token-1');
      expect(container.read(jobOrchestrationProvider).statusMessage,
          contains('recovered text from artifact'));
      expect(container.read(workstationProvider).allBBoxes.single.text,
          'Recovered line');
    });

    test('async completion skips artifact arriving for another job', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);
      notifier.state = JobOrchestrationState(
        isProcessing: true,
        activeJobId: 'job-1',
      );
      final artifact = Completer<String>();
      when(() => ocrRepo.getTextArtifact('artifact-1', 'token-1'))
          .thenAnswer((_) => artifact.future);

      final completion = notifier.handleWsClosed();
      await untilCalled(() => ocrRepo.getTextArtifact('artifact-1', 'token-1'));
      notifier.state = notifier.state.copyWith(activeJobId: 'job-2');
      artifact.complete('{"0":"Stale line"}');
      await completion;

      expect(container.read(workstationProvider).allBBoxes, isEmpty);
      expect(container.read(jobOrchestrationProvider).statusMessage,
          isNot(contains('recovered text from artifact')));
    });
  });

  group('JobOrchestrationNotifier.cancelOcr cancellation safety', () {
    test('server declining cancellation is reported as a warning', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);
      when(() => ocrRepo.cancelJob('declined-job'))
          .thenAnswer((_) async => false);
      notifier.state = JobOrchestrationState(
        isProcessing: true,
        activeJobId: 'declined-job',
      );

      await notifier.cancelOcr();

      expect(notifier.state.isProcessing, isFalse);
      expect(notifier.state.warnings,
          contains('Failed to cancel active OCR job/channel on server'));
    });

    test('late cancellation cannot mutate a replacement job', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);
      final cancelled = Completer<bool>();
      when(() => ocrRepo.cancelProgressChannel('old-channel',
              sessionToken: any(named: 'sessionToken')))
          .thenAnswer((_) => cancelled.future);
      notifier.state = JobOrchestrationState(
        isProcessing: true,
        channelId: 'old-channel',
        activeJobId: 'old-job',
      );

      final cancelling = notifier.cancelOcr();
      expect(notifier.state.isProcessing, isFalse);
      notifier.reset();
      notifier.state = JobOrchestrationState(
        isProcessing: true,
        channelId: 'new-channel',
        activeJobId: 'new-job',
        stage: 'Queued',
      );
      cancelled.complete(true);
      await cancelling;

      expect(notifier.state.isProcessing, isTrue);
      expect(notifier.state.activeJobId, 'new-job');
      expect(notifier.state.stage, 'Queued');
      verifyNever(() => ocrRepo.cancelJob('new-job'));
      verifyNever(() => ocrRepo.cancelProgressChannel('new-channel',
          sessionToken: any(named: 'sessionToken')));
    });

    test('cancelOcr is a no-op when isProcessing is false', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);

      await notifier.cancelOcr();

      final state = container.read(jobOrchestrationProvider);
      expect(state.isProcessing, isFalse);
      expect(state.stage, 'Idle');
      verifyNever(() => wsClient.cancelChannel());
    });

    test('cancelOcr succeeds cleanly when server calls succeed', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);

      notifier.state = JobOrchestrationState(
        channelId: 'ch-success',
        activeJobId: 'job-success',
        isProcessing: true,
        stage: 'Conversion',
      );

      await notifier.cancelOcr();

      final state = container.read(jobOrchestrationProvider);
      expect(state.isProcessing, isFalse);
      expect(state.stage, 'Cancelled');
      expect(state.statusMessage, 'Cancelled by user');
      expect(state.warnings, isEmpty);
      verify(() => wsClient.cancelChannel()).called(1);
      verify(() => ocrRepo.cancelProgressChannel('ch-success',
          sessionToken: any(named: 'sessionToken'))).called(1);
      verify(() => ocrRepo.cancelJob('job-success')).called(1);
    });

    test(
        'cancelOcr proceeds to local cancellation and records warning when all server cancellations fail',
        () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);

      when(() => ocrRepo.cancelProgressChannel('ch-fail',
              sessionToken: any(named: 'sessionToken')))
          .thenThrow(Exception('Channel cancel failed'));
      when(() => ocrRepo.cancelJob('job-fail'))
          .thenThrow(Exception('Job cancel failed'));

      notifier.state = JobOrchestrationState(
        channelId: 'ch-fail',
        activeJobId: 'job-fail',
        isProcessing: true,
        stage: 'Detection',
        statusMessage: 'Processing blocks...',
      );

      await notifier.cancelOcr();

      final state = container.read(jobOrchestrationProvider);
      // Safety contract: UI must never remain stuck in isProcessing: true
      expect(state.isProcessing, isFalse);
      expect(state.stage, 'Cancelled');
      expect(state.statusMessage, 'Cancelled by user');
      expect(
        state.warnings,
        contains('Failed to cancel active OCR job/channel on server'),
      );
      verify(() => wsClient.cancelChannel()).called(1);
    });

    test(
        'cancelOcr proceeds to local cancellation and records partial warning when only one cancellation fails',
        () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(jobOrchestrationProvider.notifier);

      when(() => ocrRepo.cancelProgressChannel('ch-ok',
              sessionToken: any(named: 'sessionToken')))
          .thenAnswer((_) async => true);
      when(() => ocrRepo.cancelJob('job-err'))
          .thenThrow(Exception('Job cancel network failure'));

      notifier.state = JobOrchestrationState(
        channelId: 'ch-ok',
        activeJobId: 'job-err',
        isProcessing: true,
        stage: 'OCR',
      );

      await notifier.cancelOcr();

      final state = container.read(jobOrchestrationProvider);
      expect(state.isProcessing, isFalse);
      expect(state.stage, 'Cancelled');
      expect(state.statusMessage, 'Cancelled by user');
      expect(
        state.warnings,
        contains('Partial server cancellation failure'),
      );
      expect(
        state.warnings,
        isNot(contains('Failed to cancel active OCR job/channel on server')),
      );
    });
  });

  group('WorkstationState Performance & Byte Equality', () {
    test(
        'operator == compares loadedBytes by identity without scanning byte content',
        () {
      final bytesA = Uint8List.fromList([1, 2, 3, 4]);
      final bytesB =
          Uint8List.fromList([9, 8, 7, 6]); // Different bytes, same length
      final bytesC = Uint8List.fromList([1, 2, 3]); // Different length

      final stateA = WorkstationState(
        loadedBytes: bytesA,
        filename: 'doc.pdf',
        pageCount: 1,
      );
      final stateB = WorkstationState(
        loadedBytes: bytesB,
        filename: 'doc.pdf',
        pageCount: 1,
      );
      final stateC = WorkstationState(
        loadedBytes: bytesC,
        filename: 'doc.pdf',
        pageCount: 1,
      );

      // Different buffers remain distinct even when their sizes match.
      expect(stateA == stateB, isFalse);

      // Different length => not equal
      expect(stateA == stateC, isFalse);

      // Identical reference => equal
      final stateA2 = WorkstationState(
        loadedBytes: bytesA,
        filename: 'doc.pdf',
        pageCount: 1,
      );
      expect(stateA == stateA2, isTrue);
    });

    test('same buffer retains a consistent hash without deep byte scanning', () {
      final multiMb1 = Uint8List(1024 * 1024);

      final state1 = WorkstationState(loadedBytes: multiMb1);
      final state2 = WorkstationState(loadedBytes: multiMb1);

      // Reusing the same buffer preserves identity and hash consistency.
      expect(state1.hashCode, equals(state2.hashCode));
      expect(state1, equals(state2));
    });
  });
}
