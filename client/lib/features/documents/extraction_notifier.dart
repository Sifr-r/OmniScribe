import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'document_repository.dart';
import 'documents_models.dart';
import 'extraction_state.dart';

export 'extraction_state.dart';

final extractionProvider =
    NotifierProvider<ExtractionNotifier, ExtractionState>(
  ExtractionNotifier.new,
);

class ExtractionNotifier extends Notifier<ExtractionState> {
  DocumentRepository get _repo => ref.read(documentRepositoryProvider);

  @override
  ExtractionState build() {
    return ExtractionState.initial();
  }

  void setInputText(String text) {
    state = state.copyWith(inputText: text);
  }

  void setCustomSchema(String schema) {
    state = state.copyWith(customSchema: schema);
  }

  void setSelectedTemplate(String template) {
    state = state.copyWith(selectedTemplate: template);
  }

  void clearInputText() {
    state = state.copyWith(inputText: '');
  }

  void clearError() {
    state = state.copyWith(clearError: true);
  }

  Future<void> extract({
    String? model,
    String? apiBase,
    String? apiKey,
  }) async {
    if (state.isExtracting) return;
    final text = state.inputText.trim();
    if (text.isEmpty) {
      state = state.copyWith(
        error: 'Please enter or paste input text to extract.',
      );
      return;
    }

    state = state.copyWith(
      isExtracting: true,
      clearExtractedData: true,
      clearStatusMessage: true,
      clearError: true,
    );

    try {
      final req = ExtractionRequest(
        text: text,
        template: ExtractionTemplate.fromString(state.selectedTemplate),
        customPrompt: state.selectedTemplate == 'custom'
            ? state.customSchema.trim()
            : null,
        model: model,
        apiBase: apiBase,
        apiKey: apiKey,
      );

      final res = await _repo.extractStructuredData(req);
      if (!ref.mounted) return;
      state = state.copyWith(
        isExtracting: false,
        extractedData: res.extractedData,
        statusMessage: 'Extraction complete.',
      );
    } catch (e) {
      if (!ref.mounted) return;
      state = state.copyWith(
        isExtracting: false,
        error: e.toString(),
      );
    }
  }
}
