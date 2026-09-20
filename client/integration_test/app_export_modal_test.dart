import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/workstation/modals/export_modal.dart';

import '_test_helpers.dart';

/// Integration coverage for the Export Modal: confirms the modal mounts and
/// exposes its title. The actual export-format list is only rendered once an
/// OCR result is present, which requires a full workstation golden-path run
/// (covered by `app_workstation_test.dart`). Modal dismiss is verified by
/// tapping outside the dialog barrier.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('ExportModal', () {
    testWidgets('mounts with the export document title', (tester) async {
      final configRepo = MockConfigRepository();
      final featureRepo = MockFeatureRepository();

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
      when(() => featureRepo.exportDocx(any())).thenAnswer(
        (_) async => Uint8List.fromList(<int>[1, 2, 3]),
      );
      when(() => featureRepo.exportBlockTree(any())).thenAnswer(
        (_) async => Uint8List.fromList(<int>[4, 5, 6]),
      );

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            configRepositoryProvider.overrideWithValue(configRepo),
            featureRepositoryProvider.overrideWithValue(featureRepo),
          ],
          child: MaterialApp(
            home: Builder(
              builder: (ctx) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () => ExportModal.show(ctx),
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

      expect(find.byType(ExportModal), findsOneWidget);
      // The dialog renders "Export Document" as the title (one match is the
      // title bar; the dialog body also references it). Use findsAtLeast(1).
      expect(find.text('Export Document'), findsAtLeast(1));
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('tapping outside the dialog dismisses the modal',
        (tester) async {
      final configRepo = MockConfigRepository();
      final featureRepo = MockFeatureRepository();

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
      when(() => featureRepo.exportDocx(any())).thenAnswer(
        (_) async => Uint8List.fromList(<int>[1, 2, 3]),
      );

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            configRepositoryProvider.overrideWithValue(configRepo),
            featureRepositoryProvider.overrideWithValue(featureRepo),
          ],
          child: MaterialApp(
            home: Builder(
              builder: (ctx) => Scaffold(
                body: Center(
                  child: ElevatedButton(
                    onPressed: () => ExportModal.show(ctx),
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
      expect(find.byType(ExportModal), findsOneWidget);

      // barrierDismissible: true — tap on the dialog barrier.
      await tester.tapAt(const Offset(20, 20));
      await tester.pumpAndSettle();
      expect(find.byType(ExportModal), findsNothing);
    }, tags: const [kPlatformWindows, kPlatformWeb]);
  });
}
