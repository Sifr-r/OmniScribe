import 'package:flutter/material.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// Item definition for [AppSelect].
class AppSelectItem<T> {
  const AppSelectItem({
    required this.value,
    required this.label,
    this.subtitle,
    this.icon,
    this.disabled = false,
  });

  final T value;
  final String label;
  final String? subtitle;
  final Widget? icon;
  final bool disabled;
}

/// Generic, DocuVerse-styled dropdown selector widget.
class AppSelect<T> extends StatefulWidget {
  const AppSelect({
    super.key,
    required this.items,
    this.value,
    this.onChanged,
    this.label,
    this.placeholder = 'Select an option...',
    this.helperText,
    this.errorText,
    this.prefixIcon,
    this.isRequired = false,
    this.disabled = false,
    this.isExpanded = true,
    this.testId,
  });

  final List<AppSelectItem<T>> items;
  final T? value;
  final ValueChanged<T?>? onChanged;
  final String? label;
  final String placeholder;
  final String? helperText;
  final String? errorText;
  final Widget? prefixIcon;
  final bool isRequired;
  final bool disabled;
  final bool isExpanded;
  final String? testId;

  @override
  State<AppSelect<T>> createState() => _AppSelectState<T>();
}

class _AppSelectState<T> extends State<AppSelect<T>> {
  bool _isHovered = false;
  bool _isFocused = false;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final hasError = widget.errorText != null && widget.errorText!.isNotEmpty;
    final isInteractive = !widget.disabled && widget.onChanged != null;

    final selectedItem = widget.items.cast<AppSelectItem<T>?>().firstWhere(
          (AppSelectItem<T>? item) => item?.value == widget.value,
          orElse: () => null,
        );

    // Border color resolution
    Color borderColor = colors.inputBorder;
    List<BoxShadow>? ringGlow;

    if (hasError) {
      borderColor = colors.error;
      ringGlow = [
        BoxShadow(
          color: colors.error.withValues(alpha: 0.2),
          blurRadius: 6,
          spreadRadius: 1,
        ),
      ];
    } else if (_isFocused || _isHovered) {
      borderColor = _isFocused ? colors.brand : colors.borderStrong;
      if (_isFocused) {
        ringGlow = [
          BoxShadow(
            color: colors.brand.withValues(alpha: 0.22),
            blurRadius: 6,
            spreadRadius: 1,
          ),
        ];
      }
    }

    final triggerContent = Row(
      children: [
        if (widget.prefixIcon != null) ...[
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: IconTheme(
              data: IconThemeData(
                color: _isFocused ? colors.brand : colors.textMuted,
                size: 16,
              ),
              child: widget.prefixIcon!,
            ),
          ),
        ],
        if (selectedItem?.icon != null) ...[
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: IconTheme(
              data: IconThemeData(
                color: colors.brand,
                size: 16,
              ),
              child: selectedItem!.icon!,
            ),
          ),
        ],
        Expanded(
          child: Text(
            selectedItem != null ? selectedItem.label : widget.placeholder,
            style: AppTypography.bodyMedium(
              color:
                  selectedItem != null ? colors.textPrimary : colors.textMuted,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        const SizedBox(width: 8),
        Icon(
          Icons.unfold_more_rounded,
          size: 18,
          color: colors.textMuted,
        ),
      ],
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (widget.label != null) ...[
          Row(
            children: [
              Text(
                widget.label!,
                style: AppTypography.captionStrong(color: colors.textSecondary),
              ),
              if (widget.isRequired) ...[
                const SizedBox(width: 4),
                Text(
                  '*',
                  style: AppTypography.captionStrong(color: colors.error),
                ),
              ],
            ],
          ),
          const SizedBox(height: 6),
        ],

        // Selector Trigger Box
        Opacity(
          opacity: widget.disabled ? 0.45 : 1.0,
          child: MouseRegion(
            cursor: isInteractive
                ? SystemMouseCursors.click
                : SystemMouseCursors.basic,
            onEnter: (_) {
              if (isInteractive) setState(() => _isHovered = true);
            },
            onExit: (_) {
              if (isInteractive) setState(() => _isHovered = false);
            },
            child: PopupMenuButton<T>(
              enabled: isInteractive,
              initialValue: widget.value,
              onOpened: () => setState(() => _isFocused = true),
              onCanceled: () => setState(() => _isFocused = false),
              onSelected: (T? val) {
                setState(() => _isFocused = false);
                widget.onChanged?.call(val);
              },
              tooltip: '',
              color: colors.cardRaised,
              elevation: 8,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
                side: BorderSide(color: colors.borderStrong, width: 1),
              ),
              offset: const Offset(0, 40),
              itemBuilder: (BuildContext context) {
                return widget.items.map((AppSelectItem<T> item) {
                  final isSelected = item.value == widget.value;
                  return PopupMenuItem<T>(
                    value: item.value,
                    enabled: !item.disabled,
                    height: item.subtitle != null ? 48 : 36,
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                    child: Container(
                      decoration: BoxDecoration(
                        color: isSelected
                            ? colors.brand.withValues(alpha: 0.12)
                            : Colors.transparent,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 4),
                      child: Row(
                        children: [
                          if (item.icon != null) ...[
                            IconTheme(
                              data: IconThemeData(
                                color: isSelected
                                    ? colors.brand
                                    : colors.textSecondary,
                                size: 16,
                              ),
                              child: item.icon!,
                            ),
                            const SizedBox(width: 8),
                          ],
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Text(
                                  item.label,
                                  style: AppTypography.bodyMedium(
                                    color: item.disabled
                                        ? colors.textMuted
                                        : isSelected
                                            ? colors.brand
                                            : colors.textPrimary,
                                  ).copyWith(
                                    fontWeight: isSelected
                                        ? FontWeight.w600
                                        : FontWeight.w400,
                                  ),
                                ),
                                if (item.subtitle != null) ...[
                                  const SizedBox(height: 2),
                                  Text(
                                    item.subtitle!,
                                    style: AppTypography.bodySmall(
                                        color: colors.textMuted),
                                  ),
                                ],
                              ],
                            ),
                          ),
                          if (isSelected) ...[
                            const SizedBox(width: 8),
                            Icon(Icons.check_rounded,
                                size: 16, color: colors.brand),
                          ],
                        ],
                      ),
                    ),
                  );
                }).toList();
              },
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                curve: Curves.easeOut,
                height: 38,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                decoration: BoxDecoration(
                  color: colors.card,
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                    color: borderColor,
                    width: _isFocused || hasError ? 1.5 : 1,
                  ),
                  boxShadow: ringGlow,
                ),
                child: triggerContent,
              ),
            ),
          ),
        ),

        // Helper or Error text
        if (hasError) ...[
          const SizedBox(height: 4),
          Row(
            children: [
              Icon(Icons.error_outline, size: 12, color: colors.error),
              const SizedBox(width: 4),
              Expanded(
                child: Text(
                  widget.errorText!,
                  style: AppTypography.bodySmall(color: colors.error),
                ),
              ),
            ],
          ),
        ] else if (widget.helperText != null &&
            widget.helperText!.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(
            widget.helperText!,
            style: AppTypography.bodySmall(color: colors.textMuted),
          ),
        ],
      ],
    );
  }
}
