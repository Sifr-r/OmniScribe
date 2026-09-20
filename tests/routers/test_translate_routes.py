"""Router contract tests for the translate plugin.

Contract source: the Flutter client (`feature_repository.dart:68-107`,
`api_constants.dart:59-64`, `feature_models.dart`) plus the recovered
pre-harness tests (commit `e6b7b89^`).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from omniscribe.plugins.state_backend import StateBackend


def _seed_text_artifact(client: TestClient, pages: dict[str, str]) -> tuple[str, str]:
    backend = client.app.state.context.inject(StateBackend)  # type: ignore[attr-defined]
    artifact_id = uuid.uuid4().hex
    token = "t" * 43
    asyncio.run(
        backend.put_artifact(
            id=artifact_id,
            token=token,
            owner_job_id="",
            content_type="application/json",
            blob=json.dumps(pages).encode("utf-8"),
            ttl_seconds=3600,
        )
    )
    return artifact_id, token


def _stub_llm(monkeypatch: Any, payload: str) -> None:
    from omniscribe.plugins.translate import service

    async def fake_call_llm(**kwargs: Any) -> str:
        return payload

    monkeypatch.setattr(service, "call_llm", fake_call_llm)


def _stub_llm_unreachable(monkeypatch: Any) -> None:
    from omniscribe.plugins.translate import service

    async def fail_call_llm(**kwargs: Any) -> str:
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(service, "call_llm", fail_call_llm)


def _wait_translation_state(
    client: TestClient, job_id: str, state: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    """Poll the translate status route until `state` is reached.

    The queue worker runs on the TestClient portal loop, so sleeping the
    test thread lets it make progress between polls.
    """
    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        body = client.get(f"/api/translate/status/{job_id}").json()
        if body.get("state") == state:
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {state}: {body}")


def test_translate_routes_are_mounted(api_client: TestClient) -> None:
    paths = set(json.loads(api_client.get("/openapi.json").text)["paths"])
    assert "/api/translate" in paths
    assert "/api/translate/async" in paths
    assert "/api/translate/status/{job_id}" in paths
    assert "/api/translate/result/{job_id}" in paths
    assert "/api/translate/nllb" in paths


def test_translate_sync_happy_path(api_client: TestClient, monkeypatch: Any) -> None:
    _stub_llm(monkeypatch, "Bonjour le monde")
    response = api_client.post(
        "/api/translate",
        json={"text": "Hello world", "target_language": "French"},
    )
    assert response.status_code == 200
    assert response.json() == {"translated_text": "Bonjour le monde"}


def test_translate_sync_missing_text_and_artifact_400(
    api_client: TestClient, monkeypatch: Any
) -> None:
    _stub_llm_unreachable(monkeypatch)
    response = api_client.post("/api/translate", json={"target_language": "French"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_request"
    assert (
        body["detail"]
        == "'text' or 'text_artifact_id'/'text_artifact_token' is required"
    )


def test_translate_sync_ssrf_blocked_403(
    api_client: TestClient, monkeypatch: Any
) -> None:
    _stub_llm_unreachable(monkeypatch)
    response = api_client.post(
        "/api/translate",
        json={
            "text": "x",
            "api_base": "http://169.254.169.254/latest",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"] == "ssrf_blocked"


def test_translate_sync_artifact_fallback(
    api_client: TestClient, monkeypatch: Any
) -> None:
    _stub_llm(monkeypatch, "traduit")
    artifact_id, token = _seed_text_artifact(
        api_client, {"0": "page one", "1": "page two"}
    )
    response = api_client.post(
        "/api/translate",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "target_language": "French",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"translated_text": "traduit"}


def test_translate_sync_unknown_artifact_404(
    api_client: TestClient, monkeypatch: Any
) -> None:
    _stub_llm_unreachable(monkeypatch)
    response = api_client.post(
        "/api/translate",
        json={"text_artifact_id": "0" * 32, "text_artifact_token": "t" * 43},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_translate_async_submit_and_complete(
    api_client: TestClient, monkeypatch: Any
) -> None:
    pytest.importorskip("langgraph")
    _stub_llm(monkeypatch, "traduit")
    artifact_id, token = _seed_text_artifact(api_client, {"0": "Hello world."})
    response = api_client.post(
        "/api/translate/async",
        json={"text_artifact_id": artifact_id, "text_artifact_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Processing"
    assert body["job_id"]

    pending = api_client.get(f"/api/translate/status/{body['job_id']}").json()
    assert pending["state"] in {"PENDING", "PROGRESS", "SUCCESS"}

    done = _wait_translation_state(api_client, body["job_id"], "SUCCESS")
    result = done["result"]
    assert result["artifact_id"] == artifact_id
    assert result["page_count"] == 1
    # C-3/H-3 semantics: no artifact token ever crosses the status endpoint.
    assert "translated_artifact_token" not in json.dumps(done)
    assert "token" not in result


def test_translate_async_accepts_inline_text_without_artifact(
    api_client: TestClient,
) -> None:
    # Diagnosis finding #4 (2026-09-15): the async route now accepts
    # inline ``text`` in addition to ``text_artifact_id`` /
    # ``text_artifact_token`` so the client path that translates a
    # freshly-typed string no longer needs to round-trip through an
    # artifact pair. Missing both still 400s.
    response = api_client.post("/api/translate/async", json={"text": "no artifact"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"]


def test_translate_async_rejects_completely_empty_body(
    api_client: TestClient,
) -> None:
    response = api_client.post("/api/translate/async", json={})
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "bad_request"


def test_translate_async_unknown_artifact_404(
    api_client: TestClient, monkeypatch: Any
) -> None:
    pytest.importorskip("langgraph")
    _stub_llm_unreachable(monkeypatch)
    response = api_client.post(
        "/api/translate/async",
        json={"text_artifact_id": "0" * 32, "text_artifact_token": "t" * 43},
    )
    assert response.status_code == 404


def test_translate_async_langgraph_missing_503(
    api_client: TestClient, monkeypatch: Any
) -> None:
    from omniscribe.core.translate.config import AsyncTranslationUnavailable
    from omniscribe.plugins.translate import service

    def raise_unavailable() -> None:
        raise AsyncTranslationUnavailable("langgraph is not installed")

    monkeypatch.setattr(service, "get_translation_app", raise_unavailable)
    artifact_id, token = _seed_text_artifact(api_client, {"0": "Hello."})
    response = api_client.post(
        "/api/translate/async",
        json={"text_artifact_id": artifact_id, "text_artifact_token": token},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "backend_unavailable"
    assert "langgraph" in body["detail"]


def test_translate_status_unknown_job_404(api_client: TestClient) -> None:
    response = api_client.get("/api/translate/status/no-such-job")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_translate_nllb_happy_path(api_client: TestClient, monkeypatch: Any) -> None:
    from omniscribe.core.translate.nllb import NLLBResult
    from omniscribe.plugins.translate import service

    class _FakeEngine:
        def is_available(self) -> bool:
            return True

        async def translate(self, text: str, target_language: str) -> NLLBResult:
            return NLLBResult(
                text="bonjour", source_lang="eng_Latn", target_lang="fra_Latn"
            )

    monkeypatch.setattr(service, "_get_nllb_engine", lambda: _FakeEngine())
    response = api_client.post(
        "/api/translate/nllb",
        json={"text": "hello", "target_language": "French"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "translated_text": "bonjour",
        "source_lang": "eng_Latn",
        "target_lang": "fra_Latn",
    }


def test_translate_nllb_blank_text_422(api_client: TestClient) -> None:
    response = api_client.post("/api/translate/nllb", json={"text": "   "})
    assert response.status_code == 422
    assert response.json()["error"] == "bad_request"


def test_translate_nllb_unavailable_503(
    api_client: TestClient, monkeypatch: Any
) -> None:
    from omniscribe.plugins.translate import service

    class _UnavailableEngine:
        def is_available(self) -> bool:
            return False

    monkeypatch.setattr(service, "_get_nllb_engine", lambda: _UnavailableEngine())
    response = api_client.post(
        "/api/translate/nllb", json={"text": "hello", "target_language": "French"}
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "backend_unavailable"
    assert "uv sync --extra nllb" in body["detail"]


def test_translate_nllb_engine_failure_502(
    api_client: TestClient, monkeypatch: Any
) -> None:
    from omniscribe.plugins.translate import service

    class _BrokenEngine:
        def is_available(self) -> bool:
            return True

        async def translate(self, text: str, target_language: str) -> object:
            raise RuntimeError("torch is not installed")

    monkeypatch.setattr(service, "_get_nllb_engine", lambda: _BrokenEngine())
    response = api_client.post(
        "/api/translate/nllb", json={"text": "hello", "target_language": "French"}
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "ai_error"


# ---------------------------------------------------------------------------
# GET /api/translate/result/{job_id} — token-redeeming async result (ride-along)
# ---------------------------------------------------------------------------


def _seed_artifact(
    api_client: TestClient, artifact_id: str, token: str, blob: bytes
) -> None:
    asyncio.run(
        api_client.app.state.context.inject(StateBackend).put_artifact(  # type: ignore[attr-defined]
            id=artifact_id,
            token=token,
            owner_job_id="",
            content_type="application/json",
            blob=blob,
            ttl_seconds=3600,
        )
    )


def _plant_completed_record(
    api_client: TestClient, artifact_id: str, token: str
) -> str:
    import uuid as _uuid

    from omniscribe.plugins.state_backend import JobRecord

    backend = api_client.app.state.context.inject(StateBackend)  # type: ignore[attr-defined]
    job_id = _uuid.uuid4().hex
    asyncio.run(
        backend.upsert_job(
            JobRecord(
                job_id=job_id,
                status="complete",
                result_artifact_id=artifact_id,
                result_artifact_token=token,
            )
        )
    )
    return job_id


def test_translate_result_unknown_job_404(api_client: TestClient) -> None:
    response = api_client.get("/api/translate/result/no-such-job?token=abc")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "detail": "result not found"}


def test_translate_result_wrong_token_404(api_client: TestClient) -> None:
    import uuid as _uuid

    artifact_id = _uuid.uuid4().hex
    _seed_artifact(api_client, artifact_id, "t" * 43, b'{"page_count": 1}')
    job_id = _plant_completed_record(api_client, artifact_id, "t" * 43)
    response = api_client.get(f"/api/translate/result/{job_id}?token=wrong")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "detail": "result not found"}


def test_translate_result_incomplete_job_404(api_client: TestClient) -> None:
    import uuid as _uuid

    from omniscribe.plugins.state_backend import JobRecord

    backend = api_client.app.state.context.inject(StateBackend)  # type: ignore[attr-defined]
    job_id = _uuid.uuid4().hex
    asyncio.run(backend.upsert_job(JobRecord(job_id=job_id, status="running")))
    response = api_client.get(f"/api/translate/result/{job_id}?token=abc")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found", "detail": "result not found"}


def test_translate_result_happy_path(api_client: TestClient) -> None:
    import uuid as _uuid

    artifact_id = _uuid.uuid4().hex
    token = "t" * 43
    _seed_artifact(
        api_client,
        artifact_id,
        token,
        json.dumps({"0": "Bonjour le monde"}).encode("utf-8"),
    )
    job_id = _plant_completed_record(api_client, artifact_id, token)
    response = api_client.get(f"/api/translate/result/{job_id}?token={token}")
    assert response.status_code == 200
    assert response.json() == {"0": "Bonjour le monde"}


def test_translate_result_uniform_404(api_client: TestClient) -> None:
    # No existence leak (C-3/H-3): every failure mode must return a
    # byte-identical 404 envelope.
    import uuid as _uuid

    from omniscribe.plugins.state_backend import JobRecord

    backend = api_client.app.state.context.inject(StateBackend)  # type: ignore[attr-defined]
    token = "t" * 43
    corrupt_artifact_id = _uuid.uuid4().hex
    _seed_artifact(api_client, corrupt_artifact_id, token, b"not json at all")
    job_running = _uuid.uuid4().hex
    asyncio.run(backend.upsert_job(JobRecord(job_id=job_running, status="running")))
    job_store_miss = _uuid.uuid4().hex
    asyncio.run(
        backend.upsert_job(
            JobRecord(
                job_id=job_store_miss,
                status="complete",
                result_artifact_id="ghost-artifact",
                result_artifact_token=token,
            )
        )
    )
    job_corrupt_json = _uuid.uuid4().hex
    asyncio.run(
        backend.upsert_job(
            JobRecord(
                job_id=job_corrupt_json,
                status="complete",
                result_artifact_id=corrupt_artifact_id,
                result_artifact_token=token,
            )
        )
    )
    responses = [
        api_client.get("/api/translate/result/no-such-job?token=abc"),
        api_client.get(f"/api/translate/result/{job_running}?token=abc"),
        api_client.get(f"/api/translate/result/{job_store_miss}?token={token}"),
        api_client.get("/api/translate/result/another-missing"),
        api_client.get(f"/api/translate/result/{job_corrupt_json}?token={token}"),
        api_client.get("/api/translate/result/yet-another"),
    ]
    for response in responses:
        assert response.status_code == 404
        assert response.json() == {"error": "not_found", "detail": "result not found"}
    assert len({response.content for response in responses}) == 1
