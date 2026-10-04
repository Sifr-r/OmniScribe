import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/shared/widgets/toast_service.dart';

/// Floating toast container widget that listens to [toastProvider]
/// and renders animated notification pills in the corner of the viewport.
class ToastOverlay extends ConsumerWidget {
  const ToastOverlay({
    super.key,
    required this.child,
    this.alignment = Alignment.bottomRight,
  });

  final Widget child;
  final Alignment alignment;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final toasts = ref.watch(toastProvider);

    return Stack(
      children: [
        child,
        Positioned(
          right: 20,
          bottom: 20,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 380),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: toasts.map((ToastModel toast) {
                return Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: _ToastCard(
                    key: ValueKey(toast.id),
                    toast: toast,
                    onDismiss: () =>
                        ref.read(toastProvider.notifier).dismissToast(toast.id),
                  ),
                );
              }).toList(),
            ),
          ),
        ),
      ],
    );
  }
}

class _ToastCard extends StatefulWidget {
  const _ToastCard({
    super.key,
    required this.toast,
    required this.onDismiss,
  });

  final ToastModel toast;
  final VoidCallback onDismiss;

  @override
  State<_ToastCard> createState() => _ToastCardState();
}

class _ToastCardState extends State<_ToastCard>
    with SingleTickerProviderStateMixin {
  late AnimationController _animController;
  late Animation<double> _fadeAnimation;
  late Animation<Offset> _slideAnimation;

  @override
  void initState() {
    super.initState();
    _animController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 250),
    );
    _fadeAnimation = CurvedAnimation(
      parent: _animController,
      curve: Curves.easeOut,
    );
    _slideAnimation = Tween<Offset>(
      begin: const Offset(0.25, 0),
      end: Offset.zero,
    ).animate(CurvedAnimation(
      parent: _animController,
      curve: Curves.easeOutCubic,
    ));

    _animController.forward();
  }

  @override
  void dispose() {
    _animController.dispose();
    super.dispose();
  }

  void _dismissWithAnim() {
    _animController.reverse().then((_) {
      if (mounted) widget.onDismiss();
    });
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final accentColor = _resolveAccentColor(colors, widget.toast.level);
    final iconData = _resolveIcon(widget.toast.level);

    return SlideTransition(
      position: _slideAnimation,
      child: FadeTransition(
        opacity: _fadeAnimation,
        child: Material(
          color: Colors.transparent,
          child: Container(
            decoration: BoxDecoration(
              color: colors.cardRaised,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: colors.borderStrong, width: 1),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.3),
                  blurRadius: 16,
                  offset: const Offset(0, 6),
                ),
              ],
            ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: Stack(
                children: [
                  // Left Accent Indicator Strip
                  Positioned(
                    top: 0,
                    bottom: 0,
                    left: 0,
                    width: 4,
                    child: Container(color: accentColor),
                  ),

                  Padding(
                    padding: const EdgeInsets.fromLTRB(14, 10, 8, 10),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Padding(
                          padding: const EdgeInsets.only(top: 2, right: 10),
                          child: Icon(iconData, color: accentColor, size: 18),
                        ),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              if (widget.toast.title != null) ...[
                                Text(
                                  widget.toast.title!,
                                  style: AppTypography.captionStrong(
                                    color: colors.textPrimary,
                                  ),
                                ),
                                const SizedBox(height: 2),
                              ],
                              Text(
                                widget.toast.message,
                                style: AppTypography.bodySmall(
                                  color: colors.textSecondary,
                                ),
                              ),
                              if (widget.toast.actionLabel != null &&
                                  widget.toast.onAction != null) ...[
                                const SizedBox(height: 6),
                                MouseRegion(
                                  cursor: SystemMouseCursors.click,
                                  child: GestureDetector(
                                    onTap: () {
                                      widget.toast.onAction?.call();
                                      _dismissWithAnim();
                                    },
                                    child: Text(
                                      widget.toast.actionLabel!,
                                      style: AppTypography.bodySmall(
                                        color: colors.brand,
                                      ).copyWith(fontWeight: FontWeight.w600),
                                    ),
                                  ),
                                ),
                              ],
                            ],
                          ),
                        ),
                        const SizedBox(width: 8),
                        MouseRegion(
                          cursor: SystemMouseCursors.click,
                          child: GestureDetector(
                            onTap: _dismissWithAnim,
                            child: Padding(
                              padding: const EdgeInsets.all(2),
                              child: Icon(
                                Icons.close_rounded,
                                size: 16,
                                color: colors.textMuted,
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Color _resolveAccentColor(AppColorScheme colors, ToastLevel level) {
    switch (level) {
      case ToastLevel.info:
        return colors.info;
      case ToastLevel.success:
        return colors.success;
      case ToastLevel.warning:
        return colors.warning;
      case ToastLevel.error:
        return colors.error;
    }
  }

  IconData _resolveIcon(ToastLevel level) {
    switch (level) {
      case ToastLevel.info:
        return Icons.info_outline_rounded;
      case ToastLevel.success:
        return Icons.check_circle_outline_rounded;
      case ToastLevel.warning:
        return Icons.warning_amber_rounded;
      case ToastLevel.error:
        return Icons.error_outline_rounded;
    }
  }
}
