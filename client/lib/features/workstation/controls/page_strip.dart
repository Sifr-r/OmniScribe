import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';
import 'package:omniscribe_client/features/workstation/workstation_notifier.dart';

/// Thumbnail strip allowing users to quickly preview and switch between pages.
class PageStrip extends ConsumerStatefulWidget {
  const PageStrip({
    super.key,
    this.orientation = Axis.horizontal,
  });

  final Axis orientation;

  @override
  ConsumerState<PageStrip> createState() => _PageStripState();
}

class _PageStripState extends ConsumerState<PageStrip> {
  late final ScrollController _scrollController;

  @override
  void initState() {
    super.initState();
    _scrollController = ScrollController();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        final initialIndex = ref.read(workstationProvider).selectedPageIndex;
        if (initialIndex > 0) {
          _scrollToIndex(initialIndex, animate: false);
        }
      }
    });
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToIndex(int index, {bool animate = true}) {
    if (!_scrollController.hasClients) return;

    final isHorizontal = widget.orientation == Axis.horizontal;
    final itemDimension = isHorizontal ? 68.0 : 116.0;
    const separatorDimension = 8.0;
    final slotSize = itemDimension + separatorDimension;

    final maxScroll = _scrollController.position.maxScrollExtent;
    final targetOffset = (index * slotSize).clamp(0.0, maxScroll);

    if (animate) {
      _scrollController.animateTo(
        targetOffset,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeInOut,
      );
    } else {
      _scrollController.jumpTo(targetOffset);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final wsState = ref.watch(workstationProvider);
    final notifier = ref.read(workstationProvider.notifier);

    // Auto-scroll when selected page index changes
    ref.listen<int>(
      workstationProvider.select((s) => s.selectedPageIndex),
      (previous, next) {
        if (previous != next) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) {
              _scrollToIndex(next);
            }
          });
        }
      },
    );

    final pageCount = wsState.pageCount;
    final selectedIdx = wsState.selectedPageIndex;

    if (pageCount <= 1) {
      return const SizedBox.shrink();
    }

    final isHorizontal = widget.orientation == Axis.horizontal;
    final scannedCount = wsState.pages.where((p) => p.bboxes.isNotEmpty).length;

    return Container(
      height: isHorizontal ? 108 : null,
      width: isHorizontal ? null : 116,
      padding: EdgeInsets.symmetric(
        horizontal: isHorizontal ? 12 : 8,
        vertical: 8,
      ),
      decoration: BoxDecoration(
        color: colors.cardRaised,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (isHorizontal)
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  'PAGES ($pageCount)',
                  style: AppTypography.micro(
                    color: colors.textMuted,
                  ),
                ),
                Text(
                  '$scannedCount/$pageCount scanned',
                  style: AppTypography.codeSmall(
                    color: colors.textMuted,
                  ).copyWith(fontSize: 10),
                ),
              ],
            )
          else ...[
            Text(
              'PAGES ($pageCount)',
              style: AppTypography.micro(
                color: colors.textMuted,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              '$scannedCount/$pageCount scanned',
              style: AppTypography.codeSmall(
                color: colors.textMuted,
              ).copyWith(fontSize: 9),
            ),
          ],
          const SizedBox(height: 6),
          Expanded(
            child: ListView.separated(
              controller: _scrollController,
              scrollDirection: widget.orientation,
              itemCount: pageCount,
              separatorBuilder: (_, __) => SizedBox(
                width: isHorizontal ? 8 : 0,
                height: isHorizontal ? 0 : 8,
              ),
              itemBuilder: (context, index) {
                final isSelected = index == selectedIdx;
                final pageResult =
                    index < wsState.pages.length ? wsState.pages[index] : null;
                final boxCount = pageResult?.bboxes.length ?? 0;
                final hasScanned = boxCount > 0;

                return SizedBox(
                  width: isHorizontal ? 68 : null,
                  height: isHorizontal ? null : 116,
                  child: InkWell(
                    onTap: () => notifier.selectPage(index),
                    borderRadius: BorderRadius.circular(6),
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 150),
                      padding: const EdgeInsets.all(4),
                      decoration: BoxDecoration(
                        color: isSelected
                            ? colors.brand.withValues(alpha: 0.15)
                            : colors.card,
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(
                          color: isSelected ? colors.brand : colors.border,
                          width: isSelected ? 1.8 : 1.0,
                        ),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          // Page Mini Card Preview
                          Expanded(
                            child: Container(
                              decoration: BoxDecoration(
                                color: colors.cardRaised,
                                borderRadius: BorderRadius.circular(4),
                                border: Border.all(
                                  color: colors.border.withValues(alpha: 0.5),
                                ),
                              ),
                              child: Stack(
                                children: [
                                  if (pageResult?.previewBytes != null)
                                    Positioned.fill(
                                      child: ClipRRect(
                                        borderRadius: BorderRadius.circular(3),
                                        child: Image.memory(
                                          pageResult!.previewBytes!,
                                          fit: BoxFit.cover,
                                        ),
                                      ),
                                    )
                                  else
                                    Center(
                                      child: Icon(
                                        Icons.description_outlined,
                                        size: 20,
                                        color: isSelected
                                            ? colors.brand
                                            : colors.textMuted,
                                      ),
                                    ),
                                  if (hasScanned)
                                    Positioned(
                                      top: 2,
                                      right: 2,
                                      child: Container(
                                        padding: const EdgeInsets.all(2),
                                        decoration: BoxDecoration(
                                          color: colors.success,
                                          shape: BoxShape.circle,
                                        ),
                                        child: const Icon(
                                          Icons.check,
                                          size: 8,
                                          color: Colors.white,
                                        ),
                                      ),
                                    ),
                                ],
                              ),
                            ),
                          ),
                          const SizedBox(height: 4),
                          FittedBox(
                            fit: BoxFit.scaleDown,
                            child: Text(
                              boxCount > 0
                                  ? 'P.${index + 1} ($boxCount)'
                                  : 'P.${index + 1}',
                              style: AppTypography.codeSmall(
                                color: isSelected
                                    ? colors.brand
                                    : colors.textMuted,
                              ).copyWith(
                                fontSize: 10,
                                fontWeight: isSelected
                                    ? FontWeight.w700
                                    : FontWeight.w500,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
