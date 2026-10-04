import 'package:omniscribe_client/core/serialization/json_fields.dart';

class TranslationRequest {
  const TranslationRequest({
    this.text,
    this.textArtifactId,
    this.textArtifactToken,
    this.promptTemplate,
    this.targetLanguage = 'English',
    this.apiBase,
    this.apiKey,
    this.model,
    this.glossary,
    this.glossaryText,
    this.slidingWindowWords,
    this.dualTranslate,
    this.secondApiBase,
    this.secondApiKey,
    this.secondModel,
    this.channelId,
  });

  final String? text;
  final String? textArtifactId;
  final String? textArtifactToken;
  final String? promptTemplate;
  final String? targetLanguage;
  final String? apiBase;
  final String? apiKey;
  final String? model;
  final List<Map<String, dynamic>>? glossary;
  final String? glossaryText;
  final int? slidingWindowWords;
  final bool? dualTranslate;
  final String? secondApiBase;
  final String? secondApiKey;
  final String? secondModel;
  final String? channelId;

  Map<String, dynamic> toJson() {
    final map = <String, dynamic>{};
    void addIfNonNull(String k, dynamic v) {
      if (v != null) map[k] = v;
    }

    addIfNonNull('text', text);
    addIfNonNull('text_artifact_id', textArtifactId);
    addIfNonNull('text_artifact_token', textArtifactToken);
    addIfNonNull('prompt_template', promptTemplate);
    addIfNonNull('target_language', targetLanguage);
    addIfNonNull('api_base', apiBase);
    addIfNonNull('api_key', apiKey);
    addIfNonNull('model', model);
    addIfNonNull('glossary', glossary);
    addIfNonNull('glossary_text', glossaryText);
    addIfNonNull('sliding_window_words', slidingWindowWords);
    addIfNonNull('dual_translate', dualTranslate);
    addIfNonNull('second_api_base', secondApiBase);
    addIfNonNull('second_api_key', secondApiKey);
    addIfNonNull('second_model', secondModel);
    addIfNonNull('channel_id', channelId);
    return map;
  }

  factory TranslationRequest.fromJson(Map<String, dynamic> json) {
    List<Map<String, dynamic>>? glossList;
    if (json['glossary'] is List) {
      glossList =
          (json['glossary'] as List).whereType<Map<String, dynamic>>().toList();
    }

    return TranslationRequest(
      text: (json['text'] as String?),
      textArtifactId: (json['text_artifact_id'] as String?),
      textArtifactToken: (json['text_artifact_token'] as String?),
      promptTemplate: (json['prompt_template'] as String?),
      targetLanguage: (json['target_language'] as String?),
      apiBase: (json['api_base'] as String?),
      apiKey: (json['api_key'] as String?),
      model: (json['model'] as String?),
      glossary: glossList,
      glossaryText: (json['glossary_text'] as String?),
      slidingWindowWords: (json['sliding_window_words'] as num?)?.toInt(),
      dualTranslate: json['dual_translate'] as bool?,
      secondApiBase: (json['second_api_base'] as String?),
      secondApiKey: (json['second_api_key'] as String?),
      secondModel: (json['second_model'] as String?),
      channelId: (json['channel_id'] as String?),
    );
  }
}

class TranslationResponse {
  const TranslationResponse({required this.translatedText});

  final String translatedText;

  factory TranslationResponse.fromJson(Map<String, dynamic> json) {
    return TranslationResponse(
      translatedText: jsonString(json, 'translated_text'),
    );
  }

  Map<String, dynamic> toJson() => {'translated_text': translatedText};
}

class NLLBTranslationResponse {
  const NLLBTranslationResponse({
    required this.translatedText,
    required this.sourceLang,
    required this.targetLang,
  });

  final String translatedText;
  final String sourceLang;
  final String targetLang;

  factory NLLBTranslationResponse.fromJson(Map<String, dynamic> json) {
    return NLLBTranslationResponse(
      translatedText: jsonString(json, 'translated_text'),
      sourceLang: jsonString(json, 'source_lang'),
      targetLang: jsonString(json, 'target_lang'),
    );
  }

  Map<String, dynamic> toJson() => {
        'translated_text': translatedText,
        'source_lang': sourceLang,
        'target_lang': targetLang,
      };
}

class TranslationJobStatusResponse {
  const TranslationJobStatusResponse({
    required this.jobId,
    required this.state,
    this.status,
    this.result,
    this.error,
    this.detail,
  });

  final String jobId;
  final String state;
  final String? status;
  final dynamic result;
  final String? error;
  final String? detail;

  factory TranslationJobStatusResponse.fromJson(Map<String, dynamic> json) {
    return TranslationJobStatusResponse(
      jobId: jsonString(json, 'job_id'),
      state: jsonString(json, json.containsKey('state') ? 'state' : 'status'),
      status: (json['status'] as String?),
      result: json['result'],
      error: (json['error'] as String?),
      detail: (json['detail'] as String?),
    );
  }

  Map<String, dynamic> toJson() => {
        'job_id': jobId,
        'state': state,
        if (status != null) 'status': status,
        if (result != null) 'result': result,
        if (error != null) 'error': error,
        if (detail != null) 'detail': detail,
      };
}
