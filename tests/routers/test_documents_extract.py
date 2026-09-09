"""Router contract tests for POST /api/extract."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient


def _stub_llm(monkeypatch: Any, payload: str, calls: list[dict[str, Any]]) -> None:
    from omniscribe.plugins.documents import service

    async def fake_call_llm(**kwargs: Any) -> str:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(service, "call_llm", fake_call_llm)


def test_extract_nests_under_extracted_data(
    api_client: TestClient, monkeypatch: Any
) -> None:
    calls: list[dict[str, Any]] = []
    _stub_llm(monkeypatch, '{"vendor_name": "Acme", "total_amount": 10}', calls)
    response = api_client.post(
        "/api/extract",
        json={"text": "Invoice from Acme, total 10 USD.", "template": "invoice"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "extracted_data": {"vendor_name": "Acme", "total_amount": 10}
    }
    assert calls and "'invoice_number'" in calls[0]["messages"][0]["content"]


def test_extract_passes_a_timeout_beyond_the_shared_client_default(
    api_client: TestClient, monkeypatch: Any
) -> None:
    """Extraction must not inherit the 60s shared-client timeout.

    A local reasoning model can spend minutes producing its answer; at 60s the
    route returned a bare 502 with no sign the model was still working.
    """
    calls: list[dict[str, Any]] = []
    _stub_llm(monkeypatch, '{"vendor_name": "Acme"}', calls)
    response = api_client.post(
        "/api/extract", json={"text": "Invoice from Acme.", "template": "invoice"}
    )
    assert response.status_code == 200
    assert calls[0]["timeout"] == 240.0


def test_extract_invalid_model_json_yields_empty_object(
    api_client: TestClient, monkeypatch: Any
) -> None:
    _stub_llm(monkeypatch, "completely not json", [])
    response = api_client.post(
        "/api/extract", json={"text": "some text", "template": "invoice"}
    )
    assert response.status_code == 200
    assert response.json() == {"extracted_data": {}}


def test_extract_empty_text_400(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/extract", json={"text": "   ", "template": "invoice"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_request"
    assert body["detail"] == "'text' is required"


def test_extract_ssrf_blocked_403(api_client: TestClient, monkeypatch: Any) -> None:
    _stub_llm(monkeypatch, "{}", [])
    response = api_client.post(
        "/api/extract",
        json={
            "text": "x",
            "template": "invoice",
            # Cloud-metadata range: blocked even with ALLOW_SSRF_LOCAL=true.
            "api_base": "http://169.254.169.254/latest",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"] == "ssrf_blocked"
    assert response.json()["detail"].startswith("URL targets a blocked address: ")


def test_extract_custom_template_sends_custom_prompt(
    api_client: TestClient, monkeypatch: Any
) -> None:
    calls: list[dict[str, Any]] = []
    _stub_llm(monkeypatch, '{"answer": "yes"}', calls)
    response = api_client.post(
        "/api/extract",
        json={
            "text": "doc",
            "template": "custom",
            "custom_prompt": "find the total",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"extracted_data": {"answer": "yes"}}
    assert "find the total" in calls[0]["messages"][0]["content"]


def test_extract_provider_failure_502_envelope(
    api_client: TestClient, monkeypatch: Any
) -> None:
    from omniscribe.plugins.documents import service

    async def boom(**kwargs: Any) -> str:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(service, "call_llm", boom)
    response = api_client.post(
        "/api/extract", json={"text": "x", "template": "invoice"}
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "ai_error"
    assert body["detail"] == "The AI service request failed."
    assert "connection reset" not in json.dumps(body)
