import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/provider_notifier.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/providers/settings_state.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_card.dart';
import 'package:omniscribe_client/presentation/common/app_input.dart';
import 'package:omniscribe_client/presentation/common/app_toggle.dart';
import 'package:omniscribe_client/presentation/common/section_header.dart';
import 'package:omniscribe_client/presentation/providers/provider_modal.dart';

/// Settings & Configuration screen.
class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  int _activeTabIndex = 0;
  bool _isSaving = false;
  String? _saveStatusMessage;

  late TextEditingController _serverUrlController;
  late TextEditingController _bearerTokenController;
  late TextEditingController _apiBaseController;
  late TextEditingController _apiKeyController;
  late TextEditingController _modelController;
  late TextEditingController _dpiController;
  late TextEditingController _concurrencyController;
  late TextEditingController _denseThresholdController;
  late TextEditingController _maxImageDimController;
  late TextEditingController _slidingWindowController;

  final List<String> _tabs = [
    'General & Server',
    'OCR Pipeline',
    'Translation & Voice',
    'Security & Auth',
  ];

  @override
  void initState() {
    super.initState();
    final settings = ref.read(settingsStateProvider);
    final config = settings.runtimeConfig;
    _serverUrlController = TextEditingController(text: settings.serverBaseUrl);
    _bearerTokenController =
        TextEditingController(text: settings.serverBearerToken ?? '');
    _apiBaseController = TextEditingController(text: config?.apiBase ?? '');
    _apiKeyController = TextEditingController(
      text: config?.apiKey == '******' ? '' : (config?.apiKey ?? ''),
    );
    _modelController = TextEditingController(text: config?.model ?? '');
    _dpiController = TextEditingController(text: '${config?.dpi ?? 200}');
    _concurrencyController =
        TextEditingController(text: '${config?.concurrency ?? 4}');
    _denseThresholdController =
        TextEditingController(text: '${config?.denseThreshold ?? 10}');
    _maxImageDimController =
        TextEditingController(text: '${config?.maxImageDim ?? 2048}');
    _slidingWindowController =
        TextEditingController(text: '${config?.slidingWindowWords ?? 32}');
  }

  @override
  void dispose() {
    _serverUrlController.dispose();
    _bearerTokenController.dispose();
    _apiBaseController.dispose();
    _apiKeyController.dispose();
    _modelController.dispose();
    _dpiController.dispose();
    _concurrencyController.dispose();
    _denseThresholdController.dispose();
    _maxImageDimController.dispose();
    _slidingWindowController.dispose();
    super.dispose();
  }

  Future<void> _saveAllSettings() async {
    setState(() {
      _isSaving = true;
      _saveStatusMessage = null;
    });

    try {
      final notifier = ref.read(settingsStateProvider.notifier);
      final settings = ref.read(settingsStateProvider);
      final runtimeConfig = settings.runtimeConfig;
      if (runtimeConfig == null) {
        if (mounted) {
          setState(() {
            _isSaving = false;
            _saveStatusMessage =
                'No runtime config loaded yet — connect to a server first.';
          });
        }
        return;
      }

      // Only the fields this screen edits. ConfigUpdate omits nulls and the
      // server merges just the keys present, so everything else keeps its
      // current value. Building this from ProcessSettings.defaultSettings()
      // used to reset every pipeline flag shown elsewhere.
      final ConfigUpdate update = ConfigUpdate(
        apiBase: _apiBaseController.text.trim().isNotEmpty
            ? _apiBaseController.text.trim()
            : runtimeConfig.apiBase,
        apiKey: _apiKeyController.text.trim().isNotEmpty
            ? _apiKeyController.text.trim()
            : null,
        model: _modelController.text.trim().isNotEmpty
            ? _modelController.text.trim()
            : runtimeConfig.model,
        dpi: int.tryParse(_dpiController.text),
        concurrency: int.tryParse(_concurrencyController.text),
        denseThreshold: int.tryParse(_denseThresholdController.text),
        maxImageDim: int.tryParse(_maxImageDimController.text),
      );

      await notifier.updateOcr(update);

      if (mounted) {
        setState(() {
          _isSaving = false;
          _saveStatusMessage = 'All configuration changes saved successfully.';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isSaving = false;
          _saveStatusMessage = 'Error saving settings: $e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsStateProvider);
    final providerState = ref.watch(providerBrowserProvider);
    final colors = context.colors;

    // Keep controllers in sync when runtimeConfig updates reactively
    ref.listen<String?>(
      settingsStateProvider.select((s) => s.runtimeConfig?.model),
      (previous, next) {
        if (next != null && next.isNotEmpty && previous != next) {
          _modelController.text = next;
        }
      },
    );
    ref.listen<String?>(
      settingsStateProvider.select((s) => s.runtimeConfig?.apiBase),
      (previous, next) {
        if (next != null && next.isNotEmpty && previous != next) {
          _apiBaseController.text = next;
        }
      },
    );

    return Scaffold(
      backgroundColor: colors.background,
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1000),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Settings & Configuration',
                            style: AppTypography.displaySmall(
                              color: colors.textPrimary,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            'Configure endpoints, pipeline parameters, inference providers, and limits',
                            style: AppTypography.bodySmall(
                              color: colors.textMuted,
                            ),
                          ),
                        ],
                      ),
                    ),
                    Row(
                      children: [
                        AppButton(
                          text: 'Browse providers',
                          variant: AppButtonVariant.secondary,
                          icon: const Icon(Icons.hub, size: 14),
                          onPressed: () => ProviderModal.show(context),
                        ),
                        const SizedBox(width: 8),
                        AppButton(
                          text: 'Save settings',
                          variant: AppButtonVariant.primary,
                          loading: _isSaving,
                          icon: const Icon(Icons.check, size: 14),
                          onPressed: _saveAllSettings,
                        ),
                      ],
                    ),
                  ],
                ),
                const SizedBox(height: 16),

                if (_saveStatusMessage != null) ...[
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 14, vertical: 10),
                    decoration: BoxDecoration(
                      color: colors.success.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                        color: colors.success.withValues(alpha: 0.35),
                      ),
                    ),
                    child: Row(
                      children: [
                        Icon(Icons.check_circle,
                            size: 16, color: colors.success),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            _saveStatusMessage!,
                            style: AppTypography.bodySmall(
                              color: colors.success,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                ],

                // Tabs
                Container(
                  decoration: BoxDecoration(
                    border: Border(bottom: BorderSide(color: colors.border)),
                  ),
                  child: Row(
                    children: List.generate(_tabs.length, (index) {
                      final isSelected = _activeTabIndex == index;
                      return InkWell(
                        onTap: () => setState(() => _activeTabIndex = index),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 16, vertical: 10),
                          decoration: BoxDecoration(
                            border: Border(
                              bottom: BorderSide(
                                color: isSelected
                                    ? colors.brand
                                    : Colors.transparent,
                                width: 2,
                              ),
                            ),
                          ),
                          child: Text(
                            _tabs[index],
                            style: TextStyle(
                              fontSize: 13,
                              fontWeight: isSelected
                                  ? FontWeight.w600
                                  : FontWeight.normal,
                              color: isSelected
                                  ? colors.brand
                                  : colors.textMuted,
                            ),
                          ),
                        ),
                      );
                    }),
                  ),
                ),
                const SizedBox(height: 20),

                // Tab content
                if (_activeTabIndex == 0)
                  _buildGeneralServerTab(colors, settings, providerState)
                else if (_activeTabIndex == 1)
                  _buildOcrPipelineTab(colors, settings)
                else if (_activeTabIndex == 2)
                  _buildTranslationVoiceTab(colors, settings)
                else if (_activeTabIndex == 3)
                  _buildSecurityAuthTab(colors, settings),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildGeneralServerTab(
    AppColorScheme colors,
    SettingsState settings,
    dynamic providerState,
  ) {
    final config = settings.runtimeConfig;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppCard(
          padding: AppCardPadding.lg,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionHeader(
                title: 'OmniScribe Backend Connection',
              ),
              const SizedBox(height: 8),
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: AppInput(
                      controller: _serverUrlController,
                      label: 'Backend Base URL',
                      placeholder: 'http://127.0.0.1:8000',
                      monospace: true,
                    ),
                  ),
                  const SizedBox(width: 12),
                  AppButton(
                    text: 'Test connection',
                    variant: AppButtonVariant.secondary,
                    icon: const Icon(Icons.network_check, size: 14),
                    onPressed: () {
                      ref
                          .read(settingsStateProvider.notifier)
                          .setServerBaseUrl(_serverUrlController.text.trim());
                    },
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: AppInput(
                      key: const ValueKey('settings-bearer-token'),
                      controller: _bearerTokenController,
                      label: 'Bearer Token',
                      placeholder: 'OMNISCRIBE_AUTH_TOKEN',
                      helperText:
                          'Leave empty when the backend has no OMNISCRIBE_AUTH_TOKEN',
                      obscureText: true,
                      showClearButton: true,
                      monospace: true,
                    ),
                  ),
                  const SizedBox(width: 12),
                  AppButton(
                    text: 'Apply token',
                    variant: AppButtonVariant.secondary,
                    icon: const Icon(Icons.key, size: 14),
                    onPressed: () {
                      ref
                          .read(settingsStateProvider.notifier)
                          .setServerBearerToken(
                              _bearerTokenController.text.trim());
                    },
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        AppCard(
          padding: AppCardPadding.lg,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SectionHeader(
                title: 'Active Inference Provider',
                action: AppButton(
                  text: 'Provider catalog',
                  variant: AppButtonVariant.ghost,
                  size: AppButtonSize.sm,
                  onPressed: () => ProviderModal.show(context),
                ),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: colors.cardRaised,
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(color: colors.border),
                      ),
                      child: Row(
                        children: [
                          Icon(Icons.memory, size: 20, color: colors.brand),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  'Provider: ${settings.activeProviderId.toUpperCase()}',
                                  style: AppTypography.bodySmall(
                                    color: colors.textPrimary,
                                  ).copyWith(fontWeight: FontWeight.w600),
                                ),
                                Text(
                                  'Endpoint: ${config?.apiBase ?? "—"}',
                                  style: AppTypography.codeSmall(
                                    color: colors.textMuted,
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ],
                            ),
                          ),
                          if (settings.isLoading) ...[
                            const SizedBox(width: 8),
                            const SizedBox(
                              width: 12,
                              height: 12,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              AppInput(
                key: const ValueKey('settings-api-base'),
                controller: _apiBaseController,
                label: 'API Base URL',
                placeholder: 'e.g. http://localhost:1234/v1',
                monospace: true,
              ),
              const SizedBox(height: 16),
              AppInput(
                key: const ValueKey('settings-api-key'),
                controller: _apiKeyController,
                label: 'API Key (optional for local)',
                placeholder: 'Enter API key if required',
                obscureText: true,
                monospace: true,
              ),
              const SizedBox(height: 16),
              Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: AppInput(
                      key: const ValueKey('settings-model-id'),
                      controller: _modelController,
                      label: 'Model ID',
                      placeholder: 'e.g. deepseek-ocr-2',
                      helperText: settings.ocrModels.isEmpty
                          ? 'Browse providers to discover available models'
                          : '${settings.ocrModels.length} model(s) discovered '
                              'for ${settings.activeProviderId}',
                      monospace: true,
                    ),
                  ),
                  if (settings.ocrModels.isNotEmpty) ...[
                    const SizedBox(width: 12),
                    PopupMenuButton<String>(
                      tooltip: 'Pick a discovered model',
                      padding: EdgeInsets.zero,
                      child: Padding(
                        padding: const EdgeInsets.fromLTRB(4, 0, 4, 22),
                        child: Text(
                          'Select model (${settings.ocrModels.length})',
                          style: AppTypography.bodySmall(
                            color: colors.brand,
                          ).copyWith(fontWeight: FontWeight.w500),
                        ),
                      ),
                      itemBuilder: (context) => settings.ocrModels
                          .map(
                            (m) => PopupMenuItem<String>(
                              value: m,
                              child: Text(
                                m,
                                style: AppTypography.codeSmall(
                                  color: colors.textPrimary,
                                ),
                              ),
                            ),
                          )
                          .toList(),
                      onSelected: (model) => _modelController.text = model,
                    ),
                  ],
                ],
              ),
              const SizedBox(height: 16),
              AppToggle(
                label: 'Dark Mode Theme',
                subtitle:
                    'Toggle between Obsidian Dark and Alabaster Light theme.',
                value: settings.isDarkMode,
                onChanged: (val) => ref
                    .read(settingsStateProvider.notifier)
                    .toggleDarkMode(val),
              ),
              if (settings.error != null) ...[
                const SizedBox(height: 12),
                AppBadge(
                  label: 'Error: ${settings.error}',
                  variant: AppBadgeVariant.error,
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildOcrPipelineTab(
      AppColorScheme colors, SettingsState settings) {
    final config = settings.runtimeConfig;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppCard(
          padding: AppCardPadding.lg,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionHeader(
                title: 'OCR Parameters & Concurrency',
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: AppInput(
                      controller: _dpiController,
                      label: 'Rendering DPI',
                      placeholder: '200',
                      helperText:
                          'Default 200 DPI for balance of speed and clarity',
                      monospace: true,
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: AppInput(
                      controller: _concurrencyController,
                      label: 'Worker Concurrency',
                      placeholder: '4',
                      helperText: 'Simultaneous pages processed in parallel',
                      monospace: true,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: AppInput(
                      controller: _denseThresholdController,
                      label: 'Dense Mode Threshold',
                      placeholder: '10',
                      helperText:
                          'Minimum block count to activate dense chunking',
                      monospace: true,
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: AppInput(
                      controller: _maxImageDimController,
                      label: 'Max Image Dimension (px)',
                      placeholder: '2048',
                      helperText:
                          'Caps page canvas size before vision model',
                      monospace: true,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              AppToggle(
                label: 'Async Processing by Default',
                subtitle:
                    'Queue background processing jobs instead of blocking requests.',
                value: settings.useAsync,
                onChanged: (val) =>
                    ref.read(settingsStateProvider.notifier).setUseAsync(val),
              ),
              const SizedBox(height: 12),
              AppToggle(
                label: 'Dual Engine Cross-Validation',
                subtitle:
                    'Compare secondary OCR outputs for higher precision.',
                value: config?.dualEngine ?? false,
                onChanged: (_) {},
              ),
              const SizedBox(height: 12),
              AppToggle(
                label: 'Self-Correction & Spelling Repair',
                subtitle:
                    'Run automated lexical sanity checks on detected blocks.',
                value: config?.selfCorrection ?? false,
                onChanged: (_) {},
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildTranslationVoiceTab(
      AppColorScheme colors, SettingsState settings) {
    final config = settings.runtimeConfig;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppCard(
          padding: AppCardPadding.lg,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionHeader(
                title: 'Translation & Transcription',
              ),
              const SizedBox(height: 8),
              AppToggle(
                label: 'Cross-Page Translation Context',
                subtitle:
                    'Maintain translation continuity across page boundaries.',
                value: config?.crossPage ?? false,
                onChanged: (_) {},
              ),
              const SizedBox(height: 12),
              AppInput(
                controller: _slidingWindowController,
                label: 'Sliding Window Words',
                placeholder: '32',
                helperText:
                    'Word count shared between adjacent translation segments',
                monospace: true,
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildSecurityAuthTab(
      AppColorScheme colors, SettingsState settings) {
    return const Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AppCard(
          padding: AppCardPadding.lg,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SectionHeader(
                title: 'Security & Auth',
              ),
              SizedBox(height: 12),
              AppBadge(
                label:
                    'Enforced server-side by OMNISCRIBE_AUTH_TOKEN — set the bearer under General & Server',
                variant: AppBadgeVariant.neutral,
              ),
            ],
          ),
        ),
      ],
    );
  }
}
