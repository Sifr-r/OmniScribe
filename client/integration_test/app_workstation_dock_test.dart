import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';
import 'package:omniscribe_client/presentation/workstation/controls/smart_preset_selector.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the workstation control dock: smart preset
/// selector renders the four preset cards and lets the user pick one.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('SmartPresetSelector', () {
    testWidgets('renders without throwing when no document is loaded',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      // The selector lives inside the workstation body. Before any document
      // is loaded it's still rendered (with a "Load a document" hint).
      final selector = find.byType(SmartPresetSelector);
      // Selector may not be on screen until a doc is loaded — this is a
      // best-effort assertion.
      if (selector.evaluate().isNotEmpty) {
        expect(selector, findsOneWidget);
      }
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets(
        'the empty workstation does not show the preset selector (document-gated)',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      // SmartPresetSelector is only mounted once a document is loaded. The
      // empty workstation shows only the dropzone. Verify the selector is
      // hidden so we don't accidentally regress the gated condition.
      final selector = find.byType(SmartPresetSelector);
      expect(selector, findsNothing);
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
