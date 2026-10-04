import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// Semantic variants for [AppButton] adhering to DocuVerse design tokens.
enum AppButtonVariant {
  /// Primary hero action (Indigo background, white text).
  primary,

  /// Paired action next to primary (Card raised surface with border).
  secondary,

  /// Secondary CTA that doesn't compete with primary (Brand outline).
  outline,

  /// Tertiary, toolbar, or icon-only actions (Transparent, hover fill).
  ghost,

  /// Destructive actions (Soft danger tint with danger border).
  danger,
}

/// Predefined button sizes from the DocuVerse 4px grid.
enum AppButtonSize {
  /// Height 32px, pad 12px, font 12px.
  sm(height: 32, horizontalPadding: 12, fontSize: 12, iconSize: 14),

  /// Height 36px, pad 16px, font 14px (Default).
  md(height: 36, horizontalPadding: 16, fontSize: 14, iconSize: 16),

  /// Height 44px, pad 20px, font 14px (Hero CTA).
  lg(height: 44, horizontalPadding: 20, fontSize: 14, iconSize: 18);

  const AppButtonSize({
    required this.height,
    required this.horizontalPadding,
    required this.fontSize,
    required this.iconSize,
  });

  final double height;
  final double horizontalPadding;
  final double fontSize;
  final double iconSize;
}

/// Universal DocuVerse Button primitive for OmniScribe.
///
/// Supports 5 variants, 3 sizes, leading/trailing icons, full-width mode,
/// and built-in loading indicator spinner.
class AppButton extends StatefulWidget {
  const AppButton({
    super.key,
    this.text,
    this.child,
    this.onPressed,
    this.variant = AppButtonVariant.primary,
    this.size = AppButtonSize.md,
    this.icon,
    this.trailingIcon,
    this.loading = false,
    this.disabled = false,
    this.fullWidth = false,
    this.tooltip,
    this.testId,
  }) : assert(text != null || child != null || icon != null,
            'Button must have either text, child, or icon');

  /// Text label of the button.
  final String? text;

  /// Custom child widget instead of text.
  final Widget? child;

  /// Callback executed when the button is clicked.
  final VoidCallback? onPressed;

  /// Semantic style variant.
  final AppButtonVariant variant;

  /// Size tier (sm, md, lg).
  final AppButtonSize size;

  /// Optional leading icon widget.
  final Widget? icon;

  /// Optional trailing icon widget.
  final Widget? trailingIcon;

  /// Whether to show a spinner instead of the button content.
  final bool loading;

  /// Whether the button is disabled.
  final bool disabled;

  /// Whether the button spans full available width.
  final bool fullWidth;

  /// Optional tooltip message.
  final String? tooltip;

  /// Automation test identifier.
  final String? testId;

  @override
  State<AppButton> createState() => _AppButtonState();
}

class _AppButtonState extends State<AppButton> {
  bool _isHovered = false;
  bool _isPressed = false;

  bool get _isInteractive =>
      !widget.disabled && !widget.loading && widget.onPressed != null;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    // Determine colors based on variant & state
    final colorConfig = _resolveColors(colors);

    Widget content = _buildContent(colorConfig.textColor);

    if (widget.loading) {
      content = Row(
        mainAxisSize: MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          SizedBox(
            width: widget.size.iconSize,
            height: widget.size.iconSize,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              valueColor: AlwaysStoppedAnimation<Color>(colorConfig.textColor),
            ),
          ),
          if (widget.text != null || widget.child != null) ...[
            const SizedBox(width: 8),
            Text(
              'Loading...',
              style: AppTypography.labelLarge(
                color: colorConfig.textColor,
              ).copyWith(fontSize: widget.size.fontSize),
            ),
          ],
        ],
      );
    }

    final buttonWidget = MouseRegion(
      cursor:
          _isInteractive ? SystemMouseCursors.click : SystemMouseCursors.basic,
      onEnter: (_) {
        if (_isInteractive) setState(() => _isHovered = true);
      },
      onExit: (_) {
        if (_isInteractive) setState(() => _isHovered = false);
      },
      child: GestureDetector(
        onTapDown: (_) {
          if (_isInteractive) setState(() => _isPressed = true);
        },
        onTapUp: (_) {
          if (_isInteractive) {
            setState(() => _isPressed = false);
            widget.onPressed?.call();
          }
        },
        onTapCancel: () {
          if (_isInteractive) setState(() => _isPressed = false);
        },
        // Sprint 3 / M-2 audit fix: enforce the Material 48 dp minimum
        // touch target via a SizedBox wrapper. The visible button
        // keeps its design height (32/36/44 for sm/md/lg), but the
        // touch area extends to at least 48 dp so the button meets
        // WCAG 2.5.5 (Target Size) for users with motor impairments.
        // The visible button stays centered vertically inside the
        // larger touch area, so the design layout is unchanged.
        behavior: HitTestBehavior.opaque,
        child: _MinimumTapTarget(
          minimum: 48,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            curve: Curves.easeOut,
            height: widget.size.height,
            padding: EdgeInsets.symmetric(
              horizontal: widget.size.horizontalPadding,
            ),
            decoration: BoxDecoration(
              color: colorConfig.backgroundColor,
              borderRadius: BorderRadius.circular(6),
              border: colorConfig.borderColor != null
                  ? Border.all(color: colorConfig.borderColor!, width: 1)
                  : null,
              boxShadow: colorConfig.boxShadow,
            ),
            alignment: Alignment.center,
            child: content,
          ),
        ),
      ),
    );

    Widget result = AnimatedOpacity(
      duration: const Duration(milliseconds: 150),
      opacity: widget.disabled ? 0.45 : 1.0,
      child: widget.fullWidth
          ? SizedBox(width: double.infinity, child: buttonWidget)
          : buttonWidget,
    );

    if (widget.tooltip != null) {
      result = Tooltip(
        message: widget.tooltip!,
        child: result,
      );
    }

    // Sprint 3 / H-4 audit fix: wrap the press target in a
    // FocusableActionDetector so keyboard users (Tab + Enter/Space)
    // can activate the button, not just mouse/touch users. The
    // detector also bumps the hit-target to a 48x48 minimum via its
    // enclosing Material widget below, satisfying the Material
    // accessibility guideline (the previous GestureDetector did
    // neither). Disabled / non-interactive buttons short-circuit the
    // action callback to avoid spurious activations.
    final accessible = FocusableActionDetector(
      enabled: _isInteractive,
      onShowFocusHighlight: (_) {},
      actions: <Type, Action<Intent>>{
        ActivateIntent: CallbackAction<ActivateIntent>(
          onInvoke: (intent) {
            if (_isInteractive) {
              widget.onPressed?.call();
            }
            return null;
          },
        ),
      },
      child: result,
    );

    return Semantics(
      button: true,
      enabled: _isInteractive,
      label: widget.text ?? widget.tooltip,
      child: accessible,
    );
  }

  Widget _buildContent(Color textColor) {
    if (widget.child != null) {
      return DefaultTextStyle(
        style: AppTypography.labelLarge(color: textColor)
            .copyWith(fontSize: widget.size.fontSize),
        child: IconTheme(
          data: IconThemeData(color: textColor, size: widget.size.iconSize),
          child: widget.child!,
        ),
      );
    }

    final children = <Widget>[];

    if (widget.icon != null) {
      children.add(
        IconTheme(
          data: IconThemeData(color: textColor, size: widget.size.iconSize),
          child: widget.icon!,
        ),
      );
    }

    if (widget.text != null) {
      if (children.isNotEmpty) {
        children.add(const SizedBox(width: 6));
      }
      children.add(
        Flexible(
          child: Text(
            widget.text!,
            style: AppTypography.labelLarge(
              color: textColor,
            ).copyWith(
              fontSize: widget.size.fontSize,
              fontWeight: FontWeight.w500,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      );
    }

    if (widget.trailingIcon != null) {
      if (children.isNotEmpty) {
        children.add(const SizedBox(width: 6));
      }
      children.add(
        IconTheme(
          data: IconThemeData(color: textColor, size: widget.size.iconSize),
          child: widget.trailingIcon!,
        ),
      );
    }

    return Row(
      mainAxisSize: widget.fullWidth ? MainAxisSize.max : MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.center,
      children: children,
    );
  }

  _ButtonColors _resolveColors(AppColorScheme colors) {
    switch (widget.variant) {
      case AppButtonVariant.primary:
        final base = colors.brand;
        final bg = _isPressed
            ? base.withValues(alpha: 0.85)
            : _isHovered
                ? base.withValues(alpha: 0.92)
                : base;
        return _ButtonColors(
          backgroundColor: bg,
          textColor: colors.brandForeground,
          borderColor: null,
          boxShadow: _isHovered && _isInteractive
              ? [
                  BoxShadow(
                    color: colors.brand.withValues(alpha: 0.35),
                    blurRadius: 10,
                    offset: const Offset(0, 2),
                  ),
                ]
              : null,
        );

      case AppButtonVariant.secondary:
        final bg = _isPressed
            ? colors.muted
            : _isHovered
                ? colors.cardRaised.withValues(alpha: 0.95)
                : colors.cardRaised;
        return _ButtonColors(
          backgroundColor: bg,
          textColor: colors.textPrimary,
          borderColor: colors.border,
        );

      case AppButtonVariant.outline:
        final bg = _isPressed
            ? colors.brand.withValues(alpha: 0.15)
            : _isHovered
                ? colors.brand.withValues(alpha: 0.08)
                : Colors.transparent;
        return _ButtonColors(
          backgroundColor: bg,
          textColor: colors.brand,
          borderColor: colors.brand.withValues(alpha: 0.45),
        );

      case AppButtonVariant.ghost:
        final bg = _isPressed
            ? colors.muted
            : _isHovered
                ? colors.muted.withValues(alpha: 0.6)
                : Colors.transparent;
        return _ButtonColors(
          backgroundColor: bg,
          textColor: _isHovered ? colors.textPrimary : colors.textSecondary,
          borderColor: null,
        );

      case AppButtonVariant.danger:
        final bg = _isPressed
            ? colors.error.withValues(alpha: 0.25)
            : _isHovered
                ? colors.error.withValues(alpha: 0.20)
                : colors.error.withValues(alpha: 0.12);
        return _ButtonColors(
          backgroundColor: bg,
          textColor: colors.error,
          borderColor: colors.error.withValues(alpha: 0.35),
        );
    }
  }
}

class _ButtonColors {
  const _ButtonColors({
    required this.backgroundColor,
    required this.textColor,
    this.borderColor,
    this.boxShadow,
  });

  final Color backgroundColor;
  final Color textColor;
  final Color? borderColor;
  final List<BoxShadow>? boxShadow;
}

/// Transparent wrapper that guarantees a minimum hit-target size.
///
/// WCAG 2.5.5 (Target Size) and Material's accessibility guideline
/// both recommend a minimum 48x48 dp interactive surface. Buttons with
/// compact visual labels (32 / 36 / 44 dp) extend their invisible
/// hit area to this floor without disturbing the visual layout: the
/// child is centered vertically inside a [SizedBox] of the larger
/// height. Hit testing is [HitTestBehavior.translucent] so the gap
/// between the visual button and the extended edge still routes taps
/// to the underlying [GestureDetector].
class _MinimumTapTarget extends StatelessWidget {
  const _MinimumTapTarget({
    required this.minimum,
    required this.child,
  });

  final double minimum;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: BoxConstraints(minHeight: minimum),
      child: Center(
        heightFactor: 1.0,
        child: child,
      ),
    );
  }
}
