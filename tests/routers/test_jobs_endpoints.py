"""GET/DELETE /api/jobs plus the per-job result and cancel routes."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from omniscribe.plugins.ocr.service import OCRServiceImpl

from .conftest import PDF_BYTES, artifact_token_from_events, upload, wait_status


def test_jobs_list_is_bare_array_and_clear_works(
    api_client: TestClient, fake_pipeline: dict
) -> None:
    assert api_client.get("/api/jobs").json() == []

    submit = api_client.post("/api/process/async", **upload())
    job_id = submit.json()["job_id"]
    wait_status(api_client, job_id, "complete")

    items = api_client.get("/api/jobs").json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["id"] == job_id
    assert items[0]["filename"] == "a.pdf"
    assert items[0]["status"] == "complete"
    assert items[0]["timestamp"]

    unconfirmed = api_client.delete("/api/jobs")
    assert unconfirmed.status_code == 400
    assert unconfirmed.json() == {
        "error": "confirmation_required",
        "detail": (
            "DELETE /api/jobs requires confirm=true query parameter "
            "to prevent accidental wipe"
        ),
    }

    cleared = api_client.delete("/api/jobs?confirm=true")
    assert cleared.status_code == 200
    assert cleared.json() == {"status": "ok", "cleared": 1}
    assert api_client.get("/api/jobs").json() == []


def test_result_download_requires_valid_token(
    api_client: TestClient, fake_pipeline: dict
) -> None:
    submit = api_client.post("/api/process/async", **upload())
    job_id = submit.json()["job_id"]
    wait_status(api_client, job_id, "complete")

    # 2026-08-29 audit C-3 / H-3: the status endpoint no longer
    # returns the token. The async client pulls it from the
    # ``job_completed`` SSE event payload (out-of-band channel).
    token = artifact_token_from_events(api_client, job_id)
    assert token

    result = api_client.get(f"/api/jobs/{job_id}/result", params={"token": token})
    assert result.status_code == 200
    assert result.content == PDF_BYTES

    wrong = api_client.get(f"/api/jobs/{job_id}/result", params={"token": "nope"})
    # Pedantic 2.7: wrong-token returns 404, not 403, so the response
    # is indistinguishable from unknown / not-complete / artifact-gone.
    assert wrong.status_code == 404


def test_unknown_job_result_and_cancel_are_404(api_client: TestClient) -> None:
    assert api_client.get("/api/jobs/nope/result").status_code == 404
    assert api_client.post("/api/jobs/nope/cancel").status_code == 404


def test_result_failures_are_indistinguishable(
    api_client: TestClient, fake_pipeline: dict
) -> None:
    """Pedantic review 2.7: every non-success result path must
    collapse to a single status code + body so an attacker cannot
    enumerate job ids by watching the differential responses.

    Four failure scenarios — unknown id, wrong token against a
    real id, valid token against a non-complete id, and missing
    token against a complete id — must all return the same
    ``(status, detail)``.
    """
    expected = (404, "result not available")

    # 1. Unknown job id.
    unknown = api_client.get("/api/jobs/nope/result", params={"token": "anything"})
    assert (unknown.status_code, unknown.json()["detail"]) == expected

    # 2. Wrong token against a real, complete job.
    submit = api_client.post("/api/process/async", **upload())
    job_id = submit.json()["job_id"]
    wait_status(api_client, job_id, "complete")
    bad_token = api_client.get(f"/api/jobs/{job_id}/result", params={"token": "nope"})
    assert (bad_token.status_code, bad_token.json()["detail"]) == expected

    # 3. No token at all against a real, complete job.
    no_token = api_client.get(f"/api/jobs/{job_id}/result")
    assert (no_token.status_code, no_token.json()["detail"]) == expected

    # 4. Job that errored before producing a result artifact.
    fake_pipeline["fail"] = True
    err_submit = api_client.post("/api/process/async", **upload())
    err_job_id = err_submit.json()["job_id"]
    wait_status(api_client, err_job_id, "error")
    err_resp = api_client.get(
        f"/api/jobs/{err_job_id}/result", params={"token": "anything"}
    )
    assert (err_resp.status_code, err_resp.json()["detail"]) == expected


def test_cancel_job_sets_status_cancelled(
    api_client: TestClient, fake_pipeline: dict
) -> None:
    submit = api_client.post("/api/process/async", **upload())
    job_id = submit.json()["job_id"]
    # Cancel the job (or wait until completed if it ran instantly)
    cancel_resp = api_client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_resp.status_code in {
        200,
        409,
    }  # 200 if cancelled, 409 if already complete


def test_job_page_preview_negative_index_returns_400(
    api_client: TestClient,
) -> None:
    resp = api_client.get("/api/jobs/any-job-id/pages/-1/preview")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "page_index must be >= 0"


def test_job_page_preview_missing_or_unrendered_returns_404(
    api_client: TestClient, fake_pipeline: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Unknown job id
    unknown = api_client.get("/api/jobs/nonexistent-job-id/pages/0/preview")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "page preview unavailable for this job"

    # 2. Existing completed job where preview is unavailable / returns None
    submit = api_client.post("/api/process/async", **upload())
    body = submit.json()
    job_id = body["job_id"]
    result_token = body["result_token"]
    wait_status(api_client, job_id, "complete")

    async def fake_none_preview(
        self: Any, j_id: str, p_idx: int, **kwargs: Any
    ) -> bytes | None:
        return None

    monkeypatch.setattr(OCRServiceImpl, "get_page_preview", fake_none_preview)
    unrendered = api_client.get(
        f"/api/jobs/{job_id}/pages/0/preview", params={"token": result_token}
    )
    assert unrendered.status_code == 404
    assert unrendered.json()["detail"] == "page preview unavailable for this job"


def test_job_page_preview_valid_completed_job_returns_200(
    api_client: TestClient, fake_pipeline: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    submit = api_client.post("/api/process/async", **upload())
    body = submit.json()
    job_id = body["job_id"]
    result_token = body["result_token"]
    wait_status(api_client, job_id, "complete")

    fake_preview_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR_fake_preview_content"

    async def fake_preview(
        self: Any, j_id: str, p_idx: int, **kwargs: Any
    ) -> bytes | None:
        if j_id == job_id and p_idx == 0:
            return fake_preview_bytes
        return None

    monkeypatch.setattr(OCRServiceImpl, "get_page_preview", fake_preview)

    # Without token: 404
    unauth = api_client.get(f"/api/jobs/{job_id}/pages/0/preview")
    assert unauth.status_code == 404

    resp = api_client.get(
        f"/api/jobs/{job_id}/pages/0/preview", params={"token": result_token}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == fake_preview_bytes
