import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';

/// Modal width tier matching DocuVerse layout standards.
enum AppModalWidth {
  /// 400px - Confirmation alerts & simple prompts
  sm(400),

  /// 512px - Standard form dialogs (Default)
  md(512),

  /// 640px - Large configuration / multi-step forms
  lg(640),

  /// 768px - Export & provider inspector dialogs
  xl(768),

  /// 960px - Full document comparisons
  full(960);

  const AppModalWidth(this.value);
  final double value;
}

/// DocuVerse Modal Dialog with backdrop blur and responsive layout.
class AppModal extends StatelessWidget {
  const AppModal({
    super.key,
    required this.title,
    required this.content,
    this.subtitle,
    this.icon,
    this.actions,
    this.maxWidth = AppModalWidth.md,
    this.showCloseButton = true,
    this.onClose,
    this.maxHeight,
    this.testId,
  });

  final String title;
  final Widget content;
  final String? subtitle;
  final Widget? icon;
  final List<Widget>? actions;
  final AppModalWidth maxWidth;
  final bool showCloseButton;
  final VoidCallback? onClose;
  final double? maxHeight;
  final String? testId;

  /// Static helper to display this modal using standard navigation dialog.
  static Future<T?> show<T>({
    required BuildContext context,
    required String title,
    required Widget content,
    String? subtitle,
    Widget? icon,
    List<Widget>? actions,
    AppModalWidth maxWidth = AppModalWidth.md,
    bool barrierDismissible = true,
    bool showCloseButton = true,
    String? testId,
  }) {
    return showGeneralDialog<T>(
      context: context,
      barrierDismissible: barrierDismissible,
      barrierLabel: 'Dismiss Modal',
      barrierColor: context.colors.overlay,
      transitionDuration: const Duration(milliseconds: 200),
      pageBuilder:
          (BuildContext ctx, Animation<double> anim1, Animation<double> anim2) {
        return AppModal(
          title: title,
          subtitle: subtitle,
          icon: icon,
          content: content,
          actions: actions,
          maxWidth: maxWidth,
          showCloseButton: showCloseButton,
          onClose: () => Navigator.of(ctx).pop(),
          testId: testId,
        );
      },
      transitionBuilder: (BuildContext ctx, Animation<double> anim,
          Animation<double> secondaryAnim, Widget child) {
        final curved =
            CurvedAnimation(parent: anim, curve: Curves.easeOutCubic);
        return BackdropFilter(
          filter: ImageFilter.blur(
            sigmaX: 8 * curved.value,
            sigmaY: 8 * curved.value,
          ),
          child: FadeTransition(
            opacity: curved,
            child: ScaleTransition(
              scale: Tween<double>(begin: 0.96, end: 1.0).animate(curved),
              child: child,
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final screenWidth = MediaQuery.of(context).size.width;
    final screenHeight = MediaQuery.of(context).size.height;

    final targetWidth =
        screenWidth < maxWidth.value ? screenWidth - 32 : maxWidth.value;
    final targetMaxHeight = maxHeight ?? screenHeight * 0.85;

    return Focus(
      autofocus: true,
      onKeyEvent: (FocusNode node, KeyEvent event) {
        if (event is KeyDownEvent &&
            event.logicalKey == LogicalKeyboardKey.escape) {
          if (onClose != null) {
            onClose!();
          } else {
            Navigator.of(context).maybePop();
          }
          return KeyEventResult.handled;
        }
        return KeyEventResult.ignored;
      },
      child: Center(
        child: Material(
          color: Colors.transparent,
          child: Container(
            width: targetWidth,
            constraints: BoxConstraints(maxHeight: targetMaxHeight),
            decoration: BoxDecoration(
              color: colors.card,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: colors.borderStrong, width: 1),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.45),
                  blurRadius: 28,
                  offset: const Offset(0, 12),
                ),
              ],
            ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Modal Title Header
                  Padding(
                    padding: const EdgeInsets.fromLTRB(20, 18, 16, 16),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (icon != null) ...[
                          IconTheme(
                            data: IconThemeData(
                              color: colors.brand,
                              size: 20,
                            ),
                            child: icon!,
                          ),
                          const SizedBox(width: 10),
                        ],
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                title,
                                style: AppTypography.displaySmall(
                                  color: colors.textPrimary,
                                ).copyWith(fontSize: 18),
                              ),
                              if (subtitle != null) ...[
                                const SizedBox(height: 4),
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
                        if (showCloseButton)
                          AppButton(
                            variant: AppButtonVariant.ghost,
                            size: AppButtonSize.sm,
                            icon: Icon(Icons.close_rounded,
                                size: 16, color: colors.textMuted),
                            tooltip: 'Close (ESC)',
                            onPressed: () {
                              if (onClose != null) {
                                onClose!();
                              } else {
                                Navigator.of(context).maybePop();
                              }
                            },
                          ),
                      ],
                    ),
                  ),
                  Divider(height: 1, color: colors.border),

                  // Modal Body Content (Scrollable)
                  Flexible(
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.all(20),
                      child: content,
                    ),
                  ),

                  // Modal Footer Actions
                  if (actions != null && actions!.isNotEmpty) ...[
                    Divider(height: 1, color: colors.border),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 14),
                      color: colors.cardRaised.withValues(alpha: 0.5),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.end,
                        children: [
                          for (int i = 0; i < actions!.length; i++) ...[
                            if (i > 0) const SizedBox(width: 10),
                            actions![i],
                          ],
                        ],
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
