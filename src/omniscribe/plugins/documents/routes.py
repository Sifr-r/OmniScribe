"""HTTP routes for the documents plugin.

Route declaration order matters: every concrete ``/api/export/<name>``
route is declared BEFORE the parametrized ``GET /api/export/{artifact_id}``
fetch route so ``GET /api/export/docx`` is not captured by the path
parameter.

Routes whose handler may answer with the error envelope declare a union
return type (payload or ``JSONResponse``). FastAPI cannot build a
response model from such unions, so those decorators pass
``response_model=None`` — the handler's return value is used as-is, and
``JSONResponse`` instances already bypass response-model serialization.

Pedantic review 2.1: ``/api/export/docx`` is POST-only. The previous
GET-with-text-query-parameter variant put the entire document body in
the URL — uvicorn access logs, reverse-proxy logs, browser history,
and the Referer header all leaked it. The Flutter client was updated
in lockstep (``feature_repository.dart::exportDocx``) to POST the
text in the request body.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse

from omniscribe.core.errors import ChunkingError
from omniscribe.core.writers.docx import convert_markdown_to_docx
from omniscribe.core.writers.docx_tree import convert_tree_to_docx
from omniscribe.core.writers.html import render_html
from omniscribe.core.writers.tree_json import export_json
from omniscribe.harness.context import Context
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.documents.schemas import (
    DocumentExportRequest,
    ExportBlockTreeRequest,
    ExportChunksRequest,
    ExportDocxRequest,
    ExportHtmlRequest,
    ExportMarkdownRequest,
    ExtractionRequest,
)
from omniscribe.plugins.documents.service import (
    EXPORT_MEDIA_TYPES,
    DocumentsError,
    build_chunks_export,
    build_document_export,
    build_markdown_export,
    build_tree,
    load_pages,
    run_extraction,
)
from omniscribe.plugins.runtime import RuntimeService

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _envelope(status_code: int, error: str, detail: str) -> JSONResponse:
    """Stable error envelope the Flutter client parses (``error`` + ``detail``)."""
    return JSONResponse(
        status_code=status_code, content={"error": error, "detail": detail}
    )


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


def build_documents_router(ctx: Context) -> APIRouter:
    router = APIRouter(tags=["documents"])
    store = ctx.inject(ArtifactStore)
    settings = ctx.inject(RuntimeService).settings

    @router.post("/api/extract", response_model=None)
    async def extract(body: ExtractionRequest) -> dict[str, Any] | JSONResponse:
        if not body.text.strip():
            return _envelope(400, "bad_request", "'text' is required")
        try:
            extracted = await run_extraction(body, settings)
        except DocumentsError as exc:
            return _envelope(exc.status_code, exc.error, exc.detail)
        return {"extracted_data": extracted}

    @router.post("/api/export/document", response_model=None)
    async def create_document_export(
        body: DocumentExportRequest,
    ) -> dict[str, Any] | JSONResponse:
        text_blob = await store.get(body.text_artifact_id, body.text_artifact_token)
        if text_blob is None:
            return _envelope(404, "not_found", "Export input not found")
        metadata: dict[str, Any] | None = None
        if body.metadata_artifact_id and body.metadata_artifact_token:
            meta_blob = await store.get(
                body.metadata_artifact_id, body.metadata_artifact_token
            )
            if meta_blob is None:
                return _envelope(404, "not_found", "Export input not found")
            metadata = _parse_json_object(meta_blob.blob)
            if metadata is None:
                return _envelope(404, "not_found", "Export input not found")

        raw = _parse_json_object(text_blob.blob)
        if raw is None:
            return _envelope(404, "not_found", "Export input not found")
        payload = build_document_export(
            page_text=load_pages(raw),
            metadata=metadata,
            export_format=body.export_format.value,
        )
        if isinstance(payload, dict):
            blob = json.dumps(payload).encode("utf-8")
        else:
            blob = payload.encode("utf-8")
        handle = await store.put(
            blob,
            content_type=EXPORT_MEDIA_TYPES[body.export_format.value],
            owner_job_id="",
        )
        return {
            "artifact_id": handle.id,
            "token": handle.token,
            "format": body.export_format.value,
        }

    def _docx_response(text: str) -> Response:
        stream = convert_markdown_to_docx(text)
        return Response(
            content=stream.getvalue(),
            media_type=DOCX_MEDIA_TYPE,
            headers={"Content-Disposition": 'attachment; filename="document.docx"'},
        )

    @router.post("/api/export/docx")
    async def export_docx_post(body: ExportDocxRequest) -> Response:
        return _docx_response(body.text)

    @router.post("/api/export/html", response_model=None)
    async def export_html(body: ExportHtmlRequest) -> Response | JSONResponse:
        tree = await _load_tree_or_none(body.text_artifact_id, body.text_artifact_token)
        if tree is None:
            return _envelope(404, "not_found", "text artifact not found")
        return Response(
            content=render_html(tree),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="document.html"'},
        )

    @router.post("/api/export/docx-tree", response_model=None)
    async def export_docx_tree(
        body: ExportBlockTreeRequest,
    ) -> Response | JSONResponse:
        tree = await _load_tree_or_none(body.text_artifact_id, body.text_artifact_token)
        if tree is None:
            return _envelope(404, "not_found", "text artifact not found")
        stream = convert_tree_to_docx(tree)
        return Response(
            content=stream.getvalue(),
            media_type=DOCX_MEDIA_TYPE,
            headers={"Content-Disposition": 'attachment; filename="document.docx"'},
        )

    @router.post("/api/export/blocktree")
    async def export_blocktree(body: ExportBlockTreeRequest) -> JSONResponse:
        tree = await _load_tree_or_none(body.text_artifact_id, body.text_artifact_token)
        if tree is None:
            return _envelope(404, "not_found", "text artifact not found")
        if body.metadata_artifact_id and body.metadata_artifact_token:
            meta_blob = await store.get(
                body.metadata_artifact_id, body.metadata_artifact_token
            )
            metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
            if metadata is None:
                return _envelope(404, "not_found", "metadata artifact not found")
            tree.metadata["processor_report"] = metadata
        return JSONResponse(content=json.loads(export_json(tree)))

    @router.post("/api/export/markdown", response_model=None)
    async def export_markdown_post(
        body: ExportMarkdownRequest,
    ) -> Response | JSONResponse:
        tree = await _load_tree_or_none(body.text_artifact_id, body.text_artifact_token)
        if tree is None:
            return _envelope(404, "not_found", "text artifact not found")
        if body.metadata_artifact_id and body.metadata_artifact_token:
            meta_blob = await store.get(
                body.metadata_artifact_id, body.metadata_artifact_token
            )
            metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
            if metadata is None:
                return _envelope(404, "not_found", "metadata artifact not found")
            tree.metadata["processor_report"] = metadata
        content = build_markdown_export(tree)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="document.md"'},
        )

    @router.get("/api/export/markdown", response_model=None)
    async def export_markdown_get(
        text_artifact_id: str,
        text_artifact_token: str,
        metadata_artifact_id: str | None = None,
        metadata_artifact_token: str | None = None,
    ) -> Response | JSONResponse:
        tree = await _load_tree_or_none(text_artifact_id, text_artifact_token)
        if tree is None:
            return _envelope(404, "not_found", "text artifact not found")
        if metadata_artifact_id and metadata_artifact_token:
            meta_blob = await store.get(metadata_artifact_id, metadata_artifact_token)
            metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
            if metadata is None:
                return _envelope(404, "not_found", "metadata artifact not found")
            tree.metadata["processor_report"] = metadata
        content = build_markdown_export(tree)
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="document.md"'},
        )

    @router.post("/api/export/chunks", response_model=None)
    async def export_chunks_post(
        body: ExportChunksRequest,
    ) -> JSONResponse:
        tree = await _load_tree_or_none(body.text_artifact_id, body.text_artifact_token)
        if tree is None:
            return _envelope(404, "not_found", "text artifact not found")
        if body.metadata_artifact_id and body.metadata_artifact_token:
            meta_blob = await store.get(
                body.metadata_artifact_id, body.metadata_artifact_token
            )
            metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
            if metadata is None:
                return _envelope(404, "not_found", "metadata artifact not found")
            tree.metadata["processor_report"] = metadata
        try:
            chunks = build_chunks_export(
                tree,
                max_chars=body.max_chars,
                overlap_chars=body.overlap_chars,
                min_chars=body.min_chars,
            )
        except ChunkingError as exc:
            return _envelope(400, "bad_request", str(exc))
        return JSONResponse(content={"chunks": chunks, "total_chunks": len(chunks)})

    @router.get("/api/export/chunks", response_model=None)
    async def export_chunks_get(
        text_artifact_id: str,
        text_artifact_token: str,
        metadata_artifact_id: str | None = None,
        metadata_artifact_token: str | None = None,
        max_chars: int = 1200,
        overlap_chars: int = 120,
        min_chars: int = 200,
    ) -> JSONResponse:
        tree = await _load_tree_or_none(text_artifact_id, text_artifact_token)
        if tree is None:
            return _envelope(404, "not_found", "text artifact not found")
        if metadata_artifact_id and metadata_artifact_token:
            meta_blob = await store.get(metadata_artifact_id, metadata_artifact_token)
            metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
            if metadata is None:
                return _envelope(404, "not_found", "metadata artifact not found")
            tree.metadata["processor_report"] = metadata
        try:
            chunks = build_chunks_export(
                tree,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
                min_chars=min_chars,
            )
        except ChunkingError as exc:
            return _envelope(400, "bad_request", str(exc))
        return JSONResponse(content={"chunks": chunks, "total_chunks": len(chunks)})

    @router.get("/api/export/{artifact_id}", response_model=None)
    async def get_document_export(
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ) -> Response | JSONResponse:
        token = _bearer_token(authorization)
        if not token:
            return _envelope(403, "forbidden", "Export access denied")
        blob = await store.get(artifact_id, token)
        if blob is None:
            return _envelope(404, "not_found", "Export not found")
        return Response(content=blob.blob, media_type=blob.record.content_type)

    @router.get("/api/text/{artifact_id}", response_model=None)
    async def get_text(
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ) -> Response | JSONResponse:
        token = _bearer_token(authorization)
        if not token:
            return _envelope(403, "forbidden", "Text access denied")
        blob = await store.get(artifact_id, token)
        if blob is None:
            return _envelope(404, "not_found", "Text not found")
        return Response(content=blob.blob, media_type="application/json")

    @router.get("/api/metadata/{artifact_id}", response_model=None)
    async def get_document_metadata(
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ) -> Response | JSONResponse:
        token = _bearer_token(authorization)
        if not token:
            return _envelope(403, "forbidden", "Document metadata access denied")
        blob = await store.get(artifact_id, token)
        if blob is None:
            return _envelope(404, "not_found", "Document metadata not found")
        return Response(content=blob.blob, media_type="application/json")

    async def _load_tree_or_none(artifact_id: str, token: str) -> Any:
        blob = await store.get(artifact_id, token)
        if blob is None:
            return None
        raw = _parse_json_object(blob.blob)
        if raw is None:
            return None
        return build_tree(load_pages(raw))

    return router


def _parse_json_object(blob: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(blob)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
