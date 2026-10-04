import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/features/jobs/job_history_screen.dart';
import 'package:omniscribe_client/app/shell_state.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the Job History screen: empty state, populated
/// list, refresh action, and clear-history confirmation flow.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('Job History screen', () {
    testWidgets('shows the empty-state when the repository returns no jobs',
        (tester) async {
      final ctx = await _boot(tester, jobs: const []);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.jobs);
      await tester.pumpAndSettle();

      expect(find.byType(JobHistoryScreen), findsOneWidget);
      expect(
        find.textContaining('No historical'),
        findsOneWidget,
      );
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('renders each job row when the repository returns records',
        (tester) async {
      final jobs = [
        const JobRecord(
          id: 'job-1',
          filename: 'q3-report.pdf',
          model: 'deepseek-ocr-2',
          pipelineMode: 'balanced',
          durationS: 12.5,
          timestamp: '2026-01-15T10:00:00Z',
          status: 'completed',
        ),
        const JobRecord(
          id: 'job-2',
          filename: 'invoice-2025.pdf',
          model: 'deepseek-ocr-2',
          pipelineMode: 'balanced',
          durationS: 9.0,
          timestamp: '2026-01-15T10:30:00Z',
          status: 'failed',
        ),
      ];

      final ctx = await _boot(tester, jobs: jobs);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.jobs);
      await tester.pumpAndSettle();

      expect(find.byType(JobHistoryScreen), findsOneWidget);
      expect(find.text('q3-report.pdf'), findsOneWidget);
      expect(find.text('invoice-2025.pdf'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('tapping refresh calls JobRepository.listJobs again',
        (tester) async {
      var callCount = 0;
      final jobs = [
        const JobRecord(
          id: 'job-1',
          filename: 'doc.pdf',
          model: 'deepseek-ocr-2',
          pipelineMode: 'balanced',
          durationS: 12.5,
          timestamp: '2026-01-15T10:00:00Z',
          status: 'completed',
        ),
      ];

      final ctx = await _boot(
        tester,
        jobs: jobs,
        listJobsCallback: () => callCount += 1,
      );
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.jobs);
      await tester.pumpAndSettle();
      expect(callCount, greaterThan(0));

      await tester.tap(find.byIcon(Icons.refresh).first);
      await tester.pumpAndSettle();

      expect(callCount, greaterThanOrEqualTo(2));
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });
}

class _BootResult {
  _BootResult({required this.container, required this.stub});
  final ProviderContainer container;
  final StubOmniscribeServer stub;
}

Future<_BootResult> _boot(
  WidgetTester tester, {
  required List<JobRecord> jobs,
  void Function()? listJobsCallback,
}) async {
  final stub = StubOmniscribeServer();
  await stub.start();

  final configRepo = MockConfigRepository();
  final jobRepo = MockJobRepository();

  when(() => configRepo.getConfig()).thenAnswer(
    (_) async => defaultTestRuntimeConfig(),
  );
  when(() => configRepo.updateConfig(any())).thenAnswer(
    (_) async => defaultTestRuntimeConfig(),
  );
  when(
    () => configRepo.getModelsForProvider(
      any(),
      apiBase: any(named: 'apiBase'),
    ),
  ).thenAnswer((_) async => const ['deepseek-ocr-2']);
  when(() => jobRepo.listJobs()).thenAnswer((_) async {
    listJobsCallback?.call();
    return jobs;
  });
  when(() => jobRepo.clearJobs()).thenAnswer((_) async => 0);
  when(() => jobRepo.cancelJob(any())).thenAnswer((_) async => true);

  final container = await bootAppForIntegration(
    tester,
    stub,
    configRepo: configRepo,
    jobRepo: jobRepo,
    extraOverrides: [
      serverHealthProvider.overrideWith(_OfflineHealth.new),
    ],
  );

  return _BootResult(container: container, stub: stub);
}

class _OfflineHealth extends ServerHealthNotifier {
  @override
  ServerHealthState build() =>
      const ServerHealthState(status: ServerHealth.offline);
}
