import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/websocket/ws_client.dart';
import 'package:omniscribe_client/data/models/document_result.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/models/ws_frames.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_state.dart';
import 'package:omniscribe_client/data/repositories/ocr_repository.dart';
import 'package:omniscribe_client/data/repositories/sample_pdf_repository.dart';

/// Immutable OCR job / progress / quality state for the workstation.
class JobOrchestrationState {
  JobOrchestrationState({
    this.isProcessing = false,
    this.percent = 0,
    this.stage = 'Idle',
    this.statusMessage = '',
    List<String> warnings = const <String>[],
    this.channelId,
    this.activeJobId,
    this.lastSubmittedJobId,
    this.processedBlocks = 0,
    this.totalBlocks = 0,
    this.scoredBlocks = 0,
    this.avgConfidence,
    Map<String, int> blockRetryCounts = const <String, int>{},
    this.qualitySummary,
    this.trustSummary,
    this.error,
    this.textArtifactId,
    this.textArtifactToken,
  })  : warnings = List<String>.unmodifiable(warnings),
        blockRetryCounts = Map<String, int>.unmodifiable(blockRetryCounts);

  final bool isProcessing;
  final int percent;
  final String stage;
  final String statusMessage;
  final List<String> warnings;
  final String? channelId;
  final String? activeJobId;
  final String? lastSubmittedJobId;
  final int processedBlocks;
  final int totalBlocks;
  final int scoredBlocks;
  final double? avgConfidence;
  final Map<String, int> blockRetryCounts;
  final QualitySummary? qualitySummary;
  final TrustSummary? trustSummary;
  final String? error;
  final String? textArtifactId;
  final String? textArtifactToken;

  /// Total quality retry attempts across all blocks.
  int get totalRetriesAttempted =>
      blockRetryCounts.values.fold(0, (sum, count) => sum + count);

  /// Number of repaired blocks according to summary or bbox revision flags.
  ///
  /// [documentRevisedCount] is the document-domain count of revised bboxes
  /// (from [WorkstationState]); the summary wins when present.
  int repairedCount(int documentRevisedCount) =>
      qualitySummary?.repairedCount ?? documentRevisedCount;

  /// Formatted integer progress percentage.
  int get percentInt => percent;

  /// Numeric index of active pipeline stage.
  ///
  /// Returns the position of [stage] within [WorkstationState.pipelineStages],
  /// or `-1` when the stage is not part of the pipeline (e.g. `'Idle'`,
  /// `'Complete'`, `'Error'`, `'Cancelled'`). Callers that render a stepper UI
  /// should treat `-1` as "no active step" and skip highlighting instead of
  /// defaulting to `0`.
  int get currentStageIndex => WorkstationState.pipelineStages.indexOf(stage);

  /// The job id whose artifacts/previews we should request — the active job
  /// if one is in flight, otherwise the most recently submitted one.
  /// Returns ``null`` when no job has been seen in this session.
  String? get effectiveJobId {
    final active = activeJobId;
    if (active != null && active.isNotEmpty) return active;
    return lastSubmittedJobId;
  }

  JobOrchestrationState copyWith({
    bool? isProcessing,
    int? percent,
    String? stage,
    String? statusMessage,
    List<String>? warnings,
    String? channelId,
    bool clearChannelId = false,
    String? activeJobId,
    bool clearActiveJobId = false,
    String? lastSubmittedJobId,
    bool clearLastSubmittedJobId = false,
    int? processedBlocks,
    int? totalBlocks,
    int? scoredBlocks,
    bool clearScoredBlocks = false,
    double? avgConfidence,
    bool clearAvgConfidence = false,
    Map<String, int>? blockRetryCounts,
    QualitySummary? qualitySummary,
    bool clearQualitySummary = false,
    TrustSummary? trustSummary,
    bool clearTrustSummary = false,
    String? error,
    bool clearError = false,
    String? textArtifactId,
    bool clearTextArtifactId = false,
    String? textArtifactToken,
    bool clearTextArtifactToken = false,
  }) {
    return JobOrchestrationState(
      isProcessing: isProcessing ?? this.isProcessing,
      percent: percent ?? this.percent,
      stage: stage ?? this.stage,
      statusMessage: statusMessage ?? this.statusMessage,
      warnings:
          warnings == null ? this.warnings : List<String>.unmodifiable(warnings),
      channelId: clearChannelId ? null : (channelId ?? this.channelId),
      activeJobId: clearActiveJobId ? null : (activeJobId ?? this.activeJobId),
      lastSubmittedJobId: clearLastSubmittedJobId
          ? null
          : (lastSubmittedJobId ?? this.lastSubmittedJobId),
      processedBlocks: processedBlocks ?? this.processedBlocks,
      totalBlocks: totalBlocks ?? this.totalBlocks,
      scoredBlocks: clearScoredBlocks ? 0 : (scoredBlocks ?? this.scoredBlocks),
      avgConfidence:
          clearAvgConfidence ? null : (avgConfidence ?? this.avgConfidence),
      blockRetryCounts: blockRetryCounts == null
          ? this.blockRetryCounts
          : Map<String, int>.unmodifiable(blockRetryCounts),
      qualitySummary:
          clearQualitySummary ? null : (qualitySummary ?? this.qualitySummary),
      trustSummary:
          clearTrustSummary ? null : (trustSummary ?? this.trustSummary),
      error: clearError ? null : (error ?? this.error),
      textArtifactId:
          clearTextArtifactId ? null : (textArtifactId ?? this.textArtifactId),
      textArtifactToken: clearTextArtifactToken
          ? null
          : (textArtifactToken ?? this.textArtifactToken),
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is JobOrchestrationState &&
          runtimeType == other.runtimeType &&
          isProcessing == other.isProcessing &&
          percent == other.percent &&
          stage == other.stage &&
          statusMessage == other.statusMessage &&
          listEquals(warnings, other.warnings) &&
          channelId == other.channelId &&
          activeJobId == other.activeJobId &&
          lastSubmittedJobId == other.lastSubmittedJobId &&
          processedBlocks == other.processedBlocks &&
          totalBlocks == other.totalBlocks &&
          scoredBlocks == other.scoredBlocks &&
          avgConfidence == other.avgConfidence &&
          mapEquals(blockRetryCounts, other.blockRetryCounts) &&
          qualitySummary == other.qualitySummary &&
          trustSummary == other.trustSummary &&
          error == other.error &&
          textArtifactId == other.textArtifactId &&
          textArtifactToken == other.textArtifactToken;

  @override
  int get hashCode => Object.hashAll([
        isProcessing,
        percent,
        stage,
        statusMessage,
        Object.hashAll(warnings),
        channelId,
        activeJobId,
        lastSubmittedJobId,
        processedBlocks,
        totalBlocks,
        scoredBlocks,
        avgConfidence,
        // blockRetryCounts is compared order-insensitively via [mapEquals],
        // so its hash must also be order-insensitive: fold a content-based
        // per-entry hash into an unordered accumulator.
        Object.hashAllUnordered(
          blockRetryCounts.entries.map((e) => Object.hash(e.key, e.value)),
        ),
        qualitySummary,
        trustSummary,
        error,
        textArtifactId,
        textArtifactToken,
      ]);
}

/// Global provider for the workstation OCR job orchestration.
final jobOrchestrationProvider =
    NotifierProvider<JobOrchestrationNotifier, JobOrchestrationState>(
  JobOrchestrationNotifier.new,
);

/// Owns the workstation OCR job domain: synchronous and async job submission,
/// WebSocket progress frame ingestion, cancellation, and the progress /
/// quality state those flows feed. Document data (pages, previews, bboxes)
/// stays in [WorkstationNotifier]; selection in [DocumentSelectionNotifier].
class JobOrchestrationNotifier extends Notifier<JobOrchestrationState> {
  late OcrRepository _ocrRepo;
  late WsClient _wsClient;
  StreamSubscription<WsEnvelope>? _wsSubscription;

  /// Mirrors [JobOrchestrationState.channelId] for teardown — the ref is
  /// already disposed when [ref.onDispose] callbacks fire (Riverpod 3), so
  /// the cleanup path reads private fields instead of state.
  String? _lastChannelId;

  /// The session token paired with [_lastChannelId].
  String? _lastSessionToken;

  @override
  JobOrchestrationState build() {
    _ocrRepo = ref.watch(ocrRepositoryProvider);
    _wsClient = ref.watch(wsClientProvider);
    ref.onDispose(_disposeTeardown);
    return JobOrchestrationState();
  }

  Future<void> _disposeTeardown() async {
    await _wsSubscription?.cancel();
    _wsSubscription = null;

    final channelId = _lastChannelId;
    if (channelId != null && channelId.isNotEmpty) {
      try {
        await _wsClient.disconnect();
      } catch (_) {
        // Swallow disconnect errors during cleanup.
      }
      try {
        await _ocrRepo.cancelProgressChannel(
          channelId,
          sessionToken: _lastSessionToken ?? '',
        );
      } catch (_) {
        // Best-effort: server may already be done.
      }
    }
    _lastChannelId = null;
    _lastSessionToken = null;
  }

  /// Tears down the WebSocket + server-side progress channel while the
  /// provider is still alive (end of a run, cancellation, document clear).
  /// The last known [JobOrchestrationState.channelId] stays visible in state,
  /// matching the pre-split cleanup semantics.
  Future<void> teardownProgressChannel() async {
    await _disposeTeardown();
  }

  /// Resets all orchestration state to idle (document load/clear).
  void reset() {
    state = JobOrchestrationState();
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
    final ws = ref.read(workstationProvider);
    if (!ws.hasDocument) {
      state = state.copyWith(error: 'No document loaded to process');
      return;
    }

    final fileBytes = ws.loadedBytes;
    if (fileBytes == null) {
      state = state.copyWith(error: 'Document file bytes unavailable');
      return;
    }

    final filename = ws.filename ?? 'document.pdf';

    state = JobOrchestrationState(
      isProcessing: true,
      percent: 0,
      stage: 'Conversion',
      statusMessage: 'Starting OCR pipeline...',
    );
    _lastChannelId = null;
    _lastSessionToken = null;

    ProgressSessionHandle? session;
    try {
      // 1. Open progress session & attach WebSocket
      try {
        session = await _ocrRepo.openProgressSession();
        _lastChannelId = session.channelId;
        _lastSessionToken = session.sessionToken;
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

      ref.read(workstationProvider.notifier).adoptProcessedDocument(
            result.pdfBytes,
          );
      state = state.copyWith(
        isProcessing: false,
        percent: 100,
        stage: 'Complete',
        statusMessage: 'Document OCR complete',
        trustSummary: result.trustSummary,
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
      await teardownProgressChannel();
    }
  }

  /// Submits an asynchronous OCR job to the worker queue.
  Future<void> processOcrAsync({
    ProcessSettings? settings,
    void Function(int sent, int total)? onSendProgress,
  }) async {
    final ws = ref.read(workstationProvider);
    if (!ws.hasDocument) {
      state = state.copyWith(error: 'No document loaded to process');
      return;
    }

    final fileBytes = ws.loadedBytes;
    if (fileBytes == null) {
      state = state.copyWith(error: 'Document file bytes unavailable');
      return;
    }

    final filename = ws.filename ?? 'document.pdf';

    state = JobOrchestrationState(
      isProcessing: true,
      percent: 0,
      stage: 'Conversion',
      statusMessage: 'Submitting async OCR job...',
    );
    _lastChannelId = null;
    _lastSessionToken = null;

    try {
      // 1. Open progress session
      final session = await _ocrRepo.openProgressSession();
      _lastChannelId = session.channelId;
      _lastSessionToken = session.sessionToken;
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
      await teardownProgressChannel();
      state = state.copyWith(
        isProcessing: false,
        stage: 'Error',
        statusMessage: 'Async submission failed: $e',
        error: e.toString(),
      );
      rethrow;
    }
  }

  /// Handles unexpected WebSocket closure during active asynchronous OCR.
  Future<void> _handleWsClosed() async {
    if (!state.isProcessing || state.activeJobId == null) {
      return;
    }

    final jobId = state.activeJobId!;
    try {
      final status = await _ocrRepo.getJobStatus(jobId);
      if (status.isComplete) {
        final pdfBytes = await _ocrRepo.downloadResult(jobId);
        ref.read(workstationProvider.notifier).adoptProcessedDocument(pdfBytes);
        state = state.copyWith(
          isProcessing: false,
          percent: 100,
          stage: 'Complete',
          statusMessage: 'Document OCR complete',
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

  Future<void> handleWsClosed() => _handleWsClosed();

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

  /// Cancels an active OCR job or streaming progress session.
  Future<void> cancelOcr() async {
    if (!state.isProcessing) return;

    final channelId = state.channelId;
    final jobId = state.activeJobId;

    _wsClient.cancelChannel();

    if (channelId != null && channelId.isNotEmpty) {
      try {
        await _ocrRepo.cancelProgressChannel(
          channelId,
          sessionToken: _lastSessionToken ?? '',
        );
      } catch (_) {}
    }

    if (jobId != null && jobId.isNotEmpty) {
      try {
        await _ocrRepo.cancelJob(jobId);
      } catch (_) {}
    }

    await teardownProgressChannel();
    state = state.copyWith(
      isProcessing: false,
      stage: 'Cancelled',
      statusMessage: 'Cancelled by user',
    );
  }

  /// Convenience for the Ctrl+Enter shortcut / dock button: process the
  /// current document with default settings.
  Future<void> processCurrentDocument() async {
    try {
      await processOcrSync(settings: ProcessSettings.defaultSettings());
    } catch (_) {
      // Error state is already recorded in orchestration state by processOcrSync
    }
  }

  /// Fetches a canonical fixture PDF from the server and stages it as the
  /// active document (the "Try sample PDF" affordance).
  Future<void> tryWithSamplePdf({
    String name = SamplePdfRepository.defaultFixture,
  }) async {
    if (state.isProcessing) {
      return; // Don't interrupt an in-flight job.
    }
    state = state.copyWith(
      isProcessing: true,
      stage: 'Downloading',
      statusMessage: 'Fetching sample PDF "$name" from server...',
      clearError: true,
    );
    try {
      final bytes = await ref
          .read(samplePdfRepositoryProvider)
          .fetchSamplePdf(name);
      ref.read(workstationProvider.notifier).stageSampleDocument(bytes, name);
      state = state.copyWith(
        isProcessing: false,
        stage: 'Ready',
        statusMessage: 'Sample PDF loaded — click "Run OCR" to process',
      );
    } catch (e) {
      state = state.copyWith(
        isProcessing: false,
        stage: 'Error',
        statusMessage: 'Sample PDF fetch failed: $e',
        error: e.toString(),
      );
    }
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
        ref.read(workstationProvider.notifier).addOrUpdateBBox(b.pageIdx, item);

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
        ref
            .read(workstationProvider.notifier)
            .addOrUpdateBBox(rev.pageIdx, item);

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
}
