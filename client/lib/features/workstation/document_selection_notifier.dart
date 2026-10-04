import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';

/// Immutable bounding-box selection state for the workstation canvas.
class DocumentSelectionState {
  const DocumentSelectionState({this.selectedBBox, this.hoveredBBox});

  final BBoxItem? selectedBBox;
  final BBoxItem? hoveredBBox;

  bool get hasSelection => selectedBBox != null;

  DocumentSelectionState copyWith({
    BBoxItem? selectedBBox,
    bool clearSelectedBBox = false,
    BBoxItem? hoveredBBox,
    bool clearHoveredBBox = false,
  }) {
    return DocumentSelectionState(
      selectedBBox:
          clearSelectedBBox ? null : (selectedBBox ?? this.selectedBBox),
      hoveredBBox: clearHoveredBBox ? null : (hoveredBBox ?? this.hoveredBBox),
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is DocumentSelectionState &&
          other.selectedBBox == selectedBBox &&
          other.hoveredBBox == hoveredBBox;

  @override
  int get hashCode => Object.hash(selectedBBox, hoveredBBox);
}

/// Global provider for the workstation bounding-box selection.
final documentSelectionProvider =
    NotifierProvider<DocumentSelectionNotifier, DocumentSelectionState>(
  DocumentSelectionNotifier.new,
);

/// Owns the canvas selection domain: which bbox is selected or hovered,
/// separate from the OCR/paging concerns in [WorkstationNotifier].
class DocumentSelectionNotifier extends Notifier<DocumentSelectionState> {
  @override
  DocumentSelectionState build() => const DocumentSelectionState();

  /// Selects or deselects the bounding box.
  void select(BBoxItem? bbox) {
    if (state.selectedBBox == bbox) return;
    state = state.copyWith(
      selectedBBox: bbox,
      clearSelectedBBox: bbox == null,
    );
  }

  /// Sets or clears the hovered bounding box for hover effects.
  void hover(BBoxItem? bbox) {
    if (state.hoveredBBox == bbox) return;
    state = state.copyWith(
      hoveredBBox: bbox,
      clearHoveredBBox: bbox == null,
    );
  }

  /// Keeps the selection when a box is revised in place (inline text edit).
  void replaceSelected(BBoxItem updated) {
    if (state.selectedBBox == null) return;
    select(updated);
  }

  /// Clears both selection and hover (page change, document reload).
  void clear() {
    if (state.selectedBBox == null && state.hoveredBBox == null) return;
    state = const DocumentSelectionState();
  }
}
