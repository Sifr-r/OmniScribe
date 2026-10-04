import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'translation_models.dart';
import 'translation_repository.dart';
import 'translation_state.dart';

export 'translation_state.dart';

final translationProvider =
    NotifierProvider<TranslationNotifier, TranslationState>(
  TranslationNotifier.new,
);

class TranslationNotifier extends Notifier<TranslationState> {
  TranslationRepository get _repo => ref.read(translationRepositoryProvider);
  Timer? _pollTimer;
  String? _resultToken;
  int _runEpoch = 0;
  bool _checkingStatus = false;

  @override
  TranslationState build() {
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
    if (state.isTranslating) return;
    final runId = ++_runEpoch;
    stopPolling();
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
        if (!ref.mounted || runId != _runEpoch) return;
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
        if (!ref.mounted || runId != _runEpoch) return;
        state = state.copyWith(
          translatedOutput: res.translatedText,
          isTranslating: false,
        );
      }
    } catch (e) {
      if (!ref.mounted || runId != _runEpoch) return;
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
    if (state.isTranslating) return null;
    final runId = ++_runEpoch;
    stopPolling();
    _resultToken = null;
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
      if (!ref.mounted || runId != _runEpoch) return null;
      _resultToken = res.resultToken;
      state = state.copyWith(
        asyncJobId: res.jobId,
        asyncStatus: 'Job ${res.jobId} queued. Polling progress...',
      );
      if (autoPoll) {
        startPolling(res.jobId);
      }
      return res.jobId;
    } catch (e) {
      if (!ref.mounted || runId != _runEpoch) return null;
      state = state.copyWith(
        isTranslating: false,
        error: e.toString(),
        asyncStatus: 'Async translation failed: $e',
      );
      return null;
    }
  }

  Future<void> checkTranslationStatus(String jobId) async {
    if (_checkingStatus) return;
    final runId = _runEpoch;
    _checkingStatus = true;
    try {
      final status = await _repo.getTranslationStatus(jobId);
      if (!ref.mounted || runId != _runEpoch) return;
      final stateStr = status.state.toUpperCase();

      if (stateStr == 'SUCCESS' || stateStr == 'COMPLETED') {
        final token = _resultToken;
        final result = token == null
            ? status.result
            : (await _repo.getTranslationResult(jobId, token)).translatedText;
        if (!ref.mounted || runId != _runEpoch) return;
        final translatedText =
            result is Map ? result['translated_text'] : result;
        if (translatedText is! String) {
          throw const FormatException(
              'Translation completed without translated text.');
        }
        state = state.copyWith(
          isTranslating: false,
          translatedOutput: translatedText,
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
      if (!ref.mounted || runId != _runEpoch) return;
      state = state.copyWith(
        isTranslating: false,
        error: e.toString(),
        asyncStatus: 'Polling error: $e',
      );
      stopPolling();
    } finally {
      _checkingStatus = false;
    }
  }
}
