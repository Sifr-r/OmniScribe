"""Live HTTP probe of the OmniScribe API surface.

Run as a script to hit a running OmniScribe server; import as a module
for the smoke test (no network traffic fires on import — every probe
sits inside ``main`` and only runs when the script is invoked
directly).
"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from websockets.sync.client import connect

BASE_URL = os.getenv("OMNISCRIBE_PROBE_BASE", "http://127.0.0.1:8000").rstrip("/")
WS_BASE_URL = BASE_URL.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def probe(url, *, method="GET", body=None, headers=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urlopen(req, timeout=10) as resp:
            status = resp.status
            payload = resp.read().decode("utf-8", errors="replace")
            return status, payload
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def _expected_probes():
    """Return the canned list of endpoint probes.

    Kept as a function (rather than module-level) so that the script
    body doesn't execute during ``import`` — the smoke test
    (``tests/scripts/test_scripts_smoke.py``) imports every script to catch
    renamed ``omniscribe`` symbols and would otherwise hit a live
    network request on import.
    """
    return [
        ("GET /", f"{BASE_URL}/", 200),
        ("GET /api/providers", f"{BASE_URL}/api/providers", 200),
        ("GET /api/jobs", f"{BASE_URL}/api/jobs", 200),
        ("GET /api/config", f"{BASE_URL}/api/config", 200),
        ("GET /api/config/ocr", f"{BASE_URL}/api/config/ocr", 200),
        ("GET /api/config/transcription", f"{BASE_URL}/api/config/transcription", 200),
        ("GET /api/models/transcription", f"{BASE_URL}/api/models/transcription", 200),
        (
            "GET /api/sample-pdf/digital.pdf",
            f"{BASE_URL}/api/sample-pdf/digital.pdf",
            200,
        ),
        ("POST /api/progress/session", f"{BASE_URL}/api/progress/session", 200),
        ("POST /api/process", f"{BASE_URL}/api/process", 422),
        ("POST /api/process/async", f"{BASE_URL}/api/process/async", 422),
        (
            "GET /api/process/status/missing",
            f"{BASE_URL}/api/process/status/missing",
            404,
        ),
        ("POST /api/jobs/missing/cancel", f"{BASE_URL}/api/jobs/missing/cancel", 404),
        ("GET /api/text/nonexistent", f"{BASE_URL}/api/text/nonexistent", 403),
    ]


def main() -> int:
    """Probe the OmniScribe API surface; return ``0`` on full success."""
    print(f"{'Endpoint':<40} {'Status':<10} Notes")
    print("-" * 80)
    failures = 0
    for name, url, expected in _expected_probes():
        if name.startswith("POST "):
            status, body = probe(url, method="POST", body={"client_id": "probe"})
        else:
            status, body = probe(url)
        notes = ""
        if status != expected:
            failures += 1
            notes = f"expected={expected}"
        elif name == "POST /api/progress/session":
            try:
                d = json.loads(body)
                notes = f"channel_id len={len(d.get('channel_id', ''))}"
            except Exception:
                notes = "(non-JSON)"
        elif name == "GET /api/providers" and status == 200:
            try:
                d = json.loads(body)
                notes = f"providers={len(d.get('providers', []))}"
            except Exception:
                notes = "(non-JSON)"
        print(f"{name:<40} {status:<10} {notes}")

    status, body = probe(
        f"{BASE_URL}/api/progress/session",
        method="POST",
        body={"client_id": "websocket-probe"},
    )
    if status != 200:
        print(f"{'WS /ws/{{channel_id}}':<40} {'FAILED':<10} session status={status}")
        return 1

    session = json.loads(body)
    websocket_url = f"{WS_BASE_URL}/ws/{session['channel_id']}"
    with connect(websocket_url, open_timeout=10) as websocket:
        # The token travels in the first frame, not the URL.
        websocket.send(
            json.dumps({"type": "auth", "session_token": session["session_token"]})
        )
        websocket.send(json.dumps({"type": "cancel"}))
    print(f"{'WS /ws/{{channel_id}}':<40} {'OK':<10} handshake + cancel frame")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    sys.exit(main())
