import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:omniscribe_client/core/theme/app_colors.dart';
import 'package:omniscribe_client/core/theme/app_typography.dart';

/// Clean, strictly-typed text input primitive for OmniScribe DocuVerse UI.
///
/// Features:
/// - Label with optional required marker (`*`)
/// - Placeholder / hint text
/// - Helper text & validation error messages
/// - Leading prefix icon & trailing suffix icon
/// - Quick clear button
/// - Monospace mode for keys/tokens/hashes
/// - Password obscure text with visibility toggle
/// - Glow ring on focus and error states
class AppInput extends StatefulWidget {
  const AppInput({
    super.key,
    this.label,
    this.placeholder,
    this.helperText,
    this.errorText,
    this.prefixIcon,
    this.suffixIcon,
    this.controller,
    this.initialValue,
    this.onChanged,
    this.onSubmitted,
    this.focusNode,
    this.obscureText = false,
    this.showClearButton = false,
    this.isRequired = false,
    this.enabled = true,
    this.readOnly = false,
    this.autofocus = false,
    this.monospace = false,
    this.maxLines = 1,
    this.minLines,
    this.keyboardType,
    this.textInputAction,
    this.inputFormatters,
    this.validator,
    this.testId,
  });

  final String? label;
  final String? placeholder;
  final String? helperText;
  final String? errorText;
  final Widget? prefixIcon;
  final Widget? suffixIcon;
  final TextEditingController? controller;
  final String? initialValue;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;
  final FocusNode? focusNode;
  final bool obscureText;
  final bool showClearButton;
  final bool isRequired;
  final bool enabled;
  final bool readOnly;
  final bool autofocus;
  final bool monospace;
  final int? maxLines;
  final int? minLines;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final List<TextInputFormatter>? inputFormatters;
  final FormFieldValidator<String>? validator;
  final String? testId;

  @override
  State<AppInput> createState() => _AppInputState();
}

class _AppInputState extends State<AppInput> {
  late TextEditingController _effectiveController;
  late FocusNode _effectiveFocusNode;
  bool _isObscured = false;
  bool _isFocused = false;
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    _effectiveController =
        widget.controller ?? TextEditingController(text: widget.initialValue);
    _effectiveFocusNode = widget.focusNode ?? FocusNode();
    _isObscured = widget.obscureText;
    _hasText = _effectiveController.text.isNotEmpty;

    _effectiveController.addListener(_onTextChange);
    _effectiveFocusNode.addListener(_onFocusChange);
  }

  @override
  void didUpdateWidget(covariant AppInput oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.controller != null &&
        widget.controller != _effectiveController) {
      _effectiveController.removeListener(_onTextChange);
      _effectiveController = widget.controller!;
      _effectiveController.addListener(_onTextChange);
    }
    if (widget.focusNode != null && widget.focusNode != _effectiveFocusNode) {
      _effectiveFocusNode.removeListener(_onFocusChange);
      _effectiveFocusNode = widget.focusNode!;
      _effectiveFocusNode.addListener(_onFocusChange);
    }
  }

  @override
  void dispose() {
    _effectiveController.removeListener(_onTextChange);
    _effectiveFocusNode.removeListener(_onFocusChange);
    if (widget.controller == null) {
      _effectiveController.dispose();
    }
    if (widget.focusNode == null) {
      _effectiveFocusNode.dispose();
    }
    super.dispose();
  }

  void _onTextChange() {
    final has = _effectiveController.text.isNotEmpty;
    if (has != _hasText) {
      setState(() => _hasText = has);
    }
  }

  void _onFocusChange() {
    setState(() => _isFocused = _effectiveFocusNode.hasFocus);
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final hasError = widget.errorText != null && widget.errorText!.isNotEmpty;

    // Resolve border color & ring glow
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
    } else if (_isFocused) {
      borderColor = colors.brand;
      ringGlow = [
        BoxShadow(
          color: colors.brand.withValues(alpha: 0.22),
          blurRadius: 6,
          spreadRadius: 1,
        ),
      ];
    }

    final textStyle = widget.monospace
        ? AppTypography.code(color: colors.textPrimary)
        : AppTypography.bodyMedium(color: colors.textPrimary);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (widget.label != null) ...[
          Row(
            children: [
              Flexible(
                child: Text(
                  widget.label!,
                  style:
                      AppTypography.captionStrong(color: colors.textSecondary),
                ),
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

        // Input container with glow ring
        AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          curve: Curves.easeOut,
          decoration: BoxDecoration(
            color: colors.card,
            borderRadius: BorderRadius.circular(6),
            border: Border.all(
                color: borderColor, width: _isFocused || hasError ? 1.5 : 1),
            boxShadow: ringGlow,
          ),
          child: Row(
            crossAxisAlignment: widget.maxLines != null && widget.maxLines! > 1
                ? CrossAxisAlignment.start
                : CrossAxisAlignment.center,
            children: [
              if (widget.prefixIcon != null)
                Padding(
                  padding: const EdgeInsets.only(
                      left: 12, right: 8, top: 10, bottom: 10),
                  child: IconTheme(
                    data: IconThemeData(
                      color: _isFocused ? colors.brand : colors.textMuted,
                      size: 16,
                    ),
                    child: widget.prefixIcon!,
                  ),
                ),

              Expanded(
                child: TextFormField(
                  controller: _effectiveController,
                  focusNode: _effectiveFocusNode,
                  enabled: widget.enabled,
                  readOnly: widget.readOnly,
                  autofocus: widget.autofocus,
                  obscureText: _isObscured,
                  maxLines: widget.obscureText ? 1 : widget.maxLines,
                  minLines: widget.minLines,
                  keyboardType: widget.keyboardType,
                  textInputAction: widget.textInputAction,
                  inputFormatters: widget.inputFormatters,
                  validator: widget.validator,
                  onChanged: widget.onChanged,
                  onFieldSubmitted: widget.onSubmitted,
                  style: textStyle,
                  cursorColor: colors.brand,
                  decoration: InputDecoration(
                    hintText: widget.placeholder,
                    hintStyle: widget.monospace
                        ? AppTypography.code(color: colors.textMuted)
                        : AppTypography.bodyMedium(color: colors.textMuted),
                    isDense: true,
                    contentPadding: EdgeInsets.only(
                      left: widget.prefixIcon == null ? 12 : 0,
                      right: 12,
                      top: 10,
                      bottom: 10,
                    ),
                    border: InputBorder.none,
                    enabledBorder: InputBorder.none,
                    focusedBorder: InputBorder.none,
                    errorBorder: InputBorder.none,
                    focusedErrorBorder: InputBorder.none,
                    disabledBorder: InputBorder.none,
                    filled: false,
                  ),
                ),
              ),

              // Clear button
              if (widget.showClearButton &&
                  _hasText &&
                  widget.enabled &&
                  !widget.readOnly)
                MouseRegion(
                  cursor: SystemMouseCursors.click,
                  child: GestureDetector(
                    onTap: () {
                      _effectiveController.clear();
                      widget.onChanged?.call('');
                    },
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 6),
                      child: Icon(
                        Icons.close,
                        size: 14,
                        color: colors.textMuted,
                      ),
                    ),
                  ),
                ),

              // Obscure text toggle
              if (widget.obscureText)
                MouseRegion(
                  cursor: SystemMouseCursors.click,
                  child: GestureDetector(
                    onTap: () => setState(() => _isObscured = !_isObscured),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                      child: Icon(
                        _isObscured
                            ? Icons.visibility_outlined
                            : Icons.visibility_off_outlined,
                        size: 16,
                        color: colors.textMuted,
                      ),
                    ),
                  ),
                ),

              if (widget.suffixIcon != null)
                Padding(
                  padding: const EdgeInsets.only(right: 10),
                  child: IconTheme(
                    data: IconThemeData(
                      color: colors.textMuted,
                      size: 16,
                    ),
                    child: widget.suffixIcon!,
                  ),
                ),
            ],
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
