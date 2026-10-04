"""Standalone smoke test for an already-built bundle.

The full ``scripts/build_windows.py --smoke`` re-runs the build
(and the ``uv sync`` step that can fail on Windows file locks
during dev). This script just boots the existing binary at
``dist/omniscribe-server.exe`` and hits two endpoints:

1. ``/api/health`` — the Phase 4 liveness probe (always 200 if
   the binary boots and the harness mounts).
2. ``/api/sample-pdf/digital.pdf`` — the Sprint 3 (U12) sample-
   PDF route. Asserts the route is mounted and the body starts
   with the ``%PDF-`` magic. A regression that breaks
   the Cordis plugin loader, the resources bundling, or the
   allowlist gate would fail here.

Usage:
    uv run python scripts/smoke_existing.py [--port 18766] [--deadline-s 90]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
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


def _hit_endpoints(
    port: int, deadline: float | None = None
) -> dict[str, tuple[int, str]]:
    """Return ``{path: (status, body_head)}`` for each smoke endpoint.

    Hits all endpoints on every call. The loop in ``main`` only
    re-invokes on failure; once every endpoint returns the
    expected status the loop exits.
    """
    out: dict[str, tuple[int, str]] = {}
    for path, _expected_status, _expected_substring in SMOKE_ENDPOINTS:
        timeout = 5.0 if deadline is None else min(5.0, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("bundle smoke: endpoint deadline exceeded")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=timeout
        ) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            out[path] = (resp.status, body[:200])
    return out


def _valid_body(path: str, body: str, expected: str) -> bool:
    if path == "/api/health":
        try:
            return bool(json.loads(body) == {"status": "ok"})
        except json.JSONDecodeError:
            return False
    if path == "/api/sample-pdf/digital.pdf":
        return body.startswith("%PDF-")
    return expected in body


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
    if not 1 <= args.port <= 65535 or args.deadline_s <= 0:
        parser.error("port must be 1-65535 and deadline-s must be positive")
    return smoke(BINARY, args.port, args.deadline_s)


def smoke(binary: Path, port: int = 18766, deadline_s: int = 90) -> int:
    """Probe only our bundle, with bounded polling and retained workspace evidence."""
    if not 1 <= port <= 65535 or deadline_s <= 0:
        raise ValueError("bundle smoke: invalid port or deadline")
    if not binary.is_file():
        print(f"FAIL: binary not found at {binary}")
        return 2
    # Refuse an occupied port so an unrelated healthy server cannot pass the gate.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    size_mb = binary.stat().st_size / 1024 / 1024
    print(f"binary: {binary}")
    print(f"size:   {size_mb:.1f} MB")
    print(f"launching: {binary.name} --port {port}")
    scratch = ROOT / "build"
    scratch.mkdir(exist_ok=True)
    results: dict[str, tuple[int, str]] = {}
    boot_log: list[str] = []
    staging = tempfile.mkdtemp(prefix="bundle-smoke-", dir=scratch)
    print(f"smoke evidence: {staging}")
    env = os.environ.copy()
    env.update(
        OMNISCRIBE_ARTIFACT_DIR=staging,
        OMNISCRIBE_STATE_DB_PATH=str(Path(staging) / "state.db"),
        OMNISCRIBE_SPOOL_DIR=staging,
        OMNISCRIBE_STATE_BACKEND="sqlite",
        OMNISCRIBE_JOBS_MODE="inprocess",
        OMNISCRIBE_CORDIS_PATCH=str(Path(staging) / "cordis.patch.yml"),
        OMNISCRIBE_AUTH_TOKEN="",
        TEMP=staging,
        TMP=staging,
    )
    log_path = Path(staging) / "boot.log"
    # A file never blocks polling while the bundle extracts or stops logging.
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [str(binary), "--host", "127.0.0.1", "--port", str(port)],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=staging,
        )
        deadline = time.monotonic() + deadline_s
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    results = _hit_endpoints(port, deadline)
                    if len(results) == len(SMOKE_ENDPOINTS):
                        break
                except (OSError, TimeoutError):
                    time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        finally:
            if proc.poll() is None:
                if sys.platform == "win32":
                    # PyInstaller onefile has a child; stop only our PID tree.
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
    boot_log = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(results) < len(SMOKE_ENDPOINTS):
        tail = "\n".join(boot_log[-30:])
        raise SystemExit(
            f"smoke checks did not all pass within {deadline_s}s.\n"
            f"Binary exit code: {proc.returncode}\n"
            f"Got: {sorted(results)}\n"
            f"--- last 30 lines of boot log ---\n{tail}"
        )

    # Per-endpoint assertion: status code and body substring match.
    print()
    all_ok = True
    for path, expected_status, expected_substring in SMOKE_ENDPOINTS:
        status, body = results[path]
        body_ok = _valid_body(path, body, expected_substring)
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
