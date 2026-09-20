"""One-off audit: report docstring coverage for public functions/classes/methods."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def has_docstring(node: ast.AST) -> bool:
    body = getattr(node, "body", None)
    if not body:
        return False
    first = body[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def iter_public_defs(tree: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_") and not node.name.startswith("__"):
                continue
            out.append(node)
    return out


def main() -> int:
    src = Path("D:/OmniScribe/src")
    rows: list[tuple[str, int, int, float]] = []
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        defs = iter_public_defs(tree)
        if len(defs) < 4:
            continue
        with_doc = sum(1 for d in defs if has_docstring(d))
        ratio = with_doc / len(defs) if defs else 1.0
        rel = str(path.relative_to(src)).replace("\\", "/")
        rows.append((rel, len(defs), with_doc, ratio))

    rows.sort(key=lambda r: r[3])
    print(f"{'file':<70} {'defs':>5} {'docs':>5} {'%':>6}")
    for rel, n, w, r in rows[:50]:
        flag = "" if r > 0.5 else " <-- low"
        print(f"{rel:<70} {n:>5} {w:>5} {r * 100:>5.1f}%{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
