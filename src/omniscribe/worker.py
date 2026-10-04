"""Standalone multi-worker runner script for OmniScribe (RFC 004 R3).

Spawns N concurrent asyncio worker loops that atomically claim jobs from
``RedisJobQueue``, resolve appropriate runners via DI / ``runner_protocol``,
execute jobs, update ``StateBackend``, and publish progress frames to Redis
Pub/Sub channels ``omniscribe:progress:{channel_id}``. Handles SIGINT/SIGTERM
gracefully, draining active jobs or requeuing in-flight work.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, cast

from omniscribe.config import RuntimeSettings, load_settings
from omniscribe.core.errors import redact_exception
from omniscribe.harness.context import Context
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    JobCancelled,
    JobCompleted,
    JobFailed,
    JobQueue,
    JobRunner,
    JobStarted,
    _resolve_job_runner,
)
from omniscribe.plugins.jobs_redis import RedisJobQueue
from omniscribe.plugins.state_backend import (
    TERMINAL_JOB_STATUSES,
    StateBackend,
)

_LOGGER = logging.getLogger("omniscribe.worker")


def _resolve_runner(payload: Any, ctx: Context) -> JobRunner:
    """Resolve the runner for a claimed job payload via the queue rule."""
    return _resolve_job_runner(payload, ctx)


async def boot_worker_context(
    *,
    redis_url: str | None = None,
    runtime_settings: RuntimeSettings | None = None,
) -> Context:
    """Boot minimal Cordis plugins required to resolve runners in worker mode."""
    if runtime_settings is None:
        overrides: dict[str, Any] = {"jobs_mode": "redis", "state_backend": "redis"}
        if redis_url:
            overrides["redis_url"] = redis_url
        runtime_settings = load_settings(**overrides)
    elif redis_url:
        object.__setattr__(runtime_settings, "redis_url", redis_url)
        object.__setattr__(runtime_settings, "jobs_mode", "redis")
        object.__setattr__(runtime_settings, "state_backend", "redis")

    ctx = Context()

    from omniscribe.plugins.artifacts import ArtifactsPlugin
    from omniscribe.plugins.glossary.plugin import GlossaryPlugin
    from omniscribe.plugins.jobs import JobsPlugin
    from omniscribe.plugins.logging import LoggingPlugin
    from omniscribe.plugins.ocr import OCRPlugin
    from omniscribe.plugins.progress import ProgressPlugin
    from omniscribe.plugins.runtime import RuntimePlugin, RuntimeService
    from omniscribe.plugins.state_backend import StateBackendPlugin
    from omniscribe.plugins.translate.plugin import TranslatePlugin

    await ctx.plugin(RuntimePlugin(), config={})
    # Single source of truth for the broker (audit Finding 5). ``--redis-url``
    # only ever reaches ``runtime_settings``; the environment that
    # ``load_settings()`` re-reads never sees it, so publish the resolved
    # object onto the RuntimeService. Every plugin below then resolves its
    # broker from the same settings (and the same explicit config values,
    # taken from that object), instead of each re-deriving a URL the worker
    # was never told about.
    ctx.inject(RuntimeService).settings = runtime_settings
    await ctx.plugin(
        LoggingPlugin(),
        config={"format": runtime_settings.log_format, "level": "INFO"},
    )
    await ctx.plugin(
        StateBackendPlugin(),
        config={"backend": "redis", "redis_url": runtime_settings.redis_url},
    )
    await ctx.plugin(ArtifactsPlugin(), config={})
    await ctx.plugin(
        JobsPlugin(),
        config={"mode": "redis", "redis_url": runtime_settings.redis_url},
    )
    await ctx.plugin(
        ProgressPlugin(),
        config={"mode": "redis", "redis_url": runtime_settings.redis_url},
    )
    await ctx.plugin(TranslatePlugin(), config={})
    await ctx.plugin(GlossaryPlugin(), config={})
    await ctx.plugin(OCRPlugin(), config={})

    return ctx


async def _broadcast_terminal_frame(
    ctx: Context,
    queue: Any,
    backend: StateBackend,
    job_id: str,
    frame: dict[str, Any],
    *,
    progress_channel: str | None = None,
) -> None:
    """Broadcast a terminal progress frame over Redis / ProgressService."""
    if not progress_channel:
        with contextlib.suppress(Exception):
            rec = await backend.get_job(job_id)
            if rec and rec.request_meta:
                progress_channel = rec.request_meta.get("progress_channel")

    channels = [job_id]
    if progress_channel and progress_channel != job_id:
        channels.append(progress_channel)

    from omniscribe.plugins.progress import ProgressService

    progress_svc = ctx.inject(ProgressService) if ctx.has(ProgressService) else None
    if progress_svc is not None and hasattr(progress_svc, "_local_terminal_jobs"):
        progress_svc._local_terminal_jobs.add(job_id)

    sender_id = getattr(progress_svc, "_service_id", None) or uuid.uuid4().hex
    frame_with_sender = {**frame, "_sender_id": sender_id}
    redis_client = getattr(queue, "_redis", None) or getattr(backend, "_redis", None)

    for ch in channels:
        published_to_redis = False
        if progress_svc is not None:
            with contextlib.suppress(Exception):
                await progress_svc.broadcast(ch, frame_with_sender)
                if (
                    getattr(progress_svc, "_redis_mode", False)
                    and getattr(progress_svc, "_redis", None) is not None
                ):
                    published_to_redis = True

        if not published_to_redis and redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.publish(
                    f"omniscribe:progress:{ch}",
                    json.dumps(frame_with_sender),
                )


async def _handle_job_failure(
    job_id: str,
    exc: BaseException,
    ctx: Context,
    queue: RedisJobQueue,
    backend: StateBackend,
    *,
    lease_owner: str | None = None,
) -> None:
    """Handle job failure or cancellation when runner execution raises."""
    if lease_owner and not await queue.owns_lease(job_id, lease_owner):
        _LOGGER.info("Job %s lost its lease while running", job_id)
        return
    err_msg = redact_exception(exc)
    _LOGGER.warning("Job %s failed with exception: %s", job_id, err_msg)
    is_cancelled = (
        queue.is_cancelled(job_id) or "cancelled" in exc.__class__.__name__.lower()
    )
    current = await backend.get_job(job_id)
    if current and current.status not in TERMINAL_JOB_STATUSES:
        if is_cancelled:
            await backend.upsert_job(
                replace(current, status="cancelled", updated_at=time.time())
            )
        else:
            await backend.upsert_job(
                replace(current, status="error", error=err_msg, updated_at=time.time())
            )
    if not await queue.fail(
        job_id,
        error="Cancelled" if is_cancelled else err_msg,
        lease_owner=lease_owner,
    ):
        _LOGGER.info("Job %s lost its lease before failure", job_id)
        return
    terminal_frame = (
        {
            "type": "cancelled",
            "status": "cancelled",
            "job_id": job_id,
            "error": "Cancelled",
        }
        if is_cancelled
        else {
            "type": "failed",
            "status": "error",
            "job_id": job_id,
            "error": err_msg,
        }
    )
    await _broadcast_terminal_frame(
        ctx,
        queue,
        backend,
        job_id,
        terminal_frame,
        progress_channel=current.request_meta.get("progress_channel")
        if current and current.request_meta
        else None,
    )
    await ctx.emit(
        JobCancelled(job_id=job_id)
        if is_cancelled
        else JobFailed(job_id=job_id, error=err_msg)
    )


async def _execute_job(
    job_id: str,
    payload: Any,
    ctx: Context,
    queue: RedisJobQueue,
    backend: StateBackend,
    artifacts: ArtifactStore,
    *,
    lease_owner: str | None = None,
) -> None:
    """Execute a single claimed job to completion or error."""

    async def still_owns_lease() -> bool:
        return not lease_owner or await queue.owns_lease(job_id, lease_owner)

    record = await backend.get_job(job_id)
    if record is None:
        await queue.fail(job_id, error="Job record not found in backend")
        return

    if record.status in TERMINAL_JOB_STATUSES:
        await queue.complete(job_id)
        return

    now = time.time()
    await backend.upsert_job(
        replace(record, status="running", started_at=now, updated_at=now)
    )
    await ctx.emit(JobStarted(job_id=job_id))

    try:
        runner = _resolve_runner(payload, ctx)
        outcome = await runner(payload)
    except asyncio.CancelledError:
        # Process shutdown cancels this task before ``run_worker`` requeues
        # its owned lease.  Do not delete the payload or transition the job
        # here, or the recovery claim would have nothing left to execute.
        _LOGGER.info("Job %s interrupted during execution", job_id)
        raise
    except BaseException as exc:
        await _handle_job_failure(
            job_id,
            exc,
            ctx,
            queue,
            backend,
            lease_owner=lease_owner,
        )
        return

    # Check cooperative cancel after runner settled
    if not await still_owns_lease():
        _LOGGER.info("Job %s lost its lease after execution", job_id)
        return

    if queue.is_cancelled(job_id):
        current = await backend.get_job(job_id)
        if current and current.status not in TERMINAL_JOB_STATUSES:
            await backend.upsert_job(
                replace(current, status="cancelled", updated_at=time.time())
            )
        if not await queue.fail(job_id, error="Cancelled", lease_owner=lease_owner):
            _LOGGER.info("Job %s lost its lease before cancellation", job_id)
            return
        await _broadcast_terminal_frame(
            ctx,
            queue,
            backend,
            job_id,
            {
                "type": "cancelled",
                "status": "cancelled",
                "job_id": job_id,
                "error": "Cancelled",
            },
            progress_channel=current.request_meta.get("progress_channel")
            if current and current.request_meta
            else None,
        )
        await ctx.emit(JobCancelled(job_id=job_id))
        return

    # Store result artifact only while this worker still owns the claim.
    try:
        handle = await artifacts.put(
            outcome.blob,
            content_type=outcome.content_type,
            owner_job_id=job_id,
        )
    except Exception as exc:
        await _handle_job_failure(
            job_id, exc, ctx, queue, backend, lease_owner=lease_owner
        )
        return

    current = await backend.get_job(job_id)
    if current is not None and current.status not in TERMINAL_JOB_STATUSES:
        await backend.upsert_job(
            replace(
                current,
                status="complete",
                result_artifact_id=handle.id,
                result_artifact_token=handle.token,
                request_meta={**current.request_meta, **outcome.metadata},
                updated_at=time.time(),
            )
        )
    if not await queue.complete(job_id, lease_owner=lease_owner):
        _LOGGER.info("Job %s lost its lease before completion", job_id)
        return
    await _broadcast_terminal_frame(
        ctx,
        queue,
        backend,
        job_id,
        {
            "type": "complete",
            "status": "complete",
            "job_id": job_id,
            "artifact_id": handle.id,
            "artifact_token": handle.token,
        },
        progress_channel=current.request_meta.get("progress_channel")
        if current and current.request_meta
        else None,
    )
    await ctx.emit(
        JobCompleted(
            job_id=job_id,
            artifact_id=handle.id,
            artifact_token=handle.token,
        )
    )
    _LOGGER.info("Job %s completed successfully", job_id)


async def _worker_loop(
    worker_id: str,
    worker_index: int,
    ctx: Context,
    queue: RedisJobQueue,
    backend: StateBackend,
    artifacts: ArtifactStore,
    stop_event: asyncio.Event,
    active_jobs: dict[str, tuple[asyncio.Task[None], str]],
    poll_interval: float = 0.2,
    visibility_timeout: float = 300.0,
) -> None:
    """Run one worker loop claiming and executing jobs."""
    _LOGGER.info("Worker loop %d started (worker_id=%s)", worker_index, worker_id)
    while not stop_event.is_set():
        lease_owner = f"{worker_id}:{worker_index}:{uuid.uuid4().hex}"
        try:
            claim = await queue.claim(
                worker_id=lease_owner, visibility_timeout=visibility_timeout
            )
        except Exception as exc:
            _LOGGER.warning("Worker loop %d claim error: %s", worker_index, exc)
            claim = None

        if claim is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            continue

        job_id, payload = claim
        _LOGGER.debug("Worker loop %d claimed job %s", worker_index, job_id)

        task = asyncio.create_task(
            _execute_job(
                job_id,
                payload,
                ctx,
                queue,
                backend,
                artifacts,
                lease_owner=lease_owner,
            ),
            name=f"execute-job-{job_id}",
        )
        renewal_stop = asyncio.Event()
        renewal_task = asyncio.create_task(
            _renew_lease_loop(
                job_id,
                lease_owner,
                queue,
                renewal_stop,
                visibility_timeout,
            ),
            name=f"renew-lease-{job_id}",
        )
        active_jobs[job_id] = (task, lease_owner)
        try:
            await task
        except asyncio.CancelledError:
            break
        except Exception as exc:
            err_msg = redact_exception(exc)
            _LOGGER.exception(
                "Unexpected error in job execution for %s: %s", job_id, err_msg
            )
        finally:
            renewal_stop.set()
            renewal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal_task
            active_jobs.pop(job_id, None)

    _LOGGER.info("Worker loop %d stopped", worker_index)


async def _renew_lease_loop(
    job_id: str,
    lease_owner: str,
    queue: RedisJobQueue,
    stop_event: asyncio.Event,
    visibility_timeout: float,
) -> None:
    """Keep an active job visible while its cooperative runner is alive."""
    interval = max(0.05, visibility_timeout / 3)
    while not stop_event.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        if stop_event.is_set():
            return
        try:
            if not await queue.renew_lease(job_id, lease_owner, visibility_timeout):
                _LOGGER.warning("Job %s lease was lost before renewal", job_id)
                return
        except Exception as exc:
            _LOGGER.warning("Job %s lease renewal failed: %s", job_id, exc)


async def _heartbeat_loop(
    worker_id: str,
    queue: RedisJobQueue,
    stop_event: asyncio.Event,
    interval: float = 10.0,
    ttl_seconds: int = 30,
) -> None:
    """Periodically publish worker liveness heartbeat."""
    while not stop_event.is_set():
        with contextlib.suppress(Exception):
            await queue.heartbeat(worker_id, ttl_seconds=ttl_seconds)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)


def _setup_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event
) -> None:
    """Setup graceful signal handlers across Linux/macOS and Windows."""

    def _trigger() -> None:
        if not stop_event.is_set():
            _LOGGER.info("Received shutdown signal; draining worker loops...")
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _trigger)
        except (NotImplementedError, AttributeError):
            with contextlib.suppress(Exception):
                signal.signal(sig, lambda _s, _f: loop.call_soon_threadsafe(_trigger))


async def run_worker(
    *,
    concurrency: int = 2,
    redis_url: str | None = None,
    visibility_timeout: float = 300.0,
    poll_interval: float = 0.2,
    drain_timeout: float = 15.0,
    worker_id: str | None = None,
    ctx: Context | None = None,
) -> None:
    """Main worker daemon orchestrator."""
    worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
    _LOGGER.info(
        "Starting omniscribe-worker [id=%s, concurrency=%d, vis_timeout=%.1fs]",
        worker_id,
        concurrency,
        visibility_timeout,
    )

    if ctx is None:
        ctx = await boot_worker_context(redis_url=redis_url)
    queue = cast(RedisJobQueue, ctx.inject(JobQueue))
    backend = ctx.inject(StateBackend)
    artifacts = ctx.inject(ArtifactStore)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    _setup_signal_handlers(loop, stop_event)

    active_jobs: dict[str, tuple[asyncio.Task[None], str]] = {}

    # Start worker loops
    worker_tasks = [
        asyncio.create_task(
            _worker_loop(
                worker_index=i,
                worker_id=worker_id,
                ctx=ctx,
                queue=queue,
                backend=backend,
                artifacts=artifacts,
                stop_event=stop_event,
                active_jobs=active_jobs,
                poll_interval=poll_interval,
                visibility_timeout=visibility_timeout,
            ),
            name=f"worker-loop-{i}",
        )
        for i in range(concurrency)
    ]

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(worker_id, queue, stop_event, interval=10.0, ttl_seconds=30),
        name=f"worker-heartbeat-{worker_id}",
    )

    # Wait until stop event is triggered
    await stop_event.wait()
    _LOGGER.info("Draining %d active in-flight jobs...", len(active_jobs))

    # Gracefully drain in-flight jobs up to drain_timeout
    if active_jobs:
        _done, pending = await asyncio.wait(
            [task for task, _owner in active_jobs.values()], timeout=drain_timeout
        )
        if pending:
            _LOGGER.warning(
                "Drain timeout expired; cancelling %d stuck jobs", len(pending)
            )
            for task in pending:
                task.cancel()
            # Requeue stuck jobs back to Redis queue
            for job_id, (_task, lease_owner) in list(active_jobs.items()):
                with contextlib.suppress(Exception):
                    if await queue.owns_lease(job_id, lease_owner):
                        await queue.requeue(job_id, lease_owner=lease_owner)

    # Cancel worker loop tasks and heartbeat
    heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat_task

    for wt in worker_tasks:
        wt.cancel()
    await asyncio.gather(*worker_tasks, return_exceptions=True)

    await ctx.dispose()
    _LOGGER.info("omniscribe-worker shutdown complete")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for omniscribe-worker."""
    parser = argparse.ArgumentParser(
        prog="omniscribe-worker",
        description="OmniScribe multi-worker distributed runner on Redis.",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=2,
        help="Number of concurrent worker loops (default: 2)",
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default=None,
        help="Redis connection URL (default: from REDIS_URL env)",
    )
    parser.add_argument(
        "--visibility-timeout",
        type=float,
        default=300.0,
        help="Visibility timeout in seconds for claimed jobs (default: 300.0)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.2,
        help="Poll interval in seconds when queue is empty (default: 0.2)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Main script entry point for omniscribe-worker."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(
            run_worker(
                concurrency=args.concurrency,
                redis_url=args.redis_url,
                visibility_timeout=args.visibility_timeout,
                poll_interval=args.poll_interval,
            )
        )


if __name__ == "__main__":
    main()
