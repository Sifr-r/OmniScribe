import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/features/jobs/job_history_screen.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/features/jobs/job_repository.dart';
import 'package:omniscribe_client/features/jobs/jobs_notifier.dart';

class _MockJobRepository extends Mock implements JobRepository {}

const _job = JobRecord(
  id: 'job-1',
  filename: 'invoice.pdf',
  model: 'ocr',
  pipelineMode: 'balanced',
  durationS: 1,
  timestamp: '2026-10-04T10:00:00Z',
  status: 'completed',
);

void main() {
  late _MockJobRepository repository;

  setUp(() {
    repository = _MockJobRepository();
    when(() => repository.listJobs()).thenAnswer((_) async => [_job]);
    when(() => repository.clearJobs()).thenAnswer((_) async => 1);
  });

  Future<ProviderContainer> boot(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(ProviderScope(
      overrides: [jobRepositoryProvider.overrideWithValue(repository)],
      child: MaterialApp(
        theme: AppTheme.darkTheme,
        home: const JobHistoryScreen(),
      ),
    ));
    await tester.pumpAndSettle();
    return ProviderScope.containerOf(
      tester.element(find.byType(JobHistoryScreen)),
    );
  }

  testWidgets('refresh replaces loaded rows with the repository empty state',
      (tester) async {
    final container = await boot(tester);
    expect(find.text('invoice.pdf'), findsOneWidget);
    when(() => repository.listJobs()).thenAnswer((_) async => []);

    await tester.tap(find.text('Refresh'));
    await tester.pumpAndSettle();

    expect(find.text('invoice.pdf'), findsNothing);
    expect(find.text('No historical OCR or translation jobs found.'),
        findsOneWidget);
    expect(container.read(jobsProvider).jobs, isEmpty);
    verify(() => repository.listJobs()).called(2);
  });

  testWidgets('clear requires confirmation and cancellation preserves rows',
      (tester) async {
    await boot(tester);
    await tester.tap(find.text('Clear all'));
    await tester.pumpAndSettle();
    expect(find.text('Clear All Job History?'), findsOneWidget);
    verifyNever(() => repository.clearJobs());

    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(find.text('invoice.pdf'), findsOneWidget);
    verifyNever(() => repository.clearJobs());

    await tester.tap(find.text('Clear all'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Clear all jobs'));
    await tester.pumpAndSettle();

    verify(() => repository.clearJobs()).called(1);
    expect(find.text('invoice.pdf'), findsNothing);
    expect(find.text('Job execution history cleared.'), findsOneWidget);
  });

  testWidgets('failed clear retains history and reports the server failure',
      (tester) async {
    await boot(tester);
    when(() => repository.clearJobs())
        .thenThrow(StateError('history service unavailable'));
    await tester.tap(find.text('Clear all'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Clear all jobs'));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('invoice.pdf'), findsOneWidget);
    expect(find.textContaining('history service unavailable'), findsOneWidget);
    expect(find.text('Job execution history cleared.'), findsNothing);

    await tester.tap(find.text('Refresh'));
    await tester.pumpAndSettle();
    expect(find.textContaining('history service unavailable'), findsNothing);
    expect(find.text('invoice.pdf'), findsOneWidget);
  });

  testWidgets('initial fetch failure is visible and refresh recovers',
      (tester) async {
    when(() => repository.listJobs()).thenThrow(StateError('connection lost'));
    await boot(tester);
    expect(find.textContaining('Job history error:'), findsOneWidget);
    expect(find.textContaining('connection lost'), findsOneWidget);

    when(() => repository.listJobs()).thenAnswer((_) async => [_job]);
    await tester.tap(find.text('Refresh'));
    await tester.pumpAndSettle();

    expect(find.text('invoice.pdf'), findsOneWidget);
    expect(find.textContaining('connection lost'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
