import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/app/shell_state.dart';
import 'package:omniscribe_client/features/workstation/controls/upload_dropzone.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the workstation upload dropzone: empty state,
/// browse-button surface, format pill affordances, and drag-over feedback.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('UploadDropzone', () {
    testWidgets('renders the empty-state with the Browse button',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      expect(find.byType(UploadDropzone), findsOneWidget);
      expect(find.textContaining('Browse'), findsWidgets);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('shows the format badges for supported file types',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      // The dropzone surfaces two AppBadges for the supported file types.
      expect(find.text('PDF'), findsWidgets);
      expect(find.text('PNG'), findsWidgets);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('the Browse Files CTA exists on the empty state',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      expect(find.text('Browse Files'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('tapping Browse Files does not throw on Windows',
        (tester) async {
      final ctx = await _boot(tester);
      addTearDown(ctx.stub.stop);

      final browseFinder = find.text('Browse Files');
      expect(browseFinder, findsOneWidget);

      await tester.tap(browseFinder, warnIfMissed: false);
      await tester.pump();
    }, tags: const [kPlatformWindows]);
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
