"""Tests for the OCR model pre-flight route and service layer.

Verifies:
- OCRService protocol conformance for preflight_check
- Successful model loading check (loaded=True, detail="Model is loaded and ready")
- Missing/not loaded model response (loaded=False, listing available models)
- Connection failures and timeouts handled deterministically (loaded=False, detail="Endpoint unreachable: ...")
- Ephemeral AsyncOpenAI client connection pool closed in finally block
- PreflightRequest coordinate overrides via POST /api/process/preflight
- SSRF target blocking returning HTTP 403 ssrf_blocked envelope
- Default coordinate probing via GET /api/process/preflight
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from omniscribe.config import RuntimeSettings
from omniscribe.plugins.ocr.plugin import OCRService, build_ocr_router
from omniscribe.plugins.ocr.schemas import PreflightRequest
from omniscribe.plugins.ocr.service import OCRServiceImpl


def _build_test_service(
    *,
    api_base: str = "http://localhost:1234/v1",
    api_key: str = "lm-studio",
    model: str = "allenai/olmocr-2-7b",
) -> OCRServiceImpl:
    """Instantiate a minimal OCRServiceImpl for pre-flight testing."""
    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._config = {
        "api_base": api_base,
        "api_key": api_key,
        "model": model,
    }
    settings = MagicMock(spec=RuntimeSettings)
    settings.api_base = api_base
    settings.api_key = api_key
    settings.model = model
    settings.llm_api_base = api_base
    settings.llm_api_key = api_key
    settings.llm_model = model
    service._settings = settings
    service._max_upload_mb = 100
    return service


def test_ocr_service_protocol_conformance() -> None:
    """OCRServiceImpl must satisfy the runtime-checkable OCRService protocol."""
    service = _build_test_service()
    assert isinstance(service, OCRService)
    assert callable(getattr(service, "preflight_check", None))


async def test_preflight_check_loaded_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the requested model exists on the server, return loaded=True
    with the loaded models list and the standard ready detail.
    """
    service = _build_test_service(model="allenai/olmocr-2-7b")

    client_closed: list[bool] = []

    class _MockAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            self.base_url = base_url
            self.api_key = api_key

        async def close(self) -> None:
            client_closed.append(True)

    monkeypatch.setattr("openai.AsyncOpenAI", _MockAsyncOpenAI)
    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(return_value=["allenai/olmocr-2-7b", "mistral-7b"]),
    )

    response = await service.preflight_check()

    assert response.loaded is True
    assert response.requested_model == "allenai/olmocr-2-7b"
    assert response.api_base == "http://localhost:1234/v1"
    assert response.loaded_models == ["allenai/olmocr-2-7b", "mistral-7b"]
    assert response.detail == "Model is loaded and ready"
    assert client_closed == [True], "Ephemeral client must be closed in finally"


async def test_preflight_check_model_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the requested model is not loaded, return loaded=False with the
    available models and a descriptive detail.
    """
    service = _build_test_service(model="allenai/olmocr-2-7b")

    client_closed: list[bool] = []

    class _MockAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            self.base_url = base_url
            self.api_key = api_key

        async def close(self) -> None:
            client_closed.append(True)

    monkeypatch.setattr("openai.AsyncOpenAI", _MockAsyncOpenAI)
    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(return_value=["other-model-1", "other-model-2"]),
    )

    response = await service.preflight_check()

    assert response.loaded is False
    assert response.requested_model == "allenai/olmocr-2-7b"
    assert response.api_base == "http://localhost:1234/v1"
    assert response.loaded_models == ["other-model-1", "other-model-2"]
    assert (
        response.detail
        == "Model 'allenai/olmocr-2-7b' is not currently loaded on http://localhost:1234/v1"
    )
    assert client_closed == [True], "Ephemeral client must be closed in finally"


async def test_preflight_check_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the server connection fails, return loaded=False with an
    empty models list and 'Endpoint unreachable: {exc}' detail.
    """
    service = _build_test_service()

    client_closed: list[bool] = []

    class _MockAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            self.base_url = base_url
            self.api_key = api_key

        async def close(self) -> None:
            client_closed.append(True)

    monkeypatch.setattr("openai.AsyncOpenAI", _MockAsyncOpenAI)
    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(side_effect=ConnectionRefusedError("Connection refused by target")),
    )

    response = await service.preflight_check()

    assert response.loaded is False
    assert response.loaded_models == []
    assert response.detail == "Endpoint unreachable: Connection refused by target"
    assert client_closed == [True], (
        "Ephemeral client must be closed even on connection failure"
    )


async def test_preflight_check_custom_request_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PreflightRequest overrides target coordinates over defaults."""
    service = _build_test_service(
        api_base="http://default:1234/v1",
        model="default-model",
    )

    recorded_init: list[tuple[str, str]] = []
    client_closed: list[bool] = []

    class _MockAsyncOpenAI:
        def __init__(self, *, base_url: str, api_key: str) -> None:
            recorded_init.append((base_url, api_key))

        async def close(self) -> None:
            client_closed.append(True)

    monkeypatch.setattr("openai.AsyncOpenAI", _MockAsyncOpenAI)
    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(return_value=["candidate-model-v2"]),
    )

    request = PreflightRequest(
        api_base="http://localhost:8000/v1",
        api_key="secret-token",
        model="candidate-model-v2",
    )

    response = await service.preflight_check(request)

    assert response.loaded is True
    assert response.requested_model == "candidate-model-v2"
    assert response.api_base == "http://localhost:8000/v1"
    assert response.loaded_models == ["candidate-model-v2"]
    assert response.detail == "Model is loaded and ready"
    assert recorded_init == [("http://localhost:8000/v1", "secret-token")]
    assert client_closed == [True]


async def test_preflight_check_missing_configuration() -> None:
    """When api_base or model is not configured, return loaded=False with guidance detail."""
    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._config = {}
    service._settings = MagicMock(spec=RuntimeSettings)
    service._settings.api_base = None
    service._settings.model = None
    service._settings.llm_api_base = None
    service._settings.llm_model = None

    response = await service.preflight_check()

    assert response.loaded is False
    assert response.loaded_models == []
    assert response.detail == "api_base and model must be configured before pre-flight"


async def test_preflight_check_ssrf_protection() -> None:
    """Blocked SSRF targets must return loaded=False without opening an outbound connection."""
    service = _build_test_service()

    request = PreflightRequest(api_base="http://169.254.169.254/v1")
    response = await service.preflight_check(request)

    assert response.loaded is False
    assert response.loaded_models == []
    assert "SSRF blocked" in response.detail


# ---------------------------------------------------------------------------
# Route integration tests (GET and POST /api/process/preflight)
# ---------------------------------------------------------------------------


async def test_route_preflight_get_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/process/preflight queries active coordinates and returns 200 with PreflightResponse."""
    service = _build_test_service(model="allenai/olmocr-2-7b")

    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(return_value=["allenai/olmocr-2-7b"]),
    )

    app = FastAPI()
    app.include_router(build_ocr_router(service))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/process/preflight")

    assert resp.status_code == 200
    body = resp.json()
    assert body["loaded"] is True
    assert body["requested_model"] == "allenai/olmocr-2-7b"
    assert body["api_base"] == "http://localhost:1234/v1"
    assert body["loaded_models"] == ["allenai/olmocr-2-7b"]
    assert body["detail"] == "Model is loaded and ready"


async def test_route_preflight_get_model_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/process/preflight returns 200 with loaded=False on model mismatch."""
    service = _build_test_service(model="allenai/olmocr-2-7b")

    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(return_value=["meta-llama/Llama-3-8B"]),
    )

    app = FastAPI()
    app.include_router(build_ocr_router(service))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/process/preflight")

    assert resp.status_code == 200
    body = resp.json()
    assert body["loaded"] is False
    assert body["requested_model"] == "allenai/olmocr-2-7b"
    assert body["loaded_models"] == ["meta-llama/Llama-3-8B"]
    assert "not currently loaded" in body["detail"]


async def test_route_preflight_post_custom_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/process/preflight accepts override payload and probes candidate."""
    service = _build_test_service(
        api_base="http://default:1234/v1",
        model="default-model",
    )

    monkeypatch.setattr(
        "omniscribe.core.ocr.client._list_loaded_model_ids",
        AsyncMock(return_value=["candidate-model-v2"]),
    )

    app = FastAPI()
    app.include_router(build_ocr_router(service))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/process/preflight",
            json={
                "api_base": "http://localhost:8000/v1",
                "model": "candidate-model-v2",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["loaded"] is True
    assert body["requested_model"] == "candidate-model-v2"
    assert body["api_base"] == "http://localhost:8000/v1"
    assert body["loaded_models"] == ["candidate-model-v2"]
    assert body["detail"] == "Model is loaded and ready"


async def test_route_preflight_post_ssrf_blocked() -> None:
    """POST /api/process/preflight with an SSRF target returns HTTP 403 ssrf_blocked."""
    service = _build_test_service()

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
