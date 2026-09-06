import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/data/models/feature_models.dart';
import 'package:omniscribe_client/data/models/job_record.dart';
import 'package:omniscribe_client/data/providers/features_notifier.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/repositories/feature_repository.dart';

class _MockFeatureRepository extends Mock implements FeatureRepository {}

class _FakeTranslationRequest extends Fake implements TranslationRequest {}

class _FakeExtractionRequest extends Fake implements ExtractionRequest {}

void main() {
  setUpAll(() {
    registerFallbackValue(_FakeTranslationRequest());
    registerFallbackValue(_FakeExtractionRequest());
    registerFallbackValue(Uint8List(0));
    registerFallbackValue(GlossaryFormat.csv);
  });

  late _MockFeatureRepository repo;

  setUp(() {
    repo = _MockFeatureRepository();
  });

  ProviderContainer makeContainer() {
    return ProviderContainer(
      overrides: [
        featureRepositoryProvider.overrideWithValue(repo),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // TranslationNotifier Tests
  // ---------------------------------------------------------------------------
  group('TranslationNotifier', () {
    test('build returns initial state', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      final state = container.read(translationProvider);
      expect(state.sourceText, isEmpty);
      expect(state.targetLanguage, 'French');
      expect(state.isTranslating, isFalse);
      expect(state.error, isNull);
    });

    test('mutators update state properly', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(translationProvider.notifier);

      notifier.setSourceText('Hello world');
      notifier.setTargetLanguage('German');
      notifier.setSelectedModel('qwen-2.5');
      notifier.setUseNllb(true);

      var state = container.read(translationProvider);
      expect(state.sourceText, 'Hello world');
      expect(state.targetLanguage, 'German');
      expect(state.selectedModel, 'qwen-2.5');
      expect(state.useNllb, isTrue);

      notifier.clearSourceText();
      state = container.read(translationProvider);
      expect(state.sourceText, isEmpty);
    });

    test('translate validates non-empty source text', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(translationProvider.notifier);

      await notifier.translate();

      final state = container.read(translationProvider);
      expect(state.error, contains('Please provide source text'));
      expect(state.isTranslating, isFalse);
      verifyNever(() => repo.translate(any()));
    });

    test('translate with standard model updates translatedOutput on success',
        () async {
      when(() => repo.translate(any())).thenAnswer(
        (_) async => const TranslationResponse(translatedText: 'Bonjour monde'),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(translationProvider.notifier);

      notifier.setSourceText('Hello world');
      await notifier.translate();

      final state = container.read(translationProvider);
      expect(state.translatedOutput, 'Bonjour monde');
      expect(state.isTranslating, isFalse);
      expect(state.error, isNull);
      verify(() => repo.translate(any())).called(1);
    });

    test('translate with NLLB engine calls translateNllb', () async {
      when(
        () => repo.translateNllb(
          text: any(named: 'text'),
          targetLanguage: any(named: 'targetLanguage'),
        ),
      ).thenAnswer(
        (_) async => const NLLBTranslationResponse(
          translatedText: 'Hallo Welt',
          sourceLang: 'en',
          targetLang: 'de',
        ),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(translationProvider.notifier);

      notifier.setSourceText('Hello world');
      notifier.setTargetLanguage('German');
      notifier.setUseNllb(true);
      await notifier.translate();

      final state = container.read(translationProvider);
      expect(state.translatedOutput, 'Hallo Welt');
      expect(state.isTranslating, isFalse);
      verify(
        () => repo.translateNllb(text: 'Hello world', targetLanguage: 'German'),
      ).called(1);
    });

    test('translate handles error gracefully', () async {
      when(() => repo.translate(any())).thenThrow(Exception('API error 500'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(translationProvider.notifier);

      notifier.setSourceText('Hello world');
      await notifier.translate();

      final state = container.read(translationProvider);
      expect(state.isTranslating, isFalse);
      expect(state.error, contains('API error 500'));
      expect(state.translatedOutput, isEmpty);
    });

    test('translateAsync queues job and checkTranslationStatus polls result',
        () async {
      when(() => repo.translateAsync(any())).thenAnswer(
        (_) async => const ProcessResponse(
          jobId: 'trans-job-1',
          status: 'queued',
        ),
      );
      when(() => repo.getTranslationStatus('trans-job-1')).thenAnswer(
        (_) async => const TranslationJobStatusResponse(
          jobId: 'trans-job-1',
          state: 'SUCCESS',
          result: 'Async Bonjour',
        ),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(translationProvider.notifier);

      notifier.setSourceText('Hello async');
      final jobId = await notifier.translateAsync();

      expect(jobId, 'trans-job-1');
      var state = container.read(translationProvider);
      expect(state.asyncJobId, 'trans-job-1');
      expect(state.asyncStatus, contains('queued'));

      await notifier.checkTranslationStatus('trans-job-1');
      state = container.read(translationProvider);
      expect(state.translatedOutput, 'Async Bonjour');
      expect(state.isTranslating, isFalse);
    });
  });

  // ---------------------------------------------------------------------------
  // TranscriptionNotifier Tests
  // ---------------------------------------------------------------------------
  group('TranscriptionNotifier', () {
    test('build returns initial state', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      final state = container.read(transcriptionProvider);
      expect(state.audioBytes, isNull);
      expect(state.engine, 'api');
      expect(state.model, 'whisper-1');
      expect(state.isTranscribing, isFalse);
    });

    test('setAudio and mutators update state', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(transcriptionProvider.notifier);

      final bytes = Uint8List.fromList([1, 2, 3]);
      notifier.setAudio(bytes, 'voice.wav', duration: 60.0);
      notifier.setEngine('faster-whisper');
      notifier.setModel('whisper-base');
      notifier.setLanguage('en');
      notifier.setPrompt('legal terms');

      final state = container.read(transcriptionProvider);
      expect(state.audioBytes, bytes);
      expect(state.audioFilename, 'voice.wav');
      expect(state.totalDuration, 60.0);
      expect(state.engine, 'faster-whisper');
      expect(state.model, 'whisper-base');
      expect(state.language, 'en');
      expect(state.prompt, 'legal terms');
    });

    test('transcribe validates audio selection', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(transcriptionProvider.notifier);

      await notifier.transcribe();

      final state = container.read(transcriptionProvider);
      expect(state.errorMessage, contains('Please select an audio file'));
    });

    test('transcribe success populates result and segments', () async {
      final segs = [
        const TranscriptionSegment(id: 1, start: 0.0, end: 5.0, text: 'Hello')
      ];
      when(
        () => repo.transcribe(
          audioBytes: any(named: 'audioBytes'),
          filename: any(named: 'filename'),
          request: any(named: 'request'),
        ),
      ).thenAnswer(
        (_) async => TranscriptionResponse(
          text: 'Hello',
          segments: segs,
          duration: 5.0,
        ),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(transcriptionProvider.notifier);

      notifier.setAudio(Uint8List.fromList([1]), 'test.wav');
      await notifier.transcribe();

      final state = container.read(transcriptionProvider);
      expect(state.result?.text, 'Hello');
      expect(state.result?.segments.length, 1);
      expect(state.isTranscribing, isFalse);
      expect(state.errorMessage, isNull);
    });

    test(
        'transcribe on error sets errorMessage and does not fabricate segments',
        () async {
      when(
        () => repo.transcribe(
          audioBytes: any(named: 'audioBytes'),
          filename: any(named: 'filename'),
          request: any(named: 'request'),
        ),
      ).thenThrow(Exception('Backend unavailable'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(transcriptionProvider.notifier);

      notifier.setAudio(Uint8List.fromList([1]), 'test.wav');
      await notifier.transcribe();

      final state = container.read(transcriptionProvider);
      expect(state.isTranscribing, isFalse);
      expect(state.result, isNull);
      expect(state.errorMessage, contains('Backend unavailable'));
    });

    test('playback helpers update time and active segment', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(transcriptionProvider.notifier);

      const seg1 =
          TranscriptionSegment(id: 1, start: 0.0, end: 5.0, text: 'First');
      const seg2 =
          TranscriptionSegment(id: 2, start: 5.0, end: 10.0, text: 'Second');
      notifier.setResult(
        const TranscriptionResponse(
          text: 'First Second',
          segments: [seg1, seg2],
          duration: 10.0,
        ),
      );

      notifier.setPlaybackTime(2.0);
      expect(container.read(transcriptionProvider).activeSegmentId, 1);

      notifier.setPlaybackTime(7.0);
      expect(container.read(transcriptionProvider).activeSegmentId, 2);

      notifier.seekToSegment(seg1);
      final state = container.read(transcriptionProvider);
      expect(state.currentPlaybackTime, 0.0);
      expect(state.activeSegmentId, 1);
      expect(state.isPlaying, isTrue);

      notifier.pausePlayback();
      expect(container.read(transcriptionProvider).isPlaying, isFalse);

      notifier.togglePlayback();
      expect(container.read(transcriptionProvider).isPlaying, isTrue);

      notifier.stopPlayback();
      expect(container.read(transcriptionProvider).isPlaying, isFalse);
    });

    test('startPlayback advances currentPlaybackTime until totalDuration',
        () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(transcriptionProvider.notifier);

      notifier.setAudio(Uint8List.fromList([1, 2]), 'audio.wav', duration: 0.2);
      notifier.startPlayback();
      expect(container.read(transcriptionProvider).isPlaying, isTrue);

      await Future<void>.delayed(const Duration(milliseconds: 400));

      final state = container.read(transcriptionProvider);
      expect(state.isPlaying, isFalse);
      expect(state.currentPlaybackTime, 0.0);
    });
  });

  // ---------------------------------------------------------------------------
  // GlossaryNotifier Tests
  // ---------------------------------------------------------------------------
  group('GlossaryNotifier', () {
    test('loadLibraries populates state on success', () async {
      const libs = [
        GlossaryListItem(
          id: 'lib-1',
          name: 'Medical',
          format: GlossaryFormat.csv,
          entryCount: 50,
          enabled: true,
          priority: 1,
          group: 'default',
        )
      ];
      when(() => repo.getGlossaryLibraries()).thenAnswer((_) async => libs);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(glossaryProvider.notifier);

      await notifier.loadLibraries();

      final state = container.read(glossaryProvider);
      expect(state.libraries, libs);
      expect(state.isLoading, isFalse);
      expect(state.error, isNull);
    });

    test('loadEntries populates entries and sets activeViewIndex to 1',
        () async {
      const lib = GlossaryListItem(
        id: 'lib-1',
        name: 'Legal',
        format: GlossaryFormat.jsonPairs,
        entryCount: 1,
        enabled: true,
        priority: 1,
        group: 'default',
      );
      const entries = [
        GlossaryEntry(source: 'arbitration', target: 'arbitrage')
      ];

      when(() => repo.getGlossaryEntries('lib-1'))
          .thenAnswer((_) async => entries);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(glossaryProvider.notifier);

      await notifier.loadEntries(lib);

      final state = container.read(glossaryProvider);
      expect(state.selectedLibrary, lib);
      expect(state.entries, entries);
      expect(state.activeViewIndex, 1);
      expect(state.isLoading, isFalse);
    });

    test('toggleLibrary and deleteLibrary mutate library list', () async {
      const lib = GlossaryListItem(
        id: 'lib-1',
        name: 'Legal',
        format: GlossaryFormat.jsonPairs,
        entryCount: 1,
        enabled: true,
        priority: 1,
        group: 'default',
      );

      when(() => repo.getGlossaryLibraries()).thenAnswer((_) async => [lib]);
      when(() => repo.toggleGlossaryLibrary('lib-1', false))
          .thenAnswer((_) async => true);
      when(() => repo.deleteGlossaryLibrary('lib-1'))
          .thenAnswer((_) async => true);
      when(() => repo.getMergedGlossaryEntries())
          .thenAnswer((_) async => <GlossaryEntry>[]);

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(glossaryProvider.notifier);

      await notifier.loadLibraries();
      await notifier.toggleLibrary(lib, false);

      var state = container.read(glossaryProvider);
      expect(state.libraries.first.enabled, isFalse);

      await notifier.deleteLibrary('lib-1');
      state = container.read(glossaryProvider);
      expect(state.libraries, isEmpty);
    });

    test(
        'loadLibraries on error sets error and does not fabricate fallback libraries',
        () async {
      when(() => repo.getGlossaryLibraries())
          .thenThrow(Exception('Failed to fetch glossaries'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(glossaryProvider.notifier);

      await notifier.loadLibraries();

      final state = container.read(glossaryProvider);
      expect(state.libraries, isEmpty);
      expect(state.isLoading, isFalse);
      expect(state.error, contains('Failed to fetch glossaries'));
    });

    test(
        'loadEntries on error sets error and does not fabricate fallback entries',
        () async {
      const lib = GlossaryListItem(
        id: 'lib-1',
        name: 'Legal',
        format: GlossaryFormat.jsonPairs,
        entryCount: 1,
        enabled: true,
        priority: 1,
        group: 'default',
      );

      when(() => repo.getGlossaryEntries('lib-1'))
          .thenThrow(Exception('Failed to fetch entries'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(glossaryProvider.notifier);

      await notifier.loadEntries(lib);

      final state = container.read(glossaryProvider);
      expect(state.entries, isEmpty);
      expect(state.isLoading, isFalse);
      expect(state.error, contains('Failed to fetch entries'));
    });
  });

  // ---------------------------------------------------------------------------
  // ExtractionNotifier Tests
  // ---------------------------------------------------------------------------
  group('ExtractionNotifier', () {
    test('build returns initial state', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      final state = container.read(extractionProvider);
      expect(state.inputText, isEmpty);
      expect(state.selectedTemplate, 'invoice');
      expect(state.isExtracting, isFalse);
    });

    test('extract validates empty input', () async {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(extractionProvider.notifier);

      await notifier.extract();

      final state = container.read(extractionProvider);
      expect(state.error, contains('Please enter or paste input text'));
      expect(state.isExtracting, isFalse);
    });

    test('extract success populates extractedData', () async {
      const responseData = {'invoice_num': 'INV-101', 'total': 500};
      when(() => repo.extractStructuredData(any())).thenAnswer(
        (_) async => const ExtractionResponse(extractedData: responseData),
      );

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(extractionProvider.notifier);

      notifier.setInputText(r'Invoice #INV-101 for $500');
      await notifier.extract();

      final state = container.read(extractionProvider);
      expect(state.extractedData, responseData);
      expect(state.statusMessage, 'Extraction complete.');
      expect(state.isExtracting, isFalse);
      expect(state.error, isNull);
    });

    test('extract on error sets error message without fabricating data',
        () async {
      when(() => repo.extractStructuredData(any()))
          .thenThrow(Exception('LLM failure'));

      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(extractionProvider.notifier);

      notifier.setInputText('Invoice text here');
      await notifier.extract();

      final state = container.read(extractionProvider);
      expect(state.isExtracting, isFalse);
      expect(state.extractedData, isNull);
      expect(state.error, contains('LLM failure'));
    });
  });
}
