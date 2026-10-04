import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// Custom switch toggle component adhering to DocuVerse design rules.
class AppToggle extends StatelessWidget {
  const AppToggle({
    super.key,
    required this.value,
    this.onChanged,
    this.label,
    this.subtitle,
    this.leading,
    this.disabled = false,
    this.testId,
  });

  final bool value;
  final ValueChanged<bool>? onChanged;
  final String? label;
  final String? subtitle;
  final Widget? leading;
  final bool disabled;
  final String? testId;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final isInteractive = !disabled && onChanged != null;

    final toggleSwitch = MouseRegion(
      cursor:
          isInteractive ? SystemMouseCursors.click : SystemMouseCursors.basic,
      child: GestureDetector(
        onTap: isInteractive ? () => onChanged?.call(!value) : null,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeInOut,
          width: 40,
          height: 22,
          padding: const EdgeInsets.all(2),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            color: value ? colors.brand : colors.muted,
            border: Border.all(
              color: value
                  ? colors.brandAccent.withValues(alpha: 0.6)
                  : colors.borderStrong,
              width: 1,
            ),
          ),
          child: AnimatedAlign(
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeInOut,
            alignment: value ? Alignment.centerRight : Alignment.centerLeft,
            child: Container(
              width: 16,
              height: 16,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: Colors.white,
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.25),
                    blurRadius: 3,
                    offset: const Offset(0, 1),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );

    if (label == null && subtitle == null && leading == null) {
      return Opacity(
        opacity: disabled ? 0.45 : 1.0,
        child: toggleSwitch,
      );
    }

    return Opacity(
      opacity: disabled ? 0.45 : 1.0,
      child: InkWell(
        onTap: isInteractive ? () => onChanged?.call(!value) : null,
        borderRadius: BorderRadius.circular(6),
        hoverColor: colors.muted.withValues(alpha: 0.2),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              if (leading != null) ...[
                IconTheme(
                  data: IconThemeData(color: colors.textSecondary, size: 18),
                  child: leading!,
                ),
                const SizedBox(width: 10),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (label != null)
                      Text(
                        label!,
                        style: AppTypography.bodyMedium(
                          color: colors.textPrimary,
                        ).copyWith(fontWeight: FontWeight.w500),
                      ),
                    if (subtitle != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        subtitle!,
                        style: AppTypography.bodySmall(
                          color: colors.textMuted,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 12),
              toggleSwitch,
            ],
          ),
        ),
      ),
    );
  }
}
