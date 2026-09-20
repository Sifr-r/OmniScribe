import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/data/models/job_record.dart';
import 'package:omniscribe_client/data/providers/jobs_state.dart';
import 'package:omniscribe_client/data/providers/repository_providers.dart';
import 'package:omniscribe_client/data/repositories/job_repository.dart';

/// Riverpod 2.x `Notifier` for the Jobs vertical.
///
/// Watches [jobRepositoryProvider] for the data-layer repository and exposes
/// `fetchJobs` / `clearJobs` / `cancelJob` / `downloadResult` to the UI.
///
/// The legacy `StateNotifier` `JobsNotifier` used to fire `fetchJobs()` from
/// its constructor; this implementation intentionally does **not** do that.
/// Callers (the screen's `initState` and the Refresh button) trigger the
/// initial fetch themselves, matching how slice 1 wired `SettingsNotifier`.
final jobsProvider =
    NotifierProvider<JobsNotifier, JobsState>(JobsNotifier.new);

class JobsNotifier extends Notifier<JobsState> {
  JobRepository get _repo => ref.read(jobRepositoryProvider);

  @override
  JobsState build() {
    return const JobsState();
  }

  Future<void> fetchJobs() async {
    state = state.copyWith(isFetching: true, clearError: true);
    try {
      final jobs = await _repo.listJobs();
      state = state.copyWith(jobs: jobs, isFetching: false);
    } catch (e) {
      state = state.copyWith(isFetching: false, error: e.toString());
    }
  }

  Future<void> clearJobs() async {
    state = state.copyWith(isFetching: true, clearError: true);
    try {
      await _repo.clearJobs();
      state = state.copyWith(jobs: const <JobRecord>[], isFetching: false);
    } catch (e) {
      state = state.copyWith(isFetching: false, error: e.toString());
      rethrow;
    }
  }

  Future<void> cancelJob(String jobId) async {
    try {
      await _repo.cancelJob(jobId);
      await fetchJobs();
    } catch (e) {
      state = state.copyWith(error: e.toString());
      rethrow;
    }
  }

  Future<Uint8List> downloadResult(String jobId) async {
    try {
      return await _repo.downloadResult(jobId);
    } catch (e) {
      state = state.copyWith(error: e.toString());
      rethrow;
    }
  }
}
