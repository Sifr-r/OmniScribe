import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/shared/providers/api_providers.dart';

import 'documents_models.dart';

final documentRepositoryProvider = Provider<DocumentRepository>(
  (ref) => DocumentRepository(ref.watch(apiClientProvider)),
);

class DocumentRepository {
  const DocumentRepository(this._apiClient);

  final ApiClient _apiClient;

  Future<ExtractionResponse> extractStructuredData(
    ExtractionRequest request,
  ) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.extract,
      data: request.toJson(),
    );
    return ExtractionResponse.fromJson(json);
  }

  Future<DocumentExportResult> exportDocument(
    DocumentExportRequest request,
  ) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.exportDocument,
      data: request.toJson(),
    );
    return DocumentExportResult.fromJson(json);
  }

  Future<Uint8List> exportDocx(ExportDocxRequest request) async {
    // Pedantic 2.1: POST the body instead of GETting with the text
    // in the query string. The server route is POST-only; the GET
    // variant used to put the full document text into the URL,
    // which uvicorn access logs, reverse-proxy logs, browser
    // history, and the Referer header all captured.
    final json = await _apiClient.post<List<int>>(
      ApiConstants.exportDocx,
      data: request.toJson(),
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(json);
  }

  Future<Uint8List> exportHtml(ExportHtmlRequest request) async {
    final json = await _apiClient.post<List<int>>(
      ApiConstants.exportHtml,
      data: request.toJson(),
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(json);
  }

  Future<Uint8List> exportMarkdown(ExportBlockTreeRequest request) async {
    final bytes = await _apiClient.post<List<int>>(
      ApiConstants.exportMarkdown,
      data: request.toJson(),
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(bytes);
  }

  Future<Uint8List> exportDocxTree(ExportBlockTreeRequest request) async {
    final json = await _apiClient.post<List<int>>(
      ApiConstants.exportDocxTree,
      data: request.toJson(),
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(json);
  }

  Future<dynamic> exportBlockTree(ExportBlockTreeRequest request) async {
    return _apiClient.post<dynamic>(
      ApiConstants.exportBlockTree,
      data: request.toJson(),
    );
  }
}
