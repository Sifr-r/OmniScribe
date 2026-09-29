"""Jobs plugin: single-worker queue lifecycle, cancel, events, shutdown."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from omniscribe.harness.context import Context
from omniscribe.harness.errors import ServiceNotFoundError
from omniscribe.harness.events import Event
from omniscribe.plugins import artifacts as art
from omniscribe.plugins import jobs
from omniscribe.plugins import state_backend as sb
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import (
    JobCancelled,
    JobCompleted,
    JobFailed,
    JobOutcome,
    JobPayload,
    JobQueue,
    JobQueued,
    JobRunner,
    JobStarted,
)
from omniscribe.plugins.state_backend import JobRecord


async def _boot(runner: JobRunner | None = None) -> Context:
    ctx = Context()
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "memory"})
    await ctx.plugin(art.ArtifactsPlugin(), config={})
    if runner is not None:
        ctx.service(JobRunner, runner)
    await ctx.plugin(jobs.JobsPlugin(), config={})
    return ctx


async def _wait_status(
    queue: JobQueue, job_id: str, status: str, *, timeout: float = 5.0
) -> JobRecord:
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = await queue.status(job_id)
        if record is not None and record.status == status:
            return record
        await asyncio.sleep(0.01)
    record = await queue.status(job_id)
    raise AssertionError(f"job {job_id} never reached {status!r}; last={record}")


async def test_submit_returns_handle_and_full_lifecycle() -> None:
    gate = asyncio.Event()

    async def runner(request: Any) -> JobOutcome:
        await gate.wait()
        return JobOutcome(blob=b"result-pdf", content_type="application/pdf")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)
    handle = await queue.submit({"page": 1}, request_meta={"filename": "a.pdf"})
    assert handle.status_url == f"/api/process/status/{handle.job_id}"
    await _wait_status(queue, handle.job_id, "running")
    gate.set()
    record = await _wait_status(queue, handle.job_id, "complete")
    assert record.request_meta == {"filename": "a.pdf"}
    assert record.result_artifact_id and record.result_artifact_token
    store = ctx.inject(ArtifactStore)
    blob = await store.get(record.result_artifact_id, record.result_artifact_token)
    assert blob is not None and blob.blob == b"result-pdf"
    await ctx.dispose()


async def test_second_job_stays_queued_until_worker_is_free() -> None:
    gate = asyncio.Event()

    async def runner(request: Any) -> JobOutcome:
        await gate.wait()
        return JobOutcome(blob=b"x", content_type="t/t")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)
    first = await queue.submit({"n": 1})
    await _wait_status(queue, first.job_id, "running")
    second = await queue.submit({"n": 2})
    record = await queue.status(second.job_id)
    assert record is not None and record.status == "queued"
    gate.set()
    await _wait_status(queue, first.job_id, "complete")
    await _wait_status(queue, second.job_id, "complete")
    await ctx.dispose()


async def test_raising_runner_marks_job_error() -> None:
    async def runner(request: Any) -> JobOutcome:
        raise RuntimeError("vlm endpoint down")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)
    handle = await queue.submit({})
    record = await _wait_status(queue, handle.job_id, "error")
    assert record.error is not None and "vlm endpoint down" in record.error
    assert record.result_artifact_id is None
    await ctx.dispose()


async def test_cancel_queued_job_before_it_runs() -> None:
    gate = asyncio.Event()
    ran: list[Any] = []

    async def runner(request: Any) -> JobOutcome:
        await gate.wait()
        ran.append(request)
        return JobOutcome(blob=b"x", content_type="t/t")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)
    first = await queue.submit({"n": 1})
    await _wait_status(queue, first.job_id, "running")
    second = await queue.submit({"n": 2})
    assert await queue.cancel(second.job_id) is True
    record = await queue.status(second.job_id)
    assert record is not None and record.status == "cancelled"
    gate.set()
    await _wait_status(queue, first.job_id, "complete")
    # Phase 3.7 (4.3, 2026-09-05): the previous ``await asyncio.sleep(0.05)
    # # let the worker drain the queue`` was a flaky magic-number sleep.
    # ``_wait_status`` above already polled the status endpoint until
    # the worker reported ``complete``, which means the worker has
    # finished its iteration of the loop (including ``task_done()``).
    # The second job's status is already ``cancelled`` from L126 — no
    # drain wait is needed.
    assert (await queue.status(second.job_id)).status == "cancelled"
    assert ran == [{"n": 1}]  # the cancelled job never ran
    assert await queue.cancel(second.job_id) is False  # terminal
    assert await queue.cancel("unknown") is False
    await ctx.dispose()


async def test_list_and_clear_delegate_to_state() -> None:
    async def runner(request: Any) -> JobOutcome:
        return JobOutcome(blob=b"x", content_type="t/t")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)
    handle1 = await queue.submit({}, request_meta={"filename": "a.pdf"})
    await _wait_status(queue, handle1.job_id, "complete")
    # Phase 3.7 (4.3, 2026-09-05): the previous ``asyncio.sleep(0.02)``
    # here was load-bearing in a subtle way. The memory backend's
    # ``list_jobs`` orders by ``created_at DESC``; on Windows,
    # ``time.time()`` resolution is ~15 ms, so two back-to-back
    # ``submit()`` calls can produce identical timestamps, which the
    # stable sort then breaks (handle1 stays first). The remaining
    # 10 ms sleep is a deliberate order-enforcement, not a drain
    # wait. If the test ever needs to be sub-15-ms, the real fix is
    # a monotonic ``created_at_ns`` field in ``JobRecord`` — out of
    # scope for Phase 3.
    await asyncio.sleep(0.01)
    handle2 = await queue.submit({}, request_meta={"filename": "b.pdf"})
    await _wait_status(queue, handle2.job_id, "complete")

    listed = await queue.list_jobs(limit=1, offset=0)
    assert len(listed) == 1
    assert listed[0].job_id == handle2.job_id

    listed_offset = await queue.list_jobs(limit=1, offset=1)
    assert len(listed_offset) == 1
    assert listed_offset[0].job_id == handle1.job_id

    assert await queue.clear() == 2
    assert await queue.list_jobs() == []
    await ctx.dispose()


async def test_lifecycle_events_emitted() -> None:
    async def runner(request: Any) -> JobOutcome:
        return JobOutcome(blob=b"x", content_type="t/t")

    ctx = await _boot(runner)
    seen: dict[str, list[Event]] = {
        "queued": [],
        "started": [],
        "completed": [],
        "failed": [],
        "cancelled": [],
    }

    def _collect(bucket: str) -> Any:
        def _handler(event: Event) -> None:
            seen[bucket].append(event)

        return _handler

    ctx.on(JobQueued, _collect("queued"))
    ctx.on(JobStarted, _collect("started"))
    ctx.on(JobCompleted, _collect("completed"))
    ctx.on(JobFailed, _collect("failed"))
    ctx.on(JobCancelled, _collect("cancelled"))
    queue = ctx.inject(JobQueue)
    handle = await queue.submit({})
    record = await _wait_status(queue, handle.job_id, "complete")
    assert [e.job_id for e in seen["queued"]] == [handle.job_id]  # type: ignore[attr-defined]
    assert [e.job_id for e in seen["started"]] == [handle.job_id]  # type: ignore[attr-defined]
    completed = seen["completed"]
    assert len(completed) == 1
    assert isinstance(completed[0], JobCompleted)
    assert completed[0].artifact_id == record.result_artifact_id
    assert seen["failed"] == [] and seen["cancelled"] == []
    await ctx.dispose()


async def test_shutdown_marks_pending_jobs_cancelled() -> None:
    gate = asyncio.Event()

    async def runner(request: Any) -> JobOutcome:
        await gate.wait()
        return JobOutcome(blob=b"x", content_type="t/t")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)
    first = await queue.submit({"n": 1})
    await _wait_status(queue, first.job_id, "running")
    second = await queue.submit({"n": 2})
    await ctx.dispose()  # effect: queue.shutdown()
    assert (await queue.status(second.job_id)).status == "cancelled"


async def test_shutdown_cancels_queued_jobs_beyond_one_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pedantic review 1.6: shutdown must cancel ALL queued jobs, not
    only the newest page (list_jobs orders created_at DESC)."""
    ctx = await _boot()
    try:
        queue = ctx.inject(JobQueue)
        backend = ctx.inject(sb.StateBackend)
        for i in range(5):
            await backend.upsert_job(JobRecord(job_id=f"j{i}", status="queued"))

        real_list = backend.list_jobs

        async def two_per_page(**kwargs: Any) -> list[JobRecord]:
            # Emulate a 2-row page regardless of the caller's limit so a
            # single bounded list_jobs call cannot see the whole queue.
            # Forwards offset so the paginated shutdown walks every page.
            kwargs["limit"] = 2
            return await real_list(**kwargs)  # type: ignore[no-any-return]

        monkeypatch.setattr(backend, "list_jobs", two_per_page)

        await queue.shutdown()

        records = await real_list(limit=100)
        assert {r.status for r in records} == {"cancelled"}
    finally:
        await ctx.dispose()


async def test_shutdown_cancels_thousands_of_queued_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 5.5: shutdown must walk the full queued list, not just the
    visible page. With 1500 queued jobs (well past the previous 1000-job
    stress) the paginated walk must cancel every record in a bounded
    time budget.
    """
    ctx = await _boot()
    try:
        queue = ctx.inject(JobQueue)
        backend = ctx.inject(sb.StateBackend)
        total = 1500
        for i in range(total):
            await backend.upsert_job(JobRecord(job_id=f"j{i}", status="queued"))

        real_list = backend.list_jobs

        async def small_page(**kwargs: Any) -> list[JobRecord]:
            kwargs["limit"] = 100  # tiny page so we exercise many iterations
            return await real_list(**kwargs)  # type: ignore[no-any-return]

        monkeypatch.setattr(backend, "list_jobs", small_page)

        start = time.monotonic()
        await queue.shutdown()
        elapsed = time.monotonic() - start

        records = await real_list(limit=total + 10)
        statuses = {r.status for r in records}
        assert statuses == {"cancelled"}, f"expected all cancelled, got {statuses}"
        # 15 small pages of 100 rows; generous bound to keep CI stable.
        assert elapsed < 10.0, f"shutdown took {elapsed:.2f}s for {total} jobs"
    finally:
        await ctx.dispose()


async def test_missing_artifact_store_fails_loud() -> None:
    ctx = Context()
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "memory"})
    with pytest.raises(ServiceNotFoundError):
        await ctx.plugin(jobs.JobsPlugin(), config={})
    await ctx.dispose()


# ---------------------------------------------------------------------------
# Pedantic 9.15 / 9.16: job-runner Protocols + terminal status set
# ---------------------------------------------------------------------------


def test_runner_protocols_share_contract() -> None:
    """Pedantic 9.15: the three runner Protocols (``JobRunner``,
    ``TranslationJobRunner``, ``GlossaryJobRunner``) are
    intentionally distinct DI keys but share a single structural
    contract (``_JobRunnerContract``). They remain distinct
    type identities so the harness can route to the right
    producer via the ``runner_protocol`` class attribute on the
    payload, but the duplicate ``__call__`` method lives in
    only one place.
    """
    from omniscribe.plugins.jobs import (
        GlossaryJobRunner,
        JobRunner,
        TranslationJobRunner,
        _JobRunnerContract,
    )

    # All three satisfy the shared contract — they have the same
    # callable shape, even though they are distinct types.
    assert JobRunner.__mro__[0] is not TranslationJobRunner
    assert TranslationJobRunner.__mro__[0] is not GlossaryJobRunner
    assert _JobRunnerContract in JobRunner.__mro__
    assert _JobRunnerContract in TranslationJobRunner.__mro__
    assert _JobRunnerContract in GlossaryJobRunner.__mro__


def test_terminal_job_statuses_derived_from_literal() -> None:
    """Pedantic 9.16: a single source of truth for the terminal
    job-status set, derived from the ``JobStatus`` literal in
    ``state_backend``. Both ``plugins/jobs.py`` and
    ``plugins/ocr/service.py`` import it; a new terminal status
    needs one edit.
    """
    from omniscribe.plugins.jobs import _TERMINAL_STATUSES
    from omniscribe.plugins.ocr.service import _TERMINAL_QUEUE_STATUSES
    from omniscribe.plugins.state_backend import (
        TERMINAL_JOB_STATUSES,
        JobStatus,
        get_args,
    )

    # The local aliases point at the same canonical set.
    assert _TERMINAL_STATUSES is TERMINAL_JOB_STATUSES
    assert _TERMINAL_QUEUE_STATUSES is TERMINAL_JOB_STATUSES

    # And the canonical set is exactly the JobStatus literal minus
    # the non-terminal (queued / running) values.
    expected = frozenset(get_args(JobStatus)) - frozenset({"queued", "running"})
    assert expected == TERMINAL_JOB_STATUSES
    assert frozenset({"complete", "error", "cancelled"}) == TERMINAL_JOB_STATUSES


async def test_job_payload_protocol_and_runner_dispatch() -> None:
    """Wave 6 Finding 9.14: multi-producer runner formalization.

    Verifies that:
    a) A class with a `runner_protocol` attribute conforms to `isinstance(instance, JobPayload)`.
    b) A payload declaring a custom runner protocol is dispatched to that custom runner
       protocol instance registered in `Context`.
    c) An untagged payload falls back to the default `JobRunner`.
    """
    from typing import Protocol, runtime_checkable

    from omniscribe.plugins.jobs import _JobRunnerContract

    @runtime_checkable
    class CustomRunnerProtocol(_JobRunnerContract, Protocol):
        """Mock runner protocol for a distinct producer."""

    class TaggedPayload:
        runner_protocol = CustomRunnerProtocol

        def __init__(self, message: str) -> None:
            self.message = message

    class UntaggedPayload:
        def __init__(self, count: int) -> None:
            self.count = count

    # a) Structural conformance to JobPayload
    tagged_instance = TaggedPayload("hello")
    untagged_instance = UntaggedPayload(10)
    assert isinstance(tagged_instance, JobPayload)
    assert not isinstance(untagged_instance, JobPayload)
    assert not isinstance({"page": 1}, JobPayload)
    assert not isinstance(None, JobPayload)

    # b & c) Dispatch verification
    custom_calls: list[Any] = []
    default_calls: list[Any] = []

    async def custom_runner(request: Any) -> JobOutcome:
        custom_calls.append(request)
        return JobOutcome(blob=b"custom-output", content_type="text/plain")

    async def default_runner(request: Any) -> JobOutcome:
        default_calls.append(request)
        return JobOutcome(blob=b"default-output", content_type="text/plain")

    ctx = Context()
    await ctx.plugin(sb.StateBackendPlugin(), config={"backend": "memory"})
    await ctx.plugin(art.ArtifactsPlugin(), config={})
    ctx.service(JobRunner, default_runner)
    ctx.service(CustomRunnerProtocol, custom_runner)
    await ctx.plugin(jobs.JobsPlugin(), config={})

    try:
        queue = ctx.inject(JobQueue)

        # b) Custom payload dispatched to CustomRunnerProtocol
        handle_custom = await queue.submit(tagged_instance)
        record_custom = await _wait_status(queue, handle_custom.job_id, "complete")
        assert len(custom_calls) == 1
        assert custom_calls[0] is tagged_instance
        assert len(default_calls) == 0
        assert record_custom.result_artifact_id is not None

        # c) Untagged payload falls back to default JobRunner
        handle_untagged = await queue.submit(untagged_instance)
        record_untagged = await _wait_status(queue, handle_untagged.job_id, "complete")
        assert len(default_calls) == 1
        assert default_calls[0] is untagged_instance
        assert len(custom_calls) == 1
        assert record_untagged.result_artifact_id is not None

        # Untagged raw dict also falls back to default JobRunner
        handle_dict = await queue.submit({"raw": "payload"})
        await _wait_status(queue, handle_dict.job_id, "complete")
        assert len(default_calls) == 2
        assert default_calls[1] == {"raw": "payload"}
        assert len(custom_calls) == 1
    finally:
        await ctx.dispose()


async def test_tagged_runner_missing_is_explicit_in_both_queues() -> None:
    from omniscribe.worker import _resolve_runner as resolve_worker_runner

    class MissingRunner:
        pass

    class TaggedPayload:
        runner_protocol = MissingRunner

    async def default_runner(request: Any) -> JobOutcome:
        return JobOutcome(blob=b"default", content_type="text/plain")

    ctx = await _boot(default_runner)
    try:
        queue = ctx.inject(JobQueue)
        for resolve in (
            queue._resolve_runner,
            lambda payload: resolve_worker_runner(payload, ctx),
        ):
            with pytest.raises(
                ServiceNotFoundError, match=r"MissingRunner.*TaggedPayload"
            ):
                resolve(TaggedPayload())
            assert resolve({"ocr": True}) is default_runner

        override = jobs.InMemoryJobQueue(
            ctx,
            ctx.inject(sb.StateBackend),
            ctx.inject(ArtifactStore),
            runner=default_runner,
        )
        assert override._resolve_runner(TaggedPayload()) is default_runner
    finally:
        await ctx.dispose()


async def test_inmemory_cancel_queued_cleans_up_staged_input() -> None:
    gate = asyncio.Event()

    async def runner(request: Any) -> JobOutcome:
        await gate.wait()
        return JobOutcome(blob=b"out", content_type="text/plain")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)

    work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-"))
    test_file = work_dir / "input.pdf"
    test_file.write_bytes(b"%PDF-test")

    try:
        # First job keeps worker busy
        _ = await queue.submit({"busy": True})
        # Second job remains queued
        second = await queue.submit({"file": True}, input_path=str(test_file))
        assert work_dir.exists()

        assert await queue.cancel(second.job_id) is True
        # Staged upload should be cleaned up immediately on cancel
        assert not work_dir.exists()
    finally:
        gate.set()
        await ctx.dispose()
        shutil.rmtree(work_dir, ignore_errors=True)


async def test_inmemory_shutdown_cleans_up_queued_staged_input() -> None:
    gate = asyncio.Event()

    async def runner(request: Any) -> JobOutcome:
        await gate.wait()
        return JobOutcome(blob=b"out", content_type="text/plain")

    ctx = await _boot(runner)
    queue = ctx.inject(JobQueue)

    work_dir = Path(tempfile.mkdtemp(prefix="omniscribe-ocr-"))
    test_file = work_dir / "input.pdf"
    test_file.write_bytes(b"%PDF-test")

    try:
        # First job keeps worker busy
        await queue.submit({"busy": True})
        # Second job remains queued
        _ = await queue.submit({"file": True}, input_path=str(test_file))
        assert work_dir.exists()

        await queue.shutdown()
        # Staged upload should be cleaned up during shutdown pagination
        assert not work_dir.exists()
    finally:
        gate.set()
        await ctx.dispose()
        shutil.rmtree(work_dir, ignore_errors=True)
