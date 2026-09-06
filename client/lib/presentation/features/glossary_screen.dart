import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/feature_models.dart';
import 'package:omniscribe_client/data/providers/features_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/app_input.dart';
import 'package:omniscribe_client/presentation/common/app_modal.dart';
import 'package:omniscribe_client/presentation/common/app_select.dart';
import 'package:omniscribe_client/presentation/common/error_banner.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';

class GlossaryScreen extends ConsumerStatefulWidget {
  const GlossaryScreen({super.key});

  @override
  ConsumerState<GlossaryScreen> createState() => _GlossaryScreenState();
}

class _GlossaryScreenState extends ConsumerState<GlossaryScreen> {
  final _importFormKey = GlobalKey<_GlossaryImportFormState>();

  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      final notifier = ref.read(glossaryProvider.notifier);
      unawaited(notifier.loadLibraries());
      unawaited(notifier.loadMergedLexicon());
    });
  }

  void _showImportModal() {
    AppModal.show<void>(
      context: context,
      title: 'Import Terminology Glossary',
      subtitle: 'Upload a terminology file or import from a remote lexicon URL',
      maxWidth: AppModalWidth.md,
      actions: [
        AppButton(
          text: 'Cancel',
          variant: AppButtonVariant.ghost,
          onPressed: () => Navigator.of(context).pop(),
        ),
        AppButton(
          text: 'Import Glossary',
          variant: AppButtonVariant.primary,
          onPressed: () => unawaited(_importFormKey.currentState?.submit()),
        ),
      ],
      content: _GlossaryImportForm(key: _importFormKey),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(glossaryProvider);
    final notifier = ref.read(glossaryProvider.notifier);
    final colors = context.colors;

    final activeCount = state.libraries.where((l) => l.enabled).length;

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
                          'Terminology Glossary',
                          style: AppTypography.displaySmall(
                            color: colors.textPrimary,
                          ),
                        ),
                        const SizedBox(width: 10),
                        AppBadge(
                          label: '$activeCount active',
                          variant: AppBadgeVariant.success,
                          style: AppBadgeStyle.filled,
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Manage domain lexicons, term overrides, and dictionary mappings',
                      style: AppTypography.bodySmall(
                        color: colors.textMuted,
                      ),
                    ),
                  ],
                ),
                Row(
                  children: [
                    // Segmented Control
                    Container(
                      padding: const EdgeInsets.all(3),
                      decoration: BoxDecoration(
                        color: colors.cardRaised,
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(color: colors.border),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          _buildTabButton(
                            'Libraries (${state.libraries.length})',
                            0,
                            state.activeViewIndex,
                            notifier,
                            colors,
                          ),
                          _buildTabButton(
                            state.selectedLibrary != null
                                ? 'Entries (${state.entries.length})'
                                : 'Entries',
                            1,
                            state.activeViewIndex,
                            notifier,
                            colors,
                          ),
                          _buildTabButton(
                            'Merged Lexicon (${state.mergedLexicon.length})',
                            2,
                            state.activeViewIndex,
                            notifier,
                            colors,
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 12),
                    AppButton(
                      text: 'Import glossary',
                      variant: AppButtonVariant.primary,
                      icon: const Icon(Icons.add, size: 14),
                      onPressed: _showImportModal,
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 16),

            // Views Content
            Expanded(
              child: state.activeViewIndex == 0
                  ? _buildLibrariesTable(state, notifier, colors)
                  : (state.activeViewIndex == 1
                      ? _buildEntriesView(state, notifier, colors)
                      : _buildMergedView(state, colors)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTabButton(
    String label,
    int index,
    int activeViewIndex,
    GlossaryNotifier notifier,
    AppColorScheme colors,
  ) {
    final isSelected = activeViewIndex == index;
    return InkWell(
      onTap: () => notifier.setActiveViewIndex(index),
      borderRadius: BorderRadius.circular(4),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: isSelected ? colors.surface : Colors.transparent,
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 12,
            fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
            color: isSelected ? colors.brand : colors.textMuted,
          ),
        ),
      ),
    );
  }

  Widget _buildLibrariesTable(
    GlossaryState state,
    GlossaryNotifier notifier,
    AppColorScheme colors,
  ) {
    return AppCard(
      padding: AppCardPadding.none,
      child: state.libraries.isEmpty
          ? Center(
              child: Text(
                'No glossary libraries imported yet. Click "Import glossary" to add terminology.',
                style: AppTypography.bodySmall(color: colors.textMuted),
              ),
            )
          : ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: SingleChildScrollView(
                child: DataTable(
                  headingRowColor: WidgetStateProperty.all(colors.cardRaised),
                  dataRowColor: WidgetStateProperty.all(Colors.transparent),
                  dividerThickness: 1,
                  horizontalMargin: 16,
                  columnSpacing: 24,
                  columns: const [
                    DataColumn(
                      label: Text(
                        'Priority',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                        ),
                      ),
                    ),
                    DataColumn(
                      label: Text(
                        'Name',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                        ),
                      ),
                    ),
                    DataColumn(
                      label: Text(
                        'Format',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                        ),
                      ),
                    ),
                    DataColumn(
                      label: Text(
                        'Entries',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                        ),
                      ),
                    ),
                    DataColumn(
                      label: Text(
                        'Status',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                        ),
                      ),
                    ),
                    DataColumn(
                      label: Text(
                        'Actions',
                        style: TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                        ),
                      ),
                    ),
                  ],
                  rows: state.libraries.map((lib) {
                    return DataRow(
                      cells: [
                        DataCell(
                          Text(
                            '#${lib.priority}',
                            style: AppTypography.codeSmall(
                              color: colors.textMuted,
                            ),
                          ),
                        ),
                        DataCell(
                          Text(
                            lib.name,
                            style: AppTypography.bodySmall(
                              color: colors.brand,
                            ).copyWith(fontWeight: FontWeight.w600),
                          ),
                        ),
                        DataCell(
                          Text(
                            lib.format.value.toUpperCase(),
                            style: AppTypography.codeSmall(
                              color: colors.textMuted,
                            ),
                          ),
                        ),
                        DataCell(
                          Text(
                            '${lib.entryCount}',
                            style: AppTypography.codeSmall(
                              color: colors.textPrimary,
                            ),
                          ),
                        ),
                        DataCell(
                          InkWell(
                            onTap: () {
                              unawaited(
                                  notifier.toggleLibrary(lib, !lib.enabled));
                            },
                            borderRadius: BorderRadius.circular(10),
                            child: AppBadge(
                              label: lib.enabled ? 'Enabled' : 'Disabled',
                              variant: lib.enabled
                                  ? AppBadgeVariant.success
                                  : AppBadgeVariant.neutral,
                              style: AppBadgeStyle.filled,
                            ),
                          ),
                        ),
                        DataCell(
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              AppButton(
                                text: 'View entries',
                                variant: AppButtonVariant.ghost,
                                size: AppButtonSize.sm,
                                onPressed: () {
                                  unawaited(notifier.loadEntries(lib));
                                },
                              ),
                              const SizedBox(width: 4),
                              AppButton(
                                text: 'Delete',
                                variant: AppButtonVariant.danger,
                                size: AppButtonSize.sm,
                                onPressed: () {
                                  unawaited(notifier.deleteLibrary(lib.id));
                                },
                              ),
                            ],
                          ),
                        ),
                      ],
                    );
                  }).toList(),
                ),
              ),
            ),
    );
  }

  Widget _buildEntriesView(
    GlossaryState state,
    GlossaryNotifier notifier,
    AppColorScheme colors,
  ) {
    return AppCard(
      padding: AppCardPadding.md,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: state.selectedLibrary != null
                ? '${state.selectedLibrary!.name} (${state.entries.length} terms)'
                : 'Glossary Entries',
            action: AppButton(
              text: 'Back to libraries',
              variant: AppButtonVariant.ghost,
              size: AppButtonSize.sm,
              onPressed: () => notifier.setActiveViewIndex(0),
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: state.entries.isEmpty
                ? Center(
                    child: Text(
                      'No terms found in this glossary library.',
                      style: AppTypography.bodySmall(
                        color: colors.textMuted,
                      ),
                    ),
                  )
                : ListView.separated(
                    itemCount: state.entries.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 6),
                    itemBuilder: (context, index) {
                      final entry = state.entries[index];
                      return Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 14,
                          vertical: 10,
                        ),
                        decoration: BoxDecoration(
                          color: colors.cardRaised,
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(color: colors.border),
                        ),
                        child: Row(
                          children: [
                            Expanded(
                              flex: 2,
                              child: Text(
                                entry.source,
                                style: AppTypography.code(
                                  color: colors.textPrimary,
                                ).copyWith(fontWeight: FontWeight.w600),
                              ),
                            ),
                            Icon(
                              Icons.arrow_forward,
                              size: 14,
                              color: colors.textMuted,
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              flex: 2,
                              child: Text(
                                entry.target,
                                style: AppTypography.code(
                                  color: colors.brand,
                                ).copyWith(fontWeight: FontWeight.w600),
                              ),
                            ),
                            if (entry.note != null && entry.note!.isNotEmpty)
                              Expanded(
                                flex: 3,
                                child: Text(
                                  entry.note!,
                                  style: AppTypography.bodySmall(
                                    color: colors.textMuted,
                                  ).copyWith(fontStyle: FontStyle.italic),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                          ],
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  Widget _buildMergedView(
    GlossaryState state,
    AppColorScheme colors,
  ) {
    final entries = state.mergedLexicon.entries.toList();

    return AppCard(
      padding: AppCardPadding.md,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionHeader(
            title: 'Merged Lexicon Table',
          ),
          const SizedBox(height: 8),
          Expanded(
            child: entries.isEmpty
                ? Center(
                    child: Text(
                      'No active merged terms available.',
                      style: AppTypography.bodySmall(
                        color: colors.textMuted,
                      ),
                    ),
                  )
                : GridView.builder(
                    gridDelegate:
                        const SliverGridDelegateWithMaxCrossAxisExtent(
                      maxCrossAxisExtent: 320,
                      mainAxisExtent: 44,
                      crossAxisSpacing: 10,
                      mainAxisSpacing: 10,
                    ),
                    itemCount: entries.length,
                    itemBuilder: (context, index) {
                      final item = entries[index];
                      return Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 10,
                          vertical: 6,
                        ),
                        decoration: BoxDecoration(
                          color: colors.cardRaised,
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(color: colors.border),
                        ),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Flexible(
                              child: Text(
                                item.key,
                                style: AppTypography.codeSmall(
                                  color: colors.textPrimary,
                                ).copyWith(fontWeight: FontWeight.w600),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            const SizedBox(width: 6),
                            Icon(
                              Icons.arrow_forward,
                              size: 12,
                              color: colors.textMuted,
                            ),
                            const SizedBox(width: 6),
                            Flexible(
                              child: Text(
                                item.value,
                                style: AppTypography.codeSmall(
                                  color: colors.success,
                                ).copyWith(fontWeight: FontWeight.w600),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ],
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}

/// Import form owned by a [ConsumerStatefulWidget] so the text controllers
/// are disposed when the modal closes, and import failures surface inline
/// instead of being swallowed.
class _GlossaryImportForm extends ConsumerStatefulWidget {
  const _GlossaryImportForm({super.key});

  @override
  ConsumerState<_GlossaryImportForm> createState() =>
      _GlossaryImportFormState();
}

class _GlossaryImportFormState extends ConsumerState<_GlossaryImportForm> {
  final _nameController = TextEditingController();
  final _textController = TextEditingController();
  final _urlController = TextEditingController();
  String _formatValue = 'json_pairs';
  bool _importing = false;
  String? _error;

  @override
  void dispose() {
    _nameController.dispose();
    _textController.dispose();
    _urlController.dispose();
    super.dispose();
  }

  Future<void> submit() async {
    final urlText = _urlController.text.trim();
    final nameText = _nameController.text.trim();
    final contentText = _textController.text.trim();

    if (urlText.isEmpty && contentText.isEmpty) {
      setState(() {
        _error = 'Provide inline lexicon content or an import URL.';
      });
      return;
    }

    setState(() {
      _importing = true;
      _error = null;
    });

    final notifier = ref.read(glossaryProvider.notifier);
    final fmt = GlossaryFormat.fromString(_formatValue);
    try {
      if (urlText.isNotEmpty) {
        await notifier.importGlossaryUrl(
          url: urlText,
          format: fmt,
          name: nameText.isNotEmpty ? nameText : null,
        );
      } else {
        await notifier.importGlossaryJson(
          format: fmt,
          name: nameText.isNotEmpty ? nameText : null,
          text: contentText,
        );
        // importGlossaryJson records failures on the glossary state instead
        // of throwing; surface them and keep the modal open.
        final glossaryError = ref.read(glossaryProvider).error;
        if (glossaryError != null) {
          if (mounted) {
            setState(() {
              _importing = false;
              _error = glossaryError;
            });
          }
          return;
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _importing = false;
          _error = e.toString();
        });
      }
      return;
    }

    if (mounted) {
      Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (_error != null) ...[
          ErrorBanner(message: _error!),
          const SizedBox(height: 12),
        ],
        AppInput(
          controller: _nameController,
          label: 'Glossary Name',
          placeholder: 'e.g. Financial Terms EN-ES',
        ),
        const SizedBox(height: 12),
        AppSelect<String>(
          label: 'Format',
          value: _formatValue,
          items: const [
            AppSelectItem(
              value: 'json_pairs',
              label: 'JSON Pairs / Paired Text',
            ),
            AppSelectItem(
              value: 'csv',
              label: 'CSV (Comma Separated)',
            ),
            AppSelectItem(
              value: 'tsv',
              label: 'TSV (Tab Separated)',
            ),
            AppSelectItem(
              value: 'tbx',
              label: 'TBX Glossary File',
            ),
            AppSelectItem(
              value: 'xliff',
              label: 'XLIFF Translation File',
            ),
          ],
          onChanged: (val) {
            if (val != null) {
              setState(() => _formatValue = val);
            }
          },
        ),
        const SizedBox(height: 12),
        AppInput(
          controller: _textController,
          label: 'Inline Lexicon Content',
          placeholder: 'source = target\nplaintiff = demandeur',
          maxLines: 4,
          monospace: true,
        ),
        const SizedBox(height: 12),
        AppInput(
          controller: _urlController,
          label: 'Or Import From URL',
          placeholder: 'https://example.com/lexicon.json',
          monospace: true,
        ),
        if (_importing) ...[
          const SizedBox(height: 12),
          const LinearProgressIndicator(minHeight: 2),
        ],
      ],
    );
  }
}
