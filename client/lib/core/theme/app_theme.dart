import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// DocuVerse Theme configuration for OmniScribe.
///
/// Builds complete, strictly-typed [ThemeData] for Dark and Light modes.
class AppTheme {
  AppTheme._();

  // ---------------------------------------------------------------------------
  // Dark Theme (Default)
  // ---------------------------------------------------------------------------
  static ThemeData get darkTheme {
    final colors = AppColorScheme.dark();
    final textTheme = AppTypography.createDarkTextTheme();

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: colors.background,
      canvasColor: colors.surface,
      primaryColor: colors.brand,
      colorScheme: ColorScheme.dark(
        primary: colors.brand,
        onPrimary: colors.brandForeground,
        secondary: colors.cyan,
        onSecondary: colors.brandForeground,
        surface: colors.surface,
        onSurface: colors.textPrimary,
        error: colors.error,
        onError: colors.brandForeground,
        outline: colors.border,
        outlineVariant: colors.borderStrong,
      ),
      textTheme: textTheme,
      extensions: <ThemeExtension<dynamic>>[colors],

      // App Bar
      appBarTheme: AppBarTheme(
        backgroundColor: colors.surface,
        foregroundColor: colors.textPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: AppTypography.titleLarge(color: colors.textPrimary),
        iconTheme: IconThemeData(color: colors.textSecondary),
        shape: Border(
          bottom: BorderSide(color: colors.border, width: 1),
        ),
      ),

      // Card Theme
      cardTheme: CardThemeData(
        color: colors.card,
        elevation: 0,
        margin: EdgeInsets.zero,
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: BorderSide(color: colors.border, width: 1),
        ),
      ),

      // Dialog / Modal Theme
      dialogTheme: DialogThemeData(
        backgroundColor: colors.card,
        elevation: 8,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: colors.borderStrong, width: 1),
        ),
        titleTextStyle: AppTypography.titleLarge(color: colors.textPrimary),
        contentTextStyle: AppTypography.bodyMedium(color: colors.textSecondary),
      ),

      // Input Decoration Theme
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: colors.card,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        hintStyle: AppTypography.bodyMedium(color: colors.textMuted),
        labelStyle: AppTypography.bodySmall(color: colors.textSecondary),
        floatingLabelStyle: AppTypography.bodySmall(color: colors.brandAccent),
        errorStyle: AppTypography.bodySmall(color: colors.error),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.inputBorder, width: 1),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.brand, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.error, width: 1),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.error, width: 1.5),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(
              color: colors.inputBorder.withValues(alpha: 0.5), width: 1),
        ),
      ),

      // Divider Theme
      dividerTheme: DividerThemeData(
        color: colors.border,
        thickness: 1,
        space: 1,
      ),

      // Scrollbar Theme
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.hovered)) {
            return colors.borderStrong;
          }
          return colors.border.withValues(alpha: 0.8);
        }),
        radius: const Radius.circular(4),
        thickness: WidgetStateProperty.all(6),
      ),

      // Tooltip Theme
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: colors.cardRaised,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: colors.borderStrong, width: 1),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.4),
              blurRadius: 8,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        textStyle: AppTypography.bodySmall(color: colors.textPrimary),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        waitDuration: const Duration(milliseconds: 400),
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Light Theme
  // ---------------------------------------------------------------------------
  static ThemeData get lightTheme {
    final colors = AppColorScheme.light();
    final textTheme = AppTypography.createLightTextTheme();

    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      scaffoldBackgroundColor: colors.background,
      canvasColor: colors.surface,
      primaryColor: colors.brand,
      colorScheme: ColorScheme.light(
        primary: colors.brand,
        onPrimary: colors.brandForeground,
        secondary: colors.cyan,
        onSecondary: colors.brandForeground,
        surface: colors.surface,
        onSurface: colors.textPrimary,
        error: colors.error,
        onError: colors.brandForeground,
        outline: colors.border,
        outlineVariant: colors.borderStrong,
      ),
      textTheme: textTheme,
      extensions: <ThemeExtension<dynamic>>[colors],

      // App Bar
      appBarTheme: AppBarTheme(
        backgroundColor: colors.surface,
        foregroundColor: colors.textPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: AppTypography.titleLarge(color: colors.textPrimary),
        iconTheme: IconThemeData(color: colors.textSecondary),
        shape: Border(
          bottom: BorderSide(color: colors.border, width: 1),
        ),
      ),

      // Card Theme
      cardTheme: CardThemeData(
        color: colors.card,
        elevation: 0,
        margin: EdgeInsets.zero,
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: BorderSide(color: colors.border, width: 1),
        ),
      ),

      // Dialog / Modal Theme
      dialogTheme: DialogThemeData(
        backgroundColor: colors.card,
        elevation: 8,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: colors.borderStrong, width: 1),
        ),
        titleTextStyle: AppTypography.titleLarge(color: colors.textPrimary),
        contentTextStyle: AppTypography.bodyMedium(color: colors.textSecondary),
      ),

      // Input Decoration Theme
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: colors.card,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        hintStyle: AppTypography.bodyMedium(color: colors.textMuted),
        labelStyle: AppTypography.bodySmall(color: colors.textSecondary),
        floatingLabelStyle: AppTypography.bodySmall(color: colors.brand),
        errorStyle: AppTypography.bodySmall(color: colors.error),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.inputBorder, width: 1),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.brand, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.error, width: 1),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: colors.error, width: 1.5),
        ),
        disabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(
              color: colors.inputBorder.withValues(alpha: 0.5), width: 1),
        ),
      ),

      // Divider Theme
      dividerTheme: DividerThemeData(
        color: colors.border,
        thickness: 1,
        space: 1,
      ),

      // Scrollbar Theme
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.hovered)) {
            return colors.borderStrong;
          }
          return colors.border;
        }),
        radius: const Radius.circular(4),
        thickness: WidgetStateProperty.all(6),
      ),

      // Tooltip Theme
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: colors.cardRaised,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: colors.borderStrong, width: 1),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.08),
              blurRadius: 8,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        textStyle: AppTypography.bodySmall(color: colors.textPrimary),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        waitDuration: const Duration(milliseconds: 400),
      ),
    );
  }
}
