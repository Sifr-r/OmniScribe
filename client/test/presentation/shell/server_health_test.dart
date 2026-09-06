import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/enums/server_health.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/core/theme/app_theme.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/presentation/shell/server_health_badge.dart';
import 'package:omniscribe_client/presentation/shell/shell_state.dart';

class _MockApiClient extends Mock implements ApiClient {}

void main() {
  late _MockApiClient mockClient;
  late ProviderContainer container;

  setUp(() {
    mockClient = _MockApiClient();
    // Wave 16 / flutter_riverpod 3.4: ``ServerHealthNotifier`` no longer
    // accepts a constructor argument; the api client is read from
    // ``apiClientProvider`` inside ``build()``. Each test gets its own
    // container with the mock client overridden.
    container = ProviderContainer(
      overrides: [
        apiClientProvider.overrideWithValue(mockClient),
      ],
    );
  });

  tearDown(() {
    container.dispose();
  });

  group('ServerHealthNotifier', () {
    test('checkHealth sets online when API call succeeds', () async {
      when(() => mockClient.get<dynamic>('/api/health')).thenAnswer(
        (_) async => const ApiResponse(
          data: {'status': 'ok'},
          statusCode: 200,
          headers: {},
        ),
      );

      final notifier = container.read(serverHealthProvider.notifier);
      await notifier.checkHealth();

      final state = container.read(serverHealthProvider);
      expect(state.status, ServerHealth.online);
      expect(state.latencyMs, isNotNull);
      expect(state.error, isNull);
    });

    test('checkHealth sets offline when API call fails', () async {
      when(() => mockClient.get<dynamic>('/api/health')).thenThrow(
        Exception('Connection refused'),
      );

      final notifier = container.read(serverHealthProvider.notifier);
      await notifier.checkHealth();

      final state = container.read(serverHealthProvider);
      expect(state.status, ServerHealth.offline);
      expect(state.latencyMs, isNull);
      expect(state.error, contains('Connection refused'));
    });

    test('initial state is checking before the first health probe', () {
      // The notifier seeds as "Checking" so the shell badge never displays a
      // fabricated online/latency state; main.dart fires the first real
      // probe at app start.
      final state = container.read(serverHealthProvider);
      expect(state.status, ServerHealth.checking);
      expect(state.latencyMs, isNull);
      expect(state.endpoint, isNotEmpty);
    });
  });

  group('ServerHealthBadge', () {
    testWidgets('tap triggers checkHealth via serverHealthProvider', (tester) async {
      when(() => mockClient.get<dynamic>('/api/health')).thenAnswer(
        (_) async => const ApiResponse(
          data: {'status': 'healthy'},
          statusCode: 200,
          headers: {},
        ),
      );

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            apiClientProvider.overrideWithValue(mockClient),
          ],
          child: MaterialApp(
            theme: AppTheme.darkTheme,
            home: const Scaffold(
              body: ServerHealthBadge(),
            ),
          ),
        ),
      );

      expect(find.byType(ServerHealthBadge), findsOneWidget);

      await tester.tap(find.byType(ServerHealthBadge));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      verify(() => mockClient.get<dynamic>('/api/health')).called(1);
    });
  });
}
