import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/providers/settings_state.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';
import 'package:omniscribe_client/presentation/workstation/workstation_screen.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the top navigation bar: brand chrome, all 7 tabs,
/// theme toggle, server health badge, provider preset pill, and Ctrl+1..7
/// keyboard navigation. Every test boots the real [OmniScribeApp] against the
/// in-process stub server, with the config + provider repos mocked so the
/// boot microtask doesn't block on real network calls.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('TabRibbon brand & version chrome', () {
    testWidgets('renders OmniScribe brand text and the v2.0 version badge',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      expect(find.text('OmniScribe'), findsWidgets);
      expect(find.text('v2.0'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('clicking the brand logo returns to the Workstation tab',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.settings);
      await tester.pumpAndSettle();
      expect(find.text('Settings & Configuration'), findsOneWidget);

      // Brand chip is the only clickable row anchored to "OmniScribe" before
      // the tab ribbon; tapping the first match hits the logo's InkWell.
      await tester.tap(find.text('OmniScribe').first);
      await tester.pumpAndSettle();

      expect(ctx.container.read(activeTabProvider), AppTab.workstation);
      expect(find.byType(WorkstationScreen), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });

  group('Tab navigation - all 7 tabs', () {
    final tabExpectations = <AppTab, String>{
      AppTab.workstation: 'Upload document for OCR processing',
      AppTab.translation: 'Neural Translation Engine',
      AppTab.transcription: 'Voice & Audio Transcription',
      AppTab.extraction: 'Structured Information Extraction',
      AppTab.glossary: 'Terminology Glossary',
      AppTab.jobs: 'Job Execution History',
      AppTab.settings: 'Settings & Configuration',
    };

    for (final entry in tabExpectations.entries) {
      testWidgets('switching to ${entry.key.id} shows its screen content',
          (tester) async {
        final ctx = await _boot(tester);
        addTearDown(ctx.stub.stop);

        await tester.tap(find.byKey(ValueKey(entry.key.testId)));
        await tester.pumpAndSettle();

        expect(ctx.container.read(activeTabProvider), entry.key);
        expect(find.textContaining(entry.value), findsWidgets);
      }, tags: const [kPlatformWindows, kPlatformWeb]);
    }
  });

  group('Keyboard shortcuts - Ctrl+1..7', () {
    final shortcutMap = <int, AppTab>{
      1: AppTab.workstation,
      2: AppTab.translation,
      3: AppTab.transcription,
      4: AppTab.extraction,
      5: AppTab.glossary,
      6: AppTab.jobs,
      7: AppTab.settings,
    };

    for (final entry in shortcutMap.entries) {
      testWidgets('Ctrl+${entry.key} navigates to ${entry.value.id}',
          (tester) async {
        final ctx = await _boot(tester);
        addTearDown(ctx.stub.stop);

        // flutter_test does not take modifier flags directly; press Ctrl down
        // first, then the digit, then release both. The KeyEventSimulator
        // tracks currently-held keys and sets the modifier bitmask on the
        // subsequent KeyDownEvent for the digit.
        await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
        await tester.sendKeyDownEvent(_digitKeyFor(entry.key));
        await tester.sendKeyUpEvent(_digitKeyFor(entry.key));
        await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
        await tester.pumpAndSettle();

        expect(ctx.container.read(activeTabProvider), entry.value);
      }, tags: const [kPlatformWindows, kPlatformWeb]);
    }
  });

  group('Theme toggle & provider preset pill', () {
    testWidgets('tapping the theme toggle flips dark/light mode',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      // Seed value is light mode (isDarkMode == false).
      expect(ctx.container.read(settingsStateProvider).isDarkMode, isFalse);

      // The theme toggle is the only widget in the tab ribbon whose tooltip
      // matches "Switch to Dark Theme" until it has been flipped.
      final themeFinder = find.byTooltip('Switch to Dark Theme');
      expect(themeFinder, findsOneWidget);
      await tester.tap(themeFinder);
      await tester.pumpAndSettle();

      expect(ctx.container.read(settingsStateProvider).isDarkMode, isTrue);

      // Inverse tooltip is now exposed.
      expect(find.byTooltip('Switch to Light Theme'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('the provider preset pill shows the active provider id',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      // settings_state seed activeProviderId = 'openai' — the pill renders
      // it uppercased via `settings.activeProviderId.toUpperCase()` in
      // `TabRibbon.build`.
      expect(find.text('OPENAI'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });
}

LogicalKeyboardKey _digitKeyFor(int digit) {
  switch (digit) {
    case 1:
      return LogicalKeyboardKey.digit1;
    case 2:
      return LogicalKeyboardKey.digit2;
    case 3:
      return LogicalKeyboardKey.digit3;
    case 4:
      return LogicalKeyboardKey.digit4;
    case 5:
      return LogicalKeyboardKey.digit5;
    case 6:
      return LogicalKeyboardKey.digit6;
    case 7:
      return LogicalKeyboardKey.digit7;
    default:
      throw ArgumentError.value(digit, 'digit', 'must be 1..7');
  }
}

/// Per-test boot result, so each `testWidgets` block can teardown its own
/// stub server and dispose of its own mocks.
class _BootResult {
  _BootResult({
    required this.container,
    required this.stub,
  });

  final ProviderContainer container;
  final StubOmniscribeServer stub;
}

/// Builds fresh mock repos, starts a stub server, and boots the real app
/// against it with offline server health (the badge pulses forever otherwise).
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

/// Pins [serverHealthProvider] to a settled offline state so the
/// `ServerHealthBadge` does not animate forever and `pumpAndSettle` can
/// complete.
class _OfflineHealth extends ServerHealthNotifier {
  @override
  ServerHealthState build() =>
      const ServerHealthState(status: ServerHealth.offline);
}
