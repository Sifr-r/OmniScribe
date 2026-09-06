import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/feature_models.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/app_select.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';

enum ExportFormat {
  searchablePdf('Searchable PDF', 'pdf', 'Standard searchable sandwich PDF with embedded text layer'),
  docx('Word Document', 'docx', 'Formatted Microsoft Word (.docx) document'),
  docxTree('DOCX Tree Layout', 'docx', 'Preserves hierarchical bounding-box document structure in DOCX'),
  html('Standalone HTML', 'html', 'Self-contained responsive HTML with embedded styles'),
  treeJson('Block Tree JSON', 'json', 'Hierarchical JSON containing pages, blocks, and bboxes'),
  markdown('Markdown', 'md', 'Clean formatted markdown text representation'),
  rawText('Plain Text', 'txt', 'Extracted raw OCR text content');

  const ExportFormat(this.label, this.extension, this.description);
  final String label;
  final String extension;
  final String description;
}

class ExportModal extends ConsumerStatefulWidget {
  const ExportModal({super.key});

  static Future<void> show(BuildContext context) {
    return showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (context) => const Dialog(
        backgroundColor: Colors.transparent,
        insetPadding: EdgeInsets.all(24),
        child: ExportModal(),
      ),
    );
  }

  @override
  ConsumerState<ExportModal> createState() => _ExportModalState();
}

class _ExportModalState extends ConsumerState<ExportModal> {
  ExportFormat _selectedFormat = ExportFormat.searchablePdf;
  bool _isExporting = false;
  String? _statusMessage;
  bool _isSuccess = false;

  /// Build a sensible default filename for the export picker: strip any
  /// existing extension from the current document name and append [ext].
  String _defaultFilename(String? currentName, String ext) {
    final base = (currentName != null && currentName.isNotEmpty)
        ? currentName
        : 'document';
    final dot = base.lastIndexOf('.');
    final stem = dot > 0 ? base.substring(0, dot) : base;
    return '$stem.$ext';
  }

  /// Open the system save dialog for [bytes] and update the modal status.
  /// Matches the existing Searchable-PDF UX: shows the saved path on success
  /// and a "ready (N KB)" hint if the user cancels or the picker throws.
  Future<void> _saveWithPicker({
    required String fileName,
    required Uint8List bytes,
    required String formatLabel,
  }) async {
    final kb = (bytes.length / 1024).round();
    try {
      // Wave 16 / file_picker 12: ``FilePicker.platform`` was removed in favor
      // of static methods on ``FilePicker`` itself. ``saveFile`` returns a
      // ``Uri?`` — ``toString()`` keeps the user-facing copy working.
      final savePath = await FilePicker.saveFile(
        fileName: fileName,
        bytes: bytes,
      );
      if (savePath != null) {
        _statusMessage = '$formatLabel saved to $savePath.';
      } else {
        _statusMessage = '$formatLabel ready ($kb KB).';
      }
    } catch (_) {
      _statusMessage = '$formatLabel ready ($kb KB).';
    }
    _isSuccess = true;
  }

  Future<void> _handleExport() async {
    final wsState = ref.read(workstationProvider);
    final repo = ref.read(featureRepositoryProvider);

    setState(() {
      _isExporting = true;
      _statusMessage = null;
      _isSuccess = false;
    });

    try {
      final docText = wsState.allBBoxes.map((b) => b.text).join('\n\n');

      switch (_selectedFormat) {
        case ExportFormat.searchablePdf:
          if (wsState.loadedBytes != null) {
            await _saveWithPicker(
              fileName: wsState.filename ?? 'searchable_document.pdf',
              bytes: wsState.loadedBytes!,
              formatLabel: 'Searchable PDF',
            );
          } else {
            _statusMessage =
                'PDF not available. Please run OCR processing first.';
            _isSuccess = false;
          }
          break;

        case ExportFormat.docx:
          final bytes = await repo.exportDocx(
            ExportDocxRequest(
              text: docText.isNotEmpty
                  ? docText
                  : (wsState.filename ?? 'Document text'),
            ),
          );
          await _saveWithPicker(
            fileName: _defaultFilename(wsState.filename, 'docx'),
            bytes: bytes,
            formatLabel: 'DOCX',
          );
          break;

        case ExportFormat.docxTree:
          if (wsState.textArtifactId != null &&
              wsState.textArtifactToken != null) {
            final bytes = await repo.exportDocxTree(
              ExportBlockTreeRequest(
                textArtifactId: wsState.textArtifactId!,
                textArtifactToken: wsState.textArtifactToken!,
              ),
            );
            await _saveWithPicker(
              fileName: _defaultFilename(wsState.filename, 'docx'),
              bytes: bytes,
              formatLabel: 'DOCX Tree',
            );
          } else {
            _statusMessage =
                'Text artifact not available. Please run OCR processing first.';
            _isSuccess = false;
          }
          break;

        case ExportFormat.html:
          // Build self-contained HTML representation of recognized pages and bboxes
          final rawTitle = wsState.filename ?? 'OmniScribe Export';
          final escapedTitle = htmlEscape.convert(rawTitle);
          final htmlBuffer = StringBuffer()
            ..writeln('<!DOCTYPE html>')
            ..writeln('<html><head><meta charset="utf-8"><title>$escapedTitle</title>')
            ..writeln('<style>body{font-family:sans-serif;margin:2rem;} .page{margin-bottom:2rem;padding:1rem;border:1px solid #ccc;} .block{margin-bottom:0.5rem;}</style>')
            ..writeln('</head><body>')
            ..writeln('<h1>$escapedTitle</h1>');

          for (final page in wsState.pages) {
            htmlBuffer.writeln('<div class="page"><h2>Page ${page.page + 1}</h2>');
            for (final box in page.bboxes) {
              final escapedText = htmlEscape.convert(box.text);
              htmlBuffer.writeln('<div class="block"><p>$escapedText</p></div>');
            }
            htmlBuffer.writeln('</div>');
          }
          htmlBuffer.writeln('</body></html>');

          await _saveWithPicker(
            fileName: _defaultFilename(wsState.filename, 'html'),
            bytes: Uint8List.fromList(utf8.encode(htmlBuffer.toString())),
            formatLabel: 'HTML',
          );
          break;

        case ExportFormat.treeJson:
          final pagesJson = wsState.pages.map((p) => p.toJson()).toList();
          final formattedJson =
              const JsonEncoder.withIndent('  ').convert(pagesJson);
          await _saveWithPicker(
            fileName: _defaultFilename(wsState.filename, 'json'),
            bytes: Uint8List.fromList(utf8.encode(formattedJson)),
            formatLabel: 'Block Tree JSON',
          );
          break;

        case ExportFormat.markdown:
          await _saveWithPicker(
            fileName: _defaultFilename(wsState.filename, 'md'),
            bytes: Uint8List.fromList(utf8.encode(docText)),
            formatLabel: 'Markdown',
          );
          break;

        case ExportFormat.rawText:
          final rawText = wsState.allBBoxes.map((b) => b.text).join('\n');
          await _saveWithPicker(
            fileName: _defaultFilename(wsState.filename, 'txt'),
            bytes: Uint8List.fromList(utf8.encode(rawText)),
            formatLabel: 'Plain text',
          );
          break;
      }
    } catch (e) {
      _statusMessage = 'Export failed: $e';
      _isSuccess = false;
    } finally {
      if (mounted) {
        setState(() {
          _isExporting = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);

    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 540),
      child: AppCard(
        variant: AppCardVariant.defaultCard,
        padding: AppCardPadding.lg,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Modal Header
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Row(
                    children: [
                      Container(
                        width: 32,
                        height: 32,
                        decoration: BoxDecoration(
                          color: colors.brand.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Center(
                          child: Icon(Icons.file_download_outlined,
                              size: 18, color: colors.brand),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Export Document',
                              style: AppTypography.titleMedium(
                                color: colors.textPrimary,
                              ),
                            ),
                            Text(
                              'Convert and download recognized document data',
                              style: AppTypography.bodySmall(
                                color: colors.textMuted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: 'Close',
                  icon: Icon(Icons.close_rounded, size: 20, color: colors.textMuted),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Divider(height: 1, color: colors.border),
            const SizedBox(height: 16),

            // Document Summary Banner
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: colors.cardRaised,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: colors.border),
              ),
              child: Row(
                children: [
                  Icon(Icons.description_outlined,
                      size: 20, color: colors.brand),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          wsState.filename ?? 'Untitled Document',
                          style: AppTypography.bodySmall(
                            color: colors.textPrimary,
                          ).copyWith(fontWeight: FontWeight.w600),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        Text(
                          '${wsState.pageCount} page${wsState.pageCount == 1 ? "" : "s"} • ${wsState.allBBoxes.length} extracted bounding boxes',
                          style: AppTypography.micro(
                            color: colors.textMuted,
                          ),
                        ),
                        if ((wsState.trustSummary?.flaggedCount ?? 0) > 0)
                          Text(
                            '${wsState.trustSummary!.flaggedCount} block${wsState.trustSummary!.flaggedCount == 1 ? "" : "s"} flagged for review',
                            style: AppTypography.micro(
                              color: colors.warning,
                            ),
                          ),
                      ],
                    ),
                  ),
                  AppBadge(
                    label: wsState.hasDocument ? 'READY' : 'NO DATA',
                    variant: wsState.hasDocument
                        ? AppBadgeVariant.success
                        : AppBadgeVariant.warning,
                    size: AppBadgeSize.sm,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Export Format Selection
            const SectionHeader(title: 'Export Format'),
            const SizedBox(height: 8),
            AppSelect<ExportFormat>(
              label: 'Target File Format',
              value: _selectedFormat,
              items: ExportFormat.values
                  .map(
                    (f) => AppSelectItem<ExportFormat>(
                      value: f,
                      label: '${f.label} (.${f.extension})',
                    ),
                  )
                  .toList(),
              onChanged: (val) {
                if (val != null) {
                  setState(() {
                    _selectedFormat = val;
                    _statusMessage = null;
                  });
                }
              },
            ),
            const SizedBox(height: 6),
            Text(
              _selectedFormat.description,
              style: AppTypography.micro(color: colors.textMuted),
            ),
            const SizedBox(height: 16),

            // Status message
            if (_statusMessage != null) ...[
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: _isSuccess
                      ? colors.success.withValues(alpha: 0.1)
                      : colors.error.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                    color: _isSuccess
                        ? colors.success.withValues(alpha: 0.3)
                        : colors.error.withValues(alpha: 0.3),
                  ),
                ),
                child: Row(
                  children: [
                    Icon(
                      _isSuccess ? Icons.check_circle_outline : Icons.error_outline,
                      size: 16,
                      color: _isSuccess ? colors.success : colors.error,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _statusMessage!,
                        style: AppTypography.bodySmall(
                          color: _isSuccess ? colors.success : colors.error,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
            ],

            // Action Buttons
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                AppButton(
                  text: 'Cancel',
                  variant: AppButtonVariant.ghost,
                  size: AppButtonSize.md,
                  onPressed: () => Navigator.of(context).pop(),
                ),
                const SizedBox(width: 8),
                AppButton(
                  text: _isExporting ? 'Exporting...' : 'Export Document',
                  variant: AppButtonVariant.primary,
                  size: AppButtonSize.md,
                  loading: _isExporting,
                  disabled: !wsState.hasDocument,
                  icon: const Icon(Icons.download_rounded, size: 16),
                  onPressed: _handleExport,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
