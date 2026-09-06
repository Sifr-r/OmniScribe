"""OCR events: re-export surface keeps the right domains and fields."""

from __future__ import annotations

import asyncio

from omniscribe.harness.events import AgentEvent, SessionEvent
from omniscribe.plugins.ocr import events


def test_lifecycle_events_are_session_events() -> None:
    queued = events.JobQueued(job_id="j1")
    # 2026-08-29 audit C-3 / H-3: ``artifact_token`` is the out-of-band
    # delivery channel for the async result token (status endpoint no
    # longer returns it). See ``JobCompleted`` in plugins/jobs.py.
    completed = events.JobCompleted(job_id="j1", artifact_id="a1", artifact_token="t1")
    failed = events.JobFailed(job_id="j1", error="boom")
    cancelled = events.JobCancelled(job_id="j1")
    for event in (queued, completed, failed, cancelled):
        assert isinstance(event, SessionEvent)
    assert completed.artifact_id == "a1"
    assert completed.artifact_token == "t1"
    assert failed.error == "boom"


def test_live_frames_are_agent_events() -> None:
    started = events.JobStarted(job_id="j1")
    frame = events.ProgressFrame(
        job_id="j1", channel_id="c1", frame={"percent": 42, "stage": "ocr"}
    )
    assert isinstance(started, AgentEvent)
    assert isinstance(frame, AgentEvent)
    assert frame.channel_id == "c1"
    assert frame.frame == {"percent": 42, "stage": "ocr"}


# ---------------------------------------------------------------------------
# Audit 5.4: SSE event-flap regression (see pedantic 4.18)
# ---------------------------------------------------------------------------


def _bare_service():
    """Construct a minimal OCRServiceImpl with only the SSE bookkeeping attrs.

    Bypasses the real ``__init__`` (which would build a queue, an
    ArtifactStore, an AsyncOpenAI client, and load RuntimeSettings) so the
    test can drive ``record_event`` / ``event_backlog`` / ``wait_for_events``
    directly.
    """
    from omniscribe.plugins.ocr.service import OCRServiceImpl

    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._event_buffers = {}
    service._event_notify = {}
    service._done_jobs = set()
    service._max_buffered_jobs = 500
    return service


async def test_record_event_appends_to_deque_and_wakes_waiter() -> None:
    """Audit 5.4 (baseline): ``record_event`` appends to the per-job deque
    and signals the waiter's ``asyncio.Event``. The consumer reads via
    ``event_backlog``; both the deque and the wake signal fire.
    """
    service = _bare_service()
    job_id = "j1"

    waiter = asyncio.create_task(service.wait_for_events(job_id))
    # Yield once so the waiter parks on notify.wait() before the event fires.
    await asyncio.sleep(0)
    await service.record_event(events.JobStarted(job_id=job_id))

    # The waiter must wake (the Event was set) and the deque must contain
    # the entry (data is not lost even if the wake race fires).
    await asyncio.wait_for(waiter, timeout=1.0)
    backlog = service.event_backlog(job_id)
    assert len(backlog) == 1
    assert backlog[0]["event"] == "job_started"
    assert backlog[0]["data"]["job_id"] == job_id


async def test_rapid_record_events_do_not_lose_entries() -> None:
    """Audit 5.4 (flap regression): when 200 events are recorded in a
    tight loop, the per-job deque must contain every entry. The
    ``asyncio.Event`` clear-on-wake race (pedantic 4.18) can lose wake
    notifications between ``wait()`` returning and ``clear()`` running;
    the deque buffer is the authoritative replay log the SSE consumer
    reads, so a lost wake must never translate to a lost frame.
    """
    service = _bare_service()
    job_id = "j1"

    # Pre-arm the waiter so it is parked on notify.wait() before the burst.
    waiter = asyncio.create_task(service.wait_for_events(job_id))
    await asyncio.sleep(0)

    burst_size = 200
    for i in range(burst_size):
        await service.record_event(
            events.ProgressFrame(
                job_id=job_id,
                channel_id="c1",
                frame={"percent": i, "stage": "ocr"},
            )
        )

    # Wait for the waiter to drain at least one wake cycle.
    await asyncio.wait_for(waiter, timeout=1.0)

    backlog = service.event_backlog(job_id)
    assert len(backlog) == burst_size
    percents = [entry["data"]["percent"] for entry in backlog]
    assert percents == list(range(burst_size))


async def test_backlog_entries_carry_strictly_increasing_seq_across_rotation() -> None:
    """SSE cursor correctness: every backlog entry carries a per-job ``seq``
    that keeps increasing even after the ``maxlen`` deque rotates (oldest
    entries evicted). Consumers use ``seq`` as their cursor, so a rotation
    must never reset or reuse sequence numbers.
    """
    service = _bare_service()
    job_id = "j1"

    for i in range(600):
        await service.record_event(
            events.ProgressFrame(
                job_id=job_id,
                channel_id="c1",
                frame={"percent": i, "stage": "ocr"},
            )
        )

    backlog = service.event_backlog(job_id)
    assert len(backlog) == 500  # maxlen enforced
    seqs = [entry["seq"] for entry in backlog]
    assert seqs[0] == 101  # 100 oldest evicted by rotation
    assert seqs == list(range(101, 601))


async def test_sse_consumer_delivers_events_appended_after_full_backlog_seen() -> None:
    """SSE cursor correctness: a single live SSE connection that has seen a
    full 500-entry backlog must still receive the next events after a burst
    rotates the deque. The old index-based cursor compared against the
    snapshot length, so after rotation the cursor exceeded ``len(backlog)``
    and every later event was silently skipped.
    """
    from omniscribe.plugins.ocr.plugin import iter_sse_events

    service = _bare_service()
    job_id = "j1"

    for i in range(500):
        await service.record_event(
            events.ProgressFrame(
                job_id=job_id,
                channel_id="c1",
                frame={"percent": i, "stage": "ocr"},
            )
        )

    gen = iter_sse_events(service, job_id, 0.01)

    async def next_event() -> str:
        while True:
            chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
            if not chunk.startswith(":"):
                return chunk

    seen = [await next_event() for _ in range(500)]
    assert '"percent": 0' in seen[0]
    assert '"percent": 499' in seen[-1]

    # Burst rotates the deque: the 500 oldest entries are evicted and 10
    # new ones appended. The live connection must receive all 10.
    for i in range(500, 510):
        await service.record_event(
            events.ProgressFrame(
                job_id=job_id,
                channel_id="c1",
                frame={"percent": i, "stage": "ocr"},
            )
        )

    for i in range(500, 510):
        chunk = await next_event()
        assert f'"percent": {i}' in chunk
        seen.append(chunk)

    assert len(seen) == 510
    await gen.aclose()
