import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// Semantic variants for [AppBadge].
enum AppBadgeVariant {
  /// Neutral gray tag / version pill
  neutral,

  /// Indigo / Brand accent
  brand,

  /// Emerald / High confidence / Success
  success,

  /// Amber / Attention / Warning
  warning,

  /// Red / Error / Danger
  error,

  /// Sky Blue / Information
  info,
}

/// Fill styling mode for [AppBadge].
enum AppBadgeStyle {
  /// 15-25% tint background + matching border + 100% text
  filled,

  /// Transparent background + matching border + 100% text
  outline,
}

/// Size tier for [AppBadge].
enum AppBadgeSize {
  /// Height 20px, font 10px (Default)
  sm(height: 20, horizontalPadding: 8, fontSize: 10, iconSize: 10, dotSize: 6),

  /// Height 24px, font 12px
  md(height: 24, horizontalPadding: 10, fontSize: 12, iconSize: 12, dotSize: 7);

  const AppBadgeSize({
    required this.height,
    required this.horizontalPadding,
    required this.fontSize,
    required this.iconSize,
    required this.dotSize,
  });

  final double height;
  final double horizontalPadding;
  final double fontSize;
  final double iconSize;
  final double dotSize;
}

/// Universal DocuVerse pill badge component.
class AppBadge extends StatelessWidget {
  const AppBadge({
    super.key,
    this.label,
    this.child,
    this.icon,
    this.variant = AppBadgeVariant.neutral,
    this.style = AppBadgeStyle.filled,
    this.size = AppBadgeSize.sm,
    this.dot = false,
    this.isPulsing = false,
    this.onTap,
    this.onDelete,
    this.testId,
  }) : assert(label != null || child != null,
            'Badge must have either label or child');

  final String? label;
  final Widget? child;
  final Widget? icon;
  final AppBadgeVariant variant;
  final AppBadgeStyle style;
  final AppBadgeSize size;
  final bool dot;
  final bool isPulsing;
  final VoidCallback? onTap;
  final VoidCallback? onDelete;
  final String? testId;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final colorConfig = _resolveBadgeColors(colors);

    Widget badgeContent = Row(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        if (dot) ...[
          Container(
            width: size.dotSize,
            height: size.dotSize,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: colorConfig.textColor,
            ),
          ),
          const SizedBox(width: 5),
        ],
        if (icon != null) ...[
          IconTheme(
            data: IconThemeData(
              color: colorConfig.textColor,
              size: size.iconSize,
            ),
            child: icon!,
          ),
          const SizedBox(width: 4),
        ],
        if (child != null)
          DefaultTextStyle(
            style: AppTypography.micro(color: colorConfig.textColor).copyWith(
              fontSize: size.fontSize,
              fontWeight: FontWeight.w600,
            ),
            child: child!,
          )
        else if (label != null)
          Flexible(
            child: Text(
              label!,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: AppTypography.micro(color: colorConfig.textColor).copyWith(
                fontSize: size.fontSize,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        if (onDelete != null) ...[
          const SizedBox(width: 4),
          MouseRegion(
            cursor: SystemMouseCursors.click,
            child: GestureDetector(
              onTap: onDelete,
              child: Icon(
                Icons.close_rounded,
                size: size.iconSize + 2,
                color: colorConfig.textColor.withValues(alpha: 0.8),
              ),
            ),
          ),
        ],
      ],
    );

    final container = Container(
      height: size.height,
      padding: EdgeInsets.symmetric(horizontal: size.horizontalPadding),
      decoration: BoxDecoration(
        color: colorConfig.backgroundColor,
        borderRadius: BorderRadius.circular(size.height / 2),
        border: Border.all(color: colorConfig.borderColor, width: 1),
      ),
      child: Center(
        widthFactor: 1.0,
        child: badgeContent,
      ),
    );

    if (onTap != null) {
      return MouseRegion(
        cursor: SystemMouseCursors.click,
        child: GestureDetector(
          onTap: onTap,
          child: container,
        ),
      );
    }

    return container;
  }

  _BadgeColors _resolveBadgeColors(AppColorScheme colors) {
    Color baseColor;

    switch (variant) {
      case AppBadgeVariant.neutral:
        baseColor = colors.textSecondary;
        break;
      case AppBadgeVariant.brand:
        baseColor = colors.brandAccent;
        break;
      case AppBadgeVariant.success:
        baseColor = colors.success;
        break;
      case AppBadgeVariant.warning:
        baseColor = colors.warning;
        break;
      case AppBadgeVariant.error:
        baseColor = colors.error;
        break;
      case AppBadgeVariant.info:
        baseColor = colors.info;
        break;
    }

    if (style == AppBadgeStyle.outline) {
      return _BadgeColors(
        backgroundColor: Colors.transparent,
        borderColor: baseColor.withValues(alpha: 0.5),
        textColor: baseColor,
      );
    }

    // Filled style: 15% - 20% background tint, 30% border, 100% text
    return _BadgeColors(
      backgroundColor: baseColor.withValues(alpha: 0.16),
      borderColor: baseColor.withValues(alpha: 0.32),
      textColor: baseColor,
    );
  }
}

class _BadgeColors {
  const _BadgeColors({
    required this.backgroundColor,
    required this.borderColor,
    required this.textColor,
  });

  final Color backgroundColor;
  final Color borderColor;
  final Color textColor;
}
