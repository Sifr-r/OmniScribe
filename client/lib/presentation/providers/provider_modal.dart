import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/provider_preset.dart';
import 'package:omniscribe_client/data/providers/provider_browser_state.dart';
import 'package:omniscribe_client/data/providers/provider_notifier.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'package:omniscribe_client/presentation/common/app_input.dart';
import 'package:omniscribe_client/presentation/common/app_modal.dart';
import 'provider_card.dart';

class ProviderModal extends ConsumerStatefulWidget {
  const ProviderModal({
    super.key,
    this.initialProvider,
    this.targetNamespace = 'ocr',
    this.onApplied,
  });

  final ProviderPreset? initialProvider;
  final String targetNamespace;
  final VoidCallback? onApplied;

  static Future<void> show(
    BuildContext context, {
    ProviderPreset? initialProvider,
    String targetNamespace = 'ocr',
    VoidCallback? onApplied,
  }) {
    return AppModal.show(
      context: context,
      title: 'LLM Provider Browser',
      subtitle:
          'Select and configure AI model endpoints for $targetNamespace processing',
      maxWidth: AppModalWidth.xl,
      content: ProviderModal(
        initialProvider: initialProvider,
        targetNamespace: targetNamespace,
        onApplied: onApplied,
      ),
    );
  }

  @override
  ConsumerState<ProviderModal> createState() => _ProviderModalState();
}

class _ProviderModalState extends ConsumerState<ProviderModal> {
  ProviderPreset? _selectedProvider;
  late TextEditingController _apiBaseController;
  late TextEditingController _apiKeyController;
  late TextEditingController _modelController;
  late TextEditingController _searchController;
  String? _testMessage;
  bool _testSuccess = false;
  bool _isTesting = false;
  bool _isSaving = false;

  @override
  void initState() {
    super.initState();
    _selectedProvider = widget.initialProvider;
    _apiBaseController = TextEditingController();
    _apiKeyController = TextEditingController();
    _modelController = TextEditingController();
    _searchController = TextEditingController();

    if (_selectedProvider != null) {
      _initFormForProvider(_selectedProvider!);
    }

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        ref.read(providerBrowserProvider.notifier).fetchProviders();
      }
    });
  }

  void _initFormForProvider(ProviderPreset provider) {
    _apiBaseController.text = provider.apiBase ?? provider.recommendedBaseUrl;
    _apiKeyController.text = '';
    final cachedModels =
        ref.read(providerBrowserProvider).modelsMap[provider.id];
    final models = (cachedModels != null && cachedModels.isNotEmpty)
        ? cachedModels
        : provider.models;
    _modelController.text = provider.defaultModel.isNotEmpty
        ? provider.defaultModel
        : (models.isNotEmpty ? models.first : '');
    _testMessage = null;
    _testSuccess = false;
  }

  @override
  void dispose() {
    _apiBaseController.dispose();
    _apiKeyController.dispose();
    _modelController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _runConnectionTest() async {
    if (_selectedProvider == null) return;
    setState(() {
      _isTesting = true;
      _testMessage = null;
    });

    final notifier = ref.read(providerBrowserProvider.notifier);
    final res = await notifier.validateProvider(
      _selectedProvider!.id,
      _apiBaseController.text.trim(),
      _apiKeyController.text.trim().isNotEmpty
          ? _apiKeyController.text.trim()
          : null,
      model: _modelController.text.trim().isNotEmpty
          ? _modelController.text.trim()
          : null,
    );

    if (mounted) {
      setState(() {
        _isTesting = false;
        _testSuccess = res.valid;
        _testMessage = res.valid
            ? 'Endpoint reachable! Found ${res.modelCount} models.'
            : (res.error ?? 'Validation failed');

        if (res.valid) {
          final state = ref.read(providerBrowserProvider);
          final discoveredModels = res.models.isNotEmpty
              ? res.models
              : (state.modelsMap[_selectedProvider!.id] ??
                  _selectedProvider!.models);

          final current = _modelController.text.trim();
          if (current.isEmpty ||
              (discoveredModels.isNotEmpty &&
                  !discoveredModels.contains(current))) {
            if (res.models.isNotEmpty) {
              _modelController.text = res.models.first;
            } else if (_selectedProvider!.defaultModel.isNotEmpty) {
              _modelController.text = _selectedProvider!.defaultModel;
            } else if (discoveredModels.isNotEmpty) {
              _modelController.text = discoveredModels.first;
            }
          }
        }
      });
    }
  }

  Future<void> _applyProvider() async {
    if (_selectedProvider == null) return;
    setState(() {
      _isSaving = true;
    });

    try {
      final notifier = ref.read(providerBrowserProvider.notifier);
      final modelText = _modelController.text.trim();
      await notifier.setActiveProvider(
        _selectedProvider!.id,
        _apiBaseController.text.trim(),
        _apiKeyController.text.trim().isNotEmpty
            ? _apiKeyController.text.trim()
            : null,
        modelText.isNotEmpty ? modelText : null,
      );
      await ref.read(settingsStateProvider.notifier).load();

      if (mounted) {
        widget.onApplied?.call();
        Navigator.of(context).pop();
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _testSuccess = false;
          _testMessage = 'Failed to apply provider: $e';
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _isSaving = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(providerBrowserProvider);
    final config = ref.watch(settingsStateProvider);
    final colors = context.colors;

    if (_selectedProvider != null) {
      return _buildConnectForm(colors, state);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        // Search bar
        AppInput(
          controller: _searchController,
          placeholder:
              'Search providers (e.g. OpenAI, Anthropic, Ollama, LM Studio...)',
          prefixIcon: Icon(Icons.search, size: 16, color: colors.textMuted),
          onChanged: (q) =>
              ref.read(providerBrowserProvider.notifier).setSearchQuery(q),
        ),
        const SizedBox(height: 16),

        if (state.isFetching && state.providers.isEmpty) ...[
          Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: CircularProgressIndicator(
                valueColor: AlwaysStoppedAnimation<Color>(colors.brand),
              ),
            ),
          ),
        ] else if (state.error != null && state.providers.isEmpty) ...[
          Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Could not load the provider catalog.',
                    style: AppTypography.bodySmall(
                      color: colors.textPrimary,
                    ).copyWith(fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    state.error!,
                    textAlign: TextAlign.center,
                    style: AppTypography.codeSmall(color: colors.textMuted),
                  ),
                  const SizedBox(height: 16),
                  AppButton(
                    text: 'Retry',
                    variant: AppButtonVariant.secondary,
                    onPressed: () => ref
                        .read(providerBrowserProvider.notifier)
                        .fetchProviders(),
                  ),
                ],
              ),
            ),
          ),
        ] else if (state.filteredProviders.isEmpty) ...[
          Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Text(
                _searchController.text.trim().isEmpty
                    ? 'No providers available.'
                    : 'No providers found matching "${_searchController.text}".',
                style: AppTypography.bodySmall(color: colors.textMuted),
              ),
            ),
          ),
        ] else ...[
          // Popular Section
          if (state.popularProviders.isNotEmpty) ...[
            Text(
              'POPULAR PROVIDERS',
              style: AppTypography.micro(
                color: colors.textMuted,
              ),
            ),
            const SizedBox(height: 8),
            ListView.separated(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: state.popularProviders.length,
              separatorBuilder: (_, __) => const SizedBox(height: 8),
              itemBuilder: (context, index) {
                final p = state.popularProviders[index];
                final models = state.modelsMap[p.id] ?? const [];
                final isLoading = state.loadingModelIds.contains(p.id);
                final isActive = config.activeProviderId == p.id;

                return ProviderCard(
                  provider: p,
                  models: models,
                  isLoadingModels: isLoading,
                  isActive: isActive,
                  onConnect: () {
                    setState(() {
                      _selectedProvider = p;
                      _initFormForProvider(p);
                    });
                  },
                  onUseModel: (model) async {
                    _selectedProvider = p;
                    _initFormForProvider(p);
                    _modelController.text = model;
                    await _applyProvider();
                  },
                  onRefreshModels: () => ref
                      .read(providerBrowserProvider.notifier)
                      .fetchModelsForProvider(p.id),
                );
              },
            ),
            const SizedBox(height: 16),
          ],

          // Other / Local / Custom Section
          if (state.otherProviders.isNotEmpty) ...[
            Text(
              'LOCAL & CLOUD PROVIDERS',
              style: AppTypography.micro(
                color: colors.textMuted,
              ),
            ),
            const SizedBox(height: 8),
            ListView.separated(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: state.otherProviders.length,
              separatorBuilder: (_, __) => const SizedBox(height: 8),
              itemBuilder: (context, index) {
                final p = state.otherProviders[index];
                final models = state.modelsMap[p.id] ?? const [];
                final isLoading = state.loadingModelIds.contains(p.id);
                final isActive = config.activeProviderId == p.id;

                return ProviderCard(
                  provider: p,
                  models: models,
                  isLoadingModels: isLoading,
                  isActive: isActive,
                  onConnect: () {
                    setState(() {
                      _selectedProvider = p;
                      _initFormForProvider(p);
                    });
                  },
                  onUseModel: (model) async {
                    _selectedProvider = p;
                    _initFormForProvider(p);
                    _modelController.text = model;
                    await _applyProvider();
                  },
                  onRefreshModels: () => ref
                      .read(providerBrowserProvider.notifier)
                      .fetchModelsForProvider(p.id),
                );
              },
            ),
          ],
        ],
      ],
    );
  }

  Widget _buildConnectForm(
      AppColorScheme colors, ProviderBrowserState state) {
    final p = _selectedProvider!;
    final cachedModels = state.modelsMap[p.id];
    final availableModels = (cachedModels != null && cachedModels.isNotEmpty)
        ? cachedModels
        : p.models;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        // Back link + Provider Header
        Row(
          children: [
            InkWell(
              onTap: () {
                setState(() {
                  _selectedProvider = null;
                  _testMessage = null;
                });
              },
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.arrow_back, size: 14, color: colors.brand),
                  const SizedBox(width: 4),
                  Text(
                    'All providers',
                    style: AppTypography.bodySmall(
                      color: colors.brand,
                    ).copyWith(fontWeight: FontWeight.w600),
                  ),
                ],
              ),
            ),
            const Spacer(),
            if (p.isRecommended ?? false)
              const AppBadge(
                label: 'Recommended',
                variant: AppBadgeVariant.info,
              ),
          ],
        ),
        const SizedBox(height: 12),

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
              Text(
                'Configuring ${p.name}',
                style: AppTypography.bodyLarge(
                  color: colors.textPrimary,
                ).copyWith(fontWeight: FontWeight.w600),
              ),
              if (p.description.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  p.description,
                  style: AppTypography.bodySmall(color: colors.textMuted),
                ),
              ],
              if (p.notes.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  p.notes,
                  style: AppTypography.codeSmall(color: colors.textMuted),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 16),

        // Fields
        AppInput(
          controller: _apiBaseController,
          label: 'API Base URL',
          placeholder: p.recommendedBaseUrl.isNotEmpty
              ? p.recommendedBaseUrl
              : 'http://localhost:1234/v1',
          helperText: 'The OpenAI-compatible endpoint URL',
          monospace: true,
        ),
        const SizedBox(height: 12),

        AppInput(
          controller: _apiKeyController,
          label: 'API Key',
          placeholder:
              p.requiresKey ? 'Enter API Key...' : 'Optional / Not required',
          obscureText: true,
          helperText: p.envKeys.isNotEmpty
              ? 'Environment variable: ${p.envKeys.join(", ")}'
              : null,
          monospace: true,
        ),
        const SizedBox(height: 12),

        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'Model ID',
                  style: AppTypography.labelMedium(
                    color: colors.textMuted,
                  ).copyWith(fontWeight: FontWeight.w600),
                ),
                if (availableModels.isNotEmpty)
                  PopupMenuButton<String>(
                    tooltip: 'Pick from discovered models',
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                          vertical: 2, horizontal: 4),
                      child: Text(
                        'Select model (${availableModels.length})',
                        style: AppTypography.bodySmall(
                          color: colors.brand,
                        ).copyWith(fontWeight: FontWeight.w500),
                      ),
                    ),
                    itemBuilder: (context) => availableModels
                        .map(
                          (m) => PopupMenuItem<String>(
                            value: m,
                            child: Text(m,
                                style: AppTypography.codeSmall(
                                  color: colors.textPrimary,
                                )),
                          ),
                        )
                        .toList(),
                    onSelected: (model) {
                      _modelController.text = model;
                    },
                  ),
              ],
            ),
            const SizedBox(height: 6),
            AppInput(
              controller: _modelController,
              placeholder:
                  'e.g. allenai/olmocr-2-7b, gpt-4o, claude-3-5-sonnet',
              monospace: true,
            ),
          ],
        ),
        const SizedBox(height: 14),

        // Connection Test Result Badge / Box
        if (_testMessage != null) ...[
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: _testSuccess
                  ? colors.success.withValues(alpha: 0.12)
                  : colors.error.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(
                color: _testSuccess
                    ? colors.success.withValues(alpha: 0.35)
                    : colors.error.withValues(alpha: 0.35),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  _testSuccess
                      ? Icons.check_circle_outline
                      : Icons.error_outline,
                  size: 16,
                  color: _testSuccess ? colors.success : colors.error,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _testMessage!,
                    style: AppTypography.codeSmall(
                      color: _testSuccess ? colors.success : colors.error,
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 14),
        ],

        // Action Buttons
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            AppButton(
              text: 'Test connection',
              variant: AppButtonVariant.secondary,
              loading: _isTesting,
              onPressed: _runConnectionTest,
              icon: const Icon(Icons.bolt, size: 14),
            ),
            Row(
              children: [
                AppButton(
                  text: 'Cancel',
                  variant: AppButtonVariant.ghost,
                  onPressed: () {
                    setState(() {
                      _selectedProvider = null;
                    });
                  },
                ),
                const SizedBox(width: 8),
                AppButton(
                  text: 'Apply as active',
                  variant: AppButtonVariant.primary,
                  loading: _isSaving,
                  onPressed: _applyProvider,
                ),
              ],
            ),
          ],
        ),
      ],
    );
  }
}
