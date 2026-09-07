from __future__ import annotations

import asyncio
import json
import secrets
import uuid

from fastapi.testclient import TestClient

from omniscribe.plugins.state_backend import StateBackend


def _seed_artifact(
    api_client: TestClient,
    *,
    blob: bytes,
    content_type: str = "application/json",
) -> tuple[str, str]:
    backend = api_client.app.state.context.inject(StateBackend)  # type: ignore[attr-defined]
    artifact_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    asyncio.run(
        backend.put_artifact(
            id=artifact_id,
            token=token,
            owner_job_id="",
            content_type=content_type,
            blob=blob,
            ttl_seconds=3600,
        )
    )
    return artifact_id, token


def _seed_text_artifact(
    api_client: TestClient, pages: dict[str, str]
) -> tuple[str, str]:
    return _seed_artifact(
        api_client,
        blob=json.dumps(pages).encode("utf-8"),
        content_type="application/json",
    )


def test_markdown_and_chunks_routes_mounted_in_openapi(api_client: TestClient) -> None:
    paths = set(api_client.get("/openapi.json").json()["paths"])
    assert "/api/export/markdown" in paths
    assert "/api/export/chunks" in paths


def test_export_markdown_post_success(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client,
        {
            "0": "CHAPTER 1: INTRODUCTION\nThis is the introductory text.",
            "1": "Next page paragraph.",
        },
    )

    response = api_client.post(
        "/api/export/markdown",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "document.md" in response.headers.get("content-disposition", "")
    content = response.text
    assert "CHAPTER 1: INTRODUCTION" in content
    assert "<!-- PageBreak: 1 -->" in content


def test_export_markdown_get_success(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client,
        {"0": "SECTION ONE\nSome narrative body content."},
    )

    response = api_client.get(
        "/api/export/markdown",
        params={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "SECTION ONE" in response.text


def test_export_markdown_not_found(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/export/markdown",
        json={
            "text_artifact_id": "0" * 32,
            "text_artifact_token": "t" * 43,
        },
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"

    get_resp = api_client.get(
        "/api/export/markdown",
        params={
            "text_artifact_id": "0" * 32,
            "text_artifact_token": "t" * 43,
        },
    )
    assert get_resp.status_code == 404


def test_export_chunks_post_success(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client,
        {
            "0": "OVERVIEW SECTION\nFirst block text that introduces the topic.\nSecond block text.",
            "1": "Second page content block.",
        },
    )

    response = api_client.post(
        "/api/export/chunks",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "max_chars": 500,
            "overlap_chars": 50,
            "min_chars": 100,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "chunks" in data
    assert "total_chunks" in data
    assert data["total_chunks"] == len(data["chunks"])
    assert data["total_chunks"] > 0

    first_chunk = data["chunks"][0]
    assert "chunk_id" in first_chunk
    assert "element_type" in first_chunk
    assert "text" in first_chunk
    assert "section_path" in first_chunk
    assert "page_span" in first_chunk
    assert "bbox" in first_chunk
    assert "block_ids" in first_chunk


def test_export_chunks_get_success(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client,
        {"0": "TITLE HEADER\nParagraph text line 1.\nParagraph text line 2."},
    )

    response = api_client.get(
        "/api/export/chunks",
        params={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "max_chars": 800,
            "overlap_chars": 80,
            "min_chars": 150,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_chunks"] > 0
    assert len(data["chunks"]) == data["total_chunks"]


def test_export_chunks_knob_validation(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client,
        {"0": "Some sample text content for validation."},
    )

    # overlap_chars >= max_chars
    response = api_client.post(
        "/api/export/chunks",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "max_chars": 100,
            "overlap_chars": 100,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"

    # min_chars > max_chars
    get_resp = api_client.get(
        "/api/export/chunks",
        params={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "max_chars": 100,
            "min_chars": 200,
        },
    )
    assert get_resp.status_code == 400
    assert get_resp.json()["error"] == "bad_request"


def test_routes_not_captured_by_parameterized_export_route(api_client: TestClient) -> None:
    """Ensure GET /api/export/markdown and GET /api/export/chunks are not captured
    by GET /api/export/{artifact_id} (which requires Bearer token and returns 403).
    """
    # Calling without query params on GET /api/export/markdown returns 422 validation error
    # (FastAPI query param validation), NOT 403 forbidden from {artifact_id}
    resp_md = api_client.get("/api/export/markdown")
    assert resp_md.status_code == 422

    resp_chunks = api_client.get("/api/export/chunks")
    assert resp_chunks.status_code == 422
