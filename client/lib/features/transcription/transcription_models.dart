import 'package:omniscribe_client/core/serialization/json_fields.dart';

enum TranscriptionEngineType {
  api('api'),
  whisperApi('whisper_api'),
  local('local'),
  whisperLocal('whisper_local'),
  fasterWhisper('faster-whisper'),
  fasterWhisperUnderscore('faster_whisper'),
  auto('auto');

  const TranscriptionEngineType(this.value);
  final String value;

  static TranscriptionEngineType fromString(String? value) {
    if (value == null) return TranscriptionEngineType.auto;
    for (final e in TranscriptionEngineType.values) {
      if (e.value == value) return e;
    }
    throw FormatException('Unsupported transcription engine: $value');
  }
}

class TranscriptionRequest {
  const TranscriptionRequest({
    this.model,
    this.engine = TranscriptionEngineType.auto,
    this.apiBase,
    this.apiKey,
    this.language,
    this.prompt,
    this.temperature = 0.0,
    this.translateTo,
    this.channelId,
  });

  final String? model;
  final TranscriptionEngineType? engine;
  final String? apiBase;
  final String? apiKey;
  final String? language;
  final String? prompt;
  final double? temperature;
  final String? translateTo;
  final String? channelId;

  Map<String, dynamic> toJson() {
    final map = <String, dynamic>{};
    void addIfNonNull(String k, dynamic v) {
      if (v != null) map[k] = v;
    }

    addIfNonNull('model', model);
    if (engine != null) addIfNonNull('engine', engine!.value);
    addIfNonNull('api_base', apiBase);
    addIfNonNull('api_key', apiKey);
    addIfNonNull('language', language);
    addIfNonNull('prompt', prompt);
    addIfNonNull('temperature', temperature);
    addIfNonNull('translate_to', translateTo);
    addIfNonNull('channel_id', channelId);
    return map;
  }

  factory TranscriptionRequest.fromJson(Map<String, dynamic> json) {
    return TranscriptionRequest(
      model: (json['model'] as String?),
      engine: TranscriptionEngineType.fromString((json['engine'] as String?)),
      apiBase: (json['api_base'] as String?),
      apiKey: (json['api_key'] as String?),
      language: (json['language'] as String?),
      prompt: (json['prompt'] as String?),
      temperature: (json['temperature'] as num?)?.toDouble(),
      translateTo: (json['translate_to'] as String?),
      channelId: (json['channel_id'] as String?),
    );
  }
}

class TranscriptionSegment {
  const TranscriptionSegment({
    required this.start,
    required this.end,
    required this.text,
    this.id,
    this.extra = const {},
  });

  final int? id;
  final double start;
  final double end;
  final String text;
  final Map<String, dynamic> extra;

  factory TranscriptionSegment.fromJson(Map<String, dynamic> json) {
    return TranscriptionSegment(
      id: jsonInt(json, 'id'),
      start: jsonDouble(json, 'start'),
      end: jsonDouble(json, 'end'),
      text: jsonString(json, 'text'),
      extra: Map.unmodifiable(json),
    );
  }

  Map<String, dynamic> toJson() => {
        if (id != null) 'id': id,
        'start': start,
        'end': end,
        'text': text,
      };
}

class TranscriptionResponse {
  const TranscriptionResponse({
    required this.text,
    this.segments = const [],
    this.filename,
    this.duration,
    this.textArtifactId,
    this.textArtifactToken,
  });

  final String text;
  final List<TranscriptionSegment> segments;
  final String? filename;
  final double? duration;
  final String? textArtifactId;
  final String? textArtifactToken;

  factory TranscriptionResponse.fromJson(Map<String, dynamic> json) {
    final segs = <TranscriptionSegment>[];
    for (final item in (json['segments'] as List? ?? const [])) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException(
            'Expected an object for a transcription segment.');
      }
      segs.add(TranscriptionSegment.fromJson(item));
    }

    return TranscriptionResponse(
      text: jsonString(json, 'text'),
      segments: List.unmodifiable(segs),
      filename: (json['filename'] as String?),
      duration: json['duration'] == null ? null : jsonDouble(json, 'duration'),
      textArtifactId: (json['text_artifact_id'] as String?),
      textArtifactToken: (json['text_artifact_token'] as String?),
    );
  }

  Map<String, dynamic> toJson() => {
        'text': text,
        'segments': segments.map((s) => s.toJson()).toList(),
        if (filename != null) 'filename': filename,
        if (duration != null) 'duration': duration,
        if (textArtifactId != null) 'text_artifact_id': textArtifactId,
        if (textArtifactToken != null) 'text_artifact_token': textArtifactToken,
      };
}
