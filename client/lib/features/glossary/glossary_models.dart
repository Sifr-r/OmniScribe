import 'package:omniscribe_client/core/serialization/json_fields.dart';

enum GlossaryFormat {
  csv('csv'),
  tsv('tsv'),
  xliff('xliff'),
  tbx('tbx'),
  tmx('tmx'),
  gitGlossary('git_glossary'),
  sqlTable('sql_table'),
  jsonPairs('json_pairs'),
  lanesSqlite('lanes_sqlite'),
  lanesXml('lanes_xml');

  const GlossaryFormat(this.value);
  final String value;

  static GlossaryFormat fromString(String? value) {
    if (value == null) return GlossaryFormat.csv;
    for (final f in GlossaryFormat.values) {
      if (f.value == value) return f;
    }
    throw FormatException('Unsupported glossary format: $value');
  }
}

class GlossaryEntry {
  const GlossaryEntry({
    required this.source,
    required this.target,
    this.note,
    this.extra = const {},
  });

  final String source;
  final String target;
  final String? note;
  final Map<String, dynamic> extra;

  factory GlossaryEntry.fromJson(Map<String, dynamic> json) {
    return GlossaryEntry(
      source: jsonString(json, 'source'),
      target: jsonString(json, 'target'),
      note: (json['note'] as String?),
      extra: Map.unmodifiable(json),
    );
  }

  Map<String, dynamic> toJson() => {
        'source': source,
        'target': target,
        if (note != null) 'note': note,
      };
}

class GlossaryListItem {
  const GlossaryListItem({
    required this.id,
    required this.name,
    required this.format,
    required this.entryCount,
    required this.enabled,
    required this.priority,
    required this.group,
    this.sourceUri,
    this.encoding,
  });

  final String id;
  final String name;
  final GlossaryFormat format;
  final int entryCount;
  final bool enabled;
  final int priority;
  final String group;
  final String? sourceUri;
  final String? encoding;

  factory GlossaryListItem.fromJson(Map<String, dynamic> json) {
    return GlossaryListItem(
      id: jsonString(json, 'id'),
      name: jsonString(json, 'name'),
      format: GlossaryFormat.fromString(jsonString(json, 'format')),
      entryCount: jsonInt(json, 'entry_count'),
      enabled: json['enabled'] as bool? ?? true,
      priority: (json['priority'] as num?)?.toInt() ?? 0,
      group: (json['group'] as String?) ?? 'default',
      sourceUri: (json['source_uri'] as String?),
      encoding: (json['encoding'] as String?),
    );
  }

  GlossaryListItem copyWith({
    String? id,
    String? name,
    GlossaryFormat? format,
    int? entryCount,
    bool? enabled,
    int? priority,
    String? group,
    String? sourceUri,
    String? encoding,
    bool clearSourceUri = false,
    bool clearEncoding = false,
  }) {
    return GlossaryListItem(
      id: id ?? this.id,
      name: name ?? this.name,
      format: format ?? this.format,
      entryCount: entryCount ?? this.entryCount,
      enabled: enabled ?? this.enabled,
      priority: priority ?? this.priority,
      group: group ?? this.group,
      sourceUri: clearSourceUri ? null : (sourceUri ?? this.sourceUri),
      encoding: clearEncoding ? null : (encoding ?? this.encoding),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'format': format.value,
        'entry_count': entryCount,
        'enabled': enabled,
        'priority': priority,
        'group': group,
        if (sourceUri != null) 'source_uri': sourceUri,
        if (encoding != null) 'encoding': encoding,
      };

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is GlossaryListItem &&
        other.id == id &&
        other.name == name &&
        other.format == format &&
        other.entryCount == entryCount &&
        other.enabled == enabled &&
        other.priority == priority &&
        other.group == group &&
        other.sourceUri == sourceUri &&
        other.encoding == encoding;
  }

  @override
  int get hashCode => Object.hash(
        id,
        name,
        format,
        entryCount,
        enabled,
        priority,
        group,
        sourceUri,
        encoding,
      );
}

class GlossaryPreviewResponse {
  const GlossaryPreviewResponse({
    required this.count,
    this.conflicts = const [],
    this.enabledGlossaries = const [],
  });

  final int count;
  final List<Map<String, dynamic>> conflicts;
  final List<String> enabledGlossaries;

  factory GlossaryPreviewResponse.fromJson(Map<String, dynamic> json) {
    final confs = <Map<String, dynamic>>[];
    if (json['conflicts'] is List) {
      for (final c in json['conflicts'] as List) {
        if (c is Map<String, dynamic>) confs.add(c);
      }
    }
    final gloss = <String>[];
    if (json['enabled_glossaries'] is List) {
      for (final g in json['enabled_glossaries'] as List) {
        if (g != null) gloss.add(g.toString());
      }
    }

    return GlossaryPreviewResponse(
      count: (json['count'] as num?)?.toInt() ?? 0,
      conflicts: confs,
      enabledGlossaries: gloss,
    );
  }

  Map<String, dynamic> toJson() => {
        'count': count,
        'conflicts': conflicts,
        'enabled_glossaries': enabledGlossaries,
      };
}

class GlossaryImportJobResponse {
  const GlossaryImportJobResponse({
    required this.format,
    required this.name,
    required this.entryCount,
    this.glossaryId,
    this.jobId,
    this.warnings = const [],
    this.queued = false,
  });

  final GlossaryFormat format;
  final String name;
  final int entryCount;
  final String? glossaryId;
  final String? jobId;
  final List<String> warnings;
  final bool queued;

  factory GlossaryImportJobResponse.fromJson(Map<String, dynamic> json) {
    final warns = <String>[];
    if (json['warnings'] is List) {
      for (final w in json['warnings'] as List) {
        if (w != null) warns.add(w.toString());
      }
    }

    return GlossaryImportJobResponse(
      format: GlossaryFormat.fromString((json['format'] as String?)),
      name: jsonString(json, 'name'),
      entryCount: (json['entry_count'] as num?)?.toInt() ?? 0,
      glossaryId: (json['glossary_id'] as String?),
      jobId: (json['job_id'] as String?),
      warnings: warns,
      queued: json['queued'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() => {
        'format': format.value,
        'name': name,
        'entry_count': entryCount,
        if (glossaryId != null) 'glossary_id': glossaryId,
        if (jobId != null) 'job_id': jobId,
        'warnings': warnings,
        'queued': queued,
      };
}

/// Terminal + in-flight states of a queued glossary import job, as reported
/// by the shared job-status route (`GET /api/process/status/{job_id}`).
///
/// The server maps its internal queue vocabulary onto the HTTP vocabulary in
/// `plugins/ocr/schemas.py::_QUEUE_STATUS_TO_HTTP`, so only the HTTP spellings
/// appear here.
class GlossaryJobStatus {
  const GlossaryJobStatus({required this.status, this.error});

  static const Set<String> _succeededStates = {'complete'};
  static const Set<String> _failedStates = {'error', 'cancelled'};

  final String status;
  final String? error;

  bool get isTerminal =>
      _succeededStates.contains(status) || _failedStates.contains(status);

  bool get succeeded => _succeededStates.contains(status);

  factory GlossaryJobStatus.fromJson(Map<String, dynamic> json) {
    final status = jsonString(json, 'status');
    if (!const {'pending', 'processing', 'complete', 'error', 'cancelled'}
        .contains(status)) {
      throw FormatException('Unknown glossary import status: $status');
    }
    final error = json['error'];
    if (error != null && error is! String) {
      throw const FormatException('Expected a nullable string for "error".');
    }
    return GlossaryJobStatus(
      status: status,
      error: error as String?,
    );
  }
}
