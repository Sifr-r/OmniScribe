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

from omniscribe.config import RuntimeSettings
from omniscribe.core.errors import ChunkingError
from omniscribe.core.writers.docx import convert_markdown_to_docx
from omniscribe.core.writers.docx_tree import convert_tree_to_docx
from omniscribe.core.writers.html import render_html
from omniscribe.core.writers.tree_json import export_json
from omniscribe.harness.context import Context
from omniscribe.plugins._http import envelope, extract_token
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.documents.artifact import (
    load_document_result,
    load_document_tree,
)
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


def _parse_json_object(blob: bytes) -> dict[str, Any] | None:
    """Parse ``blob`` as JSON, returning the decoded dict or ``None``.

    Used by the export handlers to decode artifact blobs: a JSON decoding
    failure or a top-level non-dict payload is treated the same as a
    missing artifact (the caller cannot do anything useful with it), so
    we surface a uniform ``None`` and let the caller issue a 404.
    """
    try:
        parsed = json.loads(blob)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _docx_response(text: str) -> Response:
    """Convert ``text`` (Markdown) to a ``.docx`` download ``Response``.

    Used by the POST ``/api/export/docx`` route. The body is set with a
    ``Content-Disposition: attachment`` so browsers trigger a download
    rather than inline rendering.
    """
    stream = convert_markdown_to_docx(text)
    return Response(
        content=stream.getvalue(),
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="document.docx"'},
    )


async def _load_tree_or_none(
    store: ArtifactStore,
    artifact_id: str,
    token: str,
    *,
    document_artifact_id: str | None = None,
    document_artifact_token: str | None = None,
) -> Any:
    """Load a text artifact and parse it into a block tree, or ``None``.

    Combines the artifact fetch + JSON decode + tree construction used
    by every export route that operates on a stored block tree. Returning
    ``None`` on any failure (missing, malformed, non-JSON) lets the route
    emit a single 404 path instead of branching on each failure mode.

    When ``document_artifact_id``/``document_artifact_token`` are supplied the
    rich stored :class:`DocumentResult` wins, so geometry, block kinds,
    section paths and trust scores reach the export instead of being
    reconstructed from page text with zero bboxes. Only an omitted rich
    handle permits legacy text reconstruction; an unavailable supplied
    handle must fail visibly instead of degrading the export.
    """
    if document_artifact_id or document_artifact_token:
        if not document_artifact_id or not document_artifact_token:
            return None
        return await load_document_tree(
            store, document_artifact_id, document_artifact_token
        )

    blob = await store.get(artifact_id, token)
    if blob is None:
        return None
    raw = _parse_json_object(blob.blob)
    if raw is None:
        return None
    return build_tree(load_pages(raw))


async def handle_extract(
    body: ExtractionRequest,
    settings: RuntimeSettings,
) -> dict[str, Any] | JSONResponse:
    """Run structured extraction against an OCR text blob.

    Validates that ``body.text`` is non-empty (returns 400 otherwise) and
    delegates to :func:`run_extraction`. Domain errors propagate
    so the FastAPI exception handler can map them to the standard error
    envelope; any other exception is the service's responsibility.
    """
    if not body.text.strip():
        return envelope(400, "bad_request", "'text' is required")
    extracted = await run_extraction(body, settings)
    return {"extracted_data": extracted}


async def handle_document_export(
    body: DocumentExportRequest,
    store: ArtifactStore,
) -> dict[str, Any] | JSONResponse:
    """Create a token-bound export artifact (JSON/MD/text/Docling/MinerU).

    Loads the text artifact (and optional metadata artifact) referenced
    by the request, builds the requested export format, and writes the
    resulting blob back to the artifact store. The response carries the
    new artifact's id+token so the caller can fetch it via
    ``GET /api/export/{artifact_id}``.
    """
    text_blob = await store.get(body.text_artifact_id, body.text_artifact_token)
    if text_blob is None:
        return envelope(404, "not_found", "Export input not found")
    metadata: dict[str, Any] | None = None
    if body.metadata_artifact_id and body.metadata_artifact_token:
        meta_blob = await store.get(
            body.metadata_artifact_id, body.metadata_artifact_token
        )
        if meta_blob is None:
            return envelope(404, "not_found", "Export input not found")
        metadata = _parse_json_object(meta_blob.blob)
        if metadata is None:
            return envelope(404, "not_found", "Export input not found")

    raw = _parse_json_object(text_blob.blob)
    if raw is None:
        return envelope(404, "not_found", "Export input not found")

    # Prefer the rich stored document so JSON/Docling/MinerU exports carry
    # real geometry, block kinds, section paths and trust scores instead of
    # a structure re-guessed from page text. Absent handle -> legacy path.
    document = None
    if body.document_artifact_id or body.document_artifact_token:
        if not body.document_artifact_id or not body.document_artifact_token:
            return envelope(404, "not_found", "Export input not found")
        document = await load_document_result(
            store, body.document_artifact_id, body.document_artifact_token
        )
        if document is None:
            return envelope(404, "not_found", "Export input not found")

    payload = build_document_export(
        page_text=load_pages(raw),
        metadata=metadata,
        export_format=body.export_format.value,
        document=document,
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


async def handle_export_docx_post(body: ExportDocxRequest) -> Response:
    """Direct markdown→docx conversion without artifact round-trip.

    This is the simple-path variant: the caller already holds the
    document text in memory (or wants to bypass the artifact store)
    and just wants a one-shot ``.docx`` download.
    """
    return _docx_response(body.text)


async def handle_export_html(
    body: ExportHtmlRequest,
    store: ArtifactStore,
) -> Response | JSONResponse:
    """Render the stored block tree as a self-contained HTML document.

    Loads the block tree from the artifact store, renders it via
    :func:`render_html`, and returns it as an HTML attachment.
    """
    tree = await _load_tree_or_none(
        store,
        body.text_artifact_id,
        body.text_artifact_token,
        document_artifact_id=body.document_artifact_id,
        document_artifact_token=body.document_artifact_token,
    )
    if tree is None:
        return envelope(404, "not_found", "text artifact not found")
    return Response(
        content=render_html(tree),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="document.html"'},
    )


async def handle_export_docx_tree(
    body: ExportBlockTreeRequest,
    store: ArtifactStore,
) -> Response | JSONResponse:
    """Convert the stored block tree to a ``.docx`` preserving structure.

    Uses :func:`convert_tree_to_docx` (block-tree→docx) rather than the
    naive markdown→docx path: headings, lists, and tables from the tree
    map to native Word constructs instead of markdown syntax.
    """
    tree = await _load_tree_or_none(
        store,
        body.text_artifact_id,
        body.text_artifact_token,
        document_artifact_id=body.document_artifact_id,
        document_artifact_token=body.document_artifact_token,
    )
    if tree is None:
        return envelope(404, "not_found", "text artifact not found")
    stream = convert_tree_to_docx(tree)
    return Response(
        content=stream.getvalue(),
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="document.docx"'},
    )


async def handle_export_blocktree(
    body: ExportBlockTreeRequest,
    store: ArtifactStore,
) -> JSONResponse:
    """Return the block tree as a JSON payload (no file download).

    Optionally folds the processor-report metadata artifact into
    ``tree.metadata['processor_report']`` before serialization so the
    client gets structure + provenance in a single round-trip.
    """
    tree = await _load_tree_or_none(
        store,
        body.text_artifact_id,
        body.text_artifact_token,
        document_artifact_id=body.document_artifact_id,
        document_artifact_token=body.document_artifact_token,
    )
    if tree is None:
        return envelope(404, "not_found", "text artifact not found")
    if body.metadata_artifact_id and body.metadata_artifact_token:
        meta_blob = await store.get(
            body.metadata_artifact_id, body.metadata_artifact_token
        )
        metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
        if metadata is None:
            return envelope(404, "not_found", "metadata artifact not found")
        tree.metadata["processor_report"] = metadata
    return JSONResponse(content=json.loads(export_json(tree)))


async def handle_export_markdown_post(
    body: ExportMarkdownRequest,
    store: ArtifactStore,
) -> Response | JSONResponse:
    """Build a Markdown export from the stored block tree (POST variant).

    The POST variant is used when the caller wants to attach an optional
    metadata artifact alongside the text artifact; the metadata, when
    present, is folded into ``tree.metadata['processor_report']`` so
    downstream consumers can attribute OCR quality findings to sections.
    """
    tree = await _load_tree_or_none(
        store,
        body.text_artifact_id,
        body.text_artifact_token,
        document_artifact_id=body.document_artifact_id,
        document_artifact_token=body.document_artifact_token,
    )
    if tree is None:
        return envelope(404, "not_found", "text artifact not found")
    if body.metadata_artifact_id and body.metadata_artifact_token:
        meta_blob = await store.get(
            body.metadata_artifact_id, body.metadata_artifact_token
        )
        metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
        if metadata is None:
            return envelope(404, "not_found", "metadata artifact not found")
        tree.metadata["processor_report"] = metadata
    content = build_markdown_export(tree)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="document.md"'},
    )


async def handle_export_markdown_get(
    text_artifact_id: str,
    text_artifact_token: str | None,
    metadata_artifact_id: str | None,
    metadata_artifact_token: str | None,
    store: ArtifactStore,
) -> Response | JSONResponse:
    """Build a Markdown export from the stored block tree (GET variant).

    Functionally identical to :func:`handle_export_markdown_post`; kept
    as a separate handler so that link-only clients (browser preview,
    curl) can fetch markdown directly from an artifact id+token without
    building a request body.
    """
    if not text_artifact_token or not text_artifact_token.strip():
        return envelope(401, "unauthorized", "text artifact token required")
    tree = await _load_tree_or_none(
        store, text_artifact_id, text_artifact_token.strip()
    )
    if tree is None:
        return envelope(404, "not_found", "text artifact not found")
    if metadata_artifact_id:
        if not metadata_artifact_token or not metadata_artifact_token.strip():
            return envelope(401, "unauthorized", "metadata artifact token required")
        meta_blob = await store.get(
            metadata_artifact_id, metadata_artifact_token.strip()
        )
        metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
        if metadata is None:
            return envelope(404, "not_found", "metadata artifact not found")
        tree.metadata["processor_report"] = metadata
    content = build_markdown_export(tree)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="document.md"'},
    )


async def handle_export_chunks_post(
    body: ExportChunksRequest,
    store: ArtifactStore,
) -> JSONResponse:
    """Slice the block tree into overlapping chunks for downstream LLM use.

    POST variant; the body carries the chunk-size parameters. A
    :class:`ChunkingError` from the underlying chunker is mapped to a
    400 with the chunker's message — invalid parameter combinations
    surface here, not as a 500.
    """
    tree = await _load_tree_or_none(
        store,
        body.text_artifact_id,
        body.text_artifact_token,
        document_artifact_id=body.document_artifact_id,
        document_artifact_token=body.document_artifact_token,
    )
    if tree is None:
        return envelope(404, "not_found", "text artifact not found")
    if body.metadata_artifact_id and body.metadata_artifact_token:
        meta_blob = await store.get(
            body.metadata_artifact_id, body.metadata_artifact_token
        )
        metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
        if metadata is None:
            return envelope(404, "not_found", "metadata artifact not found")
        tree.metadata["processor_report"] = metadata
    try:
        chunks = build_chunks_export(
            tree,
            max_chars=body.max_chars,
            overlap_chars=body.overlap_chars,
            min_chars=body.min_chars,
        )
    except ChunkingError as exc:
        return envelope(400, "bad_request", str(exc))
    return JSONResponse(content={"chunks": chunks, "total_chunks": len(chunks)})


async def handle_export_chunks_get(
    text_artifact_id: str,
    text_artifact_token: str | None,
    metadata_artifact_id: str | None,
    metadata_artifact_token: str | None,
    max_chars: int,
    overlap_chars: int,
    min_chars: int,
    store: ArtifactStore,
) -> JSONResponse:
    """Slice the block tree into overlapping chunks (GET variant).

    Same shape as :func:`handle_export_chunks_post` but with chunk-size
    parameters as query-string ints. Defaults (1200/120/200) match the
    POST defaults so the two paths are interchangeable.
    """
    if not text_artifact_token or not text_artifact_token.strip():
        return envelope(401, "unauthorized", "text artifact token required")
    tree = await _load_tree_or_none(
        store, text_artifact_id, text_artifact_token.strip()
    )
    if tree is None:
        return envelope(404, "not_found", "text artifact not found")
    if metadata_artifact_id:
        if not metadata_artifact_token or not metadata_artifact_token.strip():
            return envelope(401, "unauthorized", "metadata artifact token required")
        meta_blob = await store.get(
            metadata_artifact_id, metadata_artifact_token.strip()
        )
        metadata = None if meta_blob is None else _parse_json_object(meta_blob.blob)
        if metadata is None:
            return envelope(404, "not_found", "metadata artifact not found")
        tree.metadata["processor_report"] = metadata
    try:
        chunks = build_chunks_export(
            tree,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            min_chars=min_chars,
        )
    except ChunkingError as exc:
        return envelope(400, "bad_request", str(exc))
    return JSONResponse(content={"chunks": chunks, "total_chunks": len(chunks)})


async def handle_get_document_export(
    artifact_id: str,
    authorization: str | None,
    store: ArtifactStore,
    artifact_token: str | None = None,
) -> Response | JSONResponse:
    """Fetch a previously-created export artifact by id+token.

    Bearer-token gated: the client must present the same token that was
    returned at export time, so a leaked artifact id alone is not
    sufficient to download the file. Content-type is preserved from
    the original blob record.
    """
    token = extract_token(token=artifact_token, authorization=authorization)
    if not token:
        return envelope(403, "forbidden", "Export access denied")
    blob = await store.get(artifact_id, token)
    if blob is None:
        return envelope(404, "not_found", "Export not found")
    return Response(content=blob.blob, media_type=blob.record.content_type)


async def handle_get_text(
    artifact_id: str,
    authorization: str | None,
    store: ArtifactStore,
    artifact_token: str | None = None,
) -> Response | JSONResponse:
    """Fetch a stored OCR text artifact by id+token.

    Bearer-token gated, same pattern as the export fetch. Returns the
    blob as ``application/json`` because text artifacts are always
    serialized JSON (see ``build_text_artifact`` in the OCR routes).
    """
    token = extract_token(token=artifact_token, authorization=authorization)
    if not token:
        return envelope(403, "forbidden", "Text access denied")
    blob = await store.get(artifact_id, token)
    if blob is None:
        return envelope(404, "not_found", "Text not found")
    return Response(content=blob.blob, media_type="application/json")


async def handle_get_document_metadata(
    artifact_id: str,
    authorization: str | None,
    store: ArtifactStore,
    artifact_token: str | None = None,
) -> Response | JSONResponse:
    """Fetch the per-page OCR metadata artifact by id+token.

    Bearer-token gated. The metadata artifact carries the processor
    report (quality findings, layout enrichment, etc.); clients use it
    to surface warnings or to fold provenance into downstream exports.
    """
    token = extract_token(token=artifact_token, authorization=authorization)
    if not token:
        return envelope(403, "forbidden", "Document metadata access denied")
    blob = await store.get(artifact_id, token)
    if blob is None:
        return envelope(404, "not_found", "Document metadata not found")
    return Response(content=blob.blob, media_type="application/json")


def build_documents_router(ctx: Context) -> APIRouter:
    """Build and return the FastAPI router for the documents plugin.

    Resolves the artifact store and runtime settings from the harness
    context once at router-construction time so the per-route closures
    can stay tiny — every handler closure just forwards to its
    module-level async helper.

    The route declaration order is load-bearing: every concrete
    ``/api/export/<name>`` route (POST docx, GET markdown, GET chunks,
    etc.) is registered before the parametrized
    ``GET /api/export/{artifact_id}`` so the path parameter does not
    swallow the literal names. See the module-level docstring for the
    full ordering rationale.
    """
    router = APIRouter(tags=["documents"])
    store = ctx.inject(ArtifactStore)
    settings = ctx.inject(RuntimeService).settings

    @router.post("/api/extract", response_model=None)
    async def extract(body: ExtractionRequest) -> dict[str, Any] | JSONResponse:
        return await handle_extract(body, settings)

    @router.post("/api/export/document", response_model=None)
    async def create_document_export(
        body: DocumentExportRequest,
    ) -> dict[str, Any] | JSONResponse:
        return await handle_document_export(body, store)

    @router.post("/api/export/docx")
    async def export_docx_post(body: ExportDocxRequest) -> Response:
        return await handle_export_docx_post(body)

    @router.post("/api/export/html", response_model=None)
    async def export_html(body: ExportHtmlRequest) -> Response | JSONResponse:
        return await handle_export_html(body, store)

    @router.post("/api/export/docx-tree", response_model=None)
    async def export_docx_tree(
        body: ExportBlockTreeRequest,
    ) -> Response | JSONResponse:
        return await handle_export_docx_tree(body, store)

    @router.post("/api/export/blocktree")
    async def export_blocktree(body: ExportBlockTreeRequest) -> JSONResponse:
        return await handle_export_blocktree(body, store)

    @router.post("/api/export/markdown", response_model=None)
    async def export_markdown_post(
        body: ExportMarkdownRequest,
    ) -> Response | JSONResponse:
        return await handle_export_markdown_post(body, store)

    @router.get("/api/export/markdown", response_model=None)
    async def export_markdown_get(
        text_artifact_id: str,
        metadata_artifact_id: str | None = None,
        x_artifact_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        x_metadata_artifact_token: str | None = Header(
            default=None,
            alias="X-Metadata-Artifact-Token",
            min_length=32,
            max_length=256,
        ),
    ) -> Response | JSONResponse:
        token = extract_token(
            x_artifact_token=x_artifact_token,
            authorization=authorization,
        )
        return await handle_export_markdown_get(
            text_artifact_id,
            token,
            metadata_artifact_id,
            x_metadata_artifact_token,
            store,
        )

    @router.post("/api/export/chunks", response_model=None)
    async def export_chunks_post(
        body: ExportChunksRequest,
    ) -> JSONResponse:
        return await handle_export_chunks_post(body, store)

    @router.get("/api/export/chunks", response_model=None)
    async def export_chunks_get(
        text_artifact_id: str,
        metadata_artifact_id: str | None = None,
        max_chars: int = 1200,
        overlap_chars: int = 120,
        min_chars: int = 200,
        x_artifact_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
        x_metadata_artifact_token: str | None = Header(
            default=None,
            alias="X-Metadata-Artifact-Token",
            min_length=32,
            max_length=256,
        ),
    ) -> JSONResponse:
        token = extract_token(
            x_artifact_token=x_artifact_token,
            authorization=authorization,
        )
        return await handle_export_chunks_get(
            text_artifact_id,
            token,
            metadata_artifact_id,
            x_metadata_artifact_token,
            max_chars,
            overlap_chars,
            min_chars,
            store,
        )

    @router.get("/api/export/{artifact_id}", response_model=None)
    async def get_document_export(
        artifact_id: str,
        authorization: str | None = Header(default=None),
        x_artifact_token: str | None = Header(default=None),
    ) -> Response | JSONResponse:
        return await handle_get_document_export(
            artifact_id, authorization, store, x_artifact_token
        )

    @router.get("/api/text/{artifact_id}", response_model=None)
    async def get_text(
        artifact_id: str,
        authorization: str | None = Header(default=None),
        x_artifact_token: str | None = Header(default=None),
    ) -> Response | JSONResponse:
        return await handle_get_text(
            artifact_id, authorization, store, x_artifact_token
        )

    @router.get("/api/metadata/{artifact_id}", response_model=None)
    async def get_document_metadata(
        artifact_id: str,
        authorization: str | None = Header(default=None),
        x_artifact_token: str | None = Header(default=None),
    ) -> Response | JSONResponse:
        return await handle_get_document_metadata(
            artifact_id, authorization, store, x_artifact_token
        )

    return router
