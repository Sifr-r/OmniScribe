import 'dart:async';
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
import 'package:omniscribe_client/presentation/common/app_select.dart';
import 'package:omniscribe_client/presentation/common/app_toggle.dart';
import 'package:omniscribe_client/presentation/common/error_banner.dart';
import 'package:omniscribe_client/presentation/common/feature_screen_scaffold.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';

class TranslationScreen extends ConsumerStatefulWidget {
  const TranslationScreen({super.key});

  @override
  ConsumerState<TranslationScreen> createState() => _TranslationScreenState();
}

class _TranslationScreenState extends ConsumerState<TranslationScreen> {
  late final TextEditingController _sourceTextController;

  static const List<String> _languages = [
    'French',
    'Spanish',
    'German',
    'Italian',
    'Portuguese',
    'Japanese',
    'Chinese (Simplified)',
    'Korean',
    'Russian',
    'Arabic',
    'Dutch',
  ];

  @override
  void initState() {
    super.initState();
    final initialText = ref.read(translationProvider).sourceText;
    _sourceTextController = TextEditingController(text: initialText);
  }

  @override
  void dispose() {
    _sourceTextController.dispose();
    super.dispose();
  }

  Future<void> _handleSyncTranslate() async {
    final text = _sourceTextController.text.trim();
    if (text.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please provide source text to translate.'),
        ),
      );
      return;
    }

    final notifier = ref.read(translationProvider.notifier);
    notifier.setSourceText(text);

    final config = ref.read(settingsStateProvider).runtimeConfig;
    await notifier.translate(
      apiBase: config?.translationApiBase ?? config?.apiBase,
      apiKey: config?.translationApiKey ?? config?.apiKey,
      fallbackModel: config?.translationModel ?? config?.model,
      dualTranslate: config?.dualTranslate ?? false,
    );
  }

  Future<void> _handleAsyncTranslate() async {
    final text = _sourceTextController.text.trim();
    if (text.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Please provide source text for async translation.'),
        ),
      );
      return;
    }

    final notifier = ref.read(translationProvider.notifier);
    notifier.setSourceText(text);

    final config = ref.read(settingsStateProvider).runtimeConfig;
    await notifier.translateAsync(
      apiBase: config?.translationApiBase ?? config?.apiBase,
      apiKey: config?.translationApiKey ?? config?.apiKey,
      fallbackModel: config?.translationModel ?? config?.model,
      autoPoll: true,
    );
  }

  void _copyOutput(String translatedOutput) {
    if (translatedOutput.isNotEmpty) {
      unawaited(Clipboard.setData(ClipboardData(text: translatedOutput)));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Translated text copied to clipboard.')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(translationProvider);
    final notifier = ref.read(translationProvider.notifier);
    final colors = context.colors;

    return FeatureScreenScaffold(
      title: 'Neural Translation Engine',
      badge: const AppBadge(
        label: 'LangGraph / NLLB-200',
        variant: AppBadgeVariant.brand,
      ),
      subtitle:
          'Context-aware dual-engine translation with term preservation & sliding window',
      headerAction: SizedBox(
        width: 200,
        child: AppSelect<String>(
          value: state.targetLanguage,
          items: _languages
              .map(
                (lang) => AppSelectItem(
                  value: lang,
                  label: lang,
                ),
              )
              .toList(),
          onChanged: (val) {
            if (val != null) {
              notifier.setTargetLanguage(val);
            }
          },
        ),
      ),
      errorBanner: state.error != null
          ? ErrorBanner(
              message: state.error!,
              onDismiss: notifier.clearError,
            )
          : null,
      panes: Column(
        children: [
          // Options Bar
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            decoration: BoxDecoration(
              color: colors.cardRaised,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: colors.border),
            ),
            child: Row(
              children: [
                Expanded(
                  child: AppToggle(
                    label: 'NLLB Fast Engine',
                    subtitle: 'Direct Meta NLLB-200 offline translation',
                    value: state.useNllb,
                    onChanged: notifier.setUseNllb,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),

          // Dual Pane: Source vs Translated
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Left Pane: Source Text
                Expanded(
                  child: AppCard(
                    padding: AppCardPadding.md,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SectionHeader(
                          title: 'Source Text',
                          action: _sourceTextController.text.isNotEmpty
                              ? InkWell(
                                  onTap: () {
                                    _sourceTextController.clear();
                                    notifier.clearSourceText();
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
                            controller: _sourceTextController,
                            maxLines: null,
                            expands: true,
                            onChanged: notifier.setSourceText,
                            style: AppTypography.code(
                              color: colors.textPrimary,
                            ),
                            decoration: InputDecoration(
                              hintText:
                                  'Enter or paste source document text here…',
                              hintStyle: AppTypography.code(
                                color: colors.textMuted,
                              ),
                              border: InputBorder.none,
                            ),
                          ),
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            Expanded(
                              child: AppButton(
                                text: state.isTranslating
                                    ? 'Translating…'
                                    : 'Translate (Sync)',
                                variant: AppButtonVariant.primary,
                                loading: state.isTranslating,
                                icon: const Icon(Icons.translate, size: 16),
                                onPressed: _handleSyncTranslate,
                              ),
                            ),
                            const SizedBox(width: 8),
                            AppButton(
                              text: 'Async',
                              variant: AppButtonVariant.secondary,
                              disabled: state.isTranslating,
                              onPressed: _handleAsyncTranslate,
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 16),

                // Right Pane: Translated Output
                Expanded(
                  child: AppCard(
                    padding: AppCardPadding.md,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SectionHeader(
                          title: 'Translated Output (${state.targetLanguage})',
                          action: state.translatedOutput.isNotEmpty
                              ? AppButton(
                                  text: 'Copy',
                                  variant: AppButtonVariant.ghost,
                                  size: AppButtonSize.sm,
                                  icon: const Icon(Icons.copy, size: 14),
                                  onPressed: () =>
                                      _copyOutput(state.translatedOutput),
                                )
                              : null,
                        ),
                        const SizedBox(height: 8),
                        if (state.asyncStatus != null) ...[
                          Container(
                            padding: const EdgeInsets.all(8),
                            margin: const EdgeInsets.only(bottom: 8),
                            decoration: BoxDecoration(
                              color: colors.cardRaised,
                              borderRadius: BorderRadius.circular(6),
                              border: Border.all(color: colors.border),
                            ),
                            child: Text(
                              state.asyncStatus!,
                              style: AppTypography.codeSmall(
                                color: colors.textMuted,
                              ),
                            ),
                          ),
                        ],
                        Expanded(
                          child: Container(
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: colors.cardRaised,
                              borderRadius: BorderRadius.circular(6),
                              border: Border.all(color: colors.border),
                            ),
                            child: state.isTranslating &&
                                    state.translatedOutput.isEmpty
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
                                          'Translating document chunks…',
                                          style: AppTypography.bodySmall(
                                            color: colors.textMuted,
                                          ),
                                        ),
                                      ],
                                    ),
                                  )
                                : SingleChildScrollView(
                                    child: SelectableText(
                                      state.translatedOutput.isNotEmpty
                                          ? state.translatedOutput
                                          : 'Translated text output will appear here once translation is triggered.',
                                      style: AppTypography.code(
                                        color:
                                            state.translatedOutput.isNotEmpty
                                                ? colors.textPrimary
                                                : colors.textMuted,
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
          ),
        ],
      ),
    );
  }
}
