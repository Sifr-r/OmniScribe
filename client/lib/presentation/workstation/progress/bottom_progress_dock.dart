import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/providers/job_orchestration_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_state.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';

/// Live Bottom Progress Dock displaying the stage stepper, animated progress bar,
/// quality loop counters, and cancel controls.
class BottomProgressDock extends ConsumerWidget {
  const BottomProgressDock({
    super.key,
    this.onCancelJob,
  });

  final VoidCallback? onCancelJob;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);
    final job = ref.watch(jobOrchestrationProvider);
    final notifier = ref.read(workstationProvider.notifier);

    final isProcessing = job.isProcessing;
    final stage = job.stage;
    final percent = job.percent;
    final percentInt = job.percentInt;
    final statusMsg = job.statusMessage;

    final processedBlocks = job.processedBlocks;
    final totalBlocks = job.totalBlocks;
    final retries = job.totalRetriesAttempted;
    final repaired = job.repairedCount(wsState.documentRevisedCount);
    final avgConf = job.avgConfidence;

    // Sprint 3 / H-1 audit fix: wrap the dock in a Semantics node
    // with `liveRegion: true` so screen readers announce progress
    // changes (stage / percent / status message) without forcing the
    // user to focus the dock. The label summarises the current
    // state on every announcement.
    final a11yLabel = isProcessing
        ? 'OCR in progress, stage $stage, $percentInt percent'
        : 'OCR $stage';

    return Semantics(
      liveRegion: true,
      label: a11yLabel,
      container: true,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: colors.cardRaised,
        border: Border(top: BorderSide(color: colors.border, width: 1.0)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.15),
            blurRadius: 10,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 1. Stage Stepper Row
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: _buildStageStepper(colors, job),
          ),
          const SizedBox(height: 10),

          // 2. Animated Progress Bar & Percent
          Row(
            children: [
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: Stack(
                    children: [
                      Container(
                        height: 6,
                        color: colors.muted.withValues(alpha: 0.5),
                      ),
                      AnimatedFractionallySizedBox(
                        duration: const Duration(milliseconds: 300),
                        curve: Curves.easeOutCubic,
                        widthFactor: (percent / 100.0).clamp(0.0, 1.0),
                        child: Container(
                          height: 6,
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              colors: isProcessing
                                  ? [colors.brand, colors.info]
                                  : stage == 'Complete'
                                      ? [
                                          colors.success,
                                          const Color(0xFF10B981)
                                        ]
                                      : [colors.muted, colors.textMuted],
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Text(
                '$percentInt%',
                style: AppTypography.codeSmall(
                  color: isProcessing
                      ? colors.brand
                      : stage == 'Complete'
                          ? colors.success
                          : colors.textMuted,
                ).copyWith(fontWeight: FontWeight.w700, fontSize: 12),
              ),
            ],
          ),
          const SizedBox(height: 8),

          // 3. Live Metrics Row & Status Message & Cancel CTA
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              // Left: Status Message / Log Stream
              Expanded(
                child: Row(
                  children: [
                    if (isProcessing) ...[
                      SizedBox(
                        width: 12,
                        height: 12,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          valueColor:
                              AlwaysStoppedAnimation<Color>(colors.brand),
                        ),
                      ),
                      const SizedBox(width: 8),
                    ] else if (stage == 'Complete') ...[
                      Icon(Icons.check_circle, size: 14, color: colors.success),
                      const SizedBox(width: 6),
                    ],
                    Flexible(
                      child: Text(
                        statusMsg,
                        style: AppTypography.bodySmall(
                          color: colors.textMuted,
                        ).copyWith(fontSize: 12),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),

              // Middle: Metrics Pills (Blocks, Retries, Repaired, Conf)
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _buildMetricPill(
                    label: 'Blocks',
                    value:
                        '$processedBlocks${totalBlocks > 0 ? "/$totalBlocks" : ""}',
                    colors: colors,
                  ),
                  const SizedBox(width: 8),
                  _buildMetricPill(
                    label: 'Retries',
                    value: '$retries',
                    colors: colors,
                    highlightColor: retries > 0 ? colors.warning : null,
                  ),
                  const SizedBox(width: 8),
                  _buildMetricPill(
                    label: 'Repaired',
                    value: '$repaired',
                    colors: colors,
                    highlightColor: repaired > 0 ? colors.cyan : null,
                  ),
                  if (avgConf != null) ...[
                    const SizedBox(width: 8),
                    _buildMetricPill(
                      label: 'Avg Conf',
                      value: '${(avgConf * 100).round()}%',
                      colors: colors,
                      highlightColor:
                          avgConf >= 0.85 ? colors.success : colors.warning,
                    ),
                  ],
                ],
              ),

              // Right: Cancel Button
              if (isProcessing) ...[
                const SizedBox(width: 16),
                AppButton(
                  text: 'Cancel',
                  variant: AppButtonVariant.danger,
                  size: AppButtonSize.sm,
                  icon: const Icon(Icons.stop_circle_outlined, size: 14),
                  onPressed: () {
                    notifier.cancelOcr();
                    onCancelJob?.call();
                  },
                ),
              ],
            ],
          ),
        ],
      ),
    ),
    );
  }

  Widget _buildStageStepper(
      AppColorScheme colors, JobOrchestrationState job) {
    const stages = WorkstationState.pipelineStages;
    final currentIdx = job.currentStageIndex;
    final isDone = job.stage == 'Complete';
    // When `currentStageIndex == -1` the active stage is not part of the
    // pipeline (e.g. Idle / Error / Cancelled). Treat the same as "no active
    // step" — every connector is dim, no node is highlighted.
    final hasActiveStage = currentIdx >= 0;

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(stages.length * 2 - 1, (i) {
        if (i.isOdd) {
          // Connector line
          final stageBefore = i ~/ 2;
          final isPast =
              isDone || (hasActiveStage && currentIdx > stageBefore);
          return Flexible(
            fit: FlexFit.loose,
            child: Container(
              height: 2,
              color:
                  isPast ? colors.brand : colors.muted.withValues(alpha: 0.4),
            ),
          );
        }

        final stageIdx = i ~/ 2;
        final stageName = stages[stageIdx];
        final isStageActive =
            hasActiveStage && !isDone && (currentIdx == stageIdx);
        final isStagePast = isDone || (hasActiveStage && currentIdx > stageIdx);

        Color nodeBg;
        Color nodeBorder;
        Widget nodeContent;

        if (isStagePast) {
          nodeBg = colors.brand;
          nodeBorder = colors.brand;
          nodeContent = const Icon(Icons.check, size: 10, color: Colors.white);
        } else if (isStageActive) {
          nodeBg = colors.brand.withValues(alpha: 0.2);
          nodeBorder = colors.brand;
          nodeContent = Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(
              color: colors.brand,
              shape: BoxShape.circle,
            ),
          );
        } else {
          nodeBg = colors.card;
          nodeBorder = colors.borderStrong;
          nodeContent = Container(
            width: 4,
            height: 4,
            decoration: BoxDecoration(
              color: colors.textMuted.withValues(alpha: 0.5),
              shape: BoxShape.circle,
            ),
          );
        }

        return Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 18,
              height: 18,
              decoration: BoxDecoration(
                color: nodeBg,
                shape: BoxShape.circle,
                border: Border.all(color: nodeBorder, width: 1.5),
              ),
              child: Center(child: nodeContent),
            ),
            const SizedBox(width: 4),
            Text(
              stageName,
              style: AppTypography.micro(
                color: isStageActive
                    ? colors.brand
                    : isStagePast
                        ? colors.textPrimary
                        : colors.textMuted,
              ).copyWith(
                fontWeight: isStageActive ? FontWeight.w700 : FontWeight.w500,
              ),
            ),
          ],
        );
      }),
    );
  }

  Widget _buildMetricPill({
    required String label,
    required String value,
    required AppColorScheme colors,
    Color? highlightColor,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: colors.card,
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: colors.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            '$label: ',
            style: AppTypography.micro(
              color: colors.textMuted,
            ),
          ),
          Text(
            value,
            style: AppTypography.codeSmall(
              color: highlightColor ?? colors.textPrimary,
            ).copyWith(fontSize: 10, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }
}
