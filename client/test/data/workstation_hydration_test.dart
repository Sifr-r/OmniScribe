import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/features/workstation/ocr_repository.dart';

class _StubRepo implements OcrRepository {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  ProviderContainer makeContainer() {
    final container = ProviderContainer(
      overrides: [
        ocrRepositoryProvider.overrideWithValue(_StubRepo()),
      ],
    );
    addTearDown(container.dispose);
    return container;
  }

  group('hydratePagesFromTextArtifact', () {
    test(
        'hydrates pages and bboxes from server text artifact when WS '
        'frames never arrived', () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 2,
      );

      expect(container.read(workstationProvider).allBBoxes, isEmpty);

      final artifact = <String, dynamic>{
        '0': 'Header\n\nFirst page body.\nSecond line.',
        '1': 'Page two opening line.\n\nMiddle paragraph.\nTrailing.',
      };

      final written = notifier.hydratePagesFromTextArtifact(artifact);
      expect(written, greaterThan(0));

      final ws = container.read(workstationProvider);
      expect(ws.pageCount, 2);
      // 3 lines on page 0 (Header, First page body., Second line.)
      // 3 lines on page 1 (Page two opening line., Middle paragraph., Trailing.)
      expect(ws.allBBoxes.length, 6);
      expect(ws.pages[0].bboxes.map((b) => b.text).toList(), [
        'Header',
        'First page body.',
        'Second line.',
      ]);
      expect(ws.pages[1].bboxes.map((b) => b.text).toList(), [
        'Page two opening line.',
        'Middle paragraph.',
        'Trailing.',
      ]);
      // Placeholder bbox (full-page), but with a hydrated-from-artifact
      // label so the UI can mark it as a degraded path.
      for (final b in ws.allBBoxes) {
        expect(b.bbox, [0.0, 0.0, 1.0, 1.0]);
        expect(b.label, 'hydrated-from-artifact');
      }
    });

    test(
        'preserves existing streamed geometry while reconciling canonical text',
        () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(Uint8List.fromList([1, 2, 3]), 'scan.pdf',
          pageCount: 1);

      // Simulate a real WS frame having arrived first.
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

      expect(container.read(workstationProvider).allBBoxes, hasLength(1));

      final written = notifier.hydratePagesFromTextArtifact({
        '0': 'real-WS-text',
      });
      // Truthful count: the only artifact page already had blocks, so the
      // merge wrote nothing. (Previously this returned a blanket -1 and the
      // artifact was never consulted, which silently dropped pages when a
      // partial progress stream had delivered page 0 only.)
      expect(written, 0);
      // Real WS bbox untouched.
      expect(container.read(workstationProvider).allBBoxes.single.text,
          'real-WS-text');
    });

    test('recovers missing blocks within a page and repeated line occurrences',
        () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);
      notifier.loadDocument(Uint8List.fromList([1]), 'scan.pdf');
      const live = BBoxItem(
          blockId: 'live',
          page: 0,
          block: 3,
          bbox: [0.1, 0.2, 0.8, 0.3],
          text: 'first\nrepeated');
      notifier.addOrUpdateBBox(0, live);
      final artifact = <String, dynamic>{
        '0': 'first\nrepeated\nmissing\nrepeated'
      };
      expect(notifier.hydratePagesFromTextArtifact(artifact), 2);
      final page = container.read(workstationProvider).pages.single;
      expect(page.bboxes.first, same(live));
      expect(page.bboxes.map((box) => box.block), [3, 4, 5]);
      expect(page.text, artifact['0']);
      expect(page.bboxes.last.text, 'repeated');
      expect(notifier.hydratePagesFromTextArtifact(artifact), 0);
      expect(container.read(workstationProvider).pages.single.bboxes,
          hasLength(3));
    });

    test('returns 0 and writes nothing for an empty artifact', () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(Uint8List.fromList([1, 2, 3]), 'scan.pdf',
          pageCount: 1);
      final written =
          notifier.hydratePagesFromTextArtifact(<String, dynamic>{});
      expect(written, 0);
      expect(container.read(workstationProvider).allBBoxes, isEmpty);
    });

    test('whitespace differences do not duplicate live lines', () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);
      notifier.loadDocument(Uint8List.fromList([1]), 'scan.pdf');
      notifier.addOrUpdateBBox(
          0,
          const BBoxItem(
              blockId: 'live',
              page: 0,
              block: 0,
              bbox: [0, 0, 1, 1],
              text: ' line '));
      expect(notifier.hydratePagesFromTextArtifact({'0': 'line'}), 0);
      expect(container.read(workstationProvider).allBBoxes, hasLength(1));
    });

    test('unbounded artifact page indices fail before extending preview state',
        () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);
      notifier.loadDocument(Uint8List.fromList([1]), 'scan.pdf');
      expect(
          () =>
              notifier.hydratePagesFromTextArtifact({'1000000000': 'hostile'}),
          throwsFormatException);
      expect(container.read(workstationProvider).pages, hasLength(1));
    });

    test('ignores non-numeric page keys', () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(Uint8List.fromList([1, 2, 3]), 'scan.pdf',
          pageCount: 1);
      final written = notifier.hydratePagesFromTextArtifact(<String, dynamic>{
        'metadata': 'ignore me',
        '0': 'keep me',
        '-1': 'negative also out',
      });
      expect(written, 1);
      expect(
          container.read(workstationProvider).allBBoxes.single.text, 'keep me');
    });

    test('preserves existing page metadata and order during hydration', () {
      final container = makeContainer();
      final notifier = container.read(workstationProvider.notifier);

      notifier.loadDocument(
        Uint8List.fromList([1, 2, 3]),
        'scan.pdf',
        pageCount: 3,
      );

      final preview0 = Uint8List.fromList([10, 20, 30]);
      final preview1 = Uint8List.fromList([40, 50, 60]);
      final preview2 = Uint8List.fromList([70, 80, 90]);

      notifier.setPagePreview(0, preview0);
      notifier.setPagePreview(1, preview1);
      notifier.setPagePreview(2, preview2);

      expect(
          container.read(workstationProvider).pages[0].previewBytes, preview0);
      expect(
          container.read(workstationProvider).pages[1].previewBytes, preview1);
      expect(
          container.read(workstationProvider).pages[2].previewBytes, preview2);

      // Hydrate with only page 0 and page 2 (leaving page 1 without artifact entries)
      final written = notifier.hydratePagesFromTextArtifact({
        '0': 'Page 0 content',
        '2': 'Page 2 line 1\nPage 2 line 2',
      });

      expect(written, 3);
      final ws = container.read(workstationProvider);
      expect(ws.pages.length, 3);

      // Page 0 preserved previewBytes and updated bboxes
      expect(ws.pages[0].previewBytes, preview0);
      expect(
          ws.pages[0].bboxes.map((b) => b.text).toList(), ['Page 0 content']);

      // Page 1 preserved previewBytes and kept empty bboxes
      expect(ws.pages[1].previewBytes, preview1);
      expect(ws.pages[1].bboxes, isEmpty);

      // Page 2 preserved previewBytes and updated bboxes
      expect(ws.pages[2].previewBytes, preview2);
      expect(ws.pages[2].bboxes.map((b) => b.text).toList(), [
        'Page 2 line 1',
        'Page 2 line 2',
      ]);
    });
  });
}
