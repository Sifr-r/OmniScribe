import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/repositories/job_repository.dart';
import 'package:omniscribe_client/data/repositories/ocr_repository.dart';

class _MockApiClient extends Mock implements ApiClient {}

class _MockOcrRepository extends Mock implements OcrRepository {}

void main() {
  group('JobRepositoryImpl.downloadResult', () {
    late _MockApiClient apiClient;
    late _MockOcrRepository ocrRepo;
    late JobRepositoryImpl repo;

    setUp(() {
      apiClient = _MockApiClient();
      ocrRepo = _MockOcrRepository();
      repo = JobRepositoryImpl(apiClient, ocrRepo);
    });

    test(
        'resolves the token via the SSE channel (out-of-band) and downloads '
        'with a dedicated artifact header', () async {
      final expectedBytes = Uint8List.fromList([1, 2, 3, 4]);
      when(() => ocrRepo.getJobArtifactToken('job-42'))
          .thenAnswer((_) async => 'tok-99');
      when(() => apiClient.getBytes(
            ApiConstants.jobResult('job-42'),
            headers: {'X-Artifact-Token': 'tok-99'},
          )).thenAnswer((_) async => expectedBytes);

      final result = await repo.downloadResult('job-42');

      expect(result, expectedBytes);
      verify(() => ocrRepo.getJobArtifactToken('job-42')).called(1);
      verify(() => apiClient.getBytes(
            ApiConstants.jobResult('job-42'),
            headers: {'X-Artifact-Token': 'tok-99'},
          )).called(1);
    });

    test('propagates the SSE token-resolution error', () async {
      when(() => ocrRepo.getJobArtifactToken('job-7'))
          .thenThrow(StateError('SSE closed before job_completed'));

      await expectLater(
        repo.downloadResult('job-7'),
        throwsA(isA<StateError>()),
      );
      verifyNever(() => apiClient.getBytes(any(),
          queryParameters: any(named: 'queryParameters'),
          headers: any(named: 'headers')));
    });
  });
}
