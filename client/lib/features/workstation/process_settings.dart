/// Process settings, config update, and related domain enums matching OmniScribe API schemas.
library;

/// Pipeline processing strategy.
enum PipelineMode {
  hybrid('hybrid', 'Hybrid (OCR + VLM)'),
  grounded('grounded', 'Grounded BBox');

  const PipelineMode(this.value, [this.label = '']);
  final String value;
  final String label;

  static PipelineMode fromString(String? value) {
    if (value == null) return PipelineMode.hybrid;
    for (final mode in PipelineMode.values) {
      if (mode.value == value) return mode;
    }
    return PipelineMode.hybrid;
  }
}

/// Dense OCR mode toggle.
enum DenseMode {
  auto('auto', 'Auto'),
  on('on', 'On'),
  off('off', 'Off'),
  always('always', 'Always'),
  never('never', 'Never');

  const DenseMode(this.value, [this.label = '']);
  final String value;
  final String label;

  static DenseMode fromString(String? value) {
    if (value == null) return DenseMode.auto;
    for (final mode in DenseMode.values) {
      if (mode.value == value) return mode;
    }
    return DenseMode.auto;
  }
}

/// Spellcheck dictionary modes.
enum SpellcheckMode {
  none('none', 'None'),
  enUS('en-US', 'English (US)'),
  ar('ar', 'Arabic'),
  de('de', 'German'),
  es('es', 'Spanish'),
  fr('fr', 'French');

  const SpellcheckMode(this.value, [this.label = '']);
  final String value;
  final String label;

  static SpellcheckMode fromString(String? value) {
    if (value == null) return SpellcheckMode.none;
    for (final mode in SpellcheckMode.values) {
      if (mode.value == value) return mode;
    }
    return SpellcheckMode.none;
  }
}

/// Document processor module names.
enum DocumentProcessorName {
  readingOrder('reading_order'),
  qualityAnalysis('quality_analysis'),
  structureAnalysis('structure_analysis'),
  sectionAnalysis('section_analysis'),
  layoutEnrichment('layout_enrichment'),
  tableExtraction('table_extraction');

  const DocumentProcessorName(this.value);
  final String value;

  static DocumentProcessorName fromString(String value) {
    for (final proc in DocumentProcessorName.values) {
      if (proc.value == value) return proc;
    }
    return DocumentProcessorName.readingOrder;
  }

  static DocumentProcessorName? tryFromString(String value) {
    for (final proc in DocumentProcessorName.values) {
      if (proc.value == value) return proc;
    }
    return null;
  }
}

/// Document Processor descriptor
class DocumentProcessorInfo {
  final String id;
  final String label;
  final String description;

  const DocumentProcessorInfo({
    required this.id,
    required this.label,
    required this.description,
  });

  static const List<DocumentProcessorInfo> all = [
    DocumentProcessorInfo(
      id: 'reading_order',
      label: 'Reading Order',
      description:
          'Determines the natural human reading sequence across multiple columns and blocks.',
    ),
    DocumentProcessorInfo(
      id: 'quality_analysis',
      label: 'Quality Analysis',
      description:
          'Scores block clarity, contrast, and OCR character-level confidence.',
    ),
    DocumentProcessorInfo(
      id: 'structure_analysis',
      label: 'Structure Analysis',
      description:
          'Detects hierarchical document structure: headers, footers, body, lists.',
    ),
    DocumentProcessorInfo(
      id: 'section_analysis',
      label: 'Section Analysis',
      description:
          'Segments text into coherent semantic sections and chapter boundaries.',
    ),
    DocumentProcessorInfo(
      id: 'layout_enrichment',
      label: 'Layout Enrichment',
      description:
          'Enriches bounding boxes with semantic typography and alignment metadata.',
    ),
    DocumentProcessorInfo(
      id: 'table_extraction',
      label: 'Table Extraction',
      description:
          'Extracts structured table grids and cell relations into clean Markdown/JSON.',
    ),
  ];
}

/// Full runtime settings for OCR processing requests.
class ProcessSettings {
  const ProcessSettings({
    this.apiBase = 'http://localhost:1234/v1',
    this.apiKey = '',
    this.model = 'allenai/olmocr-2-7b',
    this.pipelineMode = PipelineMode.hybrid,
    this.dpi = 192,
    this.concurrency = 3,
    this.denseMode = DenseMode.auto,
    this.denseThreshold = 150,
    this.pages,
    this.refine = true,
    this.maxImageDim = 1024,
    this.selfCorrection = false,
    this.binarize = false,
    this.dualEngine = false,
    this.spellcheck = SpellcheckMode.none,
    this.crossPage = false,
    this.preprocessPages = false,
    this.orientationDetection = false,
    this.deskew = false,
    this.denoise = false,
    this.normalizeContrast = false,
    this.cropCleanup = false,
    this.qualityRouting = false,
    this.handwritingHint,
    this.confidenceThreshold,
    this.documentProcessors = const [],
    this.chunkPages,
    this.qualityLoopEnabled,
    this.qualityTarget,
    this.qualityMaxRetries,
    this.useAsync = false,
    this.whitespaceRecall = true,
    this.textLayerRecall = true,
  });

  final String apiBase;
  final String apiKey;
  final String model;
  final PipelineMode pipelineMode;
  final int dpi;
  final int concurrency;
  final DenseMode denseMode;
  final int denseThreshold;
  final String? pages;
  final bool refine;
  final int maxImageDim;
  final bool selfCorrection;
  final bool binarize;
  final bool dualEngine;
  final SpellcheckMode spellcheck;
  final bool crossPage;
  final bool preprocessPages;
  final bool orientationDetection;
  final bool deskew;
  final bool denoise;
  final bool normalizeContrast;
  final bool cropCleanup;
  final bool qualityRouting;
  final bool? handwritingHint;
  final double? confidenceThreshold;
  final List<DocumentProcessorName> documentProcessors;
  final int? chunkPages;
  final bool? qualityLoopEnabled;
  final double? qualityTarget;
  final int? qualityMaxRetries;
  final bool useAsync;
  final bool whitespaceRecall;
  final bool textLayerRecall;

  bool get qualityRepairEnabled => qualityLoopEnabled ?? true;
  int get maxRetries => qualityMaxRetries ?? 2;

  factory ProcessSettings.defaultSettings({
    String apiBase = 'http://localhost:1234/v1',
    String apiKey = '',
    String model = 'allenai/olmocr-2-7b',
  }) {
    return ProcessSettings(
      apiBase: apiBase,
      apiKey: apiKey,
      model: model,
      pipelineMode: PipelineMode.hybrid,
      dpi: 192,
      concurrency: 3,
      denseMode: DenseMode.auto,
      denseThreshold: 150,
      refine: true,
      maxImageDim: 1024,
      selfCorrection: false,
      binarize: false,
      dualEngine: false,
      spellcheck: SpellcheckMode.none,
      crossPage: false,
      preprocessPages: false,
      orientationDetection: false,
      deskew: false,
      denoise: false,
      normalizeContrast: false,
      cropCleanup: false,
      qualityRouting: false,
      documentProcessors: const [],
      qualityLoopEnabled: true,
      qualityTarget: 0.85,
      qualityMaxRetries: 2,
      useAsync: false,
      whitespaceRecall: true,
      textLayerRecall: true,
    );
  }

  ProcessSettings copyWith({
    String? apiBase,
    String? apiKey,
    String? model,
    PipelineMode? pipelineMode,
    int? dpi,
    int? concurrency,
    DenseMode? denseMode,
    int? denseThreshold,
    String? pages,
    bool? refine,
    int? maxImageDim,
    bool? selfCorrection,
    bool? binarize,
    bool? dualEngine,
    SpellcheckMode? spellcheck,
    bool? crossPage,
    bool? preprocessPages,
    bool? orientationDetection,
    bool? deskew,
    bool? denoise,
    bool? normalizeContrast,
    bool? cropCleanup,
    bool? qualityRouting,
    bool? handwritingHint,
    double? confidenceThreshold,
    List<DocumentProcessorName>? documentProcessors,
    int? chunkPages,
    bool? qualityLoopEnabled,
    bool? qualityRepairEnabled,
    double? qualityTarget,
    int? qualityMaxRetries,
    int? maxRetries,
    bool? useAsync,
    bool? whitespaceRecall,
    bool? textLayerRecall,
  }) {
    return ProcessSettings(
      apiBase: apiBase ?? this.apiBase,
      apiKey: apiKey ?? this.apiKey,
      model: model ?? this.model,
      pipelineMode: pipelineMode ?? this.pipelineMode,
      dpi: dpi ?? this.dpi,
      concurrency: concurrency ?? this.concurrency,
      denseMode: denseMode ?? this.denseMode,
      denseThreshold: denseThreshold ?? this.denseThreshold,
      pages: pages ?? this.pages,
      refine: refine ?? this.refine,
      maxImageDim: maxImageDim ?? this.maxImageDim,
      selfCorrection: selfCorrection ?? this.selfCorrection,
      binarize: binarize ?? this.binarize,
      dualEngine: dualEngine ?? this.dualEngine,
      spellcheck: spellcheck ?? this.spellcheck,
      crossPage: crossPage ?? this.crossPage,
      preprocessPages: preprocessPages ?? this.preprocessPages,
      orientationDetection: orientationDetection ?? this.orientationDetection,
      deskew: deskew ?? this.deskew,
      denoise: denoise ?? this.denoise,
      normalizeContrast: normalizeContrast ?? this.normalizeContrast,
      cropCleanup: cropCleanup ?? this.cropCleanup,
      qualityRouting: qualityRouting ?? this.qualityRouting,
      handwritingHint: handwritingHint ?? this.handwritingHint,
      confidenceThreshold: confidenceThreshold ?? this.confidenceThreshold,
      documentProcessors: documentProcessors ?? this.documentProcessors,
      chunkPages: chunkPages ?? this.chunkPages,
      qualityLoopEnabled: qualityRepairEnabled ??
          (qualityLoopEnabled ?? this.qualityLoopEnabled),
      qualityTarget: qualityTarget ?? this.qualityTarget,
      qualityMaxRetries:
          maxRetries ?? (qualityMaxRetries ?? this.qualityMaxRetries),
      useAsync: useAsync ?? this.useAsync,
      whitespaceRecall: whitespaceRecall ?? this.whitespaceRecall,
      textLayerRecall: textLayerRecall ?? this.textLayerRecall,
    );
  }

  factory ProcessSettings.fromJson(Map<String, dynamic> json) {
    final procsRaw = json['document_processors'];
    final procs = <DocumentProcessorName>[];
    if (procsRaw is List) {
      for (final item in procsRaw) {
        final parsed = DocumentProcessorName.tryFromString(item.toString());
        if (parsed != null) procs.add(parsed);
      }
    }

    return ProcessSettings(
      apiBase: json['api_base']?.toString() ?? 'http://localhost:1234/v1',
      apiKey: json['api_key']?.toString() ?? '',
      model: json['model']?.toString() ?? 'allenai/olmocr-2-7b',
      pipelineMode: PipelineMode.fromString(json['pipeline_mode']?.toString()),
      dpi: (json['dpi'] as num?)?.toInt() ?? 192,
      concurrency: (json['concurrency'] as num?)?.toInt() ?? 3,
      denseMode: DenseMode.fromString(json['dense_mode']?.toString()),
      denseThreshold: (json['dense_threshold'] as num?)?.toInt() ?? 150,
      pages: json['pages']?.toString(),
      refine: json['refine'] as bool? ?? true,
      maxImageDim: (json['max_image_dim'] as num?)?.toInt() ?? 1024,
      selfCorrection: json['self_correction'] as bool? ?? false,
      binarize: json['binarize'] as bool? ?? false,
      dualEngine: json['dual_engine'] as bool? ?? false,
      spellcheck: SpellcheckMode.fromString(json['spellcheck']?.toString()),
      crossPage: json['cross_page'] as bool? ?? false,
      preprocessPages: json['preprocess_pages'] as bool? ?? false,
      orientationDetection: json['orientation_detection'] as bool? ?? false,
      deskew: json['deskew'] as bool? ?? false,
      denoise: json['denoise'] as bool? ?? false,
      normalizeContrast: json['normalize_contrast'] as bool? ?? false,
      cropCleanup: json['crop_cleanup'] as bool? ?? false,
      qualityRouting: json['quality_routing'] as bool? ?? false,
      handwritingHint: json['handwriting_hint'] as bool?,
      confidenceThreshold: (json['confidence_threshold'] as num?)?.toDouble(),
      documentProcessors: procs,
      chunkPages: (json['chunk_pages'] as num?)?.toInt(),
      qualityLoopEnabled: json['quality_loop_enabled'] as bool?,
      qualityTarget: (json['quality_target'] as num?)?.toDouble(),
      qualityMaxRetries: (json['quality_max_retries'] as num?)?.toInt(),
      useAsync: json['use_async'] as bool? ?? false,
      whitespaceRecall: json['whitespace_recall'] as bool? ?? true,
      textLayerRecall: json['text_layer_recall'] as bool? ?? true,
    );
  }

  Map<String, dynamic> toJson() {
    final map = <String, dynamic>{
      'api_base': apiBase,
      'api_key': apiKey,
      'model': model,
      'pipeline_mode': pipelineMode.value,
      'dpi': dpi,
      'concurrency': concurrency,
      'dense_mode': denseMode.value,
      'dense_threshold': denseThreshold,
      'refine': refine,
      'max_image_dim': maxImageDim,
      'self_correction': selfCorrection,
      'binarize': binarize,
      'dual_engine': dualEngine,
      'spellcheck': spellcheck.value,
      'cross_page': crossPage,
      'preprocess_pages': preprocessPages,
      'orientation_detection': orientationDetection,
      'deskew': deskew,
      'denoise': denoise,
      'normalize_contrast': normalizeContrast,
      'crop_cleanup': cropCleanup,
      'quality_routing': qualityRouting,
      'document_processors': documentProcessors.map((p) => p.value).toList(),
      'use_async': useAsync,
      'whitespace_recall': whitespaceRecall,
      'text_layer_recall': textLayerRecall,
    };
    if (pages != null) map['pages'] = pages;
    if (handwritingHint != null) map['handwriting_hint'] = handwritingHint;
    if (confidenceThreshold != null) {
      map['confidence_threshold'] = confidenceThreshold;
    }
    if (chunkPages != null) map['chunk_pages'] = chunkPages;
    if (qualityLoopEnabled != null) {
      map['quality_loop_enabled'] = qualityLoopEnabled;
    }
    if (qualityTarget != null) map['quality_target'] = qualityTarget;
    if (qualityMaxRetries != null) {
      map['quality_max_retries'] = qualityMaxRetries;
    }
    return map;
  }
}
