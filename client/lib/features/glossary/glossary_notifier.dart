import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart' show visibleForTesting;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'glossary_models.dart';
import 'glossary_repository.dart';
import 'glossary_state.dart';

export 'glossary_state.dart';

final glossaryProvider = NotifierProvider<GlossaryNotifier, GlossaryState>(
  GlossaryNotifier.new,
);

class GlossaryNotifier extends Notifier<GlossaryState> {
  int _entriesEpoch = 0;
  GlossaryRepository get _repo => ref.read(glossaryRepositoryProvider);

  @override
  GlossaryState build() {
    return const GlossaryState.initial();
  }

  void setActiveViewIndex(int index) {
    state = state.copyWith(activeViewIndex: index);
  }

  void setSelectedLibrary(GlossaryListItem? lib) {
    ++_entriesEpoch;
    state = state.copyWith(
      selectedLibrary: lib,
      clearSelectedLibrary: lib == null,
    );
  }

  void clearError() {
    state = state.copyWith(clearError: true);
  }

  Future<void> loadLibraries() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final libs = await _repo.getGlossaryLibraries();
      if (!ref.mounted) return;
      state = state.copyWith(libraries: libs, isLoading: false);
    } catch (e) {
      if (!ref.mounted) return;
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }

  Future<void> loadEntries(GlossaryListItem lib) async {
    final runId = ++_entriesEpoch;
    state = state.copyWith(
      selectedLibrary: lib,
      isLoading: true,
      clearError: true,
    );

    try {
      final entries = await _repo.getGlossaryEntries(lib.id);
      if (!ref.mounted || runId != _entriesEpoch) return;
      state = state.copyWith(
        entries: entries,
        activeViewIndex: 1,
        isLoading: false,
      );
    } catch (e) {
      if (!ref.mounted || runId != _entriesEpoch) return;
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }

  Future<void> loadMergedLexicon() async {
    try {
      final entries = await _repo.getMergedGlossaryEntries();
      if (!ref.mounted) return;
      final map = <String, String>{
        for (final e in entries) e.source: e.target,
      };
      state = state.copyWith(mergedLexicon: map);
    } catch (e) {
      if (!ref.mounted) return;
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> toggleLibrary(GlossaryListItem lib, bool enabled) async {
    bool ok;
    try {
      ok = await _repo.toggleGlossaryLibrary(lib.id, enabled);
    } catch (e) {
      if (!ref.mounted) return;
      state = state.copyWith(error: e.toString());
      return;
    }
    if (!ref.mounted) return;
    if (!ok) {
      state = state.copyWith(error: 'Failed to toggle "${lib.name}".');
      return;
    }

    final updated = state.libraries.map((item) {
      if (item.id == lib.id) {
        return item.copyWith(enabled: enabled);
      }
      return item;
    }).toList();

    state = state.copyWith(libraries: updated);
    await loadMergedLexicon();
  }

  Future<void> deleteLibrary(String id) async {
    bool ok;
    try {
      ok = await _repo.deleteGlossaryLibrary(id);
    } catch (e) {
      if (!ref.mounted) return;
      state = state.copyWith(error: e.toString());
      return;
    }
    if (!ref.mounted) return;
    if (!ok) {
      state = state.copyWith(error: 'Failed to delete glossary library.');
      return;
    }

    final updated = state.libraries.where((item) => item.id != id).toList();
    final resetSelected = state.selectedLibrary?.id == id;
    if (resetSelected) ++_entriesEpoch;

    state = state.copyWith(
      libraries: updated,
      entries: resetSelected ? const <GlossaryEntry>[] : state.entries,
      activeViewIndex: resetSelected ? 0 : state.activeViewIndex,
      clearSelectedLibrary: resetSelected,
    );
    await loadMergedLexicon();
  }

  Future<GlossaryImportJobResponse> importGlossaryFile({
    required Uint8List fileBytes,
    required String filename,
    String? channelId,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final res = await _repo.importGlossaryFile(
        fileBytes: fileBytes,
        filename: filename,
        channelId: channelId,
      );
      return await followImportJob(res);
    } catch (e) {
      if (ref.mounted) {
        state = state.copyWith(isLoading: false, error: e.toString());
      }
      rethrow;
    }
  }

  Future<GlossaryImportJobResponse> importGlossaryUrl({
    required String url,
    required GlossaryFormat format,
    String? name,
    String? channelId,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final res = await _repo.importGlossaryUrl(
        url: url,
        format: format,
        name: name,
        channelId: channelId,
      );
      return await followImportJob(res);
    } catch (e) {
      if (ref.mounted) {
        state = state.copyWith(isLoading: false, error: e.toString());
      }
      rethrow;
    }
  }

  /// Refresh the library list and merged lexicon after a successful import.
  Future<void> _refreshAfterImport() async {
    await loadLibraries();
    if (!ref.mounted) return;
    await loadMergedLexicon();
    if (!ref.mounted) return;
    state = state.copyWith(isLoading: false);
  }

  /// Follow a queued import through the shared job infrastructure.
  ///
  /// A queued import has written nothing yet, so refreshing immediately (the
  /// previous behaviour) showed a stale library list and silently hid worker
  /// failures. Poll until the job reaches a terminal state, then refresh on
  /// success or surface the failure.
  @visibleForTesting
  Future<GlossaryImportJobResponse> followImportJob(
    GlossaryImportJobResponse res, {
    Duration pollInterval = const Duration(milliseconds: 500),
    Duration timeout = const Duration(minutes: 10),
  }) async {
    final jobId = res.jobId;
    if (!res.queued) {
      // Synchronous import: the library is already persisted.
      await _refreshAfterImport();
      return res;
    }
    if (jobId == null || jobId.isEmpty) {
      throw const FormatException(
          'Queued glossary import is missing its job ID.');
    }
    if (pollInterval.isNegative || timeout <= Duration.zero) {
      throw ArgumentError(
          'Glossary polling requires a positive timeout and non-negative interval.');
    }

    final deadline = DateTime.now().add(timeout);
    var failures = 0;
    while (DateTime.now().isBefore(deadline)) {
      final beforePoll = deadline.difference(DateTime.now());
      await Future<void>.delayed(
          pollInterval < beforePoll ? pollInterval : beforePoll);
      if (!ref.mounted) return res;
      final remaining = deadline.difference(DateTime.now());
      if (remaining <= Duration.zero) break;
      final GlossaryJobStatus status;
      try {
        status = await _repo.getImportJobStatus(jobId).timeout(remaining);
        failures = 0;
      } on TimeoutException {
        break;
      } catch (e) {
        if (e is! FormatException && ++failures < 3) continue;
        if (ref.mounted) {
          state = state.copyWith(
            isLoading: false,
            error:
                'Could not read glossary import $jobId status; it may still be running: $e',
          );
        }
        return res;
      }
      if (!ref.mounted) return res;
      if (!status.isTerminal) continue;

      if (status.succeeded) {
        await _refreshAfterImport();
      } else if (ref.mounted) {
        final reason = (status.error != null && status.error!.isNotEmpty)
            ? status.error!
            : status.status;
        state = state.copyWith(
          isLoading: false,
          error: 'Glossary import $jobId failed ($reason).',
        );
      }
      return res;
    }

    // Timed out: the worker may still be running. Report it honestly instead
    // of implying the import finished.
    if (ref.mounted) {
      state = state.copyWith(
        isLoading: false,
        error: 'Glossary import $jobId is still running. The library will '
            'appear once the worker finishes.',
      );
    }
    return res;
  }

  Future<void> importGlossaryJson({
    required GlossaryFormat format,
    String? name,
    String? text,
  }) async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final bytes = Uint8List.fromList(utf8.encode(text ?? ''));
      final res = await _repo.importGlossaryFile(
        fileBytes: bytes,
        filename:
            '${name ?? "glossary"}.${format == GlossaryFormat.jsonPairs ? "json" : format.value}',
      );
      await followImportJob(res);
    } catch (e) {
      if (!ref.mounted) return;
      state = state.copyWith(
        isLoading: false,
        error: e.toString(),
      );
    }
  }
}
