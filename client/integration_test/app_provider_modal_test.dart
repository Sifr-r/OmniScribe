import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/data/models/provider_preset.dart';
import 'package:omniscribe_client/data/providers/provider_notifier.dart';
import 'package:omniscribe_client/presentation/providers/provider_modal.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the Provider Modal: opening, listing providers,
/// selecting a preset, the search field, and dismissing.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('ProviderModal - open & list', () {
    testWidgets('opens from the tab ribbon provider pill', (tester) async {
      final ctx = await _boot(tester, providers: const []);
      addTearDown(ctx.stub.stop);

      // The pill text is `settings.activeProviderId.toUpperCase()` — for the
      // default 'openai' seed it's 'OPENAI'.
      await tester.tap(find.text('OPENAI'));
      await tester.pumpAndSettle();

      expect(find.byType(ProviderModal), findsOneWidget);
      expect(find.text('LLM Provider Browser'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets(
        'shows "No providers configured" when the mock repo returns an empty list',
        (tester) async {
      final ctx = await _boot(tester, providers: const []);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();

      await tester.tap(find.text('Browse providers'));
      // Avoid pumpAndSettle — the empty-state placeholder is fine, but the
      // modal's loading spinner is an indefinite animation.
      await tester.pump(const Duration(milliseconds: 500));

      // The list body shows an empty-state copy when providers.isEmpty.
      expect(find.byType(ProviderModal), findsOneWidget);
      expect(
        find.textContaining('No providers'),
        findsWidgets,
      );
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('lists every preset returned by the repository',
        (tester) async {
      final providers = [
        const ProviderPreset(
          id: 'openai',
          name: 'OpenAI',
          category: 'cloud',
          description: 'OpenAI inference',
          recommendedBaseUrl: 'https://api.openai.com',
          defaultModel: 'gpt-4o',
        ),
        const ProviderPreset(
          id: 'anthropic',
          name: 'Anthropic',
          category: 'cloud',
          description: 'Anthropic inference',
          recommendedBaseUrl: 'https://api.anthropic.com',
          defaultModel: 'claude-3-5-sonnet',
        ),
      ];

      final ctx = await _boot(tester, providers: providers);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();

      await tester.tap(find.text('Browse providers'));
      // Don't pumpAndSettle — the modal's loading spinner is an indefinite
      // animation. Pump just enough to let the provider list render.
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.text('OpenAI'), findsWidgets);
      expect(find.text('Anthropic'), findsWidgets);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });

  group('ProviderModal - search', () {
    testWidgets('typing into the search field filters the list',
        (tester) async {
      final providers = [
        const ProviderPreset(
          id: 'openai',
          name: 'OpenAI',
          category: 'cloud',
          description: 'OpenAI inference',
          recommendedBaseUrl: 'https://api.openai.com',
          defaultModel: 'gpt-4o',
        ),
        const ProviderPreset(
          id: 'anthropic',
          name: 'Anthropic',
          category: 'cloud',
          description: 'Anthropic inference',
          recommendedBaseUrl: 'https://api.anthropic.com',
          defaultModel: 'claude-3-5-sonnet',
        ),
      ];

      final ctx = await _boot(tester, providers: providers);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Browse providers'));
      await tester.pump(const Duration(milliseconds: 500));

      // Locate the search field by its placeholder.
      final searchField = find.byWidgetPredicate(
        (w) => w is TextField || w is EditableText,
      ).first;
      await tester.enterText(searchField, 'anth');
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.text('Anthropic'), findsWidgets);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });

  group('ProviderModal - dismiss', () {
    testWidgets('tapping outside the modal dismisses it',
        (tester) async {
      final ctx = await _boot(tester, providers: const []);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Browse providers'));
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.byType(ProviderModal), findsOneWidget);

      // barrierDismissible is true; tap the barrier above the modal to dismiss.
      await tester.tapAt(const Offset(20, 20));
      await tester.pumpAndSettle();

      expect(find.byType(ProviderModal), findsNothing);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });
}

class _BootResult {
  _BootResult({required this.container, required this.stub});
  final ProviderContainer container;
  final StubOmniscribeServer stub;
}

Future<_BootResult> _boot(
  WidgetTester tester, {
  required List<ProviderPreset> providers,
}) async {
  final stub = StubOmniscribeServer();
  await stub.start();

  final configRepo = MockConfigRepository();
  final providerRepo = MockProviderRepository();

  when(() => configRepo.getConfig()).thenAnswer(
    (_) async => defaultTestRuntimeConfig(),
  );
  when(() => configRepo.updateConfig(any())).thenAnswer(
    (_) async => defaultTestRuntimeConfig(),
  );
  when(
    () => configRepo.getModelsForProvider(
      any(),
      apiBase: any(named: 'apiBase'),
    ),
  ).thenAnswer((_) async => const ['deepseek-ocr-2']);
  when(() => providerRepo.getProviders()).thenAnswer((_) async => providers);

  final container = await bootAppForIntegration(
    tester,
    stub,
    configRepo: configRepo,
    providerRepo: providerRepo,
    extraOverrides: [
      serverHealthProvider.overrideWith(_OfflineHealth.new),
    ],
  );

  // Touch the provider browser so the modal's first fetch kicks off.
  container.read(providerBrowserProvider);

  return _BootResult(container: container, stub: stub);
}

class _OfflineHealth extends ServerHealthNotifier {
  @override
  ServerHealthState build() =>
      const ServerHealthState(status: ServerHealth.offline);
}
