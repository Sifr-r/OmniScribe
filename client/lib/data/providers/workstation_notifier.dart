import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/foundation.dart'
    show visibleForTesting, FlutterError, FlutterErrorDetails;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/data/models/bbox_item.dart';
import 'package:omniscribe_client/data/models/document_result.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/models/ws_frames.dart';
import 'package:omniscribe_client/data/providers/document_selection_notifier.dart';
import 'package:omniscribe_client/data/providers/job_orchestration_notifier.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/workstation_state.dart';
import 'package:omniscribe_client/data/repositories/job_repository.dart';
import 'package:omniscribe_client/data/repositories/ocr_repository.dart';
import 'package:omniscribe_client/data/repositories/sample_pdf_repository.dart';

/// Global provider for the OmniScribe Document Workstation.
final workstationProvider =
    NotifierProvider<WorkstationNotifier, WorkstationState>(
  WorkstationNotifier.new,
);

/// Internal record used by [WorkstationNotifier.hydratePagesFromTextArtifact]
/// while sorting server-side text-artifact lines back into per-page bbox
/// entries. Not exposed outside this file.
class _HydratedEntry {
  const _HydratedEntry(this.page, this.blockIdx, this.text);
  final int page;
  final int blockIdx;
  final String text;
}

/// Riverpod 2.x [Notifier] managing the Workstation document state (pages,
/// previews, bboxes) and canvas document data. OCR job orchestration lives in
/// [JobOrchestrationNotifier], viewport in [DocumentViewportNotifier], and
/// bbox selection in [DocumentSelectionNotifier].
class WorkstationNotifier extends Notifier<WorkstationState> {
  late OcrRepository _ocrRepo;
  late JobRepository _jobRepo;

  /// Cached server-side document ID for efficient page preview rendering.
  String? _previewDocId;

  /// Generation counter for background preloading to cancel superseded runs.
  int _preloadGeneration = 0;

  /// Guard flag to avoid duplicate concurrent preloader loops.
  bool _isPreloading = false;

  /// Exposes the cached preview document ID for testing.
  @visibleForTesting
  String? get previewDocId => _previewDocId;

  /// Exposes current preload generation for testing.
  @visibleForTesting
  int get preloadGeneration => _preloadGeneration;

  @override
  WorkstationState build() {
    _ocrRepo = ref.watch(ocrRepositoryProvider);
    _jobRepo = ref.watch(jobRepositoryProvider);

    ref.onDispose(_cleanup);

    return WorkstationState();
  }

  Future<void> _cleanup() async {
    _preloadGeneration++;
    _previewDocId = null;
  }

  // ---------------------------------------------------------------------------
  // Document Loading & Page Navigation
  // ---------------------------------------------------------------------------

  /// Loads a document from raw binary bytes, optional file path, and estimated page count.
  ///
  /// At least one of [bytes] or [filePath] must be provided — without either
  /// source [WorkstationState.hasDocument] would be false even though [pages]
  /// is populated, and the OCR pipeline would refuse to run.
  void loadDocument(
    Uint8List? bytes,
    String? filename, {
    int pageCount = 1,
    String? filePath,
  }) {
    assert(
      bytes != null || (filePath != null && filePath.isNotEmpty),
      'loadDocument requires either bytes or filePath to be provided',
    );
    final ext = (filename ?? '').split('.').last.toLowerCase();
    final isImage =
        const {'png', 'jpg', 'jpeg', 'webp', 'bmp', 'avif'}.contains(ext);
    final count = isImage ? 1 : (pageCount > 0 ? pageCount : 1);
    final imageDimensions =
        (isImage && bytes != null) ? parseImageDimensions(bytes) : null;
    final initialPages = List<PageResult>.generate(
      count,
      (index) => PageResult(
        page: index,
        width: (index == 0 && imageDimensions != null)
            ? imageDimensions.width
            : null,
        height: (index == 0 && imageDimensions != null)
            ? imageDimensions.height
            : null,
        previewBytes: (index == 0 && isImage) ? bytes : null,
      ),
    );

    _previewDocId = null;
    final generation = ++_preloadGeneration;

    ref.read(documentSelectionProvider.notifier).clear();
    ref.read(jobOrchestrationProvider.notifier).reset();
    state = WorkstationState(
      loadedBytes: bytes,
      filename: filename,
      filePath: filePath,
      pageCount: initialPages.length,
      selectedPageIndex: 0,
      pages: initialPages,
      showBBoxes: true,
      showHeatmap: true,
    );

    if (!isImage && bytes != null && bytes.isNotEmpty) {
      _loadDocumentPreview(0, generation: generation).then((preview) {
        if (generation == _preloadGeneration && preview != null) {
          _previewDocId = preview.docId ?? _previewDocId;
          if (preview.totalPages > 1) {
            _startBackgroundPreloader(generation);
          }
        }
      });
    }
  }

  /// Sets the currently active page index (0-indexed). Triggers a
  /// fetch for the page preview bytes if missing and re-prioritizes
  /// the background preloader around the newly selected page.
  void selectPage(int pageIndex) {
    if (pageIndex < 0 || pageIndex >= state.pageCount) {
      return;
    }
    ref.read(documentSelectionProvider.notifier).clear();
    state = state.copyWith(
      selectedPageIndex: pageIndex,
    );
    if (pageIndex < state.pages.length &&
        state.pages[pageIndex].previewBytes == null) {
      final jobId = _activeJobId();
      if (jobId != null) {
        _loadPagePreviewIfMissing(pageIndex);
      } else {
        _loadDocumentPreview(pageIndex);
      }
    }
    _startBackgroundPreloader(_preloadGeneration);
  }

  Future<void> _loadPagePreviewIfMissing(int pageIndex) async {
    final pages = state.pages;
    if (pageIndex < 0 || pageIndex >= pages.length) {
      return;
    }
    if (pages[pageIndex].previewBytes != null) {
      return;
    }
    final jobId = _activeJobId();
    if (jobId != null) {
      try {
        final bytes = await _jobRepo.fetchPagePreview(jobId, pageIndex);
        if (bytes != null) {
          setPagePreview(pageIndex, bytes);
          return;
        }
      } catch (e, st) {
        FlutterError.reportError(
          FlutterErrorDetails(exception: e, stack: st, library: 'workstation'),
        );
      }
    }
    if (state.loadedBytes != null && state.loadedBytes!.isNotEmpty) {
      await _loadDocumentPreview(pageIndex);
    }
  }

  /// Merges a rendered page [preview] into [pages] at [pageIndex], growing
  /// the list with placeholder pages as needed.
  List<PageResult> _mergePreview(
    List<PageResult> pages,
    int pageIndex,
    PagePreviewResult preview,
  ) {
    final updatedPages = List<PageResult>.from(pages);
    while (updatedPages.length <= pageIndex) {
      updatedPages.add(PageResult(page: updatedPages.length));
    }
    final cur = updatedPages[pageIndex];
    final imgDimensions = (preview.width == null || preview.height == null)
        ? parseImageDimensions(preview.bytes)
        : null;
    updatedPages[pageIndex] = cur.copyWith(
      previewBytes: preview.bytes,
      width: preview.width ?? imgDimensions?.width,
      height: preview.height ?? imgDimensions?.height,
    );
    return updatedPages;
  }

  Future<PagePreviewResult?> _loadDocumentPreview(
    int pageIndex, {
    int? generation,
  }) async {
    final fileBytes = state.loadedBytes;
    if ((fileBytes == null || fileBytes.isEmpty) && _previewDocId == null) {
      return null;
    }
    final targetGeneration = generation ?? _preloadGeneration;
    final filename = state.filename ?? 'document.pdf';

    state = state.copyWith(
      isPreviewLoading: true,
      clearPreviewError: true,
    );

    try {
      final preview = await _ocrRepo.renderDocumentPagePreview(
        fileBytes: fileBytes,
        filename: filename,
        pageIndex: pageIndex,
        docId: _previewDocId,
      );

      // Race guard: If the document was cleared, replaced, or generation bumped
      // while the preview fetch was in flight, abort cleanly without mutating state.
      if (targetGeneration != _preloadGeneration || !state.hasDocument) {
        if (state.hasDocument && state.isPreviewLoading) {
          state = state.copyWith(isPreviewLoading: false);
        }
        return null;
      }

      if (preview == null) {
        state = state.copyWith(
          isPreviewLoading: false,
          previewError:
              'Server could not render preview for page ${pageIndex + 1}',
        );
        return null;
      }
      if (preview.docId != null) {
        _previewDocId = preview.docId;
      }

      final updatedPages = _mergePreview(state.pages, pageIndex, preview);

      final newCount = math.max(state.pageCount, preview.totalPages);
      while (updatedPages.length < newCount) {
        updatedPages.add(PageResult(page: updatedPages.length));
      }

      state = state.copyWith(
        pages: updatedPages,
        pageCount: updatedPages.length,
        isPreviewLoading: false,
        clearPreviewError: true,
      );
      return preview;
    } catch (e) {
      state = state.copyWith(
        isPreviewLoading: false,
        previewError: 'Failed to generate page preview: ${e.toString()}',
      );
      return null;
    }
  }

  /// Priority distance scorer with forward bias:
  /// current + 1, current + 2, current - 1, current + 3, current - 2...
  static double _pageDistanceScore(int idx, int current) {
    if (idx == current) return 0.0;
    if (idx > current) {
      return (idx - current).toDouble();
    } else {
      return (current - idx) + 1.5;
    }
  }

  /// Progressive background preloader fetching remaining unrendered document pages.
  ///
  /// Prioritizes pages nearest to [WorkstationState.selectedPageIndex] with forward bias.
  /// Idempotent, non-blocking, and safely aborts if [generation] no longer matches
  /// [_preloadGeneration] or if document is cleared.
  Future<void> _startBackgroundPreloader(int generation) async {
    if (generation != _preloadGeneration ||
        !state.hasDocument ||
        state.pageCount <= 1 ||
        _isPreloading) {
      return;
    }

    _isPreloading = true;
    final failedIndices = <int>{};

    try {
      while (generation == _preloadGeneration &&
          state.hasDocument &&
          state.pageCount > 1) {
        final unrendered = <int>[];
        final totalCandidatePages =
            math.max(state.pageCount, state.pages.length);
        for (int i = 0; i < totalCandidatePages; i++) {
          if ((i >= state.pages.length ||
                  state.pages[i].previewBytes == null) &&
              !failedIndices.contains(i)) {
            unrendered.add(i);
          }
        }

        if (unrendered.isEmpty) {
          break;
        }

        final current = state.selectedPageIndex;
        unrendered.sort((a, b) => _pageDistanceScore(a, current)
            .compareTo(_pageDistanceScore(b, current)));

        var idx = unrendered.first;

        // If the active page is already being actively fetched by _loadDocumentPreview,
        // prioritize the next unrendered page so we don't issue duplicate requests.
        if (idx == state.selectedPageIndex && state.isPreviewLoading) {
          if (unrendered.length > 1) {
            idx = unrendered[1];
          } else {
            await Future<void>.delayed(const Duration(milliseconds: 50));
            continue;
          }
        }

        if (generation != _preloadGeneration || !state.hasDocument) {
          break;
        }

        final filename = state.filename ?? 'document.pdf';
        final preview = await _ocrRepo.renderDocumentPagePreview(
          fileBytes: state.loadedBytes,
          filename: filename,
          pageIndex: idx,
          docId: _previewDocId,
        );

        if (generation != _preloadGeneration || !state.hasDocument) {
          break;
        }

        if (preview != null) {
          _previewDocId = preview.docId ?? _previewDocId;
          final updatedPages = _mergePreview(state.pages, idx, preview);
          state = state.copyWith(
            pages: updatedPages,
            pageCount: math.max(state.pageCount, updatedPages.length),
            isPreviewLoading: idx == state.selectedPageIndex ? false : null,
          );
        } else {
          failedIndices.add(idx);
        }

        await Future<void>.delayed(const Duration(milliseconds: 25));
      }
    } catch (_) {
      // Deterministic error handling: background preloader swallows errors
      // without disturbing active UI state.
    } finally {
      _isPreloading = false;
    }
  }

  /// Retries fetching the preview for the given [pageIndex].
  void retryPagePreview(int pageIndex) {
    _loadDocumentPreview(pageIndex);
  }

  /// Returns the job id whose preview we should request — the active
  /// job if one is in flight, otherwise the most recently submitted one.
  /// Returns ``null`` when no job has been seen in this session.
  String? _activeJobId() {
    return ref.read(jobOrchestrationProvider).effectiveJobId;
  }

  /// Replaces all bounding boxes for a specific page.
  void setBBoxes(int page, List<BBoxItem> bboxes) {
    if (page < 0) return;
    final updatedPages = List<PageResult>.from(state.pages);

    while (updatedPages.length <= page) {
      updatedPages.add(PageResult(page: updatedPages.length));
    }

    updatedPages[page] = updatedPages[page].copyWith(bboxes: bboxes);

    state = state.copyWith(
      pages: updatedPages,
      pageCount: updatedPages.length,
    );
  }

  /// Hydrate the workstation pages from a server-side text artifact when
  /// the WebSocket progress stream never delivered `block_complete` frames
  /// (e.g. WS connection silently failed, or the OCR job completed before
  /// the client WS handshake finished).
  ///
  /// Server text artifact shape (from `plugins/documents/service.py::
  /// load_pages`):
  ///   `{"<page_index>": "<lines joined by \n>"}`
  ///
  /// Each non-empty line becomes a synthetic `BBoxItem` with a
  /// placeholder bbox — no real coordinates are stored server-side,
  /// only line-broken text. This is enough to drive the export modal's
  /// local formats (Markdown, Plain Text, HTML, Block Tree JSON,
  /// DOCX-from-markdown) without requiring a fresh OCR run.
  ///
  /// Returns the total number of bboxes written, or `-1` when nothing
  /// needed hydrating (workstation already had bboxes).
  int hydratePagesFromTextArtifact(Map<String, dynamic> artifact) {
    if (state.allBBoxes.isNotEmpty) {
      // WS already populated bboxes — don't clobber real coords with
      // placeholder lines. Caller should not invoke this when frames
      // were already received.
      return -1;
    }

    final entries = <_HydratedEntry>[];
    artifact.forEach((key, value) {
      final pageIndex = int.tryParse(key);
      if (pageIndex == null || pageIndex < 0) return;
      final text = value is String ? value : '';
      final lines = text.split('\n');
      for (var i = 0; i < lines.length; i++) {
        final line = lines[i];
        if (line.trim().isEmpty) continue;
        entries.add(_HydratedEntry(pageIndex, i, line));
      }
    });
    if (entries.isEmpty) return 0;

    // Sort by page, then by line order so the export modal's
    // `bboxesWithContent.join('\n\n')` produces readable output.
    entries.sort((a, b) {
      final pageCmp = a.page.compareTo(b.page);
      return pageCmp != 0 ? pageCmp : a.blockIdx.compareTo(b.blockIdx);
    });

    final newPages = <PageResult>[];
    var currentPageIdx = -1;
    var currentBBoxes = <BBoxItem>[];
    var blockCounter = 0;
    for (final entry in entries) {
      if (entry.page != currentPageIdx) {
        if (currentPageIdx >= 0) {
          newPages.add(PageResult(
            page: currentPageIdx,
            bboxes: currentBBoxes,
          ));
        }
        currentPageIdx = entry.page;
        currentBBoxes = <BBoxItem>[];
        blockCounter = 0;
      }
      currentBBoxes.add(BBoxItem(
        blockId: 'p${entry.page}_b${entry.blockIdx}_hydrated',
        page: entry.page,
        block: blockCounter++,
        bbox: const [0.0, 0.0, 1.0, 1.0],
        text: entry.text,
        kind: 'paragraph',
        label: 'hydrated-from-artifact',
      ));
    }
    if (currentPageIdx >= 0) {
      newPages.add(PageResult(page: currentPageIdx, bboxes: currentBBoxes));
    }

    state = state.copyWith(
      pages: newPages,
      pageCount: newPages.length,
    );
    return entries.length;
  }

  /// Adds a new bounding box or updates an existing bounding box on a page.
  void addOrUpdateBBox(int page, BBoxItem bbox) {
    if (page < 0) return;
    final updatedPages = List<PageResult>.from(state.pages);

    while (updatedPages.length <= page) {
      updatedPages.add(PageResult(page: updatedPages.length));
    }

    final currentPage = updatedPages[page];
    final currentBBoxes = List<BBoxItem>.from(currentPage.bboxes);
    final existingIdx = currentBBoxes.indexWhere(
      (b) =>
          b.blockId == bbox.blockId ||
          (b.block == bbox.block && b.page == bbox.page),
    );

    if (existingIdx >= 0) {
      currentBBoxes[existingIdx] = bbox;
    } else {
      currentBBoxes.add(bbox);
    }

    updatedPages[page] = currentPage.copyWith(bboxes: currentBBoxes);

    final selection = ref.read(documentSelectionProvider);
    final selected = selection.selectedBBox;
    if (selected != null &&
        (selected.blockId == bbox.blockId ||
            (selected.page == bbox.page && selected.block == bbox.block))) {
      ref.read(documentSelectionProvider.notifier).select(bbox);
    }

    state = state.copyWith(
      pages: updatedPages,
      pageCount: updatedPages.length,
    );
  }

  /// Toggles visibility of bounding box overlays.
  void toggleBBoxes([bool? force]) {
    state = state.copyWith(
      showBBoxes: force ?? !state.showBBoxes,
    );
  }

  /// Toggles confidence heatmap coloring.
  void toggleHeatmap([bool? force]) {
    state = state.copyWith(
      showHeatmap: force ?? !state.showHeatmap,
    );
  }

  /// Filters bounding boxes by block kind (paragraph, heading, table, etc.).
  void setFilterKind(String? kind) {
    state = state.copyWith(
      filterKind: kind,
      clearFilterKind: kind == null || kind.isEmpty || kind == 'all',
    );
  }

  /// Sets raster preview bytes for a specific page.
  void setPagePreview(int page, Uint8List previewBytes) {
    final updatedPages = List<PageResult>.from(state.pages);
    if (page >= 0 && page < updatedPages.length) {
      final imgDimensions = parseImageDimensions(previewBytes);
      final cur = updatedPages[page];
      updatedPages[page] = cur.copyWith(
        previewBytes: previewBytes,
        width: cur.width ?? imgDimensions?.width,
        height: cur.height ?? imgDimensions?.height,
      );
      state = state.copyWith(pages: updatedPages);
    }
  }

  /// Clears the loaded document and resets state to default.
  Future<void> clearDocument() async {
    _preloadGeneration++;
    _previewDocId = null;
    ref.read(documentSelectionProvider.notifier).clear();
    await ref.read(jobOrchestrationProvider.notifier).teardownProgressChannel();
    ref.read(jobOrchestrationProvider.notifier).reset();
    state = WorkstationState();
  }

  // ---------------------------------------------------------------------------
  // Keyboard Shortcut Plumbing
  // ---------------------------------------------------------------------------

  /// Increments the file-pick signal so any mounted listener (the upload
  /// dropzone) opens its native file picker. Idempotent on intent: every
  /// tap fires exactly one picker dialog.
  ///
  /// The signal is exposed as a monotonically increasing
  /// [WorkstationState.filePickSignal] int so Riverpod listeners can detect
  /// each increment without relying on identity changes (a plain boolean
  /// flip would be lost across consecutive taps because the toggle would
  /// settle back to `false`).
  void incrementFilePick() {
    state = state.copyWith(filePickSignal: state.filePickSignal + 1);
  }

  // ---------------------------------------------------------------------------
  // Document adoption (called by JobOrchestrationNotifier)
  // ---------------------------------------------------------------------------

  /// Adopts the OCR result PDF as the active document on successful run.
  void adoptProcessedDocument(Uint8List pdfBytes) {
    state = state.copyWith(loadedBytes: pdfBytes);
  }

  /// Stages a fetched sample PDF as the active document — the same state
  /// shape a user upload produces.
  void stageSampleDocument(Uint8List bytes, String name) {
    state = state.copyWith(
      loadedBytes: bytes,
      filename: name,
      clearFilePath: true,
      pageCount: 0,
    );
  }

  // ---------------------------------------------------------------------------
  // OCR Job Orchestration (facade over JobOrchestrationNotifier)
  // ---------------------------------------------------------------------------

  /// Executes synchronous OCR with real-time WebSocket progress updates.
  Future<void> processOcrSync({
    ProcessSettings? settings,
    void Function(int sent, int total)? onSendProgress,
    Duration? receiveTimeout,
  }) {
    return ref.read(jobOrchestrationProvider.notifier).processOcrSync(
          settings: settings,
          onSendProgress: onSendProgress,
          receiveTimeout: receiveTimeout,
        );
  }

  /// Submits an asynchronous OCR job to the worker queue.
  Future<void> processOcrAsync({
    ProcessSettings? settings,
    void Function(int sent, int total)? onSendProgress,
  }) {
    return ref.read(jobOrchestrationProvider.notifier).processOcrAsync(
          settings: settings,
          onSendProgress: onSendProgress,
        );
  }

  /// Processes an incoming WebSocket progress frame envelope.
  void handleWsFrame(WsEnvelope frame) {
    ref.read(jobOrchestrationProvider.notifier).handleWsFrame(frame);
  }

  @visibleForTesting
  Future<void> handleWsClosed() {
    return ref.read(jobOrchestrationProvider.notifier).handleWsClosed();
  }

  /// Cancels an active OCR job or streaming progress session.
  Future<void> cancelOcr() {
    return ref.read(jobOrchestrationProvider.notifier).cancelOcr();
  }

  /// Convenience for the Ctrl+Enter shortcut: process the current document
  /// with default settings (the workstation dock's tweaked values are not
  /// observable from the AppShell key handler in Phase A).
  Future<void> processCurrentDocument() {
    return ref.read(jobOrchestrationProvider.notifier).processCurrentDocument();
  }

  /// Fetches a canonical fixture PDF from the server and stages it as the
  /// active document so the normal OCR flow can process it (the
  /// "Try sample PDF" affordance).
  Future<void> tryWithSamplePdf({
    String name = SamplePdfRepository.defaultFixture,
  }) {
    return ref
        .read(jobOrchestrationProvider.notifier)
        .tryWithSamplePdf(name: name);
  }
}
