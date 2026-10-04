"""Offline script boundary and smoke checks; these do not prove a live deployment."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as redis_async

from omniscribe.plugins.jobs_redis import KEY_LEASE_PREFIX
from omniscribe.plugins.state_backend_types import (
    ArtifactBlob,
    ArtifactRecord,
    JobRecord,
    StateBackend,
)
from scripts import dev_redis_smoke as smoke

JOB = "a" * 32
TOKEN = "result-access-token"


@pytest.mark.parametrize("model", [None, "", 42])
def test_confidence_script_rejects_invalid_model_values(model: object) -> None:
    from scripts import confidence_eval

    with pytest.raises(confidence_eval.HarnessError, match="non-empty string"):
        confidence_eval.model_for(argparse.Namespace(grounded_model=model), "grounded")


async def test_key_dump_decodes_binary_redis_responses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    r = AsyncMock(spec=redis_async.Redis)

    async def keys(**kwargs: object) -> AsyncIterator[bytes | str]:
        yield b"omniscribe:one"
        yield "omniscribe:two"

    r.scan_iter = keys
    r.ttl = AsyncMock(return_value=60)
    r.type = AsyncMock(return_value=b"string")
    monkeypatch.setattr(redis_async, "from_url", lambda *args, **kwargs: r)
    assert await smoke._dump_keys("redis://unused") == 2
    assert "omniscribe:one  (type=string" in capsys.readouterr().out
    r.aclose.assert_awaited_once()


def test_http_auth_and_artifact_tokens_are_not_forwarded_on_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNISCRIBE_AUTH_TOKEN", "backend-secret")
    received: list[tuple[str, str | None, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    self.headers.get("X-Artifact-Token"),
                )
            )
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/leak")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            assert smoke._request_json(base, "/health") == {"status": "ok"}
            with pytest.raises(smoke.SmokeError, match="redirect rejected"):
                smoke._request_json(base, "/redirect", token=TOKEN)
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert received == [
        ("/health", "Bearer backend-secret", None),
        ("/redirect", "Bearer backend-secret", TOKEN),
    ]


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "base": "http://127.0.0.1:8000",
        "redis_url": "redis://localhost:6379/0",
        "port": 6379,
        "jobs": 2,
        "timeout": 2.0,
        "text": "Hello",
        "target_language": "Spanish",
        "verify_recovery": False,
    }
    return argparse.Namespace(**(values | overrides))


def _harness(
    monkeypatch: pytest.MonkeyPatch, owners: list[str | None], *, state: str = "SUCCESS"
) -> tuple[AsyncMock, AsyncMock]:
    result: dict[str, object] = {"translated_text": "Hola"}
    backend = AsyncMock(spec=StateBackend)
    backend.get_job.return_value = JobRecord(
        job_id=JOB,
        status="complete",
        request_meta={"result_access_token": TOKEN},
        result_artifact_id="artifact",
        result_artifact_token="artifact-token",
    )
    backend.get_artifact.return_value = ArtifactBlob(
        record=ArtifactRecord(
            "artifact", "artifact-token", JOB, "application/json", 1, 60
        ),
        blob=json.dumps(result).encode(),
    )
    r = AsyncMock(spec=redis_async.Redis)
    owner_iter = iter(owners)

    async def get(key: str) -> str | None:
        assert key == f"{KEY_LEASE_PREFIX}{JOB}"
        return next(owner_iter)

    r.get = AsyncMock(side_effect=get)
    r.exists = AsyncMock(return_value=1)
    states = iter(["PROGRESS"] * (len(owners) - 1) + [state])

    def request(
        base: str, path: str, payload: object = None, token: str | None = None
    ) -> dict[str, object]:
        if "/result/" in path:
            assert token == TOKEN
            return result
        return {"job_id": JOB, "state": next(states), "status": "Failed"}

    monkeypatch.setattr(smoke, "_request_json", request)
    return backend, r


async def test_success_checks_token_bound_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, r = _harness(monkeypatch, ["worker-a:0:claim"])
    assert await smoke._verify_job("http://api", JOB, TOKEN, backend, r)
    backend.get_artifact.assert_awaited_once_with("artifact", "artifact-token")
    r.delete.assert_not_called()


async def test_recovery_requires_same_job_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, r = _harness(monkeypatch, ["worker-a:0:claim", "worker-b:0:claim"])
    assert await smoke._verify_job(
        "http://api", JOB, TOKEN, backend, r, recovery=True, poll_interval=0
    )
    assert r.get.await_count == 2


@pytest.mark.parametrize(
    "owners",
    [
        [None],
        ["worker-a:0:claim"],
        ["worker-a:0:claim1", "worker-a:1:claim2"],
    ],
)
async def test_no_handoff_cannot_claim_recovery(
    monkeypatch: pytest.MonkeyPatch, owners: list[str | None]
) -> None:
    backend, r = _harness(monkeypatch, owners)
    with pytest.raises(smoke.SmokeError, match="recovery unverified"):
        await smoke._verify_job(
            "http://api", JOB, TOKEN, backend, r, recovery=True, poll_interval=0
        )


async def test_other_job_lease_cannot_claim_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, r = _harness(monkeypatch, [None])
    r.get.side_effect = lambda key: (
        "worker-b:0:claim" if key.endswith("other-job") else None
    )
    with pytest.raises(smoke.SmokeError, match="recovery unverified"):
        await smoke._verify_job("http://api", JOB, TOKEN, backend, r, recovery=True)


@pytest.mark.parametrize("failure", ["terminal", "backend", "artifact", "empty"])
async def test_verification_failures_are_explicit(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    backend, r = _harness(
        monkeypatch,
        ["worker-a:0:claim"],
        state="FAILURE" if failure == "terminal" else "SUCCESS",
    )
    if failure == "backend":
        backend.get_job.return_value = None
    elif failure == "artifact":
        backend.get_artifact.return_value = None
    elif failure == "empty":
        monkeypatch.setattr(
            smoke,
            "_request_json",
            lambda base, path, *args: {
                "job_id": JOB,
                "state": "SUCCESS",
                "translated_text": " ",
            },
        )
    with pytest.raises(smoke.SmokeError):
        await smoke._verify_job("http://api", JOB, TOKEN, backend, r)


async def test_concurrent_submission_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2)

    def submit(*args: object) -> dict[str, str]:
        index = barrier.wait(timeout=2)
        return {"job_id": str(index) * 32, "result_token": TOKEN}

    backend, r = AsyncMock(spec=StateBackend), AsyncMock(spec=redis_async.Redis)
    monkeypatch.setattr(smoke, "RedisStateBackend", lambda url: backend)
    monkeypatch.setattr(smoke.redis_async, "from_url", lambda *args, **kwargs: r)
    monkeypatch.setattr(smoke, "_request_json", submit)
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(smoke, "_verify_job", verify)
    await smoke._run_jobs("http://api", _args())
    assert verify.await_count == 2
    r.aclose.assert_awaited_once()
    backend.aclose.assert_awaited_once()


async def test_timeout_cancels_observers_and_closes_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, r = AsyncMock(spec=StateBackend), AsyncMock(spec=redis_async.Redis)
    monkeypatch.setattr(smoke, "RedisStateBackend", lambda url: backend)
    monkeypatch.setattr(smoke.redis_async, "from_url", lambda *args, **kwargs: r)
    monkeypatch.setattr(
        smoke, "_request_json", lambda *args: {"job_id": JOB, "result_token": TOKEN}
    )
    cancelled = asyncio.Event()

    async def verify(*args: object, **kwargs: object) -> bool:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return False

    monkeypatch.setattr(smoke, "_verify_job", verify)
    with pytest.raises(smoke.SmokeError, match="timed out"):
        await smoke._run_jobs(
            "http://api", _args(jobs=1, timeout=0.05, verify_recovery=True)
        )
    assert cancelled.is_set()
    r.aclose.assert_awaited_once()
    backend.aclose.assert_awaited_once()


async def test_default_remains_probe_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_hit_health", MagicMock())
    monkeypatch.setattr(smoke, "_dump_keys", AsyncMock(return_value=0))
    run_jobs = AsyncMock()
    monkeypatch.setattr(smoke, "_run_jobs", run_jobs)
    assert await smoke.main_async(_args(jobs=0)) == 0
    run_jobs.assert_not_awaited()


@pytest.mark.parametrize(
    "overrides",
    [
        {"jobs": -1},
        {"jobs": 101},
        {"timeout": float("nan")},
        {"timeout": 0},
        {"jobs": 0, "verify_recovery": True},
        {"base": "http://user:secret@host"},
        {"text": " "},
    ],
)
def test_boundary_validation(overrides: dict[str, object]) -> None:
    with pytest.raises(smoke.SmokeError):
        smoke._validate(_args(**overrides))
