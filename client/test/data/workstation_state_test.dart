// WorkstationState's constructor intentionally wraps collection arguments with
// `List.unmodifiable` to enforce the immutability contract proven by the tests
// in the `Immutability (unmodifiable getters)` group below. That defensive
// copy means the constructor cannot be `const`, which trips
// `prefer_const_constructors` and `prefer_const_literals_to_create_immutables`
// for every test instantiation in this file. Silencing these two lints here is
// intentional and scoped.
// ignore_for_file: prefer_const_constructors, prefer_const_literals_to_create_immutables

import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';
import 'package:omniscribe_client/features/workstation/workstation_state.dart';

void main() {
  group('WorkstationState Construction & Defaults', () {
    test('default constructor provides sane initial defaults', () {
      final state = WorkstationState();

      expect(state.loadedBytes, isNull);
      expect(state.filename, isNull);
      expect(state.filePath, isNull);
      expect(state.pageCount, 0);
      expect(state.selectedPageIndex, 0);
      expect(state.pages, isEmpty);

      expect(state.showBBoxes, isTrue);
      expect(state.showHeatmap, isTrue);
      expect(state.filterKind, isNull);
      expect(state.documentRevisedCount, 0);

      expect(state.hasDocument, isFalse);
      expect(state.currentPage, isNull);
      expect(state.currentPageBBoxes, isEmpty);
      expect(state.allBBoxes, isEmpty);
    });
  });

  group('WorkstationState Getters', () {
    test(
        'hasDocument requires loadedBytes or filePath (pages alone are not enough)',
        () {
      // loadedBytes alone => true
      expect(
        WorkstationState(loadedBytes: Uint8List.fromList([1, 2, 3]))
            .hasDocument,
        isTrue,
      );
      // filePath alone => true
      expect(
        WorkstationState(filePath: '/path/to/doc.pdf').hasDocument,
        isTrue,
      );
      // both => true
      expect(
        WorkstationState(
          loadedBytes: Uint8List.fromList([1, 2, 3]),
          filePath: '/path/to/doc.pdf',
        ).hasDocument,
        isTrue,
      );
      // pages alone without loadedBytes/filePath => false (new contract)
      expect(
        WorkstationState(pages: [PageResult(page: 0)]).hasDocument,
        isFalse,
      );
      // fully empty => false
      expect(WorkstationState().hasDocument, isFalse);
      expect(WorkstationState(loadedBytes: Uint8List(0)).hasDocument, isFalse);
      expect(WorkstationState(filePath: ' ').hasDocument, isFalse);
    });

    test(
        'currentPage returns page at selectedPageIndex or null if out of range',
        () {
      const page0 = PageResult(page: 0);
      const page1 = PageResult(page: 1);
      final state = WorkstationState(
        pages: [page0, page1],
        pageCount: 2,
        selectedPageIndex: 1,
      );

      expect(state.currentPage, equals(page1));

      final invalidState = state.copyWith(selectedPageIndex: 5);
      expect(invalidState.currentPage, isNull);
    });

    test('currentPageBBoxes filters by filterKind when present', () {
      const b1 = BBoxItem(
        blockId: 'b1',
        page: 0,
        block: 0,
        bbox: [0, 0, 1, 1],
        text: 'Heading',
        kind: 'heading',
      );
      const b2 = BBoxItem(
        blockId: 'b2',
        page: 0,
        block: 1,
        bbox: [0, 0, 1, 1],
        text: 'Paragraph',
        kind: 'paragraph',
      );

      final state = WorkstationState(
        pages: [
          PageResult(page: 0, bboxes: [b1, b2]),
        ],
        pageCount: 1,
        selectedPageIndex: 0,
      );

      expect(state.currentPageBBoxes, equals([b1, b2]));
      expect(state.allBBoxes, equals([b1, b2]));

      final filteredState = state.copyWith(filterKind: 'heading');
      expect(filteredState.currentPageBBoxes, equals([b1]));

      final allFilterState = state.copyWith(filterKind: 'all');
      expect(allFilterState.currentPageBBoxes, equals([b1, b2]));
    });

    test('documentRevisedCount counts revised bboxes across all pages', () {
      const b1 = BBoxItem(
        blockId: 'b1',
        page: 0,
        block: 0,
        bbox: [0, 0, 1, 1],
        text: 'T1',
        revised: true,
      );
      const b2 = BBoxItem(
        blockId: 'b2',
        page: 1,
        block: 0,
        bbox: [0, 0, 1, 1],
        text: 'T2',
      );

      final state = WorkstationState(
        pages: [
          PageResult(page: 0, bboxes: [b1]),
          PageResult(page: 1, bboxes: [b2]),
        ],
      );

      expect(state.documentRevisedCount, 1);
    });
  });

  group('WorkstationState Immutability (unmodifiable getters)', () {
    test('pages getter returns an unmodifiable list', () {
      final state = WorkstationState(pages: [PageResult(page: 0)]);

      // Reading is fine
      expect(state.pages.length, 1);
      expect(state.pages.first.page, 0);

      // Mutating the returned list must throw
      expect(
        () => state.pages.add(PageResult(page: 1)),
        throwsUnsupportedError,
      );
      expect(
        () => state.pages.clear(),
        throwsUnsupportedError,
      );
      expect(
        () => state.pages.removeAt(0),
        throwsUnsupportedError,
      );
    });

    test('copyWith produces unmodifiable collections', () {
      final original = WorkstationState(pages: [PageResult(page: 0)]);

      // Pass new mutable collections into copyWith; they must be wrapped.
      final next = original.copyWith(
        pages: [PageResult(page: 0), PageResult(page: 1)],
      );

      expect(() => next.pages.add(PageResult(page: 2)), throwsUnsupportedError);
    });
  });

  group('WorkstationState copyWith & Clear Flags', () {
    test('preserves untouched fields and overwrites specified values', () {
      final initial = WorkstationState(
        filename: 'doc.pdf',
        pageCount: 5,
        selectedPageIndex: 1,
      );

      final updated = initial.copyWith(
        selectedPageIndex: 3,
      );

      expect(updated.filename, 'doc.pdf');
      expect(updated.pageCount, 5);
      expect(updated.selectedPageIndex, 3);
    });

    test('clear flags explicitly reset nullable fields to null', () {
      final initial = WorkstationState(
        loadedBytes: Uint8List.fromList([1, 2]),
        filename: 'test.pdf',
        filePath: '/test.pdf',
        filterKind: 'heading',
        previewError: 'render failed',
      );

      final cleared = initial.copyWith(
        clearLoadedBytes: true,
        clearFilename: true,
        clearFilePath: true,
        clearFilterKind: true,
        clearPreviewError: true,
      );

      expect(cleared.loadedBytes, isNull);
      expect(cleared.filename, isNull);
      expect(cleared.filePath, isNull);
      expect(cleared.filterKind, isNull);
      expect(cleared.previewError, isNull);
    });
  });

  group('WorkstationState Equality & HashCode', () {
    test(
        'instances with identical fields are equal and have matching hashCodes',
        () {
      final bytes1 = Uint8List.fromList([1, 2, 3]);
      final bytes2 = bytes1;

      final s1 = WorkstationState(
        loadedBytes: bytes1,
        filename: 'file.pdf',
        pageCount: 1,
      );

      final s2 = WorkstationState(
        loadedBytes: bytes2,
        filename: 'file.pdf',
        pageCount: 1,
      );

      expect(s1, equals(s2));
      expect(s1.hashCode, equals(s2.hashCode));
    });

    test('instances with different fields are not equal', () {
      final s1 = WorkstationState(selectedPageIndex: 0);
      final s2 = WorkstationState(selectedPageIndex: 1);

      expect(s1, isNot(equals(s2)));
    });

    test('different same-length document buffers remain distinct', () {
      final first = WorkstationState(loadedBytes: Uint8List.fromList([1, 2]));
      final second = WorkstationState(loadedBytes: Uint8List.fromList([3, 4]));
      expect(first, isNot(equals(second)));
      expect(first.copyWith(), first);
    });

    test('processed PDF readiness can be set and cleared independently', () {
      final source = Uint8List.fromList([1]);
      final pdf = Uint8List.fromList([2]);
      final original = WorkstationState(loadedBytes: source);
      final processed = original.copyWith(processedPdfBytes: pdf);
      expect(processed, isNot(original));
      expect(processed.processedPdfBytes, same(pdf));
      expect(processed.copyWith(clearProcessedPdfBytes: true), original);
    });
  });
}
