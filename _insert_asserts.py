"""Insert `assert pipe is not None` after `else:` lines that lead to pipe.set/pipe.zadd.

Simpler: insert at the start of each line that has `pipe.set` or `pipe.zadd` an
assertion in the previous line. We use a regex over the file text.
"""
from __future__ import annotations

import re
from pathlib import Path

p = Path("scripts/migrate_sqlite_to_redis.py")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
out: list[str] = []
for i, line in enumerate(lines):
    stripped = line.lstrip()
    indent_len = len(line) - len(stripped)
    indent = line[:indent_len]
    if re.match(r"pipe\.(set|zadd|execute)\(", stripped):
        # Insert assert line just before
        out.append(f"{indent}assert pipe is not None\n")
    out.append(line)
p.write_text("".join(out), encoding="utf-8")
print("done")
