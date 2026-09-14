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

import logging
from typing import Protocol, runtime_checkable

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

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
    OCRRequest,
    PreflightRequest,
    PreflightResponse,
)
from omniscribe.plugins.progress import ProgressFrame, ProgressService

from . import routes
from .routes import (
    _MIME_TO_FORMAT,
    _PREVIEW_DOC_CACHE_CAPACITY,
    _SUPPORTED_FORMAT_SIGNATURES,
    _parse_upload,
    _preview_doc_cache,
    _sniff_format,
    iter_sse_events,
)
from .service import (
    OCRServiceImpl,
)

_LOGGER = logging.getLogger("omniscribe.plugins.ocr")


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

    async def preflight_check(
        self,
        request: PreflightRequest | None = None,
    ) -> PreflightResponse: ...


def build_ocr_router(service: OCRServiceImpl) -> APIRouter:
    """Build and configure the OCR plugin API router (< 40 lines factory)."""
    return routes.build_ocr_router(service)


# -- plugin ---------------------------------------------------------------------


class OCRSchema(BaseModel):
    """Configuration block for the OCR plugin.

    All fields are optional; ``apply`` falls back to
    ``runtime.settings`` / sensible defaults when they are unset.
    """

    max_upload_mb: int | None = None
    quality_loop_enabled: bool = True
    quality_target: float = Field(default=0.85, ge=0.5, le=1.0)
    quality_max_retries: int = Field(default=2, ge=0, le=5)


class OCRPlugin(Plugin):
    """Registers the OCR service, the queue runner, and the route surface.

    On ``apply``:

    1. Resolves the runtime, queue, artifact store, and optional
       progress service from the harness context.
    2. Constructs the concrete :class:`OCRServiceImpl`, configured
       with upload-size and quality-loop defaults from ``self.config``.
    3. Binds it under both the ``OCRService`` Protocol and the
       ``JobRunner`` slot so the harness can claim jobs from the queue.
    4. Subscribes the service to the queue + progress event channels so
       SSE streams can replay the per-job history.
    5. Mounts the FastAPI router (sync + async OCR routes, SSE,
       preflight, runtime config).
    """

    Schema = OCRSchema

    async def apply(self, ctx: Context) -> None:
        """Wire the OCR plugin into the harness context."""
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
    "_MIME_TO_FORMAT",
    "_PREVIEW_DOC_CACHE_CAPACITY",
    "_SUPPORTED_FORMAT_SIGNATURES",
    "OCRPlugin",
    "OCRSchema",
    "OCRService",
    "_parse_upload",
    "_preview_doc_cache",
    "_sniff_format",
    "build_ocr_router",
    "iter_sse_events",
    "plugin",
]
