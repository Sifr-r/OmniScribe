"""HTTP routes for the glossary plugin (client-frozen contract).

Import routes accept BOTH shapes (user decision 2026-08-31):
`POST /api/glossary/import` takes the old JSON envelope (application/json)
or the Flutter client's multipart upload; `POST /api/glossary/import/url`
takes old query params or the client's JSON body. Business-rule 422s carry
the `{"error": "validation_failed"}` envelope (old contract); malformed
request schemas return FastAPI-native 422.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from omniscribe.plugins._http import envelope
from omniscribe.plugins.glossary.schemas import (
    GlossaryFormat,
    GlossaryImportRequest,
    GlossaryReorderRequest,
    GlossaryToggleBody,
    GlossaryToggleRequest,
    GlossaryUrlImportBody,
)
from omniscribe.plugins.glossary.service import (
    GlossaryError,
    GlossaryImportService,
)

EXTENSION_TO_FORMAT: dict[str, GlossaryFormat] = {
    "csv": GlossaryFormat.CSV,
    "tsv": GlossaryFormat.TSV,
    "xlf": GlossaryFormat.XLIFF,
    "xliff": GlossaryFormat.XLIFF,
    "tbx": GlossaryFormat.TBX,
    "tmx": GlossaryFormat.TMX,
    "json": GlossaryFormat.JSON_PAIRS,
}

INFERENCE_FAILURE_DETAIL = (
    "Could not infer format from URL. Pass ?format=csv|tsv|xliff|tbx|tmx|json_pairs."
)


def _infer_format_from_name(name: str) -> GlossaryFormat | None:
    """Return the glossary :class:`GlossaryFormat` implied by ``name``, or ``None``.

    Accepts both a bare filename (``terms.csv``) and a full URL
    (``https://example.com/path/terms.tbx``) by parsing the URL path
    when a scheme is present. The lookup is extension-based, so any
    filename without a recognised suffix — or with no suffix at all —
    returns ``None`` and the caller is expected to surface the
    inference-failure error to the client.
    """
    path = urlparse(name).path if "://" in name else name
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return EXTENSION_TO_FORMAT.get(suffix)


def build_glossary_router(service: GlossaryImportService) -> APIRouter:
    """Build and return the FastAPI router for the glossary plugin.

    The router exposes the client-frozen contract documented in this
    module's docstring. Every closure follows the same shape:

    1. call :meth:`service.ensure_store_ready` so a missing/uninitialized
       LanceDB store fails with the standard error envelope rather than
       a 500 from a low-level LanceDB exception;
    2. delegate to the matching :class:`GlossaryImportService` method;
    3. let :class:`GlossaryError` propagate so the framework exception
       handler maps it to the wire envelope.
    """
    router = APIRouter(tags=["glossary"])

    @router.post("/api/glossary/import", response_model=None)
    async def import_glossary(request: Request) -> dict[str, Any] | JSONResponse:
        """Import a glossary from an uploaded file or a JSON envelope.

        Accepts BOTH shapes (see module docstring): multipart upload
        with a ``file`` field and an optional ``format`` override, or
        the legacy JSON envelope. Format is inferred from the uploaded
        filename's extension when not explicitly supplied; an unknown
        extension returns the validation_failed envelope so the client
        can ask the user to specify the format manually.
        """
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                return envelope(400, "bad_request", "missing 'file' field")
            raw: bytes = await upload.read()
            fields: dict[str, Any] = {
                key: value
                for key, value in form.items()
                if key != "file" and isinstance(value, str)
            }
            fmt = fields.pop("format", None)
            if fmt:
                try:
                    format_enum: GlossaryFormat | None = GlossaryFormat(str(fmt))
                except ValueError:
                    return envelope(422, "validation_failed", f"Unknown format: {fmt}")
            else:
                format_enum = _infer_format_from_name(
                    str(getattr(upload, "filename", "") or "")
                )
                if format_enum is None:
                    return envelope(
                        422,
                        "validation_failed",
                        "Could not infer format from filename. Pass format=csv|tsv|xliff|tbx|tmx|json_pairs.",
                    )
            try:
                source = GlossaryImportRequest.model_validate(
                    {
                        "source": {
                            "format": format_enum,
                            "inline_bytes_b64": base64.b64encode(raw).decode("ascii"),
                            "encoding": fields.get("encoding"),
                            "name": fields.get("name"),
                        }
                    }
                )
            except ValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"detail": exc.errors(include_url=False)},
                )
        else:
            try:
                payload = await request.json()
            except Exception:
                return envelope(400, "bad_request", "Malformed JSON body.")
            try:
                source = GlossaryImportRequest.model_validate(payload)
            except ValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"detail": exc.errors(include_url=False)},
                )

        try:
            body = await service.import_glossary(source.source)
        except GlossaryError:
            raise
        return body

    @router.post("/api/glossary/import/url", response_model=None)
    async def import_glossary_from_url(
        request: Request,
        url: str | None = None,
        name: str | None = None,
        encoding: str | None = None,
        format: GlossaryFormat | None = None,
    ) -> dict[str, Any] | JSONResponse:
        """Import a glossary fetched from a remote URL.

        Accepts BOTH the old query-parameter shape and the modern JSON
        body (see module docstring). Format is taken from the explicit
        query/body parameter first, then inferred from the URL's path
        suffix. A fetch failure (DNS, timeout, non-2xx) is mapped to a
        502 with an ``ai_error`` code so the client can distinguish
        "we could not reach the URL" from a downstream parse failure.
        """
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            try:
                payload = await request.json()
            except Exception:
                return envelope(400, "bad_request", "Malformed JSON body.")
            try:
                body_model = GlossaryUrlImportBody.model_validate(payload)
            except ValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"detail": exc.errors(include_url=False)},
                )
            url = body_model.url
            name = body_model.name
            encoding = body_model.encoding
            format = body_model.format
        if not url:
            return envelope(400, "bad_request", "URL is required.")
        fmt = format or _infer_format_from_name(url)
        if fmt is None:
            return envelope(422, "validation_failed", INFERENCE_FAILURE_DETAIL)

        from omniscribe.plugins.glossary.http_fetch import fetch_url_bytes

        try:
            payload_bytes = await fetch_url_bytes(url)
        except GlossaryError:
            raise
        except Exception as exc:
            return envelope(502, "ai_error", f"Failed to fetch URL: {exc}")

        try:
            source = GlossaryImportRequest.model_validate(
                {
                    "source": {
                        "format": fmt,
                        "inline_bytes_b64": base64.b64encode(payload_bytes).decode(
                            "ascii"
                        ),
                        "encoding": encoding,
                        "name": name,
                    }
                }
            )
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors(include_url=False)},
            )
        try:
            body = await service.import_glossary(source.source)
        except GlossaryError:
            raise
        return body

    @router.get("/api/glossary/sources", response_model=None)
    async def list_sources() -> list[dict[str, Any]] | JSONResponse:
        """List every glossary source present in the library.

        Convenience alias for :func:`list_library` kept around for the
        older Flutter client contract; both endpoints return the same
        payload.
        """
        try:
            service.ensure_store_ready()
            return service.list_library()
        except GlossaryError:
            raise

    @router.delete("/api/glossary/sources/{source_id}", response_model=None)
    async def delete_source(source_id: str) -> dict[str, Any] | JSONResponse:
        """Delete a glossary source by id.

        Convenience alias for the library-endpoint delete; the older
        ``/api/glossary/sources/{id}`` route is preserved for clients
        that have not migrated to ``/api/glossary/library/{id}``.
        """
        try:
            service.ensure_store_ready()
            return service.delete(source_id)
        except GlossaryError:
            raise

    @router.get("/api/glossary/library", response_model=None)
    async def list_library() -> list[dict[str, Any]] | JSONResponse:
        """List every glossary (library entry) currently stored.

        Returns the library metadata in display order (matches the
        user-configured reorder). The Flutter client uses this to
        populate the glossary management screen.
        """
        try:
            service.ensure_store_ready()
            return service.list_library()
        except GlossaryError:
            raise

    @router.post("/api/glossary/library/{glossary_id}/enable", response_model=None)
    async def toggle_library_entry(
        glossary_id: str, req: GlossaryToggleRequest
    ) -> dict[str, Any] | JSONResponse:
        """Set the enabled flag for a glossary entry.

        Toggles whether the glossary is consulted during translation /
        OCR. The request body must carry the explicit ``enabled`` value
        (``true`` or ``false``) — there is no implicit-flip variant on
        this endpoint.
        """
        try:
            service.ensure_store_ready()
            return service.toggle(glossary_id, enabled=req.enabled)
        except GlossaryError:
            raise

    @router.post("/api/glossary/library/{source_id}/toggle", response_model=None)
    async def toggle_source(
        source_id: str,
        body: GlossaryToggleBody | None = Body(None),
    ) -> dict[str, Any] | JSONResponse:
        """Flip-or-set the enabled flag for a glossary source.

        When the body is omitted the service flips the current value
        (handy for the Flutter "tap to toggle" UI); when a body is
        provided the explicit ``enabled`` value wins.
        """
        try:
            service.ensure_store_ready()
            enabled = body.enabled if body is not None else None
            return service.toggle(source_id, enabled=enabled)
        except GlossaryError:
            raise

    @router.post("/api/glossary/library/reorder", response_model=None)
    async def reorder_library(
        req: GlossaryReorderRequest,
    ) -> dict[str, Any] | JSONResponse:
        """Persist a new display order for the glossary library.

        The request body carries the full ordered id list; the service
        validates that every id is known and that no entry is missing
        before persisting the new ordering.
        """
        try:
            service.ensure_store_ready()
            return service.reorder(req.ordered_ids)
        except GlossaryError:
            raise

    @router.delete("/api/glossary/library/{glossary_id}", response_model=None)
    async def delete_library_entry(
        glossary_id: str,
    ) -> dict[str, Any] | JSONResponse:
        """Delete a glossary entry (and its entries) from the library.

        Removes both the library metadata row and every term that was
        ingested from that source; the operation is idempotent at the
        row level (a missing id returns the standard not-found
        envelope).
        """
        try:
            service.ensure_store_ready()
            return service.delete(glossary_id)
        except GlossaryError:
            raise

    @router.get("/api/glossary/library/preview", response_model=None)
    async def library_preview() -> dict[str, Any] | JSONResponse:
        """Return a small preview sample of the merged glossary.

        Backs the Flutter client's "preview" panel — typically the
        first few entries per language pair so the user can sanity-check
        an import without paging through the full library.
        """
        try:
            service.ensure_store_ready()
            return service.library_preview()
        except GlossaryError:
            raise

    @router.get("/api/glossary/library/entries", response_model=None)
    async def get_library_entries(
        q: str | None = None,
        source_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any] | JSONResponse:
        """Browse / search entries across every library glossary.

        Supports a free-text ``q`` query and an optional ``source_id``
        filter; ``limit`` and ``offset`` paginate. The merged view is
        used when ``source_id`` is omitted.
        """
        try:
            service.ensure_store_ready()
            return service.entries(
                glossary_id=source_id,
                query=q,
                limit=limit,
                offset=offset,
            )
        except GlossaryError:
            raise

    @router.get("/api/glossary/library/{glossary_id}/entries", response_model=None)
    async def library_entries(
        glossary_id: str,
        q: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any] | JSONResponse:
        """Browse entries within a single glossary.

        Same shape as :func:`get_library_entries` but always scoped to
        the path-parameter glossary; ``source_id`` is unnecessary here.
        """
        try:
            service.ensure_store_ready()
            return service.entries(
                glossary_id=glossary_id,
                query=q,
                limit=limit,
                offset=offset,
            )
        except GlossaryError:
            raise

    @router.get("/api/glossary/library/merged", response_model=None)
    async def merged_entries() -> dict[str, Any] | JSONResponse:
        """Return the merged (enabled-only) glossary for translation use.

        Concatenates every enabled library entry into a single response
        so the translation pipeline can consult the full vocabulary in
        one round-trip instead of fetching per-source.
        """
        try:
            service.ensure_store_ready()
            return service.merged()
        except GlossaryError:
            raise

    return router
