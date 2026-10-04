import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/constants/api_constants.dart';
import 'package:omniscribe_client/core/network/api_client.dart';
import 'package:omniscribe_client/shared/providers/api_providers.dart';

import 'transcription_models.dart';

final transcriptionRepositoryProvider = Provider<TranscriptionRepository>(
  (ref) => TranscriptionRepository(ref.watch(apiClientProvider)),
);

class TranscriptionRepository {
  const TranscriptionRepository(this._apiClient);

  final ApiClient _apiClient;

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

    try {
      return TranscriptionResponse.fromJson(response.data);
    } on TypeError {
      throw const FormatException('Malformed transcription response.');
    }
  }
}
