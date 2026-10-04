import 'package:flutter/foundation.dart';
import 'package:omniscribe_client/features/transcription/transcription_models.dart';

/// Immutable state for the Voice & Audio Transcription feature vertical.
@immutable
class TranscriptionState {
  const TranscriptionState({
    this.audioBytes,
    this.audioFilename,
    this.engine = 'api',
    this.model = 'whisper-1',
    this.language,
    this.prompt,
    this.isTranscribing = false,
    this.result,
    this.errorMessage,
    this.isPlaying = false,
    this.currentPlaybackTime = 0.0,
    this.totalDuration = 0.0,
    this.activeSegmentId,
  });

  const TranscriptionState.initial()
      : audioBytes = null,
        audioFilename = null,
        engine = 'api',
        model = 'whisper-1',
        language = null,
        prompt = null,
        isTranscribing = false,
        result = null,
        errorMessage = null,
        isPlaying = false,
        currentPlaybackTime = 0.0,
        totalDuration = 0.0,
        activeSegmentId = null;

  final Uint8List? audioBytes;
  final String? audioFilename;
  final String engine;
  final String model;
  final String? language;
  final String? prompt;
  final bool isTranscribing;
  final TranscriptionResponse? result;
  final String? errorMessage;
  final bool isPlaying;
  final double currentPlaybackTime;
  final double totalDuration;
  final int? activeSegmentId;

  String? get error => errorMessage;

  TranscriptionState copyWith({
    Uint8List? audioBytes,
    String? audioFilename,
    String? engine,
    String? model,
    String? language,
    String? prompt,
    bool? isTranscribing,
    TranscriptionResponse? result,
    String? errorMessage,
    bool? isPlaying,
    double? currentPlaybackTime,
    double? totalDuration,
    int? activeSegmentId,
    bool clearAudio = false,
    bool clearResult = false,
    bool clearError = false,
    bool clearActiveSegment = false,
    bool clearLanguage = false,
    bool clearPrompt = false,
  }) {
    return TranscriptionState(
      audioBytes: clearAudio ? null : (audioBytes ?? this.audioBytes),
      audioFilename: clearAudio ? null : (audioFilename ?? this.audioFilename),
      engine: engine ?? this.engine,
      model: model ?? this.model,
      language: clearLanguage ? null : (language ?? this.language),
      prompt: clearPrompt ? null : (prompt ?? this.prompt),
      isTranscribing: isTranscribing ?? this.isTranscribing,
      result: clearResult ? null : (result ?? this.result),
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
      isPlaying: isPlaying ?? this.isPlaying,
      currentPlaybackTime: currentPlaybackTime ?? this.currentPlaybackTime,
      totalDuration: totalDuration ?? this.totalDuration,
      activeSegmentId:
          clearActiveSegment ? null : (activeSegmentId ?? this.activeSegmentId),
    );
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is TranscriptionState &&
        listEquals(other.audioBytes, audioBytes) &&
        other.audioFilename == audioFilename &&
        other.engine == engine &&
        other.model == model &&
        other.language == language &&
        other.prompt == prompt &&
        other.isTranscribing == isTranscribing &&
        other.result == result &&
        other.errorMessage == errorMessage &&
        other.isPlaying == isPlaying &&
        other.currentPlaybackTime == currentPlaybackTime &&
        other.totalDuration == totalDuration &&
        other.activeSegmentId == activeSegmentId;
  }

  @override
  int get hashCode => Object.hash(
        audioBytes != null ? Object.hashAll(audioBytes!) : null,
        audioFilename,
        engine,
        model,
        language,
        prompt,
        isTranscribing,
        result,
        errorMessage,
        isPlaying,
        currentPlaybackTime,
        totalDuration,
        activeSegmentId,
      );
}
