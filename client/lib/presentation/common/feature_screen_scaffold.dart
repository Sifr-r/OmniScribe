import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'app_badge.dart';

/// Shared scaffold for the feature screens (translation, transcription,
/// extraction): header bar with title + badge + subtitle and an optional
/// trailing header action, an optional inline error banner, and the
/// screen's content panes.
class FeatureScreenScaffold extends StatelessWidget {
  const FeatureScreenScaffold({
    super.key,
    required this.title,
    required this.badge,
    required this.subtitle,
    this.headerAction,
    this.errorBanner,
    required this.panes,
  });

  final String title;
  final AppBadge badge;
  final String subtitle;
  final Widget? headerAction;
  final Widget? errorBanner;
  final Widget panes;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Scaffold(
      backgroundColor: colors.background,
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header Bar
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          title,
                          style: AppTypography.displaySmall(
                            color: colors.textPrimary,
                          ),
                        ),
                        const SizedBox(width: 10),
                        badge,
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      subtitle,
                      style: AppTypography.bodySmall(
                        color: colors.textMuted,
                      ),
                    ),
                  ],
                ),
                if (headerAction != null) headerAction!,
              ],
            ),
            const SizedBox(height: 16),
            if (errorBanner != null) ...[
              errorBanner!,
              const SizedBox(height: 16),
            ],
            Expanded(child: panes),
          ],
        ),
      ),
    );
  }
}
