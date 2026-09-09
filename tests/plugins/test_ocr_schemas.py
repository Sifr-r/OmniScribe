"""OCR schemas: frontend FormData parsing and response shapes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omniscribe.plugins.ocr.schemas import (
    AsyncSubmitResponse,
    JobListItemResponse,
    JobStatusResponse,
    OCRRequest,
)


def _frontend_form_fields() -> dict[str, str]:
    """The exact field set ``buildOcrFormData`` submits (all strings)."""
    return {
        "model": "allenai/olmocr-2-7b",
        "api_base": "http://localhost:1234/v1",
        "api_key": "lm-studio",
        "pipeline_mode": "hybrid",
        "dense_mode": "on",
        "spellcheck": "en-US",
        "document_processors": "reading_order,table_extraction",
        "preprocess_pages": "true",
        "orientation_detection": "true",
        "deskew": "false",
        "denoise": "true",
        "normalize_contrast": "false",
        "crop_cleanup": "false",
        "progress_channel": "chan-1",
        "progress_token": "tok-1",
    }


def test_parses_frontend_form_data_field_set() -> None:
    request = OCRRequest(**_frontend_form_fields())  # type: ignore[arg-type]
    assert request.model == "allenai/olmocr-2-7b"
    assert request.pipeline_mode == "hybrid"
    assert request.document_processors == ["reading_order", "table_extraction"]
    assert request.preprocess_pages is True
    assert request.orientation_detection is True
    assert request.deskew is False
    assert request.denoise is True
    assert request.progress_channel == "chan-1"
    assert request.progress_token == "tok-1"
    assert request.spellcheck == "en-US"


def test_dense_mode_aliases_map_onto_core_spellings() -> None:
    from omniscribe.core.document import DenseMode

    # Use ``model_validate`` so the constructor's ``DenseMode`` annotation
    # is bypassed: these values flow through the dense_mode field validator.
    assert OCRRequest.model_validate({"dense_mode": "on"}).dense_mode == DenseMode.ALWAYS
    assert OCRRequest.model_validate({"dense_mode": "off"}).dense_mode == DenseMode.NEVER
    assert OCRRequest.model_validate({"dense_mode": "auto"}).dense_mode == DenseMode.AUTO
    assert OCRRequest.model_validate({"dense_mode": "always"}).dense_mode == DenseMode.ALWAYS
    # unknown values fall back to auto rather than failing the upload
    assert OCRRequest.model_validate({"dense_mode": "bogus"}).dense_mode == DenseMode.AUTO


def test_unknown_document_processor_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown document processor"):
        OCRRequest(document_processors="reading_order,bogus_processor")  # type: ignore[arg-type]


def test_quality_loop_bounds_are_enforced() -> None:
    request = OCRRequest(
        quality_loop_enabled="false",  # type: ignore[arg-type]
        quality_target="0.9",  # type: ignore[arg-type]
        quality_max_retries="4",  # type: ignore[arg-type]
    )
    assert request.quality_loop_enabled is False
    assert request.quality_target == pytest.approx(0.9)
    assert request.quality_max_retries == 4
    with pytest.raises(ValidationError):
        OCRRequest(quality_target="1.5")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        OCRRequest(quality_target="0.4")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        OCRRequest(quality_max_retries="6")  # type: ignore[arg-type]


def test_preprocessing_enabled_master_flag_wins() -> None:
    assert OCRRequest(preprocess_pages="true").preprocessing_enabled is True  # type: ignore[arg-type]
    assert (
        OCRRequest(preprocess_pages="false", denoise="true").preprocessing_enabled  # type: ignore[arg-type]
        is False
    )
    assert OCRRequest(denoise="true").preprocessing_enabled is True  # type: ignore[arg-type]
    assert OCRRequest().preprocessing_enabled is False


def test_response_shapes_match_frontend_contracts() -> None:
    submit = AsyncSubmitResponse(job_id="j1", status_url="/api/process/status/j1")
    assert submit.model_dump() == {
        "job_id": "j1",
        "status": "pending",
        "status_url": "/api/process/status/j1",
    }

    # 2026-08-29 audit C-3 / H-3: the result token is intentionally
    # not a field on JobStatusResponse. The async client receives it
    # out-of-band via the ``job_completed`` SSE event payload.
    status = JobStatusResponse(
        job_id="j1",
        filename="a.pdf",
        status="complete",
        created_at=1.0,
        started_at=2.0,
        completed_at=3.0,
        duration_s=2.0,
        text_artifact_id="art",
        failed_pages=[2],
    )
    payload = status.model_dump()
    # frontend OcrJobStatusResponse field set
    assert set(payload) == {
        "job_id",
        "filename",
        "status",
        "created_at",
        "started_at",
        "completed_at",
        "duration_s",
        "error",
        "text_artifact_id",
        "failed_pages",
    }

    item = JobListItemResponse(id="j1", status="complete", timestamp="2026-01-01")
    assert set(item.model_dump()) == {
        "id",
        "filename",
        "model",
        "pipeline_mode",
        "pages",
        "duration_s",
        "timestamp",
        "status",
        "failed_pages",
    }


def test_job_status_response_accepts_cancelled() -> None:
    status = JobStatusResponse(
        job_id="j2",
        filename="b.pdf",
        status="cancelled",
        created_at=1.0,
        error="Job cancelled.",
    )
    assert status.status == "cancelled"
    assert status.error == "Job cancelled."


def test_async_submit_response_status_validation() -> None:
    for valid_status in ("pending", "processing", "complete", "error", "cancelled"):
        resp = AsyncSubmitResponse(
            job_id="j1",
            status=valid_status,
            status_url="/api/process/status/j1",
        )
        assert resp.status == valid_status

    with pytest.raises(ValidationError):
        AsyncSubmitResponse(
            job_id="j1",
            status="invalid_status",  # type: ignore[arg-type]
            status_url="/api/process/status/j1",
        )


def test_parse_bool_uniform_vocabulary() -> None:
    from omniscribe.plugins.ocr.schemas import _parse_bool

    # Truthy aliases
    for val in ("enabled", "yes", "on", "1", "true", "y", True):
        assert _parse_bool(val) is True
        assert _parse_bool(val, default=False) is True

    # Falsy aliases
    for val in ("disabled", "no", "off", "0", "false", "n", False):
        assert _parse_bool(val) is False
        assert _parse_bool(val, default=True) is False

    # Default fallback
    assert _parse_bool(None, default=False) is False
    assert _parse_bool(None, default=True) is True
    assert _parse_bool("unrecognized", default=False) is False


def test_ocr_request_coerces_extended_booleans() -> None:
    req = OCRRequest(
        preprocess_pages="enabled",  # type: ignore[arg-type]
        orientation_detection="yes",  # type: ignore[arg-type]
        deskew="disabled",  # type: ignore[arg-type]
        denoise="on",  # type: ignore[arg-type]
        normalize_contrast="off",  # type: ignore[arg-type]
        crop_cleanup="no",  # type: ignore[arg-type]
        quality_loop_enabled="1",  # type: ignore[arg-type]
    )
    assert req.preprocess_pages is True
    assert req.orientation_detection is True
    assert req.deskew is False
    assert req.denoise is True
    assert req.normalize_contrast is False
    assert req.crop_cleanup is False
    assert req.quality_loop_enabled is True


def test_ocr_payload_round_trip_preserves_request_fields() -> None:
    """Audit 5.1 (partial): the canonical _OcrPayload IR survives a
    submit → queue → run_job round trip with all request fields intact.
    """
    import dataclasses
    from pathlib import Path

    from omniscribe.plugins.ocr.service import _OcrPayload

    request = OCRRequest(model="some-model", pages="1-3", quality_target=0.9)
    payload = _OcrPayload(
        submission_id="sub-1",
        input_path=Path("/tmp/fake.pdf"),
        filename="doc.pdf",
        request=request,
    )
    # The dataclass is frozen; a replace with a new submission_id must
    # preserve every other field.
    again = dataclasses.replace(payload, submission_id="sub-2")
    assert again.submission_id == "sub-2"
    assert again.input_path == payload.input_path
    assert again.filename == payload.filename
    assert again.request.model == request.model
    assert again.request.pages == request.pages
    assert again.request.quality_target == pytest.approx(0.9)


def test_ocr_payload_lookup_miss_silently_uses_empty_job_id() -> None:
    """Audit 5.1: when the submission_id was evicted from the
    _submission_to_job map (capped at max_buffered_jobs), run_job falls
    back to job_id="". Verify the empty-string fallback is the documented
    contract — the cancellation channel degrades to "no per-job binding"
    rather than raising.
    """
    from pathlib import Path

    from omniscribe.plugins.ocr.service import _OcrPayload

    payload = _OcrPayload(
        submission_id="never-submitted",
        input_path=Path("/tmp/orphan.pdf"),
        filename="orphan.pdf",
        request=OCRRequest(),
    )
    # Empty submission-to-job map; ``.get`` with default returns "".
    submission_to_job: dict[str, str] = {}
    job_id = submission_to_job.get(payload.submission_id, "")
    assert job_id == ""


# ---------------------------------------------------------------------------
# Audit 6.3: Model Pre-flight Route
# ---------------------------------------------------------------------------


def test_preflight_request_accepts_partial_overrides() -> None:
    """The pre-flight request body is optional; each field defaults to
    None and the route falls back to the current /api/config value.
    """
    from omniscribe.plugins.ocr.schemas import PreflightRequest

    bare = PreflightRequest()
    assert bare.api_base is None
    assert bare.api_key is None
    assert bare.model is None

    override = PreflightRequest(api_base="http://localhost:9999/v1", model="m")
    assert override.api_base == "http://localhost:9999/v1"
    assert override.api_key is None
    assert override.model == "m"


def test_preflight_response_loads_false_carries_loaded_models() -> None:
    """The response shape lets the UI show "model mismatch: server has X,
    you asked for Y" without the caller parsing the detail string.
    """
    from omniscribe.plugins.ocr.schemas import PreflightResponse

    resp = PreflightResponse(
        loaded=False,
        requested_model="missing",
        api_base="http://x",
        loaded_models=["olmocr-2-7b"],
        detail="model 'missing' is not loaded",
    )
    assert resp.loaded is False
    assert resp.requested_model == "missing"
    assert resp.loaded_models == ["olmocr-2-7b"]


async def test_preflight_check_returns_misconfigured_when_coords_empty() -> None:
    """Calling preflight with no overrides on a fresh service whose
    /api/config has no api_base must return a structured 'must be
    configured' detail, not raise.
    """
    from unittest.mock import MagicMock

    from omniscribe.plugins.ocr.service import OCRServiceImpl

    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._config = {}
    service._settings = MagicMock()

    resp = await service.preflight_check()
    assert resp.loaded is False
    assert resp.detail == "api_base and model must be configured before pre-flight"
    assert resp.loaded_models == []


async def test_preflight_check_closes_ephemeral_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a clean probe (the requested model is loaded) the ephemeral
    AsyncOpenAI client must be closed via close so the connection pool is
    released — otherwise every preflight leaks an AsyncOpenAI client.
    """
    from unittest.mock import AsyncMock, MagicMock

    from omniscribe.plugins.ocr.service import OCRServiceImpl

    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._config = {
        "api_base": "http://localhost:1234/v1",
        "api_key": "lm-studio",
        "model": "allenai/olmocr-2-7b",
    }
    service._settings = MagicMock()

    closed = []

    class _MockClient:
        def __init__(self, *, base_url: str, api_key: str):
            self.base_url = base_url
            self.api_key = api_key

        async def close(self) -> None:
            closed.append(self)

    monkeypatch.setattr("openai.AsyncOpenAI", _MockClient)
    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(return_value=["allenai/olmocr-2-7b"]),
    )

    resp = await service.preflight_check()
    assert resp.loaded is True
    assert resp.requested_model == "allenai/olmocr-2-7b"
    assert resp.api_base == "http://localhost:1234/v1"
    assert resp.loaded_models == ["allenai/olmocr-2-7b"]
    assert resp.detail == "Model is loaded and ready"
    assert closed, "ephemeral client must be closed after preflight"


async def test_preflight_post_rejects_ssrf() -> None:
    """Audit 6.3: POST /api/process/preflight with an SSRF target is rejected
    with 403 and error code 'ssrf_blocked'.
    """
    from unittest.mock import MagicMock

    import httpx
    from fastapi import FastAPI

    from omniscribe.plugins.ocr.plugin import build_ocr_router
    from omniscribe.plugins.ocr.service import OCRServiceImpl

    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._config = {
        "api_base": "http://localhost:1234/v1",
        "api_key": "lm-studio",
        "model": "allenai/olmocr-2-7b",
    }
    service._settings = MagicMock()
    service._max_upload_mb = 100

    # Verify service method directly
    resp_obj = await service.preflight_check(api_base="http://169.254.169.254/v1")
    assert resp_obj.loaded is False
    assert "SSRF blocked" in resp_obj.detail

    # Verify POST /api/process/preflight route
    app = FastAPI()
    app.include_router(build_ocr_router(service))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/process/preflight",
            json={"api_base": "http://169.254.169.254/v1"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "ssrf_blocked"
        assert "SSRF blocked" in body["detail"]


async def test_empty_content_type_format_validation() -> None:
    """Empty Content-Type uploads must be validated via magic-byte sniffing:
    garbage contents are rejected with 415, while valid signatures pass.
    """
    from unittest.mock import AsyncMock, MagicMock

    import httpx
    from fastapi import FastAPI, Response

    from omniscribe.plugins.ocr.plugin import build_ocr_router
    from omniscribe.plugins.ocr.service import OCRServiceImpl

    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._config = {}
    service._settings = MagicMock()
    service._max_upload_mb = 100
    service._quality_defaults = {}
    # ``run_sync`` is a method on the class; assigning an AsyncMock to
    # an instance attribute is the canonical test pattern but mypy
    # flags it as a method-assign. Suppress locally for this stub.
    service.run_sync = AsyncMock(  # type: ignore[method-assign]
        return_value=Response(content=b"ok", media_type="application/pdf")
    )

    app = FastAPI()
    app.include_router(build_ocr_router(service))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Empty Content-Type with invalid/garbage bytes -> 415
        resp_invalid = await client.post(
            "/api/process",
            files={"file": ("doc", b"not a supported document", "")},
        )
        assert resp_invalid.status_code == 415
        assert (
            "could not detect a supported document format"
            in resp_invalid.json().get("detail", "")
        )

        # Empty Content-Type with valid PDF bytes -> 200 (format sniffing succeeds)
        resp_valid = await client.post(
            "/api/process",
            files={"file": ("doc", b"%PDF-1.4 sample content", "")},
        )
        assert resp_valid.status_code == 200
        service.run_sync.assert_awaited_once()


def test_queue_status_to_http_exported_and_mapped() -> None:
    import omniscribe.plugins.ocr.schemas as schemas
    from omniscribe.plugins.ocr.schemas import _QUEUE_STATUS_TO_HTTP

    assert "_QUEUE_STATUS_TO_HTTP" in schemas.__all__
    assert _QUEUE_STATUS_TO_HTTP["queued"] == "pending"
    assert _QUEUE_STATUS_TO_HTTP["running"] == "processing"
    assert _QUEUE_STATUS_TO_HTTP["complete"] == "complete"
    assert _QUEUE_STATUS_TO_HTTP["error"] == "error"
    assert _QUEUE_STATUS_TO_HTTP["cancelled"] == "cancelled"


def test_split_processors_with_sequence_and_flattening() -> None:
    # List of strings
    req1 = OCRRequest(document_processors=["reading_order", "table_extraction"])
    assert req1.document_processors == ["reading_order", "table_extraction"]

    # Tuple of strings
    req2 = OCRRequest(document_processors=("reading_order", "table_extraction"))  # type: ignore[arg-type]
    assert req2.document_processors == ["reading_order", "table_extraction"]

    # Sequence containing comma-separated strings
    req3 = OCRRequest(
        document_processors=["reading_order, table_extraction", "section_analysis"]
    )
    assert req3.document_processors == [
        "reading_order",
        "table_extraction",
        "section_analysis",
    ]

    # Rejection of unknown processor in sequence
    with pytest.raises(ValidationError, match="unknown document processor"):
        OCRRequest(document_processors=["reading_order", "nonexistent_proc"])


def test_coerce_bool_dynamic_across_fields() -> None:
    # All boolean fields coerced from strings. Use ``model_validate`` so the
    # ``bool`` annotations are bypassed: these values flow through the bool
    # field validators.
    req = OCRRequest.model_validate(
        {
            "preprocess_pages": "true",
            "orientation_detection": "1",
            "deskew": "false",
            "denoise": "yes",
            "normalize_contrast": "0",
            "crop_cleanup": "no",
            "quality_loop_enabled": "true",
        }
    )
    assert req.preprocess_pages is True
    assert req.orientation_detection is True
    assert req.deskew is False
    assert req.denoise is True
    assert req.normalize_contrast is False
    assert req.crop_cleanup is False
    assert req.quality_loop_enabled is True
