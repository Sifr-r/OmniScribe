import 'package:omniscribe_client/features/jobs/job_record.dart';

/// Immutable state for the Jobs vertical.
///
/// Mirrors the shape slice 1 used for [SettingsState]: a single value object
/// with a `copyWith` that supports both positional updates and a
/// `clearError` flag for explicit null resets.
class JobsState {
  const JobsState({
    this.jobs = const <JobRecord>[],
    this.isFetching = false,
    this.error,
  });

  final List<JobRecord> jobs;
  final bool isFetching;
  final String? error;

  JobsState copyWith({
    List<JobRecord>? jobs,
    bool? isFetching,
    String? error,
    bool clearError = false,
  }) {
    return JobsState(
      jobs: jobs ?? this.jobs,
      isFetching: isFetching ?? this.isFetching,
      error: clearError ? null : (error ?? this.error),
    );
  }
}
