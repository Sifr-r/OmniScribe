import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/features/jobs/job_record.dart';
import 'package:omniscribe_client/shared/providers/api_providers.dart';

import 'translation_models.dart';

final translationRepositoryProvider = Provider<TranslationRepository>(
  (ref) => TranslationRepository(ref.watch(apiClientProvider)),
);

class TranslationRepository {
  const TranslationRepository(this._apiClient);

  final ApiClient _apiClient;

  Future<TranslationResponse> translate(TranslationRequest request) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.translate,
      data: request.toJson(),
    );
    return TranslationResponse.fromJson(json);
  }

  Future<AsyncSubmitResponse> translateAsync(TranslationRequest request) async {
    final json = await _apiClient.post<Map<String, dynamic>>(
      ApiConstants.translateAsync,
      data: request.toJson(),
    );
    return AsyncSubmitResponse.fromJson(json);
  }

  Future<TranslationJobStatusResponse> getTranslationStatus(
      String jobId) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.translationStatus(jobId),
    );
    return TranslationJobStatusResponse.fromJson(json);
  }

  Future<TranslationResponse> getTranslationResult(
      String jobId, String token) async {
    final json = await _apiClient.get<Map<String, dynamic>>(
      ApiConstants.translationResult(jobId),
      headers: {'X-Artifact-Token': token},
    );
    return TranslationResponse.fromJson(json);
  }

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
}
