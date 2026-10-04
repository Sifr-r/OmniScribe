"""Router contract tests for the transcribe plugin (client-frozen)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from omniscribe.core.transcription.types import (
    TranscriptionResult,
    TranscriptionSegment,
)

WAV_HEADER = (
    b"RIFF$"
    + b"\x00\x00\x00"
    + b"WAVEfmt "
    + b"\x10\x00\x00\x00\x01\x00\x01\x00"
    + b"D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


def _result(text: str = "Sample transcribed speech text") -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        language="en",
        duration=4.0,
        segments=[TranscriptionSegment(id=0, start=0.0, end=4.0, text=text)],
    )


def _stub_engine(monkeypatch: Any, result: TranscriptionResult) -> None:
    from omniscribe.plugins.transcribe import service

    class _Engine:
        async def transcribe(self, **kwargs: Any) -> TranscriptionResult:
            return result

    monkeypatch.setattr(service, "get_transcription_engine", lambda **kw: _Engine())


def test_transcribe_routes_are_mounted(api_client: TestClient) -> None:
    paths = set(json.loads(api_client.get("/openapi.json").text)["paths"])
    assert "/api/transcribe" in paths
    assert "/api/config/transcription" in paths
    assert "/api/models/transcription" in paths


def test_transcribe_success_contract(api_client: TestClient, monkeypatch: Any) -> None:
    _stub_engine(monkeypatch, _result())
    response = api_client.post(
        "/api/transcribe",
        files={"file": ("test.wav", WAV_HEADER, "audio/wav")},
        data={"model": "whisper-1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Sample transcribed speech text"
    assert data["language"] == "en"
    assert data["duration"] == 4.0
    assert data["job_id"].startswith("job-")
    assert data["text_artifact_id"] and data["text_artifact_token"]
    assert data["metadata_artifact_id"] and data["metadata_artifact_token"]
    assert data["segments"][0]["text"] == "Sample transcribed speech text"
    assert set(data) == {
        "text",
        "language",
        "duration",
        "job_id",
        "segments",
        "text_artifact_id",
        "text_artifact_token",
        "metadata_artifact_id",
        "metadata_artifact_token",
    }
    assert data["segments"] == [
        {
            "id": 0,
            "start": 0.0,
            "end": 4.0,
            "text": "Sample transcribed speech text",
            "confidence": None,
        }
    ]


@pytest.mark.parametrize("malformed", [None, "id", "nan", "inf", "extension_nan"])
def test_transcribe_response_boundary_preserves_fields_and_rejects_invalid_types(
    api_client: TestClient, monkeypatch: Any, malformed: str | None
) -> None:
    from omniscribe.plugins.transcribe.service import TranscriptionServiceImpl

    payload: dict[str, Any] = {
        "text": "speech",
        "language": None,
        "vendor": "extension",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 4.0,
                "text": "speech",
                "words": [{"word": "speech"}],
            }
        ],
    }
    if malformed == "id":
        payload["segments"][0]["id"] = "provider-secret-invalid-id"
    elif malformed == "nan":
        payload["segments"][0]["start"] = float("nan")
    elif malformed == "inf":
        payload["duration"] = float("inf")
    elif malformed == "extension_nan":
        payload["vendor"] = float("nan")

    async def stub_transcribe(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(TranscriptionServiceImpl, "transcribe", stub_transcribe)
    response = api_client.post(
        "/api/transcribe",
        files={"file": ("test.wav", WAV_HEADER, "audio/wav")},
    )
    if malformed:
        assert response.status_code == 500
        assert response.json() == {
            "error": "internal_server_error",
            "detail": "Transcription service returned an invalid response.",
        }
        assert "provider-secret" not in response.text
    else:
        assert response.status_code == 200
        assert response.json() == payload


def test_transcribe_unsupported_format_400(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/transcribe",
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_request"
    assert "Unsupported audio format" in body["detail"]


@pytest.mark.parametrize("options", [{"engine": "unknown"}, {"temperature": "bad"}])
def test_transcribe_invalid_options_return_serializable_422(
    api_client: TestClient, options: dict[str, str]
) -> None:
    response = api_client.post(
        "/api/transcribe",
        files={"file": ("test.wav", WAV_HEADER, "audio/wav")},
        data=options,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert detail[0]["loc"] == [next(iter(options))]
    assert detail[0]["type"] == "value_error"


def test_transcribe_ssrf_override_403(api_client: TestClient, monkeypatch: Any) -> None:
    _stub_engine(monkeypatch, _result())
    response = api_client.post(
        "/api/transcribe",
        files={"file": ("a.wav", WAV_HEADER, "audio/wav")},
        data={"api_base": "http://169.254.169.254/latest"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "ssrf_blocked"


def test_config_get_masks_and_post_roundtrips(api_client: TestClient) -> None:
    get_resp = api_client.get("/api/config/transcription")
    assert get_resp.status_code == 200
    assert get_resp.json()["transcription_model"] == "whisper-1"

    post_resp = api_client.post(
        "/api/config/transcription",
        json={
            "model": "gpt-4o-audio-preview",
            "transcription_api_key": "my-real-secret-key-xyz123",
            "engine": "api",
        },
    )
    assert post_resp.status_code == 200
    updated = post_resp.json()
    assert updated["transcription_model"] == "gpt-4o-audio-preview"
    assert "my-real-secret-key" not in updated["transcription_api_key"]
    assert "..." in updated["transcription_api_key"]

    # Write-through: a later GET reflects the update.
    assert (
        api_client.get("/api/config/transcription").json()["transcription_model"]
        == "gpt-4o-audio-preview"
    )


def test_config_temperature_out_of_range_422(api_client: TestClient) -> None:
    response = api_client.post("/api/config/transcription", json={"temperature": 2.5})
    assert response.status_code == 422


def test_models_transcription_returns_fallback_shape(
    api_client: TestClient, monkeypatch: Any
) -> None:
    from omniscribe.plugins.transcribe import config_store

    class _FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, headers: dict | None = None) -> Any:
            raise RuntimeError("no network")

    monkeypatch.setattr(config_store.httpx, "AsyncClient", _FailingClient)
    response = api_client.get("/api/models/transcription")
    assert response.status_code == 200
    models = response.json()["models"]
    assert "whisper-1" in models
    assert len(models) == 6
