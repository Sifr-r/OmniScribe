"""Router contract tests for the documents plugin export family.

Contract source: the Flutter client (`feature_repository.dart`,
`api_constants.dart`, `feature_models.dart`) plus the recovered
pre-harness tests (commit `e6b7b89^`).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from omniscribe.plugins.artifacts import ArtifactHandle
from omniscribe.plugins.documents import routes as documents_routes
from omniscribe.plugins.documents.schemas import (
    DocumentExportFormat,
    DocumentExportRequest,
    ExportBlockTreeRequest,
    ExportHtmlRequest,
)
from omniscribe.plugins.documents.service import EXPORT_MEDIA_TYPES
from omniscribe.plugins.state_backend import StateBackend
from omniscribe.plugins.state_backend_types import ArtifactBlob, ArtifactRecord

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _seed_artifact(
    api_client: TestClient,
    *,
    blob: bytes,
    content_type: str = "application/json",
) -> tuple[str, str]:
    """Seed one artifact through the app's StateBackend (no events emitted)."""
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("route", ["text", "metadata", "export"])
def test_artifact_download_keeps_server_and_artifact_credentials_separate(
    cordis_env: None, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    from omniscribe.server import create_app

    monkeypatch.setenv("OMNISCRIBE_AUTH_TOKEN", "server-secret")
    with TestClient(create_app()) as client:
        artifact_id, token = _seed_text_artifact(client, {"0": "Recognized text"})
        url = f"/api/{route}/{artifact_id}"
        headers = {
            "Authorization": "Bearer server-secret",
            "X-Artifact-Token": token,
        }
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"0": "Recognized text"}
        assert client.get(url, headers={"X-Artifact-Token": token}).status_code == 401
        headers["X-Artifact-Token"] = "wrong-artifact-token"
        assert client.get(url, headers=headers).status_code == 404


def test_documents_plugin_is_mounted(api_client: TestClient) -> None:
    # FastAPI >= 0.141 keeps include_router() results wrapped in private
    # _IncludedRouter objects inside app.routes, so the stable public
    # surface for path assertions is the OpenAPI schema.
    paths = set(api_client.get("/openapi.json").json()["paths"])
    assert "/api/extract" in paths
    assert "/api/export/document" in paths
    assert "/api/export/docx" in paths
    assert "/api/export/html" in paths
    assert "/api/export/docx-tree" in paths
    assert "/api/export/blocktree" in paths
    assert "/api/text/{artifact_id}" in paths
    assert "/api/metadata/{artifact_id}" in paths
    assert "/api/export/{artifact_id}" in paths


@pytest.mark.parametrize(
    ("route", "extra"),
    [
        ("html", {}),
        ("docx-tree", {}),
        ("blocktree", {}),
        ("markdown", {}),
        ("chunks", {}),
        *[
            ("document", {"export_format": fmt})
            for fmt in ("json", "markdown", "text", "docling", "mineru")
        ],
    ],
)
@pytest.mark.parametrize("handle_parts", ["both", "id", "token"])
def test_unavailable_supplied_document_never_falls_back_to_valid_text(
    api_client: TestClient,
    route: str,
    extra: dict[str, str],
    handle_parts: str,
) -> None:
    text_id, text_token = _seed_text_artifact(api_client, {"0": "Legacy text survives"})
    document_id, document_token = _seed_artifact(api_client, blob=b"{}")
    assert isinstance(api_client.app, Starlette)
    backend = api_client.app.state.context.inject(StateBackend)
    asyncio.run(backend.delete_artifact(document_id))
    body = {
        "text_artifact_id": text_id,
        "text_artifact_token": text_token,
        **extra,
    }
    if handle_parts in {"both", "id"}:
        body["document_artifact_id"] = document_id
    if handle_parts in {"both", "token"}:
        body["document_artifact_token"] = document_token
    response = api_client.post(f"/api/export/{route}", json=body)
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_export_document_markdown_round_trip(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client, {"0": "hello\nworld", "1": "next"}
    )

    response = api_client.post(
        "/api/export/document",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "export_format": "markdown",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"artifact_id", "token", "format"}
    assert body["format"] == "markdown"
    assert len(body["artifact_id"]) == 32

    fetched = api_client.get(
        f"/api/export/{body['artifact_id']}", headers=_bearer(body["token"])
    )
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("text/markdown")
    assert fetched.text.startswith("## Page 1\n\nhello\nworld")


def test_export_document_fetch_requires_bearer(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "hello"})
    created = api_client.post(
        "/api/export/document",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "export_format": "text",
        },
    ).json()

    no_token = api_client.get(f"/api/export/{created['artifact_id']}")
    assert no_token.status_code == 403
    assert no_token.json()["error"] == "forbidden"

    wrong_token = api_client.get(
        f"/api/export/{created['artifact_id']}", headers=_bearer("t" * 43)
    )
    assert wrong_token.status_code == 404
    assert wrong_token.json()["error"] == "not_found"


def test_export_document_unknown_artifact_404(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/export/document",
        json={
            "text_artifact_id": "0" * 32,
            "text_artifact_token": "t" * 43,
            "export_format": "json",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_export_document_json_payload_shape(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "a\nb"})

    response = api_client.post(
        "/api/export/document",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "export_format": "json",
        },
    )
    assert response.status_code == 200
    exported_id = response.json()["artifact_id"]
    exported_token = response.json()["token"]
    fetched = api_client.get(
        f"/api/export/{exported_id}", headers=_bearer(exported_token)
    )
    payload: dict[str, Any] = fetched.json()
    assert payload["pages"] == [{"page_index": 0, "lines": ["a", "b"], "text": "a\nb"}]
    assert payload["metadata"] is None


def test_export_document_with_metadata_artifact(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "a"})
    meta_id, meta_token = _seed_artifact(
        api_client, blob=json.dumps({"quality": "ok"}).encode("utf-8")
    )

    response = api_client.post(
        "/api/export/document",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "export_format": "json",
            "metadata_artifact_id": meta_id,
            "metadata_artifact_token": meta_token,
        },
    )
    assert response.status_code == 200
    exported_id = response.json()["artifact_id"]
    exported_token = response.json()["token"]
    payload = api_client.get(
        f"/api/export/{exported_id}", headers=_bearer(exported_token)
    ).json()
    assert payload["metadata"] == {"quality": "ok"}


def test_export_document_unknown_metadata_404(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "a"})
    response = api_client.post(
        "/api/export/document",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "export_format": "json",
            "metadata_artifact_id": "0" * 32,
            "metadata_artifact_token": "t" * 43,
        },
    )
    assert response.status_code == 404


def test_export_docx_post_inline_bytes(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/export/docx", json={"text": "# Title\n\nBody text."}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(DOCX_MEDIA_TYPE)
    assert "document.docx" in response.headers["content-disposition"]
    assert response.content[:2] == b"PK"


def test_export_docx_get_no_longer_returns_docx(api_client: TestClient) -> None:
    """Pedantic 2.1: the GET /api/export/docx variant that put
    document text in the query string is gone. With the GET handler
    removed, ``GET /api/export/docx`` falls through to the
    parametrized ``GET /api/export/{artifact_id}`` route and is
    rejected there (403 without a Bearer token). Crucially, the
    response body is **not** a DOCX stream — the text in the query
    string is never rendered into a document.
    """
    response = api_client.get("/api/export/docx", params={"text": "ignored"})
    # 403 from the artifact_id route's bearer check; 405 would also
    # be acceptable but FastAPI matches the parametrized path first.
    assert response.status_code in (403, 405)
    assert response.content[:2] != b"PK"  # never a DOCX zip header
    assert "wordprocessingml" not in response.headers.get("content-type", "")


def test_export_docx_empty_text_is_lenient(api_client: TestClient) -> None:
    response = api_client.post("/api/export/docx", json={"text": ""})
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


def test_export_docx_whitespace_only_text_emits_visible_placeholder(
    api_client: TestClient,
) -> None:
    """Empty / whitespace-only input must not produce a content-free DOCX.

    Without the placeholder guard, the markdown parser skips every line and
    Word opens the file as a blank page — indistinguishable from a
    successful export that "lost" the OCR text. Verify the placeholder
    text is actually present in the document XML.
    """
    import io
    import re
    import zipfile

    for payload in ("", "   ", "\n\n\n", "  \n  \n  "):
        response = api_client.post("/api/export/docx", json={"text": payload})
        assert response.status_code == 200
        assert response.content[:2] == b"PK"
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
        # The placeholder paragraph should be in the body and contain
        # an explanatory run with the "No text recognized" cue.
        texts = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", doc_xml)
        joined = "".join(texts)
        assert "No text recognized" in joined, (
            f"expected placeholder text for payload {payload!r}, got: {joined!r}"
        )


def test_get_text_artifact_token_semantics(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "hello\nworld"})

    ok = api_client.get(f"/api/text/{artifact_id}", headers=_bearer(token))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("application/json")
    assert ok.json() == {"0": "hello\nworld"}

    missing = api_client.get(f"/api/text/{artifact_id}")
    assert missing.status_code == 403
    assert missing.json()["error"] == "forbidden"

    unknown = api_client.get(f"/api/text/{'0' * 32}", headers=_bearer(token))
    assert unknown.status_code == 404


def test_get_metadata_artifact_token_semantics(api_client: TestClient) -> None:
    meta_id, meta_token = _seed_artifact(
        api_client, blob=json.dumps({"page_count": 1}).encode("utf-8")
    )

    ok = api_client.get(f"/api/metadata/{meta_id}", headers=_bearer(meta_token))
    assert ok.status_code == 200
    assert ok.json() == {"page_count": 1}

    missing = api_client.get(f"/api/metadata/{meta_id}")
    assert missing.status_code == 403


def test_validation_rejects_malformed_artifact_ids(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/export/document",
        json={
            "text_artifact_id": "short",
            "text_artifact_token": "t" * 43,
            "export_format": "json",
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tree routes (html / docx-tree / blocktree)
# ---------------------------------------------------------------------------


def test_export_html_renders_block_text(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client, {"0": "Section heading\nFirst paragraph of body text."}
    )
    response = api_client.post(
        "/api/export/html",
        json={"text_artifact_id": artifact_id, "text_artifact_token": token},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "document.html" in response.headers["content-disposition"]
    assert "Section heading" in response.text
    assert "First paragraph of body text." in response.text


def test_export_html_unknown_artifact_404(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/export/html",
        json={"text_artifact_id": "0" * 32, "text_artifact_token": "t" * 43},
    )
    assert response.status_code == 404


def test_export_docx_tree_produces_docx(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "Heading\nBody line."})
    response = api_client.post(
        "/api/export/docx-tree",
        json={"text_artifact_id": artifact_id, "text_artifact_token": token},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(DOCX_MEDIA_TYPE)
    assert "document.docx" in response.headers["content-disposition"]
    assert response.content[:2] == b"PK"


def test_export_blocktree_returns_tree_json(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(
        api_client, {"0": "Section heading\nBody line.", "1": "More text."}
    )
    response = api_client.post(
        "/api/export/blocktree",
        json={"text_artifact_id": artifact_id, "text_artifact_token": token},
    )
    assert response.status_code == 200
    payload = response.json()
    # DocumentTree serializes with page children; both pages must appear.
    serialized = json.dumps(payload)
    assert "More text." in serialized
    assert "Section heading" in serialized


def test_export_blocktree_attaches_metadata_processor_report(
    api_client: TestClient,
) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "Body."})
    meta_id, meta_token = _seed_artifact(
        api_client, blob=json.dumps({"structure": {"blocks": 1}}).encode("utf-8")
    )
    response = api_client.post(
        "/api/export/blocktree",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "metadata_artifact_id": meta_id,
            "metadata_artifact_token": meta_token,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    report = payload.get("metadata", {}).get("processor_report")
    assert report is not None, f"processor_report missing: {payload.keys()}"
    assert report["structure"] == {"blocks": 1}


def test_export_blocktree_wrong_text_token_404(api_client: TestClient) -> None:
    artifact_id, _token = _seed_text_artifact(api_client, {"0": "Body."})
    response = api_client.post(
        "/api/export/blocktree",
        json={"text_artifact_id": artifact_id, "text_artifact_token": "t" * 43},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_export_blocktree_unknown_metadata_404(api_client: TestClient) -> None:
    artifact_id, token = _seed_text_artifact(api_client, {"0": "Body."})
    response = api_client.post(
        "/api/export/blocktree",
        json={
            "text_artifact_id": artifact_id,
            "text_artifact_token": token,
            "metadata_artifact_id": "0" * 32,
            "metadata_artifact_token": "t" * 43,
        },
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Event-loop blocking: export rendering must run off the loop
# ---------------------------------------------------------------------------

# The blocking stand-in below must be much slower than the responsiveness
# probe, otherwise a loaded CI box could finish the "render" before the
# probe gets a chance to observe the loop.
_RENDER_BLOCK_SECONDS = 0.5
_PROBE_SECONDS = 0.02


class _StubArtifactStore:
    """Single-artifact :class:`ArtifactStore` for handler-level tests.

    The render tests drive the handler coroutines directly (not through
    ``TestClient``) so that the loop being measured is the loop the test
    itself runs on.
    """

    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    async def get(self, artifact_id: str, token: str) -> ArtifactBlob:
        return ArtifactBlob(
            record=ArtifactRecord(
                id=artifact_id,
                token=token,
                owner_job_id="",
                content_type="application/json",
                created_at=0.0,
                ttl_seconds=3600,
            ),
            blob=self._blob,
        )

    async def put(
        self,
        blob: bytes,
        *,
        content_type: str,
        owner_job_id: str,
        ttl_seconds: int | None = None,
    ) -> ArtifactHandle:
        raise AssertionError("export render must not write artifacts")

    async def delete(self, artifact_id: str) -> None:
        raise AssertionError("export render must not delete artifacts")


class _RecordingArtifactStore(_StubArtifactStore):
    """Store stub that captures the blob written by ``handle_document_export``."""

    def __init__(self, blob: bytes) -> None:
        super().__init__(blob)
        self.put_blob: bytes | None = None
        self.put_content_type: str | None = None

    async def put(
        self,
        blob: bytes,
        *,
        content_type: str,
        owner_job_id: str,
        ttl_seconds: int | None = None,
    ) -> ArtifactHandle:
        self.put_blob = blob
        self.put_content_type = content_type
        return ArtifactHandle(id="a" * 32, token="t" * 43)


class _FakeDocxStream:
    """Minimal ``io.BytesIO`` stand-in returned by the render stand-in."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def getvalue(self) -> bytes:
        return self._payload


def _blocking_renderer(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    result: Any,
    render_threads: list[int],
    entered: threading.Event,
) -> None:
    """Patch ``target`` with a synchronously blocking render stand-in.

    The stand-in burns wall-clock time without ever yielding, exactly as
    the real per-block renderers do. It records the thread it ran on and
    signals ``entered`` before blocking, so the caller can tell "the loop
    is free" apart from "the loop was stuck inside the render".
    """

    def _render(*_args: Any, **_kwargs: Any) -> Any:
        render_threads.append(threading.get_ident())
        entered.set()
        time.sleep(_RENDER_BLOCK_SECONDS)
        return result

    monkeypatch.setattr(documents_routes, target, _render)


async def _assert_runs_off_loop(
    handler: Callable[[], Coroutine[Any, Any, Any]],
) -> Any:
    """Await ``handler`` while proving it never blocked the event loop."""
    task = asyncio.create_task(handler())
    # Yields to the loop: the handler starts and reaches its render.
    await asyncio.sleep(0)
    await asyncio.sleep(_PROBE_SECONDS)
    # The render is still blocked, yet this probe ran on schedule — so
    # the loop was never held by it.
    assert not task.done(), (
        "export render finished before the probe sleep elapsed: the "
        "render helper is running inline on the event loop"
    )
    return await task


async def test_export_html_render_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/api/export/html`` must not render inline on the event loop."""
    render_threads: list[int] = []
    entered = threading.Event()
    _blocking_renderer(
        monkeypatch,
        "render_html",
        "<html>rendered off-loop</html>",
        render_threads,
        entered,
    )
    store = _StubArtifactStore(json.dumps({"0": "Heading\nBody."}).encode("utf-8"))
    body = ExportHtmlRequest(text_artifact_id="a" * 32, text_artifact_token="t" * 43)

    response = await _assert_runs_off_loop(
        lambda: documents_routes.handle_export_html(body, store)
    )

    assert entered.is_set()
    assert render_threads and render_threads[0] != threading.get_ident()
    # The response contract is untouched by the offload.
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.body == b"<html>rendered off-loop</html>"


async def test_export_docx_tree_render_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/api/export/docx-tree`` must not build the docx on the loop."""
    render_threads: list[int] = []
    entered = threading.Event()
    _blocking_renderer(
        monkeypatch,
        "convert_tree_to_docx",
        _FakeDocxStream(b"PK-off-loop-docx-bytes"),
        render_threads,
        entered,
    )
    store = _StubArtifactStore(json.dumps({"0": "Heading\nBody."}).encode("utf-8"))
    body = ExportBlockTreeRequest(
        text_artifact_id="a" * 32, text_artifact_token="t" * 43
    )

    response = await _assert_runs_off_loop(
        lambda: documents_routes.handle_export_docx_tree(body, store)
    )

    assert entered.is_set()
    assert render_threads and render_threads[0] != threading.get_ident()
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert response.body == b"PK-off-loop-docx-bytes"


async def test_export_document_build_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/api/export/document`` must not build the export on the loop.

    ``build_document_export`` renders Markdown / base64-encodes figures
    and the payload is re-serialized into the stored artifact blob, so
    the whole build must run off-loop.
    """
    render_threads: list[int] = []
    entered = threading.Event()
    _blocking_renderer(
        monkeypatch,
        "build_document_export",
        {"pages": []},
        render_threads,
        entered,
    )
    store = _RecordingArtifactStore(json.dumps({"0": "Heading\nBody."}).encode("utf-8"))
    body = DocumentExportRequest(
        text_artifact_id="a" * 32,
        text_artifact_token="t" * 43,
        export_format=DocumentExportFormat.MARKDOWN,
    )

    result = await _assert_runs_off_loop(
        lambda: documents_routes.handle_document_export(body, store)
    )

    assert entered.is_set()
    assert render_threads and render_threads[0] != threading.get_ident()
    assert isinstance(result, dict)
    assert result == {"artifact_id": "a" * 32, "token": "t" * 43, "format": "markdown"}
    # The bytes handed to the store are unchanged by the offload.
    assert store.put_blob == b'{"pages": []}'
    assert store.put_content_type == EXPORT_MEDIA_TYPES["markdown"]
