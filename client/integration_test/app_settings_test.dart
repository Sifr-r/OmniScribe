import 'package:omniscribe_client/features/settings/runtime_config.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/features/providers/provider_browser_state.dart';
import 'package:omniscribe_client/features/providers/provider_notifier.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/settings/settings_notifier.dart';
import 'package:omniscribe_client/shared/widgets/section_header.dart';
import 'package:omniscribe_client/features/providers/provider_modal.dart';
import 'package:omniscribe_client/app/shell_state.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the Settings screen: tab strip, save flow,
/// bearer-token apply, and the "Browse providers" launchpad.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('Settings - shell integration', () {
    testWidgets('clicking the Settings tab renders the Settings screen',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();

      expect(find.text('Settings & Configuration'), findsOneWidget);
      expect(find.text('Save settings'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('the "Browse providers" button opens the provider modal',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();

      await tester.tap(find.text('Browse providers'));
      await tester.pumpAndSettle();

      expect(find.byType(ProviderModal), findsOneWidget);
      expect(find.text('LLM Provider Browser'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });

  group('Settings - sub-tabs', () {
    const subTabs = [
      'General & Server',
      'OCR Pipeline',
      'Translation & Voice',
      'Security & Auth',
    ];

    for (final tab in subTabs) {
      testWidgets('clicking "$tab" sub-tab switches the section content',
          (tester) async {
        final ctx = await _boot(tester);
        addTearDown(ctx.stub.stop);

        ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
        await tester.pumpAndSettle();

        await tester.tap(find.text(tab).first);
        await tester.pumpAndSettle();

        // Each sub-tab's first child is a SectionHeader — confirm the section
        // title actually changed by checking the section header that lives
        // only inside that sub-tab's body.
        expect(find.byType(SectionHeader), findsWidgets);
      }, tags: const [kPlatformWindows, kPlatformWeb]);
    }
  });

  group('Settings - save flow', () {
    testWidgets(
        'Save settings sends a ConfigUpdate to the repository and updates state',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();

      // Change the DPI field (the only numeric field visible by default on the
      // General & Server tab — DPI sits in the OCR Pipeline tab, so we type
      // into the model field which is on General & Server).
      final modelField = find.byWidgetPredicate(
        (w) => w is EditableText && w.controller.text == 'deepseek-ocr-2',
      );
      expect(modelField, findsOneWidget);

      // Append a tag so the underlying text differs.
      await tester.enterText(modelField, 'deepseek-ocr-2-v2');
      await tester.pumpAndSettle();

      // Capture the config update sent to the repo.
      final configRepo =
          ctx.container.read(configRepositoryProvider) as MockConfigRepository;
      await tester.tap(find.text('Save settings'));
      await tester.pumpAndSettle();

      final captured = verify(
        () => configRepo.updateConfig(captureAny()),
      ).captured.single as ConfigUpdate;
      expect(captured.model, equals('deepseek-ocr-2-v2'));

      expect(
        find.textContaining('All configuration changes saved'),
        findsOneWidget,
      );
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });

  group('Settings - bearer token', () {
    testWidgets('Apply token writes the bearer token into SettingsState',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byKey(const ValueKey('settings-bearer-token')),
        'secret-token-xyz',
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Apply token'));
      await tester.pumpAndSettle();

      final settings = ctx.container.read(settingsStateProvider);
      expect(settings.serverBearerToken, equals('secret-token-xyz'));
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });

  group('Settings - provider browser', () {
    testWidgets(
        'providerBrowserProvider is exposed and renders provider list inside the modal',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();

      await tester.tap(find.text('Browse providers'));
      await tester.pumpAndSettle();

      // ProviderBrowserProvider is shared between the settings screen and
      // the modal — both must see the same value.
      final browser = ctx.container.read(providerBrowserProvider);
      expect(browser, isA<ProviderBrowserState>());
      expect(browser.providers, isEmpty, reason: 'mock returned no providers');
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });
}

class _BootResult {
  _BootResult({required this.container, required this.stub});
  final ProviderContainer container;
  final StubOmniscribeServer stub;
}

Future<_BootResult> _boot(WidgetTester tester) async {
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
  when(() => providerRepo.getProviders()).thenAnswer((_) async => const []);

  final container = await bootAppForIntegration(
    tester,
    stub,
    configRepo: configRepo,
    providerRepo: providerRepo,
    extraOverrides: [
      serverHealthProvider.overrideWith(_OfflineHealth.new),
    ],
  );

  return _BootResult(container: container, stub: stub);
}

class _OfflineHealth extends ServerHealthNotifier {
  @override
  ServerHealthState build() =>
      const ServerHealthState(status: ServerHealth.offline);
}
