import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/features/jobs/jobs_notifier.dart';
import 'package:omniscribe_client/shared/widgets/app_badge.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';
import 'package:omniscribe_client/shared/widgets/app_card.dart';
import 'package:omniscribe_client/shared/widgets/app_modal.dart';

/// Job Execution History screen.
///
/// Slice 2: consumes the Riverpod 2.x `jobsProvider` (`Notifier<JobsState>`)
/// and the canonical `shared/widgets/app_*` widgets. The DataTable,
/// badge variant mapping, download spinner, and clear-confirm modal are
/// preserved from the legacy DocuVerse-skinned implementation.
class JobHistoryScreen extends ConsumerStatefulWidget {
  const JobHistoryScreen({super.key});

  @override
  ConsumerState<JobHistoryScreen> createState() => _JobHistoryScreenState();
}

class _JobHistoryScreenState extends ConsumerState<JobHistoryScreen> {
  String? _downloadingJobId;
  String? _statusBanner;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(jobsProvider.notifier).fetchJobs());
  }

  AppBadgeVariant _statusVariant(String status) {
    switch (status.toLowerCase()) {
      case 'completed':
      case 'complete':
      case 'success':
        return AppBadgeVariant.success;
      case 'processing':
      case 'pending':
        return AppBadgeVariant.warning;
      case 'failed':
      case 'error':
        return AppBadgeVariant.error;
      case 'cancelled':
        return AppBadgeVariant.neutral;
      default:
        return AppBadgeVariant.neutral;
    }
  }

  Future<void> _handleDownload(JobRecord job) async {
    setState(() {
      _downloadingJobId = job.id;
      _statusBanner = null;
    });

    try {
      // 2026-08-29 audit C-3 / H-3: the result token is delivered via
      // the ``job_completed`` SSE event (out-of-band), not the status
      // response body. ``downloadResult`` resolves the token itself.
      final Uint8List bytes =
          await ref.read(jobsProvider.notifier).downloadResult(job.id);
      if (mounted) {
        try {
          // Wave 16 / file_picker 12: ``FilePicker.platform`` is gone;
          // ``FilePicker.saveFile`` is now a static method returning ``Uri?``.
          final savePath = await FilePicker.saveFile(
            fileName: job.filename,
            bytes: bytes,
          );
          if (mounted) {
            setState(() {
              if (savePath != null) {
                _statusBanner = 'Saved ${job.filename} to $savePath.';
              } else {
                _statusBanner =
                    'Downloaded searchable PDF for ${job.filename} (${bytes.lengthInBytes} bytes)';
              }
            });
          }
        } catch (_) {
          if (mounted) {
            setState(() {
              _statusBanner =
                  'Downloaded searchable PDF for ${job.filename} (${bytes.lengthInBytes} bytes)';
            });
          }
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _statusBanner = 'Download error: $e';
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          // Only clear the slot if this download is still the active one —
          // a concurrent download on another row should not lose its spinner.
          if (_downloadingJobId == job.id) {
            _downloadingJobId = null;
          }
        });
      }
    }
  }

  Future<void> _handleCancel(String jobId) async {
    try {
      await ref.read(jobsProvider.notifier).cancelJob(jobId);
      if (mounted) {
        setState(() {
          _statusBanner = 'Job $jobId cancellation requested.';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _statusBanner = 'Cancel error: $e';
        });
      }
    }
  }

  Future<void> _showClearConfirmDialog() async {
    await AppModal.show<void>(
      context: context,
      title: 'Clear All Job History?',
      subtitle:
          'This will remove all job records and drop associated cached artifacts from the server.',
      maxWidth: AppModalWidth.sm,
      actions: [
        AppButton(
          text: 'Cancel',
          variant: AppButtonVariant.ghost,
          onPressed: () => Navigator.of(context).pop(),
        ),
        AppButton(
          text: 'Clear all jobs',
          variant: AppButtonVariant.danger,
          onPressed: () async {
            Navigator.of(context).pop();
            try {
              await ref.read(jobsProvider.notifier).clearJobs();
              if (mounted) {
                setState(() {
                  _statusBanner = 'Job execution history cleared.';
                });
              }
            } catch (_) {
              if (mounted) {
                setState(() {
                  // The notifier retains the failure for the error banner.
                  _statusBanner = null;
                });
              }
            }
          },
        ),
      ],
      content: Text(
        'This action is irreversible. All past OCR, translation, and extraction task logs will be wiped.',
        style: TextStyle(
          fontSize: 13,
          color: context.colors.textMuted,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final jobsState = ref.watch(jobsProvider);
    final colors = context.colors;
    final statusBanner = jobsState.error != null
        ? 'Job history error: ${jobsState.error}'
        : _statusBanner;
    final isErrorBanner =
        statusBanner != null && statusBanner.toLowerCase().contains('error');

    return Scaffold(
      backgroundColor: colors.background,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Header
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Text(
                            'Job Execution History',
                            style: TextStyle(
                              fontSize: 22,
                              fontWeight: FontWeight.bold,
                              letterSpacing: -0.5,
                            ),
                          ),
                          const SizedBox(width: 10),
                          AppBadge(
                            label: '${jobsState.jobs.length} jobs',
                            variant: AppBadgeVariant.brand,
                            dot: true,
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Audit log of previous OCR, Translation, and Extraction pipeline tasks',
                        style: TextStyle(
                          fontSize: 12,
                          color: colors.textMuted,
                        ),
                      ),
                    ],
                  ),
                  Row(
                    children: [
                      AppButton(
                        text: 'Refresh',
                        variant: AppButtonVariant.secondary,
                        loading: jobsState.isFetching,
                        icon: const Icon(Icons.refresh, size: 14),
                        onPressed: () =>
                            ref.read(jobsProvider.notifier).fetchJobs(),
                      ),
                      const SizedBox(width: 8),
                      AppButton(
                        text: 'Clear all',
                        variant: AppButtonVariant.danger,
                        disabled: jobsState.jobs.isEmpty,
                        icon: const Icon(Icons.delete_outline, size: 14),
                        onPressed: _showClearConfirmDialog,
                      ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 16),

              if (statusBanner != null) ...[
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  decoration: BoxDecoration(
                    color: (isErrorBanner ? colors.error : colors.info)
                        .withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(
                      color: (isErrorBanner ? colors.error : colors.info)
                          .withValues(alpha: 0.35),
                    ),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        isErrorBanner
                            ? Icons.error_outline
                            : Icons.info_outline,
                        size: 16,
                        color: isErrorBanner ? colors.error : colors.info,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          statusBanner,
                          style: TextStyle(
                            fontSize: 12,
                            color: isErrorBanner ? colors.error : colors.info,
                            fontFamily: 'monospace',
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),
              ],

              // Main Jobs Table
              Expanded(
                child: AppCard(
                  padding: AppCardPadding.none,
                  child: jobsState.jobs.isEmpty
                      ? Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Icons.history,
                                size: 48,
                                color: colors.textMuted,
                              ),
                              const SizedBox(height: 12),
                              Text(
                                jobsState.isFetching
                                    ? 'Loading job history…'
                                    : 'No historical OCR or translation jobs found.',
                                style: TextStyle(
                                  color: colors.textMuted,
                                  fontSize: 13,
                                ),
                              ),
                            ],
                          ),
                        )
                      : ClipRRect(
                          borderRadius: BorderRadius.circular(8),
                          child: SingleChildScrollView(
                            child: DataTable(
                              headingRowColor: WidgetStateProperty.all(
                                colors.cardRaised,
                              ),
                              dataRowColor: WidgetStateProperty.all(
                                Colors.transparent,
                              ),
                              dividerThickness: 1,
                              horizontalMargin: 16,
                              columnSpacing: 24,
                              columns: const [
                                DataColumn(
                                  label: Text(
                                    'Job ID',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 12,
                                    ),
                                  ),
                                ),
                                DataColumn(
                                  label: Text(
                                    'Status',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 12,
                                    ),
                                  ),
                                ),
                                DataColumn(
                                  label: Text(
                                    'Filename',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 12,
                                    ),
                                  ),
                                ),
                                DataColumn(
                                  label: Text(
                                    'Pipeline / Model',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 12,
                                    ),
                                  ),
                                ),
                                DataColumn(
                                  label: Text(
                                    'Duration',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 12,
                                    ),
                                  ),
                                ),
                                DataColumn(
                                  label: Text(
                                    'Failed Pages',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 12,
                                    ),
                                  ),
                                ),
                                DataColumn(
                                  label: Text(
                                    'Actions',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 12,
                                    ),
                                  ),
                                ),
                              ],
                              rows: jobsState.jobs.map((job) {
                                final isDownloading =
                                    _downloadingJobId == job.id;
                                final isRunning =
                                    job.status.toLowerCase() == 'processing' ||
                                        job.status.toLowerCase() == 'pending';

                                return DataRow(
                                  cells: [
                                    DataCell(
                                      Text(
                                        job.id.length > 8
                                            ? '${job.id.substring(0, 8)}…'
                                            : job.id,
                                        style: const TextStyle(
                                          fontFamily: 'monospace',
                                          fontSize: 12,
                                          fontWeight: FontWeight.w600,
                                        ),
                                      ),
                                    ),
                                    DataCell(
                                      AppBadge(
                                        label: job.status.toUpperCase(),
                                        variant: _statusVariant(job.status),
                                      ),
                                    ),
                                    DataCell(
                                      ConstrainedBox(
                                        constraints:
                                            const BoxConstraints(maxWidth: 160),
                                        child: Text(
                                          job.filename,
                                          style: const TextStyle(fontSize: 12),
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                    ),
                                    DataCell(
                                      Text(
                                        '${job.pipelineMode} / ${job.model}',
                                        style: TextStyle(
                                          fontSize: 12,
                                          fontFamily: 'monospace',
                                          color: colors.textMuted,
                                        ),
                                      ),
                                    ),
                                    DataCell(
                                      Text(
                                        job.durationS > 0
                                            ? '${job.durationS.toStringAsFixed(1)}s'
                                            : '—',
                                        style: TextStyle(
                                          fontSize: 12,
                                          fontFamily: 'monospace',
                                          color: colors.textMuted,
                                        ),
                                      ),
                                    ),
                                    DataCell(
                                      job.failedPages.isNotEmpty
                                          ? AppBadge(
                                              label:
                                                  'Pages: ${job.failedPages.join(", ")}',
                                              variant: AppBadgeVariant.error,
                                            )
                                          : Text(
                                              'None',
                                              style: TextStyle(
                                                fontSize: 12,
                                                color: colors.textMuted,
                                              ),
                                            ),
                                    ),
                                    DataCell(
                                      Row(
                                        mainAxisSize: MainAxisSize.min,
                                        children: [
                                          if (isRunning)
                                            AppButton(
                                              text: 'Cancel',
                                              variant: AppButtonVariant.danger,
                                              size: AppButtonSize.sm,
                                              onPressed: () =>
                                                  _handleCancel(job.id),
                                            )
                                          else
                                            AppButton(
                                              text: 'PDF',
                                              variant: AppButtonVariant.ghost,
                                              size: AppButtonSize.sm,
                                              loading: isDownloading,
                                              icon: const Icon(
                                                Icons.download,
                                                size: 14,
                                              ),
                                              onPressed: () =>
                                                  _handleDownload(job),
                                            ),
                                        ],
                                      ),
                                    ),
                                  ],
                                );
                              }).toList(),
                            ),
                          ),
                        ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
