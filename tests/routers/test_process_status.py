"""GET /api/process/status/{job_id} — the frontend polling contract."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import upload, wait_status

# 2026-08-29 audit C-3 / H-3: the result ``token`` and the pre-built
# ``/api/jobs/{id}/result?token=...`` URL are NOT in this response. The
# async client obtains the token out-of-band via the ``job_completed``
# SSE event (see ``artifact_token_from_events`` in conftest.py).
_STATUS_KEYS = {
    "job_id",
    "filename",
    "status",
    "created_at",
    "started_at",
    "completed_at",
    "duration_s",
    "error",
    "text_artifact_id",
    "document_artifact_id",
    "failed_pages",
}


def test_unknown_job_status_is_404(api_client: TestClient) -> None:
    response = api_client.get("/api/process/status/does-not-exist")
    assert response.status_code == 404


def test_status_response_matches_frontend_contract(
    api_client: TestClient, fake_pipeline: dict
) -> None:
    submit = api_client.post("/api/process/async", **upload())
    job_id = submit.json()["job_id"]
    done = wait_status(api_client, job_id, "complete")
    assert set(done) == _STATUS_KEYS
    assert done["job_id"] == job_id
    assert done["status"] in {"pending", "processing", "complete", "error", "cancelled"}
    assert done["completed_at"] is not None
    assert done["duration_s"] is not None
    assert done["error"] is None
    assert done["text_artifact_id"]


def test_status_response_never_returns_artifact_token(
    api_client: TestClient, fake_pipeline: dict
) -> None:
    """Regression test for 2026-08-29 audit C-3 / H-3.

    The unauthenticated ``GET /api/process/status/{job_id}`` + ``GET
    /api/jobs`` chain must NOT leak ``text_artifact_token`` or the
    pre-built ``text_artifact_url``. Otherwise any caller can bypass
    the constant-time gate at ``fetch_result`` and download another
    user's OCR'd PDF. The async client receives the token out-of-band
    via the ``job_completed`` SSE event.
    """
    submit = api_client.post("/api/process/async", **upload())
    job_id = submit.json()["job_id"]
    done = wait_status(api_client, job_id, "complete")
    assert "text_artifact_token" not in done
    assert "text_artifact_url" not in done
    # The result download still requires the token, but it can only be
    # obtained from the out-of-band channel.
    blocked = api_client.get(f"/api/jobs/{job_id}/result")
    # Pedantic 2.7: a missing token against a complete job now returns
    # 404 (not 403) so the response is indistinguishable from a
    # non-existent / not-complete / token-mismatched result. Only a
    # correct token sees 200.
    assert blocked.status_code == 404
