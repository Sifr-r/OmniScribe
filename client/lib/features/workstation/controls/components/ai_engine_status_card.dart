import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/features/providers/ai_setup_wizard_modal.dart';
import 'package:omniscribe_client/features/settings/settings_notifier.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/shared/widgets/app_badge.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';
import 'package:omniscribe_client/shared/widgets/app_card.dart';

/// Subwidget displaying the AI engine status banner, readiness badge,
/// active model/provider info, and the Quick Setup wizard trigger button.
class AiEngineStatusCard extends ConsumerWidget {
  const AiEngineStatusCard({
    super.key,
    required this.settings,
  });

  final ProcessSettings settings;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = context.colors;
    final settingsState = ref.watch(settingsStateProvider);

    final activeProviderId = settingsState.activeProviderId;
    final activeModel = (settingsState.runtimeConfig?.model.isNotEmpty ?? false)
        ? settingsState.runtimeConfig!.model
        : (settings.model.isNotEmpty ? settings.model : 'allenai/olmocr-2-7b');

    final isConfigured = settings.apiBase.isNotEmpty && activeModel.isNotEmpty;

    return AppCard(
      padding: AppCardPadding.md,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Flexible(
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 26,
                      height: 26,
                      decoration: BoxDecoration(
                        color: colors.brand.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Center(
                        child: Icon(
                          Icons.psychology_rounded,
                          size: 15,
                          color: colors.brand,
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        'AI Engine',
                        overflow: TextOverflow.ellipsis,
                        style: AppTypography.titleSmall(
                          color: colors.textPrimary,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              AppBadge(
                label: isConfigured ? 'READY' : 'SETUP NEEDED',
                variant: isConfigured
                    ? AppBadgeVariant.success
                    : AppBadgeVariant.warning,
                size: AppBadgeSize.sm,
                dot: true,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              color: colors.cardRaised,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: colors.border),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        activeProviderId.toUpperCase(),
                        style: AppTypography.micro(
                          color: colors.brand,
                        ).copyWith(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        activeModel,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTypography.codeSmall(
                          color: colors.textPrimary,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                AppButton(
                  text: 'Quick Setup',
                  variant: AppButtonVariant.outline,
                  size: AppButtonSize.sm,
                  icon: const Icon(Icons.tune_rounded, size: 12),
                  onPressed: () {
                    AISetupWizardModal.show(
                      context,
                      onComplete: () {
                        ref.read(settingsStateProvider.notifier).load();
                      },
                    );
                  },
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
