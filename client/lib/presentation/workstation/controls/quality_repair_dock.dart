import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/job_orchestration_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/app_toggle.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';

/// Quality Repair Loop controls dock matching the unified design system.
class QualityRepairDock extends ConsumerWidget {
  const QualityRepairDock({
    super.key,
    required this.settings,
    required this.onSettingsChanged,
  });

  final ProcessSettings settings;
  final ValueChanged<ProcessSettings> onSettingsChanged;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = context.colors;
    final job = ref.watch(jobOrchestrationProvider);

    final isRepairEnabled = settings.qualityRepairEnabled;
    final target = settings.qualityTarget ?? 0.85;
    final maxRetries = settings.maxRetries;

    final repairedCount =
        job.repairedCount(ref.watch(workstationProvider).documentRevisedCount);
    final retriesAttempted = job.totalRetriesAttempted;
    final avgConf = job.avgConfidence;

    return AppCard(
      variant: AppCardVariant.raised,
      padding: AppCardPadding.md,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          // Header with Loop Badge
          SectionHeader(
            title: 'Quality Repair Loop',
            action: AppBadge(
              label: isRepairEnabled ? 'ACTIVE' : 'OFF',
              variant: isRepairEnabled
                  ? AppBadgeVariant.info
                  : AppBadgeVariant.neutral,
              size: AppBadgeSize.sm,
            ),
          ),

          // Enable Quality Repair Loop Switch
          AppToggle(
            label: 'Auto-Repair Low Confidence Blocks',
            subtitle:
                'Iteratively prompts VLM to refine blocks falling below the target threshold.',
            value: isRepairEnabled,
            onChanged: (enabled) {
              onSettingsChanged(
                  settings.copyWith(qualityRepairEnabled: enabled));
            },
          ),
          const SizedBox(height: 12),

          if (isRepairEnabled) ...[
            // Quality Target Threshold Slider (0.50 - 1.00)
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Flexible(
                      child: Text(
                        'Quality Target Threshold',
                        style: AppTypography.labelMedium(
                          color: colors.textPrimary,
                        ).copyWith(fontWeight: FontWeight.w600),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(
                      '${(target * 100).round()}%',
                      style: AppTypography.codeSmall(
                        color: colors.brand,
                      ).copyWith(fontWeight: FontWeight.w600),
                    ),
                  ],
                ),
                SliderTheme(
                  data: SliderThemeData(
                    activeTrackColor: colors.brand,
                    inactiveTrackColor: colors.cardRaised,
                    thumbColor: colors.brand,
                    overlayColor: colors.brand.withValues(alpha: 0.15),
                    trackHeight: 3,
                    thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 6),
                  ),
                  child: Slider(
                    value: target,
                    min: 0.50,
                    max: 1.00,
                    divisions: 50,
                    onChanged: (val) {
                      onSettingsChanged(settings.copyWith(qualityTarget: val));
                    },
                  ),
                ),
                Text(
                  'Blocks scoring below this confidence trigger self-correction retries.',
                  style: AppTypography.micro(color: colors.textMuted),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Max Retries Slider (1 - 5)
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Flexible(
                      child: Text(
                        'Max Repair Retries',
                        style: AppTypography.labelMedium(
                          color: colors.textPrimary,
                        ).copyWith(fontWeight: FontWeight.w600),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(
                      '$maxRetries ${maxRetries == 1 ? "retry" : "retries"}',
                      style: AppTypography.codeSmall(
                        color: colors.brand,
                      ).copyWith(fontWeight: FontWeight.w600),
                    ),
                  ],
                ),
                SliderTheme(
                  data: SliderThemeData(
                    activeTrackColor: colors.brand,
                    inactiveTrackColor: colors.cardRaised,
                    thumbColor: colors.brand,
                    overlayColor: colors.brand.withValues(alpha: 0.15),
                    trackHeight: 3,
                    thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 6),
                  ),
                  child: Slider(
                    value: maxRetries.toDouble(),
                    min: 1.0,
                    max: 5.0,
                    divisions: 4,
                    onChanged: (val) {
                      onSettingsChanged(settings.copyWith(maxRetries: val.round()));
                    },
                  ),
                ),
                Text(
                  'Maximum re-prompts per degraded block before finalizing.',
                  style: AppTypography.micro(color: colors.textMuted),
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Live Repair Performance Metrics (During / After Run)
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: colors.card,
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: colors.border),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Flexible(
                        child: Text(
                          'Loop Statistics',
                          style: AppTypography.labelMedium(
                            color: colors.textMuted,
                          ).copyWith(fontWeight: FontWeight.w600),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (avgConf != null) ...[
                        const SizedBox(width: 8),
                        Text(
                          'Avg Conf: ${(avgConf * 100).round()}%',
                          style: AppTypography.codeSmall(
                            color: avgConf >= target
                                ? colors.success
                                : colors.warning,
                          ).copyWith(fontWeight: FontWeight.w600),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      Expanded(
                        child: _MetricItem(
                          label: 'Repaired Blocks',
                          value: '$repairedCount',
                          valueColor: colors.cyan,
                        ),
                      ),
                      Container(width: 1, height: 28, color: colors.border),
                      Expanded(
                        child: _MetricItem(
                          label: 'Retries Attempted',
                          value: '$retriesAttempted',
                          valueColor: colors.warning,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _MetricItem extends StatelessWidget {
  const _MetricItem({
    required this.label,
    required this.value,
    required this.valueColor,
  });

  final String label;
  final String value;
  final Color valueColor;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Column(
      children: [
        Text(
          value,
          style: AppTypography.titleMedium(
            color: valueColor,
          ).copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 2),
        Text(
          label,
          style: AppTypography.micro(
            color: colors.textMuted,
          ),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}
