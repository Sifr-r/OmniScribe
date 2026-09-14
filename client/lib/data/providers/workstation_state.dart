import 'package:flutter/foundation.dart';
import 'package:omniscribe_client/data/models/bbox_item.dart';
import 'package:omniscribe_client/data/models/document_result.dart';

/// Immutable state model for the OmniScribe Workstation document domain:
/// loaded document data, page previews, and bounding boxes. OCR job /
/// progress / quality state lives in [JobOrchestrationState] — see
/// `job_orchestration_notifier.dart`.
@immutable
class WorkstationState {
  WorkstationState({
    // Document data
    this.loadedBytes,
    this.filename,
    this.filePath,
    this.pageCount = 0,
    this.selectedPageIndex = 0,
    List<PageResult> pages = const <PageResult>[],
    // Canvas viewport
    this.showBBoxes = true,
    this.showHeatmap = true,
    this.filterKind,
    this.isPreviewLoading = false,
    this.previewError,
    // Keyboard shortcut plumbing: bumped each time the AppShell fires
    // Ctrl+O (or any other trigger) so listeners (e.g. the upload dropzone)
    // can react by opening the native file picker. A monotonically
    // increasing int keeps the change observable by Riverpod listeners.
    this.filePickSignal = 0,
  }) : // Defensively copy mutable inputs so the @immutable contract holds even
        // when callers pass regular (growable) collections.
        // [loadedBytes] is a Uint8List (a typed buffer view); we do NOT copy it
        // here to avoid the cost of duplicating multi-MB PDF payloads on every
        // state transition. Callers MUST treat `state.loadedBytes` as read-only
        // — see [loadedBytes] for the full convention.
        pages = List<PageResult>.unmodifiable(pages);

  // Document data
  final Uint8List? loadedBytes;
  final String? filename;
  final String? filePath;
  final int pageCount;
  final int selectedPageIndex;
  final List<PageResult> pages;

  // Canvas viewport
  final bool showBBoxes;
  final bool showHeatmap;
  final String? filterKind;
  final bool isPreviewLoading;
  final String? previewError;

  /// Monotonically increasing counter incremented whenever the workstation
  /// should open its native file picker (Ctrl+O shortcut, "Open" toolbar
  /// action, etc.). Consumers compare the value across rebuilds to detect
  /// a pick-request and react exactly once per increment.
  final int filePickSignal;

  /// Ordered stages of the OCR pipeline.
  static const List<String> pipelineStages = [
    'Conversion',
    'Detection',
    'OCR',
    'Refine / Quality Repair',
    'Postprocess',
    'Embedding',
  ];

  /// Whether a document is currently loaded.
  ///
  /// A document is considered loaded when the workstation holds either the
  /// raw PDF bytes (`loadedBytes`) or an on-disk path (`filePath`). Populated
  /// `pages` alone are NOT sufficient — the OCR pipeline rejects documents
  /// with no source bytes, and consumers should treat "has pages" and "can
  /// run OCR" as the same precondition.
  bool get hasDocument => loadedBytes != null || filePath != null;

  /// Active [PageResult] based on [selectedPageIndex].
  PageResult? get currentPage {
    if (pages.isEmpty ||
        selectedPageIndex < 0 ||
        selectedPageIndex >= pages.length) {
      return null;
    }
    return pages[selectedPageIndex];
  }

  /// Bounding boxes on the active page, optionally filtered by [filterKind].
  List<BBoxItem> get currentPageBBoxes {
    final page = currentPage;
    if (page == null) return const <BBoxItem>[];
    if (filterKind == null || filterKind!.isEmpty || filterKind == 'all') {
      return page.bboxes;
    }
    return page.bboxes.where((b) => b.kind == filterKind).toList();
  }

  /// Flattened list of all bounding boxes across all loaded pages.
  List<BBoxItem> get allBBoxes =>
      pages.expand((p) => p.bboxes).toList();

  /// Number of revised bboxes across all pages — the document-domain
  /// fallback for the orchestration state's [repairedCount].
  int get documentRevisedCount =>
      allBBoxes.where((b) => b.revised ?? false).length;

  WorkstationState copyWith({
    // Document data
    Uint8List? loadedBytes,
    bool clearLoadedBytes = false,
    String? filename,
    bool clearFilename = false,
    String? filePath,
    bool clearFilePath = false,
    int? pageCount,
    int? selectedPageIndex,
    List<PageResult>? pages,
    // Canvas viewport
    bool? showBBoxes,
    bool? showHeatmap,
    String? filterKind,
    bool clearFilterKind = false,
    bool? isPreviewLoading,
    String? previewError,
    bool clearPreviewError = false,
    // Keyboard shortcut plumbing
    int? filePickSignal,
  }) {
    return WorkstationState(
      loadedBytes: clearLoadedBytes ? null : (loadedBytes ?? this.loadedBytes),
      filename: clearFilename ? null : (filename ?? this.filename),
      filePath: clearFilePath ? null : (filePath ?? this.filePath),
      pageCount: pageCount ?? this.pageCount,
      selectedPageIndex: selectedPageIndex ?? this.selectedPageIndex,
      pages: pages == null ? this.pages : List<PageResult>.unmodifiable(pages),
      showBBoxes: showBBoxes ?? this.showBBoxes,
      showHeatmap: showHeatmap ?? this.showHeatmap,
      filterKind: clearFilterKind ? null : (filterKind ?? this.filterKind),
      isPreviewLoading: isPreviewLoading ?? this.isPreviewLoading,
      previewError:
          clearPreviewError ? null : (previewError ?? this.previewError),
      filePickSignal: filePickSignal ?? this.filePickSignal,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is WorkstationState &&
          runtimeType == other.runtimeType &&
          listEquals(loadedBytes, other.loadedBytes) &&
          filename == other.filename &&
          filePath == other.filePath &&
          pageCount == other.pageCount &&
          selectedPageIndex == other.selectedPageIndex &&
          listEquals(pages, other.pages) &&
          showBBoxes == other.showBBoxes &&
          showHeatmap == other.showHeatmap &&
          filterKind == other.filterKind &&
          isPreviewLoading == other.isPreviewLoading &&
          previewError == other.previewError &&
          filePickSignal == other.filePickSignal;

  @override
  int get hashCode => Object.hashAll([
        loadedBytes != null ? Object.hashAll(loadedBytes!) : null,
        filename,
        filePath,
        pageCount,
        selectedPageIndex,
        Object.hashAll(pages),
        showBBoxes,
        showHeatmap,
        filterKind,
        isPreviewLoading,
        previewError,
        filePickSignal,
      ]);
}
