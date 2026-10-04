import 'dart:typed_data';

import 'package:desktop_drop/desktop_drop.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';
import 'package:omniscribe_client/shared/widgets/app_badge.dart';
import 'package:omniscribe_client/shared/widgets/app_button.dart';

/// Drag & Drop zone supporting desktop_drop and native file_picker
/// Supports PDF, PNG, JPG, WEBP, AVIF, TIFF and BMP with file size validation.
/// The list mirrors the server-side upload allowlist in
/// `src/omniscribe/plugins/ocr/routes.py`.
class UploadDropzone extends ConsumerStatefulWidget {
  const UploadDropzone({
    super.key,
    this.onFileLoaded,
    this.maxBytes = 50 * 1024 * 1024, // 50MB
  });

  final void Function(Uint8List bytes, String filename, int pageCount)?
      onFileLoaded;
  final int maxBytes;

  @override
  ConsumerState<UploadDropzone> createState() => _UploadDropzoneState();
}

class _UploadDropzoneState extends ConsumerState<UploadDropzone> {
  bool _isDragging = false;
  bool _isLoading = false;
  String? _errorMessage;

  static const List<String> supportedExtensions = [
    'pdf',
    'png',
    'jpg',
    'jpeg',
    'webp',
    'avif',
    'tif',
    'tiff',
    'bmp',
  ];

  Future<void> _pickFile() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      // Wave 16 / file_picker 12: ``FilePicker.platform`` was removed;
      // ``FilePicker.pickFiles`` is now a static method returning
      // ``List<PlatformFile>`` directly. ``withData: true`` is also
      // deprecated — read the bytes lazily via ``PlatformFile.readAsBytes()``.
      final result = await FilePicker.pickFiles(
        type: FileType.custom,
        allowedExtensions: supportedExtensions,
      );

      if (result.isNotEmpty) {
        final file = result.first;
        final name = file.name;

        try {
          final bytes = await file.readAsBytes();
          if (bytes.isNotEmpty) {
            _processFile(bytes, name, file.path);
          } else {
            setState(() {
              _errorMessage = 'Could not read file data';
            });
          }
        } catch (_) {
          setState(() {
            _errorMessage = 'Could not read file data';
          });
        }
      }
    } catch (err) {
      setState(() {
        _errorMessage = 'Failed to pick file: $err';
      });
    } finally {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  void _processFile(Uint8List bytes, String filename, String? filePath) {
    if (bytes.length > widget.maxBytes) {
      final maxMb = (widget.maxBytes / (1024 * 1024)).round();
      setState(() {
        _errorMessage = 'File exceeds maximum upload size of ${maxMb}MB';
      });
      return;
    }

    final ext = filename.split('.').last.toLowerCase();
    if (!supportedExtensions.contains(ext)) {
      setState(() {
        _errorMessage =
            'Unsupported format .$ext. Please upload PDF, PNG, JPG, WEBP, AVIF, TIFF, or BMP';
      });
      return;
    }

    // Estimate page count (1 for images, estimate from bytes for PDF)
    int estimatedPages = 1;
    if (ext == 'pdf') {
      estimatedPages = _estimatePdfPages(bytes);
    }

    final notifier = ref.read(workstationProvider.notifier);
    notifier.loadDocument(
      bytes,
      filename,
      pageCount: estimatedPages,
      filePath: filePath,
    );

    widget.onFileLoaded?.call(bytes, filename, estimatedPages);
  }

  int _estimatePdfPages(Uint8List bytes) {
    // Quick heuristic scan for /Type /Page in PDF binary
    try {
      final content = String.fromCharCodes(bytes);
      final pageMatches = RegExp(r'/Type\s*/Page\b').allMatches(content);
      final count = pageMatches.length;
      return count > 0 ? count : 1;
    } catch (_) {
      return 1;
    }
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / (1024 * 1024)).toStringAsFixed(2)} MB';
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);

    // React to file-pick shortcut signals (Ctrl+O from AppShell, future toolbar
    // entries, etc.) by opening the native file picker. The signal is a
    // monotonically increasing int so consecutive increments each fire exactly
    // one picker dialog (a plain bool toggle would collapse to its previous
    // value and be lost between rapid taps).
    ref.listen<int>(
      workstationProvider.select((s) => s.filePickSignal),
      (prev, next) {
        if (prev != null && next > prev) {
          _pickFile();
        }
      },
    );

    return DropTarget(
      onDragEntered: (_) => setState(() => _isDragging = true),
      onDragExited: (_) => setState(() => _isDragging = false),
      onDragDone: (details) async {
        setState(() => _isDragging = false);
        if (details.files.isNotEmpty) {
          final xfile = details.files.first;
          final bytes = await xfile.readAsBytes();
          _processFile(bytes, xfile.name, xfile.path);
        }
      },
      child: Container(
        decoration: BoxDecoration(
          color:
              _isDragging ? colors.brand.withValues(alpha: 0.12) : colors.card,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: _isDragging ? colors.brand : colors.borderStrong,
            width: _isDragging ? 2.0 : 1.2,
          ),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 48),
        child: Center(
          // Wrap in SingleChildScrollView so the dropzone can scroll
          // gracefully when the available vertical space is tight — e.g.
          // when the AuthRequiredBanner is mounted above the TabRibbon
          // (see app_shell.dart) at narrow viewport heights.
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Icon Circle with Ambient Glow
                Container(
                  width: 64,
                  height: 64,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: colors.brand.withValues(alpha: 0.15),
                    border: Border.all(
                      color: colors.brand.withValues(alpha: 0.35),
                      width: 1.5,
                    ),
                  ),
                  child: Icon(
                    _isDragging
                        ? Icons.file_download_outlined
                        : Icons.cloud_upload_outlined,
                    size: 32,
                    color: colors.brand,
                  ),
                ),
                const SizedBox(height: 20),

                // Title & Call to Action
                Text(
                  _isDragging
                      ? 'Drop file to load'
                      : 'Upload document for OCR processing',
                  style: AppTypography.titleMedium(
                    color: colors.textPrimary,
                  ),
                ),
                const SizedBox(height: 8),

                Text(
                  'Drag and drop your file here, or browse files on your computer',
                  style: AppTypography.bodySmall(
                    color: colors.textMuted,
                  ),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 16),

                // Format Pills
                const Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  alignment: WrapAlignment.center,
                  children: [
                    AppBadge(label: 'PDF', variant: AppBadgeVariant.brand),
                    AppBadge(label: 'PNG', variant: AppBadgeVariant.neutral),
                    AppBadge(label: 'JPG', variant: AppBadgeVariant.neutral),
                    AppBadge(label: 'WEBP', variant: AppBadgeVariant.neutral),
                    AppBadge(label: 'AVIF', variant: AppBadgeVariant.neutral),
                  ],
                ),
                const SizedBox(height: 24),

                // Browse Button
                AppButton(
                  text: 'Browse Files',
                  variant: AppButtonVariant.primary,
                  size: AppButtonSize.lg,
                  icon: const Icon(Icons.folder_open_rounded, size: 16),
                  loading: _isLoading,
                  onPressed: _pickFile,
                ),

                // Error banner if any
                if (_errorMessage != null) ...[
                  const SizedBox(height: 16),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: colors.error.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                          color: colors.error.withValues(alpha: 0.3)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.error_outline,
                            size: 16, color: colors.error),
                        const SizedBox(width: 8),
                        Flexible(
                          child: Text(
                            _errorMessage!,
                            style: AppTypography.bodySmall(
                              color: colors.error,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],

                // Current Document Status summary (if already loaded)
                if (wsState.hasDocument && wsState.filename != null) ...[
                  const SizedBox(height: 24),
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 12),
                    decoration: BoxDecoration(
                      color: colors.cardRaised,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: colors.border),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.check_circle_outline,
                            size: 18, color: colors.success),
                        const SizedBox(width: 8),
                        Text(
                          'Loaded: ${wsState.filename}',
                          style: AppTypography.codeSmall(
                            color: colors.textPrimary,
                          ),
                        ),
                        if (wsState.loadedBytes != null) ...[
                          const SizedBox(width: 8),
                          Text(
                            '(${_formatSize(wsState.loadedBytes!.length)}, ${wsState.pageCount} pages)',
                            style: AppTypography.codeSmall(
                              color: colors.textMuted,
                            ).copyWith(fontSize: 11),
                          ),
                        ],
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
