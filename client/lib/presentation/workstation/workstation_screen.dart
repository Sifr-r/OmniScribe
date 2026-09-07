import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/data/models/process_settings.dart';
import 'package:omniscribe_client/data/providers/settings_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_notifier.dart';
import 'package:omniscribe_client/data/providers/workstation_state.dart';
import 'package:omniscribe_client/presentation/common/app_badge.dart';
import 'package:omniscribe_client/presentation/common/app_button.dart';
import 'canvas/bbox_inspector.dart';
import 'canvas/document_viewport.dart';
import 'controls/page_strip.dart';
import 'controls/right_control_dock.dart';
import 'controls/upload_dropzone.dart';
import 'modals/export_modal.dart';
import 'progress/bottom_progress_dock.dart';

/// Main OCR Workstation Screen uniting the GPU Document Viewport, BBox Inspector,
/// multi-page strip, controls dock, and real-time live progress dock.
class WorkstationScreen extends ConsumerStatefulWidget {
  const WorkstationScreen({super.key});

  @override
  ConsumerState<WorkstationScreen> createState() => _WorkstationScreenState();
}

class _WorkstationScreenState extends ConsumerState<WorkstationScreen> {
  ProcessSettings _processSettings = const ProcessSettings();

  /// Triggers document processing (Sync / Async OCR with Live streaming)
  Future<void> _handleProcessDocument(ProcessSettings settings) async {
    final wsState = ref.read(workstationProvider);
    if (!wsState.hasDocument) return;

    final notifier = ref.read(workstationProvider.notifier);
    final globalSettings = ref.read(settingsStateProvider);
    final effectiveUseAsync = settings.useAsync || globalSettings.useAsync;
    final effectiveSettings = settings.copyWith(useAsync: effectiveUseAsync);

    try {
      if (effectiveUseAsync) {
        await notifier.processOcrAsync(settings: effectiveSettings);
      } else {
        await notifier.processOcrSync(settings: effectiveSettings);
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Processing failed: $e',
            style: const TextStyle(color: Colors.white),
          ),
          backgroundColor: Colors.red.shade800,
          behavior: SnackBarBehavior.floating,
          duration: const Duration(seconds: 8),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);
    final notifier = ref.read(workstationProvider.notifier);

    final hasDoc = wsState.hasDocument;
    final selectedBBox = wsState.selectedBBox;

    return Container(
      color: colors.background,
      child: Column(
        children: [
          // Main Workstation Header Bar
          _buildHeaderBar(context, colors, wsState, notifier),

          // Main Workstation Content Area
          Expanded(
            child: !hasDoc
                // 1. Empty state / Initial dropzone
                ? Center(
                    child: Container(
                      constraints: const BoxConstraints(maxWidth: 640),
                      padding: const EdgeInsets.all(24),
                      child: const UploadDropzone(),
                    ),
                  )
                // 2. Full Active Workstation Split-Pane
                : Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
                    child: LayoutBuilder(
                      builder: (context, constraints) {
                        final isWide = constraints.maxWidth >= 768;

                        if (isWide) {
                          final dockWidth = constraints.maxWidth >= 1100
                              ? 340.0
                              : constraints.maxWidth >= 900
                                  ? 300.0
                                  : 270.0;
                          final inspectorWidth = constraints.maxWidth >= 1200
                              ? 320.0
                              : 260.0;

                          return Row(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              // Left Vertical Page Strip Rail
                              if (wsState.pageCount > 1) ...[
                                const PageStrip(orientation: Axis.vertical),
                                const SizedBox(width: 12),
                              ],
                              // Center: Main Viewport
                              Expanded(
                                child: DocumentViewport(
                                  onBBoxSelected: (box) =>
                                      notifier.selectBBox(box),
                                ),
                              ),
                              // Side BBox Inspector (if a box is selected)
                              if (selectedBBox != null) ...[
                                const SizedBox(width: 12),
                                SizedBox(
                                  width: inspectorWidth,
                                  child: BBoxInspector(
                                    bbox: selectedBBox,
                                    onClose: () => notifier.selectBBox(null),
                                  ),
                                ),
                              ],
                              const SizedBox(width: 16),
                              // Right Controls Dock
                              SizedBox(
                                width: dockWidth,
                                child: RightControlDock(
                                  settings: _processSettings,
                                  onSettingsChanged: (s) =>
                                      setState(() => _processSettings = s),
                                  onProcessRequested: _handleProcessDocument,
                                ),
                              ),
                            ],
                          );
                        } else {
                          // Stacked layout for smaller viewports (< 768px)
                          return SingleChildScrollView(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                SizedBox(
                                  height: math.max(
                                      360.0, constraints.maxHeight * 0.5),
                                  child: DocumentViewport(
                                    onBBoxSelected: (box) =>
                                        notifier.selectBBox(box),
                                  ),
                                ),
                                if (selectedBBox != null) ...[
                                  const SizedBox(height: 12),
                                  BBoxInspector(
                                    bbox: selectedBBox,
                                    onClose: () => notifier.selectBBox(null),
                                  ),
                                ],
                                if (wsState.pageCount > 1) ...[
                                  const SizedBox(height: 12),
                                  const PageStrip(
                                      orientation: Axis.horizontal),
                                ],
                                const SizedBox(height: 16),
                                RightControlDock(
                                  settings: _processSettings,
                                  onSettingsChanged: (s) =>
                                      setState(() => _processSettings = s),
                                  onProcessRequested: _handleProcessDocument,
                                ),
                              ],
                            ),
                          );
                        }
                      },
                    ),
                  ),
          ),

          // Live Bottom Progress Dock
          if (hasDoc || wsState.isProcessing) const BottomProgressDock(),
        ],
      ),
    );
  }

  Widget _buildHeaderBar(
    BuildContext context,
    AppColorScheme colors,
    WorkstationState wsState,
    WorkstationNotifier notifier,
  ) {
    return Container(
      height: 52,
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: BoxDecoration(
        color: colors.card,
        border: Border(bottom: BorderSide(color: colors.border)),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          // Document Filename / Status
          Flexible(
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(
                    color: colors.brand.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: colors.brand.withValues(alpha: 0.3)),
                  ),
                  child: Center(
                    child: Icon(Icons.document_scanner,
                        size: 16, color: colors.brand),
                  ),
                ),
                const SizedBox(width: 12),
                Flexible(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        wsState.hasDocument
                            ? ((wsState.filename != null && wsState.filename!.isNotEmpty) ? wsState.filename! : 'Active Document')
                            : 'OmniScribe',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTypography.titleSmall(
                          color: colors.textPrimary,
                        ),
                      ),
                      Text(
                        wsState.hasDocument
                            ? '${wsState.pageCount} page${wsState.pageCount == 1 ? "" : "s"} • ${wsState.allBBoxes.length} bounding boxes'
                            : 'GPU-Accelerated Document Workstation',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: AppTypography.codeSmall(
                          color: colors.textMuted,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),

          // Header Actions (Page Navigation, Layer Toggles, Export, Clear, Status Badge)
          Flexible(
            flex: 3,
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
              // Multi-page navigation (when wsState.pageCount > 1)
              if (wsState.hasDocument && wsState.pageCount > 1) ...[
                IconButton(
                  icon: const Icon(Icons.chevron_left_rounded, size: 20),
                  tooltip: 'Previous page',
                  padding: EdgeInsets.zero,
                  constraints:
                      const BoxConstraints(minWidth: 32, minHeight: 32),
                  color: wsState.selectedPageIndex > 0
                      ? colors.textPrimary
                      : colors.textMuted,
                  onPressed: wsState.selectedPageIndex > 0
                      ? () => notifier.selectPage(wsState.selectedPageIndex - 1)
                      : null,
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: Text(
                    'Page ${wsState.selectedPageIndex + 1} of ${wsState.pageCount}',
                    style: AppTypography.codeSmall(
                      color: colors.textPrimary,
                    ).copyWith(fontWeight: FontWeight.w500),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.chevron_right_rounded, size: 20),
                  tooltip: 'Next page',
                  padding: EdgeInsets.zero,
                  constraints:
                      const BoxConstraints(minWidth: 32, minHeight: 32),
                  color: wsState.selectedPageIndex < wsState.pageCount - 1
                      ? colors.textPrimary
                      : colors.textMuted,
                  onPressed: wsState.selectedPageIndex < wsState.pageCount - 1
                      ? () => notifier.selectPage(wsState.selectedPageIndex + 1)
                      : null,
                ),
                const SizedBox(width: 8),
                Container(width: 1, height: 20, color: colors.border),
                const SizedBox(width: 8),
              ],

              // Layer toggles (when wsState.hasDocument)
              if (wsState.hasDocument) ...[
                Tooltip(
                  message: wsState.showBBoxes
                      ? 'Hide bounding boxes'
                      : 'Show bounding boxes',
                  child: InkWell(
                    onTap: () => notifier.toggleBBoxes(),
                    borderRadius: BorderRadius.circular(4),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        color: wsState.showBBoxes
                            ? colors.brand.withValues(alpha: 0.15)
                            : Colors.transparent,
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: wsState.showBBoxes
                              ? colors.brand.withValues(alpha: 0.4)
                              : colors.border,
                        ),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.crop_free_rounded,
                            size: 14,
                            color: wsState.showBBoxes
                                ? colors.brand
                                : colors.textMuted,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            'Boxes',
                            style: AppTypography.bodySmall(
                              color: wsState.showBBoxes
                                  ? colors.brand
                                  : colors.textMuted,
                            ).copyWith(
                                fontSize: 11, fontWeight: FontWeight.w500),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Tooltip(
                  message: wsState.showHeatmap
                      ? 'Disable confidence heatmap'
                      : 'Enable confidence heatmap',
                  child: InkWell(
                    onTap: () => notifier.toggleHeatmap(),
                    borderRadius: BorderRadius.circular(4),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        color: wsState.showHeatmap
                            ? colors.success.withValues(alpha: 0.15)
                            : Colors.transparent,
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: wsState.showHeatmap
                              ? colors.success.withValues(alpha: 0.4)
                              : colors.border,
                        ),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.gradient_rounded,
                            size: 14,
                            color: wsState.showHeatmap
                                ? colors.success
                                : colors.textMuted,
                          ),
                          const SizedBox(width: 4),
                          Text(
                            'Heatmap',
                            style: AppTypography.bodySmall(
                              color: wsState.showHeatmap
                                  ? colors.success
                                  : colors.textMuted,
                            ).copyWith(
                                fontSize: 11, fontWeight: FontWeight.w500),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Container(width: 1, height: 20, color: colors.border),
                const SizedBox(width: 8),
              ],

              // Header Actions (Export, Clear, Status Badge)
              if (wsState.hasDocument) ...[
                AppButton(
                  text: 'Export',
                  variant: AppButtonVariant.secondary,
                  size: AppButtonSize.sm,
                  icon: const Icon(Icons.file_download_outlined, size: 14),
                  onPressed: () {
                    ExportModal.show(context);
                  },
                ),
                const SizedBox(width: 8),
                AppButton(
                  text: 'Clear Document',
                  variant: AppButtonVariant.ghost,
                  size: AppButtonSize.sm,
                  icon: const Icon(Icons.clear_all_rounded, size: 14),
                  onPressed: () {
                    notifier.clearDocument();
                  },
                ),
                const SizedBox(width: 8),
              ] else ...[
                // Sprint 3 (RFC 002 §4 Option b, audit U12): the
                // "Try sample PDF" affordance. A new user has no
                // PDF of their own to upload; this button fetches
                // a canonical fixture from
                // ``/api/sample-pdf/{defaultFixture}`` and stages
                // it as the active document. The button is only
                // visible in the empty-state (no document loaded)
                // because once the user has their own document,
                // they don't need the sample.
                AppButton(
                  text: 'Try sample PDF',
                  variant: AppButtonVariant.secondary,
                  size: AppButtonSize.sm,
                  icon: const Icon(Icons.description_outlined, size: 14),
                  onPressed: () {
                    // Fire-and-forget: the notifier pushes the
                    // result into the workstation state, which
                    // the screen already renders. The button
                    // gets disabled implicitly while the
                    // ``isProcessing`` flag is set.
                    unawaited(
                      ref
                          .read(workstationProvider.notifier)
                          .tryWithSamplePdf(),
                    );
                  },
                ),
                const SizedBox(width: 8),
                const AppBadge(
                  label: 'DOCUVERSE 2.0',
                  variant: AppBadgeVariant.brand,
                  size: AppBadgeSize.sm,
                ),
                const SizedBox(width: 8),
              ],
              AppBadge(
                label: wsState.hasDocument ? 'LOADED' : 'READY',
                variant: wsState.hasDocument
                    ? AppBadgeVariant.success
                    : AppBadgeVariant.neutral,
                size: AppBadgeSize.sm,
              ),
            ],
          ),
        ),
      ),
        ],
      ),
    );
  }
}
