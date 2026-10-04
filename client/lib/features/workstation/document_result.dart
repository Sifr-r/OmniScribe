import 'dart:convert';
import 'dart:ui' show Size;

import 'package:flutter/foundation.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';

/// Sniffs image dimensions from binary header (PNG, JPEG).
Size? parseImageDimensions(Uint8List bytes) {
  if (bytes.length >= 24 &&
      bytes[0] == 0x89 &&
      bytes[1] == 0x50 &&
      bytes[2] == 0x4E &&
      bytes[3] == 0x47) {
    final byteData = ByteData.sublistView(bytes);
    final width = byteData.getUint32(16, Endian.big).toDouble();
    final height = byteData.getUint32(20, Endian.big).toDouble();
    if (width > 0 && height > 0) {
      return Size(width, height);
    }
  }
  if (bytes.length >= 10 && bytes[0] == 0xFF && bytes[1] == 0xD8) {
    int offset = 2;
    while (offset < bytes.length - 8) {
      if (bytes[offset] != 0xFF) break;
      final marker = bytes[offset + 1];
      if (marker == 0xC0 || marker == 0xC1 || marker == 0xC2) {
        final byteData = ByteData.sublistView(bytes);
        final height = byteData.getUint16(offset + 5, Endian.big).toDouble();
        final width = byteData.getUint16(offset + 7, Endian.big).toDouble();
        if (width > 0 && height > 0) {
          return Size(width, height);
        }
        break;
      } else if (marker == 0xD9 || marker == 0xDA) {
        break;
      } else {
        final byteData = ByteData.sublistView(bytes);
        final len = byteData.getUint16(offset + 2, Endian.big);
        offset += 2 + len;
      }
    }
  }
  return null;
}

/// Trust and quality analysis metrics extracted from X-Document-Trust header.
class TrustSummary {
  const TrustSummary({
    required this.blockCount,
    required this.scoredCount,
    required this.flaggedCount,
    required this.average,
    this.histogram = const {},
    this.flagCounts = const {},
  });

  final int blockCount;
  final int scoredCount;
  final int flaggedCount;
  final double average;
  final Map<String, int> histogram;
  final Map<String, int> flagCounts;

  factory TrustSummary.fromJson(Map<String, dynamic> json) {
    final hist = <String, int>{};
    if (json['histogram'] is Map) {
      (json['histogram'] as Map).forEach((k, v) {
        if (v is num) hist[k.toString()] = v.toInt();
      });
    }

    final flags = <String, int>{};
    if (json['flag_counts'] is Map) {
      (json['flag_counts'] as Map).forEach((k, v) {
        if (v is num) flags[k.toString()] = v.toInt();
      });
    }

    return TrustSummary(
      blockCount: (json['block_count'] as num?)?.toInt() ?? 0,
      scoredCount: (json['scored_count'] as num?)?.toInt() ?? 0,
      flaggedCount: (json['flagged_count'] as num?)?.toInt() ?? 0,
      average: (json['average'] as num?)?.toDouble() ?? 0.0,
      histogram: hist,
      flagCounts: flags,
    );
  }

  static TrustSummary? tryParseHeader(String? rawHeader) {
    if (rawHeader == null || rawHeader.isEmpty) return null;
    try {
      final decoded = jsonDecode(rawHeader);
      if (decoded is Map<String, dynamic>) {
        return TrustSummary.fromJson(decoded);
      }
    } catch (_) {}
    return null;
  }

  Map<String, dynamic> toJson() => {
        'block_count': blockCount,
        'scored_count': scoredCount,
        'flagged_count': flaggedCount,
        'average': average,
        'histogram': histogram,
        'flag_counts': flagCounts,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is TrustSummary &&
          runtimeType == other.runtimeType &&
          blockCount == other.blockCount &&
          scoredCount == other.scoredCount &&
          flaggedCount == other.flaggedCount &&
          average == other.average &&
          mapEquals(histogram, other.histogram) &&
          mapEquals(flagCounts, other.flagCounts);

  @override
  int get hashCode => Object.hash(
        blockCount,
        scoredCount,
        flaggedCount,
        average,
        Object.hashAll(histogram.entries),
        Object.hashAll(flagCounts.entries),
      );
}

/// Structured page analysis representation.
class PageResult {
  const PageResult({
    required this.page,
    this.width,
    this.height,
    this.bboxes = const [],
    this.text,
    this.raw,
    this.imageUrl,
    this.previewBytes,
  });

  final int page;
  final double? width;
  final double? height;
  final List<BBoxItem> bboxes;
  final String? text;
  final Map<String, dynamic>? raw;
  final String? imageUrl;
  final Uint8List? previewBytes;

  double get aspectRatio {
    if (width != null && height != null && height! > 0) {
      return width! / height!;
    }
    if (previewBytes != null && previewBytes!.length >= 24) {
      final size = parseImageDimensions(previewBytes!);
      if (size != null && size.height > 0) {
        return size.width / size.height;
      }
    }
    return 8.5 / 11.0;
  }

  factory PageResult.fromJson(Map<String, dynamic> json) {
    final boxList = <BBoxItem>[];
    if (json['bboxes'] is List) {
      for (final item in json['bboxes'] as List) {
        if (item is Map<String, dynamic>) {
          boxList.add(BBoxItem.fromJson(item));
        }
      }
    }

    return PageResult(
      page: (json['page'] as num?)?.toInt() ?? 0,
      width: (json['width'] as num?)?.toDouble(),
      height: (json['height'] as num?)?.toDouble(),
      bboxes: boxList,
      text: json['text']?.toString(),
      imageUrl: json['image_url']?.toString(),
      raw: json,
    );
  }

  Map<String, dynamic> toJson() => {
        'page': page,
        if (width != null) 'width': width,
        if (height != null) 'height': height,
        'bboxes': bboxes.map((b) => b.toJson()).toList(),
        if (text != null) 'text': text,
        if (imageUrl != null) 'image_url': imageUrl,
      };

  PageResult copyWith({
    int? page,
    double? width,
    double? height,
    List<BBoxItem>? bboxes,
    String? text,
    Map<String, dynamic>? raw,
    String? imageUrl,
    Uint8List? previewBytes,
  }) {
    return PageResult(
      page: page ?? this.page,
      width: width ?? this.width,
      height: height ?? this.height,
      bboxes: bboxes ?? this.bboxes,
      text: text ?? this.text,
      raw: raw ?? this.raw,
      imageUrl: imageUrl ?? this.imageUrl,
      previewBytes: previewBytes ?? this.previewBytes,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is PageResult &&
          runtimeType == other.runtimeType &&
          page == other.page &&
          width == other.width &&
          height == other.height &&
          listEquals(bboxes, other.bboxes) &&
          text == other.text &&
          imageUrl == other.imageUrl &&
          identical(previewBytes, other.previewBytes);

  @override
  int get hashCode => Object.hash(
        page,
        width,
        height,
        Object.hashAll(bboxes),
        text,
        imageUrl,
        identityHashCode(previewBytes),
      );
}

/// Rasterized page preview payload from the document preview API.
class PagePreviewResult {
  const PagePreviewResult({
    required this.bytes,
    this.totalPages = 1,
    this.width,
    this.height,
    this.docId,
  });

  final Uint8List bytes;
  final int totalPages;
  final double? width;
  final double? height;
  final String? docId;
}

/// Confidence distribution statistics.
class ConfidenceSummary {
  const ConfidenceSummary({
    required this.average,
    required this.min,
    required this.max,
    this.count = 0,
  });

  final double average;
  final double min;
  final double max;
  final int count;

  int get averagePercent => (average * 100).round();
  int get minPercent => (min * 100).round();
  int get maxPercent => (max * 100).round();

  factory ConfidenceSummary.fromJson(Map<String, dynamic> json) {
    return ConfidenceSummary(
      average: (json['average'] as num?)?.toDouble() ?? 0.0,
      min: (json['min'] as num?)?.toDouble() ?? 0.0,
      max: (json['max'] as num?)?.toDouble() ?? 0.0,
      count: (json['count'] as num?)?.toInt() ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
        'average': average,
        'min': min,
        'max': max,
        'count': count,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ConfidenceSummary &&
          runtimeType == other.runtimeType &&
          average == other.average &&
          min == other.min &&
          max == other.max &&
          count == other.count;

  @override
  int get hashCode => Object.hash(average, min, max, count);
}

/// Quality repair loop summary metrics.
class QualitySummary {
  const QualitySummary({
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

  int get avgConfidencePercent => (avgConfidence * 100).round();
  int get targetPercent => (target * 100).round();

  factory QualitySummary.fromJson(Map<String, dynamic> json) {
    return QualitySummary(
      scope: json['scope']?.toString() ?? 'document',
      target: (json['target'] as num?)?.toDouble() ?? 0.85,
      avgConfidence: (json['avg_confidence'] as num?)?.toDouble() ?? 0.0,
      repairedCount: (json['repaired_count'] as num?)?.toInt() ?? 0,
      belowTargetCount: (json['below_target_count'] as num?)?.toInt() ?? 0,
      pageIdx: (json['page_idx'] as num?)?.toInt(),
    );
  }

  Map<String, dynamic> toJson() => {
        'scope': scope,
        'target': target,
        'avg_confidence': avgConfidence,
        'repaired_count': repairedCount,
        'below_target_count': belowTargetCount,
        if (pageIdx != null) 'page_idx': pageIdx,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is QualitySummary &&
          runtimeType == other.runtimeType &&
          scope == other.scope &&
          target == other.target &&
          avgConfidence == other.avgConfidence &&
          repairedCount == other.repairedCount &&
          belowTargetCount == other.belowTargetCount &&
          pageIdx == other.pageIdx;

  @override
  int get hashCode => Object.hash(
        scope,
        target,
        avgConfidence,
        repairedCount,
        belowTargetCount,
        pageIdx,
      );
}

/// Reference to a stored text artifact in OmniScribe artifact store.
class TextArtifactHandle {
  const TextArtifactHandle({
    required this.id,
    required this.token,
    this.pageCount,
  });

  final String id;
  final String token;
  final int? pageCount;

  factory TextArtifactHandle.fromJson(Map<String, dynamic> json) {
    return TextArtifactHandle(
      id: json['id']?.toString() ?? '',
      token: json['token']?.toString() ?? '',
      pageCount: (json['pageCount'] as num?)?.toInt(),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'token': token,
        if (pageCount != null) 'pageCount': pageCount,
      };
}

/// High-level presentation ViewModel for workstation document view.
class DocumentViewModel {
  const DocumentViewModel({
    this.pages = const [],
    this.textArtifacts = const [],
    this.textArtifact,
    this.textArtifactId,
    this.textArtifactToken,
    this.filename,
    this.selectedPageIndex = 0,
    this.bboxes = const [],
    this.confidence,
    this.confidenceSummary,
    this.pageCount = 0,
    this.trustSummary,
  });

  final List<PageResult> pages;
  final List<TextArtifactHandle> textArtifacts;
  final TextArtifactHandle? textArtifact;
  final String? textArtifactId;
  final String? textArtifactToken;
  final String? filename;
  final int selectedPageIndex;
  final List<BBoxItem> bboxes;
  final double? confidence;
  final ConfidenceSummary? confidenceSummary;
  final int pageCount;
  final TrustSummary? trustSummary;

  DocumentViewModel copyWith({
    List<PageResult>? pages,
    List<TextArtifactHandle>? textArtifacts,
    TextArtifactHandle? textArtifact,
    String? textArtifactId,
    String? textArtifactToken,
    String? filename,
    int? selectedPageIndex,
    List<BBoxItem>? bboxes,
    double? confidence,
    ConfidenceSummary? confidenceSummary,
    int? pageCount,
    TrustSummary? trustSummary,
  }) {
    return DocumentViewModel(
      pages: pages ?? this.pages,
      textArtifacts: textArtifacts ?? this.textArtifacts,
      textArtifact: textArtifact ?? this.textArtifact,
      textArtifactId: textArtifactId ?? this.textArtifactId,
      textArtifactToken: textArtifactToken ?? this.textArtifactToken,
      filename: filename ?? this.filename,
      selectedPageIndex: selectedPageIndex ?? this.selectedPageIndex,
      bboxes: bboxes ?? this.bboxes,
      confidence: confidence ?? this.confidence,
      confidenceSummary: confidenceSummary ?? this.confidenceSummary,
      pageCount: pageCount ?? this.pageCount,
      trustSummary: trustSummary ?? this.trustSummary,
    );
  }

  factory DocumentViewModel.fromJson(Map<String, dynamic> json) {
    final pagesList = <PageResult>[];
    if (json['pages'] is List) {
      for (final p in json['pages'] as List) {
        if (p is Map<String, dynamic>) pagesList.add(PageResult.fromJson(p));
      }
    }

    final artsList = <TextArtifactHandle>[];
    if (json['textArtifacts'] is List) {
      for (final a in json['textArtifacts'] as List) {
        if (a is Map<String, dynamic>) {
          artsList.add(TextArtifactHandle.fromJson(a));
        }
      }
    }

    final boxList = <BBoxItem>[];
    if (json['bboxes'] is List) {
      for (final b in json['bboxes'] as List) {
        if (b is Map<String, dynamic>) boxList.add(BBoxItem.fromJson(b));
      }
    }

    return DocumentViewModel(
      pages: pagesList,
      textArtifacts: artsList,
      textArtifact: json['textArtifact'] is Map<String, dynamic>
          ? TextArtifactHandle.fromJson(
              json['textArtifact'] as Map<String, dynamic>)
          : null,
      textArtifactId: json['textArtifactId']?.toString(),
      textArtifactToken: json['textArtifactToken']?.toString(),
      filename: json['filename']?.toString(),
      selectedPageIndex: (json['selectedPageIndex'] as num?)?.toInt() ?? 0,
      bboxes: boxList,
      confidence: (json['confidence'] as num?)?.toDouble(),
      confidenceSummary: json['confidenceSummary'] is Map<String, dynamic>
          ? ConfidenceSummary.fromJson(
              json['confidenceSummary'] as Map<String, dynamic>)
          : null,
      pageCount: (json['pageCount'] as num?)?.toInt() ?? pagesList.length,
      trustSummary: json['trustSummary'] is Map<String, dynamic>
          ? TrustSummary.fromJson(json['trustSummary'] as Map<String, dynamic>)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'pages': pages.map((p) => p.toJson()).toList(),
        'textArtifacts': textArtifacts.map((a) => a.toJson()).toList(),
        if (textArtifact != null) 'textArtifact': textArtifact!.toJson(),
        if (textArtifactId != null) 'textArtifactId': textArtifactId,
        if (textArtifactToken != null) 'textArtifactToken': textArtifactToken,
        if (filename != null) 'filename': filename,
        'selectedPageIndex': selectedPageIndex,
        'bboxes': bboxes.map((b) => b.toJson()).toList(),
        if (confidence != null) 'confidence': confidence,
        if (confidenceSummary != null)
          'confidenceSummary': confidenceSummary!.toJson(),
        'pageCount': pageCount,
        if (trustSummary != null) 'trustSummary': trustSummary!.toJson(),
      };
}

/// Result of synchronous OCR containing output PDF bytes and metadata headers.
class ProcessOcrResult {
  const ProcessOcrResult({
    required this.pdfBytes,
    required this.headers,
    this.trustSummary,
    this.textArtifactId,
    this.textArtifactToken,
    this.documentArtifactId,
    this.documentArtifactToken,
  });

  final Uint8List pdfBytes;
  final Map<String, String> headers;
  final TrustSummary? trustSummary;
  final String? textArtifactId;
  final String? textArtifactToken;
  final String? documentArtifactId;
  final String? documentArtifactToken;
}
