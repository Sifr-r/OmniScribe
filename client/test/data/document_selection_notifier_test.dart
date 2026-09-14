import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/data/models/bbox_item.dart';
import 'package:omniscribe_client/data/providers/document_selection_notifier.dart';

void main() {
  ProviderContainer makeContainer() => ProviderContainer();

  const box = BBoxItem(
    blockId: 'b1',
    page: 0,
    block: 0,
    bbox: [0.1, 0.1, 0.9, 0.3],
    text: 'Box text',
  );
  const other = BBoxItem(
    blockId: 'b2',
    page: 0,
    block: 1,
    bbox: [0.2, 0.2, 0.8, 0.4],
    text: 'Other text',
  );

  group('DocumentSelectionState', () {
    test('defaults to no selection and no hover', () {
      const state = DocumentSelectionState();
      expect(state.selectedBBox, isNull);
      expect(state.hoveredBBox, isNull);
      expect(state.hasSelection, isFalse);
    });

    test('value equality covers both fields', () {
      expect(
        const DocumentSelectionState(selectedBBox: box),
        const DocumentSelectionState(selectedBBox: box),
      );
      expect(
        const DocumentSelectionState(selectedBBox: box),
        isNot(const DocumentSelectionState(selectedBBox: other)),
      );
      expect(
        const DocumentSelectionState(selectedBBox: box),
        isNot(
          const DocumentSelectionState(selectedBBox: box, hoveredBBox: other),
        ),
      );
    });
  });

  group('DocumentSelectionNotifier', () {
    test('select sets and clears the selected box', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentSelectionProvider.notifier);

      notifier.select(box);
      expect(container.read(documentSelectionProvider).selectedBBox, box);
      expect(container.read(documentSelectionProvider).hasSelection, isTrue);

      notifier.select(null);
      expect(container.read(documentSelectionProvider).selectedBBox, isNull);
    });

    test('select is a no-op when the selection is unchanged', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentSelectionProvider.notifier);

      notifier.select(box);
      notifier.select(box);
      expect(container.read(documentSelectionProvider).selectedBBox, box);
    });

    test('hover sets and clears the hovered box independently', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentSelectionProvider.notifier);

      notifier.select(box);
      notifier.hover(other);
      expect(container.read(documentSelectionProvider).hoveredBBox, other);

      notifier.hover(null);
      final state = container.read(documentSelectionProvider);
      expect(state.hoveredBBox, isNull);
      expect(state.selectedBBox, box);
    });

    test('replaceSelected swaps a revised box in place', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentSelectionProvider.notifier);

      notifier.select(box);
      const revised = BBoxItem(
        blockId: 'b1',
        page: 0,
        block: 0,
        bbox: [0.1, 0.1, 0.9, 0.3],
        text: 'Revised text',
        revised: true,
      );
      notifier.replaceSelected(revised);
      expect(
        container.read(documentSelectionProvider).selectedBBox,
        revised,
      );
    });

    test('replaceSelected is a no-op without a selection', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentSelectionProvider.notifier);

      notifier.replaceSelected(other);
      expect(container.read(documentSelectionProvider).selectedBBox, isNull);
    });

    test('clear resets both selection and hover', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentSelectionProvider.notifier);

      notifier.select(box);
      notifier.hover(other);
      notifier.clear();

      expect(
        container.read(documentSelectionProvider),
        const DocumentSelectionState(),
      );
    });
  });
}
