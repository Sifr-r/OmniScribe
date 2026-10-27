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

  group('WorkstationState bbox memoization', () {
    BBoxItem box(
      String id, {
      int page = 0,
      int block = 0,
      String kind = 'paragraph',
      bool revised = false,
      double confidence = 0.5,
    }) {
      return BBoxItem(
        blockId: id,
        page: page,
        block: block,
        bbox: const [0.0, 0.0, 1.0, 1.0],
        text: 'text-$id',
        kind: kind,
        revised: revised,
        confidence: confidence,
      );
    }

    test(
        'copyWith with changed pages must not return stale bbox data '
        '(memo is per-instance)', () {
      final initial = WorkstationState(
        pages: [
          PageResult(page: 0, bboxes: [box('b0')])
        ],
        pageCount: 1,
      );

      // Force the memo to evaluate on the pre-copy instance.
      expect(initial.allBBoxes, hasLength(1));
      expect(initial.documentRevisedCount, 0);

      final next = initial.copyWith(
        pages: [
          PageResult(
              page: 0, bboxes: [box('b0'), box('b1', block: 1, revised: true)]),
        ],
        pageCount: 1,
      );

      // The copy must report the NEW data, never the memoized old data.
      expect(next.allBBoxes, hasLength(2));
      expect(next.documentRevisedCount, 1);
      expect(next.allBBoxes.map((b) => b.blockId).toList(), ['b0', 'b1']);
      expect(identical(next.allBBoxes, initial.allBBoxes), isFalse);

      // ...and the pre-copy instance keeps its own answer (no leakage).
      expect(initial.allBBoxes, hasLength(1));
      expect(initial.documentRevisedCount, 0);
    });

    test('repeated reads reuse a single memoized instance', () {
      final state = WorkstationState(
        pages: [
          PageResult(
            page: 0,
            bboxes: [box('b0'), box('b1', block: 1, revised: true)],
          ),
          PageResult(page: 1, bboxes: [box('b2', page: 1)]),
        ],
        pageCount: 2,
      );

      // No re-allocation on read: every read yields the same instance.
      expect(identical(state.allBBoxes, state.allBBoxes), isTrue);
      expect(
          identical(state.currentPageBBoxes, state.currentPageBBoxes), isTrue);

      // The count is derived from that shared list, so it always agrees.
      expect(state.documentRevisedCount, 1);
      expect(
        state.documentRevisedCount,
        state.allBBoxes.where((b) => b.revised ?? false).length,
      );

      // The memo is shared, so callers must not be able to mutate it.
      expect(() => state.allBBoxes.add(box('x')), throwsUnsupportedError);
    });

    test(
        'bbox values stay correct across a sequence of page mutations '
        '(the OCR block-by-block flow)', () {
      // Mirrors WorkstationNotifier.addOrUpdateBBox: each completed block
      // allocates a new page list and produces a new state via copyWith.
      var state = WorkstationState(pageCount: 0);
      final pages = <PageResult>[];

      expect(state.allBBoxes, isEmpty);
      expect(state.documentRevisedCount, 0);

      for (var i = 0; i < 6; i++) {
        final pageIdx = i ~/ 3;
        while (pages.length <= pageIdx) {
          pages.add(PageResult(page: pages.length));
        }
        final revised = i.isEven; // blocks 0, 2 and 4 arrive already revised
        final updated = List<BBoxItem>.from(pages[pageIdx].bboxes)
          ..add(box('b$i', page: pageIdx, block: i, revised: revised));
        pages[pageIdx] = pages[pageIdx].copyWith(bboxes: updated);

        state = state.copyWith(pages: pages, pageCount: pages.length);

        expect(state.allBBoxes, hasLength(i + 1));
        expect(state.documentRevisedCount, i ~/ 2 + 1);
        expect(
          state.allBBoxes.map((b) => b.blockId).toList(),
          List.generate(i + 1, (n) => 'b$n'),
        );
      }
    });

    test(
        'copyWith changing filterKind or selectedPageIndex recomputes '
        'currentPageBBoxes', () {
      final state = WorkstationState(
        pages: [
          PageResult(
            page: 0,
            bboxes: [box('h', kind: 'heading'), box('p', block: 1)],
          ),
          PageResult(
            page: 1,
            bboxes: [box('q', page: 1, kind: 'heading')],
          ),
        ],
        pageCount: 2,
        selectedPageIndex: 0,
      );

      // Evaluate the memo before changing anything.
      expect(state.currentPageBBoxes, hasLength(2));

      final filtered = state.copyWith(filterKind: 'heading');
      expect(filtered.currentPageBBoxes.map((b) => b.blockId).toList(), ['h']);

      final onPage1 = state.copyWith(selectedPageIndex: 1);
      expect(onPage1.currentPageBBoxes.map((b) => b.blockId).toList(), ['q']);

      // The filtered result is shared, so it must be unmodifiable.
      expect(
        () => filtered.currentPageBBoxes.clear(),
        throwsUnsupportedError,
      );

      // A copyWith that leaves pages alone still reports correct data.
      final renamed = state.copyWith(filename: 'other.pdf');
      expect(renamed.allBBoxes, equals(state.allBBoxes));
      expect(renamed.currentPageBBoxes, hasLength(2));
    });

    test('currentPageBBoxes is unmodifiable in the unfiltered branch too', () {
      final state = WorkstationState(
        pages: [
          PageResult(
            page: 0,
            bboxes: [box('b1'), box('b2')],
          ),
        ],
        pageCount: 1,
        selectedPageIndex: 0,
      );

      // The unfiltered branch used to hand back PageResult.bboxes itself.
      // The memo is a `late final`, so an in-place mutation would have been
      // invisible to every later read of this state instance and could let
      // documentRevisedCount disagree with allBBoxes.
      expect(() => state.currentPageBBoxes.clear(), throwsUnsupportedError);
      expect(
          () => state.currentPageBBoxes.removeLast(), throwsUnsupportedError);

      // The underlying page data is untouched by the failed mutation.
      expect(state.currentPageBBoxes, hasLength(2));
      expect(state.allBBoxes, hasLength(2));
      expect(state.documentRevisedCount, 0);
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
