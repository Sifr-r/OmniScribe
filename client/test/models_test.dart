import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';
import 'package:omniscribe_client/features/workstation/process_settings.dart';
import 'package:omniscribe_client/core/websocket/ws_frames.dart';

void main() {
  test('page preview updates have distinct identity without scanning bytes', () {
    final preview = Uint8List.fromList([1]);
    final page = PageResult(page: 0, previewBytes: preview);
    final shared = page.copyWith();
    expect(shared, page);
    expect(shared.hashCode, page.hashCode);
    expect(page.copyWith(previewBytes: Uint8List.fromList([2])), isNot(page));
    expect(PageResult(page: 0), isNot(page));
  });

  group('BBoxItem Model Tests', () {
    test('Calculates normalized dimensions and confidence tiers correctly', () {
      const box = BBoxItem(
        blockId: 'p0_b1',
        page: 0,
        block: 1,
        bbox: [0.1, 0.2, 0.6, 0.8],
        text: 'Sample OCR text',
        confidence: 0.92,
        kind: 'paragraph',
      );

      expect(box.x0, 0.1);
      expect(box.y0, 0.2);
      expect(box.x1, 0.6);
      expect(box.y1, 0.8);
      expect(box.width, closeTo(0.5, 0.001));
      expect(box.height, closeTo(0.6, 0.001));
      expect(box.confidencePercent, 92);
      expect(box.confidenceTier, 'high');
    });

    test('Identifies medium and low confidence tiers', () {
      const mediumBox = BBoxItem(
        blockId: 'b_med',
        page: 0,
        block: 2,
        bbox: [0, 0, 1, 1],
        text: 'Med',
        confidence: 0.72,
      );
      expect(mediumBox.confidenceTier, 'medium');

      const lowBox = BBoxItem(
        blockId: 'b_low',
        page: 0,
        block: 3,
        bbox: [0, 0, 1, 1],
        text: 'Low',
        confidence: 0.45,
      );
      expect(lowBox.confidenceTier, 'low');
    });

    test('Serializes to and from JSON matching backend schemas', () {
      final jsonMap = {
        'block_id': 'p1_b4',
        'page': 1,
        'block': 4,
        'bbox': [0.15, 0.25, 0.75, 0.85],
        'confidence': 0.89,
        'text': 'Detected header text',
        'kind': 'heading',
        'revised': true,
      };

      final box = BBoxItem.fromJson(jsonMap);
      expect(box.blockId, 'p1_b4');
      expect(box.page, 1);
      expect(box.block, 4);
      expect(box.text, 'Detected header text');
      expect(box.kind, 'heading');
      expect(box.revised, isTrue);
      expect(box.confidence, 0.89);

      final encoded = box.toJson();
      expect(encoded['block_id'], 'p1_b4');
      expect(encoded['revised'], isTrue);
    });
  });

  group('WsEnvelope Frame Tests', () {
    test('Parses legacy ProgressFrame without type field', () {
      final jsonMap = {
        'status': 'Extracting tokens...',
        'percent': 45,
        'stage': 'OCR',
        'warning': false,
      };

      final frame = WsEnvelope.fromJson(jsonMap);
      expect(frame, isA<ProgressFrame>());
      final p = frame as ProgressFrame;
      expect(p.status, 'Extracting tokens...');
      expect(p.percent, 45);
      expect(p.stage, 'OCR');
    });

    test('Parses block_complete frame and generates BBoxItem', () {
      final jsonMap = {
        'type': 'block_complete',
        'page_idx': 0,
        'block_idx': 2,
        'bbox': [0.05, 0.10, 0.95, 0.30],
        'text': 'Complete block text',
        'kind': 'paragraph',
        'confidence': 0.95,
      };

      final frame = WsEnvelope.fromJson(jsonMap);
      expect(frame, isA<BlockCompleteFrame>());
      final b = frame as BlockCompleteFrame;
      expect(b.pageIdx, 0);
      expect(b.blockIdx, 2);

      final bboxItem = b.toBBoxItem();
      expect(bboxItem.blockId, 'p0_b2');
      expect(bboxItem.confidence, 0.95);
      expect(bboxItem.isRevised, isFalse);
    });

    test('Parses block_retry and block_revised frames', () {
      final retryJson = {
        'type': 'block_retry',
        'page_idx': 0,
        'block_idx': 5,
        'attempt': 2,
        'confidence': 0.52,
        'target': 0.85,
      };

      final retryFrame = WsEnvelope.fromJson(retryJson) as BlockRetryFrame;
      expect(retryFrame.attempt, 2);
      expect(retryFrame.blockKey, 'p0_b5');

      final revisedJson = {
        'type': 'block_revised',
        'page_idx': 0,
        'block_idx': 5,
        'attempt': 2,
        'bbox': [0.1, 0.1, 0.9, 0.9],
        'text': 'Corrected text',
        'kind': 'paragraph',
        'confidence': 0.94,
      };

      final revisedFrame =
          WsEnvelope.fromJson(revisedJson) as BlockRevisedFrame;
      final revisedItem = revisedFrame.toBBoxItem();
      expect(revisedItem.isRevised, isTrue);
      expect(revisedItem.confidence, 0.94);
    });

    test('Parses quality_summary frame', () {
      final jsonMap = {
        'type': 'quality_summary',
        'scope': 'document',
        'target': 0.85,
        'avg_confidence': 0.93,
        'repaired_count': 3,
        'below_target_count': 0,
      };

      final frame = WsEnvelope.fromJson(jsonMap) as QualitySummaryFrame;
      expect(frame.summary.target, 0.85);
      expect(frame.summary.repairedCount, 3);
      expect(frame.summary.avgConfidencePercent, 93);
    });
  });

  group('ProcessSettings Tests', () {
    test('Default values match standard defaults', () {
      const s = ProcessSettings();
      expect(s.dpi, 192);
      expect(s.concurrency, 3);
      expect(s.pipelineMode, PipelineMode.hybrid);
      expect(s.qualityRepairEnabled, isTrue);
      expect(s.qualityTarget, isNull);
      expect(s.maxRetries, 2);
    });
  });

  group('TrustSummary Tests', () {
    test('parses from raw header string', () {
      const rawHeader =
          '{"block_count": 10, "scored_count": 10, "flagged_count": 0, "average": 0.96}';
      final summary = TrustSummary.tryParseHeader(rawHeader);

      expect(summary, isNotNull);
      expect(summary!.blockCount, 10);
      expect(summary.scoredCount, 10);
      expect(summary.flaggedCount, 0);
      expect(summary.average, 0.96);
    });
  });
}
