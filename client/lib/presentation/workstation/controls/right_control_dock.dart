import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'components/ai_engine_status_card.dart';
import 'components/document_processors_card.dart';
import 'components/execution_options_section.dart';
import 'components/image_preprocessing_card.dart';
import 'components/smart_preset_section.dart';
import 'components/workstation_action_buttons.dart';
import 'quality_repair_dock.dart';
import 'trust_breakdown_panel.dart';

/// Right-hand control dock holding AI engine status, Smart Presets,
/// collapsible pipeline options, processor selectors, image preprocessing,
/// quality repair loop, and the primary execution CTA.
class RightControlDock extends ConsumerWidget {
  const RightControlDock({
    super.key,
    required this.settings,
    required this.onSettingsChanged,
    required this.onProcessRequested,
    this.onCancelRequested,
  });

  final ProcessSettings settings;
  final ValueChanged<ProcessSettings> onSettingsChanged;
  final Future<void> Function(ProcessSettings settings) onProcessRequested;
  final VoidCallback? onCancelRequested;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wsState = ref.watch(workstationProvider);
    final hasDoc = wsState.hasDocument;

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AiEngineStatusCard(settings: settings),
          const SizedBox(height: 12),
          SmartPresetSection(
            settings: settings,
            onSettingsChanged: onSettingsChanged,
          ),
          const SizedBox(height: 12),
          ExecutionOptionsSection(
            settings: settings,
            onSettingsChanged: onSettingsChanged,
          ),
          const SizedBox(height: 12),
          DocumentProcessorsCard(
            settings: settings,
            onSettingsChanged: onSettingsChanged,
          ),
          const SizedBox(height: 12),
          QualityRepairDock(
            settings: settings,
            onSettingsChanged: onSettingsChanged,
          ),
          const SizedBox(height: 12),
          if (hasDoc || wsState.allBBoxes.isNotEmpty) ...[
            const TrustBreakdownPanel(),
            const SizedBox(height: 12),
          ],
          ImagePreprocessingCard(
            settings: settings,
            onSettingsChanged: onSettingsChanged,
          ),
          const SizedBox(height: 16),
          WorkstationActionButtons(
            settings: settings,
            onProcessRequested: onProcessRequested,
            onCancelRequested: onCancelRequested,
          ),
        ],
      ),
    );
  }
}
