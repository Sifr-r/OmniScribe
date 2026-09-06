import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/providers/features_notifier.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/error_banner.dart';
import 'package:omniscribe_client/presentation/common/feature_screen_scaffold.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';

class ExtractionScreen extends ConsumerStatefulWidget {
  const ExtractionScreen({super.key});

  @override
  ConsumerState<ExtractionScreen> createState() => _ExtractionScreenState();
}

class _ExtractionScreenState extends ConsumerState<ExtractionScreen> {
  late final TextEditingController _inputTextController;
  late final TextEditingController _customSchemaController;

  static const List<Map<String, String>> _templates = [
    {'id': 'invoice', 'label': 'Invoice'},
    {'id': 'resume', 'label': 'Resume'},
    {'id': 'academic', 'label': 'Academic'},
    {'id': 'table', 'label': 'Table Extraction'},
    {'id': 'custom', 'label': 'Custom Schema'},
  ];

  @override
  void initState() {
    super.initState();
    final extractionState = ref.read(extractionProvider);
    _inputTextController =
        TextEditingController(text: extractionState.inputText);
    _customSchemaController =
        TextEditingController(text: extractionState.customSchema);
  }

  @override
  void dispose() {
    _inputTextController.dispose();
    _customSchemaController.dispose();
    super.dispose();
  }

  Future<void> _handleExtract() async {
    final text = _inputTextController.text.trim();
    if (text.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please enter or paste input text to extract.'),
        ),
      );
      return;
    }

    final notifier = ref.read(extractionProvider.notifier);
    notifier.setInputText(text);
    notifier.setCustomSchema(_customSchemaController.text.trim());

    final config = ref.read(settingsStateProvider).runtimeConfig;
    await notifier.extract(
      model: config?.model,
      apiBase: config?.apiBase,
      apiKey: config?.apiKey,
    );
  }

  void _copyJson(dynamic extractedData) {
    if (extractedData != null) {
      final jsonStr =
          const JsonEncoder.withIndent('  ').convert(extractedData);
      unawaited(Clipboard.setData(ClipboardData(text: jsonStr)));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('JSON data copied to clipboard.')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(extractionProvider);
    final notifier = ref.read(extractionProvider.notifier);
    final colors = context.colors;

    return FeatureScreenScaffold(
      title: 'Structured Information Extraction',
      badge: const AppBadge(
        label: 'JSON Schema / AST',
        variant: AppBadgeVariant.brand,
      ),
      subtitle:
          'Extract strongly-typed entities, tables, invoices, and key-values from OCR document trees',
      headerAction:
          // Template Segmented Control
          Container(
        padding: const EdgeInsets.all(3),
        decoration: BoxDecoration(
          color: colors.cardRaised,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: colors.border),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: _templates.map((tpl) {
            final isSelected = state.selectedTemplate == tpl['id'];
            return InkWell(
              onTap: () => notifier.setSelectedTemplate(tpl['id']!),
              borderRadius: BorderRadius.circular(4),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 5,
                ),
                decoration: BoxDecoration(
                  color: isSelected ? colors.surface : Colors.transparent,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(
                  tpl['label']!,
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: isSelected
                        ? FontWeight.w600
                        : FontWeight.normal,
                    color: isSelected ? colors.brand : colors.textMuted,
                  ),
                ),
              ),
            );
          }).toList(),
        ),
      ),
      errorBanner: state.error != null
          ? ErrorBanner(
              message: state.error!,
              onDismiss: notifier.clearError,
            )
          : null,
      panes: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Left Pane: Input Text & Custom Prompt
          Expanded(
            child: AppCard(
              padding: AppCardPadding.md,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SectionHeader(
                    title: 'Input Text / Document Artifact',
                    action: _inputTextController.text.isNotEmpty
                        ? InkWell(
                            onTap: () {
                              _inputTextController.clear();
                              notifier.clearInputText();
                              setState(() {});
                            },
                            child: Text(
                              'Clear',
                              style: AppTypography.codeSmall(
                                color: colors.error,
                              ),
                            ),
                          )
                        : null,
                  ),
                  const SizedBox(height: 8),
                  Expanded(
                    child: TextField(
                      controller: _inputTextController,
                      maxLines: null,
                      expands: true,
                      onChanged: notifier.setInputText,
                      style: AppTypography.code(
                        color: colors.textPrimary,
                      ),
                      decoration: InputDecoration(
                        hintText:
                            'Paste invoice text, resume, receipt, or academic table here…',
                        hintStyle: AppTypography.code(
                          color: colors.textMuted,
                        ),
                        border: InputBorder.none,
                      ),
                    ),
                  ),
                  if (state.selectedTemplate == 'custom') ...[
                    const SizedBox(height: 12),
                    Divider(color: colors.border, height: 1),
                    const SizedBox(height: 8),
                    Text(
                      'Custom JSON Schema Definition',
                      style: AppTypography.labelMedium(
                        color: colors.textMuted,
                      ).copyWith(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 6),
                    SizedBox(
                      height: 110,
                      child: TextField(
                        controller: _customSchemaController,
                        maxLines: null,
                        expands: true,
                        onChanged: notifier.setCustomSchema,
                        style: AppTypography.code(
                          color: colors.success,
                        ),
                        decoration: InputDecoration(
                          filled: true,
                          fillColor: colors.cardRaised,
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(6),
                            borderSide: BorderSide(color: colors.border),
                          ),
                        ),
                      ),
                    ),
                  ],
                  const SizedBox(height: 14),
                  AppButton(
                    text: state.isExtracting
                        ? 'Extracting…'
                        : 'Run Structured Extraction',
                    variant: AppButtonVariant.primary,
                    fullWidth: true,
                    loading: state.isExtracting,
                    icon: const Icon(Icons.auto_fix_high, size: 16),
                    onPressed: _handleExtract,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(width: 16),

          // Right Pane: Extracted JSON AST Output
          Expanded(
            child: AppCard(
              padding: AppCardPadding.md,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SectionHeader(
                    title: 'Extracted Output AST',
                    action: state.extractedData != null
                        ? AppButton(
                            text: 'Copy JSON',
                            variant: AppButtonVariant.ghost,
                            size: AppButtonSize.sm,
                            icon: const Icon(Icons.copy, size: 14),
                            onPressed: () => _copyJson(state.extractedData),
                          )
                        : null,
                  ),
                  const SizedBox(height: 8),
                  Expanded(
                    child: Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: colors.cardRaised,
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(color: colors.border),
                      ),
                      child: state.isExtracting
                          ? Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  CircularProgressIndicator(
                                    valueColor:
                                        AlwaysStoppedAnimation<Color>(
                                      colors.brand,
                                    ),
                                  ),
                                  const SizedBox(height: 12),
                                  Text(
                                    'Parsing entities and validating against schema…',
                                    style: AppTypography.bodySmall(
                                      color: colors.textMuted,
                                    ),
                                  ),
                                ],
                              ),
                            )
                          : state.extractedData != null
                              ? SingleChildScrollView(
                                  child: SelectableText(
                                    const JsonEncoder.withIndent('  ')
                                        .convert(state.extractedData),
                                    style: AppTypography.code(
                                      color: colors.success,
                                    ).copyWith(height: 1.4),
                                  ),
                                )
                              : Center(
                                  child: Text(
                                    'Extracted JSON output structure will appear here after extraction.',
                                    style: AppTypography.bodySmall(
                                      color: colors.textMuted,
                                    ),
                                  ),
                                ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
