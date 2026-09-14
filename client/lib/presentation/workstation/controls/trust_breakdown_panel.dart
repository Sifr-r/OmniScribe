import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/providers/job_orchestration_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';

/// Trust and Calibration Breakdown Panel.
///
/// Surfaces Platt calibrated trust scores, OCR confidence distribution,
/// anomaly flag counts (hallucinations, script mismatches), and self-healing repair metrics.
class TrustBreakdownPanel extends ConsumerWidget {
  const TrustBreakdownPanel({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);
    final job = ref.watch(jobOrchestrationProvider);
    final trust = job.trustSummary;
    final quality = job.qualitySummary;

    if (trust == null && quality == null && wsState.allBBoxes.isEmpty) {
      return AppCard(
        variant: AppCardVariant.defaultCard,
        padding: AppCardPadding.md,
        child: Center(
          child: Text(
            'No trust metrics available. Run OCR to evaluate calibration.',
            style: AppTypography.bodySmall(color: colors.textMuted),
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    final double avgConfidence = trust?.average ??
        (wsState.allBBoxes.isNotEmpty
            ? wsState.allBBoxes.fold<double>(0.0, (sum, b) => sum + (b.confidence ?? 0.0)) /
                wsState.allBBoxes.length
            : 0.0);

    final int totalBlocks = trust?.blockCount ?? wsState.allBBoxes.length;
    final int flaggedBlocks = trust?.flaggedCount ?? 0;
    final int repairedBlocks =
        job.repairedCount(wsState.documentRevisedCount);
    final int totalRetries = job.totalRetriesAttempted;

    final AppBadgeVariant trustVariant = avgConfidence >= 0.85
        ? AppBadgeVariant.success
        : avgConfidence >= 0.65
            ? AppBadgeVariant.warning
            : AppBadgeVariant.error;

    return AppCard(
      variant: AppCardVariant.defaultCard,
      padding: AppCardPadding.md,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          SectionHeader(
            title: 'Trust & Calibration',
            badge: AppBadge(
              label: '${(avgConfidence * 100).round()}% TRUST',
              variant: trustVariant,
              size: AppBadgeSize.sm,
            ),
          ),
          const SizedBox(height: 12),

          // Key Metrics Grid
          Row(
            children: [
              Expanded(
                child: _buildMetricTile(
                  context,
                  label: 'Average Score',
                  value: '${(avgConfidence * 100).toStringAsFixed(1)}%',
                  valueColor: avgConfidence >= 0.85 ? colors.success : colors.warning,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _buildMetricTile(
                  context,
                  label: 'Total Blocks',
                  value: '$totalBlocks',
                  valueColor: colors.textPrimary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _buildMetricTile(
                  context,
                  label: 'Flagged Blocks',
                  value: '$flaggedBlocks',
                  valueColor: flaggedBlocks > 0 ? colors.warning : colors.textMuted,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _buildMetricTile(
                  context,
                  label: 'Repaired Blocks',
                  value: '$repairedBlocks ($totalRetries retries)',
                  valueColor: repairedBlocks > 0 ? colors.brand : colors.textMuted,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),

          // Confidence Histogram
          if (trust != null && trust.histogram.isNotEmpty) ...[
            Text(
              'CONFIDENCE DISTRIBUTION',
              style: AppTypography.micro(color: colors.textMuted),
            ),
            const SizedBox(height: 8),
            ...trust.histogram.entries.map((entry) {
              final ratio = totalBlocks > 0 ? entry.value / totalBlocks : 0.0;
              return Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          entry.key,
                          style: AppTypography.codeSmall(color: colors.textSecondary),
                        ),
                        Text(
                          '${entry.value} (${(ratio * 100).round()}%)',
                          style: AppTypography.codeSmall(color: colors.textMuted),
                        ),
                      ],
                    ),
                    const SizedBox(height: 3),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(2),
                      child: LinearProgressIndicator(
                        value: ratio.clamp(0.0, 1.0),
                        minHeight: 4,
                        backgroundColor: colors.cardRaised,
                        valueColor: AlwaysStoppedAnimation<Color>(
                          entry.key.contains('0.9') || entry.key.contains('0.8')
                              ? colors.success
                              : entry.key.contains('0.7') || entry.key.contains('0.6')
                                  ? colors.warning
                                  : colors.error,
                        ),
                      ),
                    ),
                  ],
                ),
              );
            }),
            const SizedBox(height: 10),
          ],

          // Anomaly Flags (if any)
          if (trust != null && trust.flagCounts.isNotEmpty) ...[
            Text(
              'FLAGGED ANOMALIES',
              style: AppTypography.micro(color: colors.textMuted),
            ),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: trust.flagCounts.entries.map((e) {
                return Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: colors.warning.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: colors.warning.withValues(alpha: 0.3)),
                  ),
                  child: Text(
                    '${e.key.replaceAll('_', ' ')}: ${e.value}',
                    style: AppTypography.micro(color: colors.warning),
                  ),
                );
              }).toList(),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildMetricTile(
    BuildContext context, {
    required String label,
    required String value,
    required Color valueColor,
  }) {
    final colors = context.colors;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: colors.cardRaised,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: AppTypography.micro(color: colors.textMuted),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: AppTypography.codeBold(color: valueColor),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
