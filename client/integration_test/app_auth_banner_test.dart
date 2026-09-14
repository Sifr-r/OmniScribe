import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/app_tab.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/common/auth_required_banner.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';
import 'package:omniscribe_client/presentation/workstation/workstation_screen.dart';

import '_test_helpers.dart';
import 'stub_omniscribe_server.dart';

/// Integration coverage for the Auth Required Banner: the widget is always
/// mounted under AppShell, but its rendered output collapses to a
/// SizedBox.shrink() when the auth flag is false. We assert on the visible
/// banner copy rather than the widget tree.
void main() {
  initBinding();

  setUpAll(registerOmniscribeFallbacks);

  group('AuthRequiredBanner', () {
    testWidgets('renders nothing when the auth flag is false',
        (tester) async {
      final ctx = await _boot(tester, authRequired: false);
      addTearDown(ctx.stub.stop);

      // The widget itself is always mounted, but it returns SizedBox.shrink()
      // when not visible. Assert on the rendered copy.
      expect(find.byType(WorkstationScreen), findsOneWidget);
      expect(find.text('Authentication required'), findsNothing);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('renders the banner copy when the auth flag is true',
        (tester) async {
      final ctx = await _boot(tester, authRequired: true);
      addTearDown(ctx.stub.stop);

      expect(find.byType(AuthRequiredBanner), findsOneWidget);
      expect(find.text('Authentication required'), findsOneWidget);
      expect(find.text('Open Settings'), findsOneWidget);
    }, tags: const [kPlatformWindows, kPlatformWeb]);

    testWidgets('the banner CTA switches to the Settings tab and dismisses',
        (tester) async {
      final ctx = await _boot(tester, authRequired: true);
      addTearDown(ctx.stub.stop);

      await tester.tap(find.text('Open Settings'));
      await tester.pumpAndSettle();

      expect(ctx.container.read(activeTabProvider), AppTab.settings);
      // Banner also clears the auth-required flag.
      expect(ctx.container.read(authRequiredProvider), isFalse);
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
  required bool authRequired,
}) async {
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
      authRequiredProvider.overrideWith(() => _AuthRequiredFixed(authRequired)),
    ],
  );

  return _BootResult(container: container, stub: stub);
}

class _AuthRequiredFixed extends AuthRequiredNotifier {
  _AuthRequiredFixed(this._value);
  final bool _value;

  @override
  bool build() => _value;
}

class _OfflineHealth extends ServerHealthNotifier {
  @override
  ServerHealthState build() =>
      const ServerHealthState(status: ServerHealth.offline);
}
