"""Verify all touched files still parse as valid Python."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FILES = [
    "src/omniscribe/plugins/documents/routes.py",
    "src/omniscribe/plugins/glossary/routes.py",
    "src/omniscribe/plugins/transcribe/routes.py",
    "src/omniscribe/plugins/translate/routes.py",
    "src/omniscribe/plugins/state_backend_sqlite.py",
    "src/omniscribe/plugins/state_backend_memory.py",
    "src/omniscribe/plugins/state_backend_redis.py",
    "src/omniscribe/core/workflows/hybrid.py",
    "src/omniscribe/core/workflows/grounded.py",
    "src/omniscribe/pipeline.py",
    "src/omniscribe/plugins/ocr/plugin.py",
    "src/omniscribe/server.py",
    "src/omniscribe/plugins/providers.py",
    "src/omniscribe/plugins/health.py",
    "src/omniscribe/plugins/jobs.py",
    "src/omniscribe/plugins/progress.py",
]


def main() -> int:
    root = Path("D:/OmniScribe")
    failed: list[str] = []
    for rel in FILES:
        path = root / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failed.append(f"{rel}: {exc}")
            continue
        # Count public defs with docstrings
        defs: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_") and not node.name.startswith("__"):
                    continue
                defs.append(node)
        with_doc = 0
        for d in defs:
            body = getattr(d, "body", None) or []
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                with_doc += 1
        ratio = with_doc / len(defs) if defs else 1.0
        print(f"{rel}: {len(defs)} defs, {with_doc} docs, {ratio * 100:.0f}%")
    if failed:
        print("FAILED:")
        for f in failed:
            print(" ", f)
        return 1
    print("All touched files parse cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
