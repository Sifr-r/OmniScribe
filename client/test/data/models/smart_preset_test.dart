import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/data/models/models.dart';

void main() {
  group('SmartPreset Definition & Registry Tests', () {
    test('allPresets contains all 6 predefined presets in correct order', () {
      expect(SmartPreset.allPresets.length, 6);
      expect(
        SmartPreset.allPresets.map((p) => p.id).toList(),
        ['standard', 'receipt', 'handwriting', 'historical', 'fast', 'deep'],
      );
    });

    test('All presets have non-empty metadata fields and unique IDs', () {
      final ids = <String>{};
      for (final preset in SmartPreset.allPresets) {
        expect(preset.id, isNotEmpty);
        expect(preset.title, isNotEmpty);
        expect(preset.description, isNotEmpty);
        expect(preset.iconName, isNotEmpty);
        expect(preset.badgeLabel, isNotEmpty);
        expect(ids.add(preset.id), isTrue,
            reason: 'Duplicate preset ID: ${preset.id}');
      }
    });

    test('isPopular is configured accurately for popular presets', () {
      expect(SmartPreset.standard.isPopular, isTrue);
      expect(SmartPreset.receipt.isPopular, isTrue);
      expect(SmartPreset.handwriting.isPopular, isFalse);
      expect(SmartPreset.historical.isPopular, isFalse);
      expect(SmartPreset.fast.isPopular, isFalse);
      expect(SmartPreset.deep.isPopular, isFalse);
    });

    test('fromId returns the matching preset or defaults to standard', () {
      expect(SmartPreset.fromId('standard'), SmartPreset.standard);
      expect(SmartPreset.fromId('receipt'), SmartPreset.receipt);
      expect(SmartPreset.fromId('handwriting'), SmartPreset.handwriting);
      expect(SmartPreset.fromId('historical'), SmartPreset.historical);
      expect(SmartPreset.fromId('fast'), SmartPreset.fast);
      expect(SmartPreset.fromId('deep'), SmartPreset.deep);

      // Fallback behavior
      expect(SmartPreset.fromId('unknown_id'), SmartPreset.standard);
      expect(SmartPreset.fromId(''), SmartPreset.standard);
    });

    test('Equality and toString work as expected', () {
      const copyOfStandard = SmartPreset(
        id: 'standard',
        title: 'Standard Document',
        description: 'Different desc',
        iconName: 'document',
        badgeLabel: 'Recommended',
      );
      expect(SmartPreset.standard == copyOfStandard, isTrue);
      expect(SmartPreset.standard.hashCode, copyOfStandard.hashCode);
      expect(SmartPreset.standard.toString(), contains('standard'));
    });
  });

  group('SmartPreset suggestForFilename Tests', () {
    test('Suggests receipt preset for receipt/invoice/bill keywords', () {
      expect(SmartPreset.suggestForFilename('receipt_2026_08.pdf'),
          SmartPreset.receipt);
      expect(SmartPreset.suggestForFilename('invoice-10492.pdf'),
          SmartPreset.receipt);
      expect(SmartPreset.suggestForFilename('utility_bill.png'),
          SmartPreset.receipt);
      expect(SmartPreset.suggestForFilename('RESTAURANT_RECEIPT.JPG'),
          SmartPreset.receipt);
    });

    test('Suggests handwriting preset for note/handwritten/letter keywords',
        () {
      expect(SmartPreset.suggestForFilename('meeting_notes.pdf'),
          SmartPreset.handwriting);
      expect(SmartPreset.suggestForFilename('handwritten_journal.png'),
          SmartPreset.handwriting);
      expect(SmartPreset.suggestForFilename('archival_letter.tif'),
          SmartPreset.handwriting); // 'letter' matched
      expect(SmartPreset.suggestForFilename('LECTURE_NOTES.PDF'),
          SmartPreset.handwriting);
    });

    test('Suggests historical preset for archive/old/scan/history keywords',
        () {
      expect(SmartPreset.suggestForFilename('archive_deed_1920.pdf'),
          SmartPreset.historical);
      expect(SmartPreset.suggestForFilename('old_manuscript.tif'),
          SmartPreset.historical);
      expect(SmartPreset.suggestForFilename('scan_page_01.png'),
          SmartPreset.historical);
      expect(SmartPreset.suggestForFilename('history_census.pdf'),
          SmartPreset.historical);
      expect(SmartPreset.suggestForFilename('OLD_SCAN.JPG'),
          SmartPreset.historical);
    });

    test('Suggests standard preset for generic or unmatched filenames', () {
      expect(
          SmartPreset.suggestForFilename('document.pdf'), SmartPreset.standard);
      expect(SmartPreset.suggestForFilename('annual_report_2025.pdf'),
          SmartPreset.standard);
      expect(SmartPreset.suggestForFilename('research_paper.pdf'),
          SmartPreset.standard);
      expect(SmartPreset.suggestForFilename(''), SmartPreset.standard);
    });
  });

  group('SmartPreset applyToSettings Tests', () {
    const baseSettings = ProcessSettings(
      apiBase: 'http://custom-ocr.local:8000/v1',
      apiKey: 'secret_key_123',
      model: 'custom/model-v1',
      dpi: 300,
      concurrency: 4,
      pages: '1-5',
      spellcheck: SpellcheckMode.enUS,
      useAsync: true,
    );

    test('Preserves host, model, and non-preset settings when applying preset',
        () {
      final applied = SmartPreset.standard.applyToSettings(baseSettings);
      expect(applied.apiBase, 'http://custom-ocr.local:8000/v1');
      expect(applied.apiKey, 'secret_key_123');
      expect(applied.model, 'custom/model-v1');
      expect(applied.dpi, 300);
      expect(applied.concurrency, 4);
      expect(applied.pages, '1-5');
      expect(applied.spellcheck, SpellcheckMode.enUS);
      expect(applied.useAsync, isTrue);
    });

    test('Applies Standard preset parameters correctly', () {
      final s = SmartPreset.standard.applyToSettings(baseSettings);
      expect(s.pipelineMode, PipelineMode.hybrid);
      expect(s.denseMode, DenseMode.auto);
      expect(s.denseThreshold, 150);
      expect(s.refine, isTrue);
      expect(s.documentProcessors, [
        DocumentProcessorName.readingOrder,
        DocumentProcessorName.structureAnalysis,
      ]);
      expect(s.qualityLoopEnabled, isTrue);
      expect(s.qualityTarget, 0.85);
      expect(s.preprocessPages, isFalse);
      expect(s.selfCorrection, isFalse);
      expect(s.binarize, isFalse);
      expect(s.dualEngine, isFalse);
      expect(s.deskew, isFalse);
      expect(s.denoise, isFalse);
      expect(s.normalizeContrast, isFalse);
      expect(s.cropCleanup, isFalse);
      expect(s.handwritingHint, isFalse);
    });

    test('Applies Receipt preset parameters correctly', () {
      final s = SmartPreset.receipt.applyToSettings(baseSettings);
      expect(s.pipelineMode, PipelineMode.hybrid);
      expect(s.denseMode, DenseMode.on);
      expect(s.denseThreshold, 50);
      expect(s.refine, isTrue);
      expect(s.normalizeContrast, isTrue);
      expect(s.deskew, isTrue);
      expect(s.documentProcessors, [
        DocumentProcessorName.readingOrder,
        DocumentProcessorName.qualityAnalysis,
        DocumentProcessorName.tableExtraction,
        DocumentProcessorName.layoutEnrichment,
      ]);
      expect(s.qualityLoopEnabled, isTrue);
      expect(s.qualityTarget, 0.88);
      expect(s.qualityMaxRetries, 3);
      expect(s.preprocessPages, isTrue);
    });

    test('Applies Handwriting preset parameters correctly', () {
      final s = SmartPreset.handwriting.applyToSettings(baseSettings);
      expect(s.pipelineMode, PipelineMode.hybrid);
      expect(s.denseMode, DenseMode.on);
      expect(s.denseThreshold, 80);
      expect(s.refine, isTrue);
      expect(s.selfCorrection, isTrue);
      expect(s.binarize, isTrue);
      expect(s.normalizeContrast, isTrue);
      expect(s.documentProcessors, [
        DocumentProcessorName.readingOrder,
        DocumentProcessorName.structureAnalysis,
      ]);
      expect(s.qualityLoopEnabled, isTrue);
      expect(s.qualityTarget, 0.85);
      expect(s.handwritingHint, isTrue);
      expect(s.preprocessPages, isTrue);
    });

    test('Applies Historical preset parameters correctly', () {
      final s = SmartPreset.historical.applyToSettings(baseSettings);
      expect(s.pipelineMode, PipelineMode.hybrid);
      expect(s.denseMode, DenseMode.auto);
      expect(s.refine, isTrue);
      expect(s.deskew, isTrue);
      expect(s.denoise, isTrue);
      expect(s.normalizeContrast, isTrue);
      expect(s.cropCleanup, isTrue);
      expect(s.preprocessPages, isTrue);
      expect(s.documentProcessors, [
        DocumentProcessorName.readingOrder,
        DocumentProcessorName.qualityAnalysis,
        DocumentProcessorName.layoutEnrichment,
      ]);
      expect(s.qualityLoopEnabled, isTrue);
      expect(s.qualityTarget, 0.90);
      expect(s.qualityMaxRetries, 3);
    });

    test('Applies Fast preset parameters correctly', () {
      final s = SmartPreset.fast.applyToSettings(baseSettings);
      expect(s.pipelineMode, PipelineMode.grounded);
      expect(s.denseMode, DenseMode.off);
      expect(s.refine, isFalse);
      expect(s.qualityLoopEnabled, isFalse);
      expect(s.documentProcessors, [
        DocumentProcessorName.readingOrder,
      ]);
      expect(s.preprocessPages, isFalse);
      expect(s.selfCorrection, isFalse);
      expect(s.binarize, isFalse);
      expect(s.dualEngine, isFalse);
    });

    test('Applies Deep preset parameters correctly', () {
      final s = SmartPreset.deep.applyToSettings(baseSettings);
      expect(s.pipelineMode, PipelineMode.hybrid);
      expect(s.denseMode, DenseMode.auto);
      expect(s.refine, isTrue);
      expect(s.selfCorrection, isTrue);
      expect(s.dualEngine, isTrue);
      expect(s.normalizeContrast, isTrue);
      expect(s.deskew, isTrue);
      expect(s.documentProcessors, [
        DocumentProcessorName.readingOrder,
        DocumentProcessorName.qualityAnalysis,
        DocumentProcessorName.structureAnalysis,
        DocumentProcessorName.sectionAnalysis,
        DocumentProcessorName.layoutEnrichment,
        DocumentProcessorName.tableExtraction,
      ]);
      expect(s.qualityLoopEnabled, isTrue);
      expect(s.qualityTarget, 0.92);
      expect(s.qualityMaxRetries, 3);
      expect(s.preprocessPages, isTrue);
    });
  });

  group('SmartPreset detectActivePreset Tests', () {
    test('Detects active preset immediately after applying each preset', () {
      const initial = ProcessSettings();

      for (final preset in SmartPreset.allPresets) {
        final applied = preset.applyToSettings(initial);
        final detected = SmartPreset.detectActivePreset(applied);
        expect(detected, equals(preset),
            reason: 'Failed to detect active preset for ${preset.id}');
      }
    });

    test('Returns null when settings have custom modifications', () {
      final applied =
          SmartPreset.receipt.applyToSettings(const ProcessSettings());

      // Changing deskew to false breaks receipt preset match
      final custom1 = applied.copyWith(deskew: false);
      expect(SmartPreset.detectActivePreset(custom1), isNull);

      // Changing quality target breaks match
      final custom2 = applied.copyWith(qualityTarget: 0.99);
      expect(SmartPreset.detectActivePreset(custom2), isNull);

      // Changing processor list breaks match
      final custom3 = applied.copyWith(
        documentProcessors: [DocumentProcessorName.readingOrder],
      );
      expect(SmartPreset.detectActivePreset(custom3), isNull);
    });
  });
}
