import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/shared/widgets/app_badge.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';
import 'package:omniscribe_client/shared/widgets/app_card.dart';
import 'package:omniscribe_client/shared/widgets/app_select.dart';
import 'package:omniscribe_client/shared/widgets/section_header.dart';

import 'export_notifier.dart';

export 'export_notifier.dart' show ExportFormat;

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

  /// Open the system save dialog for [bytes] and update the modal status.
  /// Report actual saved, cancelled, or failed status without claiming success.
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
        _isSuccess = true;
      } else {
        _statusMessage = '$formatLabel save cancelled ($kb KB ready).';
        _isSuccess = false;
      }
    } catch (error) {
      _statusMessage = '$formatLabel save failed: $error ($kb KB ready).';
      _isSuccess = false;
    }
  }

  Future<void> _handleExport() async {
    if (_isExporting) return;
    final exporter = ref.read(documentExportProvider.notifier);
    setState(() {
      _isExporting = true;
      _statusMessage = null;
      _isSuccess = false;
    });
    try {
      final prepared = await exporter.prepare(_selectedFormat);
      if (!mounted) return;
      await _saveWithPicker(
        fileName: prepared.filename,
        bytes: prepared.bytes,
        formatLabel: prepared.label,
      );
    } catch (e) {
      if (!mounted) return;
      _statusMessage = e is FormatException ? e.message : 'Export failed: $e';
      _isSuccess = false;
    } finally {
      if (mounted) {
        setState(() => _isExporting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);
    final jobState = ref.watch(jobOrchestrationProvider);
    final isReady = ref
            .read(documentExportProvider.notifier)
            .validationError(_selectedFormat) ==
        null;

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
                  icon: Icon(Icons.close_rounded,
                      size: 20, color: colors.textMuted),
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
                        if ((jobState.trustSummary?.flaggedCount ?? 0) > 0)
                          Text(
                            '${jobState.trustSummary!.flaggedCount} block${jobState.trustSummary!.flaggedCount == 1 ? "" : "s"} flagged for review',
                            style: AppTypography.micro(
                              color: colors.warning,
                            ),
                          ),
                      ],
                    ),
                  ),
                  AppBadge(
                    label: jobState.isProcessing
                        ? 'PROCESSING'
                        : isReady
                            ? 'READY'
                            : 'NO DATA',
                    variant: isReady
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
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
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
                      _isSuccess
                          ? Icons.check_circle_outline
                          : Icons.error_outline,
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
                  disabled: !wsState.hasDocument || jobState.isProcessing,
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
