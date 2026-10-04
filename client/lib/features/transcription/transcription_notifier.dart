import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'transcription_models.dart';
import 'transcription_repository.dart';
import 'transcription_state.dart';

export 'transcription_state.dart';

final transcriptionProvider =
    NotifierProvider<TranscriptionNotifier, TranscriptionState>(
  TranscriptionNotifier.new,
);

class TranscriptionNotifier extends Notifier<TranscriptionState> {
  TranscriptionRepository get _repo =>
      ref.read(transcriptionRepositoryProvider);
  Timer? _playbackTimer;
  int _runEpoch = 0;

  @override
  TranscriptionState build() {
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
    ++_runEpoch;
    stopPlayback();
    state = state.copyWith(
      audioBytes: bytes,
      audioFilename: filename,
      totalDuration: duration ?? 0.0,
      currentPlaybackTime: 0.0,
      isPlaying: false,
      clearError: true,
      clearResult: true,
      isTranscribing: false,
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
    ++_runEpoch;
    stopPlayback();
    state = state.copyWith(
      clearAudio: true,
      clearResult: true,
      isTranscribing: false,
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
    if (state.isTranscribing) return;
    final runId = ++_runEpoch;
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

      if (!ref.mounted || runId != _runEpoch) return;
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
      if (!ref.mounted || runId != _runEpoch) return;
      state = state.copyWith(
        isTranscribing: false,
        errorMessage: e.toString(),
      );
    }
  }
}
