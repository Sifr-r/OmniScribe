import 'package:flutter/foundation.dart';

/// Immutable state for the Translation feature vertical.
@immutable
class TranslationState {
  const TranslationState({
    this.sourceText = '',
    this.targetLanguage = 'French',
    this.selectedModel = '',
    this.useNllb = false,
    this.isTranslating = false,
    this.translatedOutput = '',
    this.error,
    this.asyncJobId,
    this.asyncStatus,
  });

  const TranslationState.initial()
      : sourceText = '',
        targetLanguage = 'French',
        selectedModel = '',
        useNllb = false,
        isTranslating = false,
        translatedOutput = '',
        error = null,
        asyncJobId = null,
        asyncStatus = null;

  final String sourceText;
  final String targetLanguage;
  final String selectedModel;
  final bool useNllb;
  final bool isTranslating;
  final String translatedOutput;
  final String? error;
  final String? asyncJobId;
  final String? asyncStatus;

  TranslationState copyWith({
    String? sourceText,
    String? targetLanguage,
    String? selectedModel,
    bool? useNllb,
    bool? isTranslating,
    String? translatedOutput,
    String? error,
    String? asyncJobId,
    String? asyncStatus,
    bool clearError = false,
    bool clearAsyncJobId = false,
    bool clearAsyncStatus = false,
  }) {
    return TranslationState(
      sourceText: sourceText ?? this.sourceText,
      targetLanguage: targetLanguage ?? this.targetLanguage,
      selectedModel: selectedModel ?? this.selectedModel,
      useNllb: useNllb ?? this.useNllb,
      isTranslating: isTranslating ?? this.isTranslating,
      translatedOutput: translatedOutput ?? this.translatedOutput,
      error: clearError ? null : (error ?? this.error),
      asyncJobId: clearAsyncJobId ? null : (asyncJobId ?? this.asyncJobId),
      asyncStatus: clearAsyncStatus ? null : (asyncStatus ?? this.asyncStatus),
    );
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is TranslationState &&
        other.sourceText == sourceText &&
        other.targetLanguage == targetLanguage &&
        other.selectedModel == selectedModel &&
        other.useNllb == useNllb &&
        other.isTranslating == isTranslating &&
        other.translatedOutput == translatedOutput &&
        other.error == error &&
        other.asyncJobId == asyncJobId &&
        other.asyncStatus == asyncStatus;
  }

  @override
  int get hashCode => Object.hash(
        sourceText,
        targetLanguage,
        selectedModel,
        useNllb,
        isTranslating,
        translatedOutput,
        error,
        asyncJobId,
        asyncStatus,
      );
}
