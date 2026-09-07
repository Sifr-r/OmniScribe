"""Tests for OCRServiceImpl event pruning and wait_for_events concurrency."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from omniscribe.plugins.jobs import JobCompleted, JobQueued, JobStarted
from omniscribe.plugins.ocr.service import OCRServiceImpl
from omniscribe.plugins.progress import ProgressFrame


def _bare_service(max_buffered_jobs: int = 500) -> OCRServiceImpl:
    """Construct a minimal OCRServiceImpl with only event bookkeeping structures."""
    service = OCRServiceImpl.__new__(OCRServiceImpl)
    service._event_buffers = {}
    service._event_notify = {}
    service._done_jobs = set()
    service._submission_to_job = {}
    service._max_buffered_jobs = max_buffered_jobs
    return service


async def test_record_event_bounds_buffers_done_jobs_and_submissions() -> None:
    """Test that generating excess jobs/events via record_event bounds structures.

    Smell 4.19: _prune_events_if_needed delegates directly to prune(limit).
    Both _event_buffers, _done_jobs, and _submission_to_job must be kept
    bounded to max_buffered_jobs.
    """
    limit = 5
    service = _bare_service(max_buffered_jobs=limit)

    for i in range(15):
        job_id = f"job_{i}"
        sub_id = f"sub_{i}"
        service._submission_to_job[sub_id] = job_id

        await service.record_event(JobQueued(job_id=job_id))
        await service.record_event(JobStarted(job_id=job_id))
        await service.record_event(
            ProgressFrame(
                job_id=job_id,
                channel_id=f"ch_{i}",
                frame={"percent": 50, "stage": "ocr"},
            )
        )
        await service.record_event(
            JobCompleted(
                job_id=job_id,
                artifact_id=f"art_{i}",
                artifact_token=f"tok_{i}",
            )
        )

    # All structures must be bounded to max_buffered_jobs
    assert len(service._event_buffers) <= limit
    assert len(service._done_jobs) <= limit
    assert len(service._submission_to_job) <= limit
    assert len(service._event_notify) <= limit

    # The remaining buffers should be the newest 5 jobs (job_10 .. job_14)
    expected_jobs = [f"job_{i}" for i in range(10, 15)]
    assert list(service._event_buffers.keys()) == expected_jobs
    assert service._done_jobs == set(expected_jobs)


async def test_explicit_prune_with_custom_limit() -> None:
    """Test calling prune(limit) explicitly with custom bounds."""
    service = _bare_service(max_buffered_jobs=100)

    for i in range(20):
        job_id = f"job_{i}"
        service._submission_to_job[f"sub_{i}"] = job_id
        await service.record_event(JobStarted(job_id=job_id))
        await service.record_event(
            JobCompleted(
                job_id=job_id,
                artifact_id=f"art_{i}",
                artifact_token=f"tok_{i}",
            )
        )

    assert len(service._event_buffers) == 20
    assert len(service._done_jobs) == 20
    assert len(service._submission_to_job) == 20

    # Prune to limit of 7
    pruned = service.prune(max_buffered_jobs=7)
    assert pruned == 13
    assert len(service._event_buffers) == 7
    assert len(service._done_jobs) <= 7
    assert len(service._submission_to_job) <= 7
    assert len(service._event_notify) <= 7

    # Explicit prune to 0 drops everything
    pruned_all = service.prune(max_buffered_jobs=0)
    assert pruned_all == 7
    assert len(service._event_buffers) == 0
    assert len(service._done_jobs) == 0
    assert len(service._submission_to_job) == 0
    assert len(service._event_notify) == 0


async def test_explicit_prune_default_limit() -> None:
    """Test calling prune() with no arguments defaults to self._max_buffered_jobs."""
    service = _bare_service(max_buffered_jobs=4)

    for i in range(10):
        job_id = f"job_{i}"
        service._submission_to_job[f"sub_{i}"] = job_id
        # Directly insert into dictionaries without triggering _prune_events_if_needed
        service._event_buffers[job_id] = []  # type: ignore[assignment]
        service._done_jobs.add(job_id)
        service._event_notify[job_id] = asyncio.Event()

    assert len(service._event_buffers) == 10
    pruned = service.prune()
    assert pruned == 6
    assert len(service._event_buffers) == 4
    assert len(service._done_jobs) <= 4
    assert len(service._submission_to_job) <= 4
    assert len(service._event_notify) <= 4


async def test_wait_for_events_returns_immediately_on_done_jobs_no_deadlock() -> None:
    """Test that wait_for_events returns immediately if job is already done.

    Guards against deadlocks where a terminal job is waited on without new events.
    """
    service = _bare_service()
    job_id = "terminal_job"
    service._done_jobs.add(job_id)

    # Must complete almost immediately without timeout or hanging
    await asyncio.wait_for(service.wait_for_events(job_id), timeout=0.2)


async def test_wait_for_events_returns_immediately_if_events_arrived_just_before_waiting() -> None:
    """Test that wait_for_events consumes events that arrived just before wait.

    Smell 4.18: Guard against missed wake-ups / event flapping.
    """
    service = _bare_service()
    job_id = "pre_event_job"

    await service.record_event(JobStarted(job_id=job_id))
    # notify is set. wait_for_events must return immediately
    await asyncio.wait_for(service.wait_for_events(job_id), timeout=0.2)

    # Backlog has the event
    backlog = service.event_backlog(job_id)
    assert len(backlog) == 1
    assert backlog[0]["event"] == "job_started"

    # Calling wait_for_events again should wait (notify was cleared)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(service.wait_for_events(job_id), timeout=0.05)


async def test_wait_for_events_wakes_up_when_event_arrives_while_waiting() -> None:
    """Test that wait_for_events wakes up when an event arrives during wait."""
    service = _bare_service()
    job_id = "in_flight_job"

    waiter = asyncio.create_task(service.wait_for_events(job_id))
    await asyncio.sleep(0)  # park on wait()

    await service.record_event(JobStarted(job_id=job_id))
    await asyncio.wait_for(waiter, timeout=0.5)

    backlog = service.event_backlog(job_id)
    assert len(backlog) == 1
    assert backlog[0]["event"] == "job_started"


async def test_wait_for_events_rapid_burst_and_terminal_event_no_missed_events() -> None:
    """Test rapid event burst and terminal event are completely consumed without flapping."""
    service = _bare_service()
    job_id = "burst_job"

    collected_events: list[dict[str, Any]] = []
    cursor = 0

    async def consumer() -> None:
        nonlocal cursor
        while True:
            for entry in service.event_backlog(job_id):
                seq = entry["seq"]
                if seq <= cursor:
                    continue
                cursor = seq
                collected_events.append(entry)
            if service.is_done(job_id):
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(service.wait_for_events(job_id), timeout=0.5)

    consumer_task = asyncio.create_task(consumer())
    await asyncio.sleep(0)

    # Emit burst of 30 progress events
    for i in range(30):
        await service.record_event(
            ProgressFrame(
                job_id=job_id,
                channel_id="c1",
                frame={"percent": i, "stage": "ocr"},
            )
        )
        if i % 5 == 0:
            await asyncio.sleep(0)  # give consumer chances to interleave

    # Emit terminal event
    await service.record_event(
        JobCompleted(
            job_id=job_id,
            artifact_id="art_burst",
            artifact_token="tok_burst",
        )
    )

    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert len(collected_events) == 31
    assert collected_events[-1]["event"] == "job_completed"
