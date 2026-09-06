import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/data/models/feature_models.dart';
import 'package:omniscribe_client/data/providers/features_state.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/repositories/feature_repository.dart';

export 'package:omniscribe_client/data/providers/features_state.dart';

// ---------------------------------------------------------------------------
// Translation Notifier & Provider
// ---------------------------------------------------------------------------

final translationProvider =
    NotifierProvider<TranslationNotifier, TranslationState>(
  TranslationNotifier.new,
);

class TranslationNotifier extends Notifier<TranslationState> {
  late final FeatureRepository _repo;
  Timer? _pollTimer;

  @override
  TranslationState build() {
    _repo = ref.watch(featureRepositoryProvider);
    ref.onDispose(stopPolling);
    return const TranslationState.initial();
  }

  void startPolling(String jobId) {
    stopPolling();
    _pollTimer = Timer.periodic(const Duration(seconds: 2), (timer) async {
      await checkTranslationStatus(jobId);
      if (!state.isTranslating) {
        stopPolling();
      }
    });
  }

  void stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  void setSourceText(String text) {
    state = state.copyWith(sourceText: text);
  }

  void setTargetLanguage(String lang) {
    state = state.copyWith(targetLanguage: lang);
  }

  void setSelectedModel(String model) {
    state = state.copyWith(selectedModel: model);
  }

  void setUseNllb(bool useNllb) {
    state = state.copyWith(useNllb: useNllb);
  }

  void clearSourceText() {
    state = state.copyWith(sourceText: '');
  }

  void clearError() {
    state = state.copyWith(clearError: true);
  }

  void setTranslatedOutput(String output) {
    state = state.copyWith(translatedOutput: output);
  }

  void setAsyncJobId(String? jobId) {
    state = state.copyWith(
      asyncJobId: jobId,
      clearAsyncJobId: jobId == null,
    );
  }

  void setAsyncStatus(String? status) {
    state = state.copyWith(
      asyncStatus: status,
      clearAsyncStatus: status == null,
    );
  }

  Future<void> translate({
    String? apiBase,
    String? apiKey,
    String? fallbackModel,
    bool? dualTranslate,
  }) async {
    final text = state.sourceText.trim();
    if (text.isEmpty) {
      state = state.copyWith(
        error: 'Please provide source text to translate.',
      );
      return;
    }

    state = state.copyWith(
      isTranslating: true,
      translatedOutput: '',
      clearError: true,
      clearAsyncStatus: true,
    );

    try {
      if (state.useNllb) {
        final res = await _repo.translateNllb(
          text: text,
          targetLanguage: state.targetLanguage,
        );
        state = state.copyWith(
          translatedOutput: res.translatedText,
          isTranslating: false,
        );
      } else {
        final req = TranslationRequest(
          text: text,
          targetLanguage: state.targetLanguage,
          model: state.selectedModel.isNotEmpty
              ? state.selectedModel
              : fallbackModel,
          apiBase: apiBase,
          apiKey: apiKey,
          dualTranslate: dualTranslate,
        );
        final res = await _repo.translate(req);
        state = state.copyWith(
          translatedOutput: res.translatedText,
          isTranslating: false,
        );
      }
    } catch (e) {
      state = state.copyWith(
        isTranslating: false,
        error: e.toString(),
      );
    }
  }

  Future<String?> translateAsync({
    String? apiBase,
    String? apiKey,
    String? fallbackModel,
    bool autoPoll = true,
  }) async {
    final text = state.sourceText.trim();
    if (text.isEmpty) {
      state = state.copyWith(
        error: 'Please provide source text for async translation.',
      );
      return null;
    }

    state = state.copyWith(
      isTranslating: true,
      translatedOutput: '',
      asyncStatus: 'Queuing async translation job...',
      clearError: true,
    );

    try {
      final req = TranslationRequest(
        text: text,
        targetLanguage: state.targetLanguage,
        model: state.selectedModel.isNotEmpty
            ? state.selectedModel
            : fallbackModel,
        apiBase: apiBase,
        apiKey: apiKey,
      );
      final res = await _repo.translateAsync(req);
      state = state.copyWith(
        asyncJobId: res.jobId,
        asyncStatus: 'Job ${res.jobId} queued. Polling progress...',
      );
      if (autoPoll) {
        startPolling(res.jobId);
      }
      return res.jobId;
    } catch (e) {
      state = state.copyWith(
        isTranslating: false,
        error: e.toString(),
        asyncStatus: 'Async translation failed: $e',
      );
      return null;
    }
  }

  Future<void> checkTranslationStatus(String jobId) async {
    try {
      final status = await _repo.getTranslationStatus(jobId);
      final stateStr = status.state.toUpperCase();

      if (stateStr == 'SUCCESS' || stateStr == 'COMPLETED') {
        state = state.copyWith(
          isTranslating: false,
          translatedOutput:
              status.result?.toString() ?? 'Translation completed.',
          asyncStatus: 'Completed.',
        );
        stopPolling();
      } else if (stateStr == 'FAILURE' ||
          stateStr == 'FAILED' ||
          status.error != null) {
        final err = status.detail ?? status.error ?? 'Unknown error';
        state = state.copyWith(
          isTranslating: false,
          error: err,
          asyncStatus: 'Failed: $err',
        );
        stopPolling();
      } else {
        state = state.copyWith(
          asyncStatus:
              'Status: ${status.state} (${status.status ?? "in-flight"})',
        );
      }
    } catch (e) {
      state = state.copyWith(
        isTranslating: false,
        error: e.toString(),
        asyncStatus: 'Polling error: $e',
      );
      stopPolling();
    }
  }
}

// ---------------------------------------------------------------------------
// Transcription Notifier & Provider
// ---------------------------------------------------------------------------

final transcriptionProvider =
    NotifierProvider<TranscriptionNotifier, TranscriptionState>(
  TranscriptionNotifier.new,
);

class TranscriptionNotifier extends Notifier<TranscriptionState> {
  late final FeatureRepository _repo;
  Timer? _playbackTimer;

  @override
  TranscriptionState build() {
    _repo = ref.watch(featureRepositoryProvider);
    // Wave 16 / flutter_riverpod 3.4: the ref is already disposed when
    // ``ref.onDispose`` callbacks fire, so touching ``state`` from inside
    // the callback raises ``UnmountedRefException``. We inline the
    // timer-cancel here (no state mutation) and keep the stateful
    // [stopPlayback] for in-method callers.
    ref.onDispose(() {
      _playbackTimer?.cancel();
      _playbackTimer = null;
    });
    return const TranscriptionState.initial();
  }

  void setAudio(Uint8List bytes, String filename, {double? duration}) {
    stopPlayback();
    state = state.copyWith(
      audioBytes: bytes,
      audioFilename: filename,
      totalDuration: duration ?? 0.0,
      currentPlaybackTime: 0.0,
      isPlaying: false,
      clearError: true,
    );
  }

  void setEngine(String engine) {
    state = state.copyWith(engine: engine);
  }

  void setModel(String model) {
    state = state.copyWith(model: model);
  }

  void setLanguage(String? language) {
    state = state.copyWith(
      language: language,
      clearLanguage: language == null || language.isEmpty,
    );
  }

  void setPrompt(String? prompt) {
    state = state.copyWith(
      prompt: prompt,
      clearPrompt: prompt == null || prompt.isEmpty,
    );
  }

  void clearAudio() {
    stopPlayback();
    state = state.copyWith(
      clearAudio: true,
      totalDuration: 0.0,
      currentPlaybackTime: 0.0,
      isPlaying: false,
    );
  }

  void clearError() {
    state = state.copyWith(clearError: true);
  }

  void setPlaybackTime(double time) {
    state = state.copyWith(currentPlaybackTime: time);
    updateActiveSegment();
  }

  void setActiveSegmentId(int? id) {
    state = state.copyWith(
      activeSegmentId: id,
      clearActiveSegment: id == null,
    );
  }

  void setIsPlaying(bool isPlaying) {
    state = state.copyWith(isPlaying: isPlaying);
  }

  void startPlayback() {
    _playbackTimer?.cancel();
    state = state.copyWith(isPlaying: true);

    _playbackTimer = Timer.periodic(const Duration(milliseconds: 100), (timer) {
      if (state.currentPlaybackTime >= state.totalDuration) {
        stopPlayback();
        setPlaybackTime(0.0);
      } else {
        setPlaybackTime(state.currentPlaybackTime + 0.1);
      }
    });
  }

  void pausePlayback() {
    _playbackTimer?.cancel();
    _playbackTimer = null;
    state = state.copyWith(isPlaying: false);
  }

  void stopPlayback() {
    _playbackTimer?.cancel();
    _playbackTimer = null;
    state = state.copyWith(isPlaying: false);
  }

  void togglePlayback() {
    if (state.isPlaying) {
      pausePlayback();
    } else {
      startPlayback();
    }
  }

  void setResult(TranscriptionResponse result) {
    state = state.copyWith(
      result: result,
      totalDuration: result.duration ??
          (result.segments.isNotEmpty
              ? result.segments.last.end
              : state.totalDuration),
    );
  }

  void updateActiveSegment() {
    final res = state.result;
    if (res == null) return;
    for (final seg in res.segments) {
      if (state.currentPlaybackTime >= seg.start &&
          state.currentPlaybackTime <= seg.end) {
        if (state.activeSegmentId != seg.id) {
          setActiveSegmentId(seg.id);
        }
        return;
      }
    }
  }

  void seekToSegment(TranscriptionSegment segment) {
    state = state.copyWith(
      currentPlaybackTime: segment.start,
      activeSegmentId: segment.id,
    );
    startPlayback();
  }

  Future<void> transcribe({
    String? apiBase,
    String? apiKey,
  }) async {
    if (state.audioBytes == null || state.audioFilename == null) {
      state = state.copyWith(
        errorMessage: 'Please select an audio file first.',
      );
      return;
    }

    state = state.copyWith(
      isTranscribing: true,
      clearResult: true,
      clearError: true,
    );

    try {
      final req = TranscriptionRequest(
        engine: TranscriptionEngineType.fromString(state.engine),
        model: state.model,
        language: state.language,
        prompt: state.prompt,
        apiBase: apiBase,
        apiKey: apiKey,
      );

      final res = await _repo.transcribe(
        audioBytes: state.audioBytes!,
        filename: state.audioFilename!,
        request: req,
      );

      final dur = res.duration ??
          (res.segments.isNotEmpty
              ? res.segments.last.end
              : state.totalDuration);

      state = state.copyWith(
        isTranscribing: false,
        result: res,
        totalDuration: dur,
      );
    } catch (e) {
      state = state.copyWith(
        isTranscribing: false,
        errorMessage: e.toString(),
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Glossary Notifier & Provider
// ---------------------------------------------------------------------------

final glossaryProvider = NotifierProvider<GlossaryNotifier, GlossaryState>(
  GlossaryNotifier.new,
);

class GlossaryNotifier extends Notifier<GlossaryState> {
  late final FeatureRepository _repo;

  @override
  GlossaryState build() {
    _repo = ref.watch(featureRepositoryProvider);
    return const GlossaryState.initial();
  }

  void setActiveViewIndex(int index) {
    state = state.copyWith(activeViewIndex: index);
  }

  void setSelectedLibrary(GlossaryListItem? lib) {
    state = state.copyWith(
      selectedLibrary: lib,
      clearSelectedLibrary: lib == null,
    );
  }

  void clearError() {
    state = state.copyWith(clearError: true);
  }

  Future<void> loadLibraries() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final libs = await _repo.getGlossaryLibraries();
      state = state.copyWith(libraries: libs, isLoading: false);
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }

  Future<void> loadEntries(GlossaryListItem lib) async {
    state = state.copyWith(
      selectedLibrary: lib,
      isLoading: true,
      clearError: true,
    );

    try {
      final entries = await _repo.getGlossaryEntries(lib.id);
      state = state.copyWith(
        entries: entries,
        activeViewIndex: 1,
        isLoading: false,
      );
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }

  Future<void> loadMergedLexicon() async {
    try {
      final entries = await _repo.getMergedGlossaryEntries();
      final map = <String, String>{
        for (final e in entries) e.source: e.target,
      };
      state = state.copyWith(mergedLexicon: map);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> toggleLibrary(GlossaryListItem lib, bool enabled) async {
    bool ok;
    try {
      ok = await _repo.toggleGlossaryLibrary(lib.id, enabled);
    } catch (e) {
      state = state.copyWith(error: e.toString());
      return;
    }
    if (!ok) {
      state = state.copyWith(error: 'Failed to toggle "${lib.name}".');
      return;
    }

    final updated = state.libraries.map((item) {
      if (item.id == lib.id) {
        return item.copyWith(enabled: enabled);
      }
      return item;
    }).toList();

    state = state.copyWith(libraries: updated);
    await loadMergedLexicon();
  }

  Future<void> deleteLibrary(String id) async {
    bool ok;
    try {
      ok = await _repo.deleteGlossaryLibrary(id);
    } catch (e) {
      state = state.copyWith(error: e.toString());
      return;
    }
    if (!ok) {
      state = state.copyWith(error: 'Failed to delete glossary library.');
      return;
    }

    final updated = state.libraries.where((item) => item.id != id).toList();
    final resetSelected = state.selectedLibrary?.id == id;

    state = state.copyWith(
      libraries: updated,
      entries: resetSelected ? const <GlossaryEntry>[] : state.entries,
      activeViewIndex: resetSelected ? 0 : state.activeViewIndex,
      clearSelectedLibrary: resetSelected,
    );
    await loadMergedLexicon();
  }

  Future<GlossaryImportJobResponse> importGlossaryFile({
    required Uint8List fileBytes,
    required String filename,
    String? channelId,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final res = await _repo.importGlossaryFile(
        fileBytes: fileBytes,
        filename: filename,
        channelId: channelId,
      );
      await loadLibraries();
      await loadMergedLexicon();
      state = state.copyWith(isLoading: false);
      return res;
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
      rethrow;
    }
  }

  Future<GlossaryImportJobResponse> importGlossaryUrl({
    required String url,
    required GlossaryFormat format,
    String? name,
    String? channelId,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final res = await _repo.importGlossaryUrl(
        url: url,
        format: format,
        name: name,
        channelId: channelId,
      );
      await loadLibraries();
      await loadMergedLexicon();
      state = state.copyWith(isLoading: false);
      return res;
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
      rethrow;
    }
  }

  Future<void> importGlossaryJson({
    required GlossaryFormat format,
    String? name,
    String? text,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final bytes = Uint8List.fromList(utf8.encode(text ?? ''));
      await _repo.importGlossaryFile(
        fileBytes: bytes,
        filename: '${name ?? "glossary"}.${format.value}',
      );
      await loadLibraries();
      await loadMergedLexicon();
      state = state.copyWith(isLoading: false);
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Extraction Notifier & Provider
// ---------------------------------------------------------------------------

final extractionProvider =
    NotifierProvider<ExtractionNotifier, ExtractionState>(
  ExtractionNotifier.new,
);

class ExtractionNotifier extends Notifier<ExtractionState> {
  late final FeatureRepository _repo;

  @override
  ExtractionState build() {
    _repo = ref.watch(featureRepositoryProvider);
    return ExtractionState.initial();
  }

  void setInputText(String text) {
    state = state.copyWith(inputText: text);
  }

  void setCustomSchema(String schema) {
    state = state.copyWith(customSchema: schema);
  }

  void setSelectedTemplate(String template) {
    state = state.copyWith(selectedTemplate: template);
  }

  void clearInputText() {
    state = state.copyWith(inputText: '');
  }

  void clearError() {
    state = state.copyWith(clearError: true);
  }

  Future<void> extract({
    String? model,
    String? apiBase,
    String? apiKey,
  }) async {
    final text = state.inputText.trim();
    if (text.isEmpty) {
      state = state.copyWith(
        error: 'Please enter or paste input text to extract.',
      );
      return;
    }

    state = state.copyWith(
      isExtracting: true,
      clearExtractedData: true,
      clearStatusMessage: true,
      clearError: true,
    );

    try {
      final req = ExtractionRequest(
        text: text,
        template: ExtractionTemplate.fromString(state.selectedTemplate),
        customPrompt: state.selectedTemplate == 'custom'
            ? state.customSchema.trim()
            : null,
        model: model,
        apiBase: apiBase,
        apiKey: apiKey,
      );

      final res = await _repo.extractStructuredData(req);
      state = state.copyWith(
        isExtracting: false,
        extractedData: res.extractedData,
        statusMessage: 'Extraction complete.',
      );
    } catch (e) {
      state = state.copyWith(
        isExtracting: false,
        error: e.toString(),
      );
    }
  }
}
