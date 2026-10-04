import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';

/// DocuVerse Typography Scale for OmniScribe.
///
/// Implements the 3-family typography system:
/// 1. Display: Fraunces / Serif (Headings h1-h3, modal titles, stats)
/// 2. Body: Sora / Sans (All body copy, buttons, inputs, labels)
/// 3. Mono: JetBrains Mono / Monospace (Artifact IDs, hashes, tokens, file sizes)
class AppTypography {
  AppTypography._();

  // ---------------------------------------------------------------------------
  // Font Family Names and Fallbacks
  // ---------------------------------------------------------------------------
  static const String fontFamilyDisplay = 'Fraunces';
  static const String fontFamilyBody = 'Sora';
  static const String fontFamilyMono = 'JetBrains Mono';

  static const List<String> displayFontFallbacks = [
    'Fraunces',
    'Georgia',
    'serif',
  ];

  static const List<String> bodyFontFallbacks = [
    'Sora',
    '-apple-system',
    'BlinkMacSystemFont',
    'Segoe UI',
    'Roboto',
    'sans-serif',
  ];

  static const List<String> monoFontFallbacks = [
    'JetBrains Mono',
    'SFMono-Regular',
    'ui-monospace',
    'Consolas',
    'Courier New',
    'monospace',
  ];

  // ---------------------------------------------------------------------------
  // Raw Typography Definitions (Independent of Theme Color)
  // ---------------------------------------------------------------------------

  /// Display Large (h1) - 30sp, weight 600, line-height 36
  static TextStyle displayLarge({Color? color}) => TextStyle(
        fontFamily: fontFamilyDisplay,
        fontFamilyFallback: displayFontFallbacks,
        fontSize: 30,
        fontWeight: FontWeight.w600,
        height: 36 / 30,
        letterSpacing: -0.5,
        color: color,
      );

  /// Display Medium (h2) - 24sp, weight 600, line-height 32
  static TextStyle displayMedium({Color? color}) => TextStyle(
        fontFamily: fontFamilyDisplay,
        fontFamilyFallback: displayFontFallbacks,
        fontSize: 24,
        fontWeight: FontWeight.w600,
        height: 32 / 24,
        letterSpacing: -0.3,
        color: color,
      );

  /// Display Small (h3) - 18sp, weight 600, line-height 28
  static TextStyle displaySmall({Color? color}) => TextStyle(
        fontFamily: fontFamilyDisplay,
        fontFamilyFallback: displayFontFallbacks,
        fontSize: 18,
        fontWeight: FontWeight.w600,
        height: 28 / 18,
        letterSpacing: -0.2,
        color: color,
      );

  /// Title Large - 16sp, weight 600, line-height 24
  static TextStyle titleLarge({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 16,
        fontWeight: FontWeight.w600,
        height: 24 / 16,
        letterSpacing: -0.1,
        color: color,
      );

  /// Title Medium - 14sp, weight 600, line-height 20
  static TextStyle titleMedium({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 14,
        fontWeight: FontWeight.w600,
        height: 20 / 14,
        color: color,
      );

  /// Title Small - 13sp, weight 600, line-height 18
  static TextStyle titleSmall({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 13,
        fontWeight: FontWeight.w600,
        height: 18 / 13,
        color: color,
      );

  /// Body Large - 15sp, weight 400, line-height 22
  static TextStyle bodyLarge({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 15,
        fontWeight: FontWeight.w400,
        height: 22 / 15,
        color: color,
      );

  /// Body Medium (Default Body) - 14sp, weight 400, line-height 20
  static TextStyle bodyMedium({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 14,
        fontWeight: FontWeight.w400,
        height: 20 / 14,
        color: color,
      );

  /// Body Medium Strong - 14sp, weight 500, line-height 20
  static TextStyle bodyMediumStrong({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 14,
        fontWeight: FontWeight.w500,
        height: 20 / 14,
        color: color,
      );

  /// Body Small / Caption - 12sp, weight 400, line-height 16
  static TextStyle bodySmall({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 12,
        fontWeight: FontWeight.w400,
        height: 16 / 12,
        color: color,
      );

  /// Caption Strong - 12sp, weight 500, line-height 16
  static TextStyle captionStrong({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 12,
        fontWeight: FontWeight.w500,
        height: 16 / 12,
        color: color,
      );

  /// Label Large (Buttons, CTA) - 14sp, weight 500, line-height 20
  static TextStyle labelLarge({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 14,
        fontWeight: FontWeight.w500,
        height: 20 / 14,
        letterSpacing: 0.1,
        color: color,
      );

  /// Label Medium (Badges, Small Buttons) - 12sp, weight 500, line-height 16
  static TextStyle labelMedium({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 12,
        fontWeight: FontWeight.w500,
        height: 16 / 12,
        color: color,
      );

  /// Micro / Uppercase Section Header - 10sp, weight 600, tracking 1.2
  static TextStyle micro({Color? color}) => TextStyle(
        fontFamily: fontFamilyBody,
        fontFamilyFallback: bodyFontFallbacks,
        fontSize: 10,
        fontWeight: FontWeight.w600,
        height: 14 / 10,
        letterSpacing: 1.2,
        color: color,
      );

  /// Code / Monospace - 13sp, weight 400
  static TextStyle code({Color? color}) => TextStyle(
        fontFamily: fontFamilyMono,
        fontFamilyFallback: monoFontFallbacks,
        fontSize: 13,
        fontWeight: FontWeight.w400,
        height: 18 / 13,
        color: color,
      );

  /// Code Small - 11sp, weight 400
  static TextStyle codeSmall({Color? color}) => TextStyle(
        fontFamily: fontFamilyMono,
        fontFamilyFallback: monoFontFallbacks,
        fontSize: 11,
        fontWeight: FontWeight.w400,
        height: 15 / 11,
        color: color,
      );

  /// Code Bold / Identifier - 12sp, weight 600
  static TextStyle codeBold({Color? color}) => TextStyle(
        fontFamily: fontFamilyMono,
        fontFamilyFallback: monoFontFallbacks,
        fontSize: 12,
        fontWeight: FontWeight.w600,
        height: 16 / 12,
        color: color,
      );

  // ---------------------------------------------------------------------------
  // Material TextTheme Generators
  // ---------------------------------------------------------------------------

  /// Creates a standard Material [TextTheme] bound to DocuVerse dark colors.
  static TextTheme createDarkTextTheme() {
    const primary = AppColors.darkTextPrimary;
    const secondary = AppColors.darkTextSecondary;
    const muted = AppColors.darkTextMuted;

    return TextTheme(
      displayLarge: displayLarge(color: primary),
      displayMedium: displayMedium(color: primary),
      displaySmall: displaySmall(color: primary),
      headlineMedium: displayMedium(color: primary),
      headlineSmall: displaySmall(color: primary),
      titleLarge: titleLarge(color: primary),
      titleMedium: titleMedium(color: primary),
      titleSmall: titleSmall(color: secondary),
      bodyLarge: bodyLarge(color: primary),
      bodyMedium: bodyMedium(color: primary),
      bodySmall: bodySmall(color: secondary),
      labelLarge: labelLarge(color: primary),
      labelMedium: labelMedium(color: secondary),
      labelSmall: micro(color: muted),
    );
  }

  /// Creates a standard Material [TextTheme] bound to DocuVerse light colors.
  static TextTheme createLightTextTheme() {
    const primary = AppColors.lightTextPrimary;
    const secondary = AppColors.lightTextSecondary;
    const muted = AppColors.lightTextMuted;

    return TextTheme(
      displayLarge: displayLarge(color: primary),
      displayMedium: displayMedium(color: primary),
      displaySmall: displaySmall(color: primary),
      headlineMedium: displayMedium(color: primary),
      headlineSmall: displaySmall(color: primary),
      titleLarge: titleLarge(color: primary),
      titleMedium: titleMedium(color: primary),
      titleSmall: titleSmall(color: secondary),
      bodyLarge: bodyLarge(color: primary),
      bodyMedium: bodyMedium(color: primary),
      bodySmall: bodySmall(color: secondary),
      labelLarge: labelLarge(color: primary),
      labelMedium: labelMedium(color: secondary),
      labelSmall: micro(color: muted),
    );
  }

  /// Convenience method to retrieve typography from BuildContext.
  static TextTheme of(BuildContext context) {
    return Theme.of(context).textTheme;
  }
}

/// Extension on [BuildContext] for easy typography access.
extension AppTypographyExtension on BuildContext {
  TextTheme get typography => Theme.of(this).textTheme;
}
