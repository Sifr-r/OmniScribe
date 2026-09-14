import 'dart:math' as math;
import 'dart:ui' show Offset, Size;

import 'package:flutter/material.dart' show Matrix4;
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Immutable canvas viewport state: uniform zoom scale plus pan translation.
class DocumentViewportState {
  const DocumentViewportState({
    this.scale = 1.0,
    this.translation = Offset.zero,
  });

  final double scale;
  final Offset translation;

  Matrix4 get matrix => Matrix4.diagonal3Values(scale, scale, 1.0)
    ..setTranslationRaw(translation.dx, translation.dy, 0.0);

  DocumentViewportState copyWith({double? scale, Offset? translation}) =>
      DocumentViewportState(
        scale: scale ?? this.scale,
        translation: translation ?? this.translation,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is DocumentViewportState &&
          other.scale == scale &&
          other.translation == translation;

  @override
  int get hashCode => Object.hash(scale, translation);
}

/// Global provider for the workstation canvas viewport.
final documentViewportProvider =
    NotifierProvider<DocumentViewportNotifier, DocumentViewportState>(
  DocumentViewportNotifier.new,
);

/// Owns the workstation viewport domain: zoom, pan, and fit-to-screen math,
/// separate from the OCR/preview concerns in [WorkstationNotifier].
class DocumentViewportNotifier extends Notifier<DocumentViewportState> {
  static const double minScale = 0.15;
  static const double maxScale = 6.0;
  static const double maxFitScale = 3.0;
  static const double fitPadding = 28.0;

  @override
  DocumentViewportState build() => const DocumentViewportState();

  /// Mirrors gesture-driven InteractiveViewer transforms back into state.
  void syncFromMatrix(Matrix4 transform) {
    final scale = transform.getMaxScaleOnAxis();
    final translation = Offset(
      transform.storage[12],
      transform.storage[13],
    );
    if (scale != state.scale || translation != state.translation) {
      state = DocumentViewportState(scale: scale, translation: translation);
    }
  }

  /// Zooms by [factor] around the viewport center, clamped to the scale band.
  void zoomBy(double factor, Size viewportSize) {
    if (viewportSize.width <= 0 || viewportSize.height <= 0) return;
    final targetScale = (state.scale * factor).clamp(minScale, maxScale);
    if ((targetScale - state.scale).abs() < 0.001) return;
    final effectiveFactor = targetScale / state.scale;

    final cx = viewportSize.width / 2.0;
    final cy = viewportSize.height / 2.0;
    final tx = state.translation.dx;
    final ty = state.translation.dy;

    state = state.copyWith(
      scale: targetScale,
      translation: Offset(
        cx * (1.0 - effectiveFactor) + tx * effectiveFactor,
        cy * (1.0 - effectiveFactor) + ty * effectiveFactor,
      ),
    );
  }

  /// Fits the canvas inside the viewport with a symmetric padding margin.
  void fitToScreen({
    required Size viewportSize,
    required Size canvasSize,
  }) {
    if (viewportSize.width <= 0 ||
        viewportSize.height <= 0 ||
        canvasSize.width <= 0 ||
        canvasSize.height <= 0) {
      state = const DocumentViewportState();
      return;
    }

    final double availWidth =
        math.max(60.0, viewportSize.width - fitPadding * 2);
    final double availHeight =
        math.max(60.0, viewportSize.height - fitPadding * 2);

    final double fitScale = math.min(
      availWidth / canvasSize.width,
      availHeight / canvasSize.height,
    ).clamp(minScale, maxFitScale);

    state = state.copyWith(
      scale: fitScale,
      translation: Offset(
        (viewportSize.width - canvasSize.width * fitScale) / 2.0,
        (viewportSize.height - canvasSize.height * fitScale) / 2.0,
      ),
    );
  }

  /// Resets to 100% zoom with the canvas centered in the viewport.
  void resetToActualSize({
    required Size viewportSize,
    required Size canvasSize,
  }) {
    if (viewportSize.width <= 0 ||
        viewportSize.height <= 0 ||
        canvasSize.width <= 0 ||
        canvasSize.height <= 0) {
      state = const DocumentViewportState();
      return;
    }
    state = state.copyWith(
      scale: 1.0,
      translation: Offset(
        (viewportSize.width - canvasSize.width) / 2.0,
        (viewportSize.height - canvasSize.height) / 2.0,
      ),
    );
  }
}
