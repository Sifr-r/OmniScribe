import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/features/workstation/smart_preset.dart';
import 'package:omniscribe_client/shared/widgets/app_badge.dart';
import 'package:omniscribe_client/shared/widgets/app_card.dart';

export 'package:omniscribe_client/features/workstation/smart_preset.dart';

/// Extension providing icon and badge variant presentation metadata for [SmartPreset].
extension SmartPresetPresentation on SmartPreset {
  IconData get icon {
    switch (iconName) {
      case 'document':
        return Icons.description_outlined;
      case 'receipt':
        return Icons.receipt_long_rounded;
      case 'handwriting':
        return Icons.draw_rounded;
      case 'history':
        return Icons.healing_rounded;
      case 'bolt':
        return Icons.bolt_rounded;
      case 'target':
        return Icons.verified_rounded;
      default:
        return Icons.auto_awesome_rounded;
    }
  }

  AppBadgeVariant get badgeVariant {
    switch (id) {
      case 'standard':
        return AppBadgeVariant.brand;
      case 'receipt':
        return AppBadgeVariant.warning;
      case 'handwriting':
        return AppBadgeVariant.info;
      case 'historical':
        return AppBadgeVariant.error;
      case 'fast':
        return AppBadgeVariant.success;
      case 'deep':
        return AppBadgeVariant.brand;
      default:
        return AppBadgeVariant.neutral;
    }
  }
}

/// Visual card selector for OmniScribe Smart Presets.
class SmartPresetSelector extends StatefulWidget {
  const SmartPresetSelector({
    super.key,
    required this.settings,
    required this.onPresetSelected,
    this.filename,
  });

  final ProcessSettings settings;
  final ValueChanged<SmartPreset> onPresetSelected;
  final String? filename;

  @override
  State<SmartPresetSelector> createState() => _SmartPresetSelectorState();
}

class _SmartPresetSelectorState extends State<SmartPresetSelector> {
  String? _dismissedSuggestionFilename;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final activePreset = SmartPreset.detectActivePreset(widget.settings);
    final suggestedPreset = SmartPreset.suggestForFilename(widget.filename);

    final showSuggestion = suggestedPreset != SmartPreset.standard &&
        suggestedPreset != activePreset &&
        _dismissedSuggestionFilename != widget.filename;

    return AppCard(
      padding: AppCardPadding.md,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Section Header with Custom Indicator
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Flexible(
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.auto_awesome_rounded,
                        size: 16, color: colors.brand),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        'Smart Presets',
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
              if (activePreset != null)
                AppBadge(
                  label: activePreset.badgeLabel,
                  variant: activePreset.badgeVariant,
                  size: AppBadgeSize.sm,
                )
              else
                const AppBadge(
                  label: 'Custom Settings',
                  variant: AppBadgeVariant.neutral,
                  size: AppBadgeSize.sm,
                ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            'One-click optimization tailored to your document structure:',
            style: AppTypography.bodySmall(
              color: colors.textMuted,
            ),
          ),
          const SizedBox(height: 12),

          // Auto-detection Banner
          if (showSuggestion) ...[
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: colors.brand.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                  color: colors.brand.withValues(alpha: 0.4),
                  width: 1,
                ),
              ),
              child: Row(
                children: [
                  Icon(
                    Icons.lightbulb_outline_rounded,
                    size: 18,
                    color: colors.brand,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text.rich(
                      TextSpan(
                        style: AppTypography.bodySmall(
                          color: colors.textPrimary,
                        ),
                        children: [
                          const TextSpan(text: 'Detected format: '),
                          TextSpan(
                            text: suggestedPreset.title,
                            style: const TextStyle(fontWeight: FontWeight.w700),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  InkWell(
                    onTap: () => widget.onPresetSelected(suggestedPreset),
                    borderRadius: BorderRadius.circular(6),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(
                        color: colors.brand,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        'Apply',
                        style: AppTypography.labelMedium(
                          color: colors.brandForeground,
                        ).copyWith(fontWeight: FontWeight.w600),
                      ),
                    ),
                  ),
                  const SizedBox(width: 4),
                  IconButton(
                    icon: Icon(Icons.close_rounded,
                        size: 14, color: colors.textMuted),
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(
                      minWidth: 24,
                      minHeight: 24,
                    ),
                    tooltip: 'Dismiss suggestion',
                    onPressed: () {
                      setState(() {
                        _dismissedSuggestionFilename = widget.filename;
                      });
                    },
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
          ],

          // Presets Grid / List
          LayoutBuilder(
            builder: (context, constraints) {
              final isCompact = constraints.maxWidth < 360;

              return GridView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: isCompact ? 1 : 2,
                  crossAxisSpacing: 8,
                  mainAxisSpacing: 8,
                  mainAxisExtent: isCompact ? 56 : 84,
                ),
                itemCount: SmartPreset.allPresets.length,
                itemBuilder: (context, index) {
                  final preset = SmartPreset.allPresets[index];
                  final isSelected = activePreset?.id == preset.id;

                  return Tooltip(
                    message: preset.description,
                    waitDuration: const Duration(milliseconds: 300),
                    child: _PresetCardItem(
                      preset: preset,
                      isSelected: isSelected,
                      isCompact: isCompact,
                      onTap: () => widget.onPresetSelected(preset),
                    ),
                  );
                },
              );
            },
          ),
        ],
      ),
    );
  }
}

class _PresetCardItem extends StatefulWidget {
  const _PresetCardItem({
    required this.preset,
    required this.isSelected,
    required this.isCompact,
    required this.onTap,
  });

  final SmartPreset preset;
  final bool isSelected;
  final bool isCompact;
  final VoidCallback onTap;

  @override
  State<_PresetCardItem> createState() => _PresetCardItemState();
}

class _PresetCardItemState extends State<_PresetCardItem> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final p = widget.preset;

    final borderColor = widget.isSelected
        ? colors.brand
        : _isHovered
            ? colors.borderStrong
            : colors.border;

    final bgColor = widget.isSelected
        ? colors.brand.withValues(alpha: 0.12)
        : _isHovered
            ? colors.cardRaised
            : colors.card;

    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(
            color: bgColor,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: borderColor,
              width: widget.isSelected ? 1.5 : 1.0,
            ),
            boxShadow: widget.isSelected
                ? [
                    BoxShadow(
                      color: colors.brand.withValues(alpha: 0.15),
                      blurRadius: 8,
                      offset: const Offset(0, 2),
                    ),
                  ]
                : null,
          ),
          child: widget.isCompact
              ? Row(
                  children: [
                    Container(
                      width: 26,
                      height: 26,
                      decoration: BoxDecoration(
                        color: widget.isSelected
                            ? colors.brand
                            : colors.cardRaised,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Center(
                        child: Icon(
                          p.icon,
                          size: 13,
                          color: widget.isSelected
                              ? colors.brandForeground
                              : colors.textSecondary,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: MainAxisAlignment.center,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            p.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: AppTypography.labelMedium(
                              color: widget.isSelected
                                  ? colors.brand
                                  : colors.textPrimary,
                            ).copyWith(
                              fontWeight: widget.isSelected
                                  ? FontWeight.w700
                                  : FontWeight.w600,
                            ),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            p.description,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: AppTypography.micro(
                              color: colors.textMuted,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 6),
                    if (widget.isSelected)
                      Container(
                        padding: const EdgeInsets.all(2),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: colors.brand,
                        ),
                        child: Icon(
                          Icons.check_rounded,
                          size: 10,
                          color: colors.brandForeground,
                        ),
                      )
                    else
                      AppBadge(
                        label: p.badgeLabel,
                        variant: p.badgeVariant,
                        size: AppBadgeSize.sm,
                      ),
                  ],
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    // Top Row: Icon + Badge / Checkmark
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Container(
                          width: 24,
                          height: 24,
                          decoration: BoxDecoration(
                            color: widget.isSelected
                                ? colors.brand
                                : colors.cardRaised,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Center(
                            child: Icon(
                              p.icon,
                              size: 13,
                              color: widget.isSelected
                                  ? colors.brandForeground
                                  : colors.textSecondary,
                            ),
                          ),
                        ),
                        const SizedBox(width: 6),
                        if (widget.isSelected)
                          Container(
                            padding: const EdgeInsets.all(2),
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: colors.brand,
                            ),
                            child: Icon(
                              Icons.check_rounded,
                              size: 10,
                              color: colors.brandForeground,
                            ),
                          )
                        else
                          Flexible(
                            child: AppBadge(
                              label: p.badgeLabel,
                              variant: p.badgeVariant,
                              size: AppBadgeSize.sm,
                            ),
                          ),
                      ],
                    ),

                    // Title & One-line Summary
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          p.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: AppTypography.labelMedium(
                            color: widget.isSelected
                                ? colors.brand
                                : colors.textPrimary,
                          ).copyWith(
                            fontWeight: widget.isSelected
                                ? FontWeight.w700
                                : FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          p.description,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: AppTypography.micro(
                            color: colors.textMuted,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
        ),
      ),
    );
  }
}
