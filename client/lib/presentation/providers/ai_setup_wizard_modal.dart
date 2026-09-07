import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/providers/provider_browser_state.dart';
import 'package:omniscribe_client/data/providers/provider_notifier.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_input.dart';
import 'package:omniscribe_client/presentation/common/app_modal.dart';
import 'package:omniscribe_client/presentation/common/app_select.dart';
import 'package:url_launcher/url_launcher.dart';

enum WizardStep {
  chooseMode,
  offlineSetup,
  cloudSetup,
  success,
}

enum OfflineEngineType {
  lmstudio('LM Studio', 'http://localhost:1234/v1', 'allenai/olmocr-2-7b'),
  ollama('Ollama', 'http://localhost:11434', 'qwen2.5-vl:7b');

  const OfflineEngineType(this.displayName, this.defaultBaseUrl, this.defaultModel);
  final String displayName;
  final String defaultBaseUrl;
  final String defaultModel;
}

class CloudProviderMeta {
  const CloudProviderMeta({
    required this.id,
    required this.name,
    required this.defaultBaseUrl,
    required this.defaultModel,
    required this.consoleUrl,
    required this.models,
  });

  final String id;
  final String name;
  final String defaultBaseUrl;
  final String defaultModel;
  final String consoleUrl;
  final List<String> models;
}

const List<CloudProviderMeta> kCloudProviders = [
  CloudProviderMeta(
    id: 'openai',
    name: 'OpenAI',
    defaultBaseUrl: 'https://api.openai.com/v1',
    defaultModel: 'gpt-4o',
    consoleUrl: 'https://platform.openai.com/api-keys',
    models: ['gpt-4o', 'gpt-4o-mini', 'o1', 'o3-mini'],
  ),
  CloudProviderMeta(
    id: 'gemini',
    name: 'Google Gemini',
    defaultBaseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    defaultModel: 'gemini-1.5-flash',
    consoleUrl: 'https://aistudio.google.com/app/apikey',
    models: ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-2.0-flash'],
  ),
  CloudProviderMeta(
    id: 'anthropic',
    name: 'Anthropic Claude',
    defaultBaseUrl: 'https://api.anthropic.com/v1',
    defaultModel: 'claude-3-5-sonnet-20241022',
    consoleUrl: 'https://console.anthropic.com/settings/keys',
    models: ['claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022'],
  ),
  CloudProviderMeta(
    id: 'groq',
    name: 'Groq',
    defaultBaseUrl: 'https://api.groq.com/openai/v1',
    defaultModel: 'llama-3.2-11b-vision-preview',
    consoleUrl: 'https://console.groq.com/keys',
    models: ['llama-3.2-11b-vision-preview', 'llama-3.2-90b-vision-preview'],
  ),
];

/// Friendly 3-step AI Setup Wizard for non-technical users.
class AISetupWizardModal extends ConsumerStatefulWidget {
  const AISetupWizardModal({
    super.key,
    this.onComplete,
  });

  final VoidCallback? onComplete;

  /// Display the wizard inside a standard modal dialog.
  static Future<void> show(BuildContext context, {VoidCallback? onComplete}) {
    return AppModal.show(
      context: context,
      title: 'AI Engine Setup Wizard',
      subtitle: 'Get your AI document scanner ready in just a few clicks',
      maxWidth: AppModalWidth.lg,
      content: AISetupWizardModal(onComplete: onComplete),
    );
  }

  @override
  ConsumerState<AISetupWizardModal> createState() => _AISetupWizardModalState();
}

class _AISetupWizardModalState extends ConsumerState<AISetupWizardModal> {
  WizardStep _step = WizardStep.chooseMode;

  // Offline state
  OfflineEngineType _selectedOfflineEngine = OfflineEngineType.lmstudio;
  late TextEditingController _offlineBaseController;
  late TextEditingController _offlineModelController;

  // Cloud state
  CloudProviderMeta _selectedCloudProvider = kCloudProviders.first;
  late TextEditingController _cloudKeyController;
  late TextEditingController _cloudBaseController;
  late TextEditingController _cloudModelController;

  // Connection testing state
  bool _isTesting = false;
  String? _testMessage;
  bool _testSuccess = false;
  bool _isApplying = false;
  bool _copiedCommand = false;

  @override
  void initState() {
    super.initState();
    _offlineBaseController =
        TextEditingController(text: _selectedOfflineEngine.defaultBaseUrl);
    _offlineModelController =
        TextEditingController(text: _selectedOfflineEngine.defaultModel);

    _cloudKeyController = TextEditingController();
    _cloudBaseController =
        TextEditingController(text: _selectedCloudProvider.defaultBaseUrl);
    _cloudModelController =
        TextEditingController(text: _selectedCloudProvider.defaultModel);
  }

  @override
  void dispose() {
    _offlineBaseController.dispose();
    _offlineModelController.dispose();
    _cloudKeyController.dispose();
    _cloudBaseController.dispose();
    _cloudModelController.dispose();
    super.dispose();
  }

  void _onSelectOfflineEngine(OfflineEngineType engine) {
    setState(() {
      _selectedOfflineEngine = engine;
      _offlineBaseController.text = engine.defaultBaseUrl;
      _offlineModelController.text = engine.defaultModel;
      _testMessage = null;
      _testSuccess = false;
      _copiedCommand = false;
    });
  }

  void _onSelectCloudProvider(CloudProviderMeta provider) {
    setState(() {
      _selectedCloudProvider = provider;
      _cloudBaseController.text = provider.defaultBaseUrl;
      _cloudModelController.text = provider.defaultModel;
      _testMessage = null;
      _testSuccess = false;
    });
  }

  Future<void> _testOfflineConnection() async {
    setState(() {
      _isTesting = true;
      _testMessage = null;
      _testSuccess = false;
    });

    final notifier = ref.read(providerBrowserProvider.notifier);
    final providerId = _selectedOfflineEngine == OfflineEngineType.lmstudio
        ? 'lmstudio'
        : 'ollama';

    final res = await notifier.validateProvider(
      providerId,
      _offlineBaseController.text.trim(),
      null,
      model: _offlineModelController.text.trim().isNotEmpty
          ? _offlineModelController.text.trim()
          : null,
    );

    if (mounted) {
      setState(() {
        _isTesting = false;
        _testSuccess = res.valid;
        if (res.valid) {
          if (res.models.isNotEmpty) {
            if (_offlineModelController.text ==
                    _selectedOfflineEngine.defaultModel &&
                !res.models.contains(_offlineModelController.text)) {
              _offlineModelController.text = res.models.first;
            }
          }
          _testMessage =
              'Success! Connected to ${_selectedOfflineEngine.displayName}. Found ${res.modelCount} ready model(s).';
        } else {
          _testMessage = res.error ??
              'Could not connect to ${_selectedOfflineEngine.displayName}. Make sure the local app is open and running.';
        }
      });
    }
  }

  Future<void> _testCloudConnection() async {
    final key = _cloudKeyController.text.trim();
    if (key.isEmpty) {
      setState(() {
        _testSuccess = false;
        _testMessage = 'Please enter your ${_selectedCloudProvider.name} API Key.';
      });
      return;
    }

    setState(() {
      _isTesting = true;
      _testMessage = null;
      _testSuccess = false;
    });

    final notifier = ref.read(providerBrowserProvider.notifier);
    final res = await notifier.validateProvider(
      _selectedCloudProvider.id,
      _cloudBaseController.text.trim(),
      key,
      model: _cloudModelController.text.trim().isNotEmpty
          ? _cloudModelController.text.trim()
          : null,
    );

    if (mounted) {
      setState(() {
        _isTesting = false;
        _testSuccess = res.valid;
        if (res.valid) {
          _testMessage =
              'Connected successfully to ${_selectedCloudProvider.name}!';
        } else {
          _testMessage = res.error ??
              'API Key validation failed. Please check that your key is active and has available quota.';
        }
      });
    }
  }

  Future<void> _finishWizardAndApply() async {
    setState(() => _isApplying = true);

    try {
      final isOffline = _step == WizardStep.offlineSetup ||
          (_step == WizardStep.success &&
              (_selectedOfflineEngine == OfflineEngineType.lmstudio ||
                  _selectedOfflineEngine == OfflineEngineType.ollama));

      final providerId = isOffline
          ? (_selectedOfflineEngine == OfflineEngineType.lmstudio
              ? 'lmstudio'
              : 'ollama')
          : _selectedCloudProvider.id;

      final apiBase = isOffline
          ? _offlineBaseController.text.trim()
          : _cloudBaseController.text.trim();

      final apiKey = isOffline
          ? null
          : (_cloudKeyController.text.trim().isNotEmpty
              ? _cloudKeyController.text.trim()
              : null);

      final model = isOffline
          ? _offlineModelController.text.trim()
          : _cloudModelController.text.trim();

      final notifier = ref.read(providerBrowserProvider.notifier);
      await notifier.setActiveProvider(
        providerId,
        apiBase,
        apiKey,
        model.isNotEmpty ? model : null,
      );

      await ref.read(settingsStateProvider.notifier).load();

      if (mounted) {
        widget.onComplete?.call();
        Navigator.of(context).pop();
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _testSuccess = false;
          _testMessage = 'Failed to apply AI engine configuration: $e';
        });
      }
    } finally {
      if (mounted) {
        setState(() => _isApplying = false);
      }
    }
  }

  void _copyOllamaCommand() {
    Clipboard.setData(
        const ClipboardData(text: 'ollama run qwen2.5-vl:7b'));
    setState(() => _copiedCommand = true);
    Future.delayed(const Duration(seconds: 3), () {
      if (mounted) setState(() => _copiedCommand = false);
    });
  }

  Future<void> _openExternalUrl(String url) async {
    final uri = Uri.parse(url);
    try {
      if (await canLaunchUrl(uri)) {
        await launchUrl(uri, mode: LaunchMode.externalApplication);
      } else {
        await Clipboard.setData(ClipboardData(text: url));
      }
    } catch (_) {
      await Clipboard.setData(ClipboardData(text: url));
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final browserState = ref.watch(providerBrowserProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        // Stepper Progress Header
        _buildStepper(colors),
        const SizedBox(height: 16),

        // Step Content Switcher
        switch (_step) {
          WizardStep.chooseMode => _buildStepChooseMode(colors),
          WizardStep.offlineSetup => _buildStepOfflineSetup(colors, browserState),
          WizardStep.cloudSetup => _buildStepCloudSetup(colors, browserState),
          WizardStep.success => _buildStepSuccess(colors),
        },
      ],
    );
  }

  Widget _buildStepper(AppColorScheme colors) {
    int activeIdx = 0;
    switch (_step) {
      case WizardStep.chooseMode:
        activeIdx = 0;
        break;
      case WizardStep.offlineSetup:
      case WizardStep.cloudSetup:
        activeIdx = 1;
        break;
      case WizardStep.success:
        activeIdx = 2;
        break;
    }

    final steps = ['Choose Mode', 'Configure Connection', 'Ready'];

    return Row(
      children: [
        for (int i = 0; i < steps.length; i++) ...[
          if (i > 0)
            Expanded(
              child: Container(
                height: 2,
                margin: const EdgeInsets.symmetric(horizontal: 8),
                color: i <= activeIdx
                    ? colors.brand
                    : colors.border,
              ),
            ),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 22,
                height: 22,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: i < activeIdx
                      ? colors.success
                      : i == activeIdx
                          ? colors.brand
                          : colors.cardRaised,
                  border: Border.all(
                    color: i <= activeIdx
                        ? Colors.transparent
                        : colors.border,
                    width: 1,
                  ),
                ),
                child: Center(
                  child: i < activeIdx
                      ? Icon(Icons.check, size: 12, color: colors.brandForeground)
                      : Text(
                          '${i + 1}',
                          style: AppTypography.micro(
                            color: i == activeIdx
                                ? colors.brandForeground
                                : colors.textMuted,
                          ).copyWith(fontWeight: FontWeight.w700),
                        ),
                ),
              ),
              const SizedBox(width: 6),
              Text(
                steps[i],
                style: AppTypography.micro(
                  color: i == activeIdx
                      ? colors.textPrimary
                      : colors.textMuted,
                ).copyWith(
                  fontWeight:
                      i == activeIdx ? FontWeight.w600 : FontWeight.w400,
                ),
              ),
            ],
          ),
        ],
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 1: CHOOSE DEPLOYMENT MODE
  // ---------------------------------------------------------------------------
  Widget _buildStepChooseMode(AppColorScheme colors) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'How would you like to run the AI engine?',
          style: AppTypography.titleMedium(
            color: colors.textPrimary,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          'Choose between running private local AI on your own computer or connecting to cloud models.',
          style: AppTypography.bodySmall(
            color: colors.textMuted,
          ),
        ),
        const SizedBox(height: 16),

        // Option 1: 100% Offline Local
        _ModeSelectionCard(
          icon: Icons.home_filled,
          title: 'Run 100% Offline (Free & Private)',
          badge: 'No API Keys Needed',
          badgeVariant: AppBadgeVariant.success,
          description:
              'Uses LM Studio or Ollama installed on your machine. Your documents never leave your computer, zero subscription fees, and works with no internet.',
          tags: const ['100% Private', 'Zero Cost', 'Offline Ready'],
          onTap: () {
            setState(() {
              _step = WizardStep.offlineSetup;
              _testMessage = null;
              _testSuccess = false;
            });
          },
        ),
        const SizedBox(height: 12),

        // Option 2: Cloud AI
        _ModeSelectionCard(
          icon: Icons.cloud_done_rounded,
          title: 'Use Cloud AI (Fast & Accurate)',
          badge: 'High Precision',
          badgeVariant: AppBadgeVariant.brand,
          description:
              'Connect directly to OpenAI (GPT-4o), Google Gemini, Claude, or Groq. Maximum recognition accuracy with no heavy hardware requirements.',
          tags: const ['Instant Setup', 'Highest Accuracy', 'No GPU needed'],
          onTap: () {
            setState(() {
              _step = WizardStep.cloudSetup;
              _testMessage = null;
              _testSuccess = false;
            });
          },
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 2A: OFFLINE ENGINE SETUP
  // ---------------------------------------------------------------------------
  Widget _buildStepOfflineSetup(
      AppColorScheme colors, ProviderBrowserState browserState) {
    final isOllama = _selectedOfflineEngine == OfflineEngineType.ollama;
    final providerId = isOllama ? 'ollama' : 'lmstudio';
    final discoveredModels = browserState.modelsMap[providerId];
    final offlineModels = (discoveredModels != null && discoveredModels.isNotEmpty)
        ? discoveredModels
        : <String>[_selectedOfflineEngine.defaultModel];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Back Link
        Row(
          children: [
            InkWell(
              onTap: () => setState(() => _step = WizardStep.chooseMode),
              child: Row(
                children: [
                  Icon(Icons.arrow_back_rounded, size: 14, color: colors.brand),
                  const SizedBox(width: 4),
                  Text(
                    'Back to mode selection',
                    style: AppTypography.bodySmall(color: colors.brand)
                        .copyWith(fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),

        Text(
          'Configure Offline Local AI',
          style: AppTypography.titleMedium(color: colors.textPrimary),
        ),
        const SizedBox(height: 4),
        Text(
          'Select your installed local AI runner and verify the connection:',
          style: AppTypography.bodySmall(color: colors.textMuted),
        ),
        const SizedBox(height: 14),

        // Local Engine Selector (LM Studio vs Ollama)
        Row(
          children: [
            Expanded(
              child: _EngineTabButton(
                title: 'LM Studio',
                subtitle: 'Port 1234',
                icon: Icons.desktop_windows_rounded,
                isSelected: _selectedOfflineEngine == OfflineEngineType.lmstudio,
                onTap: () => _onSelectOfflineEngine(OfflineEngineType.lmstudio),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _EngineTabButton(
                title: 'Ollama',
                subtitle: 'Port 11434',
                icon: Icons.terminal_rounded,
                isSelected: _selectedOfflineEngine == OfflineEngineType.ollama,
                onTap: () => _onSelectOfflineEngine(OfflineEngineType.ollama),
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),

        // Guided Helper Box
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: colors.cardRaised,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: colors.border),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.info_outline_rounded,
                      size: 16, color: colors.brand),
                  const SizedBox(width: 6),
                  Text(
                    isOllama
                        ? 'Quick Ollama Model Setup'
                        : 'LM Studio Setup Guide',
                    style: AppTypography.labelMedium(
                      color: colors.textPrimary,
                    ).copyWith(fontWeight: FontWeight.w600),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              if (isOllama) ...[
                Text(
                  'Run this command in your terminal to download and start a high-performance vision OCR model:',
                  style: AppTypography.bodySmall(color: colors.textMuted),
                ),
                const SizedBox(height: 8),
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 10, vertical: 8),
                  decoration: BoxDecoration(
                    color: colors.card,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: colors.borderStrong),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          'ollama run qwen2.5-vl:7b',
                          style: AppTypography.code(color: colors.brand),
                        ),
                      ),
                      InkWell(
                        onTap: _copyOllamaCommand,
                        borderRadius: BorderRadius.circular(4),
                        child: Padding(
                          padding: const EdgeInsets.all(4),
                          child: Row(
                            children: [
                              Icon(
                                _copiedCommand
                                    ? Icons.check_rounded
                                    : Icons.copy_rounded,
                                size: 14,
                                color: _copiedCommand
                                    ? colors.success
                                    : colors.textSecondary,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                _copiedCommand ? 'Copied!' : 'Copy',
                                style: AppTypography.micro(
                                  color: _copiedCommand
                                      ? colors.success
                                      : colors.textSecondary,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ] else ...[
                Text(
                  '1. Open LM Studio on your computer.\n'
                  '2. Search for and download "Qwen2.5-VL-7B" or "OlmOCR-2-7B".\n'
                  '3. Navigate to Local Server tab and click "Start Server" (Port 1234).',
                  style: AppTypography.bodySmall(color: colors.textMuted),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),

        // Endpoint & Model Inputs
        AppInput(
          controller: _offlineBaseController,
          label: 'Endpoint URL',
          placeholder: _selectedOfflineEngine.defaultBaseUrl,
          monospace: true,
        ),
        const SizedBox(height: 10),

        // Model Selector
        Row(
          children: [
            Expanded(
              child: AppInput(
                controller: _offlineModelController,
                label: 'Model Name / ID',
                placeholder: _selectedOfflineEngine.defaultModel,
                monospace: true,
              ),
            ),
            const SizedBox(width: 8),
            Padding(
              padding: const EdgeInsets.only(top: 22),
              child: PopupMenuButton<String>(
                tooltip: 'Select discovered model',
                itemBuilder: (context) => offlineModels
                    .map((m) => PopupMenuItem(
                          value: m,
                          child: Text(m,
                              style: AppTypography.codeSmall(
                                  color: colors.textPrimary)),
                        ))
                    .toList(),
                onSelected: (m) =>
                    setState(() => _offlineModelController.text = m),
                child: Container(
                  height: 38,
                  padding: const EdgeInsets.symmetric(horizontal: 10),
                  decoration: BoxDecoration(
                    color: colors.cardRaised,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: colors.border),
                  ),
                  child: Row(
                    children: [
                      Text(
                        'Pick',
                        style: AppTypography.labelMedium(color: colors.brand),
                      ),
                      const SizedBox(width: 4),
                      Icon(Icons.arrow_drop_down,
                          size: 16, color: colors.brand),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),

        // Test Connection Feedback Banner
        if (_testMessage != null) ...[
          _FeedbackBanner(
            success: _testSuccess,
            message: _testMessage!,
          ),
          const SizedBox(height: 12),
        ],

        // Action Buttons
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            AppButton(
              text: 'Test Connection',
              variant: AppButtonVariant.secondary,
              icon: const Icon(Icons.bolt_rounded, size: 14),
              loading: _isTesting,
              onPressed: _testOfflineConnection,
            ),
            AppButton(
              text: 'Continue',
              variant: AppButtonVariant.primary,
              icon: const Icon(Icons.arrow_forward_rounded, size: 14),
              onPressed: () {
                setState(() => _step = WizardStep.success);
              },
            ),
          ],
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 2B: CLOUD ENGINE SETUP
  // ---------------------------------------------------------------------------
  Widget _buildStepCloudSetup(
      AppColorScheme colors, ProviderBrowserState browserState) {
    final p = _selectedCloudProvider;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Back Link
        Row(
          children: [
            InkWell(
              onTap: () => setState(() => _step = WizardStep.chooseMode),
              child: Row(
                children: [
                  Icon(Icons.arrow_back_rounded, size: 14, color: colors.brand),
                  const SizedBox(width: 4),
                  Text(
                    'Back to mode selection',
                    style: AppTypography.bodySmall(color: colors.brand)
                        .copyWith(fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),

        Text(
          'Connect Cloud AI Provider',
          style: AppTypography.titleMedium(color: colors.textPrimary),
        ),
        const SizedBox(height: 4),
        Text(
          'Select your cloud AI provider and enter your API key:',
          style: AppTypography.bodySmall(color: colors.textMuted),
        ),
        const SizedBox(height: 14),

        // Provider Selector
        AppSelect<CloudProviderMeta>(
          label: 'AI Provider',
          value: _selectedCloudProvider,
          items: kCloudProviders
              .map((cp) => AppSelectItem(
                    value: cp,
                    label: cp.name,
                    subtitle: 'Default: ${cp.defaultModel}',
                  ))
              .toList(),
          onChanged: (val) {
            if (val != null) _onSelectCloudProvider(val);
          },
        ),
        const SizedBox(height: 12),

        // API Key Help Box with Link
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: colors.cardRaised,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: colors.border),
          ),
          child: Row(
            children: [
              Icon(Icons.key_rounded, size: 18, color: colors.brand),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Need an API key?',
                      style: AppTypography.labelMedium(
                        color: colors.textPrimary,
                      ).copyWith(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'Get your API key directly from the official ${p.name} dashboard.',
                      style: AppTypography.bodySmall(color: colors.textMuted),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              AppButton(
                text: 'Open Console',
                variant: AppButtonVariant.outline,
                size: AppButtonSize.sm,
                icon: const Icon(Icons.open_in_new_rounded, size: 12),
                onPressed: () => _openExternalUrl(p.consoleUrl),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),

        // API Key Field
        AppInput(
          controller: _cloudKeyController,
          label: '${p.name} API Key',
          placeholder: 'Paste your secret key (sk-...)',
          obscureText: true,
          monospace: true,
          isRequired: true,
        ),
        const SizedBox(height: 10),

        // Model Selector
        Row(
          children: [
            Expanded(
              child: AppInput(
                controller: _cloudModelController,
                label: 'Model ID',
                placeholder: p.defaultModel,
                monospace: true,
              ),
            ),
            const SizedBox(width: 8),
            Padding(
              padding: const EdgeInsets.only(top: 22),
              child: PopupMenuButton<String>(
                tooltip: 'Select popular model',
                itemBuilder: (context) => p.models
                    .map((m) => PopupMenuItem(
                          value: m,
                          child: Text(m, style: AppTypography.codeSmall(color: colors.textPrimary)),
                        ))
                    .toList(),
                onSelected: (m) => setState(() => _cloudModelController.text = m),
                child: Container(
                  height: 38,
                  padding: const EdgeInsets.symmetric(horizontal: 10),
                  decoration: BoxDecoration(
                    color: colors.cardRaised,
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: colors.border),
                  ),
                  child: Row(
                    children: [
                      Text(
                        'Pick',
                        style: AppTypography.labelMedium(color: colors.brand),
                      ),
                      const SizedBox(width: 4),
                      Icon(Icons.arrow_drop_down, size: 16, color: colors.brand),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),

        // Test Connection Feedback Banner
        if (_testMessage != null) ...[
          _FeedbackBanner(
            success: _testSuccess,
            message: _testMessage!,
          ),
          const SizedBox(height: 12),
        ],

        // Action Buttons
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            AppButton(
              text: 'Test Connection',
              variant: AppButtonVariant.secondary,
              icon: const Icon(Icons.bolt_rounded, size: 14),
              loading: _isTesting,
              onPressed: _testCloudConnection,
            ),
            AppButton(
              text: 'Continue',
              variant: AppButtonVariant.primary,
              icon: const Icon(Icons.arrow_forward_rounded, size: 14),
              onPressed: () {
                if (_cloudKeyController.text.trim().isEmpty) {
                  setState(() {
                    _testSuccess = false;
                    _testMessage =
                        'Please enter your API Key to continue.';
                  });
                  return;
                }
                setState(() => _step = WizardStep.success);
              },
            ),
          ],
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 3: SUCCESS & CONFIRMATION
  // ---------------------------------------------------------------------------
  Widget _buildStepSuccess(AppColorScheme colors) {
    final isOffline = _selectedOfflineEngine == OfflineEngineType.lmstudio ||
        _selectedOfflineEngine == OfflineEngineType.ollama;

    final engineName = isOffline
        ? _selectedOfflineEngine.displayName
        : _selectedCloudProvider.name;

    final endpoint = isOffline
        ? _offlineBaseController.text.trim()
        : _cloudBaseController.text.trim();

    final model = isOffline
        ? _offlineModelController.text.trim()
        : _cloudModelController.text.trim();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Success Graphic
        Center(
          child: Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: colors.success.withValues(alpha: 0.15),
              border: Border.all(
                color: colors.success.withValues(alpha: 0.4),
                width: 2,
              ),
            ),
            child: Center(
              child: Icon(
                Icons.check_circle_outline_rounded,
                size: 32,
                color: colors.success,
              ),
            ),
          ),
        ),
        const SizedBox(height: 12),

        Center(
          child: Text(
            'All set! Your AI Engine is ready.',
            style: AppTypography.titleLarge(color: colors.textPrimary),
          ),
        ),
        const SizedBox(height: 4),
        Center(
          child: Text(
            'OmniScribe is configured to process and analyze documents with your engine.',
            textAlign: TextAlign.center,
            style: AppTypography.bodySmall(color: colors.textMuted),
          ),
        ),
        const SizedBox(height: 16),

        // Summary Card
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: colors.cardRaised,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: colors.border),
          ),
          child: Column(
            children: [
              _SummaryRow(
                label: 'Deployment Mode',
                value: isOffline ? '100% Offline (Local)' : 'Cloud AI Provider',
                badge: isOffline ? 'Private' : 'Cloud',
                badgeVariant: isOffline
                    ? AppBadgeVariant.success
                    : AppBadgeVariant.brand,
              ),
              const Divider(height: 16),
              _SummaryRow(
                label: 'Provider Engine',
                value: engineName,
              ),
              const Divider(height: 16),
              _SummaryRow(
                label: 'Active Model',
                value: model.isNotEmpty ? model : '(Default)',
                monospace: true,
              ),
              const Divider(height: 16),
              _SummaryRow(
                label: 'API Base URL',
                value: endpoint,
                monospace: true,
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),

        // Primary Final Action CTA
        AppButton(
          text: 'Start Processing',
          variant: AppButtonVariant.primary,
          size: AppButtonSize.lg,
          fullWidth: true,
          icon: const Icon(Icons.bolt_rounded, size: 18),
          loading: _isApplying,
          onPressed: _finishWizardAndApply,
        ),
      ],
    );
  }
}

// -----------------------------------------------------------------------------
// HELPER SUB-WIDGETS
// -----------------------------------------------------------------------------

class _ModeSelectionCard extends StatefulWidget {
  const _ModeSelectionCard({
    required this.icon,
    required this.title,
    required this.badge,
    required this.badgeVariant,
    required this.description,
    required this.tags,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String badge;
  final AppBadgeVariant badgeVariant;
  final String description;
  final List<String> tags;
  final VoidCallback onTap;

  @override
  State<_ModeSelectionCard> createState() => _ModeSelectionCardState();
}

class _ModeSelectionCardState extends State<_ModeSelectionCard> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: _isHovered ? colors.cardRaised : colors.card,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(
              color: _isHovered ? colors.brand : colors.border,
              width: _isHovered ? 1.5 : 1.0,
            ),
            boxShadow: _isHovered
                ? [
                    BoxShadow(
                      color: colors.brand.withValues(alpha: 0.15),
                      blurRadius: 12,
                      offset: const Offset(0, 4),
                    ),
                  ]
                : null,
          ),
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
                          width: 32,
                          height: 32,
                          decoration: BoxDecoration(
                            color: colors.brand.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Center(
                            child: Icon(widget.icon, size: 18, color: colors.brand),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Flexible(
                          child: Text(
                            widget.title,
                            overflow: TextOverflow.ellipsis,
                            style: AppTypography.titleSmall(
                              color: colors.textPrimary,
                            ).copyWith(fontWeight: FontWeight.w700),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 6),
                  AppBadge(
                    label: widget.badge,
                    variant: widget.badgeVariant,
                    size: AppBadgeSize.sm,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                widget.description,
                style: AppTypography.bodySmall(color: colors.textMuted),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: widget.tags
                    .map(
                      (t) => Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(
                          color: colors.cardRaised,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: colors.border),
                        ),
                        child: Text(
                          t,
                          style: AppTypography.micro(color: colors.textSecondary),
                        ),
                      ),
                    )
                    .toList(),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _EngineTabButton extends StatelessWidget {
  const _EngineTabButton({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.isSelected,
    required this.onTap,
  });

  final String title;
  final String subtitle;
  final IconData icon;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: isSelected
              ? colors.brand.withValues(alpha: 0.12)
              : colors.cardRaised,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: isSelected ? colors.brand : colors.border,
            width: isSelected ? 1.5 : 1.0,
          ),
        ),
        child: Row(
          children: [
            Icon(
              icon,
              size: 20,
              color: isSelected ? colors.brand : colors.textMuted,
            ),
            const SizedBox(width: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: AppTypography.labelMedium(
                    color: isSelected ? colors.brand : colors.textPrimary,
                  ).copyWith(fontWeight: FontWeight.w600),
                ),
                Text(
                  subtitle,
                  style: AppTypography.micro(color: colors.textMuted),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _FeedbackBanner extends StatelessWidget {
  const _FeedbackBanner({
    required this.success,
    required this.message,
  });

  final bool success;
  final String message;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: success
            ? colors.success.withValues(alpha: 0.12)
            : colors.error.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(
          color: success
              ? colors.success.withValues(alpha: 0.35)
              : colors.error.withValues(alpha: 0.35),
        ),
      ),
      child: Row(
        children: [
          Icon(
            success ? Icons.check_circle_outline : Icons.error_outline,
            size: 16,
            color: success ? colors.success : colors.error,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: AppTypography.bodySmall(
                color: success ? colors.success : colors.error,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SummaryRow extends StatelessWidget {
  const _SummaryRow({
    required this.label,
    required this.value,
    this.badge,
    this.badgeVariant = AppBadgeVariant.neutral,
    this.monospace = false,
  });

  final String label;
  final String value;
  final String? badge;
  final AppBadgeVariant badgeVariant;
  final bool monospace;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;

    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          label,
          style: AppTypography.bodySmall(color: colors.textMuted),
        ),
        Row(
          children: [
            if (badge != null) ...[
              AppBadge(
                label: badge!,
                variant: badgeVariant,
                size: AppBadgeSize.sm,
              ),
              const SizedBox(width: 6),
            ],
            Text(
              value,
              style: monospace
                  ? AppTypography.codeSmall(color: colors.textPrimary)
                  : AppTypography.labelMedium(color: colors.textPrimary)
                      .copyWith(fontWeight: FontWeight.w600),
            ),
          ],
        ),
      ],
    );
  }
}
