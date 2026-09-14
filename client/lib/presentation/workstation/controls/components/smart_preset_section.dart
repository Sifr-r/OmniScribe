import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import '../smart_preset_selector.dart';

/// Subwidget managing document quality presets, providing quality preset selector
/// chips, description tooltips, and fast/balanced/accuracy switches.
class SmartPresetSection extends ConsumerWidget {
  const SmartPresetSection({
    super.key,
    required this.settings,
    required this.onSettingsChanged,
  });

  final ProcessSettings settings;
  final ValueChanged<ProcessSettings> onSettingsChanged;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wsState = ref.watch(workstationProvider);

    return SmartPresetSelector(
      settings: settings,
      filename: wsState.filename,
      onPresetSelected: (preset) {
        onSettingsChanged(preset.apply(settings));
      },
    );
  }
}
