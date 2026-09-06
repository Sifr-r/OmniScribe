"""Test-driven iterative bundling.

Runs the binary, captures the first ``ModuleNotFoundError`` or
``ImportError`` that crashes it, adds the missing module to the
spec's hiddenimports, rebuilds, and retries. Exits when the
binary boots successfully or when the missing module is already
in the spec.

This is a workaround for the well-known PyInstaller static-analysis
gap on packages like scipy / transformers that use deep private
submodule trees with lazy import patterns.

Usage:
    uv run python scripts/iterative_bundle.py [--port 18770] [--max-iter 30]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "omniscribe_server.spec"
DIST = ROOT / "dist"
BIN_NAME = "omniscribe-server.exe" if sys.platform == "win32" else "omniscribe-server"
BINARY = DIST / BIN_NAME

# Match either: ModuleNotFoundError: No module named 'X'
# Or:            ImportError: ... X ...
# We want the deepest missing module name from the traceback.
MISSING_PATTERNS = [
    re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
    re.compile(r"ImportError: cannot import name '([^']+)' from '([^']+)'"),
    re.compile(r"ImportError: The `(\w+)` install you are using seems to be broken"),
]


def run_binary(port: int) -> tuple[int, str]:
    """Run the bundle until it exits (crash) or until 90s elapse.

    A clean boot that reaches /api/health ready state will keep running;
    in that case we kill it after the deadline. The 90s window is
    generous — the bundle takes 30-60s to extract the onefile and
    import torch + transformers, so we want to give it time.
    """
    proc = subprocess.Popen(
        [str(BINARY), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.time() + 90
    log_lines: list[str] = []
    try:
        while time.time() < deadline:
            if proc.stdout is not None:
                line = proc.stdout.readline()
                if line:
                    log_lines.append(line.rstrip())
            if proc.poll() is not None:
                # Process exited — drain any remaining stdout briefly.
                if proc.stdout is not None:
                    for line in proc.stdout.readlines():
                        log_lines.append(line.rstrip())
                break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return proc.returncode if proc.returncode is not None else 1, "\n".join(log_lines)


def find_missing_module(log: str) -> str | None:
    """Scan the log for the first missing module name."""
    for pat in MISSING_PATTERNS:
        m = pat.search(log)
        if m:
            # Return the most-specific module name
            return m.group(1)
    return None


def add_hiddenimport(spec_text: str, module: str) -> str:
    """Add a module to the spec's hiddenimports list if not already there."""
    if f'"{module}"' in spec_text:
        return spec_text
    # Insert into the manual hiddenimports list (the one with the
    # torch._C, surya.model.recognition entries, etc.).
    new_entry = f'        "{module}",\n'
    return spec_text.replace(
        '+ [\n        "torch._C",',
        "+ [\n" + new_entry + '        "torch._C",',
        1,
    )


def rebuild() -> bool:
    """Run PyInstaller against the spec. Returns True on success."""
    print(
        "\n$ uv run --no-sync python -m PyInstaller --noconfirm --clean omniscribe_server.spec"
    )
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(SPEC),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Iteratively add missing hiddenimports to the PyInstaller "
        "spec until the bundled binary boots (PyInstaller lazy-import gap workaround)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18770,
        help="port the bundled server is probed on (default: 18770)",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=30,
        help="maximum build/fix iterations before giving up (default: 30)",
    )
    args = parser.parse_args()

    if not BINARY.exists():
        print(f"binary not found at {BINARY} — running first build")
        if not rebuild():
            print("initial build failed")
            return 1

    for iteration in range(1, args.max_iter + 1):
        print(f"\n=== iteration {iteration} ===")
        rc, log = run_binary(args.port)
        if rc == 0:
            print(f"\nBOOT OK after {iteration - 1} fixes")
            return 0
        missing = find_missing_module(log)
        if not missing:
            print("\nFAIL: no missing module pattern matched. Last log:")
            print("\n".join(log.splitlines()[-30:]))
            return 1
        print(f"  missing: {missing!r}")
        spec_text = SPEC.read_text(encoding="utf-8")
        if f'"{missing}"' in spec_text:
            print(f"  {missing!r} already in spec; not a hiddenimports gap")
            print("\nLast 30 lines of log:")
            print("\n".join(log.splitlines()[-30:]))
            return 1
        new_spec = add_hiddenimport(spec_text, missing)
        SPEC.write_text(new_spec, encoding="utf-8")
        print(f"  added {missing!r} to spec; rebuilding")
        if not rebuild():
            print("  rebuild failed")
            return 1
    print(f"\nreached MAX_ITER={args.max_iter} without success")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
