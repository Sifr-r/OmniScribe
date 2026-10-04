import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/app/shell_state.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// Interactive visual badge displaying backend server connectivity and latency.
class ServerHealthBadge extends ConsumerStatefulWidget {
  const ServerHealthBadge({super.key, this.testId});

  final String? testId;

  @override
  ConsumerState<ServerHealthBadge> createState() => _ServerHealthBadgeState();
}

class _ServerHealthBadgeState extends ConsumerState<ServerHealthBadge>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseController;
  late Animation<double> _pulseAnimation;
  bool _isHovered = false;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1600),
    );

    _pulseAnimation = Tween<double>(begin: 0.35, end: 1.0).animate(
      CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final healthState = ref.watch(serverHealthProvider);
    final statusColor = healthState.status.getColor(context);

    if (healthState.status == ServerHealth.checking) {
      if (!_pulseController.isAnimating) {
        _pulseController.repeat(reverse: true);
      }
    } else {
      if (_pulseController.isAnimating) {
        _pulseController.stop();
      }
    }

    final tooltipText =
        '${healthState.status.label} • ${healthState.endpoint}\n'
        'Latency: ${healthState.latencyMs != null ? "${healthState.latencyMs}ms" : "N/A"}\n'
        'Click to recheck status';

    return Tooltip(
      message: tooltipText,
      waitDuration: const Duration(milliseconds: 300),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _isHovered = true),
        onExit: (_) => setState(() => _isHovered = false),
        child: GestureDetector(
          onTap: () {
            ref.read(serverHealthProvider.notifier).checkHealth();
          },
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            height: 28,
            padding: const EdgeInsets.symmetric(horizontal: 10),
            decoration: BoxDecoration(
              color: _isHovered
                  ? colors.muted.withValues(alpha: 0.4)
                  : colors.cardRaised.withValues(alpha: 0.8),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: _isHovered ? colors.borderStrong : colors.border,
                width: 1,
              ),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                // Animated Status Dot
                Stack(
                  alignment: Alignment.center,
                  children: [
                    if (healthState.status == ServerHealth.online ||
                        healthState.status == ServerHealth.checking)
                      AnimatedBuilder(
                        animation: _pulseAnimation,
                        builder: (context, child) {
                          return Container(
                            width: 12,
                            height: 12,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: statusColor.withValues(
                                alpha: healthState.status == ServerHealth.online
                                    ? 0.25 * _pulseAnimation.value
                                    : 0.4 * _pulseAnimation.value,
                              ),
                            ),
                          );
                        },
                      ),
                    Container(
                      width: 6,
                      height: 6,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: statusColor,
                      ),
                    ),
                  ],
                ),
                const SizedBox(width: 6),
                Text(
                  healthState.status.label,
                  style:
                      AppTypography.micro(color: colors.textPrimary).copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (healthState.latencyMs != null &&
                    healthState.status == ServerHealth.online) ...[
                  const SizedBox(width: 4),
                  Text(
                    '${healthState.latencyMs}ms',
                    style: AppTypography.codeSmall(color: colors.textMuted),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
