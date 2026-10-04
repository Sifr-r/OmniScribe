/// Smart preset configurations for OmniScribe OCR pipeline settings.
library;

import 'package:omniscribe_client/features/workstation/process_settings.dart';

/// Immutable preset model representing pre-tuned OCR processing profiles.
class SmartPreset {
  const SmartPreset({
    required this.id,
    required this.title,
    required this.description,
    required this.iconName,
    required this.badgeLabel,
    this.isPopular = false,
  });

  /// Unique identifier of the preset (e.g. 'standard', 'receipt').
  final String id;

  /// Human-readable title of the preset.
  final String title;

  /// Brief 1-line description of the preset's target use case.
  final String description;

  /// Icon identifier key ('document', 'receipt', 'handwriting', 'history', 'bolt', 'target').
  final String iconName;

  /// Short badge label (e.g. 'Recommended', 'Tables & Numbers').
  final String badgeLabel;

  /// Whether this preset is highlighted as popular/recommended.
  final bool isPopular;

  /// Standard document processing preset.
  static const standard = SmartPreset(
    id: 'standard',
    title: 'Standard Document',
    description:
        'Balanced general-purpose OCR with reading order and structure analysis.',
    iconName: 'document',
    badgeLabel: 'Recommended',
    isPopular: true,
  );

  /// Receipts and invoices preset.
  static const receipt = SmartPreset(
    id: 'receipt',
    title: 'Receipts & Invoices',
    description:
        'Dense numbers, table extraction, and contrast boost for receipts and bills.',
    iconName: 'receipt',
    badgeLabel: 'Tables & Numbers',
    isPopular: true,
  );

  /// Handwritten notes preset.
  static const handwriting = SmartPreset(
    id: 'handwriting',
    title: 'Handwritten Notes',
    description:
        'Binarization, contrast normalization, and self-correction tuned for handwritten notes.',
    iconName: 'handwriting',
    badgeLabel: 'Ink & Notes',
    isPopular: false,
  );

  /// Archival / degraded scan preset.
  static const historical = SmartPreset(
    id: 'historical',
    title: 'Archival / Faded Scan',
    description:
        'Full image restoration (deskew, denoise, contrast, crop cleanup) for faded scans.',
    iconName: 'history',
    badgeLabel: 'Auto-Cleanup',
    isPopular: false,
  );

  /// Lightning fast grounded OCR preset.
  static const fast = SmartPreset(
    id: 'fast',
    title: 'Lightning Fast',
    description:
        'Ultra-fast grounded bounding-box OCR without refine or quality repair loops.',
    iconName: 'bolt',
    badgeLabel: 'Fastest',
    isPopular: false,
  );

  /// Deep high-accuracy preset.
  static const deep = SmartPreset(
    id: 'deep',
    title: 'Deep High-Accuracy',
    description:
        'Maximum accuracy with dual-engine verification, self-correction, and all processors.',
    iconName: 'target',
    badgeLabel: 'Highest Quality',
    isPopular: false,
  );

  /// All available smart presets in display order.
  static const List<SmartPreset> allPresets = [
    standard,
    receipt,
    handwriting,
    historical,
    fast,
    deep,
  ];

  /// Retrieves a preset by [id], defaulting to [standard] if not found.
  static SmartPreset fromId(String id) {
    for (final preset in allPresets) {
      if (preset.id == id) return preset;
    }
    return standard;
  }

  /// Inspects [filename] and returns a suggested [SmartPreset].
  ///
  /// - If filename contains 'receipt', 'invoice', or 'bill' -> [receipt]
  /// - If filename contains 'note', 'handwritten', or 'letter' -> [handwriting]
  /// - If filename contains 'archive', 'old', 'scan', or 'history' -> [historical]
  /// - Otherwise -> [standard]
  static SmartPreset suggestForFilename(String? filename) {
    if (filename == null || filename.isEmpty) return standard;
    final lower = filename.toLowerCase();
    if (lower.contains('receipt') ||
        lower.contains('invoice') ||
        lower.contains('bill')) {
      return receipt;
    }
    if (lower.contains('note') ||
        lower.contains('handwritten') ||
        lower.contains('letter')) {
      return handwriting;
    }
    if (lower.contains('archive') ||
        lower.contains('old') ||
        lower.contains('scan') ||
        lower.contains('history')) {
      return historical;
    }
    return standard;
  }

  /// Alias for [applyToSettings].
  ProcessSettings apply(ProcessSettings current) => applyToSettings(current);

  /// Returns a modified copy of [current] with parameters configured for this preset.
  ProcessSettings applyToSettings(ProcessSettings current) {
    switch (id) {
      case 'receipt':
        return current.copyWith(
          pipelineMode: PipelineMode.hybrid,
          denseMode: DenseMode.on,
          denseThreshold: 50,
          refine: true,
          selfCorrection: false,
          binarize: false,
          dualEngine: false,
          preprocessPages: true,
          orientationDetection: false,
          deskew: true,
          denoise: false,
          normalizeContrast: true,
          cropCleanup: false,
          handwritingHint: false,
          documentProcessors: const [
            DocumentProcessorName.readingOrder,
            DocumentProcessorName.qualityAnalysis,
            DocumentProcessorName.tableExtraction,
            DocumentProcessorName.layoutEnrichment,
          ],
          qualityLoopEnabled: true,
          qualityTarget: 0.88,
          qualityMaxRetries: 3,
        );
      case 'handwriting':
        return current.copyWith(
          pipelineMode: PipelineMode.hybrid,
          denseMode: DenseMode.on,
          denseThreshold: 80,
          refine: true,
          selfCorrection: true,
          binarize: true,
          dualEngine: false,
          preprocessPages: true,
          orientationDetection: false,
          deskew: false,
          denoise: false,
          normalizeContrast: true,
          cropCleanup: false,
          handwritingHint: true,
          documentProcessors: const [
            DocumentProcessorName.readingOrder,
            DocumentProcessorName.structureAnalysis,
          ],
          qualityLoopEnabled: true,
          qualityTarget: 0.85,
          qualityMaxRetries: 2,
        );
      case 'historical':
        return current.copyWith(
          pipelineMode: PipelineMode.hybrid,
          denseMode: DenseMode.auto,
          denseThreshold: 150,
          refine: true,
          selfCorrection: false,
          binarize: false,
          dualEngine: false,
          preprocessPages: true,
          orientationDetection: false,
          deskew: true,
          denoise: true,
          normalizeContrast: true,
          cropCleanup: true,
          handwritingHint: false,
          documentProcessors: const [
            DocumentProcessorName.readingOrder,
            DocumentProcessorName.qualityAnalysis,
            DocumentProcessorName.layoutEnrichment,
          ],
          qualityLoopEnabled: true,
          qualityTarget: 0.90,
          qualityMaxRetries: 3,
        );
      case 'fast':
        return current.copyWith(
          pipelineMode: PipelineMode.grounded,
          denseMode: DenseMode.off,
          refine: false,
          selfCorrection: false,
          binarize: false,
          dualEngine: false,
          preprocessPages: false,
          orientationDetection: false,
          deskew: false,
          denoise: false,
          normalizeContrast: false,
          cropCleanup: false,
          handwritingHint: false,
          documentProcessors: const [
            DocumentProcessorName.readingOrder,
          ],
          qualityLoopEnabled: false,
        );
      case 'deep':
        return current.copyWith(
          pipelineMode: PipelineMode.hybrid,
          denseMode: DenseMode.auto,
          denseThreshold: 150,
          refine: true,
          selfCorrection: true,
          binarize: false,
          dualEngine: true,
          preprocessPages: true,
          orientationDetection: false,
          deskew: true,
          denoise: false,
          normalizeContrast: true,
          cropCleanup: false,
          handwritingHint: false,
          documentProcessors: const [
            DocumentProcessorName.readingOrder,
            DocumentProcessorName.qualityAnalysis,
            DocumentProcessorName.structureAnalysis,
            DocumentProcessorName.sectionAnalysis,
            DocumentProcessorName.layoutEnrichment,
            DocumentProcessorName.tableExtraction,
          ],
          qualityLoopEnabled: true,
          qualityTarget: 0.92,
          qualityMaxRetries: 3,
        );
      case 'standard':
      default:
        return current.copyWith(
          pipelineMode: PipelineMode.hybrid,
          denseMode: DenseMode.auto,
          denseThreshold: 150,
          refine: true,
          selfCorrection: false,
          binarize: false,
          dualEngine: false,
          preprocessPages: false,
          orientationDetection: false,
          deskew: false,
          denoise: false,
          normalizeContrast: false,
          cropCleanup: false,
          handwritingHint: false,
          documentProcessors: const [
            DocumentProcessorName.readingOrder,
            DocumentProcessorName.structureAnalysis,
          ],
          qualityLoopEnabled: true,
          qualityTarget: 0.85,
          qualityMaxRetries: 2,
        );
    }
  }

  /// Detects whether [settings] match one of the predefined [allPresets],
  /// or returns `null` if the settings represent a custom configuration.
  static SmartPreset? detectActivePreset(ProcessSettings settings) {
    for (final preset in allPresets) {
      if (_matchesPreset(preset, settings)) {
        return preset;
      }
    }
    return null;
  }

  static bool _matchesPreset(SmartPreset preset, ProcessSettings settings) {
    final expected = preset.applyToSettings(settings);

    if (settings.pipelineMode != expected.pipelineMode) return false;
    if (settings.denseMode != expected.denseMode) return false;
    if (settings.denseMode != DenseMode.off &&
        settings.denseThreshold != expected.denseThreshold) {
      return false;
    }
    if (settings.refine != expected.refine) return false;
    if (settings.selfCorrection != expected.selfCorrection) return false;
    if (settings.binarize != expected.binarize) return false;
    if (settings.dualEngine != expected.dualEngine) return false;
    if (settings.preprocessPages != expected.preprocessPages) return false;
    if (settings.orientationDetection != expected.orientationDetection) {
      return false;
    }
    if (settings.deskew != expected.deskew) return false;
    if (settings.denoise != expected.denoise) return false;
    if (settings.normalizeContrast != expected.normalizeContrast) return false;
    if (settings.cropCleanup != expected.cropCleanup) return false;
    if ((settings.handwritingHint ?? false) !=
        (expected.handwritingHint ?? false)) {
      return false;
    }
    if (settings.qualityRepairEnabled != expected.qualityRepairEnabled) {
      return false;
    }
    if (expected.qualityRepairEnabled) {
      final actualTarget = settings.qualityTarget ?? 0.85;
      final expectedTarget = expected.qualityTarget ?? 0.85;
      if ((actualTarget - expectedTarget).abs() > 0.001) return false;
      if (settings.maxRetries != expected.maxRetries) return false;
    }
    if (!_areProcessorListsEqual(
        settings.documentProcessors, expected.documentProcessors)) {
      return false;
    }
    return true;
  }

  static bool _areProcessorListsEqual(
    List<DocumentProcessorName> a,
    List<DocumentProcessorName> b,
  ) {
    if (a.length != b.length) return false;
    final setA = a.toSet();
    final setB = b.toSet();
    return setA.length == setB.length && setA.containsAll(setB);
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is SmartPreset &&
          runtimeType == other.runtimeType &&
          id == other.id;

  @override
  int get hashCode => id.hashCode;

  @override
  String toString() => 'SmartPreset(id: $id, title: $title)';
}
