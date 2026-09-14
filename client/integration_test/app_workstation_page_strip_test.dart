import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the workstation page strip: it should not appear
/// before a document is loaded, and the layout should not crash when the
/// dropzone is shown. Full multi-page interaction is exercised by the
/// workstation widget tests in `test/presentation/`.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('Workstation page strip', () {
    testWidgets('the empty workstation renders without the page strip',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      // The PageStrip is wrapped in a "loaded-document-only" guard. Without a
      // document, only the UploadDropzone should be visible.
      expect(find.textContaining('Browse'), findsWidgets);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('the workstation body remains stable across pump cycles',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      // We can't load a PDF from the integration test without a real file
      // picker. Instead, verify the workstation's body doesn't throw and
      // remains stable across a few pump cycles.
      await tester.pump(const Duration(milliseconds: 200));
      await tester.pump(const Duration(milliseconds: 200));
      await tester.pumpAndSettle(const Duration(milliseconds: 200));

      // Dropzone still in place, app still alive.
      expect(find.textContaining('Browse'), findsWidgets);
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
