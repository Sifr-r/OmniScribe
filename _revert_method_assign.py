"""Revert specific lines back to [method-assign] ignore code."""
from pathlib import Path

p = Path("tests/core/ocr/test_ocr.py")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
target_lines = {491, 516, 538, 561, 580, 602, 623, 644}
for i in target_lines:
    idx = i - 1
    if 0 <= idx < len(lines):
        lines[idx] = lines[idx].replace(
            "type: ignore[assignment]", "type: ignore[method-assign]"
        )
p.write_text("".join(lines), encoding="utf-8")
print("done")
