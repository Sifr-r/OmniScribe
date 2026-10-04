import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// DocuVerse Section Header primitive.
///
/// Renders an uppercase micro label with an optional trailing action or count badge,
/// positioned cleanly inside cards and panels.
class SectionHeader extends StatelessWidget {
  const SectionHeader({
    super.key,
    required this.title,
    this.action,
    this.badge,
    this.showDivider = false,
    this.testId,
  });

  final String title;
  final Widget? action;
  final Widget? badge;
  final bool showDivider;
  final String? testId;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    final headerRow = Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Flexible(
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Flexible(
                child: Text(
                  title.toUpperCase(),
                  style: AppTypography.micro(
                    color: colors.textMuted,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (badge != null) ...[
                const SizedBox(width: 8),
                badge!,
              ],
            ],
          ),
        ),
        if (action != null) ...[
          const SizedBox(width: 8),
          action!,
        ],
      ],
    );

    if (!showDivider) {
      return headerRow;
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        headerRow,
        const SizedBox(height: 8),
        Divider(height: 1, color: colors.border),
        const SizedBox(height: 12),
      ],
    );
  }
}
