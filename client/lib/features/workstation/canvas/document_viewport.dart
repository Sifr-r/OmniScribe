import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/features/workstation/bbox_item.dart';
import 'package:omniscribe_client/features/workstation/canvas/bbox_painter.dart';
import 'package:omniscribe_client/features/workstation/document_result.dart';
import 'package:omniscribe_client/features/workstation/document_selection_notifier.dart';
import 'package:omniscribe_client/features/workstation/document_viewport_notifier.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/features/workstation/workstation_state.dart';

/// GPU-Accelerated Document Viewport with InteractiveViewer zoom/pan,
/// custom BBoxPainter overlay, hit-testing, and layer controls.
class DocumentViewport extends ConsumerStatefulWidget {
  const DocumentViewport({
    super.key,
    this.onBBoxSelected,
  });

  final ValueChanged<BBoxItem?>? onBBoxSelected;

  @override
  ConsumerState<DocumentViewport> createState() => _DocumentViewportState();
}

class _DocumentViewportState extends ConsumerState<DocumentViewport> {
  final TransformationController _transformController =
      TransformationController();
  final GlobalKey _canvasKey = GlobalKey();

  double _currentScale = 1.0;
  Size _lastViewportSize = Size.zero;
  Size _lastCanvasSize = Size.zero;
  double? _lastCanvasAspectRatio;
  String? _lastDocFilename;
  int? _lastPageIndex;
  bool _hasFittedInitial = false;

  @override
  void initState() {
    super.initState();
    _transformController.addListener(_onTransformChanged);
  }

  @override
  void dispose() {
    _transformController.removeListener(_onTransformChanged);
    _transformController.dispose();
    super.dispose();
  }

  void _onTransformChanged() {
    final value = _transformController.value;
    ref.read(documentViewportProvider.notifier).syncFromMatrix(value);
    final scale = value.getMaxScaleOnAxis();
    if ((scale - _currentScale).abs() > 0.01) {
      setState(() {
        _currentScale = scale;
      });
    }
  }

  /// Applies the provider's viewport transform to the controller when it
  /// diverges from the gesture-synced matrix (no-op after plain gestures).
  void _applyProviderTransform() {
    final viewport = ref.read(documentViewportProvider);
    final current = _transformController.value;
    final sameScale =
        (current.getMaxScaleOnAxis() - viewport.scale).abs() < 0.001;
    final sameTranslation =
        (current.storage[12] - viewport.translation.dx).abs() < 0.001 &&
            (current.storage[13] - viewport.translation.dy).abs() < 0.001;
    if (sameScale && sameTranslation) return;
    _transformController.value = viewport.matrix;
  }

  void _zoomBy(double factor) {
    if (_lastViewportSize.width <= 0 || _lastViewportSize.height <= 0) return;
    ref
        .read(documentViewportProvider.notifier)
        .zoomBy(factor, _lastViewportSize);
    _applyProviderTransform();
  }

  void _zoomIn() => _zoomBy(1.25);

  void _zoomOut() => _zoomBy(0.8);

  void _fitToScreen({Size? viewportSize, Size? canvasSize}) {
    ref.read(documentViewportProvider.notifier).fitToScreen(
          viewportSize: viewportSize ?? _lastViewportSize,
          canvasSize: canvasSize ?? _lastCanvasSize,
        );
    _applyProviderTransform();
  }

  void _resetZoom() {
    ref.read(documentViewportProvider.notifier).resetToActualSize(
          viewportSize: _lastViewportSize,
          canvasSize: _lastCanvasSize,
        );
    _applyProviderTransform();
  }

  void _handleTapUp(
      TapUpDetails details, Size canvasSize, List<BBoxItem> bboxes) {
    final selection = ref.read(documentSelectionProvider.notifier);
    if (canvasSize.width <= 0 || canvasSize.height <= 0 || bboxes.isEmpty) {
      selection.select(null);
      widget.onBBoxSelected?.call(null);
      return;
    }

    final localPos = details.localPosition;
    final normX = (localPos.dx / canvasSize.width).clamp(0.0, 1.0);
    final normY = (localPos.dy / canvasSize.height).clamp(0.0, 1.0);

    // Find all bboxes containing this point
    final hits = bboxes.where((b) {
      return normX >= b.x0 && normX <= b.x1 && normY >= b.y0 && normY <= b.y1;
    }).toList();

    if (hits.isEmpty) {
      selection.select(null);
      widget.onBBoxSelected?.call(null);
    } else {
      // Pick the most specific (smallest area) box
      hits.sort((a, b) => (a.width * a.height).compareTo(b.width * b.height));
      final selected = hits.first;
      selection.select(selected);
      widget.onBBoxSelected?.call(selected);
    }
  }

  void _handlePointerHover(
      PointerHoverEvent event, Size canvasSize, List<BBoxItem> bboxes) {
    final selection = ref.read(documentSelectionProvider.notifier);
    if (canvasSize.width <= 0 || canvasSize.height <= 0 || bboxes.isEmpty) {
      selection.hover(null);
      return;
    }

    final localPos = event.localPosition;
    final normX = (localPos.dx / canvasSize.width).clamp(0.0, 1.0);
    final normY = (localPos.dy / canvasSize.height).clamp(0.0, 1.0);

    final hits = bboxes.where((b) {
      return normX >= b.x0 && normX <= b.x1 && normY >= b.y0 && normY <= b.y1;
    }).toList();

    if (hits.isEmpty) {
      selection.hover(null);
    } else {
      hits.sort((a, b) => (a.width * a.height).compareTo(b.width * b.height));
      selection.hover(hits.first);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);
    final notifier = ref.read(workstationProvider.notifier);

    final PageResult? currentPage = wsState.currentPage;
    final bboxes = wsState.currentPageBBoxes;

    return Container(
      decoration: BoxDecoration(
        color: colors.background,
        border: Border.all(color: colors.border),
        borderRadius: const BorderRadius.all(Radius.circular(8)),
      ),
      child: ClipRRect(
        borderRadius: const BorderRadius.all(Radius.circular(8)),
        child: LayoutBuilder(
          builder: (context, viewportConstraints) {
            final viewportSize = Size(
              viewportConstraints.maxWidth,
              viewportConstraints.maxHeight,
            );
            _lastViewportSize = viewportSize;

            // Determine canvas physical aspect ratio & base size
            const double baseWidth = 680.0;
            final double aspectRatio = currentPage?.aspectRatio ?? (8.5 / 11.0);
            final double baseHeight = baseWidth / aspectRatio;
            final canvasSize = Size(baseWidth, baseHeight);
            _lastCanvasSize = canvasSize;

            // Auto-fit document to screen whenever document, page, or aspect ratio changes
            final docChanged = wsState.filename != _lastDocFilename;
            final pageChanged = wsState.selectedPageIndex != _lastPageIndex;
            final aspectChanged = _lastCanvasAspectRatio != null &&
                (_lastCanvasAspectRatio! - aspectRatio).abs() > 0.001;
            if (wsState.hasDocument &&
                (docChanged ||
                    pageChanged ||
                    aspectChanged ||
                    !_hasFittedInitial)) {
              _lastDocFilename = wsState.filename;
              _lastPageIndex = wsState.selectedPageIndex;
              _lastCanvasAspectRatio = aspectRatio;
              _hasFittedInitial = true;
              WidgetsBinding.instance.addPostFrameCallback((_) {
                if (mounted) {
                  ref.read(documentViewportProvider.notifier).fitToScreen(
                        viewportSize: viewportSize,
                        canvasSize: canvasSize,
                      );
                  _applyProviderTransform();
                }
              });
            } else if (!wsState.hasDocument) {
              _hasFittedInitial = false;
              _lastCanvasAspectRatio = null;
            }

            // Auto-trigger page preview fetch if missing and not currently loading or errored
            if (wsState.hasDocument &&
                currentPage?.previewBytes == null &&
                !wsState.isPreviewLoading &&
                wsState.previewError == null) {
              WidgetsBinding.instance.addPostFrameCallback((_) {
                if (mounted &&
                    currentPage?.previewBytes == null &&
                    !wsState.isPreviewLoading &&
                    wsState.previewError == null) {
                  notifier.retryPagePreview(wsState.selectedPageIndex);
                }
              });
            }

            return Stack(
              children: [
                // Subtle Spatial Grid Background
                Positioned.fill(
                  child: CustomPaint(
                    painter: _GridBackgroundPainter(
                      gridColor: colors.border.withValues(alpha: 0.35),
                    ),
                  ),
                ),

                // Interactive Viewer Canvas (Unconstrained so height is not clamped to viewport)
                Positioned.fill(
                  child: InteractiveViewer(
                    transformationController: _transformController,
                    constrained: false,
                    minScale: 0.15,
                    maxScale: 6.0,
                    boundaryMargin: const EdgeInsets.all(1200),
                    child: _buildDocumentCanvas(
                      context,
                      wsState,
                      notifier,
                      currentPage,
                      bboxes,
                      colors,
                      canvasSize,
                    ),
                  ),
                ),

                // Floating Quick-Action Layer / Zoom Controls (Bottom Right)
                Positioned(
                  right: 16,
                  bottom: 16,
                  child: _buildFloatingControls(colors, wsState, notifier),
                ),
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _buildDocumentCanvas(
    BuildContext context,
    WorkstationState wsState,
    WorkstationNotifier notifier,
    PageResult? currentPage,
    List<BBoxItem> bboxes,
    AppColorScheme colors,
    Size canvasSize,
  ) {
    final selectionState = ref.watch(documentSelectionProvider);
    return Container(
      key: _canvasKey,
      width: canvasSize.width,
      height: canvasSize.height,
      decoration: BoxDecoration(
        color: colors.card,
        borderRadius: BorderRadius.circular(6),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.45),
            blurRadius: 28,
            offset: const Offset(0, 10),
          ),
        ],
        border: Border.all(color: colors.borderStrong, width: 1.0),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: MouseRegion(
          onHover: (e) => _handlePointerHover(e, canvasSize, bboxes),
          onExit: (_) =>
              ref.read(documentSelectionProvider.notifier).hover(null),
          child: GestureDetector(
            onTapUp: (details) => _handleTapUp(details, canvasSize, bboxes),
            child: Stack(
              fit: StackFit.expand,
              children: [
                // 1. Page Background Raster / Placeholder
                if (currentPage?.previewBytes != null)
                  Image.memory(
                    currentPage!.previewBytes!,
                    fit: BoxFit.fill,
                    gaplessPlayback: true,
                  )
                else
                  _buildPagePlaceholder(colors, wsState, notifier),

                // 2. Custom Painted Bounding Box Overlays
                CustomPaint(
                  size: canvasSize,
                  painter: BBoxPainter(
                    bboxes: bboxes,
                    selectedBBox: selectionState.selectedBBox,
                    hoveredBBox: selectionState.hoveredBBox,
                    colors: colors,
                    showBBoxes: wsState.showBBoxes,
                    showHeatmap: wsState.showHeatmap,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildPagePlaceholder(
    AppColorScheme colors,
    WorkstationState wsState,
    WorkstationNotifier notifier,
  ) {
    if (wsState.hasDocument) {
      if (wsState.isPreviewLoading) {
        return Container(
          color: Colors.white,
          padding: const EdgeInsets.all(24),
          child: Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: colors.brand.withValues(alpha: 0.1),
                    shape: BoxShape.circle,
                  ),
                  child: Center(
                    child: Icon(
                      Icons.auto_awesome_rounded,
                      size: 22,
                      color: colors.brand,
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Text(
                  'Rendering Page ${wsState.selectedPageIndex + 1}...',
                  style: AppTypography.titleSmall(
                    color: const Color(0xFF1E293B),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  'Generating high-resolution raster preview',
                  style: AppTypography.bodySmall(
                    color: const Color(0xFF64748B),
                  ),
                ),
              ],
            ),
          ),
        );
      }

      // Not loading, but previewBytes is still null (e.g. server error, preview unavailable)
      return Container(
        color: Colors.white,
        padding: const EdgeInsets.all(32),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  color: colors.brand.withValues(alpha: 0.1),
                  shape: BoxShape.circle,
                ),
                child: Center(
                  child: Icon(
                    Icons.picture_as_pdf_outlined,
                    size: 26,
                    color: colors.brand,
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Text(
                'Page ${wsState.selectedPageIndex + 1} of ${math.max(1, wsState.pageCount)}',
                style: AppTypography.titleSmall(
                  color: const Color(0xFF1E293B),
                ),
              ),
              const SizedBox(height: 6),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Text(
                  wsState.previewError ??
                      'Raster preview not loaded. Click below to load or render this page.',
                  style: AppTypography.bodySmall(
                    color: const Color(0xFF64748B),
                  ),
                  textAlign: TextAlign.center,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(height: 16),
              ElevatedButton.icon(
                onPressed: () =>
                    notifier.retryPagePreview(wsState.selectedPageIndex),
                icon: const Icon(Icons.refresh_rounded, size: 16),
                label: const Text('Load Preview'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: colors.brand,
                  foregroundColor: Colors.white,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  textStyle: AppTypography.bodySmall().copyWith(
                    fontWeight: FontWeight.w600,
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(6),
                  ),
                ),
              ),
            ],
          ),
        ),
      );
    }

    return Container(
      color: colors.card,
      padding: const EdgeInsets.all(32),
      child: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              Icons.description_outlined,
              size: 48,
              color: colors.textMuted.withValues(alpha: 0.5),
            ),
            const SizedBox(height: 12),
            Text(
              'No Document Loaded',
              style: AppTypography.titleMedium(
                color: colors.textMuted,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              'Drop or select a PDF / image file from the dropzone to start',
              style: AppTypography.bodySmall(
                color: colors.textMuted,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFloatingControls(
    AppColorScheme colors,
    WorkstationState wsState,
    WorkstationNotifier notifier,
  ) {
    final zoomPercent = (_currentScale * 100).round();

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
      decoration: BoxDecoration(
        color: colors.cardRaised.withValues(alpha: 0.95),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: colors.borderStrong),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.25),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            icon: const Icon(Icons.remove, size: 16),
            tooltip: 'Zoom out',
            constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
            padding: EdgeInsets.zero,
            color: colors.textPrimary,
            onPressed: _zoomOut,
          ),
          Tooltip(
            message: 'Reset to Actual Size (100%)',
            child: InkWell(
              onTap: _resetZoom,
              borderRadius: BorderRadius.circular(4),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                child: Text(
                  '$zoomPercent%',
                  style: AppTypography.codeSmall(
                    color: colors.brand,
                  ).copyWith(fontWeight: FontWeight.w600),
                ),
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.add, size: 16),
            tooltip: 'Zoom in',
            constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
            padding: EdgeInsets.zero,
            color: colors.textPrimary,
            onPressed: _zoomIn,
          ),
          Container(
            width: 1,
            height: 16,
            color: colors.border,
            margin: const EdgeInsets.symmetric(horizontal: 4),
          ),
          IconButton(
            icon: const Icon(Icons.fit_screen_rounded, size: 16),
            tooltip: 'Fit to screen',
            constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
            padding: EdgeInsets.zero,
            color: colors.textPrimary,
            onPressed: () => _fitToScreen(),
          ),
        ],
      ),
    );
  }
}

class _GridBackgroundPainter extends CustomPainter {
  const _GridBackgroundPainter({required this.gridColor});
  final Color gridColor;

  @override
  void paint(Canvas canvas, Size size) {
    const double step = 24.0;
    final paint = Paint()
      ..color = gridColor
      ..strokeWidth = 0.5;

    for (double x = 0; x < size.width; x += step) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
    }
    for (double y = 0; y < size.height; y += step) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(covariant _GridBackgroundPainter oldDelegate) {
    return oldDelegate.gridColor != gridColor;
  }
}
