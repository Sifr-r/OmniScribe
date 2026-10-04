import 'dart:convert';

import 'package:collection/collection.dart';
import 'package:flutter/foundation.dart';

/// Immutable state for the Structured Information Extraction feature vertical.
@immutable
class ExtractionState {
  ExtractionState({
    this.inputText = '',
    String? customSchema,
    this.selectedTemplate = 'invoice',
    this.isExtracting = false,
    this.extractedData,
    this.statusMessage,
    this.error,
  }) : customSchema = customSchema ?? defaultCustomSchema;

  ExtractionState.initial()
      : inputText = '',
        customSchema = defaultCustomSchema,
        selectedTemplate = 'invoice',
        isExtracting = false,
        extractedData = null,
        statusMessage = null,
        error = null;

  static final String defaultCustomSchema =
      const JsonEncoder.withIndent('  ').convert(<String, dynamic>{
    'invoice_number': 'string',
    'vendor_name': 'string',
    'total_amount': 'number',
    'tax_amount': 'number',
    'date': 'string',
    'line_items': <Map<String, dynamic>>[
      {
        'description': 'string',
        'quantity': 'number',
        'unit_price': 'number',
      }
    ],
  });

  final String inputText;
  final String customSchema;
  final String selectedTemplate;
  final bool isExtracting;
  final dynamic extractedData;
  final String? statusMessage;
  final String? error;

  ExtractionState copyWith({
    String? inputText,
    String? customSchema,
    String? selectedTemplate,
    bool? isExtracting,
    dynamic extractedData,
    String? statusMessage,
    String? error,
    bool clearExtractedData = false,
    bool clearStatusMessage = false,
    bool clearError = false,
  }) {
    return ExtractionState(
      inputText: inputText ?? this.inputText,
      customSchema: customSchema ?? this.customSchema,
      selectedTemplate: selectedTemplate ?? this.selectedTemplate,
      isExtracting: isExtracting ?? this.isExtracting,
      extractedData:
          clearExtractedData ? null : (extractedData ?? this.extractedData),
      statusMessage:
          clearStatusMessage ? null : (statusMessage ?? this.statusMessage),
      error: clearError ? null : (error ?? this.error),
    );
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is ExtractionState &&
        other.inputText == inputText &&
        other.customSchema == customSchema &&
        other.selectedTemplate == selectedTemplate &&
        other.isExtracting == isExtracting &&
        const DeepCollectionEquality()
            .equals(other.extractedData, extractedData) &&
        other.statusMessage == statusMessage &&
        other.error == error;
  }

  @override
  int get hashCode => Object.hash(
        inputText,
        customSchema,
        selectedTemplate,
        isExtracting,
        // Use the same collection semantics as equality.
        const DeepCollectionEquality().hash(extractedData),
        statusMessage,
        error,
      );
}
