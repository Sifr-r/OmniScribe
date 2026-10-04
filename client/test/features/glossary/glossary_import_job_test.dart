import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/features/glossary/glossary_screen.dart';
import 'package:omniscribe_client/features/glossary/glossary_models.dart';
import 'package:omniscribe_client/features/glossary/glossary_notifier.dart';
import 'package:omniscribe_client/features/glossary/glossary_repository.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';

/// Hand-written fake: gives explicit control over the queued-import job
/// sequence and records exactly which refresh calls happened.
class _FakeGlossaryRepository implements GlossaryRepository {
  _FakeGlossaryRepository({
    required this.importResult,
    List<GlossaryJobStatus>? statusSequence,
    this.statusFailures = 0,
    this.statusNeverCompletes = false,
  }) : statusSequence = statusSequence ?? const [];

  final GlossaryImportJobResponse importResult;
  final List<GlossaryJobStatus> statusSequence;
  int statusFailures;
  final bool statusNeverCompletes;

  int statusPolls = 0;
  int libraryLoads = 0;
  int lexiconLoads = 0;

  @override
  Future<GlossaryJobStatus> getImportJobStatus(String jobId) async {
    if (statusNeverCompletes) return Completer<GlossaryJobStatus>().future;
    if (statusFailures > 0) {
      statusFailures--;
      throw StateError('temporary network failure');
    }
    final index = statusPolls < statusSequence.length
        ? statusPolls
        : statusSequence.length - 1;
    statusPolls += 1;
    return statusSequence[index];
  }

  @override
  Future<List<GlossaryListItem>> getGlossaryLibraries() async {
    libraryLoads += 1;
    return [_library('lib-1')];
  }

  @override
  Future<List<GlossaryEntry>> getMergedGlossaryEntries() async {
    lexiconLoads += 1;
    return const <GlossaryEntry>[];
  }

  @override
  Future<GlossaryImportJobResponse> importGlossaryFile({
    required Uint8List fileBytes,
    required String filename,
    String? channelId,
  }) async =>
      importResult;

  @override
  Future<GlossaryImportJobResponse> importGlossaryUrl({
    required String url,
    required GlossaryFormat format,
    String? name,
    String? channelId,
  }) async =>
      importResult;

  @override
  Future<List<GlossaryEntry>> getGlossaryEntries(String libraryId) async =>
      const <GlossaryEntry>[];

  @override
  Future<GlossaryPreviewResponse> getGlossaryPreview() async =>
      throw UnimplementedError();

  @override
  Future<bool> toggleGlossaryLibrary(String libraryId, bool enabled) async =>
      true;

  @override
  Future<bool> deleteGlossaryLibrary(String libraryId) async => true;

  @override
  Future<bool> reorderGlossaryLibraries(List<String> orderedIds) async => true;
}

class _MockApiClient extends Mock implements ApiClient {}

GlossaryListItem _library(String id) => GlossaryListItem(
      id: id,
      name: 'Terminology $id',
      format: GlossaryFormat.csv,
      entryCount: 3,
      enabled: true,
      priority: 0,
      group: 'default',
    );

GlossaryImportJobResponse _queued(String jobId) => GlossaryImportJobResponse(
      format: GlossaryFormat.csv,
      name: 'late.csv',
      entryCount: 0,
      jobId: jobId,
      queued: true,
    );

GlossaryImportJobResponse _inline() => const GlossaryImportJobResponse(
      format: GlossaryFormat.csv,
      name: 'now.csv',
      entryCount: 2,
      glossaryId: 'lib-1',
      queued: false,
    );

void main() {
  ProviderContainer makeContainer(_FakeGlossaryRepository repo) {
    final container = ProviderContainer(
      overrides: [glossaryRepositoryProvider.overrideWithValue(repo)],
    );
    addTearDown(container.dispose);
    return container;
  }

  test('real nullable status envelopes parse and malformed wire fields fail', () async {
    final api = _MockApiClient();
    final repo = GlossaryRepository(api);
    for (final status in ['pending', 'processing', 'complete', 'error', 'cancelled']) {
      when(() => api.get<Map<String, dynamic>>(ApiConstants.processStatus('job')))
          .thenAnswer((_) async => {'job_id': 'job', 'status': status, 'error': null});
      final parsed = await repo.getImportJobStatus('job');
      expect(parsed.status, status);
      expect(parsed.error, isNull);
    }
    expect(GlossaryJobStatus.fromJson({'status': 'complete'}).succeeded, isTrue);
    for (final malformed in <Map<String, dynamic>>[{'status': 'surprise'},
      {'status': 'complete', 'error': 17}, {'error': null}]) {
      expect(() => GlossaryJobStatus.fromJson(malformed), throwsFormatException);
    }
  });

  test('transient polling failures retry without treating the import as failed', () async {
    final repo = _FakeGlossaryRepository(importResult: _queued('retry'),
        statusFailures: 2, statusSequence: const [GlossaryJobStatus(status: 'complete')]);
    final container = makeContainer(repo);
    await container.read(glossaryProvider.notifier).followImportJob(_queued('retry'),
        pollInterval: Duration.zero);
    expect(container.read(glossaryProvider).error, isNull);
    expect(repo.libraryLoads, 1);
  });

  test('persistent polling failure reports a still-running job, without refreshing', () async {
    final repo = _FakeGlossaryRepository(importResult: _queued('unreachable'), statusFailures: 3);
    final container = makeContainer(repo);
    await container.read(glossaryProvider.notifier).followImportJob(_queued('unreachable'),
        pollInterval: Duration.zero);
    expect(container.read(glossaryProvider).error, contains('may still be running'));
    expect(repo.libraryLoads, 0);
  });

  test('a hanging status request respects the total import deadline', () async {
    final repo = _FakeGlossaryRepository(importResult: _queued('hanging'), statusNeverCompletes: true);
    final container = makeContainer(repo);
    await container.read(glossaryProvider.notifier).followImportJob(_queued('hanging'),
        pollInterval: Duration.zero, timeout: const Duration(milliseconds: 20));
    expect(container.read(glossaryProvider).error, contains('still running'));
    expect(container.read(glossaryProvider).isLoading, isFalse);
    expect(repo.libraryLoads, 0);
  });

  testWidgets('queued URL worker errors keep the import modal open', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final repo = _FakeGlossaryRepository(importResult: _queued('failed-url'),
        statusSequence: const [GlossaryJobStatus(status: 'error', error: 'invalid glossary')]);
    await tester.pumpWidget(ProviderScope(
      overrides: [glossaryRepositoryProvider.overrideWithValue(repo)],
      child: MaterialApp(theme: AppTheme.darkTheme, home: const GlossaryScreen()),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Import glossary'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'https://example.com/bad.csv');
    await tester.tap(find.text('Import Glossary').last);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.text('Import Terminology Glossary'), findsOneWidget);
    expect(find.textContaining('invalid glossary'), findsWidgets);
  });

  test('queued import refreshes libraries only after the job completes',
      () async {
    final repo = _FakeGlossaryRepository(
      importResult: _queued('job-1'),
      statusSequence: const [
        GlossaryJobStatus(status: 'processing'),
        GlossaryJobStatus(status: 'complete'),
      ],
    );
    final container = makeContainer(repo);

    final res = await container.read(glossaryProvider.notifier).importGlossaryUrl(
          url: 'https://example.com/late.csv',
          format: GlossaryFormat.csv,
        );

    expect(res.queued, isTrue);
    expect(
      repo.statusPolls,
      2,
      reason: 'must follow the job handle, not refresh blindly',
    );
    final state = container.read(glossaryProvider);
    expect(state.error, isNull);
    expect(state.isLoading, isFalse);
    expect(state.libraries.map((l) => l.id), contains('lib-1'));
    expect(repo.libraryLoads, 1);
    expect(repo.lexiconLoads, 1);
  });

  test('queued import reports a worker failure instead of refreshing', () async {
    final repo = _FakeGlossaryRepository(
      importResult: _queued('job-2'),
      statusSequence: const [
        GlossaryJobStatus(status: 'error', error: 'malformed CSV at line 4'),
      ],
    );
    final container = makeContainer(repo);

    await container.read(glossaryProvider.notifier).importGlossaryFile(
          fileBytes: Uint8List.fromList([1, 2, 3]),
          filename: 'bad.csv',
        );

    final state = container.read(glossaryProvider);
    expect(state.error, contains('job-2'));
    expect(state.error, contains('malformed CSV at line 4'));
    expect(state.isLoading, isFalse);
    // A failed import must not present a refreshed library list as success.
    expect(repo.libraryLoads, 0);
    expect(repo.lexiconLoads, 0);
  });

  test('cancelled import is surfaced as a failure', () async {
    final repo = _FakeGlossaryRepository(
      importResult: _queued('job-3'),
      statusSequence: const [GlossaryJobStatus(status: 'cancelled')],
    );
    final container = makeContainer(repo);

    await container.read(glossaryProvider.notifier).importGlossaryFile(
          fileBytes: Uint8List.fromList([1]),
          filename: 'x.csv',
        );

    expect(container.read(glossaryProvider).error, contains('cancelled'));
    expect(repo.libraryLoads, 0);
  });

  test('synchronous import refreshes immediately without polling', () async {
    final repo = _FakeGlossaryRepository(importResult: _inline());
    final container = makeContainer(repo);

    await container.read(glossaryProvider.notifier).importGlossaryFile(
          fileBytes: Uint8List.fromList([1]),
          filename: 'now.csv',
        );

    expect(repo.statusPolls, 0, reason: 'nothing to follow for an inline import');
    final state = container.read(glossaryProvider);
    expect(state.error, isNull);
    expect(state.libraries.map((l) => l.id), contains('lib-1'));
    expect(repo.libraryLoads, 1);
  });

  test('importGlossaryJson also follows a queued job', () async {
    final repo = _FakeGlossaryRepository(
      importResult: _queued('job-4'),
      statusSequence: const [
        GlossaryJobStatus(status: 'error', error: 'boom'),
      ],
    );
    final container = makeContainer(repo);

    await container
        .read(glossaryProvider.notifier)
        .importGlossaryJson(format: GlossaryFormat.jsonPairs, text: '{}');

    expect(container.read(glossaryProvider).error, contains('boom'));
    expect(repo.statusPolls, 1);
  });
}
