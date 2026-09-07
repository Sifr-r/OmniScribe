import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/app_select.dart';
import 'package:omniscribe_client/presentation/common/app_toggle.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';
import 'package:omniscribe_client/presentation/providers/ai_setup_wizard_modal.dart';
import 'quality_repair_dock.dart';
import 'smart_preset_selector.dart';
import 'trust_breakdown_panel.dart';

/// Right-hand control dock holding AI engine status, Smart Presets,
/// collapsible pipeline options, processor selectors, image preprocessing,
/// quality repair loop, and the primary execution CTA.
class RightControlDock extends ConsumerStatefulWidget {
  const RightControlDock({
    super.key,
    required this.settings,
    required this.onSettingsChanged,
    required this.onProcessRequested,
  });

  final ProcessSettings settings;
  final ValueChanged<ProcessSettings> onSettingsChanged;
  final Future<void> Function(ProcessSettings settings) onProcessRequested;

  @override
  ConsumerState<RightControlDock> createState() => _RightControlDockState();
}

class _RightControlDockState extends ConsumerState<RightControlDock> {
  bool _isPipelineAdvancedExpanded = false;
  bool _isPreprocessingExpanded = false;

  void _toggleProcessor(String processorId) {
    final proc = DocumentProcessorName.tryFromString(processorId);
    if (proc == null) return;
    final current = widget.settings.documentProcessors;
    final updated = current.contains(proc)
        ? current.where((p) => p != proc).toList()
        : [...current, proc];
    widget.onSettingsChanged(
        widget.settings.copyWith(documentProcessors: updated));
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);
    final settingsState = ref.watch(settingsStateProvider);
    final s = widget.settings;

    final isProcessing = wsState.isProcessing;
    final hasDoc = wsState.hasDocument;

    final activeProviderId = settingsState.activeProviderId;
    final activeModel = (settingsState.runtimeConfig?.model.isNotEmpty ?? false)
        ? settingsState.runtimeConfig!.model
        : (s.model.isNotEmpty ? s.model : 'allenai/olmocr-2-7b');

    final isConfigured = s.apiBase.isNotEmpty && activeModel.isNotEmpty;

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 1. Non-Technical AI Engine Status & Quick Setup Card
          AppCard(
            padding: AppCardPadding.md,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Flexible(
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Container(
                            width: 26,
                            height: 26,
                            decoration: BoxDecoration(
                              color: colors.brand.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(6),
                            ),
                            child: Center(
                              child: Icon(
                                Icons.psychology_rounded,
                                size: 15,
                                color: colors.brand,
                              ),
                            ),
                          ),
                          const SizedBox(width: 6),
                          Flexible(
                            child: Text(
                              'AI Engine',
                              overflow: TextOverflow.ellipsis,
                              style: AppTypography.titleSmall(
                                color: colors.textPrimary,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 6),
                    AppBadge(
                      label: isConfigured ? 'READY' : 'SETUP NEEDED',
                      variant: isConfigured
                          ? AppBadgeVariant.success
                          : AppBadgeVariant.warning,
                      size: AppBadgeSize.sm,
                      dot: true,
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  decoration: BoxDecoration(
                    color: colors.cardRaised,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: colors.border),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              activeProviderId.toUpperCase(),
                              style: AppTypography.micro(
                                color: colors.brand,
                              ).copyWith(fontWeight: FontWeight.w700),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              activeModel,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: AppTypography.codeSmall(
                                color: colors.textPrimary,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      AppButton(
                        text: 'Quick Setup',
                        variant: AppButtonVariant.outline,
                        size: AppButtonSize.sm,
                        icon: const Icon(Icons.tune_rounded, size: 12),
                        onPressed: () {
                          AISetupWizardModal.show(
                            context,
                            onComplete: () {
                              ref.read(settingsStateProvider.notifier).load();
                            },
                          );
                        },
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),

          // 2. Smart Presets Selector
          SmartPresetSelector(
            settings: s,
            filename: wsState.filename,
            onPresetSelected: (preset) {
              widget.onSettingsChanged(preset.apply(s));
            },
          ),
          const SizedBox(height: 12),

          // 3. Collapsible "Advanced Pipeline Options" Accordion (Collapsed by default)
          AppCard(
            padding: AppCardPadding.sm,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                InkWell(
                  onTap: () => setState(() => _isPipelineAdvancedExpanded =
                      !_isPipelineAdvancedExpanded),
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
                              Icon(Icons.settings_input_component_rounded,
                                  size: 14, color: colors.textMuted),
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
                          _isPipelineAdvancedExpanded
                              ? Icons.expand_less_rounded
                              : Icons.expand_more_rounded,
                          size: 18,
                          color: colors.textMuted,
                        ),
                      ],
                    ),
                  ),
                ),
                if (_isPipelineAdvancedExpanded) ...[
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
                        // Pipeline Mode Selector
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
                              widget.onSettingsChanged(
                                  s.copyWith(pipelineMode: mode));
                            }
                          },
                        ),
                        const SizedBox(height: 10),

                        // Dense Mode & Spellcheck Grid
                        Row(
                          children: [
                            Expanded(
                              child: AppSelect<DenseMode>(
                                label: 'Dense Mode',
                                value: s.denseMode,
                                items: const [
                                  AppSelectItem(
                                      value: DenseMode.auto, label: 'Auto'),
                                  AppSelectItem(
                                      value: DenseMode.on, label: 'On'),
                                  AppSelectItem(
                                      value: DenseMode.off, label: 'Off'),
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
                                      value: SpellcheckMode.none,
                                      label: 'None'),
                                  AppSelectItem(
                                      value: SpellcheckMode.enUS,
                                      label: 'English (US)'),
                                  AppSelectItem(
                                      value: SpellcheckMode.ar,
                                      label: 'Arabic'),
                                  AppSelectItem(
                                      value: SpellcheckMode.de,
                                      label: 'German'),
                                  AppSelectItem(
                                      value: SpellcheckMode.es,
                                      label: 'Spanish'),
                                  AppSelectItem(
                                      value: SpellcheckMode.fr,
                                      label: 'French'),
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

                        // Dense Threshold Slider
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              mainAxisAlignment:
                                  MainAxisAlignment.spaceBetween,
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
                                overlayColor:
                                    colors.brand.withValues(alpha: 0.15),
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

                        // DPI Slider
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              mainAxisAlignment:
                                  MainAxisAlignment.spaceBetween,
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
                                overlayColor:
                                    colors.brand.withValues(alpha: 0.15),
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

                        // Page Concurrency Slider
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              mainAxisAlignment:
                                  MainAxisAlignment.spaceBetween,
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
                                overlayColor:
                                    colors.brand.withValues(alpha: 0.15),
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
                        AppToggle(
                          label: 'Background Queue (Async)',
                          value: s.useAsync,
                          onChanged: (v) => widget
                              .onSettingsChanged(s.copyWith(useAsync: v)),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 12),

          // 4. Document Processors Card
          AppCard(
            padding: AppCardPadding.md,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionHeader(title: 'Document Processors'),
                Text(
                  'Select analyzers to enrich document structure and reading order:',
                  style: AppTypography.bodySmall(
                    color: colors.textMuted,
                  ),
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: DocumentProcessorInfo.all.map((info) {
                    final isSelected =
                        s.documentProcessors.any((p) => p.value == info.id);
                    return Tooltip(
                      message: info.description,
                      child: InkWell(
                        onTap: () => _toggleProcessor(info.id),
                        borderRadius: BorderRadius.circular(6),
                        child: AnimatedContainer(
                          duration: const Duration(milliseconds: 150),
                          padding: const EdgeInsets.symmetric(
                              horizontal: 10, vertical: 6),
                          decoration: BoxDecoration(
                            color: isSelected
                                ? colors.brand.withValues(alpha: 0.15)
                                : colors.cardRaised,
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                              color: isSelected
                                  ? colors.brand.withValues(alpha: 0.5)
                                  : colors.border,
                              width: 1.0,
                            ),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (isSelected) ...[
                                Icon(Icons.check,
                                    size: 12, color: colors.brand),
                                const SizedBox(width: 4),
                              ],
                              Text(
                                info.label,
                                style: AppTypography.bodySmall(
                                  color: isSelected
                                      ? colors.brand
                                      : colors.textMuted,
                                ).copyWith(
                                  fontWeight: isSelected
                                      ? FontWeight.w600
                                      : FontWeight.w500,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    );
                  }).toList(),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),

          // 5. Quality Repair Loop Dock
          QualityRepairDock(
            settings: s,
            onSettingsChanged: widget.onSettingsChanged,
          ),
          const SizedBox(height: 12),

          // 6. Trust Breakdown Panel (When document is present or bboxes exist)
          if (hasDoc || wsState.allBBoxes.isNotEmpty) ...[
            const TrustBreakdownPanel(),
            const SizedBox(height: 12),
          ],

          // 7. Advanced Image Preprocessing Accordion Card
          AppCard(
            padding: AppCardPadding.sm,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                InkWell(
                  onTap: () => setState(() => _isPreprocessingExpanded =
                      !_isPreprocessingExpanded),
                  borderRadius: BorderRadius.circular(4),
                  child: Padding(
                    padding: const EdgeInsets.all(6),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Row(
                          children: [
                            Icon(Icons.tune_rounded,
                                size: 14, color: colors.textMuted),
                            const SizedBox(width: 6),
                            Text(
                              'IMAGE PREPROCESSING',
                              style: AppTypography.micro(
                                color: colors.textMuted,
                              ),
                            ),
                          ],
                        ),
                        Icon(
                          _isPreprocessingExpanded
                              ? Icons.expand_less_rounded
                              : Icons.expand_more_rounded,
                          size: 18,
                          color: colors.textMuted,
                        ),
                      ],
                    ),
                  ),
                ),
                if (_isPreprocessingExpanded) ...[
                  const SizedBox(height: 6),
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: colors.cardRaised,
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Column(
                      children: [
                        AppToggle(
                          label: 'Orientation Detection',
                          value: s.orientationDetection,
                          onChanged: (v) => widget.onSettingsChanged(
                              s.copyWith(orientationDetection: v)),
                        ),
                        AppToggle(
                          label: 'Deskew Image',
                          value: s.deskew,
                          onChanged: (v) =>
                              widget.onSettingsChanged(s.copyWith(deskew: v)),
                        ),
                        AppToggle(
                          label: 'Denoise Image',
                          value: s.denoise,
                          onChanged: (v) =>
                              widget.onSettingsChanged(s.copyWith(denoise: v)),
                        ),
                        AppToggle(
                          label: 'Normalize Contrast',
                          value: s.normalizeContrast,
                          onChanged: (v) => widget.onSettingsChanged(
                              s.copyWith(normalizeContrast: v)),
                        ),
                        AppToggle(
                          label: 'Crop Cleanup',
                          value: s.cropCleanup,
                          onChanged: (v) => widget
                              .onSettingsChanged(s.copyWith(cropCleanup: v)),
                        ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),

          // 8. Primary Process Action CTA
          AppButton(
            text: isProcessing ? 'Processing Document...' : 'Process Document',
            variant: AppButtonVariant.primary,
            size: AppButtonSize.lg,
            fullWidth: true,
            icon: const Icon(Icons.bolt_rounded, size: 18),
            loading: isProcessing,
            disabled: !hasDoc && !isProcessing,
            onPressed: () => widget.onProcessRequested(s),
          ),
        ],
      ),
    );
  }
}
