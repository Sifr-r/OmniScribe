import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';

/// Dismissible banner shown when the backend refused this client's bearer
/// credential — a 401 answering with ``WWW-Authenticate: Bearer``, which only
/// ``BearerAuthMiddleware`` sends. The token is entered in Settings under
/// "OmniScribe Backend Connection" and lives for the session; flipping the flag
/// does not auto-clear, and a 401 guarding some other credential never gets here.
class AuthRequiredBanner extends ConsumerWidget {
  const AuthRequiredBanner({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final visible = ref.watch(authRequiredProvider);
    if (!visible) return const SizedBox.shrink();

    final colors = context.colors;
    void openSettings() {
      ref.read(authRequiredProvider.notifier).set(false);
      ref.read(activeTabProvider.notifier).set(AppTab.settings);
    }

    void dismiss() {
      ref.read(authRequiredProvider.notifier).set(false);
    }

    return Semantics(
      container: true,
      liveRegion: true,
      label: 'Authentication required',
      child: Container(
        margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
        decoration: BoxDecoration(
          color: colors.error.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
            color: colors.error.withValues(alpha: 0.30),
          ),
        ),
        clipBehavior: Clip.antiAlias,
        child: IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(width: 4, color: colors.error),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
                  child: Row(
                    children: [
                      Icon(Icons.warning_amber_rounded,
                          size: 16,
                          color: colors.error,
                          semanticLabel: 'Warning'),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              'Authentication required',
                              style: TextStyle(
                                color: colors.textPrimary,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              '\u2014 the API rejected the request with a 401. Check the bearer token in Settings to continue.',
                              style: TextStyle(color: colors.textMuted),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      AppButton(
                        text: 'Open Settings',
                        variant: AppButtonVariant.primary,
                        size: AppButtonSize.sm,
                        onPressed: openSettings,
                      ),
                      const SizedBox(width: 4),
                      IconButton(
                        tooltip: 'Dismiss authentication banner',
                        onPressed: dismiss,
                        icon: const Icon(Icons.close, size: 14),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
