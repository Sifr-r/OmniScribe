import 'dart:async';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui' show Offset;

import 'package:flutter/foundation.dart' show visibleForTesting, FlutterError, FlutterErrorDetails;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/websocket/ws_client.dart';
import 'package:omniscribe_client/data/models/bbox_item.dart';
import 'package:omniscribe_client/data/models/document_result.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/models/ws_frames.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/workstation_state.dart';
import 'package:omniscribe_client/data/repositories/job_repository.dart';
import 'package:omniscribe_client/data/repositories/ocr_repository.dart';

/// Global provider for the OmniScribe Document Workstation.
final workstationProvider =
    NotifierProvider<WorkstationNotifier, WorkstationState>(
  WorkstationNotifier.new,
);

/// Riverpod 2.x [Notifier] managing the Workstation document state, canvas viewport,
/// real-time OCR streaming, and quality repair loops.
class WorkstationNotifier extends Notifier<WorkstationState> {
  late OcrRepository _ocrRepo;
  late JobRepository _jobRepo;
  late WsClient _wsClient;
  StreamSubscription<WsEnvelope>? _wsSubscription;

  /// Cached copy of [WorkstationState.channelId] for teardown.
  ///
  /// Wave 16 / flutter_riverpod 3.4: the ref is already disposed when
  /// [ref.onDispose] callbacks fire, so reading ``state`` inside the cleanup
  /// path raises ``UnmountedRefException``. We mirror the channelId into a
  /// private field at every write site and read it from there in [_cleanup].
  String? _lastChannelId;

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
    _wsClient = ref.watch(wsClientProvider);

    ref.onDispose(_cleanup);

    return WorkstationState();
  }

  Future<void> _cleanup() async {
    _preloadGeneration++;
    _previewDocId = null;

    // The StreamSubscription<WsEnvelope>.cancel() future is awaited so we
    // don't drop in-flight frames during teardown.
    await _wsSubscription?.cancel();
    _wsSubscription = null;

    // Only tear down the WebSocket + server-side progress channel if we
    // actually opened one. We read the cached [_lastChannelId] field instead
    // of ``state.channelId`` because the ref has been disposed by the time
    // this callback fires (Riverpod 3 lifecycle change).
    final channelId = _lastChannelId;
    if (channelId != null && channelId.isNotEmpty) {
      try {
        await _wsClient.disconnect();
      } catch (_) {
        // Swallow disconnect errors during cleanup; we still want to try cancel.
      }
      try {
        await _ocrRepo.cancelProgressChannel(channelId);
      } catch (_) {
        // Best-effort: server may already be done.
      }
    }
    _lastChannelId = null;
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

    state = WorkstationState(
      loadedBytes: bytes,
      filename: filename,
      filePath: filePath,
      pageCount: initialPages.length,
      selectedPageIndex: 0,
      pages: initialPages,
      selectedBBox: null,
      hoveredBBox: null,
      showBBoxes: true,
      showHeatmap: true,
      zoomScale: 1.0,
      panOffset: Offset.zero,
      isProcessing: false,
      percent: 0,
      stage: 'Idle',
      statusMessage: 'Document loaded',
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
    state = state.copyWith(
      selectedPageIndex: pageIndex,
      clearSelectedBBox: true,
      clearHoveredBBox: true,
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
    final active = state.activeJobId;
    if (active != null && active.isNotEmpty) return active;
    return state.lastSubmittedJobId;
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

    BBoxItem? newSelected = state.selectedBBox;
    if (state.selectedBBox != null &&
        (state.selectedBBox!.blockId == bbox.blockId ||
            (state.selectedBBox!.page == bbox.page &&
                state.selectedBBox!.block == bbox.block))) {
      newSelected = bbox;
    }

    state = state.copyWith(
      pages: updatedPages,
      pageCount: updatedPages.length,
      selectedBBox: newSelected,
    );
  }

  /// Selects or deselects a bounding box.
  void selectBBox(BBoxItem? bbox) {
    if (state.selectedBBox == bbox) return;
    state = state.copyWith(
      selectedBBox: bbox,
      clearSelectedBBox: bbox == null,
    );
  }

  /// Sets or clears the hovered bounding box for hover effects.
  void hoverBBox(BBoxItem? bbox) {
    if (state.hoveredBBox == bbox) return;
    state = state.copyWith(
      hoveredBBox: bbox,
      clearHoveredBBox: bbox == null,
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

  /// Sets the zoom scale for the canvas viewport.
  void setZoomScale(double zoom) {
    state = state.copyWith(
      zoomScale: zoom.clamp(0.2, 6.0),
    );
  }

  /// Sets the pan offset for the canvas viewport.
  void setPanOffset(Offset offset) {
    state = state.copyWith(
      panOffset: offset,
    );
  }

  /// Resets zoom and pan to defaults (1.0, Offset.zero).
  void resetViewport() {
    state = state.copyWith(
      zoomScale: 1.0,
      panOffset: Offset.zero,
    );
  }

  /// Filters bounding boxes by block kind (paragraph, heading, table, etc.).
  void setFilterKind(String? kind) {
    state = state.copyWith(
      filterKind: kind,
      clearFilterKind: kind == null || kind.isEmpty || kind == 'all',
    );
  }

  /// Updates trust metrics summary.
  void setTrustSummary(TrustSummary? summary) {
    state = state.copyWith(
      trustSummary: summary,
      clearTrustSummary: summary == null,
    );
  }

  /// Sets the text artifact handle for downstream export operations.
  void setTextArtifact({String? textArtifactId, String? textArtifactToken}) {
    state = state.copyWith(
      textArtifactId: textArtifactId,
      clearTextArtifactId: textArtifactId == null,
      textArtifactToken: textArtifactToken,
      clearTextArtifactToken: textArtifactToken == null,
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
    await _cleanup();
    _lastChannelId = null;
    state = WorkstationState();
  }

  // ---------------------------------------------------------------------------
  // Real-time WebSocket Frame Handling
  // ---------------------------------------------------------------------------

  /// Processes an incoming WebSocket progress frame envelope.
  void handleWsFrame(WsEnvelope frame) {
    switch (frame) {
      case ProgressFrame p:
        final updatedWarnings = p.warning && p.status.isNotEmpty
            ? [...state.warnings, p.status]
            : state.warnings;

        state = state.copyWith(
          percent: p.percent.clamp(0, 100),
          stage: p.stage.isNotEmpty ? p.stage : state.stage,
          statusMessage: p.status.isNotEmpty ? p.status : state.statusMessage,
          warnings: updatedWarnings,
        );

      case BlockCompleteFrame b:
        final item = b.toBBoxItem();
        addOrUpdateBBox(b.pageIdx, item);

        final newProcessed = state.processedBlocks + 1;
        final newTotal =
            state.totalBlocks < newProcessed ? newProcessed : state.totalBlocks;

        double? updatedAvg = state.avgConfidence;
        final newScoredBlocks =
            b.confidence != null ? state.scoredBlocks + 1 : state.scoredBlocks;
        if (b.confidence != null) {
          if (updatedAvg == null) {
            updatedAvg = b.confidence;
          } else {
            updatedAvg =
                (updatedAvg * state.scoredBlocks + b.confidence!) /
                    newScoredBlocks;
          }
        }

        state = state.copyWith(
          processedBlocks: newProcessed,
          totalBlocks: newTotal,
          scoredBlocks: newScoredBlocks,
          avgConfidence: updatedAvg,
          statusMessage:
              'Processed block ${b.blockIdx + 1} on page ${b.pageIdx + 1}',
        );

      case BlockRetryFrame r:
        final key = r.blockKey;
        final currentCount = state.blockRetryCounts[key] ?? 0;
        final newCounts = Map<String, int>.from(state.blockRetryCounts);
        newCounts[key] = currentCount + 1;

        state = state.copyWith(
          stage: 'Refine / Quality Repair',
          statusMessage:
              'Retrying low confidence block ${r.blockIdx + 1} (attempt ${r.attempt}, conf ${(r.confidence * 100).toStringAsFixed(1)}%)',
          blockRetryCounts: newCounts,
        );

      case BlockRevisedFrame rev:
        final item = rev.toBBoxItem();
        addOrUpdateBBox(rev.pageIdx, item);

        state = state.copyWith(
          stage: 'Refine / Quality Repair',
          statusMessage:
              'Repaired block ${rev.blockIdx + 1} on page ${rev.pageIdx + 1}',
        );

      case PageCompleteFrame pageComp:
        state = state.copyWith(
          statusMessage: 'Completed page ${pageComp.pageIdx + 1}',
        );

      case QualitySummaryFrame q:
        state = state.copyWith(
          qualitySummary: q.summary,
          avgConfidence: q.avgConfidence,
          statusMessage:
              'Quality target ${(q.target * 100).round()}%: ${q.repairedCount} repaired',
        );

      case CancelledFrame c:
        state = state.copyWith(
          isProcessing: false,
          stage: 'Cancelled',
          percent: c.percent,
          statusMessage: c.status.isNotEmpty ? c.status : 'Job was cancelled',
        );

      case ConnectedFrame conn:
        _lastChannelId = conn.channelId;
        state = state.copyWith(
          channelId: conn.channelId,
        );

      default:
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // OCR Pipeline Execution
  // ---------------------------------------------------------------------------

  /// Executes synchronous OCR with real-time WebSocket progress updates.
  Future<void> processOcrSync({
    ProcessSettings? settings,
    void Function(int sent, int total)? onSendProgress,
    Duration? receiveTimeout,
  }) async {
    if (state.loadedBytes == null && state.filePath == null) {
      state = state.copyWith(error: 'No document loaded to process');
      return;
    }

    final fileBytes = state.loadedBytes;
    if (fileBytes == null) {
      state = state.copyWith(error: 'Document file bytes unavailable');
      return;
    }

    final filename = state.filename ?? 'document.pdf';

    state = state.copyWith(
      isProcessing: true,
      percent: 0,
      stage: 'Conversion',
      statusMessage: 'Starting OCR pipeline...',
      warnings: const <String>[],
      blockRetryCounts: const <String, int>{},
      clearQualitySummary: true,
      clearAvgConfidence: true,
      clearScoredBlocks: true,
      clearActiveJobId: true,
      clearChannelId: true,
      clearTrustSummary: true,
      processedBlocks: 0,
      totalBlocks: 0,
      clearError: true,
    );
    _lastChannelId = null;

    ProgressSessionHandle? session;
    try {
      // 1. Open progress session & attach WebSocket
      try {
        session = await _ocrRepo.openProgressSession();
        _lastChannelId = session.channelId;
        state = state.copyWith(channelId: session.channelId);

        await _wsClient.connect(
          channelId: session.channelId,
          sessionToken: session.sessionToken,
        );

        await _wsSubscription?.cancel();
        _wsSubscription = _wsClient.stream.listen(handleWsFrame);
      } catch (_) {
        // Fail-open for WebSocket progress attach (still run sync OCR)
      }

      // 2. Execute synchronous OCR call
      final result = await _ocrRepo.processOcrSync(
        fileBytes: fileBytes,
        filename: filename,
        settings: settings,
        progressChannel: session?.channelId,
        progressToken: session?.sessionToken,
        onSendProgress: onSendProgress,
        receiveTimeout: receiveTimeout,
      );

      state = state.copyWith(
        isProcessing: false,
        percent: 100,
        stage: 'Complete',
        statusMessage: 'Document OCR complete',
        trustSummary: result.trustSummary,
        loadedBytes: result.pdfBytes,
        textArtifactId: result.textArtifactId,
        textArtifactToken: result.textArtifactToken,
      );
    } catch (e) {
      state = state.copyWith(
        isProcessing: false,
        stage: 'Error',
        statusMessage: 'Processing failed: $e',
        error: e.toString(),
      );
      rethrow;
    } finally {
      await _cleanup();
    }
  }

  /// Submits an asynchronous OCR job to the worker queue.
  Future<void> processOcrAsync({
    ProcessSettings? settings,
    void Function(int sent, int total)? onSendProgress,
  }) async {
    if (state.loadedBytes == null && state.filePath == null) {
      state = state.copyWith(error: 'No document loaded to process');
      return;
    }

    final fileBytes = state.loadedBytes;
    if (fileBytes == null) {
      state = state.copyWith(error: 'Document file bytes unavailable');
      return;
    }

    final filename = state.filename ?? 'document.pdf';

    state = state.copyWith(
      isProcessing: true,
      percent: 0,
      stage: 'Conversion',
      statusMessage: 'Submitting async OCR job...',
      warnings: const <String>[],
      blockRetryCounts: const <String, int>{},
      clearQualitySummary: true,
      clearAvgConfidence: true,
      clearScoredBlocks: true,
      clearActiveJobId: true,
      clearChannelId: true,
      clearTrustSummary: true,
      processedBlocks: 0,
      totalBlocks: 0,
      clearError: true,
    );
    _lastChannelId = null;

    try {
      // 1. Open progress session
      final session = await _ocrRepo.openProgressSession();
      _lastChannelId = session.channelId;
      state = state.copyWith(channelId: session.channelId);

      await _wsClient.connect(
        channelId: session.channelId,
        sessionToken: session.sessionToken,
      );

      await _wsSubscription?.cancel();
      _wsSubscription = _wsClient.stream.listen(
        handleWsFrame,
        onDone: () => _handleWsClosed(),
      );

      // 2. Submit async OCR request
      final submitResponse = await _ocrRepo.processOcrAsync(
        fileBytes: fileBytes,
        filename: filename,
        settings: settings,
        progressChannel: session.channelId,
        progressToken: session.sessionToken,
        onSendProgress: onSendProgress,
      );

      state = state.copyWith(
        isProcessing: true,
        stage: 'Queued',
        activeJobId: submitResponse.jobId,
        lastSubmittedJobId: submitResponse.jobId,
        statusMessage: 'Job queued: ${submitResponse.jobId}',
      );
    } catch (e) {
      await _cleanup();
      state = state.copyWith(
        isProcessing: false,
        stage: 'Error',
        statusMessage: 'Async submission failed: $e',
        error: e.toString(),
      );
      rethrow;
    }
  }

  /// Handles unexpected WebSocket closure during active asynchronous OCR processing.
  Future<void> _handleWsClosed() async {
    if (!state.isProcessing || state.activeJobId == null) {
      return;
    }

    final jobId = state.activeJobId!;
    try {
      final status = await _ocrRepo.getJobStatus(jobId);
      if (status.isComplete) {
        final pdfBytes = await _ocrRepo.downloadResult(jobId);
        state = state.copyWith(
          isProcessing: false,
          percent: 100,
          stage: 'Complete',
          statusMessage: 'Document OCR complete',
          loadedBytes: pdfBytes,
          textArtifactId: status.textArtifactId,
        );
      } else if (status.isCancelled) {
        state = state.copyWith(
          isProcessing: false,
          stage: 'Cancelled',
          statusMessage: 'Job was cancelled',
        );
      } else if (status.isError) {
        state = state.copyWith(
          isProcessing: false,
          stage: 'Error',
          statusMessage: status.error?.isNotEmpty == true
              ? status.error!
              : 'Processing failed',
          error: status.error ?? 'Job failed with error status',
        );
      }
    } catch (e) {
      state = state.copyWith(
        isProcessing: false,
        stage: 'Error',
        statusMessage: 'Job status check failed: $e',
        error: e.toString(),
      );
    }
  }

  @visibleForTesting
  Future<void> handleWsClosed() => _handleWsClosed();

  /// Cancels an active OCR job or streaming progress session.
  Future<void> cancelOcr() async {
    if (!state.isProcessing) return;

    final channelId = state.channelId;
    final jobId = state.activeJobId;

    _wsClient.cancelChannel();

    if (channelId != null && channelId.isNotEmpty) {
      try {
        await _ocrRepo.cancelProgressChannel(channelId);
      } catch (_) {}
    }

    if (jobId != null && jobId.isNotEmpty) {
      try {
        await _ocrRepo.cancelJob(jobId);
      } catch (_) {}
    }

    await _cleanup();
    state = state.copyWith(
      isProcessing: false,
      stage: 'Cancelled',
      statusMessage: 'Cancelled by user',
    );
  }

  // ---------------------------------------------------------------------------
  // Keyboard Shortcut Plumbing
  // ---------------------------------------------------------------------------

  /// Increments the file-pick signal so any mounted listener (the upload
  /// dropzone) opens its native file picker. Idempotent on intent: every
  /// tap fires exactly one picker dialog.
  ///
  /// The signal is exposed as a monotonically increasing [WorkstationState.filePickSignal]
  /// int so Riverpod listeners can detect each increment without relying on
  /// identity changes (a plain boolean flip would be lost across consecutive
  /// taps because the toggle would settle back to `false`).
  void incrementFilePick() {
    state = state.copyWith(filePickSignal: state.filePickSignal + 1);
  }

  /// Convenience for the Ctrl+Enter shortcut: process the current document
  /// with default settings (the workstation dock's tweaked values are not
  /// observable from the AppShell key handler in Phase A).
  Future<void> processCurrentDocument() async {
    try {
      await processOcrSync(settings: ProcessSettings.defaultSettings());
    } catch (_) {
      // Error state is already recorded in workstation state by processOcrSync
    }
  }
}
