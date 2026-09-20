import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/data/models/feature_models.dart';
import 'package:omniscribe_client/data/models/job_record.dart';

abstract class FeatureRepository {
  // Translation
  Future<TranslationResponse> translate(TranslationRequest request);
  Future<AsyncSubmitResponse> translateAsync(TranslationRequest request);
  Future<TranslationJobStatusResponse> getTranslationStatus(String jobId);
  Future<TranslationResponse> getTranslationResult(String jobId, String token);
  Future<NLLBTranslationResponse> translateNllb({
    required String text,
    required String targetLanguage,
  });

  // Transcription
  Future<TranscriptionResponse> transcribe({
    required Uint8List audioBytes,
    required String filename,
    TranscriptionRequest? request,
    void Function(int sent, int total)? onSendProgress,
  });

  // Structured Extraction
  Future<ExtractionResponse> extractStructuredData(ExtractionRequest request);

  // Document Export
  Future<DocumentExportResult> exportDocument(DocumentExportRequest request);
  Future<Uint8List> exportDocx(ExportDocxRequest request);
  Future<Uint8List> exportHtml(ExportHtmlRequest request);
  Future<Uint8List> exportDocxTree(ExportBlockTreeRequest request);
  Future<dynamic> exportBlockTree(ExportBlockTreeRequest request);

  // Glossary Library
  Future<List<GlossaryListItem>> getGlossaryLibraries();
  Future<List<GlossaryEntry>> getGlossaryEntries(String libraryId);
  Future<List<GlossaryEntry>> getMergedGlossaryEntries();
  Future<GlossaryPreviewResponse> getGlossaryPreview();
  Future<bool> toggleGlossaryLibrary(String libraryId, bool enabled);
  Future<bool> deleteGlossaryLibrary(String libraryId);
  Future<bool> reorderGlossaryLibraries(List<String> orderedIds);
  Future<GlossaryImportJobResponse> importGlossaryFile({
    required Uint8List fileBytes,
    required String filename,
    String? channelId,
  });
  Future<GlossaryImportJobResponse> importGlossaryUrl({
    required String url,
    required GlossaryFormat format,
    String? name,
    String? channelId,
  });
}

class FeatureRepositoryImpl implements FeatureRepository {
  const FeatureRepositoryImpl(this._apiClient);

  final ApiClient _apiClient;

  // ---------------------------------------------------------------------------
  // Translation Methods
  // ---------------------------------------------------------------------------

  @override
  Future<TranslationResponse> translate(TranslationRequest request) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.translate,
      data: request.toJson(),
    );
    return TranslationResponse.fromJson(json);
  }

  @override
  Future<AsyncSubmitResponse> translateAsync(TranslationRequest request) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.translateAsync,
      data: request.toJson(),
    );
    return AsyncSubmitResponse.fromJson(json);
  }

  @override
  Future<TranslationJobStatusResponse> getTranslationStatus(String jobId) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.translationStatus(jobId),
    );
    return TranslationJobStatusResponse.fromJson(json);
  }

  @override
  Future<TranslationResponse> getTranslationResult(String jobId, String token) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.translationResult(jobId),
      headers: {'X-Artifact-Token': token},
    );
    if (json['translated_text'] is! String) {
      throw const FormatException('Translation result did not contain translated text.');
    }
    return TranslationResponse.fromJson(json);
  }

  @override
  Future<NLLBTranslationResponse> translateNllb({
    required String text,
    required String targetLanguage,
  }) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.translateNllb,
      data: <String, dynamic>{
        'text': text,
        'target_language': targetLanguage,
      },
    );
    return NLLBTranslationResponse.fromJson(json);
  }

  // ---------------------------------------------------------------------------
  // Audio Transcription Method
  // ---------------------------------------------------------------------------

  @override
  Future<TranscriptionResponse> transcribe({
    required Uint8List audioBytes,
    required String filename,
    TranscriptionRequest? request,
    void Function(int sent, int total)? onSendProgress,
  }) async {
    final map = <String, dynamic>{
      'file': MultipartFile.fromBytes(audioBytes, filename: filename),
    };

    if (request != null) {
      final reqJson = request.toJson();
      reqJson.forEach((key, value) {
        if (value != null) map[key] = value.toString();
      });
    }

    final formData = FormData.fromMap(map);
    final response = await _apiClient.postMultipart<Map<String, dynamic>>(
      ApiConstants.transcribe,
      formData: formData,
      onSendProgress: onSendProgress,
    );

    return TranscriptionResponse.fromJson(response.data);
  }

  // ---------------------------------------------------------------------------
  // Structured Extraction Method
  // ---------------------------------------------------------------------------

  @override
  Future<ExtractionResponse> extractStructuredData(
    ExtractionRequest request,
  ) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.extract,
      data: request.toJson(),
    );
    return ExtractionResponse.fromJson(json);
  }

  // ---------------------------------------------------------------------------
  // Document Export Methods
  // ---------------------------------------------------------------------------

  @override
  Future<DocumentExportResult> exportDocument(
    DocumentExportRequest request,
  ) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.exportDocument,
      data: request.toJson(),
    );
    return DocumentExportResult.fromJson(json);
  }

  @override
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

  @override
  Future<Uint8List> exportHtml(ExportHtmlRequest request) async {
    final json = await _apiClient.post<List<int>>(
      ApiConstants.exportHtml,
      data: request.toJson(),
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(json);
  }

  @override
  Future<Uint8List> exportDocxTree(ExportBlockTreeRequest request) async {
    final json = await _apiClient.post<List<int>>(
      ApiConstants.exportDocxTree,
      data: request.toJson(),
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(json);
  }

  @override
  Future<dynamic> exportBlockTree(ExportBlockTreeRequest request) async {
    return _apiClient.post<dynamic>(
      ApiConstants.exportBlockTree,
      data: request.toJson(),
    );
  }

  // ---------------------------------------------------------------------------
  // Glossary Management Methods
  // ---------------------------------------------------------------------------

  @override
  Future<List<GlossaryListItem>> getGlossaryLibraries() async {
    final dynamic response = await _apiClient.get<dynamic>(
      ApiConstants.glossaryLibrary,
    );

    final list = <GlossaryListItem>[];
    if (response is List) {
      for (final item in response) {
        if (item is Map<String, dynamic>) {
          list.add(GlossaryListItem.fromJson(item));
        }
      }
    }
    return list;
  }

  @override
  Future<List<GlossaryEntry>> getGlossaryEntries(String libraryId) async {
    final dynamic response = await _apiClient.get<dynamic>(
      ApiConstants.glossaryEntries(libraryId),
    );

    final list = <GlossaryEntry>[];
    if (response is List) {
      for (final item in response) {
        if (item is Map<String, dynamic>) {
          list.add(GlossaryEntry.fromJson(item));
        }
      }
    } else if (response is Map<String, dynamic> &&
        response['entries'] is List) {
      for (final item in response['entries'] as List) {
        if (item is Map<String, dynamic>) {
          list.add(GlossaryEntry.fromJson(item));
        }
      }
    }
    return list;
  }

  @override
  Future<List<GlossaryEntry>> getMergedGlossaryEntries() async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.glossaryMerged,
    );
    final list = <GlossaryEntry>[];
    if (json['entries'] is List) {
      for (final item in json['entries'] as List) {
        if (item is Map<String, dynamic>) {
          list.add(GlossaryEntry.fromJson(item));
        }
      }
    }
    return list;
  }

  @override
  Future<GlossaryPreviewResponse> getGlossaryPreview() async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.glossaryPreview,
    );
    return GlossaryPreviewResponse.fromJson(json);
  }

  @override
  Future<bool> toggleGlossaryLibrary(String libraryId, bool enabled) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.glossaryToggle(libraryId),
      data: <String, dynamic>{'enabled': enabled},
    );
    return json['status'] == 'ok' || json['enabled'] == enabled;
  }

  @override
  Future<bool> deleteGlossaryLibrary(String libraryId) async {
    final json = await _apiClient.delete<Map<String, dynamic>>(
      ApiConstants.glossaryDelete(libraryId),
    );
    return json['status'] == 'ok' || json['deleted'] == true;
  }

  @override
  Future<bool> reorderGlossaryLibraries(List<String> orderedIds) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.glossaryReorder,
      data: <String, dynamic>{'ordered_ids': orderedIds},
    );
    return json['status'] == 'ok';
  }

  @override
  Future<GlossaryImportJobResponse> importGlossaryFile({
    required Uint8List fileBytes,
    required String filename,
    String? channelId,
  }) async {
    final map = <String, dynamic>{
      'file': MultipartFile.fromBytes(fileBytes, filename: filename),
    };
    if (channelId != null) map['channel_id'] = channelId;

    final formData = FormData.fromMap(map);
    final response = await _apiClient.postMultipart<Map<String, dynamic>>(
      ApiConstants.glossaryImport,
      formData: formData,
    );

    return GlossaryImportJobResponse.fromJson(response.data);
  }

  @override
  Future<GlossaryImportJobResponse> importGlossaryUrl({
    required String url,
    required GlossaryFormat format,
    String? name,
    String? channelId,
  }) async {
    final map = <String, dynamic>{
      'url': url,
      'format': format.value,
      if (name != null) 'name': name,
      if (channelId != null) 'channel_id': channelId,
    };

    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.glossaryImportUrl,
      data: map,
    );

    return GlossaryImportJobResponse.fromJson(json);
  }
}
