"""Q11 chaos and fault-injection test suite for JobQueue and worker execution.

Tests fault tolerance, graceful degradation, and resilience boundaries:
- Test 1: Worker cancellation mid-job (cooperative cancel & task interruption).
- Test 2: Stale connection / subscriber drop during job execution.
- Test 3: Runner exception / worker crash (failed transition, error sanitization, non-stalling queue).
- Test 4: Rapid concurrent queue/cancel races (state consistency and count invariants).
- Test 5: Replay buffer under rapid event bursts (per-job deque and global buffer memory bounds).
- Test 6: Redis multi-worker chaos (abrupt worker kill & visibility recovery under high contention).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, cast

from fastapi import WebSocketDisconnect

from omniscribe.harness.context import Context
from omniscribe.plugins import artifacts as art
from omniscribe.plugins import jobs, progress
from omniscribe.plugins import state_backend as sb
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    InMemoryJobQueue,
    JobCompleted,
    JobFailed,
    JobHandle,
    JobOutcome,
    JobQueue,
    JobRunner,
    JobStarted,
)
from omniscribe.plugins.ocr.service import OCRServiceImpl
from omniscribe.plugins.ocr.services import sanitize_job_error
from omniscribe.plugins.progress import (
    ProgressFrame,
    ProgressService,
    ProgressServiceImpl,
)
from omniscribe.plugins.state_backend import (
    TERMINAL_JOB_STATUSES,
    JobRecord,
    JobStatus,
)

# -- Test harness helpers ------------------------------------------------------


async def _boot(
    runner: JobRunner | None = None,
    *,
    frame_cap: int = 1000,
    channel_ttl_seconds: int = 600,
) -> Context:
    """Boot a harness context with state backend, artifacts, progress, and jobs."""
    ctx = Context()
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "memory"})
    await ctx.plugin(art.ArtifactsPlugin(), config={})
    await ctx.plugin(
        progress.ProgressPlugin(),
        config={"frame_cap": frame_cap, "channel_ttl_seconds": channel_ttl_seconds},
    )
    if runner is not None:
        ctx.service(JobRunner, runner)
    await ctx.plugin(jobs.JobsPlugin(), config={})
    return ctx


async def _wait_status(
    queue: JobQueue, job_id: str, status: JobStatus, *, timeout: float = 5.0
) -> JobRecord:
    """Poll queue until the job reaches the expected status or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = await queue.status(job_id)
        if record is not None and record.status == status:
            return record
        await asyncio.sleep(0.01)
    record = await queue.status(job_id)
    raise AssertionError(f"Job {job_id} never reached {status!r}; last record: {record}")


async def _wait_terminal(
    queue: JobQueue, job_id: str, *, timeout: float = 5.0
) -> JobRecord:
    """Poll queue until the job reaches any terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = await queue.status(job_id)
        if record is not None and record.status in TERMINAL_JOB_STATUSES:
            return record
        await asyncio.sleep(0.01)
    record = await queue.status(job_id)
    raise AssertionError(f"Job {job_id} never reached a terminal status; last record: {record}")


def _bare_ocr_service(max_buffered_jobs: int = 500) -> OCRServiceImpl:
    """Construct a minimal OCRServiceImpl with only SSE replay buffer bookkeeping."""
    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._event_buffers = {}
    service._event_notify = {}
    service._done_jobs = set()
    service._submission_to_job = {}
    service._max_buffered_jobs = max_buffered_jobs
    return service


# -- Test 1: Worker cancellation mid-job ---------------------------------------


async def test_worker_cancellation_mid_job_cleans_up_and_does_not_wedge() -> None:
    """Test 1: Worker cancellation mid-job.

    When a running job is cancelled or its task is interrupted:
    - JobQueue cleans up state and marks job cancelled/failed.
    - Subsequent jobs can still be enqueued and completed without wedging the queue.
    """
    job1_running = asyncio.Event()
    job1_can_proceed = asyncio.Event()

    async def runner(request: Any) -> JobOutcome:
        job_n = request.get("n", 0)
        if job_n == 1:
            job1_running.set()
            while not job1_can_proceed.is_set():
                if queue.is_cancelled(job1.job_id):
                    # Runner honors cooperative cancellation
                    return JobOutcome(blob=b"", content_type="text/plain")
                await asyncio.sleep(0.01)
        return JobOutcome(blob=f"result-{job_n}".encode(), content_type="text/plain")

    ctx = await _boot(runner)
    try:
        queue = ctx.inject(JobQueue)

        # 1. Enqueue Job 1 and wait for it to be running
        job1: JobHandle = await queue.submit({"n": 1})
        await _wait_status(queue, job1.job_id, "running")
        await job1_running.wait()

        # 2. Cancel Job 1 mid-job
        cancelled = await queue.cancel(job1.job_id)
        assert cancelled is True
        assert queue.is_cancelled(job1.job_id) is True

        # Wait for Job 1 to settle in terminal cancelled state
        record1 = await _wait_status(queue, job1.job_id, "cancelled")
        assert record1.status == "cancelled"

        # 3. Verify queue is not wedged: submit Job 2 and verify it completes
        job2: JobHandle = await queue.submit({"n": 2})
        record2 = await _wait_status(queue, job2.job_id, "complete")
        assert record2.status == "complete"
        assert record2.result_artifact_id is not None

        # 4. Interruption fault-injection: cancel the worker task directly mid-job
        assert isinstance(queue, InMemoryJobQueue)
        job3_running = asyncio.Event()

        async def hung_runner(request: Any) -> JobOutcome:
            job3_running.set()
            await asyncio.sleep(60.0)  # deliberate hang to simulate mid-job interruption
            return JobOutcome(blob=b"hung", content_type="text/plain")

        # Swap runner to hung_runner for job 3
        queue._runner_override = hung_runner
        job3: JobHandle = await queue.submit({"n": 3})
        await _wait_status(queue, job3.job_id, "running")
        await job3_running.wait()

        # Simulate harsh worker interruption
        if queue._worker is not None:
            queue._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue._worker
            queue._worker = None

        # Restore a healthy runner and restart worker task
        queue._runner_override = runner
        queue.start()

        # Submit Job 4 and verify that the queue processes it cleanly
        job4: JobHandle = await queue.submit({"n": 4})
        record4 = await _wait_status(queue, job4.job_id, "complete")
        assert record4.status == "complete"
    finally:
        await ctx.dispose()


# -- Test 2: Stale connection / subscriber drop during job execution -----------


class _BrokenStaleSocket:
    """Socket that raises ConnectionResetError immediately on send."""

    async def send_text(self, data: str) -> None:
        raise ConnectionResetError("Connection reset by peer (stale connection)")


class _DroppingSubscriberSocket:
    """Socket that receives a couple frames and then abruptly disconnects."""

    def __init__(self, drop_after: int = 2) -> None:
        self.drop_after = drop_after
        self.received: list[str] = []

    async def send_text(self, data: str) -> None:
        if len(self.received) >= self.drop_after:
            raise WebSocketDisconnect(code=1006, reason="Abnormal socket closure")
        self.received.append(data)


async def test_stale_connection_and_subscriber_drop_does_not_abort_job() -> None:
    """Test 2: Stale connection / subscriber drop during job execution.

    When progress events are dropped or subscribers abruptly disconnect:
    - Progress service detaches dead sockets safely.
    - Dropped progress events or connection errors do not abort the job.
    - Job executes to complete status with artifact created.
    """
    ctx = await _boot(frame_cap=5)  # low frame cap to exercise dropped progress events
    try:
        progress_svc = ctx.inject(ProgressService)
        assert isinstance(progress_svc, ProgressServiceImpl)

        # Open progress channel for the job
        channel_handle = await progress_svc.open_channel(job_id="chaos_job")
        channel_id = channel_handle.channel_id
        loop = asyncio.get_running_loop()

        # Attach broken/stale sockets
        stale_sock = _BrokenStaleSocket()
        dropping_sock = _DroppingSubscriberSocket(drop_after=1)
        progress_svc.attach(channel_id, stale_sock, loop)
        progress_svc.attach(channel_id, dropping_sock, loop)

        async def runner(request: Any) -> JobOutcome:
            # Runner broadcasts progress during execution
            for step in range(10):
                await progress_svc.emit_progress(
                    job_id="chaos_job",
                    channel_id=channel_id,
                    frame={"type": "progress", "percent": step * 10, "stage": "processing"},
                )
                await asyncio.sleep(0.005)
            return JobOutcome(blob=b"pdf-output-content", content_type="application/pdf")

        queue = ctx.inject(JobQueue)
        assert isinstance(queue, InMemoryJobQueue)
        queue._runner_override = runner

        handle = await queue.submit({"doc": "sample.pdf"})
        record = await _wait_status(queue, handle.job_id, "complete")

        # Assert job reached complete despite subscriber disconnects and frame cap drops
        assert record.status == "complete"
        assert record.result_artifact_id is not None
        assert record.result_artifact_token is not None

        # Verify artifact is stored and intact
        artifacts = ctx.inject(ArtifactStore)
        blob = await artifacts.get(record.result_artifact_id, record.result_artifact_token)
        assert blob is not None
        assert blob.blob == b"pdf-output-content"

        # Verify dead connections were detached
        remaining_connections = progress_svc._connections.get(channel_id, set())
        assert stale_sock not in [c.ws for c in remaining_connections]
        assert dropping_sock not in [c.ws for c in remaining_connections]
    finally:
        await ctx.dispose()


# -- Test 3: Runner exception / worker crash -----------------------------------


async def test_runner_exception_worker_crash_transitions_failed_and_does_not_stall() -> None:
    """Test 3: Runner exception / worker crash.

    When a registered JobRunner raises an unexpected unhandled exception:
    - JobQueue transitions the job to terminal error/FAILED state.
    - Error message is sanitized (stripping sensitive paths, secrets, database errors).
    - JobFailed event is emitted with the failure payload.
    - JobQueue does NOT stall and promptly processes subsequent jobs.
    """
    failed_events: list[JobFailed] = []
    completed_events: list[JobCompleted] = []

    async def crashing_runner(request: Any) -> JobOutcome:
        step = request.get("step")
        if step == "crash":
            # Simulate raw exception leaking internal path and sensitive secret
            raise RuntimeError(
                r"Failed to open D:\OmniScribe\private\secret_doc.pdf: "
                r"sqlite3.OperationalError database locked with api_key: secret_key_12345"
            )
        return JobOutcome(blob=f"recovered-{step}".encode(), content_type="text/plain")

    ctx = await _boot(crashing_runner)
    try:
        ctx.on(JobFailed, lambda e: failed_events.append(e))  # type: ignore[arg-type]
        ctx.on(JobCompleted, lambda e: completed_events.append(e))  # type: ignore[arg-type]
        queue = ctx.inject(JobQueue)

        # 1. Submit job that crashes in runner
        handle_crash = await queue.submit({"step": "crash"})
        record_crash = await _wait_status(queue, handle_crash.job_id, "error")

        # Verify terminal error status and event emission
        assert record_crash.status == "error"
        assert record_crash.error is not None
        assert len(failed_events) == 1
        assert failed_events[0].job_id == handle_crash.job_id

        # Verify error sanitization: sanitize_job_error sanitizes paths, db errors, secrets
        sanitized_msg = sanitize_job_error(record_crash.error)
        assert sanitized_msg is not None
        assert r"D:\OmniScribe\private" not in sanitized_msg
        assert "secret_key_12345" not in sanitized_msg
        assert (
            "A storage error occurred." in sanitized_msg
            or "[redacted]" in sanitized_msg
            or "[path]" in sanitized_msg
        )

        # 2. Verify queue does NOT stall — submit subsequent jobs
        handle_next1 = await queue.submit({"step": "next_1"})
        record_next1 = await _wait_status(queue, handle_next1.job_id, "complete")
        assert record_next1.status == "complete"
        assert record_next1.result_artifact_id is not None

        handle_next2 = await queue.submit({"step": "next_2"})
        record_next2 = await _wait_status(queue, handle_next2.job_id, "complete")
        assert record_next2.status == "complete"

        assert len(completed_events) == 2
        assert [e.job_id for e in completed_events] == [handle_next1.job_id, handle_next2.job_id]
    finally:
        await ctx.dispose()


# -- Test 4: Rapid concurrent queue/cancel races -------------------------------


async def test_rapid_concurrent_queue_cancel_races_maintain_consistent_state() -> None:
    """Test 4: Rapid concurrent queue/cancel races.

    Enqueueing and immediately cancelling multiple jobs under concurrency:
    - Maintains consistent state counts.
    - Prevents jobs from staying wedged in non-terminal states.
    - Leaves the queue fully operational for subsequent submissions.
    """
    total_jobs = 30

    async def runner(request: Any) -> JobOutcome:
        # Small latency to allow cancel requests to race during execution
        await asyncio.sleep(0.01)
        return JobOutcome(blob=b"payload", content_type="text/plain")

    ctx = await _boot(runner)
    try:
        queue = cast(JobQueue, ctx.inject(JobQueue))
        submitted_handles: list[JobHandle] = []

        async def submit_and_race_cancel(idx: int) -> JobHandle:
            handle = await queue.submit({"item": idx})
            # Staggered chaotic cancel decisions:
            if idx % 2 == 0:
                # Immediate cancel race
                await queue.cancel(handle.job_id)
            elif idx % 3 == 0:
                # Slight sleep before cancel
                await asyncio.sleep(0.005)
                await queue.cancel(handle.job_id)
            return handle

        # Launch concurrent submissions and cancel races
        submitted_handles = await asyncio.gather(
            *(submit_and_race_cancel(i) for i in range(total_jobs))
        )
        assert len(submitted_handles) == total_jobs

        # Wait for all jobs to reach a terminal status
        settled_records: list[JobRecord] = await asyncio.gather(
            *(_wait_terminal(queue, h.job_id, timeout=10.0) for h in submitted_handles)
        )

        # Invariant checks:
        # 1. Every job transitioned to a recognized terminal status
        for rec in settled_records:
            assert rec.status in TERMINAL_JOB_STATUSES, (
                f"Job {rec.job_id} stayed in non-terminal status {rec.status}"
            )

        # 2. Consistent status partition
        complete_count = sum(1 for r in settled_records if r.status == "complete")
        cancelled_count = sum(1 for r in settled_records if r.status == "cancelled")
        error_count = sum(1 for r in settled_records if r.status == "error")
        assert complete_count + cancelled_count + error_count == total_jobs

        # 3. List jobs returns all submitted records
        all_records = await queue.list_jobs(limit=total_jobs + 10)
        assert len(all_records) == total_jobs

        # 4. Queue is not corrupted — submit a final job and verify clean completion
        final_handle = await queue.submit({"item": "final_check"})
        final_record = await _wait_status(queue, final_handle.job_id, "complete")
        assert final_record.status == "complete"

        # 5. Clear empties all jobs
        cleared = await queue.clear()
        assert cleared == total_jobs + 1
        assert await queue.list_jobs() == []
    finally:
        await ctx.dispose()


# -- Test 5: Replay buffer under rapid event bursts ----------------------------


async def test_replay_buffer_under_rapid_event_bursts_maintains_memory_bounds() -> None:
    """Test 5: Replay buffer under rapid event bursts.

    Ensure memory bounds hold under chaotic worker firing:
    - Per-job event backlog obeys deque maxlen (500).
    - Monotonic sequence numbers are preserved across eviction rotations.
    - Global active job buffers never exceed max_buffered_jobs.
    - Explicit prune operation safely truncates older jobs under pressure.
    """
    max_buffered_jobs = 20
    service = _bare_ocr_service(max_buffered_jobs=max_buffered_jobs)

    # 1. Single-job massive event burst: 1200 events fired into one job
    single_job_id = "job_burst_single"
    burst_size = 1200
    for i in range(burst_size):
        await service.record_event(
            ProgressFrame(
                job_id=single_job_id,
                channel_id="c_burst",
                frame={"percent": i, "detail": f"burst_{i}"},
            )
        )

    single_backlog = service.event_backlog(single_job_id)
    # Memory bound: per-job deque maxlen is 500
    assert len(single_backlog) == 500
    # Strictly increasing sequence numbers across rotations
    seqs = [entry["seq"] for entry in single_backlog]
    assert seqs[0] == 701
    assert seqs[-1] == 1200
    assert seqs == list(range(701, 1201))

    # 2. Multi-worker chaotic firing across many concurrent jobs
    total_concurrent_jobs = 60  # exceeds max_buffered_jobs (20)
    events_per_job = 50

    async def chaotic_worker_burst(job_idx: int) -> str:
        job_id = f"job_concurrent_{job_idx}"
        await service.record_event(JobStarted(job_id=job_id))
        for step in range(events_per_job):
            await service.record_event(
                ProgressFrame(
                    job_id=job_id,
                    channel_id=f"channel_{job_idx}",
                    frame={"step": step, "job": job_idx},
                )
            )
            # Yield occasionally to simulate interleaved concurrent bursts
            if step % 15 == 0:
                await asyncio.sleep(0.001)
        await service.record_event(
            JobCompleted(job_id=job_id, artifact_id=f"art_{job_idx}", artifact_token="tok")
        )
        return job_id

    # Fire bursts across all 60 jobs concurrently
    await asyncio.gather(*(chaotic_worker_burst(i) for i in range(total_concurrent_jobs)))

    # Global memory bounds: buffer count must never exceed max_buffered_jobs
    assert len(service._event_buffers) <= max_buffered_jobs
    assert len(service._done_jobs) <= max_buffered_jobs

    # 3. Explicit prune bound verification
    prune_target = 5
    pruned_count = service.prune(max_buffered_jobs=prune_target)
    assert pruned_count >= 0
    assert len(service._event_buffers) <= prune_target
    assert len(service._done_jobs) <= prune_target


# -- Test 6: Redis multi-worker chaos ------------------------------------------


async def test_redis_worker_crash_visibility_recovery_chaos() -> None:
    """Test 6: Redis multi-worker chaos.

    Simulates high contention where a worker claims multiple jobs with short
    visibility timeouts and abruptly crashes (dies). Verifies:
    - Visibility timeout recovery accurately reclaims abandoned jobs.
    - Surviving workers process all jobs to completion without wedging.
    - Zero data loss across concurrent worker failures.
    """
    from dataclasses import replace
    import fakeredis.aioredis
    from omniscribe.plugins.jobs_redis import RedisJobQueue
    from omniscribe.plugins.state_backend_redis import RedisStateBackend
    from omniscribe.plugins.state_backend_types import StateBackend

    fake = fakeredis.aioredis.FakeRedis()
    try:
        ctx = Context()
        backend = RedisStateBackend(redis_url="redis://fake:6379/0")
        backend._redis = fake
        await backend.open()
        ctx.service(StateBackend, backend)

        await ctx.plugin(art.ArtifactsPlugin(), config={})
        artifacts = ctx.inject(ArtifactStore)

        queue = RedisJobQueue(ctx, backend, artifacts, redis_client=fake)
        await queue.open()

        # Submit 10 jobs
        handles = [await queue.submit({"job_num": i}) for i in range(10)]

        # Worker 1 claims 4 jobs with short visibility timeout (0.05s) and dies
        abandoned_jids: list[str] = []
        for _ in range(4):
            claim = await queue.claim(worker_id="dying-worker", visibility_timeout=0.05)
            assert claim is not None
            abandoned_jids.append(claim[0])

        # Wait for visibility timeout to expire
        await asyncio.sleep(0.08)

        # Run recovery
        recovered = await queue.recover_stale_jobs(max_retries=3)
        assert set(abandoned_jids).issubset(set(recovered))

        # Two healthy workers race to finish all jobs
        completed: set[str] = set()
        lock = asyncio.Lock()

        async def healthy_worker(wid: str) -> None:
            while True:
                c = await queue.claim(worker_id=wid)
                if c is None:
                    break
                jid, _ = c
                # simulate processing
                await asyncio.sleep(0.005)
                await artifacts.put(b"ok", content_type="text/plain", owner_job_id=jid)
                cur = await backend.get_job(jid)
                if cur:
                    await backend.upsert_job(
                        replace(cur, status="complete", updated_at=time.time())
                    )
                await queue.complete(jid)
                async with lock:
                    completed.add(jid)

        await asyncio.gather(healthy_worker("w1"), healthy_worker("w2"))

        # Verify all 10 jobs completed
        assert len(completed) == 10
        assert completed == {h.job_id for h in handles}

        # Verify all records in backend are complete
        for h in handles:
            rec = await queue.status(h.job_id)
            assert rec is not None and rec.status == "complete"
    finally:
        await queue.aclose()
        await backend.aclose()
        await ctx.dispose()
        await fake.aclose()
