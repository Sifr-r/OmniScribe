import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';

/// Subwidget for selecting document processors (layout enrichment, table extraction,
/// structure analysis, reading order, etc.).
class DocumentProcessorsCard extends StatelessWidget {
  const DocumentProcessorsCard({
    super.key,
    required this.settings,
    required this.onSettingsChanged,
  });

  final ProcessSettings settings;
  final ValueChanged<ProcessSettings> onSettingsChanged;

  void _toggleProcessor(String processorId) {
    final proc = DocumentProcessorName.tryFromString(processorId);
    if (proc == null) return;
    final current = settings.documentProcessors;
    final updated = current.contains(proc)
        ? current.where((p) => p != proc).toList()
        : [...current, proc];
    onSettingsChanged(settings.copyWith(documentProcessors: updated));
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final s = settings;

    return AppCard(
      padding: AppCardPadding.md,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionHeader(title: 'Document Processors'),
          Text(
            'Select analyzers to enrich document structure and reading order:',
            style: AppTypography.bodySmall(
              color: colors.textMuted,
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: DocumentProcessorInfo.all.map((info) {
              final isSelected =
                  s.documentProcessors.any((p) => p.value == info.id);
              return Tooltip(
                message: info.description,
                child: InkWell(
                  onTap: () => _toggleProcessor(info.id),
                  borderRadius: BorderRadius.circular(6),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 150),
                    padding:
                        const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: isSelected
                          ? colors.brand.withValues(alpha: 0.15)
                          : colors.cardRaised,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                        color: isSelected
                            ? colors.brand.withValues(alpha: 0.5)
                            : colors.border,
                        width: 1.0,
                      ),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (isSelected) ...[
                          Icon(Icons.check, size: 12, color: colors.brand),
                          const SizedBox(width: 4),
                        ],
                        Text(
                          info.label,
                          style: AppTypography.bodySmall(
                            color:
                                isSelected ? colors.brand : colors.textMuted,
                          ).copyWith(
                            fontWeight: isSelected
                                ? FontWeight.w600
                                : FontWeight.w500,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
        ],
      ),
    );
  }
}
