import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/core/websocket/ws_frames.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';

/// A two-page text artifact: one non-empty line per page.
const _twoPageArtifact = <String, dynamic>{
  '0': 'PAGE-ONE-LINE-A\nPAGE-ONE-LINE-B',
  '1': 'PAGE-TWO-LINE-A',
};

class _FakeOcrRepository implements OcrRepository {
  _FakeOcrRepository({
    this.artifacts = const <String, String>{},
    this.syncResult,
    this.gate,
  });

  /// Raw text-artifact payloads keyed by artifact id.
  final Map<String, String> artifacts;

  /// Result handed back by [processOcrSync] when set.
  final ProcessOcrResult? syncResult;

  /// When set, [getTextArtifact] blocks on this completer before answering.
  final Completer<String>? gate;

  @override
  Future<String> getTextArtifact(String artifactId, String token) {
    final gate = this.gate;
    if (gate != null) return gate.future;
    final payload = artifacts[artifactId];
    if (payload == null) {
      return Future<String>.error(StateError('unknown artifact $artifactId'));
    }
    return Future<String>.value(payload);
  }

  @override
  Future<ProcessOcrResult> processOcrSync({
    required Uint8List fileBytes,
    required String filename,
    ProcessSettings? settings,
    String? progressChannel,
    String? progressToken,
    void Function(int sent, int total)? onSendProgress,
    Duration? receiveTimeout,
  }) async {
    final result = syncResult;
    if (result == null) {
      throw StateError('processOcrSync was not configured for this test');
    }
    return result;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

ProcessOcrResult _resultWithArtifact(String artifactId, String token) =>
    ProcessOcrResult(
      pdfBytes: Uint8List.fromList([7, 7, 7, 7]),
      headers: const <String, String>{},
      textArtifactId: artifactId,
      textArtifactToken: token,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  ProviderContainer makeContainer(_FakeOcrRepository repo) {
    final container = ProviderContainer(
      overrides: [ocrRepositoryProvider.overrideWithValue(repo)],
    );
    addTearDown(container.dispose);
    return container;
  }

  group('hydratePagesFromTextArtifact merge', () {
    test('recovers every page when no live frames arrived', () {
      final container =
          makeContainer(_FakeOcrRepository(artifacts: const {}));
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 2,
      );
      expect(container.read(workstationProvider).allBBoxes, isEmpty);

      final written =
          notifier.hydratePagesFromTextArtifact(_twoPageArtifact);

      // 2 lines on page 0 + 1 line on page 1.
      expect(written, 3);
      final state = container.read(workstationProvider);
      expect(state.pageCount, 2);
      expect(state.allBBoxes, hasLength(3));
      expect(
        state.allBBoxes.map((b) => b.text).toList(),
        ['PAGE-ONE-LINE-A', 'PAGE-ONE-LINE-B', 'PAGE-TWO-LINE-A'],
      );
      // Page indices survive the merge.
      expect(state.allBBoxes.map((b) => b.page).toList(), [0, 0, 1]);
    });

    test('keeps live page geometry and hydrates only the missing page', () {
      final container = makeContainer(_FakeOcrRepository());
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 2,
      );
      // Page 0 arrived live with real coordinates; page 1 never arrived.
      notifier.addOrUpdateBBox(
        0,
        const BBoxItem(
          blockId: 'p0_b0',
          page: 0,
          block: 0,
          bbox: [0.05, 0.05, 0.95, 0.15],
          text: 'real-WS-text',
          kind: 'heading',
        ),
      );

      final written =
          notifier.hydratePagesFromTextArtifact(_twoPageArtifact);

      // Only the one line belonging to the missing page is written.
      expect(written, 1);
      final state = container.read(workstationProvider);

      // Page 0 is untouched: same block count, text, geometry and kind.
      final livePage = state.pages[0].bboxes;
      expect(livePage, hasLength(1));
      expect(livePage.single.blockId, 'p0_b0');
      expect(livePage.single.text, 'real-WS-text');
      expect(livePage.single.bbox, [0.05, 0.05, 0.95, 0.15]);
      expect(livePage.single.kind, 'heading');
      expect(livePage.single.label, isNull);

      // Page 1 is filled from the artifact with placeholder geometry.
      final recovered = state.pages[1].bboxes;
      expect(recovered, hasLength(1));
      expect(recovered.single.text, 'PAGE-TWO-LINE-A');
      expect(recovered.single.bbox, [0.0, 0.0, 1.0, 1.0]);
      expect(recovered.single.label, 'hydrated-from-artifact');
    });

    test('reports 0 and writes nothing when every page already has blocks',
        () {
      final container = makeContainer(_FakeOcrRepository());
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 1,
      );
      notifier.addOrUpdateBBox(
        0,
        const BBoxItem(
          blockId: 'p0_b0',
          page: 0,
          block: 0,
          bbox: [0.05, 0.05, 0.95, 0.15],
          text: 'real-WS-text',
          kind: 'paragraph',
        ),
      );

      final written =
          notifier.hydratePagesFromTextArtifact(<String, dynamic>{
        '0': 'should-not-be-written',
      });

      expect(written, 0);
      expect(container.read(workstationProvider).allBBoxes.single.text,
          'real-WS-text');
    });

    test('is idempotent: hydrating the same artifact twice adds no blocks',
        () {
      final container = makeContainer(_FakeOcrRepository());
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 2,
      );

      expect(notifier.hydratePagesFromTextArtifact(_twoPageArtifact), 3);
      final afterFirst = container.read(workstationProvider).allBBoxes;

      expect(notifier.hydratePagesFromTextArtifact(_twoPageArtifact), 0);
      final afterSecond = container.read(workstationProvider).allBBoxes;

      expect(afterSecond, hasLength(afterFirst.length));
      expect(
        afterSecond.map((b) => b.blockId).toList(),
        afterFirst.map((b) => b.blockId).toList(),
      );
      expect(
        afterSecond.map((b) => b.text).toList(),
        afterFirst.map((b) => b.text).toList(),
      );
    });

    test('grows the page list when the artifact holds more pages', () {
      final container = makeContainer(_FakeOcrRepository());
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 1,
      );

      final written = notifier.hydratePagesFromTextArtifact(
        const <String, dynamic>{
          '0': 'only page one',
          '1': 'page two arrived late',
          '2': 'page three arrived late',
        },
      );

      expect(written, 3);
      final state = container.read(workstationProvider);
      expect(state.pageCount, 3);
      expect(state.pages, hasLength(3));
      expect(state.pages[2].bboxes.single.text, 'page three arrived late');
    });

    test('ignores malformed artifact keys and values without writing them', () {
      final container = makeContainer(_FakeOcrRepository());
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 1,
      );

      final written = notifier.hydratePagesFromTextArtifact(
        const <String, dynamic>{
          'metadata': 'ignore me',
          '-1': 'negative index is out of range',
          '0': 42,
          '1': '   ',
        },
      );

      expect(written, 0);
      expect(container.read(workstationProvider).allBBoxes, isEmpty);
    });
  });

  group('stale artifact guard', () {
    test('a superseded run does not merge its artifact into a replaced '
        'document, even when live frames were already received', () async {
      // The gate lets the test swap documents while the artifact is in flight.
      final gate = Completer<String>();
      final repo = _FakeOcrRepository(
        gate: gate,
        syncResult: _resultWithArtifact('artifact-1', 'token-1'),
      );
      final container = makeContainer(repo);
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 1, 1]),
        'first.pdf',
        pageCount: 2,
      );

      final run = notifier.processOcrSync();

      // Partial stream: page 0 arrives live while the OCR call is still in
      // flight. This is the case that made the old blanket bail skip
      // reconciliation entirely, so the stale guard must now hold.
      await Future<void>.delayed(Duration.zero);
      notifier.handleWsFrame(const BlockCompleteFrame(
        pageIdx: 0,
        blockIdx: 0,
        bbox: [0.05, 0.05, 0.95, 0.15],
        text: 'first-doc-live-text',
        kind: 'paragraph',
      ));
      expect(
        container.read(workstationProvider).allBBoxes.single.text,
        'first-doc-live-text',
      );

      // Replace the document while the artifact request is still pending.
      // loadDocument resets the orchestration state, bumping the run epoch.
      notifier.loadDocument(
        Uint8List.fromList([2, 2, 2]),
        'second.pdf',
        pageCount: 1,
      );

      gate.complete(jsonEncode(_twoPageArtifact));
      await run;

      final state = container.read(workstationProvider);
      expect(state.filename, 'second.pdf');
      // Neither the stale artifact's text nor the previous document's live
      // block may leak into the newly loaded document.
      final texts = state.allBBoxes.map((b) => b.text).toList();
      expect(texts, isNot(contains('PAGE-ONE-LINE-A')));
      expect(texts, isNot(contains('PAGE-TWO-LINE-A')));
      expect(texts, isNot(contains('first-doc-live-text')));
      expect(state.allBBoxes, isEmpty);
    });

    test('the current run keeps live frame geometry and recovers the rest',
        () async {
      final gate = Completer<String>();
      final repo = _FakeOcrRepository(
        gate: gate,
        syncResult: _resultWithArtifact('artifact-1', 'token-1'),
      );
      final container = makeContainer(repo);
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 1, 1]),
        'first.pdf',
        pageCount: 2,
      );

      final run = notifier.processOcrSync();
      await Future<void>.delayed(Duration.zero);
      notifier.handleWsFrame(const BlockCompleteFrame(
        pageIdx: 0,
        blockIdx: 0,
        bbox: [0.05, 0.05, 0.95, 0.15],
        text: 'first-doc-live-text',
        kind: 'paragraph',
      ));

      gate.complete(jsonEncode(_twoPageArtifact));
      await run;

      final state = container.read(workstationProvider);
      // Live geometry preserved on page 0 ...
      final page0 = state.pages[0].bboxes;
      expect(page0, hasLength(1));
      expect(page0.single.text, 'first-doc-live-text');
      expect(page0.single.bbox, [0.05, 0.05, 0.95, 0.15]);
      expect(page0.single.label, isNull);
      // ... and the page that never streamed in is recovered.
      expect(state.pages[1].bboxes.single.text, 'PAGE-TWO-LINE-A');
      expect(state.pages[1].bboxes.single.label, 'hydrated-from-artifact');
    });

    test('a run with no live frames recovers every page from the artifact',
        () async {
      final gate = Completer<String>();
      final repo = _FakeOcrRepository(
        gate: gate,
        syncResult: _resultWithArtifact('artifact-1', 'token-1'),
      );
      final container = makeContainer(repo);
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 1, 1]),
        'first.pdf',
        pageCount: 2,
      );

      final run = notifier.processOcrSync();
      await Future<void>.delayed(Duration.zero);
      gate.complete(jsonEncode(_twoPageArtifact));
      await run;

      final state = container.read(workstationProvider);
      expect(
        state.allBBoxes.map((b) => b.text).toList(),
        ['PAGE-ONE-LINE-A', 'PAGE-ONE-LINE-B', 'PAGE-TWO-LINE-A'],
      );
      expect(
        state.allBBoxes.every((b) => b.label == 'hydrated-from-artifact'),
        isTrue,
      );
    });
  });
}
