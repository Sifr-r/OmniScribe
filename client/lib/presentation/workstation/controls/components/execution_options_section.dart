import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/app_select.dart';
import 'package:omniscribe_client/presentation/common/app_toggle.dart';

/// Subwidget for configuring OCR execution options including target model dropdown,
/// whitespace recall toggle, text layer recall toggle, and advanced pipeline parameters.
class ExecutionOptionsSection extends StatefulWidget {
  const ExecutionOptionsSection({
    super.key,
    required this.settings,
    required this.onSettingsChanged,
  });

  final ProcessSettings settings;
  final ValueChanged<ProcessSettings> onSettingsChanged;

  @override
  State<ExecutionOptionsSection> createState() =>
      _ExecutionOptionsSectionState();
}

class _ExecutionOptionsSectionState extends State<ExecutionOptionsSection> {
  bool _isExpanded = false;

  static const List<({String id, String label, String subtitle})> _knownModels = [
    (
      id: 'allenai/olmocr-2-7b',
      label: 'olmOCR 2 7B',
      subtitle: 'Default specialized document OCR VLM',
    ),
    (
      id: 'google/gemini-2.5-flash',
      label: 'Gemini 2.5 Flash',
      subtitle: 'High-speed cloud multimodal reasoning',
    ),
    (
      id: 'openai/gpt-4o-mini',
      label: 'GPT-4o mini',
      subtitle: 'Efficient multimodal cloud processing',
    ),
    (
      id: 'meta-llama/llama-3.2-11b-vision',
      label: 'Llama 3.2 11B Vision',
      subtitle: 'Open-weights vision-language model',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final s = widget.settings;

    // Prepare model selection items ensuring current model is always selectable
    final currentModel = s.model.isNotEmpty ? s.model : 'allenai/olmocr-2-7b';
    final hasCurrentInKnown = _knownModels.any((m) => m.id == currentModel);

    final modelItems = [
      if (!hasCurrentInKnown && currentModel.isNotEmpty)
        AppSelectItem<String>(
          value: currentModel,
          label: currentModel,
          subtitle: 'Active runtime model',
        ),
      ..._knownModels.map(
        (m) => AppSelectItem<String>(
          value: m.id,
          label: m.label,
          subtitle: m.subtitle,
        ),
      ),
    ];

    return AppCard(
      padding: AppCardPadding.sm,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: () => setState(() => _isExpanded = !_isExpanded),
            borderRadius: BorderRadius.circular(4),
            child: Padding(
              padding: const EdgeInsets.all(6),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Flexible(
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          Icons.settings_input_component_rounded,
                          size: 14,
                          color: colors.textMuted,
                        ),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(
                            'ADVANCED PIPELINE OPTIONS',
                            overflow: TextOverflow.ellipsis,
                            style: AppTypography.micro(
                              color: colors.textMuted,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 4),
                  Icon(
                    _isExpanded
                        ? Icons.expand_less_rounded
                        : Icons.expand_more_rounded,
                    size: 18,
                    color: colors.textMuted,
                  ),
                ],
              ),
            ),
          ),
          if (_isExpanded) ...[
            const SizedBox(height: 6),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: colors.cardRaised,
                borderRadius: BorderRadius.circular(6),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // 1. Target Model Dropdown
                  AppSelect<String>(
                    label: 'Target Model',
                    value: currentModel,
                    items: modelItems,
                    onChanged: (model) {
                      if (model != null && model.isNotEmpty) {
                        widget.onSettingsChanged(s.copyWith(model: model));
                      }
                    },
                  ),
                  const SizedBox(height: 10),

                  // 2. Recall Boosters: Whitespace & Text Layer
                  AppToggle(
                    label: 'Whitespace Recall Booster',
                    subtitle:
                        'Recovers faint text-line candidates from whitespace gaps',
                    value: s.whitespaceRecall,
                    onChanged: (v) => widget.onSettingsChanged(
                      s.copyWith(whitespaceRecall: v),
                    ),
                  ),
                  AppToggle(
                    label: 'PDF Text Layer Recall',
                    subtitle:
                        'Extracts embedded digital text streams for layout fusion',
                    value: s.textLayerRecall,
                    onChanged: (v) => widget.onSettingsChanged(
                      s.copyWith(textLayerRecall: v),
                    ),
                  ),
                  const SizedBox(height: 10),

                  // 3. Pipeline Mode Selector
                  AppSelect<PipelineMode>(
                    label: 'Pipeline Mode',
                    value: s.pipelineMode,
                    items: const [
                      AppSelectItem(
                        value: PipelineMode.hybrid,
                        label: 'Hybrid (OCR + VLM)',
                        subtitle:
                            'Combines layout grounding with multimodal reasoning',
                      ),
                      AppSelectItem(
                        value: PipelineMode.grounded,
                        label: 'Grounded BBox',
                        subtitle:
                            'Fast bounding-box layout parsing without VLM pass',
                      ),
                      AppSelectItem(
                        value: PipelineMode.groundedNative,
                        label: 'Grounded Native',
                        subtitle: 'Direct token-level model grounding',
                      ),
                    ],
                    onChanged: (mode) {
                      if (mode != null) {
                        widget.onSettingsChanged(s.copyWith(pipelineMode: mode));
                      }
                    },
                  ),
                  const SizedBox(height: 10),

                  // 4. Dense Mode & Spellcheck Grid
                  Row(
                    children: [
                      Expanded(
                        child: AppSelect<DenseMode>(
                          label: 'Dense Mode',
                          value: s.denseMode,
                          items: const [
                            AppSelectItem(
                                value: DenseMode.auto, label: 'Auto'),
                            AppSelectItem(value: DenseMode.on, label: 'On'),
                            AppSelectItem(value: DenseMode.off, label: 'Off'),
                          ],
                          onChanged: (mode) {
                            if (mode != null) {
                              widget.onSettingsChanged(
                                  s.copyWith(denseMode: mode));
                            }
                          },
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: AppSelect<SpellcheckMode>(
                          label: 'Spellcheck',
                          value: s.spellcheck,
                          items: const [
                            AppSelectItem(
                                value: SpellcheckMode.none, label: 'None'),
                            AppSelectItem(
                                value: SpellcheckMode.enUS,
                                label: 'English (US)'),
                            AppSelectItem(
                                value: SpellcheckMode.ar, label: 'Arabic'),
                            AppSelectItem(
                                value: SpellcheckMode.de, label: 'German'),
                            AppSelectItem(
                                value: SpellcheckMode.es, label: 'Spanish'),
                            AppSelectItem(
                                value: SpellcheckMode.fr, label: 'French'),
                          ],
                          onChanged: (mode) {
                            if (mode != null) {
                              widget.onSettingsChanged(
                                  s.copyWith(spellcheck: mode));
                            }
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),

                  // 5. Dense Threshold Slider
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Flexible(
                            child: Text(
                              'Dense Switch Threshold',
                              style: AppTypography.labelMedium(
                                color: colors.textPrimary,
                              ).copyWith(fontWeight: FontWeight.w600),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            '${s.denseThreshold} boxes',
                            style: AppTypography.codeSmall(
                              color: colors.brand,
                            ).copyWith(fontWeight: FontWeight.w600),
                          ),
                        ],
                      ),
                      SliderTheme(
                        data: SliderThemeData(
                          activeTrackColor: colors.brand,
                          inactiveTrackColor: colors.card,
                          thumbColor: colors.brand,
                          overlayColor: colors.brand.withValues(alpha: 0.15),
                          trackHeight: 3,
                          thumbShape: const RoundSliderThumbShape(
                              enabledThumbRadius: 6),
                        ),
                        child: Slider(
                          value: s.denseThreshold.toDouble(),
                          min: 50,
                          max: 300,
                          divisions: 25,
                          onChanged: (val) {
                            widget.onSettingsChanged(
                                s.copyWith(denseThreshold: val.round()));
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),

                  // 6. DPI Slider
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Flexible(
                            child: Text(
                              'Raster DPI',
                              style: AppTypography.labelMedium(
                                color: colors.textPrimary,
                              ).copyWith(fontWeight: FontWeight.w600),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            '${s.dpi} DPI',
                            style: AppTypography.codeSmall(
                              color: colors.brand,
                            ).copyWith(fontWeight: FontWeight.w600),
                          ),
                        ],
                      ),
                      SliderTheme(
                        data: SliderThemeData(
                          activeTrackColor: colors.brand,
                          inactiveTrackColor: colors.card,
                          thumbColor: colors.brand,
                          overlayColor: colors.brand.withValues(alpha: 0.15),
                          trackHeight: 3,
                          thumbShape: const RoundSliderThumbShape(
                              enabledThumbRadius: 6),
                        ),
                        child: Slider(
                          value: s.dpi.toDouble(),
                          min: 150,
                          max: 400,
                          divisions: 25,
                          onChanged: (val) {
                            widget.onSettingsChanged(
                                s.copyWith(dpi: val.round()));
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),

                  // 7. Page Concurrency Slider
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Flexible(
                            child: Text(
                              'Page Concurrency',
                              style: AppTypography.labelMedium(
                                color: colors.textPrimary,
                              ).copyWith(fontWeight: FontWeight.w600),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            '${s.concurrency} workers',
                            style: AppTypography.codeSmall(
                              color: colors.brand,
                            ).copyWith(fontWeight: FontWeight.w600),
                          ),
                        ],
                      ),
                      SliderTheme(
                        data: SliderThemeData(
                          activeTrackColor: colors.brand,
                          inactiveTrackColor: colors.card,
                          thumbColor: colors.brand,
                          overlayColor: colors.brand.withValues(alpha: 0.15),
                          trackHeight: 3,
                          thumbShape: const RoundSliderThumbShape(
                              enabledThumbRadius: 6),
                        ),
                        child: Slider(
                          value: s.concurrency.toDouble(),
                          min: 1,
                          max: 16,
                          divisions: 15,
                          onChanged: (val) {
                            widget.onSettingsChanged(
                                s.copyWith(concurrency: val.round()));
                          },
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),

                  // 8. Async Background Queue Toggle
                  AppToggle(
                    label: 'Background Queue (Async)',
                    value: s.useAsync,
                    onChanged: (v) =>
                        widget.onSettingsChanged(s.copyWith(useAsync: v)),
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
