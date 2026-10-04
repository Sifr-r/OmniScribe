import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/features/documents/document_repository.dart';
import 'package:omniscribe_client/features/documents/documents_models.dart';
import 'package:omniscribe_client/features/documents/export_notifier.dart';
import 'package:omniscribe_client/features/documents/extraction_notifier.dart';
import 'package:omniscribe_client/features/glossary/glossary_models.dart';
import 'package:omniscribe_client/features/glossary/glossary_notifier.dart';
import 'package:omniscribe_client/features/glossary/glossary_repository.dart';
import 'package:omniscribe_client/features/transcription/transcription_models.dart';
import 'package:omniscribe_client/features/transcription/transcription_notifier.dart';
import 'package:omniscribe_client/features/transcription/transcription_repository.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/features/workstation/ocr_repository.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';

class _GlossaryRepository extends Mock implements GlossaryRepository {}

class _TranscriptionRepository extends Mock
    implements TranscriptionRepository {}

class _DocumentRepository extends Mock implements DocumentRepository {}

class _OcrRepository extends Mock implements OcrRepository {}

GlossaryListItem library(String id) => GlossaryListItem(
      id: id,
      name: id,
      format: GlossaryFormat.csv,
      entryCount: 1,
      enabled: true,
      priority: 0,
      group: 'default',
    );

void main() {
  setUpAll(() {
    registerFallbackValue(const TranscriptionRequest());
    registerFallbackValue(const ExtractionRequest());
    registerFallbackValue(const ExportDocxRequest(text: 'fallback'));
    registerFallbackValue(const ExportBlockTreeRequest(
      textArtifactId: 'fallback-id',
      textArtifactToken: 'fallback-token',
    ));
    registerFallbackValue(Uint8List(0));
  });

  test('later glossary selection wins when requests complete out of order',
      () async {
    final repository = _GlossaryRepository();
    final first = Completer<List<GlossaryEntry>>();
    final second = Completer<List<GlossaryEntry>>();
    when(() => repository.getGlossaryEntries('a'))
        .thenAnswer((_) => first.future);
    when(() => repository.getGlossaryEntries('b'))
        .thenAnswer((_) => second.future);
    final container = ProviderContainer(
        overrides: [glossaryRepositoryProvider.overrideWithValue(repository)]);
    addTearDown(container.dispose);
    final notifier = container.read(glossaryProvider.notifier);
    final requestA = notifier.loadEntries(library('a'));
    final requestB = notifier.loadEntries(library('b'));
    second.complete([const GlossaryEntry(source: 'b', target: 'B')]);
    await requestB;
    first.complete([const GlossaryEntry(source: 'a', target: 'A')]);
    await requestA;
    final state = container.read(glossaryProvider);
    expect(state.selectedLibrary?.id, 'b');
    expect(state.entries.single.source, 'b');
    expect(state.isLoading, isFalse);
  });

  test('inline JSON pairs use a supported .json upload filename', () async {
    final repository = _GlossaryRepository();
    when(() => repository.importGlossaryFile(
            fileBytes: any(named: 'fileBytes'),
            filename: any(named: 'filename')))
        .thenAnswer((_) async => const GlossaryImportJobResponse(
            format: GlossaryFormat.jsonPairs, name: 'terms', entryCount: 1));
    when(() => repository.getGlossaryLibraries()).thenAnswer((_) async => []);
    when(() => repository.getMergedGlossaryEntries())
        .thenAnswer((_) async => []);
    final container = ProviderContainer(
        overrides: [glossaryRepositoryProvider.overrideWithValue(repository)]);
    addTearDown(container.dispose);
    await container.read(glossaryProvider.notifier).importGlossaryJson(
        format: GlossaryFormat.jsonPairs, text: '{"source":"target"}');
    verify(() => repository.importGlossaryFile(
        fileBytes: any(named: 'fileBytes'),
        filename: 'glossary.json')).called(1);
  });

  test(
      'transcription preserves local engine, ignores repeated submit and stale audio result',
      () async {
    final repository = _TranscriptionRepository();
    final response = Completer<TranscriptionResponse>();
    when(() => repository.transcribe(
        audioBytes: any(named: 'audioBytes'),
        filename: any(named: 'filename'),
        request: any(named: 'request'))).thenAnswer((_) => response.future);
    final container = ProviderContainer(overrides: [
      transcriptionRepositoryProvider.overrideWithValue(repository)
    ]);
    addTearDown(container.dispose);
    final notifier = container.read(transcriptionProvider.notifier);
    notifier.setAudio(Uint8List.fromList([1]), 'first.wav');
    notifier.setEngine('faster-whisper');
    final pending = notifier.transcribe();
    await notifier.transcribe();
    final captured = verify(() => repository.transcribe(
        audioBytes: any(named: 'audioBytes'),
        filename: 'first.wav',
        request: captureAny(named: 'request'))).captured;
    expect((captured.single as TranscriptionRequest).engine?.value,
        'faster-whisper');
    notifier.setAudio(Uint8List.fromList([2]), 'second.wav');
    response.complete(const TranscriptionResponse(text: 'Old file'));
    await pending;
    expect(container.read(transcriptionProvider).audioFilename, 'second.wav');
    expect(container.read(transcriptionProvider).result, isNull);
  });

  test('extraction ignores duplicate submit and completion after disposal',
      () async {
    final repository = _DocumentRepository();
    final response = Completer<ExtractionResponse>();
    when(() => repository.extractStructuredData(any()))
        .thenAnswer((_) => response.future);
    final container = ProviderContainer(
        overrides: [documentRepositoryProvider.overrideWithValue(repository)]);
    final notifier = container.read(extractionProvider.notifier)
      ..setInputText('invoice');
    final pending = notifier.extract();
    await notifier.extract();
    verify(() => repository.extractStructuredData(any())).called(1);
    container.dispose();
    response.complete(const ExtractionResponse(extractedData: {'total': 1}));
    await pending;
  });

  test('export preparation escapes HTML and rejects missing artifact before IO',
      () async {
    final repository = _DocumentRepository();
    final ocr = _OcrRepository();
    when(() => ocr.renderDocumentPagePreview(
        fileBytes: any(named: 'fileBytes'),
        filename: any(named: 'filename'),
        pageIndex: any(named: 'pageIndex'),
        docId: any(named: 'docId'))).thenAnswer((_) async => null);
    final container = ProviderContainer(overrides: [
      documentRepositoryProvider.overrideWithValue(repository),
      ocrRepositoryProvider.overrideWithValue(ocr)
    ]);
    addTearDown(container.dispose);
    container
        .read(workstationProvider.notifier)
        .loadDocument(Uint8List.fromList([1]), '<scan>.pdf');
    container.read(workstationProvider.notifier).addOrUpdateBBox(
          0,
          const BBoxItem(
            blockId: 'b0',
            page: 0,
            block: 0,
            bbox: [0, 0, 1, 1],
            text: '<recognized>',
          ),
        );
    final notifier = container.read(documentExportProvider.notifier);
    final html = await notifier.prepare(ExportFormat.html);
    expect(utf8.decode(html.bytes), contains('&lt;scan&gt;.pdf'));
    expect(utf8.decode(html.bytes), contains('&lt;recognized&gt;'));
    await expectLater(
        notifier.prepare(ExportFormat.docxTree), throwsFormatException);
    expect(container.read(documentExportProvider), isFalse);
    verifyNever(() => repository.exportDocxTree(any()));
  });

  test('exports require recognized data and use processed PDF bytes and suffix',
      () async {
    final repository = _DocumentRepository();
    final container = ProviderContainer(overrides: [
      documentRepositoryProvider.overrideWithValue(repository)
    ]);
    addTearDown(container.dispose);
    final document = container.read(workstationProvider.notifier);
    document.loadDocument(Uint8List.fromList([1]), 'scan.png');
    final exporter = container.read(documentExportProvider.notifier);
    for (final format in ExportFormat.values) {
      await expectLater(exporter.prepare(format), throwsFormatException);
      expect(container.read(documentExportProvider), isFalse);
    }
    verifyNever(() => repository.exportDocx(any()));
    final pdf = Uint8List.fromList([37, 80, 68, 70]);
    document.adoptProcessedDocument(pdf);
    final prepared = await exporter.prepare(ExportFormat.searchablePdf);
    expect(identical(prepared.bytes, pdf), isTrue);
    expect(prepared.filename, 'scan.pdf');
    document.loadDocument(Uint8List.fromList([2]), 'replacement.png');
    await expectLater(exporter.prepare(ExportFormat.searchablePdf),
        throwsFormatException);
  });

  test('document replacement invalidates an in-flight DOCX export', () async {
    final repository = _DocumentRepository();
    final response = Completer<Uint8List>();
    when(() => repository.exportDocx(any())).thenAnswer((_) => response.future);
    final container = ProviderContainer(overrides: [
      documentRepositoryProvider.overrideWithValue(repository)
    ]);
    addTearDown(container.dispose);
    final document = container.read(workstationProvider.notifier);
    document.loadDocument(Uint8List.fromList([1]), 'first.png');
    document.addOrUpdateBBox(
      0,
      const BBoxItem(
        blockId: 'b0',
        page: 0,
        block: 0,
        bbox: [0, 0, 1, 1],
        text: 'recognized',
      ),
    );
    final pending = container
        .read(documentExportProvider.notifier)
        .prepare(ExportFormat.docx);
    document.loadDocument(Uint8List.fromList([2]), 'second.png');
    final rejected = expectLater(pending, throwsStateError);
    response.complete(Uint8List.fromList([1, 2, 3]));
    await rejected;
    expect(container.read(documentExportProvider), isFalse);
  });

  test(
      'document preview error completion after disposal does not touch provider state',
      () async {
    final ocr = _OcrRepository();
    final preview = Completer<PagePreviewResult?>();
    when(() => ocr.renderDocumentPagePreview(
        fileBytes: any(named: 'fileBytes'),
        filename: any(named: 'filename'),
        pageIndex: any(named: 'pageIndex'),
        docId: any(named: 'docId'))).thenAnswer((_) => preview.future);
    final container = ProviderContainer(
        overrides: [ocrRepositoryProvider.overrideWithValue(ocr)]);
    container
        .read(workstationProvider.notifier)
        .loadDocument(Uint8List.fromList([1]), 'scan.pdf');
    container.dispose();
    preview.completeError(StateError('Delayed preview failure'));
    await Future<void>.delayed(Duration.zero);
  });
}
