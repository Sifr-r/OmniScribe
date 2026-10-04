import 'package:omniscribe_client/core/serialization/json_fields.dart';

enum ExtractionTemplate {
  invoice('invoice'),
  resume('resume'),
  academic('academic'),
  table('table'),
  tableExtraction('table_extraction'),
  custom('custom');

  const ExtractionTemplate(this.value);
  final String value;

  static ExtractionTemplate fromString(String? value) {
    if (value == null) return ExtractionTemplate.custom;
    for (final t in ExtractionTemplate.values) {
      if (t.value == value) return t;
    }
    return ExtractionTemplate.custom;
  }
}

class ExtractionRequest {
  const ExtractionRequest({
    this.text,
    this.template = ExtractionTemplate.custom,
    this.customPrompt,
    this.apiBase,
    this.apiKey,
    this.model,
  });

  final String? text;
  final ExtractionTemplate? template;
  final String? customPrompt;
  final String? apiBase;
  final String? apiKey;
  final String? model;

  Map<String, dynamic> toJson() {
    final map = <String, dynamic>{};
    void addIfNonNull(String k, dynamic v) {
      if (v != null) map[k] = v;
    }

    addIfNonNull('text', text);
    if (template != null) addIfNonNull('template', template!.value);
    addIfNonNull('custom_prompt', customPrompt);
    addIfNonNull('api_base', apiBase);
    addIfNonNull('api_key', apiKey);
    addIfNonNull('model', model);
    return map;
  }

  factory ExtractionRequest.fromJson(Map<String, dynamic> json) {
    return ExtractionRequest(
      text: (json['text'] as String?),
      template: ExtractionTemplate.fromString((json['template'] as String?)),
      customPrompt: (json['custom_prompt'] as String?),
      apiBase: (json['api_base'] as String?),
      apiKey: (json['api_key'] as String?),
      model: (json['model'] as String?),
    );
  }
}

class ExtractionResponse {
  const ExtractionResponse({required this.extractedData});

  final dynamic extractedData;

  factory ExtractionResponse.fromJson(Map<String, dynamic> json) {
    if (!json.containsKey('extracted_data')) {
      throw const FormatException(
          'Extraction response is missing extracted_data.');
    }
    return ExtractionResponse(
      extractedData: json['extracted_data'],
    );
  }

  Map<String, dynamic> toJson() => {'extracted_data': extractedData};
}

enum DocumentExportFormat {
  json('json'),
  markdown('markdown'),
  text('text'),
  docling('docling'),
  mineru('mineru');

  const DocumentExportFormat(this.value);
  final String value;

  static DocumentExportFormat fromString(String? value) {
    if (value == null) return DocumentExportFormat.markdown;
    for (final f in DocumentExportFormat.values) {
      if (f.value == value) return f;
    }
    return DocumentExportFormat.markdown;
  }
}

class DocumentExportRequest {
  const DocumentExportRequest({
    required this.textArtifactId,
    required this.textArtifactToken,
    this.exportFormat = DocumentExportFormat.markdown,
    this.metadataArtifactId,
    this.metadataArtifactToken,
    this.documentArtifactId,
    this.documentArtifactToken,
  });

  final String textArtifactId;
  final String textArtifactToken;
  final DocumentExportFormat exportFormat;
  final String? metadataArtifactId;
  final String? metadataArtifactToken;
  final String? documentArtifactId;
  final String? documentArtifactToken;

  Map<String, dynamic> toJson() => {
        'text_artifact_id': textArtifactId,
        'text_artifact_token': textArtifactToken,
        'export_format': exportFormat.value,
        if (metadataArtifactId != null)
          'metadata_artifact_id': metadataArtifactId,
        if (metadataArtifactToken != null)
          'metadata_artifact_token': metadataArtifactToken,
        if (documentArtifactId != null)
          'document_artifact_id': documentArtifactId,
        if (documentArtifactToken != null)
          'document_artifact_token': documentArtifactToken,
      };

  factory DocumentExportRequest.fromJson(Map<String, dynamic> json) {
    return DocumentExportRequest(
      textArtifactId: (json['text_artifact_id'] as String?) ?? '',
      textArtifactToken: (json['text_artifact_token'] as String?) ?? '',
      exportFormat:
          DocumentExportFormat.fromString((json['export_format'] as String?)),
      metadataArtifactId: (json['metadata_artifact_id'] as String?),
      metadataArtifactToken: (json['metadata_artifact_token'] as String?),
      documentArtifactId: (json['document_artifact_id'] as String?),
      documentArtifactToken: (json['document_artifact_token'] as String?),
    );
  }
}

class DocumentExportResult {
  const DocumentExportResult({
    required this.artifactId,
    required this.token,
    required this.format,
  });

  final String artifactId;
  final String token;
  final String format;

  factory DocumentExportResult.fromJson(Map<String, dynamic> json) {
    return DocumentExportResult(
      artifactId: jsonString(json, 'artifact_id'),
      token: jsonString(json, 'token'),
      format: jsonString(json, 'format'),
    );
  }

  Map<String, dynamic> toJson() => {
        'artifact_id': artifactId,
        'token': token,
        'format': format,
      };
}

class ExportDocxRequest {
  const ExportDocxRequest({this.text});

  final String? text;

  Map<String, dynamic> toJson() => {
        if (text != null) 'text': text,
      };

  factory ExportDocxRequest.fromJson(Map<String, dynamic> json) {
    return ExportDocxRequest(text: (json['text'] as String?));
  }
}

class ExportHtmlRequest {
  const ExportHtmlRequest({
    required this.textArtifactId,
    required this.textArtifactToken,
    this.documentArtifactId,
    this.documentArtifactToken,
  });

  final String textArtifactId;
  final String textArtifactToken;
  final String? documentArtifactId;
  final String? documentArtifactToken;

  Map<String, dynamic> toJson() => {
        'text_artifact_id': textArtifactId,
        'text_artifact_token': textArtifactToken,
        if (documentArtifactId != null)
          'document_artifact_id': documentArtifactId,
        if (documentArtifactToken != null)
          'document_artifact_token': documentArtifactToken,
      };

  factory ExportHtmlRequest.fromJson(Map<String, dynamic> json) {
    return ExportHtmlRequest(
      textArtifactId: (json['text_artifact_id'] as String?) ?? '',
      textArtifactToken: (json['text_artifact_token'] as String?) ?? '',
      documentArtifactId: (json['document_artifact_id'] as String?),
      documentArtifactToken: (json['document_artifact_token'] as String?),
    );
  }
}

class ExportBlockTreeRequest {
  const ExportBlockTreeRequest({
    required this.textArtifactId,
    required this.textArtifactToken,
    this.metadataArtifactId,
    this.metadataArtifactToken,
    this.documentArtifactId,
    this.documentArtifactToken,
  });

  final String textArtifactId;
  final String textArtifactToken;
  final String? metadataArtifactId;
  final String? metadataArtifactToken;
  final String? documentArtifactId;
  final String? documentArtifactToken;

  Map<String, dynamic> toJson() => {
        'text_artifact_id': textArtifactId,
        'text_artifact_token': textArtifactToken,
        if (metadataArtifactId != null)
          'metadata_artifact_id': metadataArtifactId,
        if (metadataArtifactToken != null)
          'metadata_artifact_token': metadataArtifactToken,
        if (documentArtifactId != null)
          'document_artifact_id': documentArtifactId,
        if (documentArtifactToken != null)
          'document_artifact_token': documentArtifactToken,
      };

  factory ExportBlockTreeRequest.fromJson(Map<String, dynamic> json) {
    return ExportBlockTreeRequest(
      textArtifactId: (json['text_artifact_id'] as String?) ?? '',
      textArtifactToken: (json['text_artifact_token'] as String?) ?? '',
      metadataArtifactId: (json['metadata_artifact_id'] as String?),
      metadataArtifactToken: (json['metadata_artifact_token'] as String?),
      documentArtifactId: (json['document_artifact_id'] as String?),
      documentArtifactToken: (json['document_artifact_token'] as String?),
    );
  }
}
