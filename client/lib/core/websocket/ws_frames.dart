import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';

/// WebSocket frame envelope and polymorphic message models matching OmniScribe backend progress channels.

abstract class WsEnvelope {
  const WsEnvelope();

  /// Deserializes incoming JSON maps into the corresponding typed frame.
  factory WsEnvelope.fromJson(Map<String, dynamic> json) {
    final type = json['type']?.toString();

    // Legacy progress frames do not carry a 'type' field
    if (type == null &&
        json.containsKey('status') &&
        json.containsKey('percent')) {
      return ProgressFrame.fromJson(json);
    }

    switch (type) {
      case 'progress':
        return ProgressFrame.fromJson(json);
      case 'block_complete':
        return BlockCompleteFrame.fromJson(json);
      case 'block_retry':
        return BlockRetryFrame.fromJson(json);
      case 'block_revised':
        return BlockRevisedFrame.fromJson(json);
      case 'page_complete':
        return PageCompleteFrame.fromJson(json);
      case 'quality_summary':
        return QualitySummaryFrame.fromJson(json);
      case 'chunk_init':
        return ChunkInitFrame.fromJson(json);
      case 'chunk_complete':
        return ChunkCompleteFrame.fromJson(json);
      case 'translate_chunk_complete':
        return TranslateChunkCompleteFrame.fromJson(json);
      case 'cancelled':
        return CancelledFrame.fromJson(json);
      case 'glossary_import':
        return GlossaryImportFrame.fromJson(json);
      case 'connected':
        return ConnectedFrame.fromJson(json);
      case 'pong':
        return PongFrame.fromJson(json);
      default:
        return UnknownFrame(type: type ?? 'unknown', rawData: json);
    }
  }

  Map<String, dynamic> toJson();
}

/// Standard progress update frame.
class ProgressFrame extends WsEnvelope {
  const ProgressFrame({
    required this.status,
    required this.percent,
    required this.stage,
    this.warning = false,
  });

  final String status;
  final int percent;
  final String stage;
  final bool warning;

  factory ProgressFrame.fromJson(Map<String, dynamic> json) {
    return ProgressFrame(
      status: json['status']?.toString() ?? '',
      percent: (json['percent'] as num?)?.toInt() ?? 0,
      stage: json['stage']?.toString() ?? '',
      warning: json['warning'] as bool? ?? false,
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'status': status,
        'percent': percent,
        'stage': stage,
        if (warning) 'warning': warning,
      };
}

/// Block processed frame.
class BlockCompleteFrame extends WsEnvelope {
  const BlockCompleteFrame({
    required this.pageIdx,
    required this.blockIdx,
    required this.bbox,
    required this.text,
    required this.kind,
    this.confidence,
  });

  final int pageIdx;
  final int blockIdx;
  final List<double> bbox;
  final String text;
  final String kind;
  final double? confidence;

  BBoxItem toBBoxItem() => BBoxItem(
        blockId: 'p${pageIdx}_b$blockIdx',
        page: pageIdx,
        block: blockIdx,
        bbox: bbox,
        text: text,
        kind: kind,
        confidence: confidence,
      );

  factory BlockCompleteFrame.fromJson(Map<String, dynamic> json) {
    final rawBbox = json['bbox'];
    final coords = <double>[];
    if (rawBbox is List) {
      for (final item in rawBbox) {
        if (item is num) coords.add(item.toDouble());
      }
    }
    return BlockCompleteFrame(
      pageIdx: (json['page_idx'] as num?)?.toInt() ?? 0,
      blockIdx: (json['block_idx'] as num?)?.toInt() ?? 0,
      bbox: coords.length == 4 ? coords : const [0.0, 0.0, 1.0, 1.0],
      text: json['text']?.toString() ?? '',
      kind: json['kind']?.toString() ?? 'paragraph',
      confidence: (json['confidence'] as num?)?.toDouble(),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'block_complete',
        'page_idx': pageIdx,
        'block_idx': blockIdx,
        'bbox': bbox,
        'text': text,
        'kind': kind,
        if (confidence != null) 'confidence': confidence,
      };
}

/// Quality repair retry notification for a specific block.
class BlockRetryFrame extends WsEnvelope {
  const BlockRetryFrame({
    required this.pageIdx,
    required this.blockIdx,
    required this.attempt,
    required this.confidence,
    required this.target,
  });

  final int pageIdx;
  final int blockIdx;
  final int attempt;
  final double confidence;
  final double target;

  String get blockKey => 'p${pageIdx}_b$blockIdx';

  factory BlockRetryFrame.fromJson(Map<String, dynamic> json) {
    return BlockRetryFrame(
      pageIdx: (json['page_idx'] as num?)?.toInt() ?? 0,
      blockIdx: (json['block_idx'] as num?)?.toInt() ?? 0,
      attempt: (json['attempt'] as num?)?.toInt() ?? 1,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0.0,
      target: (json['target'] as num?)?.toDouble() ?? 0.85,
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'block_retry',
        'page_idx': pageIdx,
        'block_idx': blockIdx,
        'attempt': attempt,
        'confidence': confidence,
        'target': target,
      };
}

/// Revised block payload after quality repair loop.
class BlockRevisedFrame extends WsEnvelope {
  const BlockRevisedFrame({
    required this.pageIdx,
    required this.blockIdx,
    required this.attempt,
    required this.bbox,
    required this.text,
    required this.kind,
    this.confidence,
  });

  final int pageIdx;
  final int blockIdx;
  final int attempt;
  final List<double> bbox;
  final String text;
  final String kind;
  final double? confidence;

  BBoxItem toBBoxItem() => BBoxItem(
        blockId: 'p${pageIdx}_b$blockIdx',
        page: pageIdx,
        block: blockIdx,
        bbox: bbox,
        text: text,
        kind: kind,
        confidence: confidence,
        revised: true,
      );

  factory BlockRevisedFrame.fromJson(Map<String, dynamic> json) {
    final rawBbox = json['bbox'];
    final coords = <double>[];
    if (rawBbox is List) {
      for (final item in rawBbox) {
        if (item is num) coords.add(item.toDouble());
      }
    }
    return BlockRevisedFrame(
      pageIdx: (json['page_idx'] as num?)?.toInt() ?? 0,
      blockIdx: (json['block_idx'] as num?)?.toInt() ?? 0,
      attempt: (json['attempt'] as num?)?.toInt() ?? 1,
      bbox: coords.length == 4 ? coords : const [0.0, 0.0, 1.0, 1.0],
      text: json['text']?.toString() ?? '',
      kind: json['kind']?.toString() ?? 'paragraph',
      confidence: (json['confidence'] as num?)?.toDouble(),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'block_revised',
        'page_idx': pageIdx,
        'block_idx': blockIdx,
        'attempt': attempt,
        'bbox': bbox,
        'text': text,
        'kind': kind,
        if (confidence != null) 'confidence': confidence,
      };
}

/// Page processing completed notification.
class PageCompleteFrame extends WsEnvelope {
  const PageCompleteFrame({required this.pageIdx});

  final int pageIdx;

  factory PageCompleteFrame.fromJson(Map<String, dynamic> json) {
    return PageCompleteFrame(
      pageIdx: (json['page_idx'] as num?)?.toInt() ?? 0,
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'page_complete',
        'page_idx': pageIdx,
      };
}

/// Quality loop summary metrics.
class QualitySummaryFrame extends WsEnvelope {
  const QualitySummaryFrame({
    required this.scope,
    required this.target,
    required this.avgConfidence,
    required this.repairedCount,
    required this.belowTargetCount,
    this.pageIdx,
  });

  final String scope;
  final double target;
  final double avgConfidence;
  final int repairedCount;
  final int belowTargetCount;
  final int? pageIdx;

  QualitySummary get summary => QualitySummary(
        scope: scope,
        target: target,
        avgConfidence: avgConfidence,
        repairedCount: repairedCount,
        belowTargetCount: belowTargetCount,
        pageIdx: pageIdx,
      );

  factory QualitySummaryFrame.fromJson(Map<String, dynamic> json) {
    return QualitySummaryFrame(
      scope: json['scope']?.toString() ?? 'document',
      target: (json['target'] as num?)?.toDouble() ?? 0.85,
      avgConfidence: (json['avg_confidence'] as num?)?.toDouble() ?? 0.0,
      repairedCount: (json['repaired_count'] as num?)?.toInt() ?? 0,
      belowTargetCount: (json['below_target_count'] as num?)?.toInt() ?? 0,
      pageIdx: (json['page_idx'] as num?)?.toInt(),
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'quality_summary',
        'scope': scope,
        'target': target,
        'avg_confidence': avgConfidence,
        'repaired_count': repairedCount,
        'below_target_count': belowTargetCount,
        if (pageIdx != null) 'page_idx': pageIdx,
      };
}

/// Chunk processing initialization frame for multi-part large documents.
class ChunkInitFrame extends WsEnvelope {
  const ChunkInitFrame({
    required this.totalChunks,
    this.chapters = const [],
  });

  final int totalChunks;
  final List<Map<String, dynamic>> chapters;

  factory ChunkInitFrame.fromJson(Map<String, dynamic> json) {
    final list = <Map<String, dynamic>>[];
    if (json['chapters'] is List) {
      for (final item in json['chapters'] as List) {
        if (item is Map<String, dynamic>) list.add(item);
      }
    }
    return ChunkInitFrame(
      totalChunks: (json['total_chunks'] as num?)?.toInt() ?? 1,
      chapters: list,
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'chunk_init',
        'total_chunks': totalChunks,
        'chapters': chapters,
      };
}

/// Chunk processing complete frame.
class ChunkCompleteFrame extends WsEnvelope {
  const ChunkCompleteFrame({
    required this.chunkIdx,
    required this.totalChunks,
    required this.pageRange,
    required this.sourcePages,
    required this.textCharsSoFar,
    this.overallPercent,
    this.chapters = const [],
  });

  final int chunkIdx;
  final int totalChunks;
  final String pageRange;
  final List<int> sourcePages;
  final int textCharsSoFar;
  final int? overallPercent;
  final List<Map<String, dynamic>> chapters;

  factory ChunkCompleteFrame.fromJson(Map<String, dynamic> json) {
    final pages = <int>[];
    if (json['source_pages'] is List) {
      for (final item in json['source_pages'] as List) {
        if (item is num) pages.add(item.toInt());
      }
    }
    final chaps = <Map<String, dynamic>>[];
    if (json['chapters'] is List) {
      for (final item in json['chapters'] as List) {
        if (item is Map<String, dynamic>) chaps.add(item);
      }
    }
    return ChunkCompleteFrame(
      chunkIdx: (json['chunk_idx'] as num?)?.toInt() ?? 0,
      totalChunks: (json['total_chunks'] as num?)?.toInt() ?? 1,
      pageRange: json['page_range']?.toString() ?? '',
      sourcePages: pages,
      textCharsSoFar: (json['text_chars_so_far'] as num?)?.toInt() ?? 0,
      overallPercent: (json['overall_percent'] as num?)?.toInt(),
      chapters: chaps,
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'chunk_complete',
        'chunk_idx': chunkIdx,
        'total_chunks': totalChunks,
        'page_range': pageRange,
        'source_pages': sourcePages,
        'text_chars_so_far': textCharsSoFar,
        if (overallPercent != null) 'overall_percent': overallPercent,
        'chapters': chapters,
      };
}

/// Translation chunk stream frame.
class TranslateChunkCompleteFrame extends WsEnvelope {
  const TranslateChunkCompleteFrame({
    required this.chunkIdx,
    required this.sourceChars,
    required this.translatedText,
    required this.targetLanguage,
  });

  final int chunkIdx;
  final int sourceChars;
  final String translatedText;
  final String targetLanguage;

  factory TranslateChunkCompleteFrame.fromJson(Map<String, dynamic> json) {
    return TranslateChunkCompleteFrame(
      chunkIdx: (json['chunk_idx'] as num?)?.toInt() ?? 0,
      sourceChars: (json['source_chars'] as num?)?.toInt() ?? 0,
      translatedText: json['translated_text']?.toString() ?? '',
      targetLanguage: json['target_language']?.toString() ?? '',
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'translate_chunk_complete',
        'chunk_idx': chunkIdx,
        'source_chars': sourceChars,
        'translated_text': translatedText,
        'target_language': targetLanguage,
      };
}

/// Job cancellation confirmation frame.
class CancelledFrame extends WsEnvelope {
  const CancelledFrame({
    this.status = 'Cancelled by user.',
    this.percent = 0,
    this.stage = 'cancelled',
  });

  final String status;
  final int percent;
  final String stage;

  factory CancelledFrame.fromJson(Map<String, dynamic> json) {
    return CancelledFrame(
      status: json['status']?.toString() ?? 'Cancelled by user.',
      percent: (json['percent'] as num?)?.toInt() ?? 0,
      stage: json['stage']?.toString() ?? 'cancelled',
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'cancelled',
        'status': status,
        'percent': percent,
        'stage': stage,
      };
}

/// Glossary asynchronous import event frame.
class GlossaryImportFrame extends WsEnvelope {
  const GlossaryImportFrame({
    required this.status,
    required this.glossaryId,
    required this.name,
    required this.format,
    required this.entryCount,
    this.warnings = const [],
  });

  final String status;
  final String glossaryId;
  final String name;
  final String format;
  final int entryCount;
  final List<String> warnings;

  factory GlossaryImportFrame.fromJson(Map<String, dynamic> json) {
    final warns = <String>[];
    if (json['warnings'] is List) {
      for (final item in json['warnings'] as List) {
        warns.add(item.toString());
      }
    }
    return GlossaryImportFrame(
      status: json['status']?.toString() ?? '',
      glossaryId: json['glossary_id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      format: json['format']?.toString() ?? '',
      entryCount: (json['entry_count'] as num?)?.toInt() ?? 0,
      warnings: warns,
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'glossary_import',
        'status': status,
        'glossary_id': glossaryId,
        'name': name,
        'format': format,
        'entry_count': entryCount,
        'warnings': warnings,
      };
}

/// Successful WebSocket connection confirmation frame.
class ConnectedFrame extends WsEnvelope {
  const ConnectedFrame({required this.channelId});

  final String channelId;

  factory ConnectedFrame.fromJson(Map<String, dynamic> json) {
    return ConnectedFrame(
      channelId: json['channel_id']?.toString() ?? '',
    );
  }

  @override
  Map<String, dynamic> toJson() => {
        'type': 'connected',
        'channel_id': channelId,
      };
}

/// Keep-alive pong response frame.
class PongFrame extends WsEnvelope {
  const PongFrame();

  factory PongFrame.fromJson(Map<String, dynamic> json) => const PongFrame();

  @override
  Map<String, dynamic> toJson() => {'type': 'pong'};
}

/// Fallback for unexpected or custom frame types.
class UnknownFrame extends WsEnvelope {
  const UnknownFrame({
    required this.type,
    required this.rawData,
  });

  final String type;
  final Map<String, dynamic> rawData;

  @override
  Map<String, dynamic> toJson() => rawData;
}
