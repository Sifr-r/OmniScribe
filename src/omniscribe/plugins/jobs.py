"""JobQueue plugin — single-worker async job queue over StateBackend.

Job lifecycle events are defined here (not in the OCR plugin) so the queue
never imports its producer: the OCR plugin registers a :class:`JobRunner`
service which the worker resolves lazily at claim time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, NamedTuple, Protocol, cast, runtime_checkable

from pydantic import BaseModel

from omniscribe.harness.context import Context
from omniscribe.harness.events import AgentEvent, SessionEvent
from omniscribe.harness.plugin import Plugin
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.state_backend_types import (
    TERMINAL_JOB_STATUSES,
    JobRecord,
    StateBackend,
)

_LOGGER = logging.getLogger("omniscribe.plugins.jobs")

# Pedantic 9.16: derive the terminal set from the JobStatus literal
# instead of duplicating the literal set. The single source of truth
# lives in ``state_backend.py`` so a new terminal status (e.g.
# ``"superseded"``) needs only one edit.
_TERMINAL_STATUSES = TERMINAL_JOB_STATUSES


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


# -- queue ----------------------------------------------------------------------


class JobHandle(NamedTuple):
    """Opaque job id plus the status-polling URL for the frontend."""

    job_id: str
    status_url: str


@runtime_checkable
class JobQueue(Protocol):
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
        self._payloads[job_id] = request
        await self._queue.put(job_id)
        await self._ctx.emit(JobQueued(job_id=job_id))
        return JobHandle(job_id=job_id, status_url=f"/api/process/status/{job_id}")

    async def status(self, job_id: str) -> JobRecord | None:
        return await self._backend.get_job(job_id)

    async def cancel(self, job_id: str) -> bool:
        record = await self._backend.get_job(job_id)
        if record is None or record.status in _TERMINAL_STATUSES:
            return False
        self._cancelled.add(job_id)
        if record.status == "queued":
            await self._backend.upsert_job(
                replace(record, status="cancelled", updated_at=time.time())
            )
            await self._ctx.emit(JobCancelled(job_id=job_id))
        return True

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled

    async def list_jobs(self, *, limit: int = 100, offset: int = 0) -> list[JobRecord]:
        return await self._backend.list_jobs(limit=limit, offset=offset)

    async def clear(self) -> int:
        count = await self._backend.clear_jobs()
        self._payloads.clear()
        self._cancelled.clear()
        return count

    # -- worker lifecycle ------------------------------------------------------

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.get_running_loop().create_task(
                self._run(), name="omniscribe-job-worker"
            )

    async def shutdown(self) -> None:
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
                    await self._backend.upsert_job(
                        replace(record, status="cancelled", updated_at=time.time())
                    )
        self._payloads.clear()

    async def _run(self) -> None:
        # Phase 3.5 (4.7, 2026-09-05): the previous
        # ``except Exception: _LOGGER.exception(...)`` block was the
        # second source of error reporting — it logged but never
        # emitted ``JobFailed`` to the client, so a runner that
        # consistently raised (e.g. an LLM provider stuck in a
        # retry loop) would flood the log and the status response
        # would silently stay ``processing`` forever. ``_process_one``
        # already catches ``BaseException`` and emits ``JobFailed``;
        # removing the worker-level catch makes the inner handler
        # the single source of truth. An exception escaping
        # ``_process_one`` is a worker bug, not a job bug, and
        # killing the worker is the right signal in that case.
        while True:
            job_id = await self._queue.get()
            try:
                await self._process_one(job_id)
            except asyncio.CancelledError:
                raise
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
        marker = (
            payload.runner_protocol
            if isinstance(payload, JobPayload)
            else getattr(type(payload), "runner_protocol", None)
        )
        if marker is not None:
            return cast("JobRunner", self._ctx.inject(marker))
        return cast("JobRunner", self._ctx.inject(JobRunner))

    async def _process_one(self, job_id: str) -> None:
        payload = self._payloads.pop(job_id, None)
        if job_id in self._cancelled:
            await self._mark_cancelled(job_id, emit=True)
            return
        record = await self._backend.get_job(job_id)
        if record is None:
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
            message = str(exc) or exc.__class__.__name__
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
        if record is not None:
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
            _LOGGER.info("jobs plugin mounted (mode=redis, url=%s)", settings.redis_url)
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
    "JobQueued",
    "JobRunner",
    "JobStarted",
    "JobsPlugin",
    "JobsSchema",
    "TranslationJobRunner",
    "plugin",
]
