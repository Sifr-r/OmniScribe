import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/data/models/models.dart';
import 'package:omniscribe_client/data/providers/features_state.dart';

void main() {
  group('TranslationState', () {
    test('initial state returns sane defaults', () {
      const state = TranslationState.initial();

      expect(state.sourceText, isEmpty);
      expect(state.targetLanguage, 'French');
      expect(state.selectedModel, isEmpty);
      expect(state.useNllb, isFalse);
      expect(state.isTranslating, isFalse);
      expect(state.translatedOutput, isEmpty);
      expect(state.error, isNull);
      expect(state.asyncJobId, isNull);
      expect(state.asyncStatus, isNull);
    });

    test('copyWith preserves untouched fields', () {
      const before = TranslationState(
        sourceText: 'Hello',
        targetLanguage: 'Spanish',
        selectedModel: 'gpt-4',
        useNllb: true,
        isTranslating: true,
        translatedOutput: 'Hola',
        error: 'err',
        asyncJobId: 'job-1',
        asyncStatus: 'queued',
      );

      final after = before.copyWith(targetLanguage: 'German');

      expect(after.targetLanguage, 'German');
      expect(after.sourceText, 'Hello');
      expect(after.selectedModel, 'gpt-4');
      expect(after.useNllb, isTrue);
      expect(after.isTranslating, isTrue);
      expect(after.translatedOutput, 'Hola');
      expect(after.error, 'err');
      expect(after.asyncJobId, 'job-1');
      expect(after.asyncStatus, 'queued');
    });

    test('copyWith clear flags reset nullable fields', () {
      const before = TranslationState(
        error: 'failed',
        asyncJobId: 'job-123',
        asyncStatus: 'pending',
      );

      final after = before.copyWith(
        clearError: true,
        clearAsyncJobId: true,
        clearAsyncStatus: true,
      );

      expect(after.error, isNull);
      expect(after.asyncJobId, isNull);
      expect(after.asyncStatus, isNull);
    });

    test('value equality and hashCode', () {
      const a = TranslationState(
        sourceText: 'Text',
        targetLanguage: 'German',
      );
      const b = TranslationState(
        sourceText: 'Text',
        targetLanguage: 'German',
      );
      const c = TranslationState(
        sourceText: 'Text',
        targetLanguage: 'French',
      );

      expect(a, equals(b));
      expect(a.hashCode, equals(b.hashCode));
      expect(a, isNot(equals(c)));
    });
  });

  group('TranscriptionState', () {
    test('initial state returns sane defaults', () {
      const state = TranscriptionState.initial();

      expect(state.audioBytes, isNull);
      expect(state.audioFilename, isNull);
      expect(state.engine, 'api');
      expect(state.model, 'whisper-1');
      expect(state.language, isNull);
      expect(state.prompt, isNull);
      expect(state.isTranscribing, isFalse);
      expect(state.result, isNull);
      expect(state.errorMessage, isNull);
      expect(state.error, isNull);
      expect(state.isPlaying, isFalse);
      expect(state.currentPlaybackTime, 0.0);
      expect(state.totalDuration, 0.0);
      expect(state.activeSegmentId, isNull);
    });

    test('copyWith preserves untouched fields', () {
      final bytes = Uint8List.fromList([1, 2, 3]);
      final before = TranscriptionState(
        audioBytes: bytes,
        audioFilename: 'sample.wav',
        engine: 'faster-whisper',
        model: 'large-v3',
        language: 'en',
        prompt: 'test prompt',
        isTranscribing: true,
        errorMessage: 'error',
        isPlaying: true,
        currentPlaybackTime: 5.5,
        totalDuration: 30.0,
        activeSegmentId: 2,
      );

      final after = before.copyWith(currentPlaybackTime: 6.0);

      expect(after.currentPlaybackTime, 6.0);
      expect(after.audioBytes, bytes);
      expect(after.audioFilename, 'sample.wav');
      expect(after.engine, 'faster-whisper');
      expect(after.model, 'large-v3');
      expect(after.language, 'en');
      expect(after.prompt, 'test prompt');
      expect(after.isTranscribing, isTrue);
      expect(after.errorMessage, 'error');
      expect(after.isPlaying, isTrue);
      expect(after.totalDuration, 30.0);
      expect(after.activeSegmentId, 2);
    });

    test('copyWith clear flags reset nullable fields', () {
      final bytes = Uint8List.fromList([1, 2, 3]);
      const res = TranscriptionResponse(text: 'Hello');
      final before = TranscriptionState(
        audioBytes: bytes,
        audioFilename: 'sample.wav',
        language: 'en',
        prompt: 'vocab',
        result: res,
        errorMessage: 'failed',
        activeSegmentId: 1,
      );

      final after = before.copyWith(
        clearAudio: true,
        clearResult: true,
        clearError: true,
        clearActiveSegment: true,
        clearLanguage: true,
        clearPrompt: true,
      );

      expect(after.audioBytes, isNull);
      expect(after.audioFilename, isNull);
      expect(after.result, isNull);
      expect(after.errorMessage, isNull);
      expect(after.activeSegmentId, isNull);
      expect(after.language, isNull);
      expect(after.prompt, isNull);
    });

    test('value equality and hashCode', () {
      final bytesA = Uint8List.fromList([1, 2, 3]);
      final bytesB = Uint8List.fromList([1, 2, 3]);
      final stateA = TranscriptionState(
        audioBytes: bytesA,
        audioFilename: 'a.wav',
      );
      final stateB = TranscriptionState(
        audioBytes: bytesB,
        audioFilename: 'a.wav',
      );
      final stateC = TranscriptionState(
        audioBytes: bytesA,
        audioFilename: 'b.wav',
      );

      expect(stateA, equals(stateB));
      expect(stateA.hashCode, equals(stateB.hashCode));
      expect(stateA, isNot(equals(stateC)));
    });
  });

  group('GlossaryState', () {
    test('initial state returns sane defaults', () {
      const state = GlossaryState.initial();

      expect(state.libraries, isEmpty);
      expect(state.selectedLibrary, isNull);
      expect(state.entries, isEmpty);
      expect(state.mergedLexicon, isEmpty);
      expect(state.activeViewIndex, 0);
      expect(state.isLoading, isFalse);
      expect(state.error, isNull);
    });

    test('copyWith preserves untouched fields and clears error / library', () {
      const lib = GlossaryListItem(
        id: 'lib-1',
        name: 'Legal',
        format: GlossaryFormat.jsonPairs,
        entryCount: 5,
        enabled: true,
        priority: 1,
        group: 'default',
      );
      const entry = GlossaryEntry(source: 'hello', target: 'bonjour');

      const before = GlossaryState(
        libraries: [lib],
        selectedLibrary: lib,
        entries: [entry],
        mergedLexicon: {'hello': 'bonjour'},
        activeViewIndex: 1,
        isLoading: true,
        error: 'error',
      );

      final after = before.copyWith(
        activeViewIndex: 2,
        clearSelectedLibrary: true,
        clearError: true,
      );

      expect(after.activeViewIndex, 2);
      expect(after.selectedLibrary, isNull);
      expect(after.error, isNull);
      expect(after.libraries, [lib]);
      expect(after.entries, [entry]);
      expect(after.mergedLexicon, {'hello': 'bonjour'});
      expect(after.isLoading, isTrue);
    });

    test('value equality and hashCode', () {
      const lib = GlossaryListItem(
        id: 'lib-1',
        name: 'Legal',
        format: GlossaryFormat.jsonPairs,
        entryCount: 5,
        enabled: true,
        priority: 1,
        group: 'default',
      );

      const a = GlossaryState(libraries: [lib], activeViewIndex: 0);
      const b = GlossaryState(libraries: [lib], activeViewIndex: 0);
      const c = GlossaryState(libraries: [lib], activeViewIndex: 1);

      expect(a, equals(b));
      expect(a.hashCode, equals(b.hashCode));
      expect(a, isNot(equals(c)));
    });
  });

  group('ExtractionState', () {
    test('initial state returns sane defaults with default schema', () {
      final state = ExtractionState.initial();

      expect(state.inputText, isEmpty);
      expect(state.customSchema, contains('invoice_number'));
      expect(state.selectedTemplate, 'invoice');
      expect(state.isExtracting, isFalse);
      expect(state.extractedData, isNull);
      expect(state.statusMessage, isNull);
      expect(state.error, isNull);
    });

    test('copyWith preserves untouched fields and clears fields cleanly', () {
      final before = ExtractionState(
        inputText: 'Sample invoice',
        selectedTemplate: 'invoice',
        isExtracting: true,
        extractedData: const {'invoice_number': '123'},
        statusMessage: 'done',
        error: 'err',
      );

      final after = before.copyWith(
        selectedTemplate: 'custom',
        clearExtractedData: true,
        clearStatusMessage: true,
        clearError: true,
      );

      expect(after.selectedTemplate, 'custom');
      expect(after.extractedData, isNull);
      expect(after.statusMessage, isNull);
      expect(after.error, isNull);
      expect(after.inputText, 'Sample invoice');
      expect(after.isExtracting, isTrue);
    });

    test('value equality with nested map extraction data', () {
      final a = ExtractionState(
        inputText: 'Text',
        extractedData: {
          'key': 'value',
          'nested': [1, 2, 3],
        },
      );
      final b = ExtractionState(
        inputText: 'Text',
        extractedData: {
          'nested': [1, 2, 3],
          'key': 'value',
        },
      );
      final c = ExtractionState(
        inputText: 'Text',
        extractedData: const {
          'key': 'diff',
        },
      );

      expect(a, equals(b));
      expect(a.hashCode, equals(b.hashCode),
          reason:
              'Equal ExtractionState values must have equal hashCodes so Set/Map invariants hold.');
      expect(a, isNot(equals(c)));
      expect(a, isNot(equals(b.hashCode)),
          reason: 'hashCode is an int; never equal to an ExtractionState.');
    });
  });
}
