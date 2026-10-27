import 'package:flutter/foundation.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';

/// Immutable state model for the OmniScribe Workstation document domain:
/// loaded document data, page previews, and bounding boxes. OCR job /
/// progress / quality state lives in [JobOrchestrationState] — see
/// `job_orchestration_notifier.dart`.
///
/// ## bbox memoization
///
/// [currentPageBBoxes], [allBBoxes] and [documentRevisedCount] derive from
/// [pages] / [selectedPageIndex] / [filterKind]. These used to rebuild a full
/// flattened document-wide list on *every read*, so a single rebuild cost
/// roughly six full-document allocations (the trust panel alone read
/// `allBBoxes` five times). They are now backed by `late final` fields, which
/// evaluate at most once per state *instance*.
///
/// This is correct under [copyWith] because:
///
/// 1. Every state transition builds a brand new instance via [copyWith],
///    which routes through the constructor — a fresh `late final` starts
///    unevaluated, so a memo can never outlive the [pages] value that
///    produced it or leak from a previous state version.
/// 2. Within one instance the inputs ([pages], [selectedPageIndex],
///    [filterKind]) are `final`, so the memo's answer cannot become wrong.
///
/// The one remaining obligation, same convention as [loadedBytes]: callers
/// must treat `PageResult.bboxes` as read-only. Page bbox lists are replaced,
/// never mutated in place — `WorkstationNotifier.addOrUpdateBBox` allocates a
/// new `List<PageResult>` and a new `PageResult` per call — so nothing can
/// change [pages] behind a memo. Mutating a retained `PageResult.bboxes` list
/// in place would break this invariant and must not be done.
@immutable
class WorkstationState {
  WorkstationState({
    // Document data
    this.loadedBytes,
    this.processedPdfBytes,
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

  /// Searchable PDF output from the current successful OCR run.
  final Uint8List? processedPdfBytes;
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
  bool get hasDocument =>
      loadedBytes?.isNotEmpty == true || filePath?.trim().isNotEmpty == true;

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
  ///
  /// Memoized — see the [bbox memoization] note below.
  List<BBoxItem> get currentPageBBoxes => _pageBBoxesMemo;

  /// Flattened list of all bounding boxes across all loaded pages.
  ///
  /// Memoized — see the [bbox memoization] note below. The list is
  /// unmodifiable and SHARED by every reader of this state instance.
  List<BBoxItem> get allBBoxes => _allBBoxesMemo;

  /// Number of revised bboxes across all pages — the document-domain
  /// fallback for the orchestration state's [repairedCount].
  ///
  /// Memoized — see the [bbox memoization] note below. Derived from
  /// [_allBBoxesMemo] so the count can never disagree with [allBBoxes].
  int get documentRevisedCount => _revisedCountMemo;

  /// Memoized derivation backing [currentPageBBoxes].
  ///
  /// Both branches are wrapped unmodifiable because every reader of this state
  /// instance now shares one instance — a caller mutating the result would
  /// corrupt every other reader's view, and because the memo is a
  /// `late final` it could never observe that mutation afterwards, which
  /// would let [documentRevisedCount] silently disagree with [allBBoxes].
  late final List<BBoxItem> _pageBBoxesMemo = _derivePageBBoxes();

  /// Memoized derivation backing [allBBoxes].
  late final List<BBoxItem> _allBBoxesMemo =
      List<BBoxItem>.unmodifiable(pages.expand((p) => p.bboxes));

  /// Memoized derivation backing [documentRevisedCount].
  late final int _revisedCountMemo =
      _allBBoxesMemo.where((b) => b.revised ?? false).length;

  List<BBoxItem> _derivePageBBoxes() {
    final page = currentPage;
    if (page == null) return const <BBoxItem>[];
    if (filterKind == null || filterKind!.isEmpty || filterKind == 'all') {
      return List<BBoxItem>.unmodifiable(page.bboxes);
    }
    return List<BBoxItem>.unmodifiable(
      page.bboxes.where((b) => b.kind == filterKind),
    );
  }

  WorkstationState copyWith({
    // Document data
    Uint8List? loadedBytes,
    bool clearLoadedBytes = false,
    Uint8List? processedPdfBytes,
    bool clearProcessedPdfBytes = false,
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
      processedPdfBytes: clearProcessedPdfBytes
          ? null
          : (processedPdfBytes ?? this.processedPdfBytes),
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
          identical(loadedBytes, other.loadedBytes) &&
          identical(processedPdfBytes, other.processedPdfBytes) &&
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
        identityHashCode(loadedBytes),
        identityHashCode(processedPdfBytes),
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
