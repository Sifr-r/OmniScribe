"""Comprehensive tests for RedisJobQueue and multi-worker dispatch (RFC 004 R3).

Validates:
- All JobQueue Protocol methods (submit, status, cancel, is_cancelled, list_jobs, clear).
- Atomic claim Lua script (FIFO order, skipping cancelled jobs, empty queue).
- Multi-worker race concurrency (4 workers racing for 20 jobs, exactly-once claim, zero loss).
- Visibility timeout and automatic dead-worker recovery.
- Visibility timeout retry exhaustion failing the job.
- Worker heartbeat monitoring.
- Cross-pod progress fan-out over Redis Pub/Sub.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import fakeredis.aioredis
import pytest

from omniscribe.harness.context import Context
from omniscribe.plugins import artifacts as art_plugin
from omniscribe.plugins import jobs, progress
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    JobCancelled,
    JobCompleted,
    JobFailed,
    JobHandle,
    JobOutcome,
    JobQueue,
    JobQueued,
    JobRunner,
    JobStarted,
)
from omniscribe.plugins.jobs_redis import (
    KEY_ACTIVE,
    KEY_CANCELLED_SET,
    KEY_HEARTBEAT_PREFIX,
    KEY_PAYLOAD_PREFIX,
    KEY_QUEUE,
    RedisJobQueue,
    deserialize_payload,
    serialize_payload,
)
from omniscribe.plugins.progress import ProgressFrame, ProgressService, ProgressServiceImpl
from omniscribe.plugins.state_backend_redis import RedisStateBackend
from omniscribe.plugins.state_backend_types import JobRecord, StateBackend
from omniscribe.worker import _execute_job, _resolve_runner


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis()
    try:
        yield fake
    finally:
        await fake.aclose()


@pytest.fixture
async def harness(fake_redis: fakeredis.aioredis.FakeRedis) -> AsyncIterator[dict[str, Any]]:
    """Construct an in-process Redis test harness context."""
    ctx = Context()
    backend = RedisStateBackend(redis_url="redis://fake:6379/0")
    # Patch backend to use fake_redis
    backend._redis = fake_redis
    await backend.open()
    ctx.service(StateBackend, backend)

    await ctx.plugin(art_plugin.ArtifactsPlugin(), config={})
    artifacts = ctx.inject(ArtifactStore)

    queue = RedisJobQueue(
        ctx,
        backend,
        artifacts,
        redis_client=fake_redis,
        visibility_timeout_seconds=5.0,
    )
    await queue.open()
    ctx.service(JobQueue, queue)

    prog_svc = ProgressServiceImpl(
        ctx,
        backend,
        redis_mode=True,
        redis_client=fake_redis,
    )
    await prog_svc.open()
    ctx.service(ProgressService, prog_svc)

    try:
        yield {
            "ctx": ctx,
            "backend": backend,
            "artifacts": artifacts,
            "queue": queue,
            "progress": prog_svc,
            "redis": fake_redis,
        }
    finally:
        await prog_svc.aclose()
        await queue.aclose()
        await backend.aclose()
        await ctx.dispose()


# -- 1. Basic Protocol Methods ------------------------------------------------


async def test_job_queue_submit_and_status(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]
    backend: StateBackend = harness["backend"]

    payload = {"task": "ocr", "page": 1}
    meta = {"filename": "doc.pdf", "user": "alice"}
    handle: JobHandle = await queue.submit(
        payload, request_meta=meta, input_path="/tmp/doc.pdf"
    )

    assert handle.job_id
    assert handle.status_url == f"/api/process/status/{handle.job_id}"

    # Verify status in backend
    record = await queue.status(handle.job_id)
    assert record is not None
    assert record.job_id == handle.job_id
    assert record.status == "queued"
    assert record.request_meta == meta
    assert record.input_path == "/tmp/doc.pdf"
    assert record.created_at > 0


async def test_job_queue_list_and_pagination(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]

    job_ids = []
    for i in range(5):
        h = await queue.submit({"index": i})
        job_ids.append(h.job_id)
        await asyncio.sleep(0.01)

    # Page 1: limit 3
    page1 = await queue.list_jobs(limit=3, offset=0)
    assert len(page1) == 3

    # Page 2: limit 3, offset 3
    page2 = await queue.list_jobs(limit=3, offset=3)
    assert len(page2) == 2

    # Check order (newest first)
    all_listed = [r.job_id for r in page1 + page2]
    assert all_listed == list(reversed(job_ids))


async def test_job_queue_cancellation(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]

    handle = await queue.submit({"action": "cancel-me"})
    assert queue.is_cancelled(handle.job_id) is False

    # Cancel the queued job
    ok = await queue.cancel(handle.job_id)
    assert ok is True
    assert queue.is_cancelled(handle.job_id) is True

    record = await queue.status(handle.job_id)
    assert record is not None
    assert record.status == "cancelled"

    # Cancelling again on terminal status returns False
    ok2 = await queue.cancel(handle.job_id)
    assert ok2 is False


async def test_job_queue_clear(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]
    fake_redis = harness["redis"]

    for i in range(3):
        await queue.submit({"i": i})

    count = await queue.clear()
    assert count == 3

    jobs_left = await queue.list_jobs()
    assert len(jobs_left) == 0

    # Verify Redis keys cleared
    assert await fake_redis.zcard(KEY_QUEUE) == 0
    assert await fake_redis.zcard(KEY_ACTIVE) == 0


# -- 2. Atomic Lua Claim -------------------------------------------------------


async def test_atomic_claim_fifo_order(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]

    h1 = await queue.submit({"order": 1})
    await asyncio.sleep(0.01)
    h2 = await queue.submit({"order": 2})

    claim1 = await queue.claim()
    assert claim1 is not None
    jid1, payload1 = claim1
    assert jid1 == h1.job_id
    assert payload1 == {"order": 1}

    claim2 = await queue.claim()
    assert claim2 is not None
    jid2, payload2 = claim2
    assert jid2 == h2.job_id
    assert payload2 == {"order": 2}

    # Queue should now be empty
    claim3 = await queue.claim()
    assert claim3 is None


async def test_atomic_claim_skips_cancelled_jobs(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]

    h1 = await queue.submit({"order": 1})
    h2 = await queue.submit({"order": 2})

    # Cancel first job while in queue
    await queue.cancel(h1.job_id)

    # Claim should automatically skip h1 and return h2
    claim = await queue.claim()
    assert claim is not None
    jid, payload = claim
    assert jid == h2.job_id
    assert payload == {"order": 2}

    # Queue should be empty now
    assert await queue.claim() is None


# -- 3. Multi-Worker Race (No Duplicates, Zero Loss) ----------------------------


async def test_multi_worker_race_condition(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]
    fake_redis = harness["redis"]

    total_jobs = 20
    num_workers = 4

    # Enqueue jobs
    job_handles = [await queue.submit({"num": i}) for i in range(total_jobs)]
    expected_job_ids = {h.job_id for h in job_handles}

    claimed_by_worker: dict[int, list[str]] = {w: [] for w in range(num_workers)}
    lock = asyncio.Lock()

    async def worker_loop(worker_id: int) -> None:
        while True:
            claim = await queue.claim(worker_id=f"w-{worker_id}")
            if claim is None:
                break
            jid, _ = claim
            async with lock:
                claimed_by_worker[worker_id].append(jid)
            # Simulate work
            await asyncio.sleep(0.005)
            await queue.complete(jid)

    # Run all workers concurrently
    workers = [asyncio.create_task(worker_loop(w)) for w in range(num_workers)]
    await asyncio.gather(*workers)

    # Aggregate all claimed jobs
    all_claimed: list[str] = []
    for w, jids in claimed_by_worker.items():
        all_claimed.extend(jids)

    # Invariants:
    # 1. Total claimed equals total submitted
    assert len(all_claimed) == total_jobs
    # 2. Exactly-once processing: no duplicates
    assert len(set(all_claimed)) == total_jobs
    # 3. Every submitted job was claimed
    assert set(all_claimed) == expected_job_ids
    # 4. Active and Queue ZSETs in Redis are empty
    assert await fake_redis.zcard(KEY_QUEUE) == 0
    assert await fake_redis.zcard(KEY_ACTIVE) == 0


# -- 4. Visibility Timeout & Stale Job Recovery --------------------------------


async def test_visibility_timeout_recovery(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]
    fake_redis = harness["redis"]

    # Short visibility timeout
    handle = await queue.submit({"resilient": True})
    claim = await queue.claim(worker_id="crasher", visibility_timeout=0.1)
    assert claim is not None
    jid, _ = claim
    assert jid == handle.job_id

    # Active score should be roughly now + 0.1
    active_jobs = await fake_redis.zrange(KEY_ACTIVE, 0, -1)
    assert jid.encode() in active_jobs or jid in active_jobs

    # Worker crashes: does not call complete()
    # Wait for visibility timeout to expire
    await asyncio.sleep(0.15)

    # Run recovery
    recovered = await queue.recover_stale_jobs(max_retries=3)
    assert jid in recovered

    # The job is now requeued in pending queue
    queue_jobs = await fake_redis.zrange(KEY_QUEUE, 0, -1)
    assert jid.encode() in queue_jobs or jid in queue_jobs

    # A healthy worker can now claim it
    healthy_claim = await queue.claim(worker_id="healthy")
    assert healthy_claim is not None
    h_jid, h_payload = healthy_claim
    assert h_jid == jid
    assert h_payload == {"resilient": True}

    await queue.complete(h_jid)
    assert await fake_redis.zcard(KEY_ACTIVE) == 0


async def test_visibility_timeout_retry_exhaustion_fails_job(
    harness: dict[str, Any],
) -> None:
    queue: RedisJobQueue = harness["queue"]
    fake_redis = harness["redis"]

    handle = await queue.submit({"crash_loop": True})

    # Claim and expire repeatedly up to max_retries=2
    for _ in range(2):
        c = await queue.claim(visibility_timeout=0.05)
        assert c is not None
        await asyncio.sleep(0.08)
        rec = await queue.recover_stale_jobs(max_retries=2)
        assert handle.job_id in rec

    # Next claim and timeout should exhaust retries
    c_last = await queue.claim(visibility_timeout=0.05)
    assert c_last is not None
    await asyncio.sleep(0.08)

    rec_last = await queue.recover_stale_jobs(max_retries=2)
    assert handle.job_id in rec_last

    # The job should be failed, evicted from active and queue
    record = await queue.status(handle.job_id)
    assert record is not None
    assert record.status == "error"
    assert "Visibility timeout exceeded" in (record.error or "")

    assert await fake_redis.zcard(KEY_ACTIVE) == 0
    assert await fake_redis.zcard(KEY_QUEUE) == 0


# -- 5. Worker Heartbeat -------------------------------------------------------


async def test_worker_heartbeat(harness: dict[str, Any]) -> None:
    queue: RedisJobQueue = harness["queue"]
    fake_redis = harness["redis"]

    worker_id = "test-worker-alpha"
    await queue.heartbeat(worker_id, ttl_seconds=20)

    key = f"{KEY_HEARTBEAT_PREFIX}{worker_id}"
    val = await fake_redis.get(key)
    assert val is not None

    ttl = await fake_redis.ttl(key)
    assert 0 < ttl <= 20


# -- 6. Progress Pub/Sub Fan-out ----------------------------------------------


class _MockWebSocket:
    def __init__(self) -> None:
        self.sent_frames: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent_frames.append(text)


async def test_progress_pubsub_cross_pod_fanout(harness: dict[str, Any]) -> None:
    prog: ProgressServiceImpl = harness["progress"]
    fake_redis = harness["redis"]

    # Open channel
    handle = await prog.open_channel()
    channel_id = handle.channel_id

    # Attach mock socket
    mock_ws = _MockWebSocket()
    loop = asyncio.get_running_loop()
    prog.attach(channel_id, mock_ws, loop)

    # Another worker/pod publishes to Redis Pub/Sub channel
    frame = {"stage": "aligning", "progress": 0.75}
    await fake_redis.publish(
        f"omniscribe:progress:{channel_id}",
        json.dumps(frame),
    )

    # Allow pubsub listener task to process
    await asyncio.sleep(0.05)

    # Verify frame was received and forwarded to mock WebSocket
    assert len(mock_ws.sent_frames) == 1
    received = json.loads(mock_ws.sent_frames[0].strip())
    assert received["stage"] == "aligning"
    assert received["progress"] == 0.75


# -- 7. Full Worker Execution Flow --------------------------------------------


async def test_full_worker_execution_flow(harness: dict[str, Any]) -> None:
    ctx: Context = harness["ctx"]
    queue: RedisJobQueue = harness["queue"]
    backend: StateBackend = harness["backend"]
    artifacts: ArtifactStore = harness["artifacts"]

    # Register mock runner
    async def sample_runner(req: Any) -> JobOutcome:
        msg = f"Processed {req.get('filename')}"
        return JobOutcome(blob=msg.encode("utf-8"), content_type="text/plain")

    ctx.service(JobRunner, sample_runner)

    handle = await queue.submit({"filename": "invoice.pdf"})
    claim = await queue.claim()
    assert claim is not None
    job_id, payload = claim

    await _execute_job(job_id, payload, ctx, queue, backend, artifacts)

    record = await queue.status(job_id)
    assert record is not None
    assert record.status == "complete"
    assert record.result_artifact_id is not None
    assert record.result_artifact_token is not None

    # Verify result blob in artifacts store
    blob = await artifacts.get(record.result_artifact_id, record.result_artifact_token)
    assert blob is not None
    assert blob.blob == b"Processed invoice.pdf"


async def test_standalone_worker_runner_graceful_drain(harness: dict[str, Any]) -> None:
    import contextlib
    from omniscribe.worker import run_worker

    ctx: Context = harness["ctx"]
    queue: RedisJobQueue = harness["queue"]

    # Register mock runner
    async def sample_runner(req: Any) -> JobOutcome:
        return JobOutcome(blob=b"Worker Result", content_type="text/plain")

    ctx.service(JobRunner, sample_runner)

    # Launch worker runner as background task
    worker_task = asyncio.create_task(
        run_worker(
            concurrency=2,
            poll_interval=0.05,
            drain_timeout=2.0,
            ctx=ctx,
        )
    )

    # Submit 3 jobs
    handles = [await queue.submit({"job_idx": i}) for i in range(3)]

    # Wait for all to complete
    for h in handles:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            rec = await queue.status(h.job_id)
            if rec and rec.status == "complete":
                break
            await asyncio.sleep(0.05)
        rec = await queue.status(h.job_id)
        assert rec is not None and rec.status == "complete"

    # Cancel worker task to trigger graceful exit
    worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task
