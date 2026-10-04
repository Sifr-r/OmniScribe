import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/shared/providers/api_providers.dart';

import 'glossary_models.dart';

final glossaryRepositoryProvider = Provider<GlossaryRepository>(
  (ref) => GlossaryRepository(ref.watch(apiClientProvider)),
);

class GlossaryRepository {
  const GlossaryRepository(this._apiClient);

  final ApiClient _apiClient;

  Future<List<GlossaryListItem>> getGlossaryLibraries() async {
    final dynamic response = await _apiClient.get<dynamic>(
      ApiConstants.glossaryLibrary,
    );

    if (response is! List ||
        response.any((item) => item is! Map<String, dynamic>)) {
      throw const FormatException('Malformed glossary library response.');
    }
    return List.unmodifiable(response.map(
        (item) => GlossaryListItem.fromJson(item as Map<String, dynamic>)));
  }

  Future<List<GlossaryEntry>> getGlossaryEntries(String libraryId) async {
    final dynamic response = await _apiClient.get<dynamic>(
      ApiConstants.glossaryEntries(libraryId),
    );

    return _readEntries(
        response is Map<String, dynamic> ? response['entries'] : response);
  }

  Future<List<GlossaryEntry>> getMergedGlossaryEntries() async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.glossaryMerged,
    );
    return _readEntries(json['entries']);
  }

  List<GlossaryEntry> _readEntries(Object? value) {
    if (value is! List || value.any((item) => item is! Map<String, dynamic>)) {
      throw const FormatException('Malformed glossary entries response.');
    }
    return List.unmodifiable(value
        .map((item) => GlossaryEntry.fromJson(item as Map<String, dynamic>)));
  }

  Future<GlossaryPreviewResponse> getGlossaryPreview() async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.glossaryPreview,
    );
    return GlossaryPreviewResponse.fromJson(json);
  }

  Future<bool> toggleGlossaryLibrary(String libraryId, bool enabled) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.glossaryToggle(libraryId),
      data: <String, dynamic>{'enabled': enabled},
    );
    return json['status'] == 'ok' || json['enabled'] == enabled;
  }

  Future<bool> deleteGlossaryLibrary(String libraryId) async {
    final json = await _apiClient.delete<Map<String, dynamic>>(
      ApiConstants.glossaryDelete(libraryId),
    );
    return json['status'] == 'ok' || json['deleted'] == true;
  }

  Future<bool> reorderGlossaryLibraries(List<String> orderedIds) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.glossaryReorder,
      data: <String, dynamic>{'ordered_ids': orderedIds},
    );
    return json['status'] == 'ok';
  }

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

  /// Poll the shared job-status route for a queued import job.
  ///
  /// A queued import returns `{"job_id": ..., "queued": true}` and nothing is
  /// written to the lexicon yet, so the caller must follow the handle instead
  /// of refreshing immediately.
  Future<GlossaryJobStatus> getImportJobStatus(String jobId) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.processStatus(jobId),
    );
    return GlossaryJobStatus.fromJson(json);
  }
}
