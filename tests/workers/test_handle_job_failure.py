"""Focused unit tests for ``_handle_job_failure`` ``lease_owner=None`` contract.

Background
----------
``tests/plugins/test_jobs_redis.py`` exercises ``_handle_job_failure`` with a real
``RedisJobQueue`` via ``fakeredis`` and pins the lease-loss (stale-owner) path
plus the full success path. The ``lease_owner=None`` case, used by the
in-process ``JobRunner`` plugin path (no Redis lease), was only exercised
indirectly.

These tests pin down the ``lease_owner=None`` contract using plain mocks so the
behaviour is regression-protected without spinning up a Redis harness.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from omniscribe.core.errors import redact_exception
from omniscribe.harness.context import Context
from omniscribe.plugins.jobs import JobCancelled, JobFailed
from omniscribe.plugins.jobs_redis import RedisJobQueue
from omniscribe.plugins.state_backend import JobRecord
from omniscribe.plugins.state_backend_types import StateBackend
from omniscribe.worker import _handle_job_failure


def _make_ctx() -> MagicMock:
    """Build a Context mock with ``emit`` overridden as ``AsyncMock``."""
    ctx = MagicMock(spec=Context)
    ctx.emit = AsyncMock()
    return ctx


def _make_queue(*, fail_returns: bool = True, is_cancelled: bool = False) -> MagicMock:
    """Build a ``RedisJobQueue`` mock with the methods ``_handle_job_failure`` calls."""
    queue = MagicMock(spec=RedisJobQueue)
    # ``owns_lease`` is async; must not be called when ``lease_owner`` is ``None``.
    queue.owns_lease = AsyncMock()
    # ``is_cancelled`` is a sync method on ``RedisJobQueue``.
    queue.is_cancelled = MagicMock(return_value=is_cancelled)
    # ``fail`` is async; returns ``True`` to indicate the lease was still ours.
    queue.fail = AsyncMock(return_value=fail_returns)
    return queue


def _make_backend(*, current_status: str = "running") -> tuple[MagicMock, JobRecord]:
    """Build a ``StateBackend`` mock with a pre-populated running ``JobRecord``."""
    record = JobRecord(job_id="job-handle-1", status=current_status)  # type: ignore[arg-type]
    backend = MagicMock(spec=StateBackend)
    backend.get_job = AsyncMock(return_value=record)
    backend.upsert_job = AsyncMock()
    return backend, record


async def test_handle_job_failure_with_lease_owner_none_proceeds_without_lease_check() -> (
    None
):
    """``lease_owner=None`` skips the lease check and persists the failure."""
    job_id = "job-handle-1"
    exc = RuntimeError("boom")

    ctx = _make_ctx()
    queue = _make_queue()
    backend, _ = _make_backend(current_status="running")

    await _handle_job_failure(
        job_id,
        exc,
        ctx,
        queue,
        backend,
        lease_owner=None,
    )

    # Core contract: no Redis lease probe when the caller already owns the
    # in-process runner (lease_owner is None). This is the contract that
    # protects the in-process JobRunner plugin path from unnecessary Redis I/O.
    queue.owns_lease.assert_not_called()

    expected_err = redact_exception(exc)

    # ``queue.fail`` receives the redacted message and the same ``lease_owner``.
    queue.fail.assert_awaited_once_with(job_id, error=expected_err, lease_owner=None)

    # ``backend.upsert_job`` receives a record with ``status="error"`` and the
    # redacted message.
    assert backend.upsert_job.await_count == 1
    upsert_args = backend.upsert_job.await_args
    assert upsert_args is not None
    persisted = upsert_args.args[0]
    assert isinstance(persisted, JobRecord)
    assert persisted.status == "error"
    assert persisted.error == expected_err

    # ``JobFailed`` event is emitted with the redacted message.
    assert ctx.emit.await_count == 1
    event = ctx.emit.await_args.args[0]
    assert isinstance(event, JobFailed)
    assert event.job_id == job_id
    assert event.error == expected_err


async def test_handle_job_failure_with_lease_owner_none_does_not_emit_cancelled_for_non_cancelled_exc() -> (
    None
):
    """With ``lease_owner=None`` and a non-cancelled exception, no ``JobCancelled`` event fires.

    Contract ambiguity note
    -----------------------
    The current implementation derives ``is_cancelled`` from
    ``queue.is_cancelled(job_id) or "cancelled" in exc.__class__.__name__.lower()``.
    In the ``lease_owner=None`` (in-process) path the queue's ``is_cancelled``
    check may not be meaningful, so a ``cancelled``-named exception class could
    still trip the cancellation branch even when no cooperative cancel was
    requested. This test pins the negative case: a regular ``RuntimeError``
    whose class name contains no ``cancelled`` substring, combined with the
    queue reporting no cancel, must NOT emit ``JobCancelled``.
    """
    job_id = "job-handle-2"
    exc = RuntimeError("regular failure, not a cancel")

    ctx = _make_ctx()
    queue = _make_queue(is_cancelled=False)
    backend, _ = _make_backend(current_status="running")

    await _handle_job_failure(
        job_id,
        exc,
        ctx,
        queue,
        backend,
        lease_owner=None,
    )

    emitted = [call.args[0] for call in ctx.emit.await_args_list]
    assert not any(isinstance(ev, JobCancelled) for ev in emitted), (
        "JobCancelled must not be emitted when lease_owner=None and the "
        "exception is not a cancellation"
    )
    assert any(isinstance(ev, JobFailed) for ev in emitted), (
        "JobFailed must still be emitted for a plain failure"
    )
