import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/repositories/sample_pdf_repository.dart';

class _MockApiClient extends Mock implements ApiClient {}

void main() {
  group('SamplePdfRepository', () {
    late _MockApiClient apiClient;
    late SamplePdfRepository repo;

    setUp(() {
      apiClient = _MockApiClient();
      repo = SamplePdfRepository(apiClient);
    });

    test('availableFixtures is the server-side allowlist, locked', () {
      // Lockstep contract with the server-side ALLOWED_SAMPLE_PDFS
      // in omniscribe.plugins.sample_pdfs. Adding a fixture
      // server-side requires also adding the name here. If the
      // list drifts, the Flutter UI will either fail to render the
      // option (list missing the name) or get a 404 from the server
      // (list has a name the server doesn't ship).
      expect(SamplePdfRepository.availableFixtures, <String>[
        'digital.pdf',
        'handwritten.pdf',
        'hybrid.pdf',
        'dense.pdf',
        'notes.pdf',
      ]);
    });

    test('defaultFixture is the most representative (digital.pdf)', () {
      // digital.pdf is small (126 KB), fast to OCR, and has clear
      // text on every page — the best first impression for a new
      // user verifying the install.
      expect(SamplePdfRepository.defaultFixture, 'digital.pdf');
    });

    test('fetchSamplePdf calls the correct route and returns bytes', () async {
      final fakePdf = Uint8List.fromList([0x25, 0x50, 0x44, 0x46, 0x2D]);
      when(() => apiClient.getBytes(
            '/api/sample-pdf/digital.pdf',
            headers: any(named: 'headers'),
          )).thenAnswer((_) async => fakePdf);

      final result = await repo.fetchSamplePdf('digital.pdf');

      expect(result, fakePdf);
      verify(() => apiClient.getBytes(
            '/api/sample-pdf/digital.pdf',
            headers: any(named: 'headers'),
          )).called(1);
    });

    test('fetchDefaultSamplePdf uses the default fixture name', () async {
      final fakePdf = Uint8List.fromList([0x25, 0x50, 0x44, 0x46, 0x2D]);
      when(() => apiClient.getBytes(
            '/api/sample-pdf/digital.pdf',
            headers: any(named: 'headers'),
          )).thenAnswer((_) async => fakePdf);

      final result = await repo.fetchDefaultSamplePdf();

      expect(result, fakePdf);
      verify(() => apiClient.getBytes(
            '/api/sample-pdf/digital.pdf',
            headers: any(named: 'headers'),
          )).called(1);
    });

    test('fetchSamplePdf propagates a 404 as a DioException', () async {
      when(() => apiClient.getBytes(
            any(),
            headers: any(named: 'headers'),
          )).thenThrow(
        DioException(
          requestOptions: RequestOptions(path: '/api/sample-pdf/bogus.pdf'),
          response: Response<dynamic>(
            requestOptions: RequestOptions(path: '/api/sample-pdf/bogus.pdf'),
            statusCode: 404,
          ),
          type: DioExceptionType.badResponse,
        ),
      );

      expect(
        () => repo.fetchSamplePdf('bogus.pdf'),
        throwsA(isA<DioException>()),
      );
    });
  });
}
