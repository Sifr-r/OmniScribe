"""HTTP routes and endpoint handlers for the OCR plugin."""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import secrets
import tempfile
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    Body,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from omniscribe.core.workflows.base import OCRCancelled
from omniscribe.plugins._http import envelope
from omniscribe.plugins.errors import PluginError
from omniscribe.plugins.ocr.schemas import (
    AsyncSubmitResponse,
    JobListItemResponse,
    JobStatusResponse,
    OCRRequest,
    PreflightRequest,
    PreflightResponse,
)
from omniscribe.plugins.ocr.service import SSE_KEEPALIVE_SECONDS

if TYPE_CHECKING:
    from omniscribe.plugins.ocr.service import OCRServiceImpl

_LOGGER = logging.getLogger("omniscribe.plugins.ocr.routes")

_PREVIEW_DOC_CACHE_CAPACITY = 10
_preview_doc_cache: dict[str, tuple[Path, str, float]] = {}


def _cleanup_preview_file(file_path: Path) -> None:
    """Safely unlink a preview temp file on disk."""
    try:
        if file_path.exists():
            file_path.unlink(missing_ok=True)
    except OSError as exc:
        _LOGGER.debug("Failed to remove preview temp file %s: %s", file_path, exc)


def _evict_preview_cache() -> None:
    """Evict oldest entries when preview cache exceeds capacity and delete temp files."""
    while len(_preview_doc_cache) >= _PREVIEW_DOC_CACHE_CAPACITY:
        oldest_id = min(
            _preview_doc_cache,
            key=lambda k: _preview_doc_cache[k][2],
        )
        old_path, _, _ = _preview_doc_cache.pop(oldest_id)
        _cleanup_preview_file(old_path)


def _cleanup_all_preview_files() -> None:
    """Clean up all cached preview files on exit."""
    for _doc_id, (file_path, _, _) in list(_preview_doc_cache.items()):
        _cleanup_preview_file(file_path)
    _preview_doc_cache.clear()


atexit.register(_cleanup_all_preview_files)

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
    (
        "tiff",
        # Classic TIFF (II*\0 / MM\0*) and BigTIFF (II+\0 / MM\0+).
        lambda head: (
            head[:4] in (b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b")
        ),
    ),
    ("bmp", lambda head: head[:2] == b"BM"),
    ("docx", lambda head: head.startswith(b"PK\x03\x04")),
    (
        "html",
        lambda head: (
            head.decode("utf-8", errors="ignore")
            .lstrip()
            .lower()
            .startswith(
                (
                    "<!doc",
                    "<html",
                    "<head",
                    "<body",
                    "<?xml",
                    "<p",
                    "<div",
                    "<h1",
                    "<h2",
                    "<h3",
                    "<table",
                )
            )
        ),
    ),
    (
        "md",
        lambda head: (
            bool(head)
            and head.lstrip().startswith((b"#", b"---", b"```", b">", b"- ", b"* "))
        ),
    ),
)

_MIME_TO_FORMAT: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/html": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
}

#: Human-readable list of the upload formats the OCR route accepts. Kept next
#: to the two tables above so the error messages cannot drift from the
#: allowlist again (BMP/TIFF were advertised in the README but rejected here).
_SUPPORTED_UPLOAD_FORMATS: str = "PDF, PNG, JPEG, WebP, AVIF, TIFF, BMP"


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


def _is_markdown_text(blob: bytes) -> bool:
    """Markdown has no magic bytes: require nonempty UTF-8 text, not binary."""
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return bool(text.strip()) and all(
        char.isprintable() or char in "\r\n\t" for char in text
    )


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
            yield f"event: {entry['event']}\ndata: {json.dumps(entry['data'])}\n\n"
        if service.is_done(job_id):
            return
        try:
            await asyncio.wait_for(
                service.wait_for_events(job_id),
                timeout=keepalive_seconds,
            )
        except TimeoutError:
            yield ": keep-alive\n\n"


async def parse_multipart_upload(
    request: Request,
    service: OCRServiceImpl,
) -> tuple[OCRRequest, bytes, str, str]:
    """Parse and validate incoming multipart form document upload."""
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

    content_type = (
        (getattr(upload, "content_type", "") or "").split(";", 1)[0].strip().lower()
    )
    filename = str(getattr(upload, "filename", "") or "") or "upload.pdf"
    allowed_types = {
        "application/pdf",
        "application/octet-stream",  # Flutter file picker fallback
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/avif",
        "image/tiff",
        "image/bmp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/html",
        "text/markdown",
        "text/x-markdown",
    }
    if content_type and content_type not in allowed_types:
        raise HTTPException(
            status_code=415,
            detail=(
                f"unsupported content type: {content_type!r}. "
                f"Allowed: {_SUPPORTED_UPLOAD_FORMATS}."
            ),
        )

    head = blob[:12]
    sniffed = _sniff_format(head)
    markdown_upload = content_type in {"text/markdown", "text/x-markdown"} or (
        content_type in {"", "application/octet-stream"}
        and (Path(filename).suffix.lower() in {".md", ".markdown"} or sniffed == "md")
    )
    if markdown_upload:
        if sniffed in {
            "pdf",
            "png",
            "jpeg",
            "webp",
            "avif",
            "tiff",
            "bmp",
            "docx",
        } or not _is_markdown_text(blob):
            raise HTTPException(
                status_code=415,
                detail="Markdown uploads must contain valid UTF-8 text.",
            )
        content_type = "text/markdown"
    elif not content_type or content_type == "application/octet-stream":
        if _sniff_format(head) is None:
            raise HTTPException(
                status_code=415,
                detail=(
                    "could not detect a supported document format from "
                    "the upload contents; octet-stream uploads must be "
                    f"one of {_SUPPORTED_UPLOAD_FORMATS}"
                ),
            )
    elif content_type in _MIME_TO_FORMAT:
        sniffed = _sniff_format(head)
        if sniffed != _MIME_TO_FORMAT[content_type]:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"file contents do not match declared content type {content_type!r}"
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
        options = OCRRequest.model_validate(fields)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return options, blob, filename, content_type


# Alias for backward compatibility
_parse_upload = parse_multipart_upload


async def handle_process_sync(request: Request, service: OCRServiceImpl) -> Response:
    """Synchronous document processing endpoint handler."""
    options, blob, filename, content_type = await parse_multipart_upload(
        request, service
    )
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
    except PluginError as exc:
        return envelope(exc.status_code, exc.error, exc.detail)


async def handle_process_async(
    request: Request, service: OCRServiceImpl
) -> AsyncSubmitResponse:
    """Asynchronous document submission endpoint handler."""
    options, blob, filename, content_type = await parse_multipart_upload(
        request, service
    )
    return await service.submit(options, blob, filename, content_type=content_type)


async def handle_get_job(job_id: str, service: OCRServiceImpl) -> JobStatusResponse:
    """Fetch status for an OCR processing job."""
    status = await service.job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return status


def _extract_job_token(
    token: str | None = None,
    authorization: str | None = None,
    x_artifact_token: str | None = None,
    x_job_token: str | None = None,
) -> str | None:
    """Extract job / artifact capability token from query parameter or headers."""
    if token and token.strip():
        return token.strip()
    if x_job_token and x_job_token.strip():
        return x_job_token.strip()
    if x_artifact_token and x_artifact_token.strip():
        return x_artifact_token.strip()
    if authorization and authorization.strip():
        auth = authorization.strip()
        if auth.startswith("Bearer "):
            return auth.removeprefix("Bearer ").strip()
        return auth
    return None


async def _verify_job_token(
    job_id: str,
    service: OCRServiceImpl,
    token: str | None = None,
    authorization: str | None = None,
    x_artifact_token: str | None = None,
    x_job_token: str | None = None,
    detail: str = "Job not found",
) -> None:
    """Validate capability token against record.request_meta['result_access_token'].

    Raises 404 on missing or invalid token to avoid disclosing job existence.
    """
    provided = _extract_job_token(token, authorization, x_artifact_token, x_job_token)
    if not provided:
        raise HTTPException(status_code=404, detail=detail)

    record = await service.job_record(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=detail)

    candidates: list[str] = []
    access_token = record.request_meta.get("result_access_token")
    if isinstance(access_token, str) and access_token:
        candidates.append(access_token)
    res_tok = getattr(record, "result_artifact_token", None)
    if isinstance(res_tok, str) and res_tok:
        candidates.append(res_tok)

    if not candidates or not any(
        secrets.compare_digest(provided, cand) for cand in candidates
    ):
        raise HTTPException(status_code=404, detail=detail)


async def handle_subscribe_events(
    job_id: str,
    service: OCRServiceImpl,
    token: str | None = None,
    authorization: str | None = None,
    x_artifact_token: str | None = None,
    x_job_token: str | None = None,
) -> StreamingResponse:
    """Stream SSE lifecycle events for an active job with capability token validation."""
    await _verify_job_token(
        job_id=job_id,
        service=service,
        token=token,
        authorization=authorization,
        x_artifact_token=x_artifact_token,
        x_job_token=x_job_token,
        detail="Job not found",
    )
    return StreamingResponse(
        iter_sse_events(service, job_id, SSE_KEEPALIVE_SECONDS),
        media_type="text/event-stream",
    )


handle_get_job_events = handle_subscribe_events


async def handle_list_jobs(service: OCRServiceImpl) -> list[JobListItemResponse]:
    """Enumerate all jobs in the queue."""
    records = await service.queue.list_jobs()
    return [service.job_list_item(record) for record in records]


async def handle_clear_jobs(confirm: bool, service: OCRServiceImpl) -> Any:
    """Clear all completed or cancelled jobs from the queue."""
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


async def handle_get_job_result(
    job_id: str,
    token: str | None,
    authorization: str | None,
    service: OCRServiceImpl,
) -> Response:
    """Fetch completed PDF result artifact using token gate."""
    bearer = token
    if not bearer and authorization and authorization.startswith("Bearer "):
        bearer = authorization.removeprefix("Bearer ").strip()
    return await service.fetch_result(job_id, bearer)


async def handle_get_page_preview(
    job_id: str,
    page_index: int,
    service: OCRServiceImpl,
    token: str | None = None,
    authorization: str | None = None,
    x_artifact_token: str | None = None,
    x_job_token: str | None = None,
) -> Response:
    """Render a page of the original upload as PNG bytes with capability token validation."""
    if page_index < 0:
        raise HTTPException(status_code=400, detail="page_index must be >= 0")
    await _verify_job_token(
        job_id=job_id,
        service=service,
        token=token,
        authorization=authorization,
        x_artifact_token=x_artifact_token,
        x_job_token=x_job_token,
        detail="page preview unavailable for this job",
    )
    png_bytes = await service.get_page_preview(job_id, page_index)
    if png_bytes is None:
        raise HTTPException(
            status_code=404,
            detail="page preview unavailable for this job",
        )
    return Response(content=png_bytes, media_type="image/png")


async def handle_get_document_page_preview(  # noqa: C901
    request: Request,
    page: int = 0,
    dpi: int = 150,
) -> Response:
    """Render any page of an uploaded document as PNG bytes with disk-backed cache."""
    form = await request.form()
    req_doc_id = (
        form.get("doc_id")
        or request.headers.get("X-Document-Id")
        or request.headers.get("x-document-id")
    )
    doc_id: str | None = str(req_doc_id).strip() if req_doc_id else None

    file_path: Path | None = None
    filetype: str = "pdf"

    if doc_id and doc_id in _preview_doc_cache:
        cached_path, cached_type, _ = _preview_doc_cache[doc_id]
        if cached_path.exists():
            file_path = cached_path
            filetype = cached_type
            _preview_doc_cache[doc_id] = (file_path, filetype, time.time())
        else:
            del _preview_doc_cache[doc_id]

    if file_path is None:
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

        filename = getattr(upload, "filename", "") or "document.pdf"
        suffix = Path(filename).suffix.lower()

        # Stream directly to temporary file on disk to avoid keeping uploads in RAM
        temp_dir = tempfile.gettempdir()
        temp_fd, temp_raw_path = tempfile.mkstemp(
            prefix="omniscribe_preview_", dir=temp_dir
        )
        temp_file = Path(temp_raw_path)
        hasher = hashlib.sha256()
        total_read = 0
        first_chunk = b""

        try:
            with open(temp_fd, "wb") as f:
                chunk_size = 64 * 1024
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    if not first_chunk:
                        first_chunk = chunk
                    hasher.update(chunk)
                    f.write(chunk)
                    total_read += len(chunk)
        except Exception:
            _cleanup_preview_file(temp_file)
            raise

        if total_read == 0:
            _cleanup_preview_file(temp_file)
            raise HTTPException(
                status_code=400,
                detail="uploaded file is empty",
            )

        doc_id = hasher.hexdigest()[:16]
        is_pdf = suffix == ".pdf" or first_chunk.startswith(b"%PDF")
        filetype = "pdf" if is_pdf else (suffix.lstrip(".") or "png")

        if doc_id in _preview_doc_cache and _preview_doc_cache[doc_id][0].exists():
            _cleanup_preview_file(temp_file)
            file_path, filetype, _ = _preview_doc_cache[doc_id]
            _preview_doc_cache[doc_id] = (file_path, filetype, time.time())
        else:
            _evict_preview_cache()
            file_path = temp_file
            _preview_doc_cache[doc_id] = (file_path, filetype, time.time())

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
            doc = fitz.open(str(file_path), filetype=filetype)  # type: ignore[no-untyped-call]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot open document: {e}")
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
            "X-Document-Id": doc_id or "",
            "X-Total-Pages": str(total_pages),
            "X-Page-Width": str(w),
            "X-Page-Height": str(h),
        },
    )


async def handle_cancel_job(job_id: str, service: OCRServiceImpl) -> dict[str, Any]:
    """Cancel an active or queued job."""
    record = await service.job_record(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if record.status in ("cancelled", "complete"):
        return {"cancelled": True, "status": record.status}
    outcome = await service.cancel_job(job_id)
    if outcome is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {"cancelled": outcome}


async def handle_get_config(service: OCRServiceImpl) -> dict[str, Any]:
    """Read current OCR runtime configuration."""
    return service.get_config()


async def handle_update_config(
    updates: dict[str, Any],
    service: OCRServiceImpl,
) -> dict[str, Any]:
    """Update runtime OCR configuration."""
    try:
        return service.update_config(updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def handle_preflight(
    body: PreflightRequest | None,
    service: OCRServiceImpl,
) -> PreflightResponse | JSONResponse:
    """Probe server to verify requested VLM model is loaded."""
    req = body or PreflightRequest()
    res = await service.preflight_check(req)
    if not res.loaded and res.detail and "SSRF blocked" in res.detail:
        return envelope(403, "ssrf_blocked", res.detail)
    return res


# Backward compatibility aliases
process_sync = handle_process_sync
process_async = handle_process_async
get_job = handle_get_job
process_status = handle_get_job
get_job_events = handle_subscribe_events
process_events = handle_subscribe_events
subscribe_events = handle_subscribe_events
list_jobs = handle_list_jobs
clear_jobs = handle_clear_jobs
get_job_result = handle_get_job_result
job_result = handle_get_job_result
get_page_preview = handle_get_page_preview
page_preview = handle_get_page_preview
get_document_page_preview = handle_get_document_page_preview
document_page_preview = handle_get_document_page_preview
cancel_job = handle_cancel_job
get_config = handle_get_config
update_config = handle_update_config
preflight = handle_preflight


def build_ocr_router(service: OCRServiceImpl) -> APIRouter:
    """Build and configure the declarative OCR plugin router."""
    router = APIRouter(tags=["ocr"])

    @router.post("/api/process")
    async def process_sync(request: Request) -> Response:
        return await handle_process_sync(request, service)

    @router.post("/api/process/async", status_code=202)
    async def process_async(request: Request) -> AsyncSubmitResponse:
        return await handle_process_async(request, service)

    @router.get("/api/process/status/{job_id}")
    async def process_status(job_id: str) -> JobStatusResponse:
        return await handle_get_job(job_id, service)

    @router.get("/api/process/{job_id}/events")
    async def process_events(
        job_id: str,
        token: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
        x_artifact_token: str | None = Header(default=None, alias="X-Artifact-Token"),
        x_job_token: str | None = Header(default=None, alias="X-Job-Token"),
    ) -> StreamingResponse:
        return await handle_subscribe_events(
            job_id,
            service,
            token=token,
            authorization=authorization,
            x_artifact_token=x_artifact_token,
            x_job_token=x_job_token,
        )

    @router.get("/api/jobs")
    async def list_jobs() -> list[JobListItemResponse]:
        return await handle_list_jobs(service)

    @router.delete("/api/jobs")
    async def clear_jobs(confirm: bool = Query(default=False)) -> Any:
        return await handle_clear_jobs(confirm, service)

    @router.get("/api/jobs/{job_id}/result")
    async def job_result(
        job_id: str,
        token: str | None = None,
        authorization: str | None = Header(default=None),
        x_artifact_token: str | None = Header(default=None, alias="X-Artifact-Token"),
    ) -> Response:
        return await handle_get_job_result(
            job_id, token or x_artifact_token, authorization, service
        )

    @router.get("/api/jobs/{job_id}/pages/{page_index}/preview", response_model=None)
    async def page_preview(
        job_id: str,
        page_index: int,
        token: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
        x_artifact_token: str | None = Header(default=None, alias="X-Artifact-Token"),
        x_job_token: str | None = Header(default=None, alias="X-Job-Token"),
    ) -> Response:
        """Render a page of the original upload as PNG bytes.

        Used by the workstation viewport to show the underlying page
        beneath the bounding-box / heatmap overlays. Requires valid capability
        token matching the job.
        """
        return await handle_get_page_preview(
            job_id,
            page_index,
            service,
            token=token,
            authorization=authorization,
            x_artifact_token=x_artifact_token,
            x_job_token=x_job_token,
        )

    @router.post("/api/documents/preview", response_model=None)
    async def document_page_preview(
        request: Request, page: int = 0, dpi: int = 150
    ) -> Response:
        """Render any page of an uploaded document (PDF or image) as PNG bytes.

        Used by the workstation viewport to instantly display the page
        raster when a document is opened before or after processing.
        """
        return await handle_get_document_page_preview(request, page, dpi)

    @router.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        return await handle_cancel_job(job_id, service)

    @router.get("/api/config")
    @router.get("/api/config/ocr")
    async def get_config() -> dict[str, Any]:
        return await handle_get_config(service)

    @router.post("/api/config")
    @router.put("/api/config/ocr")
    async def update_config(
        updates: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        return await handle_update_config(updates, service)

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
        return await handle_preflight(body, service)

    return router


__all__ = [
    "_MIME_TO_FORMAT",
    "_PREVIEW_DOC_CACHE_CAPACITY",
    "_SUPPORTED_FORMAT_SIGNATURES",
    "_cleanup_all_preview_files",
    "_cleanup_preview_file",
    "_evict_preview_cache",
    "_extract_job_token",
    "_parse_upload",
    "_preview_doc_cache",
    "_sniff_format",
    "_verify_job_token",
    "build_ocr_router",
    "cancel_job",
    "clear_jobs",
    "document_page_preview",
    "get_config",
    "get_document_page_preview",
    "get_job",
    "get_job_events",
    "get_job_result",
    "get_page_preview",
    "handle_cancel_job",
    "handle_clear_jobs",
    "handle_get_config",
    "handle_get_document_page_preview",
    "handle_get_job",
    "handle_get_job_events",
    "handle_get_job_result",
    "handle_get_page_preview",
    "handle_list_jobs",
    "handle_preflight",
    "handle_process_async",
    "handle_process_sync",
    "handle_subscribe_events",
    "handle_update_config",
    "iter_sse_events",
    "job_result",
    "list_jobs",
    "page_preview",
    "parse_multipart_upload",
    "preflight",
    "process_async",
    "process_events",
    "process_status",
    "process_sync",
    "subscribe_events",
    "update_config",
]
