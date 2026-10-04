import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// Semantic surface tier for [AppCard].
enum AppCardVariant {
  /// Surface 1 — Default top-level card (bg-card + border-border).
  defaultCard,

  /// Surface 2 — Nested card (bg-card-raised + border-border).
  raised,

  /// Inset — Borderless soft group well (bg-card-raised + border-0).
  inset,

  /// Glass — Translucent card with subtle border and blur.
  glass,
}

/// Padding presets for [AppCard].
enum AppCardPadding {
  none(0),
  sm(12),
  md(16),
  lg(24);

  const AppCardPadding(this.value);
  final double value;
}

/// Universal DocuVerse panel container.
class AppCard extends StatefulWidget {
  const AppCard({
    super.key,
    required this.child,
    this.title,
    this.subtitle,
    this.headerLeading,
    this.headerAction,
    this.header,
    this.footer,
    this.variant = AppCardVariant.defaultCard,
    this.padding = AppCardPadding.md,
    this.onTap,
    this.borderColor,
    this.glowColor,
    this.width,
    this.height,
    this.testId,
  });

  final Widget child;
  final String? title;
  final String? subtitle;
  final Widget? headerLeading;
  final Widget? headerAction;
  final Widget? header;
  final Widget? footer;
  final AppCardVariant variant;
  final AppCardPadding padding;
  final VoidCallback? onTap;
  final Color? borderColor;
  final Color? glowColor;
  final double? width;
  final double? height;
  final String? testId;

  @override
  State<AppCard> createState() => _AppCardState();
}

class _AppCardState extends State<AppCard> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    // Resolve surface color
    Color bgColor;
    Color borderCol;

    switch (widget.variant) {
      case AppCardVariant.defaultCard:
        bgColor = colors.card;
        borderCol = widget.borderColor ?? colors.border;
        break;
      case AppCardVariant.raised:
        bgColor = colors.cardRaised;
        borderCol = widget.borderColor ?? colors.borderStrong;
        break;
      case AppCardVariant.inset:
        bgColor = colors.cardRaised;
        borderCol = Colors.transparent;
        break;
      case AppCardVariant.glass:
        bgColor = colors.card.withValues(alpha: 0.75);
        borderCol = widget.borderColor ?? colors.border.withValues(alpha: 0.6);
        break;
    }

    if (_isHovered && widget.onTap != null) {
      borderCol = widget.borderColor ?? colors.brand.withValues(alpha: 0.6);
    }

    List<BoxShadow>? shadows;
    if (widget.glowColor != null) {
      shadows = [
        BoxShadow(
          color: widget.glowColor!.withValues(alpha: 0.25),
          blurRadius: 16,
          spreadRadius: 2,
        ),
      ];
    } else if (_isHovered && widget.onTap != null) {
      shadows = [
        BoxShadow(
          color: Colors.black.withValues(alpha: 0.2),
          blurRadius: 12,
          offset: const Offset(0, 4),
        ),
      ];
    }

    final hasHeader = widget.header != null || widget.title != null;
    final hasFooter = widget.footer != null;

    Widget content;
    if (hasHeader || hasFooter) {
      content = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          // Optional Header
          if (hasHeader) ...[
            if (widget.header != null)
              widget.header!
            else
              Padding(
                padding: EdgeInsets.only(
                  left: widget.padding.value,
                  right: widget.padding.value,
                  top: widget.padding.value,
                  bottom: 12,
                ),
                child: Row(
                  children: [
                    if (widget.headerLeading != null) ...[
                      widget.headerLeading!,
                      const SizedBox(width: 8),
                    ],
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          if (widget.title != null)
                            Text(
                              widget.title!,
                              style: AppTypography.titleMedium(
                                color: colors.textPrimary,
                              ),
                            ),
                          if (widget.subtitle != null) ...[
                            const SizedBox(height: 2),
                            Text(
                              widget.subtitle!,
                              style: AppTypography.bodySmall(
                                color: colors.textMuted,
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                    if (widget.headerAction != null) widget.headerAction!,
                  ],
                ),
              ),
            Divider(height: 1, color: colors.border),
          ],

          // Card Body Content
          Flexible(
            fit: FlexFit.loose,
            child: Padding(
              padding: EdgeInsets.all(widget.padding.value),
              child: widget.child,
            ),
          ),

          // Optional Footer
          if (hasFooter) ...[
            Divider(height: 1, color: colors.border),
            Padding(
              padding: EdgeInsets.symmetric(
                horizontal: widget.padding.value,
                vertical: 12,
              ),
              child: widget.footer!,
            ),
          ],
        ],
      );
    } else {
      content = Padding(
        padding: EdgeInsets.all(widget.padding.value),
        child: widget.child,
      );
    }

    final cardBody = Container(
      width: widget.width,
      height: widget.height,
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: borderCol, width: 1),
        boxShadow: shadows,
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(8),
        child: content,
      ),
    );

    if (widget.onTap != null) {
      return MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _isHovered = true),
        onExit: (_) => setState(() => _isHovered = false),
        child: GestureDetector(
          onTap: widget.onTap,
          child: cardBody,
        ),
      );
    }

    return cardBody;
  }
}
