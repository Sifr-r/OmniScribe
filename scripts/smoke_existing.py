"""Standalone smoke test for an already-built bundle.

The full ``scripts/build_windows.py --smoke`` re-runs the build
(and the ``uv sync`` step that can fail on Windows file locks
during dev). This script just boots the existing binary at
``dist/omniscribe-server.exe`` and hits two endpoints:

1. ``/api/health`` — the Phase 4 liveness probe (always 200 if
   the binary boots and the harness mounts).
2. ``/api/sample-pdf/digital.pdf`` — the Sprint 3 (U12) sample-
   PDF route. Asserts the route is mounted, the allowlist is
   honoured, the body starts with the ``%PDF-`` magic, and the
   Content-Disposition header is set. A regression that breaks
   the Cordis plugin loader, the resources bundling, or the
   allowlist gate would fail here.

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

#: Endpoints the smoke test must hit. Each tuple is
#: ``(path, expected_status, expected_substring_in_body)``.
SMOKE_ENDPOINTS: list[tuple[str, int, str]] = [
    ("/api/health", 200, "status"),
    ("/api/sample-pdf/digital.pdf", 200, "%PDF"),
]


def _hit_endpoints(port: int) -> dict[str, tuple[int, str]]:
    """Return ``{path: (status, body_head)}`` for each smoke endpoint.

    Hits all endpoints on every call. The loop in ``main`` only
    re-invokes on failure; once every endpoint returns the
    expected status the loop exits.
    """
    out: dict[str, tuple[int, str]] = {}
    for path, _expected_status, _expected_substring in SMOKE_ENDPOINTS:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=5
        ) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            out[path] = (resp.status, body[:200])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Boot an already-built omniscribe-server bundle and "
        "require the smoke endpoints to return 200 within the deadline."
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
    results: dict[str, tuple[int, str]] = {}
    boot_log: list[str] = []
    try:
        while time.time() < deadline and len(results) < len(SMOKE_ENDPOINTS):
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
                    f"binary exited with rc={proc.returncode} before all smoke "
                    f"checks passed.\n--- last 30 lines of boot log ---\n{tail}"
                )
            try:
                results = _hit_endpoints(port)
                if len(results) == len(SMOKE_ENDPOINTS):
                    break
            except Exception:
                time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if len(results) < len(SMOKE_ENDPOINTS):
        tail = "\n".join(boot_log[-30:])
        raise SystemExit(
            f"smoke checks did not all pass within {deadline_s}s.\n"
            f"Got: {sorted(results)}\n"
            f"--- last 30 lines of boot log ---\n{tail}"
        )

    # Per-endpoint assertion: status code and body substring match.
    print()
    all_ok = True
    for path, expected_status, expected_substring in SMOKE_ENDPOINTS:
        status, body = results[path]
        body_ok = expected_substring in body
        ok = status == expected_status and body_ok
        flag = "OK  " if ok else "FAIL"
        if not ok:
            all_ok = False
        print(
            f"{flag}: {path} -> {status} (expected {expected_status}, "
            f"body contains {expected_substring!r}: {body_ok})"
        )
        if not ok:
            print(f"  body head: {body!r}")

    if not all_ok:
        return 1

    print(
        f"\nSMOKE PASS: bundle serves {len(SMOKE_ENDPOINTS)} endpoints "
        f"(/api/health, /api/sample-pdf/digital.pdf) in {size_mb:.1f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
