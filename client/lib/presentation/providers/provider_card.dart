import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/provider_preset.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';

class ProviderCard extends StatefulWidget {
  const ProviderCard({
    super.key,
    required this.provider,
    required this.models,
    required this.isLoadingModels,
    required this.onConnect,
    required this.onUseModel,
    required this.onRefreshModels,
    this.isActive = false,
  });

  final ProviderPreset provider;
  final List<String> models;
  final bool isLoadingModels;
  final VoidCallback onConnect;
  final ValueChanged<String> onUseModel;
  final VoidCallback onRefreshModels;
  final bool isActive;

  @override
  State<ProviderCard> createState() => _ProviderCardState();
}

class _ProviderCardState extends State<ProviderCard> {
  bool _isExpanded = false;

  IconData _getProviderIcon(String id) {
    final lower = id.toLowerCase();
    if (lower.contains('openai')) return Icons.auto_awesome;
    if (lower.contains('anthropic') || lower.contains('claude')) {
      return Icons.psychology;
    }
    if (lower.contains('ollama')) return Icons.terminal;
    if (lower.contains('lmstudio')) return Icons.desktop_windows;
    if (lower.contains('openrouter')) return Icons.alt_route;
    if (lower.contains('groq')) return Icons.bolt;
    if (lower.contains('deepseek')) return Icons.explore;
    if (lower.contains('azure')) return Icons.cloud;
    if (lower.contains('vllm')) return Icons.memory;
    return Icons.hub;
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final effectiveModels =
        widget.models.isNotEmpty ? widget.models : widget.provider.models;

    return Container(
      decoration: BoxDecoration(
        color: colors.cardRaised,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: widget.isActive ? colors.brand : colors.border,
          width: widget.isActive ? 1.5 : 1,
        ),
      ),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          // Top Row: Icon + Name + Badges + Connect Button
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: colors.surface,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: colors.borderStrong),
                ),
                child: Icon(
                  _getProviderIcon(widget.provider.id),
                  size: 18,
                  color: widget.isActive ? colors.brand : colors.textPrimary,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: InkWell(
                  onTap: widget.onConnect,
                  borderRadius: BorderRadius.circular(4),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              widget.provider.name,
                              style: AppTypography.bodyMedium(
                                color: colors.textPrimary,
                              ).copyWith(fontWeight: FontWeight.w600),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          if (widget.isActive) ...[
                            const SizedBox(width: 6),
                            const AppBadge(
                              label: 'Active',
                              variant: AppBadgeVariant.brand,
                            ),
                          ],
                          if (widget.provider.isRecommended ?? false) ...[
                            const SizedBox(width: 6),
                            const AppBadge(
                              label: 'Recommended',
                              variant: AppBadgeVariant.info,
                            ),
                          ],
                          if (!widget.provider.requiresKey) ...[
                            const SizedBox(width: 6),
                            const AppBadge(
                              label: 'Local',
                              variant: AppBadgeVariant.success,
                            ),
                          ],
                          if (widget.provider.isCustom ?? false) ...[
                            const SizedBox(width: 6),
                            const AppBadge(
                              label: 'Custom',
                              variant: AppBadgeVariant.warning,
                            ),
                          ],
                        ],
                      ),
                      if (widget.provider.description.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(
                          widget.provider.description,
                          style: AppTypography.bodySmall(
                            color: colors.textMuted,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  AppButton(
                    onPressed:
                        widget.isLoadingModels ? null : widget.onRefreshModels,
                    variant: AppButtonVariant.ghost,
                    size: AppButtonSize.sm,
                    loading: widget.isLoadingModels,
                    tooltip: 'Refresh models',
                    icon: const Icon(Icons.refresh, size: 14),
                  ),
                  const SizedBox(width: 4),
                  AppButton(
                    text: widget.isActive ? 'Configure' : 'Connect',
                    onPressed: widget.onConnect,
                    variant: widget.isActive
                        ? AppButtonVariant.primary
                        : AppButtonVariant.secondary,
                    size: AppButtonSize.sm,
                  ),
                ],
              ),
            ],
          ),

          // Models Section
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(
              color: colors.surface,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: colors.border.withValues(alpha: 0.5)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                InkWell(
                  onTap: () {
                    final willExpand = !_isExpanded;
                    setState(() {
                      _isExpanded = willExpand;
                    });
                    if (willExpand &&
                        widget.models.isEmpty &&
                        !widget.isLoadingModels) {
                      widget.onRefreshModels();
                    }
                  },
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          if (widget.isLoadingModels) ...[
                            SizedBox(
                              width: 12,
                              height: 12,
                              child: CircularProgressIndicator(
                                strokeWidth: 1.5,
                                valueColor:
                                    AlwaysStoppedAnimation<Color>(colors.brand),
                              ),
                            ),
                            const SizedBox(width: 6),
                            Text(
                              'Discovering models…',
                              style: AppTypography.bodySmall(
                                color: colors.textMuted,
                              ),
                            ),
                          ] else ...[
                            Text(
                              '${effectiveModels.length} models available',
                              style: AppTypography.bodySmall(
                                color: colors.textMuted,
                              ).copyWith(fontWeight: FontWeight.w500),
                            ),
                            if (widget.provider.defaultModel.isNotEmpty) ...[
                              Text(
                                ' (default: ${widget.provider.defaultModel})',
                                style: AppTypography.bodySmall(
                                  color: colors.textMuted,
                                ),
                              ),
                            ],
                          ],
                        ],
                      ),
                      Icon(
                        _isExpanded
                            ? Icons.keyboard_arrow_up
                            : Icons.keyboard_arrow_down,
                        size: 16,
                        color: colors.textMuted,
                      ),
                    ],
                  ),
                ),
                if (_isExpanded) ...[
                  const SizedBox(height: 8),
                  Divider(color: colors.border, height: 1),
                  const SizedBox(height: 6),
                  if (effectiveModels.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 6),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              'No models discovered yet.',
                              style: AppTypography.codeSmall(
                                color: colors.textMuted,
                              ),
                            ),
                          ),
                          AppButton(
                            text: 'Fetch',
                            size: AppButtonSize.sm,
                            variant: AppButtonVariant.ghost,
                            loading: widget.isLoadingModels,
                            onPressed: widget.isLoadingModels
                                ? null
                                : widget.onRefreshModels,
                          ),
                        ],
                      ),
                    )
                  else
                    ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 140),
                    child: ListView.separated(
                      shrinkWrap: true,
                      itemCount: effectiveModels.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 4),
                      itemBuilder: (context, index) {
                        final modelName = effectiveModels[index];
                        return Row(
                          children: [
                            Expanded(
                              child: Text(
                                modelName,
                                style: AppTypography.codeSmall(
                                  color: colors.textPrimary,
                                ),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            InkWell(
                              onTap: () => widget.onUseModel(modelName),
                              child: Padding(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 6, vertical: 2),
                                child: Text(
                                  'Use',
                                  style: AppTypography.codeSmall(
                                    color: colors.brand,
                                  ).copyWith(fontWeight: FontWeight.w600),
                                ),
                              ),
                            ),
                          ],
                        );
                      },
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
