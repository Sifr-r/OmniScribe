import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/features/settings/config_repository.dart';

class _MockApiClient extends Mock implements ApiClient {}

void main() {
  late _MockApiClient apiClient;
  late ConfigRepositoryImpl repo;

  setUp(() {
    apiClient = _MockApiClient();
    repo = ConfigRepositoryImpl(apiClient);
  });

  group('ConfigRepositoryImpl.getModelsForProvider', () {
    test('parses the models list and hits /api/providers/{id}/models',
        () async {
      when(() => apiClient.get<Map<String, dynamic>>(
            '/api/providers/lmstudio/models',
          )).thenAnswer((_) async => <String, dynamic>{
            'models': <String>['a', 'b', 'c'],
          });

      final models = await repo.getModelsForProvider('lmstudio');

      expect(models, ['a', 'b', 'c']);
      verify(() => apiClient.get<Map<String, dynamic>>(
            '/api/providers/lmstudio/models',
          )).called(1);
    });

    test('returns an empty list when the response shape is unexpected',
        () async {
      when(() => apiClient.get<Map<String, dynamic>>(
            '/api/providers/lmstudio/models',
          )).thenAnswer((_) async => <String, dynamic>{'unexpected': true});

      final models = await repo.getModelsForProvider('lmstudio');

      expect(models, isEmpty);
    });
  });

  group('ConfigRepositoryImpl.getModels namespace mapping', () {
    test('general namespace delegates to lmstudio provider', () async {
      when(() => apiClient.get<Map<String, dynamic>>(
            '/api/providers/lmstudio/models',
          )).thenAnswer((_) async => <String, dynamic>{
            'models': <String>['m1'],
          });

      final models = await repo.getModels(namespace: 'general');

      expect(models, ['m1']);
      verify(() => apiClient.get<Map<String, dynamic>>(
            '/api/providers/lmstudio/models',
          )).called(1);
    });

    test('translation namespace returns an empty list (deferred)', () async {
      final models = await repo.getModels(namespace: 'translation');

      expect(models, isEmpty);
      verifyNever(() => apiClient.get<Map<String, dynamic>>(any()));
    });

    test('transcription namespace returns an empty list (deferred)', () async {
      final models = await repo.getModels(namespace: 'transcription');

      expect(models, isEmpty);
      verifyNever(() => apiClient.get<Map<String, dynamic>>(any()));
    });
  });
}
