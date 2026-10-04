import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/features/jobs/job_orchestration_notifier.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';

/// Subwidget rendering the primary "Process Document" execution button
/// and dynamic cancellation controls during active OCR runs.
class WorkstationActionButtons extends ConsumerWidget {
  const WorkstationActionButtons({
    super.key,
    required this.settings,
    required this.onProcessRequested,
    this.onCancelRequested,
  });

  final ProcessSettings settings;
  final Future<void> Function(ProcessSettings settings) onProcessRequested;
  final VoidCallback? onCancelRequested;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wsState = ref.watch(workstationProvider);
    final job = ref.watch(jobOrchestrationProvider);
    final isProcessing = job.isProcessing;
    final hasDoc = wsState.hasDocument;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppButton(
          text: isProcessing ? 'Processing Document...' : 'Process Document',
          variant: AppButtonVariant.primary,
          size: AppButtonSize.lg,
          fullWidth: true,
          icon: const Icon(Icons.bolt_rounded, size: 18),
          loading: isProcessing,
          disabled: !hasDoc && !isProcessing,
          onPressed: () => onProcessRequested(settings),
        ),
        if (isProcessing) ...[
          const SizedBox(height: 8),
          AppButton(
            text: 'Cancel Processing',
            variant: AppButtonVariant.outline,
            size: AppButtonSize.md,
            fullWidth: true,
            icon: const Icon(Icons.close_rounded, size: 16),
            onPressed: () {
              ref.read(workstationProvider.notifier).cancelOcr();
              onCancelRequested?.call();
            },
          ),
        ],
      ],
    );
  }
}
