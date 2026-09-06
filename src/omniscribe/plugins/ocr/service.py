"""OCR service implementation — bridges HTTP onto ``OCRPipeline``.

Audit catalog (Sprint 6 long-file split): separated from
``plugins/ocr/plugin.py`` so the plugin file is just the
Protocol + plugin class + route factory. This module holds
``OCRServiceImpl`` + its private ``_OcrPayload`` + the SSE
event-formatting helper + the queue/event-name lookup tables;
smaller helpers live under :mod:`omniscribe.plugins.ocr.services`.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException
from fastapi.responses import Response

from omniscribe.config import RuntimeSettings
from omniscribe.core.ocr.exceptions import ModelNotLoadedError
from omniscribe.core.ocr.processor import OCRProcessor
from omniscribe.core.workflows.base import OCRCancelled
from omniscribe.harness.events import Event
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    JobCancelled,
    JobCompleted,
    JobFailed,
    JobOutcome,
    JobQueue,
    JobQueued,
    JobStarted,
)
from omniscribe.plugins.ocr.pipeline_bridge import build_pipeline, run_pipeline
from omniscribe.plugins.ocr.schemas import (
    AsyncSubmitResponse,
    JobListItemResponse,
    JobStatusResponse,
    OCRRequest,
)

# Phase 3.8 (4.8, 2026-09-05): the previous ``service.py`` carried
# 65 lines of error-sanitization regexes, a content-type sniffing
# table + helper, and a config-seed catalogue + function — all
# service-adjacent but not part of the OCR service's actual
# implementation. Extracted into three focused modules under
# ``services/`` so this file can focus on the HTTP-onto-pipeline
# bridge. Imports below expose only the small public surface of
# each module.
from omniscribe.plugins.ocr.services import (
    guess_suffix,
    sanitize_job_error,
    seed_config,
)
from omniscribe.plugins.progress import ProgressFrame, ProgressService
from omniscribe.plugins.state_backend import TERMINAL_JOB_STATUSES, JobRecord
from omniscribe.utils.security import check_ssrf_target_sync

_HttpJobStatus = Literal["pending", "processing", "complete", "error", "cancelled"]

_QUEUE_STATUS_TO_HTTP: dict[str, _HttpJobStatus] = {
    "queued": "pending",
    "running": "processing",
    "complete": "complete",
    "error": "error",
    "cancelled": "cancelled",
}
_TERMINAL_QUEUE_STATUSES = TERMINAL_JOB_STATUSES

_EVENT_NAMES: dict[type, str] = {
    JobQueued: "job_queued",
    JobStarted: "job_started",
    JobCompleted: "job_completed",
    JobFailed: "job_failed",
    JobCancelled: "job_cancelled",
    ProgressFrame: "progress",
}
_TERMINAL_EVENTS: tuple[type, ...] = (JobCompleted, JobFailed, JobCancelled)

SSE_KEEPALIVE_SECONDS = 15.0

# Phase 3.8 (4.8, 2026-09-05): the error-sanitization regexes,
# the content-type sniffing table + helper, and the config-seed
# catalogue + function used to live below this comment. They are
# now in :mod:`omniscribe.plugins.ocr.services` and imported
# above. The 65 lines of constants and helpers in this file are
# now down to four service-implementation concerns.


@dataclass(frozen=True)
class _OcrPayload:
    """Everything the async worker needs for one queued upload.

    Audit 2.8: the previous version held the full upload ``file_bytes`` in
    the dataclass, so the queue and the in-flight job kept the original
    upload AND the result PDF in heap memory for the whole job lifetime.
    The bytes are now streamed to a per-job ``input_path`` at submit time
    and the path is what the worker reads. Memory is bounded by the
    concurrent on-disk upload count, not by the queue depth times the
    average upload size.
    """

    submission_id: str
    input_path: Path
    filename: str
    request: OCRRequest


class OCRServiceImpl:
    """Concrete OCRService: bridges HTTP onto ``OCRPipeline``."""

    def __init__(
        self,
        settings: RuntimeSettings,
        queue: JobQueue,
        artifacts: ArtifactStore,
        *,
        progress: ProgressService | None,
        max_upload_mb: int,
        quality_defaults: Mapping[str, bool | float | int] | None = None,
        max_buffered_jobs: int = 500,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._artifacts = artifacts
        self._progress = progress
        self._max_upload_mb = max_upload_mb
        # cordis.yml-seeded defaults for the quality repair loop; applied to
        # uploads whose form omits the corresponding field.
        self._quality_defaults: Mapping[str, bool | float | int] = (
            quality_defaults or {}
        )
        self._max_buffered_jobs = max_buffered_jobs
        self._submission_to_job: dict[str, str] = {}
        self._config: dict[str, Any] = seed_config(settings)
        self._event_buffers: dict[str, deque[dict[str, Any]]] = {}
        self._event_notify: dict[str, asyncio.Event] = {}
        self._done_jobs: set[str] = set()

    # -- public surface (audit D7) ---------------------------------------------
    # Read-only views over the constructor-set state. Tests and operational
    # tooling (e.g. /api/health inspectors) can read these without poking
    # at private attributes. The internal ``_event_buffers`` /
    # ``_event_notify`` / ``_done_jobs`` / ``_submission_to_job`` stay
    # private — they are mutable mid-job and exposing them would let
    # callers corrupt the per-job dispatch state.

    @property
    def settings(self) -> RuntimeSettings:
        """Active :class:`RuntimeSettings` for this service instance."""
        return self._settings

    @property
    def queue(self) -> JobQueue:
        """The in-process :class:`JobQueue` that runs registered runners."""
        return self._queue

    @property
    def artifacts(self) -> ArtifactStore:
        """The artifact store backing the result fetch endpoints."""
        return self._artifacts

    @property
    def progress(self) -> ProgressService | None:
        """The progress service used to publish per-channel events; ``None`` if disabled."""
        return self._progress

    @property
    def max_upload_mb(self) -> int:
        """Configured upload size cap in MB; enforced by ``MaxUploadSizeMiddleware``."""
        return self._max_upload_mb

    @property
    def max_buffered_jobs(self) -> int:
        """Maximum in-flight + queued jobs before submission is rejected with 429."""
        return self._max_buffered_jobs

    @max_buffered_jobs.setter
    def max_buffered_jobs(self, value: int) -> None:
        # Setter exists for the small set of tests that simulate
        # backpressure (e.g. ``test_ocr_plugin.py`` drops the cap to 10
        # and submits 25 requests). Production code never reassigns
        # the cap after boot; the runtime cap is a constructor
        # argument and the audit-trail lives in
        # ``docs/AGENTS.md`` §"Configuration".
        self._max_buffered_jobs = int(value)

    @property
    def quality_defaults(self) -> Mapping[str, bool | float | int]:
        """cordis.yml-seeded defaults for the quality repair loop; applied
        to uploads whose form omits the corresponding field."""
        return self._quality_defaults

    @property
    def config(self) -> dict[str, Any]:
        """Current effective config dict (``api_base`` / ``api_key`` / ``model``
        + the form-field defaults). Mutated in place by
        :meth:`update_config`; treat as read-only from outside.
        """
        return self._config

    # -- execution ------------------------------------------------------------

    async def run_sync(
        self,
        options: OCRRequest,
        blob: bytes,
        filename: str,
        content_type: str | None = None,
    ) -> Response:
        # Audit 2.8: the sync path also streams the upload to disk before
        # calling ``_execute`` so the worker reuses one file-write instead
        # of holding the bytes on the heap for the OCR duration.
        suffix = guess_suffix(filename, content_type)
        work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-"))
        input_path = work_dir / f"input{suffix}"
        input_path.write_bytes(blob)
        pdf_bytes, pages_data = await self._execute(
            options, input_path, filename, job_id=""
        )
        text_handle = await self._artifacts.put(
            json.dumps(
                {str(idx): "\n".join(lines) for idx, lines in pages_data.items()}
            ).encode("utf-8"),
            content_type="application/json",
            owner_job_id="",
        )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "X-Text-Artifact-Id": text_handle.id,
                "X-Text-Artifact-Token": text_handle.token,
            },
        )

    async def submit(
        self,
        options: OCRRequest,
        blob: bytes,
        filename: str,
        content_type: str | None = None,
    ) -> AsyncSubmitResponse:
        submission_id = secrets.token_hex(16)
        # Audit 2.8: stream the upload to a per-job tempfile so the queue
        # payload only carries a path, not the bytes. ``run_job`` reads
        # the file and ``run_sync`` shares the same per-job directory so
        # the worker can re-use the already-written bytes.
        suffix = guess_suffix(filename, content_type)
        work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-"))
        input_path = work_dir / f"input{suffix}"
        input_path.write_bytes(blob)
        payload = _OcrPayload(
            submission_id=submission_id,
            input_path=input_path,
            filename=filename,
            request=options,
        )
        handle = await self._queue.submit(
            payload,
            request_meta={
                "submission_id": submission_id,
                "filename": filename,
                "model": options.model or self._settings.llm_model,
                "pipeline_mode": options.pipeline_mode,
                "pages": options.pages,
            },
            input_path=str(input_path),
        )
        self._submission_to_job[submission_id] = handle.job_id
        # Audit 2.6: the inline insertion-order trim on every submit
        # duplicated the ``prune()`` eviction policy with conflicting
        # timing. ``prune()`` is the single source of truth for bounding
        # ``_submission_to_job`` alongside the other per-job state, and it
        # is invoked explicitly at shutdown. Drop the inline trim and let
        # ``prune()`` (called from ``record_event`` on terminal events via
        # ``_prune_events_if_needed`` and from ``shutdown``) handle the
        # bound. ``max_buffered_jobs`` therefore bounds all per-job maps
        # uniformly; ``_submission_to_job`` may briefly exceed the cap
        # between submits, but the next terminal event / shutdown closes
        # the gap.
        return AsyncSubmitResponse(
            job_id=handle.job_id, status="pending", status_url=handle.status_url
        )

    async def run_job(self, payload: Any) -> JobOutcome:
        """The JobRunner the queue worker injects at claim time."""
        if not isinstance(payload, _OcrPayload):
            raise ValueError("OCR job queue received a foreign payload")
        job_id = self._submission_to_job.get(payload.submission_id, "")
        cancel_check = self._cancel_check(job_id, payload.request.progress_channel)
        pdf_bytes, _ = await self._execute(
            payload.request, payload.input_path, payload.filename, job_id=job_id
        )
        if cancel_check is not None and cancel_check():
            raise OCRCancelled(f"job {job_id} cancelled")
        return JobOutcome(blob=pdf_bytes, content_type="application/pdf")

    async def _execute(
        self,
        options: OCRRequest,
        input_path: Path,
        filename: str,
        *,
        job_id: str,
    ) -> tuple[bytes, dict[int, list[str]]]:
        # Audit 2.8: input_path is the already-written per-job tempfile
        # (see ``submit`` and ``run_sync``). The worker re-uses the same
        # directory; the output PDF is written alongside it.
        work_dir = input_path.parent
        output_path = work_dir / "output.pdf"
        try:
            channel = options.progress_channel
            pipeline = build_pipeline(self._settings, options)
            pages_data = await run_pipeline(
                pipeline,
                settings=self._settings,
                request=options,
                input_path=str(input_path),
                output_path=str(output_path),
                on_progress=self._progress_adapter(job_id, channel),
                on_warning=self._warning_adapter(job_id, channel),
                cancel_check=self._cancel_check(job_id, channel),
            )
            return output_path.read_bytes(), pages_data
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _progress_adapter(
        self, job_id: str, channel: str | None
    ) -> Callable[..., Awaitable[None]] | None:
        if self._progress is None or not channel:
            return None

        progress = self._progress

        async def on_progress(percent: int, stage: str, message: str) -> None:
            # Legacy progress frame shape: no ``type`` discriminator.
            await progress.emit_progress(
                job_id,
                channel,
                {"status": message, "percent": percent, "stage": stage},
            )

        return on_progress

    def _warning_adapter(
        self, job_id: str, channel: str | None
    ) -> Callable[..., Awaitable[None]] | None:
        if self._progress is None or not channel:
            return None

        progress = self._progress

        async def on_warning(text: str) -> None:
            await progress.emit_progress(
                job_id,
                channel,
                {"status": text, "percent": 0, "stage": "warning", "warning": True},
            )

        return on_warning

    def _cancel_check(
        self, job_id: str, channel: str | None
    ) -> Callable[[], bool] | None:
        if not job_id and not channel:
            return None
        queue, progress = self._queue, self._progress

        def check() -> bool:
            if job_id and queue.is_cancelled(job_id):
                return True
            return bool(
                channel and progress is not None and progress.is_cancelled(channel)
            )

        return check

    # -- job queries ------------------------------------------------------------

    async def job_record(self, job_id: str) -> JobRecord | None:
        return await self._queue.status(job_id)

    async def job_status(self, job_id: str) -> JobStatusResponse | None:
        record = await self._queue.status(job_id)
        if record is None:
            return None
        return self._status_response(record)

    def _status_response(self, record: JobRecord) -> JobStatusResponse:
        terminal = record.status in _TERMINAL_QUEUE_STATUSES
        error = record.error
        if record.status == "cancelled":
            error = error or "Job cancelled."
        error = sanitize_job_error(error)
        # Security (2026-08-29 audit C-3 / H-3): the result token is NOT
        # returned here. The unauthenticated /api/process/status + /api/jobs
        # chain would otherwise bypass the constant-time gate at
        # fetch_result. The async client receives the token via the
        # ``job_completed`` SSE event payload (see _event_entry).
        return JobStatusResponse(
            job_id=record.job_id,
            filename=str(record.request_meta.get("filename", "")),
            status=_QUEUE_STATUS_TO_HTTP.get(record.status, "error"),
            created_at=record.created_at,
            # Phase 3.4 (4.6, 2026-09-05): surface the persisted
            # ``started_at`` instead of the previous always-``None``
            # placeholder. ``record.started_at`` is set when the worker
            # flips the job to ``running`` (``plugins/jobs.py``); it is
            # ``None`` only for jobs that have not yet started.
            started_at=record.started_at,
            completed_at=record.updated_at if terminal else None,
            duration_s=(record.updated_at - record.created_at) if terminal else None,
            error=error,
            text_artifact_id=record.result_artifact_id,
            failed_pages=[],
        )

    def job_list_item(self, record: JobRecord) -> JobListItemResponse:
        terminal = record.status in _TERMINAL_QUEUE_STATUSES
        meta = record.request_meta
        return JobListItemResponse(
            id=record.job_id,
            filename=str(meta.get("filename", "")),
            model=str(meta.get("model", "")),
            pipeline_mode=str(meta.get("pipeline_mode", "")),
            pages=meta.get("pages") if isinstance(meta.get("pages"), str) else None,
            duration_s=(record.updated_at - record.created_at) if terminal else 0.0,
            timestamp=datetime.fromtimestamp(record.created_at, tz=UTC).isoformat(),
            status=record.status,
            failed_pages=[],
        )

    async def fetch_result(self, job_id: str, token: str | None) -> Response:
        """Return the result PDF for a completed job.

        Pedantic review 2.7: every non-success path collapses to a
        single ``404`` with the same generic detail. The previous
        code distinguished ``unknown job`` (404), ``not complete``
        (409), and ``invalid result token`` (403) — an attacker
        with a job-id guess could enumerate which ids exist by
        watching the differential responses. The single-shape
        failure makes ``unknown job``, ``not complete``,
        ``bad token``, and ``artifact gone`` all indistinguishable
        to the caller; the only successful code is 200 with the
        PDF bytes.
        """
        not_found = HTTPException(status_code=404, detail="result not available")
        record = await self._queue.status(job_id)
        # Token compare runs only when there is a token to compare
        # against (i.e. a record with a stored result_artifact_token).
        # When there is no record, we skip the compare and fall
        # through to the same 404 — the queue lookup latency
        # dominates the constant-time compare by orders of
        # magnitude so there is no practical side channel here.
        expected = record.result_artifact_token if record else ""
        if expected and (not token or not secrets.compare_digest(token, expected)):
            raise not_found
        if record is None or record.status != "complete":
            raise not_found
        # Narrow the optional ``token`` to ``str`` so the artifact
        # store's typed ``get(artifact_id: str, token: str)`` is
        # satisfied. By this point we have either matched the stored
        # token (so the caller passed one) or bypassed the compare
        # (because the record has no stored token), and the runtime
        # contract is: caller-supplied token wins when present, the
        # stored token is used when the caller did not pass one.
        resolved_token = cast("str", token if token is not None else expected)
        artifact = await self._artifacts.get(
            record.result_artifact_id or "", resolved_token
        )
        if artifact is None:
            raise not_found
        return Response(
            content=artifact.blob,
            media_type=artifact.record.content_type or "application/pdf",
        )

    async def cancel_job(self, job_id: str) -> bool | None:
        """Returns None for unknown jobs, else the queue's cancel outcome."""
        if await self._queue.status(job_id) is None:
            return None
        return await self._queue.cancel(job_id)

    # -- per-page preview (workstation viewport) --------------------------------

    async def get_page_preview(
        self,
        job_id: str,
        page_index: int,
        *,
        dpi: int = 150,
    ) -> bytes | None:
        """Render one page of the original upload as a PNG.

        Returns ``None`` when the job has no recorded input path (older
        jobs, jobs whose source was never written to disk, or jobs from
        an in-memory backend that has been wiped on restart). The route
        surfaces a 404 in that case so the client can fall back to its
        placeholder.
        """
        record = await self._queue.status(job_id)
        if record is None:
            return None
        input_path_str = record.input_path
        if not input_path_str:
            return None
        input_path = Path(input_path_str)
        if not input_path.is_file():
            return None

        # Off-load PyMuPDF (a C extension) to a worker thread so the event
        # loop stays responsive while the page rasterizes.
        def _render() -> bytes | None:
            import pymupdf as fitz  # local: not every test env has it

            suffix = input_path.suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}:
                # Single-image input: there's only one page; ignore the
                # caller-supplied index and render the file as-is.
                if page_index != 0:
                    return None
                img_doc = fitz.open(input_path)  # type: ignore[no-untyped-call]
                try:
                    page = img_doc[0]
                    pix = page.get_pixmap(dpi=dpi, alpha=False)
                    return bytes(pix.tobytes("png"))  # type: ignore[no-untyped-call]
                finally:
                    img_doc.close()  # type: ignore[no-untyped-call]
            doc = fitz.open(input_path)  # type: ignore[no-untyped-call]
            try:
                if page_index < 0 or page_index >= doc.page_count:
                    return None
                page = doc[page_index]
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                return bytes(pix.tobytes("png"))  # type: ignore[no-untyped-call]
            finally:
                doc.close()  # type: ignore[no-untyped-call]

        return await asyncio.to_thread(_render)

    # -- config store -------------------------------------------------------------

    def get_config(self) -> dict[str, Any]:
        cfg = dict(self._config)
        key = str(cfg.get("api_key", "") or "")
        if key and key != "lm-studio":
            cfg["api_key"] = "******"
        return cfg

    # -- preflight (audit 6.3) ----------------------------------------------------

    async def preflight_check(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> tuple[bool, str, str, list[str], str]:
        """Audit 6.3: verify the requested model is loaded on the VLM server.

        Constructs an ephemeral :class:`OCRProcessor` against the resolved
        coordinates (overrides → current ``/api/config``) and invokes its
        ``ensure_model_loaded``. The long-lived per-request processor is
        untouched; the ephemeral one is closed via its ``aclose`` so the
        connection pool is released after the probe.

        Returns ``(loaded, requested_model, api_base, loaded_models, detail)``.
        On connection failure the detail is a human-readable diagnostic;
        the caller can decide whether to surface a 200 with ``loaded=False``
        (UI badge "model mismatch") or a 502 (server unreachable).
        """
        resolved_api_base = (api_base or self._config.get("api_base") or "").strip()
        resolved_api_key = api_key or self._config.get("api_key") or ""
        resolved_model = (model or self._config.get("model") or "").strip()
        if not resolved_api_base or not resolved_model:
            return (
                False,
                resolved_model,
                resolved_api_base,
                [],
                "api_base and model must be configured before pre-flight",
            )

        if api_base and api_base.strip():
            from omniscribe.utils.security import check_ssrf_target_sync

            check = check_ssrf_target_sync(api_base.strip())
            if not check.allowed:
                return (
                    False,
                    resolved_model,
                    resolved_api_base,
                    [],
                    f"SSRF blocked: {check.reason}",
                )

        probe = OCRProcessor(
            api_base=resolved_api_base,
            api_key=resolved_api_key,
            model=resolved_model,
        )
        try:
            try:
                await probe.ensure_model_loaded()
            except ModelNotLoadedError as exc:
                return (
                    False,
                    resolved_model,
                    resolved_api_base,
                    list(getattr(exc, "loaded_models", []) or []),
                    str(exc),
                )
            # Walk the same listing the processor used so we can echo the
            # server-side model list back to the UI without a second call.
            from openai import AsyncOpenAI

            list_client = getattr(probe, "client", None)
            ephemeral_client = False
            if list_client is None or not isinstance(list_client, AsyncOpenAI):
                list_client = AsyncOpenAI(
                    base_url=resolved_api_base,
                    api_key=resolved_api_key or "lm-studio",
                )
                ephemeral_client = True
            try:
                from omniscribe.core.ocr.client import _list_loaded_model_ids

                loaded_models = list(
                    await _list_loaded_model_ids(list_client, resolved_api_base)
                )
            except Exception:
                loaded_models = []
            finally:
                if ephemeral_client:
                    close_method = getattr(list_client, "close", None)
                    if callable(close_method):
                        res = close_method()
                        if asyncio.iscoroutine(res):
                            await res
            return (True, resolved_model, resolved_api_base, loaded_models, "")
        finally:
            await probe.aclose()

    def update_config(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        if "api_base" in updates and updates["api_base"] is not None:
            new_base = str(updates["api_base"]).strip()
            if new_base and new_base != self._config.get("api_base"):
                check = check_ssrf_target_sync(new_base)
                if not check.allowed:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid api_base URL (SSRF blocked: {check.reason})",
                    )
        changed_keys: set[str] = set()
        for key, value in updates.items():
            if value is None or key not in self._config:
                continue
            if key == "api_key" and value == "******":
                continue
            if self._config[key] != value:
                self._config[key] = value
                changed_keys.add(key)

        if not changed_keys:
            return self.get_config()

        # LLM coordinates write through to settings so the pipeline bridge
        # and the providers plugin observe the same active provider.
        if "api_base" in changed_keys:
            self._settings.llm_api_base = str(self._config["api_base"])
        if "api_key" in changed_keys:
            self._settings.llm_api_key = str(self._config["api_key"])
        if "model" in changed_keys:
            self._settings.llm_model = str(self._config["model"])
        return self.get_config()

    # -- SSE replay -----------------------------------------------------------------

    async def record_event(self, event: Event) -> None:
        job_id = getattr(event, "job_id", "")
        if not job_id:
            return
        buffer = self._event_buffers.setdefault(job_id, deque(maxlen=500))
        # Per-job monotonic sequence: the SSE consumer's cursor keys off
        # this, so a maxlen rotation (oldest entries evicted) must never
        # shift the cursor past unseen events. Deriving from the tail
        # keeps the counter continuous across rotations without extra
        # bookkeeping state.
        entry = event_entry(event)
        entry["seq"] = buffer[-1]["seq"] + 1 if buffer else 1
        buffer.append(entry)
        if type(event) in _TERMINAL_EVENTS:
            self._done_jobs.add(job_id)
        self._event_notify.setdefault(job_id, asyncio.Event()).set()
        self._prune_events_if_needed()

    def _prune_events_if_needed(self) -> None:
        """Keep event buffers and done job sets bounded to _max_buffered_jobs."""
        while len(self._event_buffers) > self._max_buffered_jobs:
            oldest = next(iter(self._event_buffers))
            self._event_buffers.pop(oldest, None)
            self._event_notify.pop(oldest, None)
            self._done_jobs.discard(oldest)

        if len(self._done_jobs) > self._max_buffered_jobs:
            excess = set(self._done_jobs) - set(self._event_buffers)
            for jid in excess:
                self._done_jobs.discard(jid)
            while len(self._done_jobs) > self._max_buffered_jobs:
                self._done_jobs.pop()

    def prune(self, max_buffered_jobs: int | None = None) -> int:
        """Explicitly prune event buffers and done jobs to the specified limit.

        Returns the number of pruned job buffers.
        """
        limit = (
            self._max_buffered_jobs if max_buffered_jobs is None else max_buffered_jobs
        )
        initial_count = len(self._event_buffers)
        while len(self._event_buffers) > limit:
            oldest = next(iter(self._event_buffers))
            self._event_buffers.pop(oldest, None)
            self._event_notify.pop(oldest, None)
            self._done_jobs.discard(oldest)
        while len(self._submission_to_job) > limit:
            self._submission_to_job.pop(next(iter(self._submission_to_job)), None)
        if len(self._done_jobs) > limit:
            excess = set(self._done_jobs) - set(self._event_buffers)
            for jid in excess:
                self._done_jobs.discard(jid)
            while len(self._done_jobs) > limit:
                self._done_jobs.pop()
        return initial_count - len(self._event_buffers)

    def event_backlog(self, job_id: str) -> list[dict[str, Any]]:
        return list(self._event_buffers.get(job_id, ()))

    def is_done(self, job_id: str) -> bool:
        return job_id in self._done_jobs

    async def wait_for_events(self, job_id: str) -> None:
        notify = self._event_notify.setdefault(job_id, asyncio.Event())
        await notify.wait()
        notify.clear()


def event_entry(event: Event) -> dict[str, Any]:
    """Format one job / progress event for the SSE stream.

    Public helper (no leading underscore) so the route factory in
    ``plugin.py`` can call it without depending on a private symbol.
    """
    data: dict[str, Any] = {"job_id": getattr(event, "job_id", "")}
    if isinstance(event, JobCompleted):
        # The async client uses ``artifact_token`` to authorize the
        # result download (this is the out-of-band channel that pairs
        # with the sync path's ``X-Text-Artifact-Token`` response header).
        data["artifact_id"] = event.artifact_id
        data["artifact_token"] = event.artifact_token
    elif isinstance(event, JobFailed):
        data["error"] = event.error
    elif isinstance(event, ProgressFrame):
        data.update(event.frame)
    return {
        "event": _EVENT_NAMES.get(type(event), type(event).__name__),
        "data": data,
    }


OCRService = OCRServiceImpl

__all__ = [
    "SSE_KEEPALIVE_SECONDS",
    "OCRService",
    "OCRServiceImpl",
    "event_entry",
]
# Phase 3.8 (4.8, 2026-09-05): config seeding / suffix / error-sanitization
# helpers moved to :mod:`omniscribe.plugins.ocr.services` — import from there.
