import 'dart:math' as math;
import 'dart:ui' show Offset, Size;

import 'package:flutter/material.dart' show Matrix4;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:omniscribe_client/features/workstation/document_viewport_notifier.dart';

void main() {
  ProviderContainer makeContainer() => ProviderContainer();

  group('DocumentViewportState', () {
    test('defaults to 100% zoom with zero translation', () {
      const state = DocumentViewportState();
      expect(state.scale, 1.0);
      expect(state.translation, Offset.zero);
      expect(state.matrix, Matrix4.identity());
    });

    test('matrix getter encodes scale and translation', () {
      const state = DocumentViewportState(
        scale: 2.0,
        translation: Offset(30, 40),
      );
      final matrix = state.matrix;
      expect(matrix.getMaxScaleOnAxis(), 2.0);
      expect(matrix.storage[12], 30.0);
      expect(matrix.storage[13], 40.0);
    });

    test('value equality covers scale and translation', () {
      expect(
        const DocumentViewportState(scale: 2.0),
        const DocumentViewportState(scale: 2.0),
      );
      expect(
        const DocumentViewportState(scale: 2.0),
        isNot(const DocumentViewportState(scale: 3.0)),
      );
      expect(
        const DocumentViewportState(translation: Offset(1, 0)),
        isNot(const DocumentViewportState(translation: Offset(0, 1))),
      );
    });
  });

  group('DocumentViewportNotifier zoom', () {
    test('zoomBy multiplies scale around the viewport center', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentViewportProvider.notifier);

      notifier.zoomBy(1.25, const Size(800, 600));

      final state = container.read(documentViewportProvider);
      expect(state.scale, 1.25);
      // At scale 1 / zero translation, center-anchored zoom keeps tx,ty at
      // cx * (1 - factor) = 400 * (1 - 1.25) = -100 (same for ty with 300).
      expect(state.translation.dx, closeTo(-100.0, 0.01));
      expect(state.translation.dy, closeTo(-75.0, 0.01));
    });

    test('zoomBy clamps to the scale band', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentViewportProvider.notifier);

      notifier.zoomBy(100, const Size(800, 600));
      expect(container.read(documentViewportProvider).scale,
          DocumentViewportNotifier.maxScale);

      notifier.zoomBy(1.0 / 1000, const Size(800, 600));
      expect(container.read(documentViewportProvider).scale,
          DocumentViewportNotifier.minScale);
    });

    test('zoomBy ignores invalid viewports', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      container.read(documentViewportProvider.notifier).zoomBy(
            2.0,
            Size.zero,
          );
      expect(container.read(documentViewportProvider).scale, 1.0);
    });

    test('syncFromMatrix mirrors gesture transforms into state', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentViewportProvider.notifier);

      final matrix = Matrix4.diagonal3Values(3.0, 3.0, 1.0)
        ..setTranslationRaw(12.0, 34.0, 0.0);
      notifier.syncFromMatrix(matrix);

      final state = container.read(documentViewportProvider);
      expect(state.scale, 3.0);
      expect(state.translation, const Offset(12.0, 34.0));
    });
  });

  group('DocumentViewportNotifier fit & reset', () {
    test('fitToScreen centers the canvas within padding', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentViewportProvider.notifier);

      // Viewport 900x700, canvas 680x880 (portrait): width-limited fit.
      notifier.fitToScreen(
        viewportSize: const Size(900, 700),
        canvasSize: const Size(680, 880),
      );

      final state = container.read(documentViewportProvider);
      const padding = DocumentViewportNotifier.fitPadding;
      const availWidth = 900 - padding * 2;
      final expectedScale =
          math.min(availWidth / 680, (700 - padding * 2) / 880);
      expect(state.scale, closeTo(expectedScale, 0.0001));
      expect(
        state.translation.dx,
        closeTo((900 - 680 * expectedScale) / 2.0, 0.0001),
      );
      expect(
        state.translation.dy,
        closeTo((700 - 880 * expectedScale) / 2.0, 0.0001),
      );
    });

    test('fitToScreen clamps oversized fit scale', () {
      final container = makeContainer();
      addTearDown(container.dispose);

      // Tiny canvas inside a huge viewport: raw fit scale far above the cap.
      container.read(documentViewportProvider.notifier).fitToScreen(
            viewportSize: const Size(2000, 2000),
            canvasSize: const Size(100, 100),
          );
      expect(container.read(documentViewportProvider).scale,
          DocumentViewportNotifier.maxFitScale);
    });

    test('resetToActualSize centers at 100% zoom', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentViewportProvider.notifier);

      notifier.resetToActualSize(
        viewportSize: const Size(800, 600),
        canvasSize: const Size(680, 880),
      );

      final state = container.read(documentViewportProvider);
      expect(state.scale, 1.0);
      expect(state.translation,
          const Offset((800 - 680) / 2.0, (600 - 880) / 2.0));
    });

    test('invalid sizes fall back to identity', () {
      final container = makeContainer();
      addTearDown(container.dispose);
      final notifier = container.read(documentViewportProvider.notifier);

      notifier.fitToScreen(
          viewportSize: Size.zero, canvasSize: const Size(10, 10));
      expect(container.read(documentViewportProvider),
          const DocumentViewportState());

      notifier.resetToActualSize(
        viewportSize: const Size(10, 10),
        canvasSize: Size.zero,
      );
      expect(container.read(documentViewportProvider),
          const DocumentViewportState());
    });
  });
}
