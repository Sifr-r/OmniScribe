import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Semantic severity level for toasts.
enum ToastLevel {
  info,
  success,
  warning,
  error,
}

/// Data model representing an active toast notification.
@immutable
class ToastModel {
  const ToastModel({
    required this.id,
    required this.level,
    required this.message,
    this.title,
    this.duration = const Duration(seconds: 4),
    required this.createdAt,
    this.actionLabel,
    this.onAction,
  });

  final String id;
  final ToastLevel level;
  final String message;
  final String? title;
  final Duration duration;
  final DateTime createdAt;
  final String? actionLabel;
  final VoidCallback? onAction;

  ToastModel copyWith({
    String? id,
    ToastLevel? level,
    String? message,
    String? title,
    Duration? duration,
    DateTime? createdAt,
    String? actionLabel,
    VoidCallback? onAction,
  }) {
    return ToastModel(
      id: id ?? this.id,
      level: level ?? this.level,
      message: message ?? this.message,
      title: title ?? this.title,
      duration: duration ?? this.duration,
      createdAt: createdAt ?? this.createdAt,
      actionLabel: actionLabel ?? this.actionLabel,
      onAction: onAction ?? this.onAction,
    );
  }

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is ToastModel && runtimeType == other.runtimeType && id == other.id;

  @override
  int get hashCode => id.hashCode;
}

/// Riverpod 3 [Notifier] managing the stack of active toasts.
///
/// Migrated from ``StateNotifier<List<ToastModel>>`` in Wave 16. The new
/// ``Notifier`` lifecycle hooks teardown via ``ref.onDispose`` from
/// ``build()`` rather than the legacy ``override void dispose()``.
class ToastNotifier extends Notifier<List<ToastModel>> {
  final Map<String, Timer> _dismissTimers = {};
  int _counter = 0;

  @override
  List<ToastModel> build() {
    ref.onDispose(() {
      for (final timer in _dismissTimers.values) {
        timer.cancel();
      }
      _dismissTimers.clear();
    });
    return <ToastModel>[];
  }

  /// Show a toast notification.
  String showToast(
    String message, {
    ToastLevel level = ToastLevel.info,
    String? title,
    Duration duration = const Duration(seconds: 4),
    String? actionLabel,
    VoidCallback? onAction,
  }) {
    _counter++;
    final id = 'toast_${DateTime.now().millisecondsSinceEpoch}_$_counter';

    final toast = ToastModel(
      id: id,
      level: level,
      message: message,
      title: title,
      duration: duration,
      createdAt: DateTime.now(),
      actionLabel: actionLabel,
      onAction: onAction,
    );

    // Limit active toasts to at most 5 at a time
    final nextList = <ToastModel>[...state, toast];
    if (nextList.length > 5) {
      final removed = nextList.removeAt(0);
      _dismissTimers[removed.id]?.cancel();
      _dismissTimers.remove(removed.id);
    }

    state = nextList;

    // Schedule auto-dismiss
    if (duration > Duration.zero) {
      _dismissTimers[id] = Timer(duration, () {
        dismissToast(id);
      });
    }

    return id;
  }

  /// Convenience helper for success toasts.
  String success(String message,
      {String? title, Duration duration = const Duration(seconds: 4)}) {
    return showToast(message,
        level: ToastLevel.success, title: title, duration: duration);
  }

  /// Convenience helper for info toasts.
  String info(String message,
      {String? title, Duration duration = const Duration(seconds: 4)}) {
    return showToast(message,
        level: ToastLevel.info, title: title, duration: duration);
  }

  /// Convenience helper for warning toasts.
  String warning(String message,
      {String? title, Duration duration = const Duration(seconds: 5)}) {
    return showToast(message,
        level: ToastLevel.warning, title: title, duration: duration);
  }

  /// Convenience helper for error toasts.
  String error(String message,
      {String? title, Duration duration = const Duration(seconds: 6)}) {
    return showToast(message,
        level: ToastLevel.error, title: title, duration: duration);
  }

  /// Dismiss an active toast by ID.
  void dismissToast(String id) {
    _dismissTimers[id]?.cancel();
    _dismissTimers.remove(id);
    state = state.where((ToastModel t) => t.id != id).toList();
  }

  /// Clear all toasts.
  void clearAll() {
    for (final timer in _dismissTimers.values) {
      timer.cancel();
    }
    _dismissTimers.clear();
    state = <ToastModel>[];
  }
}

/// Global provider for toast notifications.
final toastProvider =
    NotifierProvider<ToastNotifier, List<ToastModel>>(ToastNotifier.new);
