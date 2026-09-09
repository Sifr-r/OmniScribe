"""Remove '# type: ignore[...]' or '# type: ignore' comments from lines that mypy flagged as unused-ignore.

For each target (file, line), read the file, replace the line's trailing '# type: ignore[...]' comment,
and write back. This is targeted: only the lines mypy reported.
"""
from __future__ import annotations

import re
from pathlib import Path

# (file_path, line_number_1_indexed)
targets: list[tuple[str, int]] = [
    ("tests/conftest.py", 296),
    ("tests/core/lexicon/test_lexicon_store.py", 561),
    ("tests/core/lexicon/test_lexicon_store.py", 742),
    ("tests/core/translate/test_translation_evaluator.py", 116),
    ("tests/core/translate/test_translation_evaluator.py", 117),
    ("tests/core/translate/test_translation_evaluator.py", 269),
    ("tests/core/translate/test_translation_evaluator.py", 270),
    ("tests/core/translate/test_translation_evaluator.py", 283),
    ("tests/core/translate/test_translation_evaluator.py", 295),
    ("tests/core/translate/test_translation_evaluator.py", 540),
    ("tests/core/translate/test_translation_evaluator.py", 547),
    ("tests/core/translate/test_translation_evaluator.py", 571),
    ("tests/core/translate/test_translation_evaluator.py", 572),
    ("tests/plugins/test_ocr_schemas.py", 504),
    ("tests/plugins/test_state_backend_plugin.py", 68),
    ("tests/plugins/test_state_backend_plugin.py", 83),
    ("tests/test_server_boot.py", 33),
    ("tests/test_server_boot.py", 73),
    ("tests/test_server_boot.py", 84),
    ("tests/test_server_boot.py", 97),
    ("tests/test_server_boot.py", 124),
    ("tests/test_server_boot.py", 149),
    ("tests/test_server_boot.py", 179),
]

ignore_re = re.compile(r"\s*#\s*type:\s*ignore(?:\[[^\]]*\])?\s*$")

grouped: dict[str, list[int]] = {}
for path, line_no in targets:
    grouped.setdefault(path, []).append(line_no)

for path, line_nos in grouped.items():
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    for line_no in line_nos:
        idx = line_no - 1
        if idx >= len(lines):
            print(f"SKIP {path}:{line_no} (out of range)")
            continue
        original = lines[idx]
        stripped = ignore_re.sub("", original.rstrip("\r\n"))
        if stripped != original.rstrip("\r\n"):
            # Preserve original line ending
            ending = original[len(original.rstrip("\r\n")):]
            lines[idx] = stripped + ending
            print(f"FIXED {path}:{line_no}")
        else:
            print(f"NO-CHANGE {path}:{line_no}: {original!r}")
    p.write_text("".join(lines), encoding="utf-8")
