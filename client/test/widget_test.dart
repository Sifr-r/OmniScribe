import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';

import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/repositories/config_repository.dart';
import 'package:omniscribe_client/main.dart';

class _MockConfigRepository extends Mock implements ConfigRepository {}

class _MockApiClient extends Mock implements ApiClient {}

void main() {
  testWidgets('OmniScribe app boots without throwing', (tester) async {
    final mockConfig = _MockConfigRepository();
    when(() => mockConfig.getConfig()).thenAnswer(
      (_) async => RuntimeConfig.fromJson(const {}),
    );
    when(() => mockConfig.getModelsForProvider(any())).thenAnswer(
      (_) async => ['allenai/olmocr-2-7b'],
    );

    // main.dart fires a startup health probe against /api/health; stub it so
    // the widget test stays hermetic.
    final mockClient = _MockApiClient();
    when(() => mockClient.get<dynamic>('/api/health')).thenAnswer(
      (_) async => const ApiResponse(
        data: {'status': 'ok'},
        statusCode: 200,
        headers: {},
      ),
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          configRepositoryProvider.overrideWithValue(mockConfig),
          apiClientProvider.overrideWithValue(mockClient),
        ],
        child: const OmniScribeApp(),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(MaterialApp), findsWidgets);
  });
}
