"""Standalone smoke test for an already-built bundle.

The full ``scripts/build_windows.py --smoke`` re-runs the build
(and the ``uv sync`` step that can fail on Windows file locks
during dev). This script just boots the existing binary at
``dist/omniscribe-server.exe`` and hits ``/api/health``.

Usage:
    uv run python scripts/smoke_existing.py [--port 18766] [--deadline-s 90]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN_NAME = "omniscribe-server.exe" if sys.platform == "win32" else "omniscribe-server"
BINARY = ROOT / "dist" / BIN_NAME


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Boot an already-built omniscribe-server bundle and "
        "require /api/health -> 200 within the deadline."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=18766,
        help="port the bundled server is probed on (default: 18766)",
    )
    parser.add_argument(
        "--deadline-s",
        type=int,
        default=90,
        help="seconds to wait for a healthy boot (default: 90)",
    )
    args = parser.parse_args()
    port = args.port
    deadline_s = args.deadline_s
    if not BINARY.exists():
        print(f"FAIL: binary not found at {BINARY}")
        return 2
    size_mb = BINARY.stat().st_size / 1024 / 1024
    print(f"binary: {BINARY}")
    print(f"size:   {size_mb:.1f} MB")
    print(f"launching: {BINARY.name} --port {port}")
    proc = subprocess.Popen(
        [str(BINARY), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.time() + deadline_s
    health_ok = False
    health_body = ""
    boot_log: list[str] = []
    try:
        while time.time() < deadline:
            if proc.stdout is not None:
                line = proc.stdout.readline()
                if line:
                    boot_log.append(line.rstrip())
                    if (
                        "Uvicorn running on" in line
                        or "Application startup" in line
                        or "ERROR" in line
                    ):
                        print(line.rstrip())
            if proc.poll() is not None:
                tail = "\n".join(boot_log[-30:])
                raise SystemExit(
                    f"binary exited with rc={proc.returncode} before health check.\n"
                    f"--- last 30 lines of boot log ---\n{tail}"
                )
            if not health_ok:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=2
                    ) as resp:
                        health_body = resp.read().decode("utf-8", errors="replace")
                        if resp.status == 200:
                            health_ok = True
                            print(
                                f"\nhealth check OK: /api/health -> 200 "
                                f"{health_body.strip()[:200]}"
                            )
                            break
                except Exception:
                    time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if not health_ok:
        tail = "\n".join(boot_log[-30:])
        raise SystemExit(
            f"health check did not return 200 within {deadline_s}s.\n"
            f"--- last 30 lines of boot log ---\n{tail}"
        )
    print(f"\nSMOKE PASS: bundle serves /api/health -> 200 in {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
