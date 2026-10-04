"""Build the OmniScribe Windows server bundle (Phase 4.4, RFC 001 Option A).

Usage:
    uv run python scripts/build_windows.py            # build only
    uv run python scripts/build_windows.py --smoke     # build + smoke-test
    uv run python scripts/build_windows.py --clean    # remove build/ + dist/

What this script does:
1. Verifies the venv has PyInstaller installed (it does, in the
   project's ``.venv``).
2. Runs PyInstaller against the spec at the repo root.
3. If ``--smoke``: starts the binary, polls ``/api/health`` until it
   returns 200, kills the process, prints the boot log + health
   response. This is the cheapest end-to-end verification possible
   without a real VLM endpoint.
4. Prints a one-line summary the maintainer can paste into the
   release notes (binary path, size, boot latency).

The spec lives at the repo root (``omniscribe_server.spec``) so the
``SPECPATH = abspath(SPEC)`` idiom resolves cleanly. Do not move it
into ``scripts/`` — the convention is "spec at root, source under
``src/``, build script under ``scripts/``".

Phase 4 timeline: 2 weeks. v0.2.0 ships the Windows binary; v0.3.0
adds macOS + Linux + the Flutter-embedded single-bundle path.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "omniscribe_server.spec"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
BINARY_NAME = (
    "omniscribe-server.exe" if sys.platform == "win32" else "omniscribe-server"
)
# Phase 4.4 fix: PyInstaller's EXE() pattern (without a separate
# COLLECT()) produces a onefile build that lands at
# ``dist/<name>`` directly, not ``dist/<name>/<name>``. The original
# path was wrong; onedir / onefile paths were confused.
BINARY = DIST_DIR / BINARY_NAME


def _run(cmd: list[str], **kwargs) -> None:
    """Run a subprocess, streaming output, failing loud on non-zero exit."""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"command failed (rc={result.returncode}): {' '.join(cmd)}")


def clean() -> None:
    """Remove build/ and dist/ — PyInstaller's intermediate and output dirs."""
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            print(f"removing {path}")
            shutil.rmtree(path, ignore_errors=True)


def build() -> None:
    """Run PyInstaller against the spec. Cold cache ~5-10 min; warm ~2-3 min.

    Phase 4.4 fix: the bundle needs the ``web`` + ``preprocessing``
    extras at build time, otherwise PyInstaller's static analysis
    can't find uvicorn / fastapi / surya-ocr, the runtime guard at
    ``omniscribe.server.main()`` raises ``SystemExit`` with "uvicorn
    is not installed", and the binary boots for 1 second then dies.
    The first time the maintainer hits this they'll waste 5 minutes
    of PyInstaller time. ``uv sync --extra web --extra preprocessing``
    is idempotent — extra packages are added, base packages are
    untouched, lockfile is preserved.
    """
    if not SPEC.exists():
        raise SystemExit(f"spec not found at {SPEC}")
    _run(
        ["uv", "sync", "--extra", "web", "--extra", "preprocessing", "--quiet"],
        cwd=ROOT,
        check=True,
    )
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(SPEC),
        ],
        cwd=ROOT,
        check=True,
    )


def smoke() -> None:
    """Use the bounded, isolated gate for existing and freshly built bundles."""
    if __package__:
        from .smoke_existing import smoke as package_smoke

        result = package_smoke(BINARY, port=18765, deadline_s=90)
    else:
        from smoke_existing import smoke as standalone_smoke

        result = standalone_smoke(BINARY, port=18765, deadline_s=90)

    if result != 0:
        raise SystemExit("Windows bundle smoke failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--smoke", action="store_true", help="boot the binary and hit /api/health"
    )
    parser.add_argument(
        "--clean", action="store_true", help="remove build/ and dist/ first"
    )
    args = parser.parse_args()

    if args.clean:
        clean()
    build()
    if args.smoke:
        smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
