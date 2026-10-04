import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/features/workstation/ocr_repository.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/features/workstation/workstation_state.dart';

import 'document_repository.dart';
import 'documents_models.dart';

enum ExportFormat {
  searchablePdf('Searchable PDF', 'pdf',
      'Standard searchable sandwich PDF with embedded text layer'),
  docx('Word Document', 'docx', 'Formatted Microsoft Word (.docx) document'),
  docxTree('DOCX Tree Layout', 'docx',
      'Preserves hierarchical bounding-box document structure in DOCX'),
  html('Standalone HTML', 'html',
      'Self-contained responsive HTML with embedded styles'),
  treeJson('Block Tree JSON', 'json',
      'Hierarchical JSON containing pages, blocks, and bboxes'),
  markdown('Markdown', 'md', 'Clean formatted markdown text representation'),
  rawText('Plain Text', 'txt', 'Extracted raw OCR text content');

  const ExportFormat(this.label, this.extension, this.description);
  final String label;
  final String extension;
  final String description;
}

class PreparedExport {
  const PreparedExport(
      {required this.bytes, required this.filename, required this.label});

  final Uint8List bytes;
  final String filename;
  final String label;
}

final documentExportProvider = NotifierProvider<DocumentExportNotifier, bool>(
  DocumentExportNotifier.new,
);

/// Export text resolved from the authoritative source for [ExportFormat].
///
/// The completed server-side **text artifact** wins whenever the finished job
/// published one: it is the only representation guaranteed to hold every page.
/// The live preview bboxes are a projection of the WebSocket
/// `block_complete` stream and lose whole pages when that stream is partial
/// (socket drop, reconnect race, handshake finishing after the job), so they
/// are used only as a fallback for runs with no artifact — for example local
/// session-restored documents that were never submitted to the server.
class _ExportText {
  const _ExportText({
    required this.paragraphs,
    required this.pages,
    required this.fromArtifact,
  });

  /// Ordered non-empty blocks for the whole document, in page order.
  final List<String> paragraphs;

  /// Per-page ordered non-empty blocks, keyed by 0-based page index.
  final Map<int, List<String>> pages;

  /// Whether this text came from the completed text artifact.
  final bool fromArtifact;

  /// Blocks for [page], or an empty list when the page holds no text.
  List<String> blocksOf(int page) => pages[page] ?? const <String>[];
}

/// Prepares the selected representation; the view owns the platform save dialog.
class DocumentExportNotifier extends Notifier<bool> {
  @override
  bool build() => false;

  /// Whether a completed text artifact handle exists for the active job.
  ///
  /// `JobOrchestrationState.reset()` clears these handles, which is what keeps
  /// a previous run's artifact from being applied to a newly loaded document.
  static bool _hasArtifactHandle(JobOrchestrationState job) =>
      job.textArtifactId?.isNotEmpty == true &&
      job.textArtifactToken?.isNotEmpty == true;

  String? validationError(ExportFormat format) {
    final document = ref.read(workstationProvider);
    final job = ref.read(jobOrchestrationProvider);
    if (job.isProcessing) return 'Please wait for OCR processing to finish.';
    if (format != ExportFormat.searchablePdf) {
      if ((job.textArtifactId?.isNotEmpty == true) !=
          (job.textArtifactToken?.isNotEmpty == true)) {
        return 'Completed text artifact handle is incomplete. Please run OCR again.';
      }
      if (job.stage == 'Complete' && !_hasArtifactHandle(job)) {
        return 'Completed text artifact unavailable. Please run OCR again.';
      }
      if ((job.documentArtifactId?.isNotEmpty == true) !=
          (job.documentArtifactToken?.isNotEmpty == true)) {
        return 'Completed document artifact handle is incomplete. Please run OCR again.';
      }
    }
    switch (format) {
      case ExportFormat.searchablePdf:
        if (document.processedPdfBytes?.isNotEmpty != true) {
          return 'PDF not available. Please run OCR processing first.';
        }
      case ExportFormat.docxTree:
        // Server-rendered tree layout: inherently artifact-only, no local fallback.
        if (!_hasArtifactHandle(job)) {
          return 'Text artifact not available. Please run OCR processing first.';
        }
      case ExportFormat.docx:
      case ExportFormat.html:
      case ExportFormat.treeJson:
      case ExportFormat.markdown:
      case ExportFormat.rawText:
        // Text-bearing formats resolve from the artifact when one exists and
        // from the live preview otherwise, so either source satisfies them.
        final hasLiveText =
            document.allBBoxes.any((box) => box.text.trim().isNotEmpty);
        if (!_hasArtifactHandle(job) && !hasLiveText) {
          return 'Recognized text not available. Please run OCR processing first.';
        }
    }
    return null;
  }

  /// Resolves the export text, preferring the completed text artifact.
  ///
  /// A published artifact must be readable and contain text. Its failure must
  /// never turn a partially streamed preview into a successful export.
  Future<_ExportText> _resolveExportText(
    JobOrchestrationState job,
    WorkstationState document,
  ) async {
    if (_hasArtifactHandle(job)) {
      try {
        final artifactJson = await ref
            .read(ocrRepositoryProvider)
            .getTextArtifact(job.textArtifactId!, job.textArtifactToken!);
        final artifact = decodeTextArtifact(artifactJson);
        if (artifact != null) {
          if (artifact.entries.any((entry) =>
              int.tryParse(entry.key) == null ||
              int.parse(entry.key) < 0 ||
              entry.value is! String)) {
            throw const FormatException(
                'Completed text artifact has malformed pages.');
          }
          final pages = parseTextArtifactPages(artifact);
          final content = _textFromArtifactPages(pages);
          if (content.paragraphs.any((text) => text.trim().isNotEmpty)) {
            return content;
          }
        }
        throw const FormatException(
            'Completed text artifact contains no usable text.');
      } catch (error) {
        throw StateError('Cannot export the completed document: $error');
      }
    }
    return _textFromLivePreview(document);
  }

  /// Flattens parsed artifact pages into document-ordered paragraphs.
  static _ExportText _textFromArtifactPages(TextArtifactPages pages) {
    final orderedPageIndices = pages.keys.toList()..sort();
    final paragraphs = <String>[];
    for (final pageIndex in orderedPageIndices) {
      paragraphs.addAll(pages[pageIndex]!);
    }
    return _ExportText(
      paragraphs: List<String>.unmodifiable(paragraphs),
      pages: Map<int, List<String>>.unmodifiable(pages),
      fromArtifact: true,
    );
  }

  /// Projects the live preview bboxes into per-page blocks.
  static _ExportText _textFromLivePreview(WorkstationState document) {
    final pages = <int, List<String>>{};
    final paragraphs = <String>[];
    for (final page in document.pages) {
      final blocks = page.bboxes
          .where((box) => box.text.trim().isNotEmpty)
          .map((box) => box.text)
          .toList(growable: false);
      if (blocks.isEmpty) continue;
      pages[page.page] = blocks;
      paragraphs.addAll(blocks);
    }
    return _ExportText(
      paragraphs: List<String>.unmodifiable(paragraphs),
      pages: Map<int, List<String>>.unmodifiable(pages),
      fromArtifact: false,
    );
  }

  /// Renders standalone HTML from already-resolved [content].
  static Uint8List _buildHtml(String title, _ExportText content) {
    final html = StringBuffer()
      ..writeln('<!DOCTYPE html>')
      ..writeln('<html><head><meta charset="utf-8"><title>$title</title>')
      ..writeln(
          '<style>body{font-family:sans-serif;margin:2rem;} .page{margin-bottom:2rem;padding:1rem;border:1px solid #ccc;} .block{margin-bottom:0.5rem;}</style>')
      ..writeln('</head><body>')
      ..writeln('<h1>$title</h1>');
    for (final page in _orderedPageIndices(content)) {
      html.writeln('<div class="page"><h2>Page ${page + 1}</h2>');
      for (final block in content.blocksOf(page)) {
        html.writeln(
            '<div class="block"><p>${htmlEscape.convert(block)}</p></div>');
      }
      html.writeln('</div>');
    }
    html.writeln('</body></html>');
    return Uint8List.fromList(utf8.encode(html.toString()));
  }

  /// Page indices to render, ascending. Falls back to a single page when the
  /// source carries no page information at all.
  static List<int> _orderedPageIndices(_ExportText content) {
    final indices = content.pages.keys.toList()..sort();
    return indices.isEmpty ? const <int>[0] : indices;
  }

  /// Whether [format] needs resolved export text.
  ///
  /// The searchable PDF ships the server-returned result bytes and the DOCX
  /// tree is rendered by the server straight from the artifact, so neither
  /// reads the text artifact and neither may be affected by it.
  static bool _needsResolvedText(ExportFormat format) => switch (format) {
        ExportFormat.searchablePdf || ExportFormat.docxTree => false,
        ExportFormat.docx ||
        ExportFormat.html ||
        ExportFormat.treeJson ||
        ExportFormat.markdown ||
        ExportFormat.rawText =>
          true,
      };

  Future<PreparedExport> prepare(ExportFormat format) async {
    if (state) throw StateError('Document export is already in progress.');
    final error = validationError(format);
    if (error != null) throw FormatException(error);
    state = true;
    try {
      final document = ref.read(workstationProvider);
      final job = ref.read(jobOrchestrationProvider);
      final repository = ref.read(documentRepositoryProvider);
      final resolved = _needsResolvedText(format)
          ? await _resolveExportText(job, document)
          : null;
      if (!ref.mounted) throw StateError('Document export was cancelled.');
      // Unused for formats that take their bytes from the server.
      final content = resolved ??
          const _ExportText(
            paragraphs: <String>[],
            pages: <int, List<String>>{},
            fromArtifact: false,
          );
      final text = content.paragraphs.join('\n\n');
      final base = document.filename?.isNotEmpty == true
          ? document.filename!
          : 'document';
      final dot = base.lastIndexOf('.');
      final stem = dot > 0 ? base.substring(0, dot) : base;
      final filename = '$stem.${format.extension}';
      late final Uint8List bytes;
      late final String label;
      switch (format) {
        case ExportFormat.searchablePdf:
          // Server-returned result bytes, not a client projection.
          bytes = document.processedPdfBytes!;
          label = 'Searchable PDF';
        case ExportFormat.docx:
          bytes = await repository.exportDocx(ExportDocxRequest(
            text: text,
          ));
          label = 'DOCX';
        case ExportFormat.docxTree:
          final id = job.textArtifactId;
          final token = job.textArtifactToken;
          if (id == null || id.isEmpty || token == null || token.isEmpty) {
            throw const FormatException(
                'Text artifact not available. Please run OCR processing first.');
          }
          bytes = await repository.exportDocxTree(ExportBlockTreeRequest(
            textArtifactId: id,
            textArtifactToken: token,
            documentArtifactId: job.documentArtifactId,
            documentArtifactToken: job.documentArtifactToken,
          ));
          label = 'DOCX Tree';
        case ExportFormat.html:
          final title =
              htmlEscape.convert(document.filename ?? 'OmniScribe Export');
          if (content.fromArtifact) {
            // Structured export: let the server render the artifact's block tree.
            bytes = await repository.exportHtml(ExportHtmlRequest(
              textArtifactId: job.textArtifactId!,
              textArtifactToken: job.textArtifactToken!,
              documentArtifactId: job.documentArtifactId,
              documentArtifactToken: job.documentArtifactToken,
            ));
          } else {
            bytes = _buildHtml(title, content);
          }
          label = 'HTML';
        case ExportFormat.treeJson:
          if (content.fromArtifact) {
            // Structured export: the server's block tree is the only
            // representation that survives a partial progress stream.
            final tree = await repository.exportBlockTree(
              ExportBlockTreeRequest(
                textArtifactId: job.textArtifactId!,
                textArtifactToken: job.textArtifactToken!,
                documentArtifactId: job.documentArtifactId,
                documentArtifactToken: job.documentArtifactToken,
              ),
            );
            bytes = tree is List<int>
                ? Uint8List.fromList(tree)
                : Uint8List.fromList(
                    utf8.encode(const JsonEncoder.withIndent('  ').convert(tree)),
                  );
          } else {
            bytes = Uint8List.fromList(utf8.encode(
                const JsonEncoder.withIndent('  ')
                    .convert(document.pages.map((p) => p.toJson()).toList())));
          }
          label = 'Block Tree JSON';
        case ExportFormat.markdown:
          bytes = content.fromArtifact
              ? await repository.exportMarkdown(ExportBlockTreeRequest(
                  textArtifactId: job.textArtifactId!,
                  textArtifactToken: job.textArtifactToken!,
                  documentArtifactId: job.documentArtifactId,
                  documentArtifactToken: job.documentArtifactToken,
                ))
              : Uint8List.fromList(utf8.encode(text));
          label = 'Markdown';
        case ExportFormat.rawText:
          bytes = Uint8List.fromList(
              utf8.encode(content.paragraphs.join('\n')));
          label = 'Plain text';
      }
      if (bytes.isEmpty) throw StateError('The server returned an empty export.');
      if (!ref.mounted) throw StateError('Document export was cancelled.');
      final current = ref.read(workstationProvider);
      if (!identical(current.loadedBytes, document.loadedBytes) ||
          !identical(current.processedPdfBytes, document.processedPdfBytes) ||
          current.filename != document.filename ||
          current.filePath != document.filePath) {
        throw StateError('Document changed while preparing the export.');
      }
      return PreparedExport(bytes: bytes, filename: filename, label: label);
    } finally {
      if (ref.mounted) state = false;
    }
  }
}
