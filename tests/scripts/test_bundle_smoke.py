"""Verify the bundle gate cannot pass another server or wait on silent stdout."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import smoke_existing as smoke


def test_occupied_port_does_not_launch_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    launch = Mock()
    monkeypatch.setattr(smoke.subprocess, "Popen", launch)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        with pytest.raises(OSError):
            smoke.smoke(Path(__file__), occupied.getsockname()[1], 1)
    launch.assert_not_called()


@pytest.mark.parametrize(
    "body, expected", [(' {"status":"ok"}', 0), ('{"status":"wrong"}', 1)]
)
def test_silent_bundle_is_polled_and_only_owned_tree_stopped(
    monkeypatch: pytest.MonkeyPatch, body: str, expected: int
) -> None:
    proc = Mock(pid=12345, returncode=0)
    proc.poll.return_value = None
    proc.stdout.readline.side_effect = AssertionError("must not block on stdout")
    launch = Mock(return_value=proc)
    stop = Mock()
    monkeypatch.setattr(smoke.subprocess, "Popen", launch)
    monkeypatch.setattr(smoke.subprocess, "run", stop)
    monkeypatch.setattr(smoke.sys, "platform", "win32")
    monkeypatch.setattr(
        smoke,
        "_hit_endpoints",
        lambda port, deadline: {
            "/api/health": (200, body),
            "/api/sample-pdf/digital.pdf": (200, "%PDF-1.4"),
        },
    )
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    assert smoke.smoke(Path(__file__), port, 1) == expected
    proc.stdout.readline.assert_not_called()
    proc.wait.assert_called_once_with(timeout=5)
    assert stop.call_args.args[0] == ["taskkill", "/PID", "12345", "/T", "/F"]
    options = launch.call_args.kwargs
    assert Path(options["cwd"]).is_relative_to(smoke.ROOT / "build")
    assert options["env"]["OMNISCRIBE_ARTIFACT_DIR"] == options["cwd"]
    assert options["env"]["OMNISCRIBE_AUTH_TOKEN"] == ""


def test_exited_bundle_does_not_kill_a_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = Mock(returncode=1)
    proc.poll.return_value = 1
    stop = Mock()
    monkeypatch.setattr(smoke.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(smoke.subprocess, "run", stop)
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    with pytest.raises(SystemExit, match="Binary exit code: 1"):
        smoke.smoke(Path(__file__), port, 1)
    stop.assert_not_called()
    proc.terminate.assert_not_called()


def test_expired_probe_budget_never_opens_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = Mock()
    monkeypatch.setattr(smoke.urllib.request, "urlopen", request)
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        smoke._hit_endpoints(18766, time.monotonic() - 1)
    request.assert_not_called()


def test_silent_bundle_still_obeys_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = Mock(returncode=0)
    proc.poll.return_value = None
    monkeypatch.setattr(smoke.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(smoke.sys, "platform", "linux")
    monkeypatch.setattr(smoke, "_hit_endpoints", Mock(side_effect=TimeoutError))
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    started = time.monotonic()
    with pytest.raises(SystemExit, match="within 1s"):
        smoke.smoke(Path(__file__), port, 1)
    assert time.monotonic() - started < 2
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=5)
