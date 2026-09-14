import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/features/extraction_screen.dart';
import 'package:omniscribe_client/presentation/features/glossary_screen.dart';
import 'package:omniscribe_client/presentation/features/transcription_screen.dart';
import 'package:omniscribe_client/presentation/features/translation_screen.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the four feature screens (translation,
/// transcription, extraction, glossary): each renders without crashing and
/// exposes its primary action affordance.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('Feature screens', () {
    testWidgets('Translation screen renders the hero header & translate button',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.translation);
      await tester.pumpAndSettle();

      expect(find.byType(TranslationScreen), findsOneWidget);
      expect(find.text('Neural Translation Engine'), findsOneWidget);
      expect(find.textContaining('Translate'), findsWidgets);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('Transcription screen renders the audio ingestion card',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.transcription);
      await tester.pumpAndSettle();

      expect(find.byType(TranscriptionScreen), findsOneWidget);
      expect(find.text('Voice & Audio Transcription'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('Extraction screen renders the schema header',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.extraction);
      await tester.pumpAndSettle();

      expect(find.byType(ExtractionScreen), findsOneWidget);
      expect(find.text('Structured Information Extraction'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('Glossary screen renders the terminology header',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      ctx.container.read(activeTabProvider.notifier).set(AppTab.glossary);
      await tester.pumpAndSettle();

      expect(find.byType(GlossaryScreen), findsOneWidget);
      expect(find.text('Terminology Glossary'), findsOneWidget);
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

  final container = await bootAppForIntegration(
    tester,
    stub,
    configRepo: configRepo,
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
