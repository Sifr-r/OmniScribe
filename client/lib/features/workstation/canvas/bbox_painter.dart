import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';

/// CustomPainter rendering normalized bounding boxes, confidence heatmap fills,
/// revision indicators, and selection handles over document canvas.
class BBoxPainter extends CustomPainter {
  BBoxPainter({
    required this.bboxes,
    this.selectedBBox,
    this.hoveredBBox,
    required this.colors,
    this.showBBoxes = true,
    this.showHeatmap = true,
    this.showLabels = true,
  });

  final List<BBoxItem> bboxes;
  final BBoxItem? selectedBBox;
  final BBoxItem? hoveredBBox;
  final AppColorScheme colors;
  final bool showBBoxes;
  final bool showHeatmap;
  final bool showLabels;

  @override
  void paint(Canvas canvas, Size size) {
    if (!showBBoxes || bboxes.isEmpty || size.width <= 0 || size.height <= 0) {
      return;
    }

    final double width = size.width;
    final double height = size.height;

    // First pass: Paint non-selected boxes
    for (final box in bboxes) {
      if (box == selectedBBox) continue;
      _paintBBox(
        canvas: canvas,
        box: box,
        width: width,
        height: height,
        isSelected: false,
        isHovered: box == hoveredBBox,
      );
    }

    // Second pass: Paint selected box on top with focus ring and corner handles
    if (selectedBBox != null) {
      _paintBBox(
        canvas: canvas,
        box: selectedBBox!,
        width: width,
        height: height,
        isSelected: true,
        isHovered: selectedBBox == hoveredBBox,
      );
    }
  }

  void _paintBBox({
    required Canvas canvas,
    required BBoxItem box,
    required double width,
    required double height,
    required bool isSelected,
    required bool isHovered,
  }) {
    final rect = Rect.fromLTRB(
      box.x0 * width,
      box.y0 * height,
      box.x1 * width,
      box.y1 * height,
    );

    // Skip degenerate zero-area rects
    if (rect.width <= 0 || rect.height <= 0) return;

    final Color tierColor = _getConfidenceColor(box.confidence);
    final isRevised = box.isRevised;

    // 1. Box Fill (Heatmap / Hover / Selection)
    final fillPaint = Paint()..style = PaintingStyle.fill;
    if (isSelected) {
      fillPaint.color = isRevised
          ? colors.cyan.withValues(alpha: 0.25)
          : colors.brand.withValues(alpha: 0.20);
      canvas.drawRect(rect, fillPaint);
    } else if (isHovered) {
      fillPaint.color = tierColor.withValues(alpha: 0.30);
      canvas.drawRect(rect, fillPaint);
    } else if (showHeatmap) {
      fillPaint.color = isRevised
          ? colors.cyan.withValues(alpha: 0.15)
          : tierColor.withValues(alpha: 0.12);
      canvas.drawRect(rect, fillPaint);
    }

    // 2. Box Stroke Border
    final borderPaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.miter;

    if (isSelected) {
      borderPaint
        ..color = isRevised ? colors.cyan : colors.brand
        ..strokeWidth = 2.4;
      canvas.drawRect(rect, borderPaint);
    } else if (isRevised) {
      // Revised box: Dashed / styled cyan-purple border
      _drawDashedRect(
        canvas: canvas,
        rect: rect,
        color: colors.cyan,
        strokeWidth: isHovered ? 2.0 : 1.6,
        dashWidth: 4.0,
        dashSpace: 3.0,
      );
    } else if (isHovered) {
      borderPaint
        ..color = tierColor
        ..strokeWidth = 2.0;
      canvas.drawRect(rect, borderPaint);
    } else {
      borderPaint
        ..color = tierColor.withValues(alpha: 0.7)
        ..strokeWidth = 1.2;
      canvas.drawRect(rect, borderPaint);
    }

    // 3. Corner Handles for Selected Box
    if (isSelected) {
      _drawCornerHandles(canvas, rect, isRevised ? colors.cyan : colors.brand);
    }

    // 4. Label Pill (Kind + Confidence badge)
    if (showLabels && (isSelected || isHovered || rect.height > 24)) {
      _drawLabelPill(
        canvas: canvas,
        rect: rect,
        box: box,
        tierColor: tierColor,
        isSelected: isSelected,
        isHovered: isHovered,
      );
    }
  }

  Color _getConfidenceColor(double? confidence) {
    if (confidence == null) return colors.textMuted;
    if (confidence >= 0.85) return colors.success;
    if (confidence >= 0.60) return colors.warning;
    return colors.error;
  }

  void _drawDashedRect({
    required Canvas canvas,
    required Rect rect,
    required Color color,
    required double strokeWidth,
    required double dashWidth,
    required double dashSpace,
  }) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = strokeWidth
      ..style = PaintingStyle.stroke;

    final path = Path()..addRect(rect);
    final metrics = path.computeMetrics();

    for (final metric in metrics) {
      double distance = 0.0;
      while (distance < metric.length) {
        final double next = distance + dashWidth;
        final extractPath = metric.extractPath(
          distance,
          next > metric.length ? metric.length : next,
        );
        canvas.drawPath(extractPath, paint);
        distance += dashWidth + dashSpace;
      }
    }
  }

  void _drawCornerHandles(Canvas canvas, Rect rect, Color handleColor) {
    const double handleSize = 7.0;

    final fillPaint = Paint()
      ..color = Colors.white
      ..style = PaintingStyle.fill;

    final strokePaint = Paint()
      ..color = handleColor
      ..strokeWidth = 1.8
      ..style = PaintingStyle.stroke;

    final corners = [
      Offset(rect.left, rect.top),
      Offset(rect.right, rect.top),
      Offset(rect.left, rect.bottom),
      Offset(rect.right, rect.bottom),
    ];

    for (final corner in corners) {
      final handleRect = Rect.fromCenter(
        center: corner,
        width: handleSize,
        height: handleSize,
      );
      canvas.drawRect(handleRect, fillPaint);
      canvas.drawRect(handleRect, strokePaint);
    }
  }

  void _drawLabelPill({
    required Canvas canvas,
    required Rect rect,
    required BBoxItem box,
    required Color tierColor,
    required bool isSelected,
    required bool isHovered,
  }) {
    final String kindLabel = (box.kind ?? 'txt').toUpperCase();
    final String confStr =
        box.confidence != null ? '${(box.confidence! * 100).round()}%' : '—';
    final String badgeText =
        box.isRevised ? 'REV • $confStr' : '$kindLabel $confStr';

    final textSpan = TextSpan(
      text: badgeText,
      style: const TextStyle(
        fontFamily: 'JetBrains Mono',
        fontSize: 9,
        fontWeight: FontWeight.w700,
        color: Colors.white,
        letterSpacing: 0.3,
      ),
    );

    final textPainter = TextPainter(
      text: textSpan,
      textDirection: ui.TextDirection.ltr,
    )..layout();

    final pillWidth = textPainter.width + 8;
    final pillHeight = textPainter.height + 4;

    // Anchor pill inside top-left of rect, or above if tight
    final double pillLeft = (rect.left + 2).clamp(0.0, double.infinity);
    final double pillTop = (rect.top - pillHeight - 2 >= 0)
        ? (rect.top - pillHeight - 2)
        : (rect.top + 2);

    final pillRect = RRect.fromRectAndRadius(
      Rect.fromLTWH(pillLeft, pillTop, pillWidth, pillHeight),
      const Radius.circular(3),
    );

    final pillBgPaint = Paint()
      ..color = box.isRevised
          ? colors.cyan
          : isSelected
              ? colors.brand
              : tierColor.withValues(alpha: 0.90)
      ..style = PaintingStyle.fill;

    canvas.drawRRect(pillRect, pillBgPaint);
    textPainter.paint(canvas, Offset(pillLeft + 4, pillTop + 2));
  }

  @override
  bool shouldRepaint(covariant BBoxPainter oldDelegate) {
    return oldDelegate.bboxes != bboxes ||
        oldDelegate.selectedBBox != selectedBBox ||
        oldDelegate.hoveredBBox != hoveredBBox ||
        oldDelegate.showBBoxes != showBBoxes ||
        oldDelegate.showHeatmap != showHeatmap ||
        oldDelegate.showLabels != showLabels ||
        oldDelegate.colors != colors;
  }
}
