"""JobQueue plugin — single-worker async job queue over StateBackend.

Job lifecycle events are defined here (not in the OCR plugin) so the queue
never imports its producer: the OCR plugin registers a :class:`JobRunner`
service which the worker resolves lazily at claim time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field, is_dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast, runtime_checkable

from pydantic import BaseModel

from omniscribe.core.errors import redact_exception
from omniscribe.core.interfaces import JobQueueProtocol
from omniscribe.harness.context import Context
from omniscribe.harness.errors import ServiceNotFoundError
from omniscribe.harness.events import AgentEvent, SessionEvent
from omniscribe.harness.plugin import Plugin
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.state_backend_types import (
    TERMINAL_JOB_STATUSES,
    JobRecord,
    StateBackend,
)
from omniscribe.utils.security import redact_redis_url

_LOGGER = logging.getLogger("omniscribe.plugins.jobs")

# Pedantic 9.16: derive the terminal set from the JobStatus literal
# instead of duplicating the literal set. The single source of truth
# lives in ``state_backend.py`` so a new terminal status (e.g.
# ``"superseded"``) needs only one edit.
_TERMINAL_STATUSES = TERMINAL_JOB_STATUSES


def _is_strictly_inside_spool(path: Path) -> bool:
    """Validate that path is strictly located beneath a trusted spool directory."""
    try:
        resolved = path.resolve()
        if resolved == resolved.parent:
            return False
        roots: list[Path] = [Path(tempfile.gettempdir()).resolve()]
        for env_var in ("OMNISCRIBE_SPOOL_DIR", "OMNISCRIBE_ARTIFACT_DIR"):
            val = os.environ.get(env_var)
            if val and val.strip():
                roots.append(Path(val.strip()).resolve())
        for root in roots:
            if root == root.parent:
                continue
            if resolved.is_relative_to(root) and resolved != root:
                return True
        return False
    except (ValueError, RuntimeError):
        return False


def _cleanup_staged_ocr_input(raw_input_path: Any) -> None:
    """Remove staged OCR directory if inside a safe spool root and directory name starts with omniscribe-ocr-."""
    if not raw_input_path:
        return
    try:
        target_path = Path(raw_input_path).resolve()
        if _is_strictly_inside_spool(target_path):
            parent = target_path.parent
            if parent.name.startswith("omniscribe-ocr-") and _is_strictly_inside_spool(
                parent
            ):
                shutil.rmtree(parent, ignore_errors=True)
            elif target_path.is_dir() and target_path.name.startswith(
                "omniscribe-ocr-"
            ):
                shutil.rmtree(target_path, ignore_errors=True)
            elif target_path.is_file():
                target_path.unlink(missing_ok=True)
    except Exception as exc:
        _LOGGER.warning(
            "Failed to clean up spooled input path %s: %s", raw_input_path, exc
        )


# -- events -------------------------------------------------------------------


@dataclass(frozen=True)
class JobQueued(SessionEvent):
    job_id: str


@dataclass(frozen=True)
class JobStarted(AgentEvent):
    job_id: str


@dataclass(frozen=True)
class JobCompleted(SessionEvent):
    """Fired once a job's result blob has been written to the artifact store.

    The ``artifact_token`` is the out-of-band delivery channel for the
    async path — the same role the sync path's ``X-Text-Artifact-Token``
    response header plays. The unauthenticated status polling endpoint
    (``GET /api/process/status/{job_id}``) deliberately does **not**
    return the token; the client consumes it from this event payload
    (SSE stream at ``/api/process/{job_id}/events``) instead. The
    artifact id alone is safe to expose everywhere.
    """

    job_id: str
    artifact_id: str
    artifact_token: str


@dataclass(frozen=True)
class JobFailed(SessionEvent):
    job_id: str
    error: str


@dataclass(frozen=True)
class JobCancelled(SessionEvent):
    job_id: str


# -- runner seam ----------------------------------------------------------------


@dataclass(frozen=True)
class JobOutcome:
    """What a runner returns: the result blob plus its content type."""

    blob: bytes
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class _JobRunnerContract(Protocol):
    """The shape every async job runner satisfies.

    The three concrete runner protocols below (JobRunner,
    TranslationJobRunner, GlossaryJobRunner) are intentionally
    distinct type identities — they double as DI keys so the
    multi-producer dispatch in :meth:`InMemoryJobQueue._resolve_runner`
    can route a payload to the right runner via the
    ``runner_protocol`` class attribute. They are structurally
    identical (the ``__call__`` signature below is the entire
    contract); the distinct keys are the reason they exist as
    three named classes instead of a single alias.
    """

    async def __call__(self, request: Any) -> JobOutcome: ...


@runtime_checkable
class JobRunner(_JobRunnerContract, Protocol):
    """Executes one queued request; registered by the OCR plugin."""


@runtime_checkable
class TranslationJobRunner(_JobRunnerContract, Protocol):
    """Executes one queued translation request; registered by the translate plugin."""


@runtime_checkable
class GlossaryJobRunner(_JobRunnerContract, Protocol):
    """Executes one queued glossary import; registered by the glossary plugin."""


@runtime_checkable
class JobPayload(Protocol):
    """Structural protocol for payloads declaring their own runner service key.

    Producers (e.g. translation, glossary) whose jobs require a runner
    distinct from the default OCR `JobRunner` tag their payload classes
    with `runner_protocol = <RunnerProtocol>`.
    """

    runner_protocol: type


def _resolve_job_runner(payload: Any, ctx: Context) -> JobRunner:
    """Resolve tagged payloads only through their declared runner service."""
    marker = (
        payload.runner_protocol
        if isinstance(payload, JobPayload)
        else getattr(type(payload), "runner_protocol", None)
    )
    if marker is None:
        return cast("JobRunner", ctx.inject(JobRunner))
    if not isinstance(marker, type):
        raise TypeError(f"Invalid runner_protocol for {type(payload).__name__}")
    if not ctx.has(marker):
        raise ServiceNotFoundError(
            f"{marker.__name__} (runner for {type(payload).__name__})"
        )
    return cast("JobRunner", ctx.inject(marker))


# -- queue ----------------------------------------------------------------------


class JobHandle(NamedTuple):
    """Opaque job id plus the status-polling URL for the frontend."""

    job_id: str
    status_url: str


@runtime_checkable
class JobQueue(JobQueueProtocol, Protocol):
    """Async OCR job queue seam."""

    async def submit(
        self,
        request: Any,
        *,
        request_meta: dict[str, Any] | None = None,
        input_path: str | None = None,
    ) -> JobHandle: ...

    async def status(self, job_id: str) -> JobRecord | None: ...

    async def cancel(self, job_id: str) -> bool: ...

    def is_cancelled(self, job_id: str) -> bool: ...

    async def list_jobs(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[JobRecord]: ...

    async def clear(self) -> int: ...

    # JobQueueProtocol methods
    async def enqueue(
        self,
        request: Any,
        *,
        request_meta: dict[str, Any] | None = None,
        input_path: str | None = None,
    ) -> Any: ...

    async def get_job(self, job_id: str) -> Any: ...

    async def cancel_job(self, job_id: str) -> bool: ...


class InMemoryJobQueue:
    """One ``asyncio.Queue`` drained by a single worker task.

    Cancellation is cooperative: queued jobs are marked ``cancelled`` before
    they run; running jobs expose :meth:`is_cancelled` for the runner to poll
    at block boundaries.
    """

    def __init__(
        self,
        ctx: Context,
        backend: StateBackend,
        artifacts: ArtifactStore,
        *,
        runner: JobRunner | None = None,
    ) -> None:
        """Store dependencies; do not start the worker until :meth:`start` is called.

        The worker task is intentionally NOT created in ``__init__`` so
        the queue can be built during plugin ``apply`` before the
        asyncio loop is the canonical loop (a defensive choice — the
        current plugin ordering already gives us a running loop, but
        this avoids implicit-loop coupling for tests that build a
        queue manually).
        """
        self._ctx = ctx
        self._backend = backend
        self._artifacts = artifacts
        self._runner_override = runner
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._payloads: dict[str, Any] = {}
        self._cancelled: set[str] = set()
        self._worker: asyncio.Task[None] | None = None

    # -- public seam -------------------------------------------------------

    async def submit(
        self,
        request: Any,
        *,
        request_meta: dict[str, Any] | None = None,
        input_path: str | None = None,
    ) -> JobHandle:
        job_id = uuid.uuid4().hex
        now = time.time()
        await self._backend.upsert_job(
            JobRecord(
                job_id=job_id,
                status="queued",
                request_meta=dict(request_meta or {}),
                input_path=input_path,
                created_at=now,
                updated_at=now,
            )
        )
        if (
            is_dataclass(request)
            and not isinstance(request, type)
            and hasattr(request, "job_id")
        ):
            request = replace(request, job_id=job_id)
        self._payloads[job_id] = request
        await self._queue.put(job_id)
        await self._ctx.emit(JobQueued(job_id=job_id))
        return JobHandle(job_id=job_id, status_url=f"/api/process/status/{job_id}")

    async def status(self, job_id: str) -> JobRecord | None:
        """Return the current :class:`JobRecord` or ``None`` if unknown."""
        return await self._backend.get_job(job_id)

    def _cleanup_queued_job_input(self, job_id: str, record: JobRecord) -> None:
        raw_input_path = record.input_path or (record.request_meta or {}).get(
            "input_path"
        )
        if not raw_input_path and job_id in self._payloads:
            payload = self._payloads[job_id]
            if isinstance(payload, dict):
                raw_input_path = payload.get("input_path")
            else:
                raw_input_path = getattr(payload, "input_path", None)

        if raw_input_path:
            _cleanup_staged_ocr_input(raw_input_path)

    async def cancel(self, job_id: str) -> bool:
        """Mark ``job_id`` cancelled; ``False`` if missing or already terminal.

        Queued jobs flip immediately; running jobs rely on the runner
        polling :meth:`is_cancelled` at a block boundary (cooperative
        cancel — the runner is the only place that can interrupt a
        running VLM call without losing the artifact slot).
        """
        record = await self._backend.get_job(job_id)
        if record is None or record.status in _TERMINAL_STATUSES:
            return False
        self._cancelled.add(job_id)
        if record.status == "queued":
            self._cleanup_queued_job_input(job_id, record)
            await self._backend.upsert_job(
                replace(record, status="cancelled", updated_at=time.time())
            )
            await self._ctx.emit(JobCancelled(job_id=job_id))
        return True

    def is_cancelled(self, job_id: str) -> bool:
        """Return ``True`` if :meth:`cancel` has been called for ``job_id``."""
        return job_id in self._cancelled

    async def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        """Return job records newest-first, paginated by ``limit``/``offset``."""
        return await self._backend.list_jobs(limit=limit, offset=offset)

    async def clear(self) -> int:
        """Clear only jobs that have a terminal status (complete, error, cancelled).

        Does not clear queued or active jobs.
        """
        all_jobs: list[JobRecord] = []
        offset = 0
        while True:
            batch = await self._backend.list_jobs(limit=100, offset=offset)
            if not batch:
                break
            all_jobs.extend(batch)
            offset += len(batch)

        terminal_statuses = _TERMINAL_STATUSES
        cleared = 0
        for job in all_jobs:
            if job.status in terminal_statuses:
                await self._backend.delete_job(job.job_id)
                self._payloads.pop(job.job_id, None)
                self._cancelled.discard(job.job_id)
                cleared += 1
        return cleared

    # -- JobQueueProtocol aliases ----------------------------------------------
    enqueue = submit
    get_job = status
    cancel_job = cancel

    # -- worker lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Spawn the worker task on the running loop (idempotent).

        Idempotent: a second call is a no-op so the plugin ``apply``
        can call this safely even if the queue was already started.
        """
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(
                self._run(), name="omniscribe-job-worker"
            )

    async def shutdown(self) -> None:
        """Cancel the worker and mark any remaining queued rows ``cancelled``."""
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        # Pending work will never run now — mark it cancelled for callers.
        # Paginate until exhausted: list_jobs orders created_at DESC, so a
        # single bounded page would strand older queued rows forever
        # (pedantic review 1.6).
        offset = 0
        while True:
            page = await self._backend.list_jobs(limit=100, offset=offset)
            if not page:
                break
            offset += len(page)
            for record in page:
                if record.status == "queued":
                    self._cleanup_queued_job_input(record.job_id, record)
                    await self._backend.upsert_job(
                        replace(record, status="cancelled", updated_at=time.time())
                    )
        self._payloads.clear()

    async def _run(self) -> None:
        # Storage and dispatch failures must not terminate the only worker.
        while True:
            job_id = await self._queue.get()
            try:
                await self._process_one(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = redact_exception(exc)
                _LOGGER.exception("Job %s failed outside its runner", job_id)
                try:
                    await self._set_error(job_id, message)
                    await self._ctx.emit(JobFailed(job_id=job_id, error=message))
                except Exception:
                    _LOGGER.exception("Unable to persist failure for job %s", job_id)
            finally:
                self._queue.task_done()

    def _resolve_runner(self, payload: Any) -> JobRunner:
        """Resolve the runner for a queued job payload via DI.

        Multi-producer dispatch:
        Producers (e.g. translation, glossary) whose jobs require a runner
        distinct from the default OCR :class:`JobRunner` conform to the
        :class:`JobPayload` structural protocol by tagging their payload class
        with a ``runner_protocol = <RunnerProtocol>`` class attribute.

        Resolution order:
        1. If an explicit runner override was passed to ``__init__``, return it.
        2. If ``payload`` conforms to :class:`JobPayload` (or defines
           ``runner_protocol`` on its class/type), inject that protocol key from
           the application :class:`Context`.
        3. Untagged payloads (e.g. default OCR dicts/requests) fall back to
           injecting the default :class:`JobRunner` service.

        Any future 4th producer author only needs to:
        - Define their runner protocol inheriting from :class:`_JobRunnerContract`.
        - Register that runner implementation in :class:`Context` under their protocol key.
        - Tag their job payload class with ``runner_protocol = <TheirRunnerProtocol>``.
        """
        if self._runner_override is not None:
            return self._runner_override
        return _resolve_job_runner(payload, self._ctx)

    async def _process_one(self, job_id: str) -> None:
        payload = self._payloads.pop(job_id, None)
        if job_id in self._cancelled:
            await self._mark_cancelled(job_id, emit=True)
            return
        record = await self._backend.get_job(job_id)
        if record is None or record.status in _TERMINAL_STATUSES:
            return
        runner = self._resolve_runner(payload)
        # Phase 3.4 (4.6, 2026-09-05): persist ``started_at`` so the
        # status response can surface a real timestamp instead of the
        # previous ``None`` placeholder. Tracked by ``outstanding-work.md``
        # §6.84/6.85.
        await self._backend.upsert_job(
            replace(
                record,
                status="running",
                started_at=time.time(),
                updated_at=time.time(),
            )
        )
        await self._ctx.emit(JobStarted(job_id=job_id))
        try:
            outcome = await runner(payload)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if (
                self.is_cancelled(job_id)
                or "cancelled" in exc.__class__.__name__.lower()
            ):
                await self._mark_cancelled(job_id, emit=True)
                return
            if not isinstance(exc, Exception):
                raise
            message = redact_exception(exc)
            await self._set_error(job_id, message)
            await self._ctx.emit(JobFailed(job_id=job_id, error=message))
            return
        if self.is_cancelled(job_id):
            # The runner honored the cooperative cancel at a block boundary.
            await self._mark_cancelled(job_id, emit=True)
            return
        handle = await self._artifacts.put(
            outcome.blob,
            content_type=outcome.content_type,
            owner_job_id=job_id,
        )
        current = await self._backend.get_job(job_id)
        if current is not None:
            await self._backend.upsert_job(
                replace(
                    current,
                    status="complete",
                    result_artifact_id=handle.id,
                    result_artifact_token=handle.token,
                    request_meta={**current.request_meta, **outcome.metadata},
                    updated_at=time.time(),
                )
            )
        await self._ctx.emit(
            JobCompleted(
                job_id=job_id,
                artifact_id=handle.id,
                artifact_token=handle.token,
            )
        )

    async def _mark_cancelled(self, job_id: str, *, emit: bool) -> None:
        self._cancelled.discard(job_id)
        record = await self._backend.get_job(job_id)
        transitioned = False
        if record is not None and record.status not in _TERMINAL_STATUSES:
            await self._backend.upsert_job(
                replace(record, status="cancelled", updated_at=time.time())
            )
            transitioned = True
        if emit and transitioned:
            await self._ctx.emit(JobCancelled(job_id=job_id))

    async def _set_error(self, job_id: str, message: str) -> None:
        record = await self._backend.get_job(job_id)
        if record is not None and record.status not in _TERMINAL_STATUSES:
            await self._backend.upsert_job(
                replace(record, status="error", error=message, updated_at=time.time())
            )


# -- plugin ---------------------------------------------------------------------


class JobsSchema(BaseModel):
    worker_count: int = 1
    mode: str = "inprocess"


class JobsPlugin(Plugin):
    """Mounts the job queue (inprocess or redis); the runner arrives later via DI."""

    Schema = JobsSchema

    async def apply(self, ctx: Context) -> None:
        mode = str(self.config.get("mode") or "").strip().lower()
        if not mode or mode == "inprocess":
            from omniscribe.config import load_settings

            settings_mode = load_settings().jobs_mode
            if settings_mode and mode != "inprocess":
                mode = settings_mode
        backend = ctx.inject(StateBackend)
        artifacts = ctx.inject(ArtifactStore)
        if mode == "redis":
            from omniscribe.config import load_settings

            from .jobs_redis import RedisJobQueue

            settings = load_settings()
            redis_queue = RedisJobQueue(
                ctx,
                backend,
                artifacts,
                redis_url=settings.redis_url,
            )
            await redis_queue.open()
            ctx.service(JobQueue, redis_queue)
            ctx.effect(redis_queue.aclose)
            _LOGGER.info(
                "jobs plugin mounted (mode=redis, url=%s)",
                redact_redis_url(settings.redis_url),
            )
        else:
            worker_count = int(self.config.get("worker_count", 1))
            if worker_count != 1:
                _LOGGER.warning(
                    "worker_count=%d requested; this build ships a single worker",
                    worker_count,
                )
            inmem_queue = InMemoryJobQueue(ctx, backend, artifacts)
            inmem_queue.start()
            ctx.service(JobQueue, inmem_queue)
            ctx.effect(inmem_queue.shutdown)
            _LOGGER.info("jobs plugin mounted (mode=inprocess)")


plugin = JobsPlugin()


__all__ = [
    "GlossaryJobRunner",
    "InMemoryJobQueue",
    "JobCancelled",
    "JobCompleted",
    "JobFailed",
    "JobHandle",
    "JobOutcome",
    "JobPayload",
    "JobQueue",
    "JobQueueProtocol",
    "JobQueued",
    "JobRunner",
    "JobStarted",
    "JobsPlugin",
    "JobsSchema",
    "TranslationJobRunner",
    "plugin",
]
