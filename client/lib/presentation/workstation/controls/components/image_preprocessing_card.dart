import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/app_toggle.dart';

/// Subwidget for image preprocessing toggles (orientation detection,
/// deskew, denoise, contrast normalization, crop cleanup).
class ImagePreprocessingCard extends StatefulWidget {
  const ImagePreprocessingCard({
    super.key,
    required this.settings,
    required this.onSettingsChanged,
  });

  final ProcessSettings settings;
  final ValueChanged<ProcessSettings> onSettingsChanged;

  @override
  State<ImagePreprocessingCard> createState() => _ImagePreprocessingCardState();
}

class _ImagePreprocessingCardState extends State<ImagePreprocessingCard> {
  bool _isExpanded = false;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final s = widget.settings;

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
                      s.copyWith(orientationDetection: v),
                    ),
                  ),
                  AppToggle(
                    label: 'Deskew Image',
                    value: s.deskew,
                    onChanged: (v) => widget.onSettingsChanged(
                      s.copyWith(deskew: v),
                    ),
                  ),
                  AppToggle(
                    label: 'Denoise Image',
                    value: s.denoise,
                    onChanged: (v) => widget.onSettingsChanged(
                      s.copyWith(denoise: v),
                    ),
                  ),
                  AppToggle(
                    label: 'Normalize Contrast',
                    value: s.normalizeContrast,
                    onChanged: (v) => widget.onSettingsChanged(
                      s.copyWith(normalizeContrast: v),
                    ),
                  ),
                  AppToggle(
                    label: 'Crop Cleanup',
                    value: s.cropCleanup,
                    onChanged: (v) => widget.onSettingsChanged(
                      s.copyWith(cropCleanup: v),
                    ),
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
