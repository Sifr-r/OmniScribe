import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/app/server_health_badge.dart';
import 'package:omniscribe_client/app/shell_state.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/features/providers/provider_modal.dart';
import 'package:omniscribe_client/features/settings/settings_notifier.dart';
import 'package:omniscribe_client/shared/widgets/app_badge.dart';

/// Top navigation bar for OmniScribe workspace.
///
/// Features:
/// - Brand icon & title with version badge
/// - Centered tab navigation ribbon with animated active pill
/// - Active provider preset indicator pill
/// - Theme mode toggle (Dark/Light)
/// - Backend server health status monitor
class TabRibbon extends ConsumerWidget {
  const TabRibbon({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colors = context.colors;
    final activeTab = ref.watch(activeTabProvider);
    final settings = ref.watch(settingsStateProvider);
    final providerPreset = settings.activeProviderId.toUpperCase();

    return Container(
      height: 56,
      decoration: BoxDecoration(
        color: colors.surface.withValues(alpha: 0.92),
        border: Border(
          bottom: BorderSide(color: colors.border, width: 1),
        ),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Row(
        children: [
          // -------------------------------------------------------------------
          // Brand Logo & Version
          // -------------------------------------------------------------------
          _buildBrand(context, colors, ref),
          const SizedBox(width: 24),

          // -------------------------------------------------------------------
          // Centered Tab Items Ribbon (Scrollable for compact screens)
          // -------------------------------------------------------------------
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              physics: const BouncingScrollPhysics(),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: AppTab.values.map((tab) {
                  final isSelected = tab == activeTab;
                  return _TabButton(
                    key: ValueKey(tab.id),
                    tab: tab,
                    isSelected: isSelected,
                    onTap: () {
                      ref.read(activeTabProvider.notifier).set(tab);
                    },
                  );
                }).toList(),
              ),
            ),
          ),
          const SizedBox(width: 16),

          // -------------------------------------------------------------------
          // Right Accessories
          // -------------------------------------------------------------------
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Provider Preset Pill
              InkWell(
                onTap: () => ProviderModal.show(context),
                borderRadius: BorderRadius.circular(14),
                child: _ProviderPresetPill(providerName: providerPreset),
              ),
              const SizedBox(width: 8),

              // Server Health Badge
              const ServerHealthBadge(),
              const SizedBox(width: 8),

              // Dark / Light Theme Toggle
              _ThemeToggleButton(
                isDark: settings.isDarkMode,
                onToggle: () {
                  final nextIsDark = !settings.isDarkMode;
                  ref
                      .read(themeModeProvider.notifier)
                      .set(nextIsDark ? ThemeMode.dark : ThemeMode.light);
                  ref
                      .read(settingsStateProvider.notifier)
                      .toggleDarkMode(nextIsDark);
                },
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildBrand(
      BuildContext context, AppColorScheme colors, WidgetRef ref) {
    return InkWell(
      onTap: () {
        ref.read(activeTabProvider.notifier).set(AppTab.workstation);
      },
      borderRadius: BorderRadius.circular(8),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          // Glowing brand logo glyph
          Container(
            width: 30,
            height: 30,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(8),
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  AppColors.brand,
                  AppColors.cyan,
                ],
              ),
              boxShadow: [
                BoxShadow(
                  color: AppColors.brand.withValues(alpha: 0.35),
                  blurRadius: 10,
                  offset: const Offset(0, 2),
                ),
              ],
            ),
            child: const Center(
              child: Icon(
                Icons.auto_awesome,
                size: 16,
                color: Colors.white,
              ),
            ),
          ),
          const SizedBox(width: 10),

          // Brand Name
          Text(
            'OmniScribe',
            style:
                AppTypography.displaySmall(color: colors.textPrimary).copyWith(
              fontSize: 17,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.2,
            ),
          ),
          const SizedBox(width: 8),

          // Version Pill
          const AppBadge(
            label: 'v2.0',
            variant: AppBadgeVariant.neutral,
            size: AppBadgeSize.sm,
          ),
        ],
      ),
    );
  }
}

class _TabButton extends StatefulWidget {
  const _TabButton({
    super.key,
    required this.tab,
    required this.isSelected,
    required this.onTap,
  });

  final AppTab tab;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  State<_TabButton> createState() => _TabButtonState();
}

class _TabButtonState extends State<_TabButton> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    // Color resolution
    Color bgColor = Colors.transparent;
    Color textColor = colors.textSecondary;

    if (widget.isSelected) {
      bgColor = colors.brand;
      textColor = colors.brandForeground;
    } else if (_isHovered) {
      bgColor = colors.muted.withValues(alpha: 0.5);
      textColor = colors.textPrimary;
    }

    // Sprint 3 / H-2 audit fix: explicit Semantics wraps the InkWell
    // so screen readers announce both "button" and the selection
    // state. Without this, a screen reader says only "Workstation"
    // with no indication that the tab is the active one. We
    // include the tab's description in the label so the screen
    // reader gets the full tooltip text on long-press / focus.
    return Tooltip(
      message: widget.tab.description,
      waitDuration: const Duration(milliseconds: 500),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _isHovered = true),
        onExit: (_) => setState(() => _isHovered = false),
        child: Semantics(
          button: true,
          selected: widget.isSelected,
          label: '${widget.tab.label}, tab',
          child: InkWell(
            key: ValueKey(widget.tab.testId),
            onTap: widget.onTap,
            borderRadius: BorderRadius.circular(6),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 160),
              curve: Curves.easeOut,
              height: 34,
              margin: const EdgeInsets.symmetric(horizontal: 2),
              padding: const EdgeInsets.symmetric(horizontal: 12),
              decoration: BoxDecoration(
                color: bgColor,
                borderRadius: BorderRadius.circular(6),
                boxShadow: widget.isSelected
                    ? [
                        BoxShadow(
                          color: colors.brand.withValues(alpha: 0.3),
                          blurRadius: 8,
                          offset: const Offset(0, 2),
                        ),
                      ]
                    : null,
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  Icon(
                    widget.isSelected
                        ? widget.tab.selectedIcon
                        : widget.tab.icon,
                    size: 15,
                    color: textColor,
                  ),
                  const SizedBox(width: 7),
                  Text(
                    widget.tab.label,
                    style: AppTypography.labelMedium(color: textColor).copyWith(
                      fontWeight:
                          widget.isSelected ? FontWeight.w600 : FontWeight.w500,
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
}

class _ProviderPresetPill extends StatelessWidget {
  const _ProviderPresetPill({required this.providerName});

  final String providerName;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Container(
      height: 28,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: colors.cardRaised,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: colors.border, width: 1),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.hub_outlined, size: 13, color: colors.brandAccent),
          const SizedBox(width: 6),
          Text(
            providerName,
            style:
                AppTypography.bodySmall(color: colors.textSecondary).copyWith(
              fontSize: 11,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }
}

class _ThemeToggleButton extends StatelessWidget {
  const _ThemeToggleButton({
    required this.isDark,
    required this.onToggle,
  });

  final bool isDark;
  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    // Sprint 3 / H-2 audit fix: explicit Semantics(button: true) on
    // the theme toggle so a screen reader announces it as a
    // toggleable button. Without this, the ``GestureDetector`` reads
    // as plain text + icon and the user has no way to know they can
    // tap it.
    return Tooltip(
      message: isDark ? 'Switch to Light Theme' : 'Switch to Dark Theme',
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        child: Semantics(
          button: true,
          label: isDark ? 'Switch to Light Theme' : 'Switch to Dark Theme',
          toggled: isDark,
          child: GestureDetector(
            onTap: onToggle,
            child: Container(
              width: 28,
              height: 28,
              decoration: BoxDecoration(
                color: colors.cardRaised,
                shape: BoxShape.circle,
                border: Border.all(color: colors.border, width: 1),
              ),
              child: Center(
                child: Icon(
                  isDark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
                  size: 14,
                  color: colors.textSecondary,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
