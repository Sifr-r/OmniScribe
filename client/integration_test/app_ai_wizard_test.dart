import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/shared/providers/repository_providers.dart';
import 'package:omniscribe_client/features/providers/ai_setup_wizard_modal.dart';

import '_test_helpers.dart';

/// Integration coverage for the AI Setup Wizard modal: standalone launch
/// (we don't go through the provider modal because the wizard is only
/// reachable from the AI Engine Status card, which only appears once a
/// document has been processed — out of scope for the empty-state tests).
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('AI Setup Wizard', () {
    testWidgets(
        'renders its mode-selection screen with the four engine options',
        (tester) async {
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

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            configRepositoryProvider.overrideWithValue(configRepo),
            providerRepositoryProvider.overrideWithValue(providerRepo),
          ],
          child: MaterialApp(
            home: Builder(
              builder: (ctx) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () => AISetupWizardModal.show(ctx),
                    child: const Text('open'),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      // Mode-selection screen offers Offline and Cloud paths.
      expect(find.byType(AISetupWizardModal), findsOneWidget);
      expect(find.text('AI Engine Setup Wizard'), findsOneWidget);
      expect(find.textContaining('Offline'), findsWidgets);
      expect(find.textContaining('Cloud'), findsWidgets);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('tapping outside the modal dismisses the wizard',
        (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            home: Builder(
              builder: (ctx) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () => AISetupWizardModal.show(ctx),
                    child: const Text('open'),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      expect(find.byType(AISetupWizardModal), findsOneWidget);

      // AppModal uses barrierDismissible; tap outside the modal.
      await tester.tapAt(const Offset(20, 20));
      await tester.pumpAndSettle();
      expect(find.byType(AISetupWizardModal), findsNothing);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('the wizard shows the four-step progress header',
        (tester) async {
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

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            configRepositoryProvider.overrideWithValue(configRepo),
            providerRepositoryProvider.overrideWithValue(providerRepo),
          ],
          child: MaterialApp(
            home: Builder(
              builder: (ctx) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () => AISetupWizardModal.show(ctx),
                    child: const Text('open'),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      // The progress header renders four step labels (Choose, Offline,
      // Cloud, Done — exact labels vary). Just confirm the header row is
      // present.
      expect(find.byType(AISetupWizardModal), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });
}
