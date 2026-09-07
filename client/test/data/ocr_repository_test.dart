import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/repositories/ocr_repository.dart';

class _MockApiClient extends Mock implements ApiClient {}

void main() {
  setUpAll(() {
    registerFallbackValue(FormData());
  });

  group('OcrRepositoryImpl.renderDocumentPagePreview', () {
    late _MockApiClient apiClient;
    late OcrRepositoryImpl repo;

    setUp(() {
      apiClient = _MockApiClient();
      repo = OcrRepositoryImpl(apiClient);
    });

    test('uploads file when fileBytes is provided and extracts x-document-id',
        () async {
      final samplePdf = Uint8List.fromList([0x25, 0x50, 0x44, 0x46]);
      final fakePngBytes = Uint8List.fromList([0x89, 0x50, 0x4E, 0x47]);

      when(() => apiClient.postMultipartBytes(
            ApiConstants.documentPreview,
            formData: any(named: 'formData'),
            receiveTimeout: any(named: 'receiveTimeout'),
          )).thenAnswer(
        (_) async => ApiResponse<Uint8List>(
          data: fakePngBytes,
          statusCode: 200,
          headers: {
            'x-document-id': 'doc-12345678',
            'x-total-pages': '5',
            'x-page-width': '612.0',
            'x-page-height': '792.0',
          },
        ),
      );

      final result = await repo.renderDocumentPagePreview(
        fileBytes: samplePdf,
        filename: 'test.pdf',
        pageIndex: 0,
        dpi: 150,
      );

      expect(result, isNotNull);
      expect(result!.docId, 'doc-12345678');
      expect(result.totalPages, 5);
      expect(result.width, 612.0);
      expect(result.height, 792.0);
      expect(result.bytes, fakePngBytes);

      final captured = verify(() => apiClient.postMultipartBytes(
            ApiConstants.documentPreview,
            formData: captureAny(named: 'formData'),
            receiveTimeout: any(named: 'receiveTimeout'),
          )).captured.single as FormData;

      expect(captured.fields.any((f) => f.key == 'page' && f.value == '0'),
          isTrue);
      expect(captured.fields.any((f) => f.key == 'dpi' && f.value == '150'),
          isTrue);
      expect(captured.files.any((f) => f.key == 'file'), isTrue);
    });

    test('omits file field when fileBytes is null and docId is provided',
        () async {
      final fakePngBytes = Uint8List.fromList([0x89, 0x50, 0x4E, 0x47]);

      when(() => apiClient.postMultipartBytes(
            ApiConstants.documentPreview,
            formData: any(named: 'formData'),
            receiveTimeout: any(named: 'receiveTimeout'),
          )).thenAnswer(
        (_) async => ApiResponse<Uint8List>(
          data: fakePngBytes,
          statusCode: 200,
          headers: {
            'x-document-id': 'doc-abcdef12',
            'x-total-pages': '3',
            'x-page-width': '595.0',
            'x-page-height': '842.0',
          },
        ),
      );

      final result = await repo.renderDocumentPagePreview(
        filename: 'cached.pdf',
        pageIndex: 2,
        dpi: 200,
        docId: 'doc-abcdef12',
      );

      expect(result, isNotNull);
      expect(result!.docId, 'doc-abcdef12');
      expect(result.totalPages, 3);
      expect(result.width, 595.0);
      expect(result.height, 842.0);

      final captured = verify(() => apiClient.postMultipartBytes(
            ApiConstants.documentPreview,
            formData: captureAny(named: 'formData'),
            receiveTimeout: any(named: 'receiveTimeout'),
          )).captured.single as FormData;

      expect(
          captured.fields
              .any((f) => f.key == 'doc_id' && f.value == 'doc-abcdef12'),
          isTrue);
      expect(captured.fields.any((f) => f.key == 'page' && f.value == '2'),
          isTrue);
      expect(captured.fields.any((f) => f.key == 'dpi' && f.value == '200'),
          isTrue);
      expect(captured.files.any((f) => f.key == 'file'), isFalse);
    });

    test('returns null without network call when both fileBytes and docId are null',
        () async {
      final result = await repo.renderDocumentPagePreview(
        filename: 'none.pdf',
        pageIndex: 0,
      );

      expect(result, isNull);
      verifyZeroInteractions(apiClient);
    });
  });

  group('OcrRepositoryImpl.cancelProgressChannel', () {
    late _MockApiClient apiClient;
    late OcrRepositoryImpl repo;

    setUp(() {
      apiClient = _MockApiClient();
      repo = OcrRepositoryImpl(apiClient);
    });

    test('sends the channel session token as X-Session-Token', () async {
      when(() => apiClient.post<Map<String, dynamic>>(
            any(),
            headers: any(named: 'headers'),
          )).thenAnswer((_) async => <String, dynamic>{'cancelled': true});

      final cancelled = await repo.cancelProgressChannel(
        'ch-42',
        sessionToken: 'sess-abc',
      );

      expect(cancelled, isTrue);

      final captured = verify(() => apiClient.post<Map<String, dynamic>>(
            ApiConstants.cancelProgress('ch-42'),
            headers: captureAny(named: 'headers'),
          )).captured.single as Map<String, dynamic>;
      expect(captured['X-Session-Token'], 'sess-abc');
    });

    test('does not put the session token in the URL', () async {
      when(() => apiClient.post<Map<String, dynamic>>(
            any(),
            headers: any(named: 'headers'),
          )).thenAnswer((_) async => <String, dynamic>{'cancelled': true});

      await repo.cancelProgressChannel('ch-42', sessionToken: 'sess-abc');

      final path = verify(() => apiClient.post<Map<String, dynamic>>(
            captureAny(),
            headers: any(named: 'headers'),
          )).captured.single as String;
      expect(path, isNot(contains('sess-abc')));
    });
  });
}
