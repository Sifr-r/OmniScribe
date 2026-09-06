/// OmniScribe API and WebSocket constants matching FastAPI routes and header contracts.
abstract final class ApiConstants {
  // Base URLs
  static const String defaultBaseUrl = 'http://127.0.0.1:8000';
  static const String defaultWsUrl = 'ws://127.0.0.1:8000';

  // Timeouts
  static const Duration defaultConnectTimeout = Duration(seconds: 30);
  static const Duration defaultReceiveTimeout = Duration(minutes: 5);
  static const Duration defaultSendTimeout = Duration(minutes: 5);
  static const Duration defaultOcrReceiveTimeout = Duration(minutes: 30);

  // Health Endpoints
  static const String apiHealth = '/api/health';

  // Config Endpoints
  static const String config = '/api/config';
  static const String configTranscription = '/api/config/transcription';

  // OCR Endpoints
  static const String processSync = '/api/process';
  static const String processAsync = '/api/process/async';
  static String processStatus(String jobId) => '/api/process/status/$jobId';
  static String processEvents(String jobId) => '/api/process/$jobId/events';

  // Jobs Endpoints
  static const String jobs = '/api/jobs';
  static String jobResult(String jobId) => '/api/jobs/$jobId/result';
  static String jobPagePreview(String jobId, int pageIndex) =>
      '/api/jobs/$jobId/pages/$pageIndex/preview';
  static String cancelJob(String jobId) => '/api/jobs/$jobId/cancel';

  // Progress & WebSocket Endpoints
  static const String progressSession = '/api/progress/session';
  static String cancelProgress(String channelId) =>
      '/api/progress/cancel/$channelId';
  static String wsProgress(String channelId) => '/ws/$channelId';

  // Provider Endpoints
  static const String providers = '/api/providers';
  static String providerDetails(String providerId) =>
      '/api/providers/$providerId';
  static String providerModels(String providerId) =>
      '/api/providers/$providerId/models';
  static const String setActiveProvider = '/api/providers/active';
  static const String validateProvider = '/api/providers/validate';

  // Artifact & Text Endpoints
  static String textArtifact(String artifactId) => '/api/text/$artifactId';

  // Feature Endpoints - Translation
  static const String translate = '/api/translate';
  static const String translateAsync = '/api/translate/async';
  static String translationStatus(String jobId) =>
      '/api/translate/status/$jobId';
  static const String translateNllb = '/api/translate/nllb';

  // Feature Endpoints - Transcription
  static const String transcribe = '/api/transcribe';

  // Feature Endpoints - Extraction & Export
  static const String extract = '/api/extract';
  static const String exportDocument = '/api/export/document';
  static const String exportDocx = '/api/export/docx';
  static const String exportHtml = '/api/export/html';
  static const String exportDocxTree = '/api/export/docx-tree';
  static const String exportBlockTree = '/api/export/blocktree';

  // Feature Endpoints - Glossary
  static const String glossaryLibrary = '/api/glossary/library';
  static String glossaryEntries(String libraryId) =>
      '/api/glossary/library/$libraryId/entries';
  static const String glossaryMerged = '/api/glossary/library/merged';
  static const String glossaryPreview = '/api/glossary/library/preview';
  static String glossaryToggle(String libraryId) =>
      '/api/glossary/library/$libraryId/enable';
  static String glossaryDelete(String libraryId) =>
      '/api/glossary/library/$libraryId';
  static const String glossaryReorder = '/api/glossary/library/reorder';
  static const String glossaryImport = '/api/glossary/import';
  static const String glossaryImportUrl = '/api/glossary/import/url';

  // Document Preview Endpoint
  static const String documentPreview = '/api/documents/preview';

  // Response Header Keys (Exposed by FastAPI CORS middleware)
  static const String headerTextArtifactId = 'x-text-artifact-id';
  static const String headerTextArtifactToken = 'x-text-artifact-token';
  static const String headerDocumentTrust = 'x-document-trust';
  static const String headerDocumentQuality = 'x-document-quality';
  static const String headerDocumentStructure = 'x-document-structure';
  static const String headerDocumentSections = 'x-document-sections';
  static const String headerMetadataArtifactId =
      'x-document-metadata-artifact-id';
  static const String headerMetadataArtifactToken =
      'x-document-metadata-artifact-token';
  static const String headerTotalPages = 'x-total-pages';
  static const String headerPageWidth = 'x-page-width';
  static const String headerPageHeight = 'x-page-height';
  static const String headerDocumentId = 'x-document-id';
}
