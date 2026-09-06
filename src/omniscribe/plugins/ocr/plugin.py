"""OCR plugin — Protocol, route factory, plugin class.

Wraps :mod:`omniscribe.plugins.ocr.pipeline_bridge` behind an
:class:`OCRService` seam:

- ``POST /api/process`` — synchronous OCR; returns the searchable PDF blob
  with ``X-Text-Artifact-Id`` / ``X-Text-Artifact-Token`` headers.
- ``POST /api/process/async`` — enqueues onto the injected ``JobQueue`` and
  returns ``202`` + ``{job_id, status, status_url}``.
- Job status / list / clear / cancel / result download, SSE event stream,
  and the ``/api/config`` runtime config store (frontend ``ConfigResponse``
  shape — GET/POST, non-secret round-trip with LLM write-through).

The plugin also registers the :class:`JobRunner` the queue worker resolves
at claim time, and subscribes to the job/progress events so the SSE route
can replay them per job.

Audit catalog (Sprint 6 long-file split):
:file:`omniscribe.plugins.ocr.service` holds
``OCRServiceImpl`` + the SSE event-formatting helper + the
queue/event-name lookup tables. This file is just the
Protocol + plugin class + route factory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from omniscribe.core.workflows.base import OCRCancelled
from omniscribe.harness.context import Context
from omniscribe.harness.plugin import Plugin
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    JobCancelled,
    JobCompleted,
    JobFailed,
    JobQueue,
    JobQueued,
    JobRunner,
    JobStarted,
)
from omniscribe.plugins.ocr.schemas import (
    AsyncSubmitResponse,
    JobListItemResponse,
    JobStatusResponse,
    OCRRequest,
    PreflightRequest,
    PreflightResponse,
)
from omniscribe.plugins.progress import ProgressFrame, ProgressService

from .service import (
    SSE_KEEPALIVE_SECONDS,
    OCRServiceImpl,
)

_LOGGER = logging.getLogger("omniscribe.plugins.ocr")

_PREVIEW_DOC_CACHE_CAPACITY = 10
_preview_doc_cache: dict[str, tuple[bytes, str, float]] = {}


@runtime_checkable
class OCRService(Protocol):
    """Sync/async OCR execution seam over the core pipeline."""

    async def run_sync(
        self,
        options: OCRRequest,
        blob: bytes,
        filename: str,
        content_type: str | None = None,
    ) -> Response: ...

    async def submit(
        self,
        options: OCRRequest,
        blob: bytes,
        filename: str,
        content_type: str | None = None,
    ) -> AsyncSubmitResponse: ...

    async def get_page_preview(
        self,
        job_id: str,
        page_index: int,
        *,
        dpi: int = 150,
    ) -> bytes | None: ...


# -- routes -------------------------------------------------------------------


def _envelope(status_code: int, error: str, detail: str) -> JSONResponse:
    """Stable error envelope the Flutter client parses (matches the
    translate / transcribe / glossary plugins).
    """
    return JSONResponse(
        status_code=status_code, content={"error": error, "detail": detail}
    )


#: Document-format signatures the route sniffs out of an upload's first
#: 12 bytes. The keys are the format names that match the downstream
#: rasterizer / pipeline branch; the values are the head-byte predicates
#: checked in order. AVIF/HEIF uses the ISOBMFF ``ftyp`` box layout
#: (``ftyp`` at offset 4, brand at offset 8) which handles variable-sized
#: ftyp boxes correctly (pedantic review 1.7 & 1.8).
_SUPPORTED_FORMAT_SIGNATURES: tuple[tuple[str, Callable[[bytes], bool]], ...] = (
    ("pdf", lambda head: head.startswith(b"%PDF-")),
    ("png", lambda head: head.startswith(b"\x89PNG\r\n\x1a\n")),
    ("jpeg", lambda head: head[:3] == b"\xff\xd8\xff"),
    (
        "webp",
        lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
    ),
    (
        "avif",
        # ISOBMFF ftyp box: 4 bytes size, ``ftyp`` literal, 4 bytes major brand.
        # Recognised brands: avif, avis (AVIF image sequence), mif1 (HEIF).
        lambda head: (
            len(head) >= 12
            and head[4:8] == b"ftyp"
            and head[8:12] in {b"avif", b"avis", b"mif1"}
        ),
    ),
)

_MIME_TO_FORMAT: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/webp": "webp",
    "image/avif": "avif",
}


def _sniff_format(head: bytes) -> str | None:
    """Return the supported document format matching ``head``, or ``None``.

    The 12-byte window covers every supported signature; AVIF/HEIF needs
    the 4-byte size + ``ftyp`` literal + 4-byte brand to disambiguate
    from a coincidental ``\\x00\\x00\\x00\\x1c`` prefix. A 415 in the
    route is the natural fallback for the ``None`` case — the route
    refuses to write arbitrary bytes to the tempdir before the
    downstream parser gets a chance to reject them.
    """
    for name, predicate in _SUPPORTED_FORMAT_SIGNATURES:
        if predicate(head):
            return name
    return None


async def iter_sse_events(
    service: OCRServiceImpl, job_id: str, keepalive_seconds: float
) -> AsyncGenerator[str, None]:
    """Yield SSE frames for a job's events with a sequence-based cursor.

    Entries are stamped with per-job monotonic ``seq`` numbers by
    ``record_event``; the cursor tracks the last delivered ``seq`` so a
    ``maxlen`` deque rotation (which shifts list indices and evicts the
    oldest entries) can never skip or replay unseen events.
    """
    cursor = 0
    while True:
        for entry in service.event_backlog(job_id):
            seq = entry["seq"]
            if seq <= cursor:
                continue
            cursor = seq
            yield (f"event: {entry['event']}\ndata: {json.dumps(entry['data'])}\n\n")
        if service.is_done(job_id):
            return
        try:
            await asyncio.wait_for(
                service.wait_for_events(job_id),
                timeout=keepalive_seconds,
            )
        except TimeoutError:
            yield ": keep-alive\n\n"


def build_ocr_router(service: OCRServiceImpl) -> APIRouter:
    """Every OCR-plugin route from the spec's route table."""
    router = APIRouter(tags=["ocr"])

    async def _parse_upload(
        request: Request,
    ) -> tuple[OCRRequest, bytes, str, str]:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="missing 'file' field")
        cap = service.max_upload_mb * 1024 * 1024
        chunks: list[bytes] = []
        total_read = 0
        chunk_size = 1024 * 1024
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > cap:
                raise HTTPException(
                    status_code=413,
                    detail=f"upload exceeds {service.max_upload_mb} MB limit",
                )
            chunks.append(chunk)
        blob = b"".join(chunks)
        # H-5 audit fix: validate the upload's content type against
        # the allowlist. FastAPI's ``request.form()`` accepts the
        # multipart ``content_type`` field, which is the per-file
        # MIME type set by the client. We compare it to a
        # document-handler allowlist (PDF, PNG, JPEG, WebP, AVIF) and
        # reject anything else with 415.
        content_type = getattr(upload, "content_type", "") or ""
        allowed_types = {
            "application/pdf",
            "application/octet-stream",  # Flutter file picker fallback
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/avif",
        }
        if content_type and content_type not in allowed_types:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"unsupported content type: {content_type!r}. "
                    "Allowed: PDF, PNG, JPEG, WebP, AVIF."
                ),
            )
        # H-5 audit fix (continued) + pedantic review 1.7/1.8:
        # Magic-byte check so an attacker cannot bypass the filter.
        #
        # Two branches:
        #   * ``application/octet-stream``: the Flutter file picker
        #     fallback when the OS can't surface a MIME. The route
        #     sniffs the first 12 bytes via :func:`_sniff_format` and
        #     accepts the upload only if the sniff matches a supported
        #     format. Bytes that don't match any signature 415 *before*
        #     the tempdir is written.
        #   * Typed upload: declared MIME must match the sniffed format.
        head = blob[:12]
        if not content_type or content_type == "application/octet-stream":
            if _sniff_format(head) is None:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        "could not detect a supported document format from "
                        "the upload contents; octet-stream uploads must be "
                        "one of PDF, PNG, JPEG, WebP, or AVIF"
                    ),
                )
        elif content_type in _MIME_TO_FORMAT:
            sniffed = _sniff_format(head)
            if sniffed != _MIME_TO_FORMAT[content_type]:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"file contents do not match declared content type "
                        f"{content_type!r}"
                    ),
                )
        fields: dict[str, Any] = {
            key: value
            for key, value in form.items()
            if key != "file" and isinstance(value, str)
        }
        for key, value in service.quality_defaults.items():
            fields.setdefault(key, value)
        try:
            # model_validate (not **kwargs): form values are all strings and
            # the before-validators coerce them at runtime.
            options = OCRRequest.model_validate(fields)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        filename = str(getattr(upload, "filename", "") or "") or "upload.pdf"
        return options, blob, filename, content_type

    @router.post("/api/process")
    async def process_sync(request: Request) -> Response:
        options, blob, filename, content_type = await _parse_upload(request)
        try:
            return await service.run_sync(
                options, blob, filename, content_type=content_type
            )
        except OCRCancelled as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "cancelled": True,
                    "error": "cancelled",
                    "detail": str(exc) or "OCR run was cancelled before completion.",
                },
            )

    @router.post("/api/process/async", status_code=202)
    async def process_async(request: Request) -> AsyncSubmitResponse:
        options, blob, filename, content_type = await _parse_upload(request)
        return await service.submit(options, blob, filename, content_type=content_type)

    @router.get("/api/process/status/{job_id}")
    async def process_status(job_id: str) -> JobStatusResponse:
        status = await service.job_status(job_id)
        if status is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return status

    @router.get("/api/process/{job_id}/events")
    async def process_events(job_id: str) -> StreamingResponse:
        if await service.job_record(job_id) is None and not service.event_backlog(
            job_id
        ):
            raise HTTPException(status_code=404, detail="unknown job")

        return StreamingResponse(
            iter_sse_events(service, job_id, SSE_KEEPALIVE_SECONDS),
            media_type="text/event-stream",
        )

    @router.get("/api/jobs")
    async def list_jobs() -> list[JobListItemResponse]:
        # Audit finding S15 (doc): on the loopback dev profile (Profile 1)
        # this endpoint is unauthenticated. Any local process can enumerate
        # the job list (and from there, the per-job ``id``, the SSE
        # ``progress_channel``, and the ``progress_token`` exposed on
        # ``JobListItemResponse``). The result ``token`` is intentionally
        # NOT on this response (audit C-3 / H-3 fix in
        # ``plugins/ocr/schemas.py:145``), so a local attacker cannot
        # fetch another user's OCR'd PDF via the ``list → id → token →
        # /api/jobs/{id}/result`` chain — the constant-time gate at
        # ``fetch_result`` blocks it. They CAN, however, list the job
        # IDs and watch real-time progress via the SSE endpoint (also
        # unauthenticated on loopback per Profile 1). This is
        # documented and intentional: Profile 1 is a single-user,
        # trusted-environment default; the v0.2.0 install / dev loop
        # requires it. Operators on Profile 2 (LAN) or Profile 3
        # (public) must set ``OMNISCRIBE_AUTH_TOKEN`` (the Bearer
        # AuthMiddleware on Profile 2+ rejects the unauthenticated
        # ``GET /api/jobs`` request before this handler runs).
        records = await service.queue.list_jobs()
        return [service.job_list_item(record) for record in records]

    @router.delete("/api/jobs")
    async def clear_jobs(confirm: bool = Query(default=False)) -> Any:
        if not confirm:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "confirmation_required",
                    "detail": (
                        "DELETE /api/jobs requires confirm=true query parameter "
                        "to prevent accidental wipe"
                    ),
                },
            )
        cleared = await service.queue.clear()
        return {"status": "ok", "cleared": cleared}

    @router.get("/api/jobs/{job_id}/result")
    async def job_result(
        job_id: str,
        token: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> Response:
        bearer = token
        if not bearer and authorization and authorization.startswith("Bearer "):
            bearer = authorization.removeprefix("Bearer ").strip()
        return await service.fetch_result(job_id, bearer)

    @router.get("/api/jobs/{job_id}/pages/{page_index}/preview", response_model=None)
    async def page_preview(job_id: str, page_index: int) -> Response:
        """Render a page of the original upload as PNG bytes.

        Used by the workstation viewport to show the underlying page
        beneath the bounding-box / heatmap overlays. Returns 404 when
        the job has no recorded source path (e.g. submitted before this
        route landed, or whose source has already been cleaned up).
        """
        if page_index < 0:
            raise HTTPException(status_code=400, detail="page_index must be >= 0")
        png_bytes = await service.get_page_preview(job_id, page_index)
        if png_bytes is None:
            raise HTTPException(
                status_code=404,
                detail="page preview unavailable for this job",
            )
        return Response(content=png_bytes, media_type="image/png")

    @router.post("/api/documents/preview", response_model=None)
    async def document_page_preview(
        request: Request,
        page: int = 0,
        dpi: int = 150,
    ) -> Response:
        """Render any page of an uploaded document (PDF or image) as PNG bytes.

        Used by the workstation viewport to instantly display the page
        raster when a document is opened before or after processing.
        """
        form = await request.form()
        req_doc_id = (
            form.get("doc_id")
            or request.headers.get("X-Document-Id")
            or request.headers.get("x-document-id")
        )
        doc_id: str | None = str(req_doc_id).strip() if req_doc_id else None

        blob: bytes | None = None
        filetype: str = "pdf"

        if doc_id and doc_id in _preview_doc_cache:
            blob, filetype, _ = _preview_doc_cache[doc_id]
            _preview_doc_cache[doc_id] = (blob, filetype, time.time())
        else:
            upload = form.get("file")
            if not upload or not hasattr(upload, "read"):
                if doc_id:
                    raise HTTPException(
                        status_code=404,
                        detail=f"document '{doc_id}' not found in preview cache; please re-upload file",
                    )
                raise HTTPException(
                    status_code=400,
                    detail="file multipart field required",
                )

            blob = await upload.read()
            if not blob:
                raise HTTPException(
                    status_code=400,
                    detail="uploaded file is empty",
                )

            doc_id = hashlib.sha256(
                blob[:8192] + len(blob).to_bytes(8, "big")
            ).hexdigest()[:16]

            filename = getattr(upload, "filename", "") or "document.pdf"
            suffix = Path(filename).suffix.lower()
            is_pdf = suffix == ".pdf" or blob.startswith(b"%PDF")
            filetype = "pdf" if is_pdf else (suffix.lstrip(".") or "png")

            if (
                len(_preview_doc_cache) >= _PREVIEW_DOC_CACHE_CAPACITY
                and doc_id not in _preview_doc_cache
            ):
                oldest_id = min(
                    _preview_doc_cache,
                    key=lambda k: _preview_doc_cache[k][2],
                )
                del _preview_doc_cache[oldest_id]

            _preview_doc_cache[doc_id] = (blob, filetype, time.time())

        form_page = form.get("page")
        if form_page is not None:
            try:
                page = int(str(form_page))
            except ValueError:
                raise HTTPException(status_code=400, detail="page must be an integer")
        if page < 0:
            raise HTTPException(status_code=400, detail="page must be >= 0")

        form_dpi = form.get("dpi")
        if form_dpi is not None:
            try:
                dpi = int(str(form_dpi))
            except ValueError:
                raise HTTPException(status_code=400, detail="dpi must be an integer")
        dpi = max(50, min(300, dpi))

        def _render_page() -> tuple[bytes, int, float, float]:
            import pymupdf as fitz

            try:
                doc = fitz.open(stream=blob, filetype=filetype)  # type: ignore[no-untyped-call]
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"Cannot open document: {e}"
                )
            try:
                total_pages = doc.page_count
                if page >= total_pages:
                    raise HTTPException(
                        status_code=404,
                        detail=f"page index {page} out of range ({total_pages} total)",
                    )
                p = doc[page]
                rect = p.rect
                pix = p.get_pixmap(dpi=dpi, alpha=False)
                png = bytes(pix.tobytes("png"))  # type: ignore[no-untyped-call]
                return png, total_pages, float(rect.width), float(rect.height)
            finally:
                doc.close()  # type: ignore[no-untyped-call]

        png_bytes, total_pages, w, h = await asyncio.to_thread(_render_page)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "X-Document-Id": doc_id,
                "X-Total-Pages": str(total_pages),
                "X-Page-Width": str(w),
                "X-Page-Height": str(h),
            },
        )

    @router.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        record = await service.job_record(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if record.status in ("cancelled", "complete"):
            return {"cancelled": True, "status": record.status}
        outcome = await service.cancel_job(job_id)
        if outcome is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return {"cancelled": outcome}

    @router.get("/api/config")
    @router.get("/api/config/ocr")
    async def get_config() -> dict[str, Any]:
        return service.get_config()

    @router.post("/api/config")
    @router.put("/api/config/ocr")
    async def update_config(
        updates: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            return service.update_config(updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/process/preflight", response_model=None)
    @router.post("/api/process/preflight", response_model=None)
    async def preflight(
        body: PreflightRequest | None = None,
    ) -> PreflightResponse | JSONResponse:
        """Audit 6.3: verify the requested model is loaded on the VLM server.

        GET (no body) prefights the active ``/api/config`` coordinates;
        POST a ``PreflightRequest`` to override ``api_base`` / ``api_key``
        / ``model`` for the probe. Returns 200 with ``loaded=False`` when
        the model is missing (the UI badge shows "model mismatch") and
        502 with an envelope when the server is unreachable.
        """
        body = body or PreflightRequest()
        loaded, requested, base, loaded_models, detail = await service.preflight_check(
            api_base=body.api_base,
            api_key=body.api_key,
            model=body.model,
        )
        if not loaded and detail and "must be configured" in detail:
            return _envelope(400, "bad_request", detail)
        if not loaded and detail and "SSRF blocked" in detail:
            return _envelope(403, "ssrf_blocked", detail)
        return PreflightResponse(
            loaded=loaded,
            requested_model=requested,
            api_base=base,
            loaded_models=loaded_models,
            detail=detail,
        )

    return router


# -- plugin ---------------------------------------------------------------------


class OCRSchema(BaseModel):
    max_upload_mb: int | None = None
    quality_loop_enabled: bool = True
    quality_target: float = Field(default=0.85, ge=0.5, le=1.0)
    quality_max_retries: int = Field(default=2, ge=0, le=5)


class OCRPlugin(Plugin):
    """Registers the OCR service, the queue runner, and the route surface."""

    Schema = OCRSchema

    async def apply(self, ctx: Context) -> None:
        from omniscribe.plugins.runtime import RuntimeService

        runtime = ctx.inject(RuntimeService)
        queue = ctx.inject(JobQueue)
        artifacts = ctx.inject(ArtifactStore)
        progress = ctx.inject(ProgressService) if ctx.has(ProgressService) else None
        configured = self.config.get("max_upload_mb")
        max_upload_mb = (
            int(configured) if configured else runtime.settings.max_upload_mb
        )
        schema = OCRSchema(**self.config)
        service = OCRServiceImpl(
            runtime.settings,
            queue,
            artifacts,
            progress=progress,
            max_upload_mb=max_upload_mb,
            quality_defaults={
                "quality_loop_enabled": schema.quality_loop_enabled,
                "quality_target": schema.quality_target,
                "quality_max_retries": schema.quality_max_retries,
            },
        )
        ctx.service(OCRService, service)
        ctx.service(JobRunner, service.run_job)
        for event_type in (
            JobQueued,
            JobStarted,
            JobCompleted,
            JobFailed,
            JobCancelled,
            ProgressFrame,
        ):
            ctx.on(event_type, service.record_event)
        ctx.mount_router(build_ocr_router(service))


plugin = OCRPlugin()


__all__ = [
    "OCRPlugin",
    "OCRSchema",
    "OCRService",
    "build_ocr_router",
    "plugin",
]
