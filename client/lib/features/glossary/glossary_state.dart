import 'package:flutter/foundation.dart';
import 'package:omniscribe_client/features/glossary/glossary_models.dart';

/// Immutable state for the Terminology Glossary feature vertical.
@immutable
class GlossaryState {
  const GlossaryState({
    this.libraries = const <GlossaryListItem>[],
    this.selectedLibrary,
    this.entries = const <GlossaryEntry>[],
    this.mergedLexicon = const <String, String>{},
    this.activeViewIndex = 0,
    this.isLoading = false,
    this.error,
  });

  const GlossaryState.initial()
      : libraries = const <GlossaryListItem>[],
        selectedLibrary = null,
        entries = const <GlossaryEntry>[],
        mergedLexicon = const <String, String>{},
        activeViewIndex = 0,
        isLoading = false,
        error = null;

  final List<GlossaryListItem> libraries;
  final GlossaryListItem? selectedLibrary;
  final List<GlossaryEntry> entries;
  final Map<String, String> mergedLexicon;
  final int activeViewIndex;
  final bool isLoading;
  final String? error;

  GlossaryState copyWith({
    List<GlossaryListItem>? libraries,
    GlossaryListItem? selectedLibrary,
    List<GlossaryEntry>? entries,
    Map<String, String>? mergedLexicon,
    int? activeViewIndex,
    bool? isLoading,
    String? error,
    bool clearSelectedLibrary = false,
    bool clearError = false,
  }) {
    return GlossaryState(
      libraries: libraries ?? this.libraries,
      selectedLibrary: clearSelectedLibrary
          ? null
          : (selectedLibrary ?? this.selectedLibrary),
      entries: entries ?? this.entries,
      mergedLexicon: mergedLexicon ?? this.mergedLexicon,
      activeViewIndex: activeViewIndex ?? this.activeViewIndex,
      isLoading: isLoading ?? this.isLoading,
      error: clearError ? null : (error ?? this.error),
    );
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is GlossaryState &&
        listEquals(other.libraries, libraries) &&
        other.selectedLibrary == selectedLibrary &&
        listEquals(other.entries, entries) &&
        mapEquals(other.mergedLexicon, mergedLexicon) &&
        other.activeViewIndex == activeViewIndex &&
        other.isLoading == isLoading &&
        other.error == error;
  }

  @override
  int get hashCode => Object.hash(
        Object.hashAll(libraries),
        selectedLibrary,
        Object.hashAll(entries),
        Object.hashAll(mergedLexicon.entries),
        activeViewIndex,
        isLoading,
        error,
      );
}
