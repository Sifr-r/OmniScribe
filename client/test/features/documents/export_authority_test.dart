import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/documents/documents_models.dart';
import 'package:omniscribe_client/features/documents/export_notifier.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';

const _artifactId = 'artifact-run-1';
const _artifactToken = 'token-run-1';

/// A completed two-page text artifact as the server stores it:
/// `{"<page_index>": "<lines joined by \n>"}`.
const _twoPageArtifact = <String, dynamic>{
  '0': 'PAGE-ONE-LINE-A\nPAGE-ONE-LINE-B',
  '1': 'PAGE-TWO-LINE-A',
};

const _staleArtifact = <String, dynamic>{
  '0': 'STALE-RUN-TEXT',
};

/// Serves the text artifact the way `OcrRepository` does.
class _FakeOcrRepository implements OcrRepository {
  _FakeOcrRepository({this.artifactStore = const <String, String>{}});

  /// Raw text-artifact payloads keyed by artifact id.
  final Map<String, String> artifactStore;
  Future<String>? pendingArtifact;

  @override
  Future<String> getTextArtifact(String artifactId, String token) {
    final pending = pendingArtifact;
    if (pending != null) return pending;
    final payload = artifactStore[artifactId];
    if (payload == null) {
      return Future<String>.error(StateError('unknown artifact $artifactId'));
    }
    return Future<String>.value(payload);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// Fails loudly if the client fetches a text artifact, proving a code path
/// stayed independent of the artifact.
class _RecordingOcrRepository implements OcrRepository {
  _RecordingOcrRepository({
    this.artifactStore = const <String, String>{},
    required this.onArtifactRead,
  });

  final Map<String, String> artifactStore;
  final void Function(String artifactId) onArtifactRead;

  @override
  Future<String> getTextArtifact(String artifactId, String token) {
    onArtifactRead(artifactId);
    final payload = artifactStore[artifactId];
    if (payload == null) {
      return Future<String>.error(StateError('unknown artifact $artifactId'));
    }
    return Future<String>.value(payload);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// Stands in for the export service: resolves structured exports from the
/// server-side artifact store, so a payload containing both pages can only be
/// produced when the client routes through the repository with the right
/// artifact handle. An unknown artifact id throws rather than degrading.
class _FakeDocumentRepository implements DocumentRepository {
  _FakeDocumentRepository({required this.artifactStore, this.structuredFails = false});

  final Map<String, String> artifactStore;

  /// Simulates the server being unable to serve structured artifact exports.
  final bool structuredFails;

  String? lastDocxText;
  final List<ExportHtmlRequest> htmlRequests = [];
  final List<ExportBlockTreeRequest> treeRequests = [];
  final List<ExportBlockTreeRequest> docxTreeRequests = [];
  final List<ExportBlockTreeRequest> markdownRequests = [];

  Map<int, String> _pagesOf(String artifactId) {
    final raw = artifactStore[artifactId];
    if (raw == null) throw StateError('unknown artifact $artifactId');
    final decoded = jsonDecode(raw) as Map<String, dynamic>;
    return decoded.map((key, value) => MapEntry(int.parse(key), value as String));
  }

  @override
  Future<Uint8List> exportDocx(ExportDocxRequest request) async {
    lastDocxText = request.text;
    return Uint8List.fromList(const <int>[1, 2, 3]);
  }

  @override
  Future<Uint8List> exportDocxTree(ExportBlockTreeRequest request) async {
    docxTreeRequests.add(request);
    return Uint8List.fromList(const <int>[4, 5, 6]);
  }

  @override
  Future<Uint8List> exportHtml(ExportHtmlRequest request) async {
    htmlRequests.add(request);
    if (structuredFails) throw StateError('artifact html unavailable');
    final pages = _pagesOf(request.textArtifactId);
    final buffer = StringBuffer('<html><body>');
    for (final entry in pages.entries) {
      buffer.write('<div class="page" data-page="${entry.key}">');
      for (final line in entry.value.split('\n')) {
        buffer.write('<div class="block"><p>$line</p></div>');
      }
      buffer.write('</div>');
    }
    buffer.write('</body></html>');
    return Uint8List.fromList(utf8.encode(buffer.toString()));
  }

  @override
  Future<dynamic> exportBlockTree(ExportBlockTreeRequest request) async {
    treeRequests.add(request);
    if (structuredFails) throw StateError('artifact block tree unavailable');
    final pages = _pagesOf(request.textArtifactId);
    return <Map<String, Object?>>[
      for (final entry in pages.entries)
        <String, Object?>{'page': entry.key, 'text': entry.value},
    ];
  }

  @override
  Future<Uint8List> exportMarkdown(ExportBlockTreeRequest request) async {
    markdownRequests.add(request);
    if (structuredFails) throw StateError('artifact markdown unavailable');
    final text = _pagesOf(request.textArtifactId)
        .values.expand((page) => page.split('\n')).join('\n\n');
    return Uint8List.fromList(utf8.encode(request.documentArtifactId == null
        ? text : '# Preserved heading\n\n$text'));
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

String _asText(Uint8List bytes) => utf8.decode(bytes);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Map<String, String> artifactStore;
  late _FakeDocumentRepository repo;
  late _FakeOcrRepository ocrRepo;
  late ProviderContainer container;

  /// Sets up a two-page document whose completed run published [_artifactId].
  void setUpScenario({
    Map<String, dynamic> artifact = _twoPageArtifact,
    int livePages = 0,
    bool setArtifactHandle = true,
    bool adoptPdf = false,
  }) {
    artifactStore = {_artifactId: jsonEncode(artifact)};
    repo = _FakeDocumentRepository(artifactStore: artifactStore);
    ocrRepo = _FakeOcrRepository(artifactStore: artifactStore);
    container = ProviderContainer(
      overrides: [
        documentRepositoryProvider.overrideWithValue(repo),
        ocrRepositoryProvider.overrideWithValue(ocrRepo),
      ],
    );
    addTearDown(container.dispose);

    final workstation = container.read(workstationProvider.notifier);
    workstation.loadDocument(
      Uint8List.fromList([1, 2, 3]),
      'scan.pdf',
      pageCount: 2,
    );
    if (adoptPdf) {
      workstation.adoptProcessedDocument(Uint8List.fromList([9, 9, 9, 9]));
    }
    for (var page = 0; page < livePages; page++) {
      workstation.addOrUpdateBBox(
        page,
        BBoxItem(
          blockId: 'p${page}_b0',
          page: page,
          block: 0,
          bbox: const [0.05, 0.05, 0.95, 0.15],
          text: 'live-page-$page-text',
          kind: 'paragraph',
        ),
      );
    }
    if (setArtifactHandle) {
      container.read(jobOrchestrationProvider.notifier).setTextArtifact(
            textArtifactId: _artifactId,
            textArtifactToken: _artifactToken,
          );
    }
  }

  Future<String> exportText(ExportFormat format) async {
    final prepared =
        await container.read(documentExportProvider.notifier).prepare(format);
    return _asText(prepared.bytes);
  }

  /// Every text-bearing format must carry all three artifact lines.
  void expectBothPages(String text) {
    expect(text, contains('PAGE-ONE-LINE-A'));
    expect(text, contains('PAGE-ONE-LINE-B'));
    expect(text, contains('PAGE-TWO-LINE-A'));
  }

  group('export content source of truth', () {
    test('every text export carries both pages when only page 1 arrived live',
        () async {
      setUpScenario(livePages: 1);
      // The live preview really is missing page 2 ...
      expect(
        container.read(workstationProvider).allBBoxes.single.text,
        'live-page-0-text',
      );

      expectBothPages(await exportText(ExportFormat.markdown));
      expectBothPages(await exportText(ExportFormat.rawText));
      expectBothPages(await exportText(ExportFormat.html));
      expectBothPages(await exportText(ExportFormat.treeJson));
      // DOCX bytes are produced by the repository; the text it was handed is
      // what must carry both pages.
      await exportText(ExportFormat.docx);
      expectBothPages(repo.lastDocxText!);
    });

    test('every text export carries both pages when no live frames arrived',
        () async {
      setUpScenario();
      expect(container.read(workstationProvider).allBBoxes, isEmpty);

      expectBothPages(await exportText(ExportFormat.markdown));
      expectBothPages(await exportText(ExportFormat.rawText));
      expectBothPages(await exportText(ExportFormat.html));
      expectBothPages(await exportText(ExportFormat.treeJson));
      await exportText(ExportFormat.docx);
      expectBothPages(repo.lastDocxText!);
    });

    test('page order and joining are preserved for markdown and raw text',
        () async {
      setUpScenario();
      expect(
        await exportText(ExportFormat.markdown),
        'PAGE-ONE-LINE-A\n\nPAGE-ONE-LINE-B\n\nPAGE-TWO-LINE-A',
      );
      expect(
        await exportText(ExportFormat.rawText),
        'PAGE-ONE-LINE-A\nPAGE-ONE-LINE-B\nPAGE-TWO-LINE-A',
      );
    });

    test('structured exports carry the artifact handle to the repository',
        () async {
      setUpScenario();
      container.read(jobOrchestrationProvider.notifier).setTextArtifact(
            textArtifactId: _artifactId,
            textArtifactToken: _artifactToken,
            documentArtifactId: 'rich-document',
            documentArtifactToken: 'rich-token',
          );
      await exportText(ExportFormat.html);
      await exportText(ExportFormat.treeJson);
      await exportText(ExportFormat.docxTree);
      final markdown = await exportText(ExportFormat.markdown);
      expect(markdown, startsWith('# Preserved heading'));

      expect(repo.htmlRequests.single.textArtifactId, _artifactId);
      expect(repo.htmlRequests.single.textArtifactToken, _artifactToken);
      expect(repo.treeRequests.single.textArtifactId, _artifactId);
      expect(repo.treeRequests.single.textArtifactToken, _artifactToken);
      expect(repo.docxTreeRequests.single.textArtifactId, _artifactId);
      for (final request in [repo.treeRequests.single,
        repo.docxTreeRequests.single, repo.markdownRequests.single]) {
        expect(request.toJson()['document_artifact_id'], 'rich-document');
        expect(request.toJson()['document_artifact_token'], 'rich-token');
      }
      expect(repo.htmlRequests.single.toJson()['document_artifact_id'], 'rich-document');
      expect(repo.htmlRequests.single.toJson()['document_artifact_token'], 'rich-token');
    });

    test('treeJson output is indented JSON', () async {
      setUpScenario();
      final text = await exportText(ExportFormat.treeJson);
      final decoded = jsonDecode(text) as List<dynamic>;
      expect(decoded, hasLength(2));
      expect((decoded.first as Map<String, dynamic>)['page'], 0);
      expect((decoded.last as Map<String, dynamic>)['page'], 1);
      expect(text, contains('\n  '));
    });

    test('falls back to the live preview when the run has no artifact',
        () async {
      setUpScenario(livePages: 1, setArtifactHandle: false);

      expect(
        await exportText(ExportFormat.markdown),
        'live-page-0-text',
      );
      expect(
        await exportText(ExportFormat.rawText),
        'live-page-0-text',
      );
      final html = await exportText(ExportFormat.html);
      expect(html, contains('live-page-0-text'));
      expect(html, contains('Page 1'));
      // No artifact handle means no server-side structured export.
      expect(repo.htmlRequests, isEmpty);
      expect(repo.treeRequests, isEmpty);
      expect(await exportText(ExportFormat.treeJson), contains('"page": 0'));
    });
  });

  group('replaced document', () {
    test('replacement while artifact fetch is pending rejects the stale export', () async {
      setUpScenario();
      final response = Completer<String>();
      ocrRepo.pendingArtifact = response.future;
      final exporting = container.read(documentExportProvider.notifier)
          .prepare(ExportFormat.rawText);
      container.read(workstationProvider.notifier).loadDocument(
          Uint8List.fromList([7]), 'replacement.pdf');
      final rejected = expectLater(exporting, throwsStateError);
      response.complete(jsonEncode(_twoPageArtifact));
      await rejected;
      expect(container.read(documentExportProvider), isFalse);
      expect(container.read(jobOrchestrationProvider).documentArtifactId, isNull);
    });
    test('a previous run artifact is never exported for a new document',
        () async {
      setUpScenario(artifact: _staleArtifact, livePages: 1);
      expect(await exportText(ExportFormat.markdown), contains('STALE-RUN-TEXT'));

      // Loading another document resets the orchestration state, which clears
      // the artifact handle.
      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([4, 5, 6]),
            'other.pdf',
            pageCount: 1,
          );
      container.read(workstationProvider.notifier).addOrUpdateBBox(
            0,
            const BBoxItem(
              blockId: 'p0_b0',
              page: 0,
              block: 0,
              bbox: [0.05, 0.05, 0.95, 0.15],
              text: 'other-doc-text',
              kind: 'paragraph',
            ),
          );

      final markdown = await exportText(ExportFormat.markdown);
      expect(markdown, contains('other-doc-text'));
      expect(markdown, isNot(contains('STALE-RUN-TEXT')));
      expect(repo.htmlRequests, isEmpty);
      expect(repo.treeRequests, isEmpty);
    });

    test('a replaced document with no text of its own is rejected, not '
        'back-filled from a stale artifact', () async {
      setUpScenario(artifact: _staleArtifact, livePages: 1);
      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([4, 5, 6]),
            'other.pdf',
            pageCount: 1,
          );

      final notifier = container.read(documentExportProvider.notifier);
      expect(
        notifier.validationError(ExportFormat.markdown),
        'Recognized text not available. Please run OCR processing first.',
      );
      await expectLater(
        notifier.prepare(ExportFormat.markdown),
        throwsA(isA<FormatException>()),
      );
    });
  });

  group('unaffected paths', () {
    test('searchable PDF exports the server result bytes unchanged', () async {
      setUpScenario(livePages: 1, adoptPdf: true);
      final expected = container.read(workstationProvider).processedPdfBytes!;

      final prepared = await container
          .read(documentExportProvider.notifier)
          .prepare(ExportFormat.searchablePdf);

      expect(prepared.bytes, expected);
      expect(prepared.label, 'Searchable PDF');
      // The PDF path never consults the text artifact.
      expect(repo.htmlRequests, isEmpty);
      expect(repo.treeRequests, isEmpty);
      expect(repo.lastDocxText, isNull);
    });

    test('searchable PDF validation still requires processed bytes', () {
      setUpScenario(livePages: 1);
      final notifier = container.read(documentExportProvider.notifier);
      expect(
        notifier.validationError(ExportFormat.searchablePdf),
        'PDF not available. Please run OCR processing first.',
      );
    });

    test('searchable PDF never fetches the text artifact', () async {
      setUpScenario(livePages: 1, adoptPdf: true);
      final expected = container.read(workstationProvider).processedPdfBytes!;
      // A repository that fails any text-artifact access, so reaching it
      // would surface as a thrown error.
      final strictRepo = _FakeDocumentRepository(
        artifactStore: const <String, String>{},
      );

      final scoped = ProviderContainer(
        overrides: [
          documentRepositoryProvider.overrideWithValue(strictRepo),
          ocrRepositoryProvider.overrideWithValue(
            _RecordingOcrRepository(
              artifactStore: const <String, String>{},
              onArtifactRead: (id) =>
                  throw StateError('PDF export fetched artifact $id'),
            ),
          ),
        ],
      );
      addTearDown(scoped.dispose);
      scoped.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'scan.pdf',
            pageCount: 2,
          );
      scoped.read(workstationProvider.notifier)
          .adoptProcessedDocument(Uint8List.fromList([9, 9, 9, 9]));
      scoped.read(jobOrchestrationProvider.notifier).setTextArtifact(
            textArtifactId: _artifactId,
            textArtifactToken: _artifactToken,
          );

      final prepared = await scoped
          .read(documentExportProvider.notifier)
          .prepare(ExportFormat.searchablePdf);

      expect(prepared.bytes, expected);
      expect(expected, isNotEmpty);
    });
  });

  group('validationError coherence', () {
    test('an artifact handle alone satisfies every text format', () {
      setUpScenario();
      final notifier = container.read(documentExportProvider.notifier);
      for (final format in const <ExportFormat>[
        ExportFormat.docx,
        ExportFormat.html,
        ExportFormat.treeJson,
        ExportFormat.markdown,
        ExportFormat.rawText,
        ExportFormat.docxTree,
      ]) {
        expect(notifier.validationError(format), isNull, reason: '$format');
      }
    });

    test('live preview text alone satisfies the local text formats', () {
      setUpScenario(livePages: 1, setArtifactHandle: false);
      final notifier = container.read(documentExportProvider.notifier);
      for (final format in const <ExportFormat>[
        ExportFormat.docx,
        ExportFormat.html,
        ExportFormat.treeJson,
        ExportFormat.markdown,
        ExportFormat.rawText,
      ]) {
        expect(notifier.validationError(format), isNull, reason: '$format');
      }
      // docxTree is server-rendered from the artifact and has no local fallback.
      expect(
        notifier.validationError(ExportFormat.docxTree),
        'Text artifact not available. Please run OCR processing first.',
      );
    });

    test('no artifact and no text rejects every local text format', () {
      setUpScenario(setArtifactHandle: false);
      final notifier = container.read(documentExportProvider.notifier);
      for (final format in const <ExportFormat>[
        ExportFormat.docx,
        ExportFormat.html,
        ExportFormat.treeJson,
        ExportFormat.markdown,
        ExportFormat.rawText,
      ]) {
        expect(
          notifier.validationError(format),
          'Recognized text not available. Please run OCR processing first.',
          reason: '$format',
        );
      }
    });

    test('completed jobs without an artifact cannot export partial live text', () async {
      setUpScenario(livePages: 1, setArtifactHandle: false);
      final orchestration = container.read(jobOrchestrationProvider.notifier);
      orchestration.state = JobOrchestrationState(stage: 'Complete');
      await expectLater(container.read(documentExportProvider.notifier)
          .prepare(ExportFormat.rawText), throwsFormatException);
    });
  });

  group('artifact fetch failure', () {
    test('artifact failure rejects export even when live text exists',
        () async {
      setUpScenario(livePages: 2);
      // The artifact id the run published is not in the served store, so the
      // fetch fails and the export must fall back to the live preview.
      final brokenOcr = _FakeOcrRepository(
        artifactStore: const <String, String>{},
      );
      final scoped = ProviderContainer(
        overrides: [
          documentRepositoryProvider.overrideWithValue(repo),
          ocrRepositoryProvider.overrideWithValue(brokenOcr),
        ],
      );
      addTearDown(scoped.dispose);
      scoped.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'scan.pdf',
            pageCount: 2,
          );
      for (var page = 0; page < 2; page++) {
        scoped.read(workstationProvider.notifier).addOrUpdateBBox(
              page,
              BBoxItem(
                blockId: 'p${page}_b0',
                page: page,
                block: 0,
                bbox: const [0.05, 0.05, 0.95, 0.15],
                text: 'live-page-$page-text',
                kind: 'paragraph',
              ),
            );
      }
      scoped.read(jobOrchestrationProvider.notifier).setTextArtifact(
            textArtifactId: 'missing-artifact',
            textArtifactToken: 'token',
          );

      await expectLater(scoped.read(documentExportProvider.notifier)
          .prepare(ExportFormat.markdown), throwsStateError);
      expect(scoped.read(documentExportProvider), isFalse);
    });

    test('empty or malformed completed text never produces an empty export', () async {
      for (final artifact in <Map<String, dynamic>>[
        {}, {'0': ''}, {'0': 17}, {'0': 'valid', '1': 17},
      ]) {
        setUpScenario(artifact: artifact);
        for (final format in [ExportFormat.rawText, ExportFormat.markdown,
          ExportFormat.html, ExportFormat.treeJson, ExportFormat.docx]) {
          await expectLater(container.read(documentExportProvider.notifier)
              .prepare(format), throwsStateError);
        }
      }
    });

    test('artifact fetch failure with no live blocks cannot export empty text', () async {
      setUpScenario();
      artifactStore.clear();
      await expectLater(container.read(documentExportProvider.notifier)
          .prepare(ExportFormat.rawText), throwsStateError);
    });

    test('a failing structured export surfaces the error rather than silently '
        'shipping a partial file', () async {
      artifactStore = {_artifactId: jsonEncode(_twoPageArtifact)};
      repo = _FakeDocumentRepository(
        artifactStore: artifactStore,
        structuredFails: true,
      );
      container = ProviderContainer(
        overrides: [
          documentRepositoryProvider.overrideWithValue(repo),
          ocrRepositoryProvider
              .overrideWithValue(_FakeOcrRepository(artifactStore: artifactStore)),
        ],
      );
      addTearDown(container.dispose);
      container.read(workstationProvider.notifier).loadDocument(
            Uint8List.fromList([1, 2, 3]),
            'scan.pdf',
            pageCount: 2,
          );
      container.read(jobOrchestrationProvider.notifier).setTextArtifact(
            textArtifactId: _artifactId,
            textArtifactToken: _artifactToken,
          );

      final notifier = container.read(documentExportProvider.notifier);
      await expectLater(
        notifier.prepare(ExportFormat.treeJson),
        throwsA(isA<StateError>()),
      );
      // The in-progress flag is released even on failure.
      expect(container.read(documentExportProvider), isFalse);
    });
  });
}
