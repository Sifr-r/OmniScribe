"""Shared fixtures for the router contract tests.

Every test in this package exercises the real ten-plugin tree booted by
the ``api_client`` fixture in ``tests/conftest.py``; the OCR routes
additionally need the pipeline bridge faked so no VLM or Surya predictor
is ever touched.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

PDF_BYTES = b"%PDF-1.4 fake"

# The package __init__ re-exports the ``plugin`` instance, which shadows the
# submodule attribute — import the module itself for monkeypatching.
ocr_plugin_mod = importlib.import_module("omniscribe.plugins.ocr.plugin")
# Sprint 6 split (audit catalog): the pipeline bridge call sites moved
# to ``service.py``. The fake has to be patched on the new module too,
# otherwise the test would hit the real VLM/Surya code path.
ocr_service_mod = importlib.import_module("omniscribe.plugins.ocr.service")


@pytest.fixture()
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replaces the bridge so no VLM / Surya is touched."""
    state: dict[str, Any] = {"fail": False}

    def fake_build(settings: Any, request: Any, *, block_callbacks: Any = None) -> Any:
        return object()

    async def fake_run(
        pipeline: Any,
        *,
        settings: Any,
        request: Any,
        input_path: str,
        output_path: str,
        on_progress: Any = None,
        on_warning: Any = None,
        cancel_check: Any = None,
    ) -> dict[int, list[str]]:
        if on_progress is not None:
            await on_progress(50, "ocr", "Processing page 1")
        if state["fail"]:
            raise RuntimeError("vlm exploded")
        Path(output_path).write_bytes(PDF_BYTES)
        return {0: ["hello world"]}

    monkeypatch.setattr(ocr_service_mod, "build_pipeline", fake_build)
    monkeypatch.setattr(ocr_service_mod, "run_pipeline", fake_run)
    return state


def upload() -> dict[str, Any]:
    """The multipart payload every OCR route test shares."""
    return {"files": {"file": ("a.pdf", b"%PDF-1.4 input", "application/pdf")}}


def wait_status(
    client: TestClient, job_id: str, status: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    """Poll the status route until the job reaches ``status``.

    The queue worker runs on the TestClient portal loop, so sleeping the
    test thread is what lets it make progress between polls.
    """
    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        body = client.get(f"/api/process/status/{job_id}").json()
        if body.get("status") == status:
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {status!r}; last={body}")


def artifact_token_from_events(
    client: TestClient,
    job_id: str,
    *,
    token: str | None = None,
    timeout: float = 5.0,
) -> str:
    """Read the ``job_completed`` SSE event and return its result/artifact token."""
    import asyncio
    import json

    if token is None:
        ctx = getattr(getattr(client, "app", None), "state", None)
        ctx = getattr(ctx, "context", None)
        if ctx:
            from omniscribe.plugins.jobs import JobQueue

            queue = ctx.inject(JobQueue)
            record = asyncio.run(queue.status(job_id))
            if record:
                token = record.request_meta.get("result_access_token")

    deadline = time.time() + timeout
    params = {"token": token} if token else None
    with client.stream(
        "GET", f"/api/process/{job_id}/events", params=params
    ) as response:
        assert response.status_code == 200
        # Parse the SSE stream in a single iter_lines() pass — httpx
        # raises ``StreamConsumed`` if you try to iterate twice.
        current_event: str | None = None
        for raw in response.iter_lines():
            if time.time() > deadline:
                raise AssertionError(
                    f"job {job_id} never emitted job_completed within {timeout}s"
                )
            if raw is None or raw == "" or raw.startswith(":"):
                # blank line / keep-alive comment ends the current event
                current_event = None
                continue
            if raw.startswith("event:"):
                current_event = raw.removeprefix("event:").strip()
            elif raw.startswith("data:") and current_event == "job_completed":
                body = json.loads(raw.removeprefix("data:").strip())
                tok = body.get("artifact_token") or token
                if tok:
                    return str(tok)
                raise AssertionError(f"job_completed for {job_id} had no token")
    raise AssertionError(f"job {job_id} never emitted job_completed")
